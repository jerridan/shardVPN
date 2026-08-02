from datetime import UTC, datetime

import pytest
from shardvpn.ttl import (
    NEVER,
    format_duration,
    is_expired,
    parse_rfc3339,
    parse_ttl,
    rfc3339,
)

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


def test_none_ttl_means_never():
    assert parse_ttl(None, NOW) == NEVER


def test_literal_none_means_never():
    assert parse_ttl("none", NOW) == NEVER


def test_hours_ttl():
    assert parse_ttl("48h", NOW) == "2026-08-03T12:00:00Z"


def test_minutes_ttl():
    assert parse_ttl("90m", NOW) == "2026-08-01T13:30:00Z"


def test_days_ttl():
    assert parse_ttl("3d", NOW) == "2026-08-04T12:00:00Z"


@pytest.mark.parametrize("bad", ["", "48", "h48", "-1h", "0h", "48x", "1.5h", "99999999d"])
def test_rejects_malformed_or_absurd_ttl(bad):
    # '99999999d' overflows timedelta; it must raise ValueError like the rest
    # rather than OverflowError, which the handler would not catch.
    with pytest.raises(ValueError):
        parse_ttl(bad, NOW)


def test_never_is_not_expired():
    assert is_expired(NEVER, NOW) is False


def test_past_expiry_is_expired():
    assert is_expired("2026-07-31T12:00:00Z", NOW) is True


def test_future_expiry_is_not_expired():
    assert is_expired("2026-08-02T12:00:00Z", NOW) is False


def test_malformed_expiry_is_not_expired():
    # A hand-edited tag must not cause a 502 or a surprise termination.
    assert is_expired("garbage", NOW) is False


def test_roundtrips_rfc3339():
    assert parse_rfc3339(rfc3339(NOW)) == NOW


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0m"),
        (59, "0m"),
        (60, "1m"),
        (360, "6m"),
        (3600, "1h0m"),
        (8040, "2h14m"),
        (90000, "1d1h0m"),
    ],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected
