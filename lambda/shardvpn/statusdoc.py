"""Builds the single response document returned by every action."""

from datetime import datetime

from shardvpn.ec2ops import TAG_EXPIRES, TAG_HOSTNAME, TAG_LAUNCHED, tag_value
from shardvpn.ttl import NEVER, format_duration, parse_rfc3339

ABSENT = {
    "state": "absent",
    "region": None,
    "instance_id": None,
    "public_ip": None,
    "tailnet": "unknown",
    "hostname": None,
    "age": None,
    "expires_at": None,
    "idle_for": None,
}


def _age(launched: str | None, now: datetime) -> str | None:
    if not launched:
        return None
    try:
        return format_duration(int((now - parse_rfc3339(launched)).total_seconds()))
    except ValueError:
        # A hand-edited tag must not turn `status` into a 502.
        return None


def build(
    instance: dict | None,
    region: str | None,
    ts_online: bool | None,
    idle_seconds: int | None,
    now: datetime,
) -> dict:
    """Describe the current node uniformly, whatever action produced it."""
    if instance is None:
        return dict(ABSENT)

    expires = tag_value(instance, TAG_EXPIRES)

    return {
        "state": instance["State"]["Name"],
        "region": region,
        "instance_id": instance["InstanceId"],
        "public_ip": instance.get("PublicIpAddress"),
        "tailnet": "unknown" if ts_online is None else ("online" if ts_online else "absent"),
        "hostname": tag_value(instance, TAG_HOSTNAME),
        "age": _age(tag_value(instance, TAG_LAUNCHED), now),
        "expires_at": None if expires in (None, NEVER) else expires,
        "idle_for": None if idle_seconds is None else format_duration(idle_seconds),
    }
