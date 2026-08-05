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


def test_authkey_appears_exactly_once_in_rendered_output():
    # userdata.sh's header comment used to mention ${TS_AUTHKEY} literally,
    # so safe_substitute rewrote it into a second, nonsensical copy of the
    # live auth key sitting in a comment. Pin that the key appears exactly
    # where it belongs (the `tailscale up --auth-key=` line) and nowhere
    # else.
    out = render_userdata("tskey-auth-onceonly", "shardvpn-ca-central-1-4f2a")
    assert out.count("tskey-auth-onceonly") == 1


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


# Ordering invariants. Shellcheck checks syntax, not cross-line ordering, and
# none of the tests above touch userdata.sh's content — a future edit could
# reorder these steps and both shellcheck and the rest of the suite would
# stay green while silently recreating the "advertises but routes nothing"
# failure this file exists to prevent.


def test_installs_dnf_plugins_core_before_using_config_manager():
    out = render_userdata("k", "h")
    assert out.index("dnf install -y dnf-plugins-core") < out.index("dnf config-manager --add-repo")


def test_enables_forwarding_before_starting_tailscaled():
    out = render_userdata("k", "h")
    assert out.index("net.ipv4.ip_forward = 1") < out.index("systemctl enable --now tailscaled")


def test_gro_unit_is_ordered_before_tailscaled():
    assert "Before=tailscaled.service" in render_userdata("k", "h")


def test_verifies_forwarding_before_advertising_exit_node():
    out = render_userdata("k", "h")
    up = out.index("tailscale up")
    forwarding_check = out.index('echo "shardvpn: verifying forwarding"')
    advertise = out.index("tailscale set --advertise-exit-node")
    assert up < forwarding_check < advertise


def test_advertisement_retry_loop_is_bounded():
    assert "seq 1 15" in render_userdata("k", "h")


def test_err_trap_is_set_before_the_first_dnf_call():
    # Without this trap, a non-zero exit from an early command (dnf install,
    # dnf config-manager --add-repo, systemctl enable --now
    # shardvpn-gro.service) aborts cloud-init under `set -e` with the
    # instance left running and billing — the pointer is already written, so
    # nothing else in the system notices. It must be armed before the first
    # fallible command, not somewhere later in the script.
    out = render_userdata("k", "h")
    assert "shutdown -h now' ERR" in out
    # "trap '" (with the opening quote) is unique to the ERR-trap assignment
    # itself; the bare word "trap" and "dnf install" (without "-y") also
    # appear in prose comments elsewhere in the file, so a looser search
    # would false-positive on those instead of the real statements.
    trap_index = out.index("trap '")
    first_dnf_command_index = out.index("dnf install -y dnf-plugins-core")
    assert trap_index < first_dnf_command_index


def test_err_trap_is_disabled_before_the_tailnet_join():
    # The join and verification steps below already fail closed on their own
    # terms (logout + shutdown); the generic bootstrap trap must not also
    # fire across them. "tailscale up \" (with the line continuation) is
    # unique to the actual join command; bare "tailscale up" also appears in
    # prose comments both above and below it.
    out = render_userdata("k", "h")
    disable_index = out.index("trap - ERR")
    join_index = out.index("tailscale up \\")
    assert disable_index < join_index
