import pytest
from shardvpn.render import render_userdata


def test_substitutes_authkey_and_hostname():
    out = render_userdata("tskey-auth-abc123", "shardvpn-ca-central-1-4f2a")
    assert '--auth-key="tskey-auth-abc123"' in out
    assert '--hostname="shardvpn-ca-central-1-4f2a"' in out


def test_leaves_no_unsubstituted_placeholders():
    out = render_userdata("k", "h")
    assert "${TS_AUTHKEY}" not in out
    assert "${TS_HOSTNAME}" not in out


def test_preserves_heredoc_shell_variables():
    assert 'ethtool -K "${NETDEV}"' in render_userdata("k", "h")


def test_starts_with_a_shebang():
    assert render_userdata("k", "h").startswith("#!/bin/bash")


def test_fits_within_the_ec2_user_data_limit():
    assert len(render_userdata("k" * 64, "h" * 64).encode()) < 16384


@pytest.mark.parametrize("bad", ["", None])
def test_rejects_empty_authkey(bad):
    with pytest.raises(ValueError):
        render_userdata(bad, "h")


@pytest.mark.parametrize("bad", ["", None])
def test_rejects_empty_hostname(bad):
    with pytest.raises(ValueError):
        render_userdata("k", bad)
