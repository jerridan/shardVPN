#!/bin/bash
# shardVPN exit node bootstrap.
# ${TS_AUTHKEY} and ${TS_HOSTNAME} are substituted by render.py via
# string.Template before this is passed as EC2 user data.
#
# Ordering matters: forwarding and GRO must be configured before tailscaled
# starts, or the node advertises itself and silently routes nothing.
set -euo pipefail

exec > >(tee /var/log/shardvpn-init.log) 2>&1

echo "shardvpn: enabling IP forwarding"
cat > /etc/sysctl.d/99-tailscale.conf <<'SYSCTL'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
SYSCTL
sysctl --system

# dnf config-manager lives in dnf-plugins-core, which is not reliably present
# on the base AL2023 AMI. Installing it first stops `set -e` from aborting
# after the GRO unit exists but before tailscale does — which would leave a
# running, billed instance that never joins the tailnet.
echo "shardvpn: installing prerequisites"
dnf install -y dnf-plugins-core ethtool

echo "shardvpn: installing UDP GRO forwarding unit"
# Tailscale documents persisting this with a networkd-dispatcher hook, which
# does not exist on Amazon Linux 2023. A systemd oneshot ordered before
# tailscaled is the AL2023 equivalent.
cat > /usr/local/sbin/shardvpn-gro <<'GRO'
#!/bin/bash
set -euo pipefail
NETDEV=$(ip -o route get 8.8.8.8 | cut -f 5 -d " ")
ethtool -K "${NETDEV}" rx-udp-gro-forwarding on rx-gro-list off
GRO
chmod 0755 /usr/local/sbin/shardvpn-gro

cat > /etc/systemd/system/shardvpn-gro.service <<'UNIT'
[Unit]
Description=shardVPN UDP GRO forwarding tuning
After=network-online.target
Wants=network-online.target
Before=tailscaled.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/shardvpn-gro

[Install]
WantedBy=multi-user.target
UNIT

echo "shardvpn: installing tailscale"
dnf config-manager --add-repo \
  https://pkgs.tailscale.com/stable/amazon-linux/2023/tailscale.repo
dnf install -y tailscale

systemctl daemon-reload
systemctl enable --now shardvpn-gro.service

echo "shardvpn: installing logout-on-shutdown unit"
# Removes this ephemeral node from the tailnet immediately on termination.
# Ordered after tailscaled AND the network so that it is stopped before both;
# a unit with no network ordering can be stopped after networking is gone, at
# which point `tailscale logout` cannot reach the coordination server.
# Best effort — Tailscale's own ephemeral expiry is the backstop.
cat > /etc/systemd/system/shardvpn-logout.service <<'UNIT'
[Unit]
Description=shardVPN leave tailnet on shutdown
After=network-online.target tailscaled.service
Wants=network-online.target
Requires=tailscaled.service

[Service]
Type=oneshot
RemainAfterExit=yes
TimeoutStopSec=20
ExecStart=/bin/true
ExecStop=/usr/bin/tailscale logout

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now tailscaled
systemctl enable --now shardvpn-logout.service

echo "shardvpn: joining tailnet"
tailscale up \
  --auth-key="${TS_AUTHKEY}" \
  --advertise-exit-node \
  --ssh \
  --hostname="${TS_HOSTNAME}"

echo "shardvpn: verifying forwarding"
if [ "$(cat /proc/sys/net/ipv4/ip_forward)" != "1" ]; then
  echo "shardvpn: FATAL ipv4 forwarding is not enabled" >&2
  exit 1
fi

# `tailscale up` returns once authenticated, but the netmap may not yet show
# the advertisement. A single-shot check would fail healthy nodes, turning the
# assertion into the silent failure it exists to catch.
echo "shardvpn: verifying exit node advertisement"
advertised=0
for _ in $(seq 1 15); do
  if tailscale status --json | grep -q '"ExitNodeOption": *true'; then
    advertised=1
    break
  fi
  sleep 2
done

if [ "${advertised}" != "1" ]; then
  echo "shardvpn: FATAL node is not advertising as an exit node" >&2
  tailscale status --json >&2
  exit 1
fi

echo "shardvpn: ready"
