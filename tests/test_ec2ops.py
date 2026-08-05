# tests/test_ec2ops.py
from datetime import UTC, datetime

import boto3
import pytest
from botocore.stub import ANY, Stubber

from shardvpn import ec2ops

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def ec2():
    return boto3.client("ec2", region_name="ca-central-1")


@pytest.fixture
def cw():
    return boto3.client("cloudwatch", region_name="ca-central-1")


def test_resolve_ami_reads_the_public_parameter():
    ssm = boto3.client("ssm", region_name="ca-central-1")
    with Stubber(ssm) as stub:
        stub.add_response(
            "get_parameter", {"Parameter": {"Value": "ami-0abc"}}, {"Name": ec2ops.AMI_PARAMETER}
        )
        assert ec2ops.resolve_ami(ssm) == "ami-0abc"


def test_ensure_security_group_reuses_an_existing_group_in_the_default_vpc(ec2):
    with Stubber(ec2) as stub:
        stub.add_response(
            "describe_vpcs",
            {"Vpcs": [{"VpcId": "vpc-default"}]},
            {"Filters": [{"Name": "isDefault", "Values": ["true"]}]},
        )
        stub.add_response(
            "describe_security_groups",
            {"SecurityGroups": [{"GroupId": "sg-existing"}]},
            {
                "Filters": [
                    {"Name": "group-name", "Values": [ec2ops.SG_NAME]},
                    {"Name": "vpc-id", "Values": ["vpc-default"]},
                ]
            },
        )
        assert ec2ops.ensure_security_group(ec2) == "sg-existing"


def test_ensure_security_group_refuses_to_reuse_a_group_with_ingress_rules(ec2):
    # The zero-ingress guarantee only holds for a group this code created.
    # If a reused group somehow has ingress rules (e.g. added by hand while
    # debugging and forgotten), returning it silently would make the
    # README's "zero inbound rules" claim false with no signal at all.
    with Stubber(ec2) as stub:
        stub.add_response(
            "describe_vpcs",
            {"Vpcs": [{"VpcId": "vpc-default"}]},
            {"Filters": [{"Name": "isDefault", "Values": ["true"]}]},
        )
        stub.add_response(
            "describe_security_groups",
            {
                "SecurityGroups": [
                    {
                        "GroupId": "sg-existing",
                        "IpPermissions": [
                            {
                                "IpProtocol": "tcp",
                                "FromPort": 22,
                                "ToPort": 22,
                                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                            }
                        ],
                    }
                ]
            },
            {
                "Filters": [
                    {"Name": "group-name", "Values": [ec2ops.SG_NAME]},
                    {"Name": "vpc-id", "Values": ["vpc-default"]},
                ]
            },
        )
        with pytest.raises(RuntimeError, match="ingress"):
            ec2ops.ensure_security_group(ec2)


def test_ensure_security_group_creates_one_and_adds_no_rules(ec2):
    # CreateSecurityGroup already attaches allow-all egress. Adding it again
    # returns InvalidPermission.Duplicate and breaks every first launch.
    with Stubber(ec2) as stub:
        stub.add_response("describe_vpcs", {"Vpcs": [{"VpcId": "vpc-default"}]})
        stub.add_response("describe_security_groups", {"SecurityGroups": []})
        stub.add_response(
            "create_security_group",
            {"GroupId": "sg-new"},
            {"GroupName": ec2ops.SG_NAME, "Description": ANY, "VpcId": "vpc-default"},
        )
        assert ec2ops.ensure_security_group(ec2) == "sg-new"
        # Stubber raises if any un-queued call is made, so this asserts that
        # neither authorize_security_group_egress nor _ingress was called.


def test_find_nodes_returns_empty_when_none(ec2):
    with Stubber(ec2) as stub:
        stub.add_response(
            "describe_instances",
            {"Reservations": []},
            {
                "Filters": [
                    {"Name": f"tag:{ec2ops.TAG_ROLE}", "Values": [ec2ops.ROLE_VALUE]},
                    {"Name": "instance-state-name", "Values": ec2ops.LIVE_STATES},
                ]
            },
        )
        assert ec2ops.find_nodes(ec2) == []


def test_find_nodes_returns_all_live_instances(ec2):
    reservations = [
        {"Instances": [{"InstanceId": "i-0aaa", "State": {"Name": "running"}}]},
        {"Instances": [{"InstanceId": "i-0bbb", "State": {"Name": "pending"}}]},
    ]
    with Stubber(ec2) as stub:
        stub.add_response(
            "describe_instances",
            {"Reservations": reservations},
            {
                "Filters": [
                    {"Name": f"tag:{ec2ops.TAG_ROLE}", "Values": [ec2ops.ROLE_VALUE]},
                    {"Name": "instance-state-name", "Values": ec2ops.LIVE_STATES},
                ]
            },
        )
        assert [i["InstanceId"] for i in ec2ops.find_nodes(ec2)] == ["i-0aaa", "i-0bbb"]


