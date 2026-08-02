"""Lambda entry point: event routing, auth gate, action dispatch."""

import json
import logging
import os
import secrets
import time
from datetime import UTC, datetime

import boto3

from shardvpn import ec2ops, settings, statusdoc, tailscale, ttl, watchdog
from shardvpn.auth import AuthError, extract_body, verify
from shardvpn.render import render_userdata

log = logging.getLogger()
log.setLevel(logging.INFO)

CONTROL_REGION = os.environ["CONTROL_REGION"]
TS_TAG = os.environ.get("TS_TAG", "tag:shardvpn-exit")
KEY_EXPIRY_SECONDS = 600
FRESH_NODE_SECONDS = 900

FORBIDDEN = {"statusCode": 403, "headers": {"content-type": "application/json"}, "body": "{}"}


def _respond(code: int, payload: dict) -> dict:
    return {
        "statusCode": code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload),
    }


def lambda_handler(event: dict, context) -> dict:
    # Route on event shape first. A Function URL event always carries
    # requestContext; a Scheduler event never does. This is what makes 'sweep'
    # unreachable over HTTP regardless of signature validity.
    if "requestContext" not in event:
        return watchdog.sweep(datetime.now(UTC))

    control_ssm = boto3.client("ssm", region_name=CONTROL_REGION)

    # Fetching the secret is kept strictly separate from verifying against it.
    # A transient SSM/KMS failure here is infrastructure health, not a
    # verification result — an attacker cannot induce an SSM outage, so
    # returning 503 in this branch leaks nothing about signature validity and
    # creates no new oracle. It must not surface as an unhandled exception
    # (Lambda would turn that into a bodyless, indistinguishable-from-broken
    # 502) nor be folded into the byte-identical 403 an invalid signature
    # gets.
    try:
        secret = settings.cached_secret(
            control_ssm, settings.PARAM_NAMES["signing_secret"], time.time()
        )
    except Exception as exc:
        log.error("could not retrieve signing secret: %s", type(exc).__name__)
        return _respond(503, {"error": "cannot verify request"})

    try:
        verify(event.get("headers") or {}, extract_body(event), secret, int(time.time()))
    except AuthError:
        return dict(FORBIDDEN)

    try:
        body = json.loads(extract_body(event) or b"{}")
        if not isinstance(body, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        return _respond(400, {"error": "malformed json"})

    action = body.get("action")
    try:
        if action == "up":
            return _up(control_ssm, body)
        if action == "down":
            return _down(control_ssm)
        if action == "status":
            return _status(control_ssm)
        return _respond(400, {"error": "unknown action"})
    except settings.PointerUnavailable:
        log.warning("pointer unavailable; refusing to act")
        return _respond(503, {"error": "cannot determine current state"})
    except Exception as exc:
        # Never let the exception body out: botocore's ParamValidationError can
        # embed UserData, which carries a minted auth key. log.exception()
        # would attach the traceback (and therefore str(exc)) regardless of
        # the message passed here, so use log.error() with no exc_info
        # instead — only the exception type is ever logged.
        request_id = getattr(context, "aws_request_id", "unknown")
        log.error("action %s failed: %s", action, type(exc).__name__)
        return _respond(500, {"error": "internal", "request_id": request_id})


def _tailscale_token(control_ssm) -> str:
    return tailscale.get_token(
        settings.cached_secret(control_ssm, settings.PARAM_NAMES["ts_client_id"], time.time()),
        settings.cached_secret(control_ssm, settings.PARAM_NAMES["ts_client_secret"], time.time()),
    )


def _describe(control_ssm, pointer) -> dict:
    if pointer is None:
        return dict(statusdoc.ABSENT)

    ec2 = boto3.client("ec2", region_name=pointer.region)
    nodes = ec2ops.find_nodes(ec2)
    if not nodes:
        return dict(statusdoc.ABSENT)

    instance = nodes[0]
    now = datetime.now(UTC)

    online = None
    try:
        online = (
            tailscale.find_device(
                _tailscale_token(control_ssm),
                TS_TAG,
                hostname=ec2ops.tag_value(instance, ec2ops.TAG_HOSTNAME),
            )
            is not None
        )
    except tailscale.TailscaleError:
        log.warning("tailnet status unavailable")

    idle = None
    launched = ec2ops.tag_value(instance, ec2ops.TAG_LAUNCHED)
    fresh = False
    if launched:
        try:
            fresh = (now - ttl.parse_rfc3339(launched)).total_seconds() < FRESH_NODE_SECONDS
        except ValueError:
            fresh = False

    # CloudWatch basic monitoring lags several minutes, so a young node would
    # otherwise report itself as maximally idle.
    if instance["State"]["Name"] == "running" and not fresh:
        try:
            cw = boto3.client("cloudwatch", region_name=pointer.region)
            idle = watchdog.idle_seconds(
                ec2ops.network_out(cw, instance["InstanceId"], 24, now), now
            )
        except Exception as exc:
            # Mirrors the Tailscale lookup above: idle_for is strictly less
            # important than the rest of the status document, so a
            # CloudWatch throttle or permissions gap must degrade to
            # idle: None rather than fail the whole request.
            log.warning("idle lookup unavailable: %s", type(exc).__name__)

    return statusdoc.build(instance, pointer.region, online, idle, now)


def _status(control_ssm) -> dict:
    pointer = settings.read_pointer(control_ssm, settings.PARAM_NAMES["current_node"])
    return _respond(200, _describe(control_ssm, pointer))


def _down(control_ssm) -> dict:
    pointer = settings.read_pointer(control_ssm, settings.PARAM_NAMES["current_node"])
    if pointer is None:
        # No pointer does not mean no node: a launch whose write_pointer call
        # failed after RunInstances already succeeded leaves a live, billing
        # node with nothing tracking it. Reporting success while a node
        # keeps running is the worst failure this API has, so check the
        # default region — one extra DescribeInstances, not a global sweep —
        # before giving up. Cross-region orphan recovery is the watchdog's
        # job, not this request path's.
        region = settings.get_parameter(control_ssm, settings.PARAM_NAMES["default_region"])
        ec2 = boto3.client("ec2", region_name=region)
        for instance in ec2ops.find_nodes(ec2):
            ec2ops.terminate(ec2, instance["InstanceId"])
        return _respond(200, dict(statusdoc.ABSENT))

    ec2 = boto3.client("ec2", region_name=pointer.region)
    for instance in ec2ops.find_nodes(ec2):
        ec2ops.terminate(ec2, instance["InstanceId"])

    settings.write_pointer(control_ssm, settings.PARAM_NAMES["current_node"], None)
    return _respond(200, dict(statusdoc.ABSENT))


def _up(control_ssm, body: dict) -> dict:
    pointer = settings.read_pointer(control_ssm, settings.PARAM_NAMES["current_node"])
    if pointer is not None:
        existing = _describe(control_ssm, pointer)
        if existing["state"] != "absent":
            return _respond(200, existing)

    control_ec2 = boto3.client("ec2", region_name=CONTROL_REGION)
    regions = ec2ops.valid_regions(control_ec2)

    region = body.get("region") or settings.get_parameter(
        control_ssm, settings.PARAM_NAMES["default_region"]
    )
    if region not in regions:
        return _respond(400, {"error": "unknown region", "valid": regions})

    now = datetime.now(UTC)
    try:
        expires_at = ttl.parse_ttl(
            body.get("ttl")
            or settings.get_parameter(control_ssm, settings.PARAM_NAMES["default_ttl"]),
            now,
        )
    except ValueError:
        return _respond(400, {"error": "malformed ttl"})

    ec2 = boto3.client("ec2", region_name=region)

    # Regional, so it does not close the global window — but it eliminates the
    # common double-tap-in-the-same-region case before spending money.
    found = ec2ops.find_nodes(ec2)
    if found:
        # The pointer may be stale or absent here — e.g. a prior launch's
        # write_pointer failed after RunInstances already succeeded. Repair
        # it before returning: an unrepaired pointer is exactly what lets a
        # later `down` report success while this node keeps running and
        # billing (read_pointer returns None, so `_down` would otherwise
        # take its early "nothing to do" return without ever calling
        # terminate).
        found_id = found[0]["InstanceId"]
        settings.write_pointer(
            control_ssm,
            settings.PARAM_NAMES["current_node"],
            settings.Pointer(region=region, instance_id=found_id),
        )
        return _respond(200, _describe(control_ssm, settings.Pointer(region, found_id)))

    hostname = f"shardvpn-{region}-{secrets.token_hex(2)}"
    authkey = tailscale.mint_auth_key(
        _tailscale_token(control_ssm), TS_TAG, KEY_EXPIRY_SECONDS, f"shardvpn {hostname}"
    )

    instance_id = ec2ops.launch(
        ec2,
        ami=ec2ops.resolve_ami(boto3.client("ssm", region_name=region)),
        instance_type=settings.get_parameter(control_ssm, settings.PARAM_NAMES["instance_type"]),
        sg_id=ec2ops.ensure_security_group(ec2),
        user_data=render_userdata(authkey, hostname),
        tags={
            "Name": hostname,
            ec2ops.TAG_ROLE: ec2ops.ROLE_VALUE,
            ec2ops.TAG_LAUNCHED: ttl.rfc3339(now),
            ec2ops.TAG_EXPIRES: expires_at,
            ec2ops.TAG_HOSTNAME: hostname,
        },
        client_token=f"shardvpn-{hostname}",
    )

    settings.write_pointer(
        control_ssm,
        settings.PARAM_NAMES["current_node"],
        settings.Pointer(region=region, instance_id=instance_id),
    )

    log.info("launched %s in %s as %s", instance_id, region, hostname)
    return _respond(
        200,
        statusdoc.ABSENT
        | {
            "state": "pending",
            "region": region,
            "instance_id": instance_id,
            "hostname": hostname,
            "expires_at": None if expires_at == ttl.NEVER else expires_at,
            "age": "0m",
        },
    )
