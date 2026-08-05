"""Renders the cloud-init script. Reads the template bundled beside it."""

from pathlib import Path
from string import Template

_TEMPLATE_PATH = Path(__file__).with_name("userdata.sh")


def render_userdata(authkey: str, hostname: str) -> str:
    """Substitute the auth key and hostname into the bootstrap script.

    safe_substitute so that ordinary shell variables in the script — notably
    ${NETDEV} inside a heredoc — survive untouched.
    """
    if not authkey:
        raise ValueError("authkey is required")
    if not hostname:
        raise ValueError("hostname is required")

    return Template(_TEMPLATE_PATH.read_text()).safe_substitute(
        TS_AUTHKEY=authkey, TS_HOSTNAME=hostname
    )