def test_launch_returns_the_instance_id(ec2):
    with Stubber(ec2) as stub:
        stub.add_response("run_instances", {"Instances": [{"InstanceId": "i-0new"}]})
        assert (
            ec2ops.launch(
                ec2,
                ami="ami-0abc",
                instance_type="t4g.small",
                sg_id="sg-1",
                user_data="#!/bin/bash\n",
                tags={ec2ops.TAG_ROLE: ec2ops.ROLE_VALUE},
                client_token="token-1",
            )
            == "i-0new"
        )


def test_launch_sends_raw_user_data_and_a_client_token(ec2):
    # boto3 base64-encodes UserData on the wire; the Stubber sees the raw str.
    with Stubber(ec2) as stub:
        stub.add_response(
            "run_instances",
            {"Instances": [{"InstanceId": "i-0new"}]},
            {
                "ImageId": "ami-0abc",
                "InstanceType": "t4g.small",
                "MinCount": 1,
                "MaxCount": 1,
                "SecurityGroupIds": ["sg-1"],
                "UserData": "#!/bin/bash\n",
                "ClientToken": "token-1",
                "InstanceInitiatedShutdownBehavior": "terminate",
                "MetadataOptions": {"HttpTokens": "required"},
                "TagSpecifications": [
                    {
                        "ResourceType": "instance",
                        "Tags": [{"Key": ec2ops.TAG_ROLE, "Value": ec2ops.ROLE_VALUE}],
                    }
                ],
            },
        )
        ec2ops.launch(
            ec2,
            ami="ami-0abc",
            instance_type="t4g.small",
            sg_id="sg-1",
            user_data="#!/bin/bash\n",
            tags={ec2ops.TAG_ROLE: ec2ops.ROLE_VALUE},
            client_token="token-1",
        )


def test_terminate_calls_terminate_instances(ec2):
    with Stubber(ec2) as stub:
        stub.add_response(
            "terminate_instances", {"TerminatingInstances": []}, {"InstanceIds": ["i-0abc"]}
        )
        ec2ops.terminate(ec2, "i-0abc")


def test_stamp_tag_calls_create_tags(ec2):
    with Stubber(ec2) as stub:
        stub.add_response(
            "create_tags",
            {},
            {
                "Resources": ["i-0abc"],
                "Tags": [{"Key": ec2ops.TAG_ALERTED, "Value": "2026-08-01T12:00:00Z"}],
            },
        )
        ec2ops.stamp_tag(ec2, "i-0abc", ec2ops.TAG_ALERTED, "2026-08-01T12:00:00Z")


def test_valid_regions_lists_region_names(ec2):
    with Stubber(ec2) as stub:
        stub.add_response(
            "describe_regions",
            {"Regions": [{"RegionName": "ca-central-1"}, {"RegionName": "eu-west-1"}]},
        )
        assert ec2ops.valid_regions(ec2) == ["ca-central-1", "eu-west-1"]


def test_network_out_bytes_sums_datapoints(cw):
    with Stubber(cw) as stub:
        stub.add_response(
            "get_metric_statistics",
            {"Datapoints": [{"Sum": 1000.0, "Timestamp": NOW}, {"Sum": 2500.0, "Timestamp": NOW}]},
            {
                "Namespace": "AWS/EC2",
                "MetricName": "NetworkOut",
                "Dimensions": [{"Name": "InstanceId", "Value": "i-0abc"}],
                "StartTime": ANY,
                "EndTime": ANY,
                "Period": 300,
                "Statistics": ["Sum"],
            },
        )
        assert ec2ops.network_out_bytes(cw, "i-0abc", 24, NOW) == 3500


def test_network_out_bytes_handles_no_datapoints(cw):
    with Stubber(cw) as stub:
        stub.add_response("get_metric_statistics", {"Datapoints": []})
        assert ec2ops.network_out_bytes(cw, "i-0abc", 24, NOW) == 0


def test_network_out_returns_sorted_timestamped_points(cw):
    early = datetime(2026, 8, 1, 10, 0, 0, tzinfo=UTC)
    late = datetime(2026, 8, 1, 11, 0, 0, tzinfo=UTC)
    with Stubber(cw) as stub:
        stub.add_response(
            "get_metric_statistics",
            {"Datapoints": [{"Sum": 5.0, "Timestamp": late}, {"Sum": 9.0, "Timestamp": early}]},
            {
                "Namespace": "AWS/EC2",
                "MetricName": "NetworkOut",
                "Dimensions": [{"Name": "InstanceId", "Value": "i-0abc"}],
                "StartTime": ANY,
                "EndTime": ANY,
                "Period": 300,
                "Statistics": ["Sum"],
            },
        )
        assert ec2ops.network_out(cw, "i-0abc", 24, NOW) == [(early, 9.0), (late, 5.0)]


def test_tag_value_reads_a_tag():
    instance = {"Tags": [{"Key": "shardvpn:role", "Value": "exit-node"}]}
    assert ec2ops.tag_value(instance, "shardvpn:role") == "exit-node"
    assert ec2ops.tag_value(instance, "missing") is None


def test_tag_value_handles_an_untagged_instance():
    assert ec2ops.tag_value({}, "shardvpn:role") is None
