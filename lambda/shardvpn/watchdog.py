"""The hourly sweep: reconcile the pointer, reap, deduplicate, notify."""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import boto3

from shardvpn import ec2ops
from shardvpn.settings import (
    PARAM_NAMES,
    Pointer,
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


def _order_survivors(
    survivors: list[tuple[str, dict]],
) -> tuple[list[tuple[str, dict]], bool]:
    """Order survivors newest-first by parsed launch time, or refuse to guess.

    Returns (ordered, orderable). Ordering is refused entirely — not defaulted
    to "sorts last" — when any survivor's shardvpn:launched-at tag is missing
    or malformed. A lexical string sort would rank a value like "unknown" or
    "tampered" above a real RFC3339 timestamp, because 'u'/'t' > any digit:
    that would terminate the genuinely newest, actively-used node and keep
    the corrupted one. Leaving a duplicate running costs about $0.52/day and
    sends an email; terminating the wrong one drops the user's live VPN
    connection mid-session and cannot be undone. When unorderable, `ordered`
    is returned unchanged and the caller must not use it to pick a "newest".
    """
    if len(survivors) <= 1:
        return survivors, True

    parsed: list[tuple[datetime, str, dict]] = []
    for region, instance in survivors:
        raw = ec2ops.tag_value(instance, ec2ops.TAG_LAUNCHED)
        if raw is None:
            return survivors, False
        try:
            parsed.append((parse_rfc3339(raw), region, instance))
        except ValueError:
            return survivors, False

    parsed.sort(key=lambda item: item[0], reverse=True)
    return [(region, instance) for _, region, instance in parsed], True


def _safe_terminate(
    client_factory, region: str, instance_id: str, summary: dict, reason: str
) -> bool:
    """Terminate one instance; record and continue instead of raising.

    A throttled or failed terminate_instances call must not abort the sweep:
    every remaining node still needs to be reaped or deduplicated, and the
    pointer still needs to be reconciled once the loop finishes.
    """
    try:
        ec2ops.terminate(client_factory("ec2", region_name=region), instance_id)
        return True
    except Exception as exc:
        log.warning("failed to terminate %s in %s (%s): %s", instance_id, region, reason, exc)
        summary["failures"].append(f"terminate {instance_id} in {region} ({reason}): {exc}")
        return False


def sweep(
    now: datetime,
    client_factory=boto3.client,
    control_region: str | None = None,
    topic_arn: str | None = None,
) -> dict:
    """Scan every region for exit nodes; reap, deduplicate, reconcile, notify.

    This is the only unattended code in the system and the only thing
    bounding cost while TTL defaults to "no expiry", so no single node's AWS
    call is allowed to abort the rest of the cycle: every terminate call and
    every per-node idle check is isolated so a failure is recorded and
    skipped rather than propagated, and pointer reconciliation (step 4) is
    guaranteed to run afterward regardless. Region discovery (`valid_regions`)
    is the one call that still aborts the sweep on failure: without it there
    is no data at all, and reconciling against zero nodes we failed to
    *discover* is indistinguishable from zero nodes that genuinely don't
    exist — the difference being that only one of those should clear the
    pointer.
    """
    import os

    control_region = control_region or os.environ["CONTROL_REGION"]
    topic_arn = topic_arn if topic_arn is not None else os.environ.get("TOPIC_ARN", "")

    ssm = client_factory("ssm", region_name=control_region)
    sns = client_factory("sns", region_name=control_region)

    summary: dict = {
        "regions_scanned": 0,
        "nodes": [],
        "reaped": [],
        "alerted": [],
        "orphans": [],
        "duplicates": [],
        "failures": [],
    }

    threshold: int | None = None
    try:
        threshold = int(get_parameter(ssm, PARAM_NAMES["idle_threshold_bytes"]))
    except Exception as exc:
        log.warning("could not read idle threshold; idle alerts disabled this cycle: %s", exc)
        summary["failures"].append(f"idle-threshold-bytes: {exc}")

    pointer_readable = True
    try:
        pointer = read_pointer(ssm, PARAM_NAMES["current_node"])
    except Exception as exc:
        pointer, pointer_readable = None, False
        log.warning("pointer unreadable; not treating live nodes as intrusions: %s", exc)

    try:
        regions = ec2ops.valid_regions(client_factory("ec2", region_name=control_region))
    except Exception as exc:
        log.error("could not enumerate regions; sweep aborted: %s", exc)
        summary["failures"].append(f"valid_regions: {exc}")
        _notify_failures(sns, topic_arn, summary)
        return summary

    def scan(region: str, ec2) -> tuple[str, list[dict], Exception | None]:
        try:
            return region, ec2ops.find_nodes(ec2), None
        except Exception as exc:
            log.warning("region scan failed: %s: %s", region, type(exc).__name__)
            return region, [], exc

    # Clients are created here, on the main thread, one per region, before
    # any thread touches them. boto3.client (the default client_factory) is
    # documented as not thread-safe when clients are created concurrently
    # from the same session — a loader/data-cache race there raises inside
    # scan(), which without this would be indistinguishable from "no nodes in
    # this region" and feed the exact pointer-clearing bug below.
    region_clients = {region: client_factory("ec2", region_name=region) for region in regions}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        scanned = list(pool.map(lambda r: scan(r, region_clients[r]), regions))

    found = [(region, instance) for region, nodes, _ in scanned for instance in nodes]
    summary["regions_scanned"] = len(regions)
    summary["nodes"] = [i["InstanceId"] for _, i in found]

    # A region we failed to scan is undiscovered, not confirmed absent — the
    # same distinction valid_regions failing makes for the whole sweep,
    # applied per-region. Recorded so step 4 can refuse to clear the pointer
    # on unproven absence, and so a scan that fails every hour is alarmable
    # via _notify_failures rather than silently returning "nothing found".
    scan_failed_regions = [region for region, _, exc in scanned if exc is not None]
    for region, _, exc in scanned:
        if exc is not None:
            summary["failures"].append(f"region scan failed: {region} ({type(exc).__name__})")

    # 1. Reap anything past its expiry. A failed terminate is recorded and
    #    skipped, not fatal — the next node still needs reaping and the
    #    pointer still needs reconciling below.
    survivors = []
    for region, instance in found:
        instance_id = instance["InstanceId"]
        expires = ec2ops.tag_value(instance, ec2ops.TAG_EXPIRES) or "never"
        if is_expired(expires, now):
            if _safe_terminate(client_factory, region, instance_id, summary, "expired"):
                summary["reaped"].append(instance_id)
                _publish(
                    sns,
                    topic_arn,
                    f"shardvpn: reaped {instance_id} in {region} (expired {expires})",
                )
        else:
            survivors.append((region, instance))

    # 2. Terminate duplicates, keeping the newest by parsed launch time.
    #    Reconciling the pointer without reconciling reality would leave a
    #    duplicate running indefinitely — but see _order_survivors: a
    #    malformed launch tag must refuse the guess, not make it.
    ordered, orderable = _order_survivors(survivors)
    if not orderable:
        detail = ", ".join(
            f"{instance['InstanceId']} in {region}" for region, instance in survivors
        )
        summary["failures"].append(f"unresolved duplicates (unordered launch tags): {detail}")
        _publish(
            sns,
            topic_arn,
            "shardvpn: found multiple exit nodes whose launch time could not be "
            f"determined ({detail}). Terminating the wrong one could drop a live "
            "VPN session, so none were touched — please resolve this manually.",
        )
        final_candidates: list[tuple[str, dict]] = []
    else:
        for region, instance in ordered[1:]:
            instance_id = instance["InstanceId"]
            if _safe_terminate(client_factory, region, instance_id, summary, "duplicate"):
                summary["duplicates"].append(instance_id)
                _publish(
                    sns, topic_arn, f"shardvpn: terminated duplicate {instance_id} in {region}"
                )
        final_candidates = ordered[:1]

    # 3. Orphan check and idle notification for the sole remaining candidate,
    #    if any. The alert block is isolated per node: a failed CloudWatch
    #    read or tag stamp must not skip reconciliation below.
    for region, instance in final_candidates:
        instance_id = instance["InstanceId"]

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

        if instance["State"]["Name"] != "running" or threshold is None:
            continue

        try:
            ec2 = client_factory("ec2", region_name=region)
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
        except Exception as exc:
            log.warning("idle check failed for %s in %s: %s", instance_id, region, exc)
            summary["failures"].append(f"idle check {instance_id} in {region}: {exc}")

    # 4. Reconcile the pointer: it is a cache, the region scan is the truth.
    #    Always attempted — this is what stops `up` from launching a second
    #    instance against a stale pointer, so it must run even when earlier
    #    steps hit isolated per-node failures. The one exception is
    #    `orderable is False`: with an unresolved duplicate set we do not
    #    know which node is "the" node, and guessing here is exactly the
    #    mistake step 2 refuses to make — so the pointer is left untouched
    #    until the ambiguity is resolved.
    #
    #    Adopting a *found* node into an empty/stale pointer is still safe
    #    even when some other region failed to scan — that is positive
    #    evidence, not a guess. Clearing the pointer to "none" is not: if any
    #    region failed to scan, "no survivors" is indistinguishable from "the
    #    tracked node is sitting in the region we couldn't see", and clearing
    #    on unproven absence is exactly the mistake this sweep's docstring
    #    calls out for `valid_regions` — the same argument applies per-region.
    if pointer_readable and orderable:
        try:
            if final_candidates:
                region, instance = final_candidates[0]
                if pointer is None or pointer.instance_id != instance["InstanceId"]:
                    write_pointer(
                        ssm,
                        PARAM_NAMES["current_node"],
                        Pointer(region=region, instance_id=instance["InstanceId"]),
                    )
            elif pointer is not None:
                if scan_failed_regions:
                    log.warning(
                        "not clearing pointer: %d region(s) failed to scan, absence unproven: %s",
                        len(scan_failed_regions),
                        scan_failed_regions,
                    )
                else:
                    write_pointer(ssm, PARAM_NAMES["current_node"], None)
        except Exception as exc:
            log.warning("pointer reconciliation failed: %s", exc)
            summary["failures"].append(f"pointer reconciliation: {exc}")

    _notify_failures(sns, topic_arn, summary)

    log.info("sweep complete: %s", summary)
    return summary


def _notify_failures(sns, topic_arn: str | None, summary: dict) -> None:
    """Publish a summary of this sweep's failures, if there were any.

    `summary["failures"]` otherwise only ever reaches log.info: on its own
    that satisfies nothing external, because the CloudWatch alarm in
    alarms.tf watches Lambda `Errors`, which only counts unhandled
    exceptions/timeouts/OOM — never a successful invocation that returns a
    summary full of caught, recorded failures. Without this, a sweep that
    fails at every single node forever (e.g. ec2:DescribeRegions itself
    starts failing) logs quietly and alarms nothing, every hour, forever.
    Routed through the existing best-effort _publish so a publish failure
    here can't resurrect the old propagate-and-abort behaviour.
    """
    if not summary["failures"]:
        return
    _publish(
        sns,
        topic_arn,
        "shardvpn: sweep completed with failures: " + "; ".join(summary["failures"]),
    )


def _publish(sns, topic_arn: str | None, message: str) -> None:
    """Best-effort SNS publish.

    Never raises — a notification failure must never abandon a reap, a
    dedup, or the pointer reconciliation that follows.
    """
    if not topic_arn:
        return
    try:
        sns.publish(TopicArn=topic_arn, Subject="shardvpn", Message=message)
    except Exception as exc:
        log.warning("sns publish failed: %s", exc)
