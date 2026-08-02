# tests/test_handler.py
import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from shardvpn import handler, settings

SIGNING_KEY = "s3cr3t"


def signed_event(body: dict) -> dict:
    raw = json.dumps(body)
    ts = int(time.time())
    sig = hmac.new(SIGNING_KEY.encode(), f"{ts}.{raw}".encode(), hashlib.sha256).hexdigest()
    return {
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"x-shardvpn-timestamp": str(ts), "x-shardvpn-signature": sig},
        "body": raw,
        "isBase64Encoded": False,
    }


@pytest.fixture(autouse=True)
def stub_secret(monkeypatch):
    settings._SECRET_CACHE.clear()
    monkeypatch.setattr(handler.settings, "cached_secret", lambda *a, **k: SIGNING_KEY)
    monkeypatch.setattr(handler.boto3, "client", lambda *a, **k: MagicMock())


def test_rejects_an_unsigned_request():
    event = {"requestContext": {"http": {}}, "headers": {}, "body": "{}"}
    assert handler.lambda_handler(event, None)["statusCode"] == 403


def test_rejects_a_wrongly_signed_request():
    event = signed_event({"action": "status"})
    event["headers"]["x-shardvpn-signature"] = "0" * 64
    assert handler.lambda_handler(event, None)["statusCode"] == 403


def test_forbidden_responses_are_indistinguishable():
    missing = handler.lambda_handler(
        {"requestContext": {"http": {}}, "headers": {}, "body": "{}"}, None
    )
    wrong = signed_event({"action": "status"})
    wrong["headers"]["x-shardvpn-signature"] = "0" * 64
    assert missing == handler.lambda_handler(wrong, None)


def test_a_non_ascii_signature_is_403_not_502():
    event = signed_event({"action": "status"})
    event["headers"]["x-shardvpn-signature"] = "é" * 64
    assert handler.lambda_handler(event, None)["statusCode"] == 403


def test_returns_503_when_the_signing_secret_cannot_be_retrieved():
    # A transient SSM/KMS failure fetching the secret is infrastructure
    # health, not a verification result, so it must be distinguishable from
    # both the byte-identical 403 an invalid signature gets and an unhandled
    # 502.
    with patch("shardvpn.handler.settings.cached_secret", side_effect=RuntimeError("boom")):
        response = handler.lambda_handler(signed_event({"action": "status"}), None)

    assert response["statusCode"] == 503
    assert response["statusCode"] != 403
    assert response["body"] != "{}"
    assert json.loads(response["body"]) == {"error": "cannot verify request"}


def test_sweep_is_unreachable_over_http():
    response = handler.lambda_handler(signed_event({"action": "sweep"}), None)
    assert response["statusCode"] == 400
    assert "regions_scanned" not in response["body"]


def test_scheduler_event_routes_to_the_watchdog():
    with patch("shardvpn.handler.watchdog.sweep", return_value={"nodes": []}) as sweep:
        result = handler.lambda_handler({"action": "sweep"}, None)
    sweep.assert_called_once()
    assert result == {"nodes": []}


def test_unknown_action_is_rejected():
    assert handler.lambda_handler(signed_event({"action": "explode"}), None)["statusCode"] == 400


def test_malformed_json_body_is_rejected():
    event = signed_event({"action": "status"})
    raw = "{not json"
    ts = int(time.time())
    event["body"] = raw
    event["headers"]["x-shardvpn-signature"] = hmac.new(
        SIGNING_KEY.encode(), f"{ts}.{raw}".encode(), hashlib.sha256
    ).hexdigest()
    event["headers"]["x-shardvpn-timestamp"] = str(ts)
    assert handler.lambda_handler(event, None)["statusCode"] == 400


def test_up_returns_the_existing_node_instead_of_launching():
    existing = {
        "InstanceId": "i-0abc",
        "State": {"Name": "running"},
        "Tags": [{"Key": "shardvpn:launched-at", "Value": "2026-08-01T09:46:00Z"}],
    }
    with (
        patch("shardvpn.handler.settings.read_pointer") as pointer,
        patch("shardvpn.handler.ec2ops.find_nodes", return_value=[existing]),
        patch("shardvpn.handler.ec2ops.launch") as launch,
        patch("shardvpn.handler.ec2ops.network_out", return_value=[]),
        patch("shardvpn.handler.tailscale.get_token", return_value="tok"),
        patch("shardvpn.handler.tailscale.find_device", return_value={"id": "1"}),
    ):
        pointer.return_value = settings.Pointer("ca-central-1", "i-0abc")
        response = handler.lambda_handler(signed_event({"action": "up"}), None)

    launch.assert_not_called()
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["instance_id"] == "i-0abc"


