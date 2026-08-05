from datetime import UTC, datetime

from shardvpn.statusdoc import ABSENT, build

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)

INSTANCE = {
    "InstanceId": "i-0abc",
    "State": {"Name": "running"},
    "PublicIpAddress": "3.98.0.1",
    "Tags": [
        {"Key": "shardvpn:launched-at", "Value": "2026-08-01T09:46:00Z"},
        {"Key": "shardvpn:expires-at", "Value": "never"},
        {"Key": "shardvpn:ts-hostname", "Value": "shardvpn-ca-central-1-4f2a"},
    ],
}


def test_absent_when_no_instance():
    assert build(None, None, None, None, NOW) == ABSENT


def test_absent_is_not_mutated_by_callers():
    result = build(None, None, None, None, NOW)
    result["state"] = "tampered"
    assert ABSENT["state"] == "absent"


def test_running_instance_is_fully_described():
    result = build(INSTANCE, "ca-central-1", True, 360, NOW)
    assert result["state"] == "running"
    assert result["region"] == "ca-central-1"
    assert result["instance_id"] == "i-0abc"
    assert result["public_ip"] == "3.98.0.1"
    assert result["tailnet"] == "online"
    assert result["hostname"] == "shardvpn-ca-central-1-4f2a"
    assert result["age"] == "2h14m"
    assert result["idle_for"] == "6m"


def test_never_expiry_reports_null():
    assert build(INSTANCE, "ca-central-1", True, 0, NOW)["expires_at"] is None


def test_real_expiry_is_passed_through():
    instance = INSTANCE | {
        "Tags": [
            {"Key": "shardvpn:launched-at", "Value": "2026-08-01T09:46:00Z"},
            {"Key": "shardvpn:expires-at", "Value": "2026-08-03T12:00:00Z"},
        ]
    }
    assert build(instance, "ca-central-1", True, 0, NOW)["expires_at"] == "2026-08-03T12:00:00Z"


def test_booted_but_not_in_tailnet_is_visible():
    result = build(INSTANCE, "ca-central-1", False, None, NOW)
    assert result["state"] == "running"
    assert result["tailnet"] == "absent"


def test_unknown_tailnet_state_when_not_checked():
    assert build(INSTANCE, "ca-central-1", None, None, NOW)["tailnet"] == "unknown"


def test_pending_instance_has_no_idle_measure():
    instance = INSTANCE | {"State": {"Name": "pending"}}
    result = build(instance, "ca-central-1", False, None, NOW)
    assert result["state"] == "pending"
    assert result["idle_for"] is None


def test_malformed_launched_at_does_not_raise():
    instance = INSTANCE | {"Tags": [{"Key": "shardvpn:launched-at", "Value": "garbage"}]}
    assert build(instance, "ca-central-1", True, None, NOW)["age"] is None


def test_missing_tags_do_not_raise():
    instance = {"InstanceId": "i-0abc", "State": {"Name": "running"}}
    result = build(instance, "ca-central-1", None, None, NOW)
    assert result["age"] is None
    assert result["hostname"] is None
    assert result["expires_at"] is None
