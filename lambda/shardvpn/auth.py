"""HMAC request verification for the Function URL. Pure: no AWS, no I/O."""

import base64
import binascii
import hashlib
import hmac
import re
from collections.abc import Mapping

TIMESTAMP_HEADER = "x-shardvpn-timestamp"
SIGNATURE_HEADER = "x-shardvpn-signature"
MAX_SKEW_SECONDS = 120

_TIMESTAMP_RE = re.compile(r"\A[0-9]{1,11}\Z")
_SIGNATURE_RE = re.compile(r"\A[0-9a-f]{64}\Z")


class AuthError(Exception):
    """Any verification failure. Deliberately carries no detail: the caller
    must not be able to tell which check failed."""


def extract_body(event: dict) -> bytes:
    """Return the request body as the exact bytes the client signed."""
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        try:
            return base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise AuthError from exc
    try:
        return raw.encode()
    except (AttributeError, TypeError):
        raise AuthError from None


def verify(
    headers: Mapping[str, str],
    raw_body: bytes,
    secret: str,
    now: int,
    max_skew: int = MAX_SKEW_SECONDS,
) -> None:
    """Raise AuthError unless the request carries a fresh, valid signature."""
    try:
        lowered = {k.lower(): v for k, v in headers.items()}
    except (AttributeError, TypeError):
        raise AuthError from None

    raw_ts = lowered.get(TIMESTAMP_HEADER, "")
    provided = lowered.get(SIGNATURE_HEADER, "")

    # Validate shape before parsing. int() accepts whitespace, a leading '+',
    # underscores and non-ASCII digits; compare_digest raises TypeError on any
    # non-ASCII string. Both would break the 'every failure is 403' property.
    try:
        if not _TIMESTAMP_RE.match(raw_ts) or not _SIGNATURE_RE.match(provided):
            raise AuthError
    except TypeError:
        raise AuthError from None

    if abs(now - int(raw_ts)) > max_skew:
        raise AuthError

    # Sign raw_ts verbatim, not str(int(raw_ts)): _TIMESTAMP_RE permits
    # leading zeros ("01700000000"), which int() silently normalises away.
    # Re-serializing here would make the server verify a different message
    # than the client signed — a leading-zero timestamp would always 403,
    # indistinguishable from any other bad request.
    expected = hmac.new(
        secret.encode(), f"{raw_ts}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, provided):
        raise AuthError
