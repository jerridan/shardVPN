#!/bin/bash

# Runs OpenVPN

set -e

if [ "$DEBUG" == "1" ]; then
  set -x
fi

function setupIptablesAndRouting {
  iptables -t nat -A POSTROUTING -s ${OVPN_SERVER} -o ${OVPN_NATDEVICE} -j MASQUERADE
}

source "${OVPN_ENV}"

mkdir -p "/dev/net"
if [ ! -c /dev/net/tun ]; then
  mknod /dev/net/tun c 10 200
fi

# When using --net=host, use this to specify nat device.
[ -z "${OVPN_NATDEVICE}" ] && OVPN_NATDEVICE=eth0

# Setup NAT forwarding if requested
if [ "$OVPN_DEFROUTE" != "0" ] || [ "$OVPN_NAT" == "1" ] ; then
	# call function to setup iptables rules and routing
	# this allows rules to be customized by supplying
	# a replacement function in, for example, ovpn_env.sh
	setupIptablesAndRouting
fi

echo "Running OpenVPN"
exec openvpn --config "${OPENVPN}/openvpn.conf"