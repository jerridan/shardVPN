# tests/test_handler.py
import hashlib
import hmac
import json
import logging
import time
from unittest.mock import ANY, MagicMock, patch

import pytest

from shardvpn import ec2ops, handler, settings
from shardvpn.render import render_userdata

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


def test_returns_503_when_the_signing_secret_is_still_the_placeholder():
    # terraform/ssm.tf seeds /shardvpn/signing-secret with this exact literal
    # and never writes a real value itself; if the out-of-band
    # `aws ssm put-parameter --overwrite` step is skipped, the "secret" is a
    # string committed to this public repository. Must fail closed via the
    # existing 503 path rather than ever verifying a request against it.
    with patch(
        "shardvpn.handler.settings.cached_secret",
        return_value=settings.PLACEHOLDER_SIGNING_SECRET,
    ):
        response = handler.lambda_handler(signed_event({"action": "status"}), None)

    assert response["statusCode"] == 503
    assert json.loads(response["body"]) == {"error": "signing secret not configured"}


def test_placeholder_secret_and_kms_failure_return_distinguishable_bodies():
    # Both are a 503 (neither is an auth result an attacker can induce), but
    # they are not the same problem: a KMS/SSM failure means the role likely
    # needs a kms:Decrypt grant, while a still-placeholder secret means the
    # out-of-band `aws ssm put-parameter` setup step was never run. The
    # README's verification recipe used to tell a reader "503 means add
    # kms:Decrypt" unconditionally, which misdiagnoses this second case. The
    # bodies must differ so the two are distinguishable without reading logs.
    with patch("shardvpn.handler.settings.cached_secret", side_effect=RuntimeError("boom")):
        kms_failure = handler.lambda_handler(signed_event({"action": "status"}), None)
    with patch(
        "shardvpn.handler.settings.cached_secret",
        return_value=settings.PLACEHOLDER_SIGNING_SECRET,
    ):
        placeholder = handler.lambda_handler(signed_event({"action": "status"}), None)

    assert kms_failure["statusCode"] == placeholder["statusCode"] == 503
    assert kms_failure["body"] != placeholder["body"]


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


def test_up_repairs_the_pointer_when_recovering_a_node_in_the_target_region():
    # An unrepaired pointer here is what lets a later `down` report success
    # while the recovered node keeps running and billing: read_pointer would
    # keep returning None, so `_down` would take its early "nothing to do"
    # return without ever calling terminate. This pins that the recovery
    # branch writes the real discovered instance id, not the launch path's
    # pointer only.
    existing = {
        "InstanceId": "i-0recovered",
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
        patch("shardvpn.handler.settings.write_pointer") as write_pointer,
    ):
        response = handler.lambda_handler(
            signed_event({"action": "up", "region": "ca-central-1", "ttl": "48h"}), None
        )

    launch.assert_not_called()
    assert response["statusCode"] == 200
    write_pointer.assert_called_once_with(
        ANY,
        settings.PARAM_NAMES["current_node"],
        settings.Pointer("ca-central-1", "i-0recovered"),
    )


def test_up_launches_a_node_with_correctly_tagged_kwargs():
    # Every other `up` test in this file asserts launch.assert_not_called(),
    # so the actual launch branch — the one iam.tf's LaunchTaggedExitNodes
    # statement conditions on (aws:RequestTag/shardvpn:role = exit-node) —
    # was never exercised. A misspelled tag key here fails with
    # UnauthorizedOperation, live, with no test to have caught it first.
    with (
        patch("shardvpn.handler.settings.read_pointer", return_value=None),
        patch("shardvpn.handler.ec2ops.valid_regions", return_value=["ca-central-1"]),
        patch("shardvpn.handler.ec2ops.find_nodes", return_value=[]),
        patch("shardvpn.handler.ec2ops.resolve_ami", return_value="ami-0abc"),
        patch("shardvpn.handler.settings.get_parameter", return_value="t4g.small"),
        patch("shardvpn.handler.ec2ops.ensure_security_group", return_value="sg-0shard"),
        patch("shardvpn.handler.tailscale.get_token", return_value="tok"),
        patch("shardvpn.handler.tailscale.mint_auth_key", return_value="tskey-auth-xyz"),
        patch("shardvpn.handler.ec2ops.launch", return_value="i-0new") as launch,
        patch("shardvpn.handler.settings.write_pointer") as write_pointer,
    ):
        response = handler.lambda_handler(
            signed_event({"action": "up", "region": "ca-central-1", "ttl": "48h"}), None
        )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["state"] == "pending"
    assert body["region"] == "ca-central-1"
    assert body["instance_id"] == "i-0new"
    hostname = body["hostname"]
    assert hostname.startswith("shardvpn-ca-central-1-")
    assert body["expires_at"] is not None and body["expires_at"].endswith("Z")

    launch.assert_called_once()
    kwargs = launch.call_args.kwargs
    assert kwargs["ami"] == "ami-0abc"
    assert kwargs["instance_type"] == "t4g.small"
    assert kwargs["sg_id"] == "sg-0shard"
    assert kwargs["user_data"] == render_userdata("tskey-auth-xyz", hostname)
    assert kwargs["client_token"] == f"shardvpn-{hostname}"

    tags = kwargs["tags"]
    assert tags["Name"] == hostname
    assert tags[ec2ops.TAG_ROLE] == ec2ops.ROLE_VALUE
    assert tags[ec2ops.TAG_HOSTNAME] == hostname
    assert tags[ec2ops.TAG_LAUNCHED].endswith("Z")
    assert tags[ec2ops.TAG_EXPIRES] == body["expires_at"]

    write_pointer.assert_called_once_with(
        ANY,
        settings.PARAM_NAMES["current_node"],
        settings.Pointer("ca-central-1", "i-0new"),
    )


