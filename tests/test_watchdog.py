# tests/test_watchdog.py
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from shardvpn import ec2ops
from shardvpn.watchdog import idle_seconds, should_alert, sweep

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
THRESHOLD = 5_000_000


def test_alerts_when_idle_and_never_alerted():
    assert should_alert(100, THRESHOLD, None, NOW) is True


def test_does_not_alert_when_busy():
    assert should_alert(90_000_000, THRESHOLD, None, NOW) is False


def test_does_not_alert_twice_within_a_day():
    assert should_alert(100, THRESHOLD, "2026-08-01T02:00:00Z", NOW) is False


def test_alerts_again_after_a_day():
    assert should_alert(100, THRESHOLD, "2026-07-31T11:00:00Z", NOW) is True


def test_threshold_boundary_is_not_idle():
    assert should_alert(THRESHOLD, THRESHOLD, None, NOW) is False


def test_malformed_last_alert_is_treated_as_never_alerted():
    assert should_alert(100, THRESHOLD, "garbage", NOW) is True


def test_does_not_alert_about_a_node_younger_than_the_idle_window():
    # CloudWatch has no data yet for a freshly launched node, which is
    # indistinguishable from zero traffic. Claiming "idle for 24h" about a
    # node launched 20 minutes ago is false and trains the user to ignore
    # the alert that matters.
    fresh = "2026-08-01T11:40:00Z"
    assert should_alert(0, THRESHOLD, None, NOW, fresh) is False


def test_alerts_about_a_node_older_than_the_idle_window():
    old = "2026-07-28T12:00:00Z"
    assert should_alert(0, THRESHOLD, None, NOW, old) is True


def test_malformed_launched_at_does_not_suppress_the_alert():
    assert should_alert(0, THRESHOLD, None, NOW, "garbage") is True


def test_idle_seconds_measures_from_the_last_non_zero_datapoint():
    points = [
        (NOW - timedelta(hours=3), 900.0),
        (NOW - timedelta(hours=2), 0.0),
        (NOW - timedelta(hours=1), 0.0),
    ]
    assert idle_seconds(points, NOW) == 3 * 3600


def test_idle_seconds_is_none_without_datapoints():
    assert idle_seconds([], NOW) is None


def test_idle_seconds_is_zero_when_currently_busy():
    assert idle_seconds([(NOW, 5000.0)], NOW) == 0


# --- sweep -----------------------------------------------------------------


def node(instance_id, *, launched, expires="never", alerted=None, state="running"):
    tags = [
        {"Key": ec2ops.TAG_ROLE, "Value": ec2ops.ROLE_VALUE},
        {"Key": ec2ops.TAG_LAUNCHED, "Value": launched},
        {"Key": ec2ops.TAG_EXPIRES, "Value": expires},
    ]
    if alerted:
        tags.append({"Key": ec2ops.TAG_ALERTED, "Value": alerted})
    return {"InstanceId": instance_id, "State": {"Name": state}, "Tags": tags}


def make_factory(nodes_by_region, *, pointer_value="none", threshold=THRESHOLD, out_bytes=0):
    """Build a client_factory returning MagicMocks wired for the given world."""
    ssm = MagicMock()
    ssm.get_parameter.side_effect = lambda **kw: {
        "Parameter": {
            "Value": str(threshold)
            if kw["Name"].endswith("idle-threshold-bytes")
            else pointer_value
        }
    }
    sns = MagicMock()
    clients = {"ssm": ssm, "sns": sns, "ec2": {}, "cloudwatch": MagicMock()}
    clients["cloudwatch"].get_metric_statistics.return_value = {
        "Datapoints": [{"Sum": float(out_bytes), "Timestamp": NOW}]
    }

    def factory(service, region_name=None):
        if service == "ssm":
            return ssm
        if service == "sns":
            return sns
        if service == "cloudwatch":
            return clients["cloudwatch"]
        ec2 = clients["ec2"].get(region_name)
        if ec2 is None:
            ec2 = MagicMock()
            ec2.describe_regions.return_value = {
                "Regions": [{"RegionName": r} for r in ["ca-central-1", "eu-west-1"]]
            }
            found = nodes_by_region.get(region_name, [])
            ec2.describe_instances.return_value = {
                "Reservations": [{"Instances": found}] if found else []
            }
            clients["ec2"][region_name] = ec2
        return ec2

    factory.ssm = ssm
    factory.sns = sns
    factory.ec2 = clients["ec2"]
    return factory


