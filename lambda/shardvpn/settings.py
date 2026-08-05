"""SSM-backed configuration and the authoritative current-node pointer."""

import json
from dataclasses import dataclass

from botocore.exceptions import ClientError

PARAM_NAMES = {
    "signing_secret": "/shardvpn/signing-secret",
    "ts_client_id": "/shardvpn/tailscale-client-id",
    "ts_client_secret": "/shardvpn/tailscale-client-secret",
    "default_region": "/shardvpn/default-region",
    "default_ttl": "/shardvpn/default-ttl",
    "instance_type": "/shardvpn/instance-type",
    "idle_threshold_bytes": "/shardvpn/idle-threshold-bytes",
    "current_node": "/shardvpn/current-node",
}

EMPTY = "none"
SECRET_TTL_SECONDS = 300

# terraform/ssm.tf seeds the three secret SSM parameters with this exact
# literal, then leaves them alone (`lifecycle { ignore_changes = [value] }`)
# until populated for real with `aws ssm put-parameter --overwrite`. It is
# committed to a public repository, so if the out-of-band population step is
# ever skipped, this string is a valid, published signing secret that anyone
# with the Function URL can sign requests with. Kept here as a named
# constant — rather than a magic string only handler.py knows — so the two
# copies (this one, and the literal in terraform/ssm.tf) are visibly the same
# known value; Terraform has no way to import this constant directly, so
# keeping the literal in ssm.tf in sync with this one is a manual invariant.
PLACEHOLDER_SIGNING_SECRET = "PLACEHOLDER-set-with-aws-ssm-put-parameter"

_SECRET_CACHE: dict[str, tuple[float, str]] = {}


class PointerUnavailable(Exception):
    """The pointer could not be read. Distinct from 'there is no node'."""


@dataclass(frozen=True)
class Pointer:
    """Which region holds the live node, and which instance it is.

    DescribeInstances is regional, so this is what makes 'is a node already
    up?' answerable without scanning every region on the request path.
    """

    region: str
    instance_id: str


def get_parameter(ssm, name: str, *, decrypt: bool = False) -> str:
    kwargs = {"Name": name}
    if decrypt:
        kwargs["WithDecryption"] = True
    return ssm.get_parameter(**kwargs)["Parameter"]["Value"]


def cached_secret(ssm, name: str, now: float) -> str:
    """Read a SecureString, memoised for SECRET_TTL_SECONDS.

    Without this, every unauthenticated request costs a GetParameter plus a
    KMS Decrypt before the signature is even checked. The TTL bounds how long
    a rotated secret takes to take effect.
    """
    cached = _SECRET_CACHE.get(name)
    if cached and now - cached[0] < SECRET_TTL_SECONDS:
        return cached[1]

    value = get_parameter(ssm, name, decrypt=True)
    _SECRET_CACHE[name] = (now, value)
    return value


def read_pointer(ssm, name: str) -> Pointer | None:
    """Return the tracked node, or None if there genuinely is not one.

    Fails closed: only ParameterNotFound means 'no node'. Any other error
    raises, because the caller may be about to launch an instance and a
    transient SSM error must not be read as 'nothing is running'.
    """
    try:
        raw = get_parameter(ssm, name)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ParameterNotFound":
            return None
        raise PointerUnavailable(exc.response["Error"]["Code"]) from exc

    if not raw or raw == EMPTY:
        return None

    try:
        data = json.loads(raw)
        return Pointer(region=data["region"], instance_id=data["instance_id"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PointerUnavailable("malformed pointer") from exc


def write_pointer(ssm, name: str, pointer: Pointer | None) -> None:
    value = (
        EMPTY
        if pointer is None
        else json.dumps({"region": pointer.region, "instance_id": pointer.instance_id})
    )
    ssm.put_parameter(Name=name, Value=value, Type="String", Overwrite=True)