def test_up_does_not_mint_an_auth_key_when_the_security_group_check_fails():
    # ensure_security_group raises a deliberate, repeatable RuntimeError when
    # someone hand-added an ingress rule to the shardvpn security group.
    # Minting the Tailscale auth key before that call is resolved would leave
    # a fresh, pre-authorized, ephemeral key live for KEY_EXPIRY_SECONDS with
    # nothing to revoke it — and since the failure repeats, every retry would
    # mint another orphan key. All fallible calls must resolve before mint.
    with (
        patch("shardvpn.handler.settings.read_pointer", return_value=None),
        patch("shardvpn.handler.ec2ops.valid_regions", return_value=["ca-central-1"]),
        patch("shardvpn.handler.ec2ops.find_nodes", return_value=[]),
        patch("shardvpn.handler.ec2ops.resolve_ami", return_value="ami-0abc"),
        patch("shardvpn.handler.settings.get_parameter", return_value="t4g.small"),
        patch(
            "shardvpn.handler.ec2ops.ensure_security_group",
            side_effect=RuntimeError("ingress rules present"),
        ),
        patch("shardvpn.handler.tailscale.get_token", return_value="tok"),
        patch("shardvpn.handler.tailscale.mint_auth_key") as mint_auth_key,
        patch("shardvpn.handler.ec2ops.launch") as launch,
    ):
        response = handler.lambda_handler(
            signed_event({"action": "up", "region": "ca-central-1", "ttl": "48h"}), None
        )

    assert response["statusCode"] == 500
    mint_auth_key.assert_not_called()
    launch.assert_not_called()


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


def test_up_rejects_a_non_string_ttl():
    # {"action":"up","ttl":123} is valid JSON from an authenticated caller.
    # Before ttl.parse_ttl hardened against this, it raised TypeError, which
    # only `except ValueError` catches here — turning a malformed request
    # into an opaque 500 instead of a 400.
    with (
        patch("shardvpn.handler.settings.read_pointer", return_value=None),
        patch("shardvpn.handler.ec2ops.valid_regions", return_value=["ca-central-1"]),
        patch("shardvpn.handler.settings.get_parameter", return_value="ca-central-1"),
        patch("shardvpn.handler.ec2ops.launch") as launch,
    ):
        response = handler.lambda_handler(signed_event({"action": "up", "ttl": 123}), None)

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


def test_down_terminates_an_orphaned_node_in_the_default_region_when_the_pointer_is_none():
    # No pointer does not mean no node: a launch whose write_pointer failed
    # after RunInstances already succeeded leaves a live, billing node with
    # nothing tracking it. `down` must not silently report "absent" while
    # that node keeps running — it has to check the default region first.
    orphan = {"InstanceId": "i-0orphan", "State": {"Name": "running"}, "Tags": []}
    with (
        patch("shardvpn.handler.settings.read_pointer", return_value=None),
        patch("shardvpn.handler.settings.get_parameter", return_value="ca-central-1"),
        patch("shardvpn.handler.ec2ops.find_nodes", return_value=[orphan]),
        patch("shardvpn.handler.ec2ops.terminate") as terminate,
    ):
        response = handler.lambda_handler(signed_event({"action": "down"}), None)

    terminate.assert_called_once_with(ANY, "i-0orphan")
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["state"] == "absent"