def sweep_with(factory):
    return sweep(NOW, client_factory=factory, control_region="ca-central-1", topic_arn="arn:t")


def test_sweep_reports_no_nodes_in_an_empty_account():
    factory = make_factory({})
    result = sweep_with(factory)
    assert result["nodes"] == []
    assert result["regions_scanned"] == 2
    factory.sns.publish.assert_not_called()


def test_sweep_terminates_an_expired_node():
    factory = make_factory(
        {
            "eu-west-1": [
                node("i-0old", launched="2026-07-01T00:00:00Z", expires="2026-07-02T00:00:00Z")
            ]
        }
    )
    result = sweep_with(factory)
    assert result["reaped"] == ["i-0old"]
    factory.ec2["eu-west-1"].terminate_instances.assert_called_once_with(InstanceIds=["i-0old"])


def test_sweep_does_not_terminate_a_node_with_no_expiry():
    factory = make_factory({"eu-west-1": [node("i-0keep", launched="2026-07-01T00:00:00Z")]})
    assert sweep_with(factory)["reaped"] == []
    factory.ec2["eu-west-1"].terminate_instances.assert_not_called()


def test_sweep_emails_about_an_idle_node_and_stamps_the_tag():
    factory = make_factory(
        {"ca-central-1": [node("i-0idle", launched="2026-07-30T00:00:00Z")]}, out_bytes=10
    )
    result = sweep_with(factory)
    assert result["alerted"] == ["i-0idle"]
    factory.sns.publish.assert_called()
    factory.ec2["ca-central-1"].create_tags.assert_called_once()


def test_sweep_does_not_email_twice_within_a_day():
    recent = "2026-08-01T06:00:00Z"
    factory = make_factory(
        {"ca-central-1": [node("i-0idle", launched="2026-07-30T00:00:00Z", alerted=recent)]},
        out_bytes=10,
    )
    assert sweep_with(factory)["alerted"] == []


def test_sweep_does_not_email_about_a_busy_node():
    factory = make_factory(
        {"ca-central-1": [node("i-0busy", launched="2026-07-30T00:00:00Z")]},
        out_bytes=90_000_000,
    )
    assert sweep_with(factory)["alerted"] == []


def test_sweep_terminates_duplicates_keeping_the_newest():
    factory = make_factory(
        {
            "ca-central-1": [node("i-0older", launched="2026-07-30T00:00:00Z")],
            "eu-west-1": [node("i-0newer", launched="2026-07-31T00:00:00Z")],
        }
    )
    result = sweep_with(factory)
    assert result["duplicates"] == ["i-0older"]
    factory.ec2["ca-central-1"].terminate_instances.assert_called_once_with(
        InstanceIds=["i-0older"]
    )
    factory.ec2["eu-west-1"].terminate_instances.assert_not_called()


def test_sweep_adopts_an_untracked_node_into_the_pointer():
    factory = make_factory({"eu-west-1": [node("i-0found", launched="2026-07-30T00:00:00Z")]})
    result = sweep_with(factory)
    assert result["orphans"] == ["i-0found"]
    factory.ssm.put_parameter.assert_called_once()
    assert "i-0found" in factory.ssm.put_parameter.call_args.kwargs["Value"]


def test_sweep_clears_a_stale_pointer():
    factory = make_factory({}, pointer_value='{"region":"eu-west-1","instance_id":"i-0gone"}')
    sweep_with(factory)
    assert factory.ssm.put_parameter.call_args.kwargs["Value"] == "none"


def test_sweep_survives_a_region_that_errors():
    factory = make_factory({"eu-west-1": [node("i-0ok", launched="2026-07-30T00:00:00Z")]})
    factory("ec2", region_name="ca-central-1").describe_instances.side_effect = RuntimeError("nope")
    result = sweep_with(factory)
    assert result["nodes"] == ["i-0ok"]
