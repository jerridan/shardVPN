"""The hourly sweep: reconcile the pointer, reap, deduplicate, notify."""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import boto3

from shardvpn import ec2ops
from shardvpn.settings import (
    PARAM_NAMES,
    Pointer,
    PointerUnavailable,
    get_parameter,
    read_pointer,
    write_pointer,
)
from shardvpn.ttl import is_expired, parse_rfc3339, rfc3339

log = logging.getLogger(__name__)

IDLE_WINDOW_HOURS = 24
ALERT_INTERVAL = timedelta(hours=24)
MAX_WORKERS = 10


def should_alert(
    idle_bytes: int,
    threshold: int,
    last_alert: str | None,
    now: datetime,
    launched_at: str | None = None,
) -> bool:
    """True when the node looks unused and we have not said so recently.

    A node younger than the idle window never alerts. CloudWatch basic
    monitoring publishes every 5 minutes and lags several more, so a node
    launched shortly before a sweep has near-zero NetworkOut simply because
    the data does not exist yet — indistinguishable from genuine idleness at
    this interface, since network_out_bytes returns 0 for both. Without this
    guard the user gets an email claiming the node "has been idle for 24h"
    twenty minutes after they launched it, which is both false and exactly
    the kind of noise that trains someone to ignore the alert that matters.
    """
    if idle_bytes >= threshold:
        return False

    if launched_at is not None:
        try:
            if now - parse_rfc3339(launched_at) < ALERT_INTERVAL:
                return False
        except ValueError:
            # An unparseable launch stamp should not suppress a real alert.
            pass

    if last_alert is None:
        return True
    try:
        return now - parse_rfc3339(last_alert) >= ALERT_INTERVAL
    except ValueError:
        # An unparseable stamp must not silence the alert forever.
        return True


def idle_seconds(points: list[tuple[datetime, float]], now: datetime) -> int | None:
    """Seconds since the most recent datapoint carrying any traffic."""
    if not points:
        return None
    for timestamp, value in reversed(points):
        if value > 0:
            return max(int((now - timestamp).total_seconds()), 0)
    return max(int((now - points[0][0]).total_seconds()), 0)


def _launched_at(instance: dict) -> str:
    return ec2ops.tag_value(instance, ec2ops.TAG_LAUNCHED) or ""


def sweep(
    now: datetime,
    client_factory=boto3.client,
    control_region: str | None = None,
    topic_arn: str | None = None,
) -> dict:
    """Scan every region for exit nodes; reap, deduplicate, reconcile, notify."""
    import os

    control_region = control_region or os.environ["CONTROL_REGION"]
    topic_arn = topic_arn if topic_arn is not None else os.environ.get("TOPIC_ARN", "")

    ssm = client_factory("ssm", region_name=control_region)
    sns = client_factory("sns", region_name=control_region)

    threshold = int(get_parameter(ssm, PARAM_NAMES["idle_threshold_bytes"]))

    pointer_readable = True
    try:
        pointer = read_pointer(ssm, PARAM_NAMES["current_node"])
    except PointerUnavailable:
        pointer, pointer_readable = None, False
        log.warning("pointer unreadable; not treating live nodes as intrusions")

    regions = ec2ops.valid_regions(client_factory("ec2", region_name=control_region))

    def scan(region: str) -> tuple[str, list[dict]]:
        try:
            return region, ec2ops.find_nodes(client_factory("ec2", region_name=region))
        except Exception:
            log.warning("region scan failed: %s", region)
            return region, []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        scanned = list(pool.map(scan, regions))

    found = [(region, instance) for region, nodes in scanned for instance in nodes]
    summary: dict = {
        "regions_scanned": len(regions),
        "nodes": [i["InstanceId"] for _, i in found],
        "reaped": [],
        "alerted": [],
        "orphans": [],
        "duplicates": [],
    }

    # 1. Reap anything past its expiry.
    survivors = []
    for region, instance in found:
        instance_id = instance["InstanceId"]
        expires = ec2ops.tag_value(instance, ec2ops.TAG_EXPIRES) or "never"
        if is_expired(expires, now):
            ec2ops.terminate(client_factory("ec2", region_name=region), instance_id)
            summary["reaped"].append(instance_id)
            _publish(
                sns, topic_arn, f"shardvpn: reaped {instance_id} in {region} (expired {expires})"
            )
        else:
            survivors.append((region, instance))

    # 2. Terminate duplicates, keeping the newest. Reconciling the pointer
    #    without reconciling reality would leave one running indefinitely.
    survivors.sort(key=lambda pair: _launched_at(pair[1]), reverse=True)
    for region, instance in survivors[1:]:
        instance_id = instance["InstanceId"]
        ec2ops.terminate(client_factory("ec2", region_name=region), instance_id)
        summary["duplicates"].append(instance_id)
        _publish(sns, topic_arn, f"shardvpn: terminated duplicate {instance_id} in {region}")
    survivors = survivors[:1]

    # 3. Orphan check and idle notification for the survivor.
    for region, instance in survivors:
        instance_id = instance["InstanceId"]
        ec2 = client_factory("ec2", region_name=region)

        if pointer is None or pointer.instance_id != instance_id:
            summary["orphans"].append(instance_id)
            if pointer_readable:
                _publish(
                    sns,
                    topic_arn,
                    f"shardvpn: found untracked node {instance_id} in {region}. "
                    "If you did not launch this, rotate the signing secret.",
                )
            else:
                _publish(
                    sns,
                    topic_arn,
                    f"shardvpn: node {instance_id} in {region} could not be checked "
                    "against the pointer (SSM read failed). Not necessarily a problem.",
                )

        if instance["State"]["Name"] != "running":
            continue

        cw = client_factory("cloudwatch", region_name=region)
        points = ec2ops.network_out(cw, instance_id, IDLE_WINDOW_HOURS, now)
        idle_bytes = int(sum(value for _, value in points))
        last_alert = ec2ops.tag_value(instance, ec2ops.TAG_ALERTED)

        launched_at = ec2ops.tag_value(instance, ec2ops.TAG_LAUNCHED)

        if should_alert(idle_bytes, threshold, last_alert, now, launched_at):
            launched = launched_at or "unknown"
            _publish(
                sns,
                topic_arn,
                f"shardvpn: node {instance_id} in {region} has been idle for "
                f"{IDLE_WINDOW_HOURS}h (launched {launched}, {idle_bytes} bytes out). "
                'Send {"action":"down"} if you are finished with it.',
            )
            ec2ops.stamp_tag(ec2, instance_id, ec2ops.TAG_ALERTED, rfc3339(now))
            summary["alerted"].append(instance_id)

    # 4. Reconcile the pointer: it is a cache, the region scan is the truth.
    if pointer_readable:
        if survivors:
            region, instance = survivors[0]
            if pointer is None or pointer.instance_id != instance["InstanceId"]:
                write_pointer(
                    ssm,
                    PARAM_NAMES["current_node"],
                    Pointer(region=region, instance_id=instance["InstanceId"]),
                )
        elif pointer is not None:
            write_pointer(ssm, PARAM_NAMES["current_node"], None)

    log.info("sweep complete: %s", summary)
    return summary


def _publish(sns, topic_arn: str | None, message: str) -> None:
    if not topic_arn:
        return
    sns.publish(TopicArn=topic_arn, Subject="shardvpn", Message=message)
