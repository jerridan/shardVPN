#!/usr/bin/env python3
"""Signed CLI for the shardVPN Function URL — the end-to-end test's client.

Stdlib only, matching the Lambda's own constraint: this runs on a GitHub
runner with nothing installed and must not need a pip step to work.

The signing construction is byte-for-byte the one in lambda/shardvpn/auth.py
and docs/ios/shardvpn.js. tests/test_e2e_driver.py pins this side against the
server's verify(), so a change to either fails the offline suite on the next
push rather than the weekly run six days later.

Deliberately NOT under tests/ — `uv run pytest` must stay hermetic, and
anything it collects that can reach AWS breaks that.

Configuration comes from the environment:

    SHARDVPN_URL      the Function URL
    SHARDVPN_SECRET   the HMAC signing secret

Both are read out of AWS at runtime by the workflow; neither is stored in
GitHub. Running this by hand from a laptop is supported and is a better
debugging tool than curl — see e2e/README.md.

Exit codes: 0 success, 1 failure, 2 refused (a guard tripped; nothing sent).
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TIMEOUT = 30

# The single-concurrency Lambda shares its one slot with the hourly sweep, so
# a 429 here is an ordinary event rather than a fault. Retry the whole signed
# request, re-signing each time: a retry that reused the first attempt's
# timestamp would eventually fall outside the 120s skew window and turn a
# transient throttle into an indistinguishable 403.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_DELAYS = (2, 4, 8)

# EC2 states from which a node will never reach 'running'. Cloud-init fails
# closed — a node that cannot advertise itself as an exit node logs out and
# terminates — so seeing one of these while waiting is a real failure and
# must not be waited out to the full timeout.
DOOMED_STATES = frozenset({"shutting-down", "terminated", "stopping", "stopped"})


class DriverError(Exception):
    """Anything that should exit non-zero with a readable message."""


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """HMAC-SHA256 over the timestamp, a literal dot, and the raw body.

    Must stay identical to auth.py's `expected`. Signs the timestamp string
    verbatim rather than a re-rendered int, and the body bytes exactly as
    they go on the wire — re-serializing either side is the trap recorded in
    CLAUDE.md's gotchas.
    """
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def call(url: str, secret: str, payload: dict) -> dict:
    """Send one signed request, retrying transient failures."""
    body = json.dumps(payload, separators=(",", ":")).encode()
    last: Exception | None = None

    for attempt, delay in enumerate((*RETRY_DELAYS, None)):
        timestamp = str(int(time.time()))
        request = Request(  # noqa: S310
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-ShardVPN-Timestamp": timestamp,
                "X-ShardVPN-Signature": sign(secret, timestamp, body),
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
                return json.loads(response.read())
        except HTTPError as exc:
            if exc.code not in RETRY_STATUSES or delay is None:
                raise DriverError(_explain(exc)) from exc
            last = exc
        except URLError as exc:
            if delay is None:
                raise DriverError(f"could not reach the function url: {exc.reason}") from exc
            last = exc

        warn(f"attempt {attempt + 1} failed ({last}); retrying in {delay}s")
        time.sleep(delay)

    raise DriverError(f"gave up after {len(RETRY_DELAYS) + 1} attempts: {last}")


def _explain(exc: HTTPError) -> str:
    """Turn the handler's deliberately terse statuses into something actionable.

    Every rejection the auth path can produce is a bodyless 403 by design, so
    the status code is the whole diagnostic surface. Saying what each one
    means here saves rediscovering it from the handler source at the moment
    the weekly run goes red.
    """
    if exc.code == 403:
        return (
            "403: signature rejected. Either SHARDVPN_SECRET does not match "
            "/shardvpn/signing-secret, or this machine's clock is more than "
            "120s off."
        )
    if exc.code == 503:
        return (
            "503: the Lambda cannot verify requests. Either the signing secret "
            "is still the placeholder (run the put-parameter step in "
            "docs/tailnet-setup.md), or SSM/KMS is failing."
        )
    return f"{exc.code}: {exc.reason}"


def warn(message: str) -> None:
    """Progress and diagnostics go to stderr; stdout is JSON only."""
    print(message, file=sys.stderr, flush=True)


def emit(document: dict) -> None:
    print(json.dumps(document, indent=2))


def wait_online(url: str, secret: str, timeout: int, interval: int) -> dict:
    """Poll status until the node is running AND on the tailnet.

    Both conditions matter. `running` alone means EC2 handed us an instance;
    it says nothing about whether cloud-init got through installing
    Tailscale, and a node that boots but never joins is the exact failure
    this whole test exists to catch.
    """
    deadline = time.monotonic() + timeout
    document: dict = {}

    while time.monotonic() < deadline:
        document = call(url, secret, {"action": "status"})
        state, tailnet = document.get("state"), document.get("tailnet")
        warn(f"  state={state} tailnet={tailnet}")

        if state == "running" and tailnet == "online":
            return document
        if state in DOOMED_STATES:
            raise DriverError(
                f"node reached {state} while waiting to come online. Cloud-init "
                "fails closed, so this usually means it could not advertise "
                "itself as an exit node — check the tailnet policy file's "
                "autoApprovers, then the instance's cloud-init log."
            )
        time.sleep(interval)

    raise DriverError(f"node was not online within {timeout}s; last status: {json.dumps(document)}")


def cmd_up(args, url: str, secret: str) -> int:
    payload = {"action": "up", "ttl": args.ttl}
    if args.region:
        payload["region"] = args.region

    document = call(url, secret, payload)
    if document.get("state") == "absent":
        raise DriverError(f"up returned absent, which should be impossible: {document}")

    warn(f"launched {document.get('instance_id')} in {document.get('region')}")
    emit(document)
    return 0


def cmd_status(args, url: str, secret: str) -> int:
    if args.wait_online:
        emit(wait_online(url, secret, args.timeout, args.interval))
    else:
        emit(call(url, secret, {"action": "status"}))
    return 0


def cmd_down(args, url: str, secret: str) -> int:
    # Refuse to terminate anything this run did not create. Reserved
    # concurrency 1 plus the SSM pointer should make a mismatch impossible;
    # if the impossible happens anyway, tearing down a node someone is
    # actually using is far worse than leaving the test's node to the TTL
    # sweep, which will reap it within the hour.
    if args.expect:
        current = call(url, secret, {"action": "status"})
        if current.get("state") == "absent":
            warn("already absent; nothing to terminate")
            emit(current)
            return 0
        if current.get("instance_id") != args.expect:
            warn(
                f"REFUSING to send down: expected {args.expect} but the pointer "
                f"names {current.get('instance_id')}. Leaving it alone; the TTL "
                "sweep will reap this run's node."
            )
            return 2

    document = call(url, secret, {"action": "down"})
    if document.get("state") != "absent":
        raise DriverError(f"down did not report absent: {document}")

    warn("terminated")
    emit(document)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="launch a node")
    up.add_argument("--region")
    # Not optional, and not 'none'. This TTL is the leak insurance: if the
    # runner is cancelled between up and down, the hourly sweep reaps the
    # node. A test that could launch a node with no expiry is one cancelled
    # job away from billing forever.
    up.add_argument("--ttl", default="30m")
    up.set_defaults(func=cmd_up)

    status = sub.add_parser("status", help="report on the current node")
    status.add_argument("--wait-online", action="store_true")
    status.add_argument("--timeout", type=int, default=600)
    status.add_argument("--interval", type=int, default=15)
    status.set_defaults(func=cmd_status)

    down = sub.add_parser("down", help="terminate the current node")
    down.add_argument("--expect", help="only act if the pointer names this instance id")
    down.set_defaults(func=cmd_down)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    url, secret = os.environ.get("SHARDVPN_URL"), os.environ.get("SHARDVPN_SECRET")
    if not url or not secret:
        warn("SHARDVPN_URL and SHARDVPN_SECRET must both be set")
        return 2

    try:
        return args.func(args, url, secret)
    except DriverError as exc:
        warn(f"error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
