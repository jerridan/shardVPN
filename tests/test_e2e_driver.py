"""Pins the e2e driver's client-side signing against the handler's verify().

The driver is the one client that CI exercises, and it is not covered by the
weekly run's own failures in any useful way: if the signing drifts, every
weekly run returns a bodyless 403 that looks exactly like a rotated secret or
a skewed clock. Catching it here, offline, on the push that causes it, is the
whole point of this file.

No network: `call` is exercised with urlopen patched.
"""

import io
import json
from urllib.error import HTTPError

import pytest

import driver
from shardvpn.auth import AuthError, verify

SECRET = "s3cr3t"
NOW = 1753977600


class FakeResponse(io.BytesIO):
    """Minimal stand-in for the object urlopen returns as a context manager."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def captured(monkeypatch, responses):
    """Patch driver.urlopen, recording each request and replaying `responses`.

    A response is either bytes (a body to return) or an Exception to raise.
    """
    sent = []

    def fake_urlopen(request, timeout=None):
        sent.append(request)
        # The last response repeats, so a polling test can run to its timeout
        # instead of dying on IndexError.
        result = responses[min(len(sent) - 1, len(responses) - 1)]
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)

    monkeypatch.setattr(driver, "urlopen", fake_urlopen)
    monkeypatch.setattr(driver.time, "sleep", lambda _: None)
    return sent


def test_driver_signature_is_accepted_by_the_handler():
    """The property that matters: both sides agree on what was signed."""
    body = b'{"action":"up","ttl":"30m"}'
    signature = driver.sign(SECRET, str(NOW), body)

    verify(
        {"X-ShardVPN-Timestamp": str(NOW), "X-ShardVPN-Signature": signature},
        body,
        SECRET,
        NOW,
    )


def test_handler_rejects_a_signature_over_different_bytes():
    """Guards the test above against passing for a trivial reason."""
    signature = driver.sign(SECRET, str(NOW), b'{"action":"up"}')

    with pytest.raises(AuthError):
        verify(
            {"X-ShardVPN-Timestamp": str(NOW), "X-ShardVPN-Signature": signature},
            b'{"action":"down"}',
            SECRET,
            NOW,
        )


def test_signs_the_exact_bytes_it_sends(monkeypatch):
    """The re-serialization trap: sign the wire bytes, not a fresh dump.

    Verifies the request as the Lambda would, straight out of what was
    actually transmitted.
    """
    sent = captured(monkeypatch, [b'{"state":"absent"}'])

    driver.call("https://example.invalid/", SECRET, {"action": "status"})

    request = sent[0]
    verify(request.headers, request.data, SECRET, int(request.headers["X-shardvpn-timestamp"]))


def test_retries_transient_statuses_and_resigns_each_attempt(monkeypatch):
    """A retry must not reuse the first attempt's timestamp.

    Reusing it would eventually push the request outside the 120s skew window
    and turn a throttle into a 403 that reads as an auth failure.
    """
    throttled = HTTPError("https://example.invalid/", 429, "Too Many Requests", {}, None)
    sent = captured(monkeypatch, [throttled, b'{"state":"absent"}'])

    monkeypatch.setattr(driver.time, "time", lambda: NOW + len(sent) * 10)
    driver.call("https://example.invalid/", SECRET, {"action": "status"})

    assert len(sent) == 2
    for request in sent:
        stamp = int(request.headers["X-shardvpn-timestamp"])
        verify(request.headers, request.data, SECRET, stamp)


def test_does_not_retry_a_rejected_signature(monkeypatch):
    """403 is terminal. Retrying it just spends the concurrency slot."""
    sent = captured(
        monkeypatch, [HTTPError("https://example.invalid/", 403, "Forbidden", {}, None)]
    )

    with pytest.raises(driver.DriverError, match="signature rejected"):
        driver.call("https://example.invalid/", SECRET, {"action": "status"})

    assert len(sent) == 1


def test_down_refuses_when_the_pointer_names_another_instance(monkeypatch):
    """Never terminate a node this run did not launch."""
    sent = captured(
        monkeypatch, [json.dumps({"state": "running", "instance_id": "i-other"}).encode()]
    )
    args = driver.build_parser().parse_args(["down", "--expect", "i-ours"])

    assert args.func(args, "https://example.invalid/", SECRET) == 2
    # One status call, and crucially no second call carrying the down action.
    assert len(sent) == 1


def test_down_proceeds_when_the_instance_matches(monkeypatch):
    sent = captured(
        monkeypatch,
        [
            json.dumps({"state": "running", "instance_id": "i-ours"}).encode(),
            json.dumps({"state": "absent"}).encode(),
        ],
    )
    args = driver.build_parser().parse_args(["down", "--expect", "i-ours"])

    assert args.func(args, "https://example.invalid/", SECRET) == 0
    assert json.loads(sent[1].data)["action"] == "down"


def test_wait_online_gives_up_immediately_on_a_doomed_state(monkeypatch):
    """Cloud-init fails closed, so a terminating node will never come online."""
    captured(monkeypatch, [json.dumps({"state": "terminated", "tailnet": "absent"}).encode()])

    with pytest.raises(driver.DriverError, match="fails closed"):
        driver.wait_online("https://example.invalid/", SECRET, timeout=600, interval=0)


def test_wait_online_returns_once_running_and_on_the_tailnet(monkeypatch):
    captured(
        monkeypatch,
        [
            json.dumps({"state": "pending", "tailnet": "unknown"}).encode(),
            json.dumps({"state": "running", "tailnet": "absent"}).encode(),
            json.dumps({"state": "running", "tailnet": "online"}).encode(),
        ],
    )

    document = driver.wait_online("https://example.invalid/", SECRET, timeout=600, interval=0)

    assert document["state"] == "running"


def test_running_but_not_on_the_tailnet_is_not_enough(monkeypatch):
    """A node that boots and never joins is the failure this test exists for.

    A non-zero timeout so the poll loop actually runs — with timeout=0 this
    would pass without ever calling status.
    """
    sent = captured(monkeypatch, [json.dumps({"state": "running", "tailnet": "absent"}).encode()])

    with pytest.raises(driver.DriverError, match="not online within"):
        driver.wait_online("https://example.invalid/", SECRET, timeout=0.05, interval=0)

    assert sent, "wait_online returned without polling status even once"