def test_status_selects_the_tracked_node_over_an_untracked_duplicate():
    # With an unresolved duplicate in the region (the watchdog refuses to
    # guess which one to terminate when launch-time tags are missing or
    # malformed), DescribeInstances ordering is arbitrary. Picking nodes[0]
    # unconditionally can report the *untracked* node's public_ip/hostname
    # and run the tailnet lookup against the wrong device. The pointer names
    # which instance is actually tracked; it must win.
    untracked = {
        "InstanceId": "i-0untracked",
        "State": {"Name": "running"},
        "PublicIpAddress": "203.0.113.9",
        "Tags": [
            {"Key": "shardvpn:launched-at", "Value": "2026-08-01T09:00:00Z"},
            {"Key": "shardvpn:ts-hostname", "Value": "shardvpn-untracked"},
        ],
    }
    tracked = {
        "InstanceId": "i-0tracked",
        "State": {"Name": "running"},
        "PublicIpAddress": "203.0.113.1",
        "Tags": [
            {"Key": "shardvpn:launched-at", "Value": "2026-08-01T09:46:00Z"},
            {"Key": "shardvpn:ts-hostname", "Value": "shardvpn-tracked"},
        ],
    }
    with (
        patch("shardvpn.handler.settings.read_pointer") as pointer,
        # DescribeInstances happens to list the untracked node first.
        patch("shardvpn.handler.ec2ops.find_nodes", return_value=[untracked, tracked]),
        patch("shardvpn.handler.ec2ops.network_out", return_value=[]),
        patch("shardvpn.handler.tailscale.get_token", return_value="tok"),
        patch("shardvpn.handler.tailscale.find_device", return_value=None) as find_device,
    ):
        pointer.return_value = settings.Pointer("ca-central-1", "i-0tracked")
        response = handler.lambda_handler(signed_event({"action": "status"}), None)

    body = json.loads(response["body"])
    assert body["instance_id"] == "i-0tracked"
    assert body["public_ip"] == "203.0.113.1"
    find_device.assert_called_once_with("tok", ANY, hostname="shardvpn-tracked")


def test_status_degrades_idle_to_none_when_cloudwatch_fails():
    # Mirrors the Tailscale lookup's degrade-not-fail behavior: idle_for is
    # strictly less important than the rest of the status document, so a
    # CloudWatch throttle or permissions gap must not turn `status` into a
    # bare 500 the way it would have before this call was isolated.
    node = {
        "InstanceId": "i-0cw",
        "State": {"Name": "running"},
        "Tags": [{"Key": "shardvpn:launched-at", "Value": "2020-01-01T00:00:00Z"}],
    }
    with (
        patch("shardvpn.handler.settings.read_pointer") as pointer,
        patch("shardvpn.handler.ec2ops.find_nodes", return_value=[node]),
        patch("shardvpn.handler.ec2ops.network_out", side_effect=RuntimeError("boom")),
        patch("shardvpn.handler.tailscale.get_token", return_value="tok"),
        patch("shardvpn.handler.tailscale.find_device", return_value={"id": "1"}),
    ):
        pointer.return_value = settings.Pointer("ca-central-1", "i-0cw")
        response = handler.lambda_handler(signed_event({"action": "status"}), None)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["instance_id"] == "i-0cw"
    assert body["idle_for"] is None


def test_unexpected_errors_become_500_not_502():
    with patch("shardvpn.handler.settings.read_pointer", side_effect=RuntimeError("boom")):
        response = handler.lambda_handler(signed_event({"action": "status"}), None)
    assert response["statusCode"] == 500
    assert "boom" not in response["body"]


def test_generic_exception_handler_never_logs_the_exception_body(caplog):
    # The correctness of log.error (not log.exception) in the generic
    # exception handler is the highest-stakes line in this file — the
    # difference between "internal error" and a live auth key leaked into
    # CloudWatch Logs — and it must not rest on a comment alone. A future
    # revert to log.exception would attach a traceback ending in
    # "RuntimeError: <secret_marker>" (making it appear in caplog.text) and
    # would set exc_info on the record; this test fails on either signal.
    secret_marker = "sk-fake-test-secret-3f9a7c21"
    with patch(
        "shardvpn.handler.settings.read_pointer",
        side_effect=RuntimeError(secret_marker),
    ):
        with caplog.at_level(logging.WARNING):
            response = handler.lambda_handler(signed_event({"action": "status"}), None)

    assert response["statusCode"] == 500
    assert secret_marker not in response["body"]
    assert secret_marker not in caplog.text
    for record in caplog.records:
        assert record.exc_info is None
