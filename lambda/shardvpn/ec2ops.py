"""EC2, SSM parameter and CloudWatch operations for exit nodes."""

from datetime import datetime, timedelta

TAG_ROLE = "shardvpn:role"
ROLE_VALUE = "exit-node"
TAG_LAUNCHED = "shardvpn:launched-at"
TAG_EXPIRES = "shardvpn:expires-at"
TAG_ALERTED = "shardvpn:last-idle-alert"
TAG_HOSTNAME = "shardvpn:ts-hostname"

SG_NAME = "shardvpn-exit"
SG_DESCRIPTION = "shardVPN exit node: no ingress rules, default egress only"

AMI_PARAMETER = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"

LIVE_STATES = ["pending", "running"]


def resolve_ami(ssm) -> str:
    """Resolve the current AL2023 arm64 AMI in this client's region."""
    return ssm.get_parameter(Name=AMI_PARAMETER)["Parameter"]["Value"]


def valid_regions(ec2) -> list[str]:
    return [r["RegionName"] for r in ec2.describe_regions()["Regions"]]


def default_vpc_id(ec2) -> str:
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        raise RuntimeError("no default VPC in this region")
    return vpcs[0]["VpcId"]


def ensure_security_group(ec2) -> str:
    """Return the id of the shardvpn security group, creating it if needed.

    Group names are unique per VPC, not per region, so the lookup is filtered
    to the default VPC — RunInstances without a SubnetId lands there, and a
    same-named group elsewhere would fail with InvalidParameterValue.

    No rules are added. CreateSecurityGroup already attaches allow-all egress;
    re-authorizing it returns InvalidPermission.Duplicate. Zero ingress is
    achieved by never calling authorize_security_group_ingress at all.
    """
    vpc_id = default_vpc_id(ec2)

    existing = ec2.describe_security_groups(
        Filters=[
            {"Name": "group-name", "Values": [SG_NAME]},
            {"Name": "vpc-id", "Values": [vpc_id]},
        ]
    )["SecurityGroups"]
    if existing:
        group = existing[0]
        # The zero-ingress guarantee only holds for a group this code
        # created: it never calls authorize_security_group_ingress. If
        # someone added a rule by hand (e.g. temporary 0.0.0.0/0:22 while
        # debugging) and forgot to remove it, blindly reusing the group
        # would silently attach that rule to every future launch. Fail
        # loudly instead of returning a group that no longer matches the
        # README's "zero inbound rules" claim.
        if group.get("IpPermissions"):
            raise RuntimeError(
                f"security group {group['GroupId']} ({SG_NAME}) has ingress rules; "
                "refusing to reuse it — shardVPN's zero-inbound guarantee only holds "
                "for a group this code created itself"
            )
        return group["GroupId"]

    return ec2.create_security_group(GroupName=SG_NAME, Description=SG_DESCRIPTION, VpcId=vpc_id)[
        "GroupId"
    ]


def find_nodes(ec2) -> list[dict]:
    """Every live exit node in this client's region.

    Returns a list, not the first match: duplicates must be terminated rather
    than silently ignored.
    """
    reservations = ec2.describe_instances(
        Filters=[
            {"Name": f"tag:{TAG_ROLE}", "Values": [ROLE_VALUE]},
            {"Name": "instance-state-name", "Values": LIVE_STATES},
        ]
    )["Reservations"]
    return [instance for r in reservations for instance in r["Instances"]]


def launch(
    ec2,
    *,
    ami: str,
    instance_type: str,
    sg_id: str,
    user_data: str,
    tags: dict[str, str],
    client_token: str,
) -> str:
    """Launch one exit node and return its instance id.

    No SubnetId: with a default VPC present, EC2 picks a default subnet, and
    default subnets set MapPublicIpOnLaunch, which is where the public IP the
    design depends on comes from.

    InstanceInitiatedShutdownBehavior is 'terminate' because cloud-init fails
    closed: if forwarding or the exit-node advertisement cannot be verified,
    the script leaves the tailnet and runs `shutdown -h now`. The EBS-backed
    default for that is *stop*, not terminate — and a stopped instance is
    invisible to find_nodes (which filters on pending/running), so it would
    never be reaped while EBS kept billing. This makes self-shutdown mean
    self-destruct.
    """
    response = ec2.run_instances(
        ImageId=ami,
        InstanceType=instance_type,
        MinCount=1,
        MaxCount=1,
        SecurityGroupIds=[sg_id],
        UserData=user_data,
        ClientToken=client_token,
        InstanceInitiatedShutdownBehavior="terminate",
        MetadataOptions={"HttpTokens": "required"},
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [{"Key": k, "Value": v} for k, v in tags.items()],
            }
        ],
    )
    return response["Instances"][0]["InstanceId"]


def terminate(ec2, instance_id: str) -> None:
    ec2.terminate_instances(InstanceIds=[instance_id])


def stamp_tag(ec2, instance_id: str, key: str, value: str) -> None:
    ec2.create_tags(Resources=[instance_id], Tags=[{"Key": key, "Value": value}])


def tag_value(instance: dict, key: str) -> str | None:
    for tag in instance.get("Tags", []):
        if tag["Key"] == key:
            return tag["Value"]
    return None


def network_out(cw, instance_id: str, hours: int, now: datetime) -> list[tuple[datetime, float]]:
    """NetworkOut datapoints over the trailing window, oldest first.

    Exit node traffic leaves the instance toward the client, so NetworkOut is
    the usage signal. Basic monitoring gives 5-minute datapoints for free, and
    GetMetricStatistics (not GetMetricData) sits inside CloudWatch's free API
    request tier.
    """
    datapoints = cw.get_metric_statistics(
        Namespace="AWS/EC2",
        MetricName="NetworkOut",
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        StartTime=now - timedelta(hours=hours),
        EndTime=now,
        Period=300,
        Statistics=["Sum"],
    )["Datapoints"]
    return sorted((p["Timestamp"], p["Sum"]) for p in datapoints)


def network_out_bytes(cw, instance_id: str, hours: int, now: datetime) -> int:
    return int(sum(value for _, value in network_out(cw, instance_id, hours, now)))
