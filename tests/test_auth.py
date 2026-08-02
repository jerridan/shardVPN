import base64
import hashlib
import hmac

import pytest
from shardvpn.auth import AuthError, extract_body, verify

SIGNING_KEY = "s3cr3t"
BODY = b'{"action":"up"}'
NOW = 1753977600


def sign(body: bytes, ts: int, key: str = SIGNING_KEY) -> str:
    return hmac.new(key.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def headers(ts: int = NOW, sig: str | None = None) -> dict[str, str]:
    return {
        "x-shardvpn-timestamp": str(ts),
        "x-shardvpn-signature": sig if sig is not None else sign(BODY, ts),
    }


def test_accepts_a_valid_signature():
    verify(headers(), BODY, SIGNING_KEY, NOW)


def test_header_lookup_is_case_insensitive():
    h = {"X-ShardVPN-Timestamp": str(NOW), "X-ShardVPN-Signature": sign(BODY, NOW)}
    verify(h, BODY, SIGNING_KEY, NOW)


def test_rejects_wrong_secret():
    with pytest.raises(AuthError):
        verify(headers(sig=sign(BODY, NOW, "wrong")), BODY, SIGNING_KEY, NOW)


def test_rejects_tampered_body():
    with pytest.raises(AuthError):
        verify(headers(), b'{"action":"down"}', SIGNING_KEY, NOW)


@pytest.mark.parametrize("missing", ["x-shardvpn-timestamp", "x-shardvpn-signature"])
def test_rejects_missing_header(missing):
    h = headers()
    del h[missing]
    with pytest.raises(AuthError):
        verify(h, BODY, SIGNING_KEY, NOW)


@pytest.mark.parametrize(
    "bad_ts",
    ["soon", " 1753977600 ", "+1753977600", "1_753_977_600", "١٧٥٣٩٧٧٦٠٠", "", "-1"],
)
def test_rejects_non_canonical_timestamp(bad_ts):
    # int() accepts all of these; the client and server would then disagree
    # about the canonical signed message for the same header value.
    with pytest.raises(AuthError):
        verify(headers() | {"x-shardvpn-timestamp": bad_ts}, BODY, SIGNING_KEY, NOW)


@pytest.mark.parametrize("bad_sig", ["ééé", "z" * 64, "abc", "AB" * 32, "0" * 63])
def test_rejects_malformed_signature_without_raising(bad_sig):
    # A non-ASCII value makes hmac.compare_digest raise TypeError, which would
    # surface as a 502 while every other bad request is a 403 — an oracle.
    with pytest.raises(AuthError):
        verify(headers(sig=bad_sig), BODY, SIGNING_KEY, NOW)


def test_rejects_expired_timestamp():
    with pytest.raises(AuthError):
        verify(headers(ts=NOW - 121), BODY, SIGNING_KEY, NOW)


def test_rejects_far_future_timestamp():
    with pytest.raises(AuthError):
        verify(headers(ts=NOW + 121), BODY, SIGNING_KEY, NOW)


def test_accepts_timestamp_at_the_skew_boundary():
    verify(headers(ts=NOW - 120), BODY, SIGNING_KEY, NOW)


def test_extract_body_handles_plain_text():
    assert extract_body({"body": '{"a":1}', "isBase64Encoded": False}) == b'{"a":1}'


def test_extract_body_handles_base64():
    assert extract_body({"body": base64.b64encode(BODY).decode(), "isBase64Encoded": True}) == BODY


def test_extract_body_handles_absent_body():
    assert extract_body({}) == b""


def test_extract_body_raises_authcode_on_malformed_base64():
    with pytest.raises(AuthError):
        extract_body({"body": "!!!not base64!!!", "isBase64Encoded": True})


def test_rejects_non_string_header_value():
    h = headers() | {"x-shardvpn-signature": 12345}
    with pytest.raises(AuthError):
        verify(h, BODY, SIGNING_KEY, NOW)


def test_rejects_none_header_value():
    h = headers() | {"x-shardvpn-signature": None}
    with pytest.raises(AuthError):
        verify(h, BODY, SIGNING_KEY, NOW)


def test_extract_body_raises_autherror_on_non_string_body_plain():
    with pytest.raises(AuthError):
        extract_body({"body": 12345, "isBase64Encoded": False})


def test_extract_body_raises_autherror_on_non_string_body_base64():
    with pytest.raises(AuthError):
        extract_body({"body": 12345, "isBase64Encoded": True})
