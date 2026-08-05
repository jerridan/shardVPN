import io
import json
from unittest.mock import patch

import pytest

from shardvpn.tailscale import TailscaleError, find_device, get_token, mint_auth_key

TAG = "tag:shardvpn-exit"


def fake_response(payload: dict):
    return io.BytesIO(json.dumps(payload).encode())


def test_get_token_posts_client_credentials():
    with patch("shardvpn.tailscale.urlopen") as opener:
        opener.return_value.__enter__.return_value = fake_response({"access_token": "tok"})
        assert get_token("cid", "csec") == "tok"

        request = opener.call_args[0][0]
        assert request.full_url == "https://api.tailscale.com/api/v2/oauth/token"
        assert b"client_id=cid" in request.data
        assert b"client_secret=csec" in request.data


def test_mint_auth_key_sends_the_expected_capability_body():
    with patch("shardvpn.tailscale.urlopen") as opener:
        opener.return_value.__enter__.return_value = fake_response({"key": "tskey-auth-xyz"})
        assert mint_auth_key("tok", TAG, 600, "shardvpn test") == "tskey-auth-xyz"

        request = opener.call_args[0][0]
        assert request.full_url == "https://api.tailscale.com/api/v2/tailnet/-/keys"
        assert request.headers["Authorization"] == "Bearer tok"

        body = json.loads(request.data)
        assert body["capabilities"]["devices"]["create"] == {
            "reusable": False,
            "ephemeral": True,
            "preauthorized": True,
            "tags": [TAG],
        }
        assert body["expirySeconds"] == 600


def test_mint_auth_key_raises_when_no_key_returned():
    with patch("shardvpn.tailscale.urlopen") as opener:
        opener.return_value.__enter__.return_value = fake_response({})
        with pytest.raises(TailscaleError):
            mint_auth_key("tok", TAG, 600, "d")


def test_find_device_matches_on_tag_not_hostname():
    # Whether `tailscale up --hostname=X` surfaces verbatim as `hostname` is
    # not guaranteed. Tag membership is, because we minted the key.
    devices = {
        "devices": [
            {"id": "1", "hostname": "laptop", "tags": ["tag:personal"]},
            {"id": "2", "hostname": "something-else-entirely", "tags": [TAG]},
        ]
    }
    with patch("shardvpn.tailscale.urlopen") as opener:
        opener.return_value.__enter__.return_value = fake_response(devices)
        assert find_device("tok", TAG)["id"] == "2"


def test_find_device_prefers_a_hostname_match_when_several_are_tagged():
    devices = {
        "devices": [
            {"id": "1", "hostname": "shardvpn-old", "tags": [TAG]},
            {"id": "2", "hostname": "shardvpn-new", "tags": [TAG]},
        ]
    }
    with patch("shardvpn.tailscale.urlopen") as opener:
        opener.return_value.__enter__.return_value = fake_response(devices)
        assert find_device("tok", TAG, hostname="shardvpn-new")["id"] == "2"


def test_find_device_returns_none_when_no_tagged_device():
    devices = {"devices": [{"id": "1", "hostname": "laptop", "tags": ["tag:personal"]}]}
    with patch("shardvpn.tailscale.urlopen") as opener:
        opener.return_value.__enter__.return_value = fake_response(devices)
        assert find_device("tok", TAG) is None


def test_find_device_handles_devices_without_a_tags_key():
    with patch("shardvpn.tailscale.urlopen") as opener:
        opener.return_value.__enter__.return_value = fake_response({"devices": [{"id": "1"}]})
        assert find_device("tok", TAG) is None


def test_find_device_falls_back_to_the_only_tagged_device_when_hostname_does_not_match():
    # The hostname we asked for didn't round-trip, but there is exactly one
    # tagged device, so there is no ambiguity about which one is ours.
    devices = {"devices": [{"id": "1", "hostname": "something-else", "tags": [TAG]}]}
    with patch("shardvpn.tailscale.urlopen") as opener:
        opener.return_value.__enter__.return_value = fake_response(devices)
        assert find_device("tok", TAG, hostname="shardvpn-new")["id"] == "1"


def test_find_device_returns_none_when_hostname_does_not_match_and_several_are_tagged():
    # Two tagged devices and neither matches the requested hostname: this is
    # the stale-node-during-a-region-switch case. We cannot tell which
    # device is ours, so we must not guess.
    devices = {
        "devices": [
            {"id": "1", "hostname": "shardvpn-old", "tags": [TAG]},
            {"id": "2", "hostname": "something-else-entirely", "tags": [TAG]},
        ]
    }
    with patch("shardvpn.tailscale.urlopen") as opener:
        opener.return_value.__enter__.return_value = fake_response(devices)
        assert find_device("tok", TAG, hostname="shardvpn-new") is None


def test_uses_the_dash_tailnet_alias_never_a_name():
    with patch("shardvpn.tailscale.urlopen") as opener:
        opener.return_value.__enter__.return_value = fake_response({"devices": []})
        find_device("tok", TAG)
        assert "/tailnet/-/" in opener.call_args[0][0].full_url
