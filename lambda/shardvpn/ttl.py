"""TTL parsing and expiry. Pure: no AWS, no I/O."""

import re
from datetime import UTC, datetime, timedelta

NEVER = "never"

_TTL_PATTERN = re.compile(r"\A(\d+)([mhd])\Z")
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}
_MAX_TTL_SECONDS = 365 * 86400


def rfc3339(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_rfc3339(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def parse_ttl(ttl: str | None, now: datetime) -> str:
    """Turn a TTL like '48h' into an absolute expiry, or NEVER."""
    if ttl is None or ttl == NEVER or ttl == "none":
        return NEVER

    match = _TTL_PATTERN.match(ttl)
    if not match:
        raise ValueError(f"malformed ttl: {ttl!r}")

    seconds = int(match.group(1)) * _UNIT_SECONDS[match.group(2)]
    if seconds < 60:
        raise ValueError(f"ttl must be at least one minute: {ttl!r}")
    if seconds > _MAX_TTL_SECONDS:
        # Bounded so timedelta cannot raise OverflowError, which callers
        # catching ValueError would miss.
        raise ValueError(f"ttl exceeds one year: {ttl!r}")

    return rfc3339(now + timedelta(seconds=seconds))


def is_expired(expires_at: str, now: datetime) -> bool:
    """True only when a well-formed expiry is in the past.

    An unparseable tag returns False: a hand-edited value should never cause
    an unexpected termination.
    """
    if expires_at == NEVER:
        return False
    try:
        return parse_rfc3339(expires_at) <= now
    except ValueError:
        return False


def format_duration(seconds: int) -> str:
    """Render a duration compactly: '6m', '2h14m', '1d1h0m'."""
    days, rem = divmod(max(seconds, 0), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d{hours}h{minutes}m"
    if hours:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"