def test_up_does_not_launch_when_a_node_already_exists_in_the_target_region():
    # Isolates the regional find_nodes check that runs immediately before
    # RunInstances, independent of the pointer-driven short-circuit above:
    # the pointer says nothing is tracked, but a node is already live in the
    # requested region (e.g. a concurrent request beat this one there). This
    # is what stands between a double-tap and paying for two instances.
    existing = {
        "InstanceId": "i-0existing",
        "State": {"Name": "running"},
        "Tags": [{"Key": "shardvpn:launched-at", "Value": "2026-08-01T09:46:00Z"}],
    }
    with (
        patch("shardvpn.handler.settings.read_pointer", return_value=None),
        patch("shardvpn.handler.ec2ops.valid_regions", return_value=["ca-central-1"]),
        patch("shardvpn.handler.ec2ops.find_nodes", return_value=[existing]),
        patch("shardvpn.handler.ec2ops.launch") as launch,
        patch("shardvpn.handler.ec2ops.network_out", return_value=[]),
        patch("shardvpn.handler.tailscale.get_token", return_value="tok"),
        patch("shardvpn.handler.tailscale.find_device", return_value=None),
    ):
        response = handler.lambda_handler(
            signed_event({"action": "up", "region": "ca-central-1", "ttl": "48h"}), None
        )

    launch.assert_not_called()
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["instance_id"] == "i-0existing"


def test_up_fails_closed_when_the_pointer_cannot_be_read():
    # A transient SSM error must not be read as 'no node' and launch a second.
    with (
        patch(
            "shardvpn.handler.settings.read_pointer",
            side_effect=settings.PointerUnavailable("Throttling"),
        ),
        patch("shardvpn.handler.ec2ops.launch") as launch,
    ):
        response = handler.lambda_handler(signed_event({"action": "up"}), None)

    launch.assert_not_called()
    assert response["statusCode"] == 503


def test_up_rejects_an_unknown_region():
    with (
        patch("shardvpn.handler.settings.read_pointer", return_value=None),
        patch("shardvpn.handler.ec2ops.valid_regions", return_value=["ca-central-1"]),
        patch("shardvpn.handler.ec2ops.launch") as launch,
    ):
        response = handler.lambda_handler(
            signed_event({"action": "up", "region": "moon-base-1"}), None
        )

    launch.assert_not_called()
    assert response["statusCode"] == 400


def test_up_rejects_a_malformed_ttl():
    with (
        patch("shardvpn.handler.settings.read_pointer", return_value=None),
        patch("shardvpn.handler.ec2ops.valid_regions", return_value=["ca-central-1"]),
        patch("shardvpn.handler.settings.get_parameter", return_value="ca-central-1"),
        patch("shardvpn.handler.ec2ops.launch") as launch,
    ):
        response = handler.lambda_handler(signed_event({"action": "up", "ttl": "soon"}), None)

    launch.assert_not_called()
    assert response["statusCode"] == 400


def test_down_terminates_every_live_node():
    nodes = [
        {"InstanceId": "i-0aaa", "State": {"Name": "running"}, "Tags": []},
        {"InstanceId": "i-0bbb", "State": {"Name": "running"}, "Tags": []},
    ]
    with (
        patch("shardvpn.handler.settings.read_pointer") as pointer,
        patch("shardvpn.handler.ec2ops.find_nodes", return_value=nodes),
        patch("shardvpn.handler.ec2ops.terminate") as terminate,
        patch("shardvpn.handler.settings.write_pointer"),
    ):
        pointer.return_value = settings.Pointer("ca-central-1", "i-0aaa")
        response = handler.lambda_handler(signed_event({"action": "down"}), None)

    assert terminate.call_count == 2
    assert response["statusCode"] == 200


def test_unexpected_errors_become_500_not_502():
    with patch("shardvpn.handler.settings.read_pointer", side_effect=RuntimeError("boom")):
        response = handler.lambda_handler(signed_event({"action": "status"}), None)
    assert response["statusCode"] == 500
    assert "boom" not in response["body"]
