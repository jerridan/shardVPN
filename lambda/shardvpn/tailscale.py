"""Tailscale API client. Stdlib only.

The tailnet is always addressed by the '-' alias so the tailnet name never
appears in this public repository.
"""

import json
import logging
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

API = "https://api.tailscale.com/api/v2"
TIMEOUT = 10


class TailscaleError(Exception):
    """A Tailscale API call failed or returned an unexpected shape."""


def _call(request: Request) -> dict:
    try:
        with urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            return json.loads(response.read())
    except HTTPError as exc:
        # The status code is the entire debugging surface here: 401 is a bad
        # OAuth client, 403 a missing scope, 400 usually a tag the client is
        # not permitted to grant. None of these leak a secret. The body might,
        # so it is not logged.
        log.warning("tailscale api %s %s", exc.code, exc.reason)
        raise TailscaleError(f"tailscale api returned {exc.code}") from exc
    except Exception as exc:
        raise TailscaleError(f"tailscale api call failed: {type(exc).__name__}") from exc


def get_token(client_id: str, client_secret: str) -> str:
    """Exchange OAuth client credentials for a short-lived access token."""
    data = urlencode({"client_id": client_id, "client_secret": client_secret}).encode()
    request = Request(  # noqa: S310
        f"{API}/oauth/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    token = _call(request).get("access_token")
    if not token:
        raise TailscaleError("no access_token in oauth response")
    return token


def mint_auth_key(token: str, tag: str, expiry_seconds: int, description: str) -> str:
    """Mint a single-use, pre-authorized, ephemeral auth key for one launch."""
    body = json.dumps(
        {
            "capabilities": {
                "devices": {
                    "create": {
                        "reusable": False,
                        "ephemeral": True,
                        "preauthorized": True,
                        "tags": [tag],
                    }
                }
            },
            "expirySeconds": expiry_seconds,
            "description": description,
        }
    ).encode()

    request = Request(  # noqa: S310
        f"{API}/tailnet/-/keys",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    key = _call(request).get("key")
    if not key:
        raise TailscaleError("no key in auth key response")
    return key


def find_device(token: str, tag: str, hostname: str | None = None) -> dict | None:
    """Return our exit node device, or None.

    Matches on tag membership, which is guaranteed by the auth key we minted.
    Hostname only disambiguates when several tagged devices exist — matching
    on it alone would silently report a healthy node as absent if Tailscale
    ever sanitises or rewrites the value we passed to `tailscale up`.
    """
    request = Request(  # noqa: S310
        f"{API}/tailnet/-/devices",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    tagged = [d for d in _call(request).get("devices", []) if tag in (d.get("tags") or [])]
    if not tagged:
        return None
    if hostname:
        for device in tagged:
            if device.get("hostname") == hostname:
                return device
    return tagged[0]
