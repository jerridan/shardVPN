#!/bin/bash

# Based on ovpn_genconfig file from https://github.com/kylemanna/docker-openvpn

set -e

TMP_PUSH_CONFIGFILE=$(mktemp -t vpn_push.XXXXXXX)

on_exit() {
  echo "Removing temporary files..."
  rm -f "${TMP_PUSH_CONFIGFILE}"
}
trap on_exit EXIT

# Convert 1.2.3.4/24 -> 255.255.255.0
cidr2mask() {
  local i
  local subnetmask=""
  local cidr=${1#*/}
  local full_octets=$((${cidr}/8))
  local partial_octet=$((${cidr}%8))

  for ((i=0;i<4;i+=1)); do
    if [[ ${i} < ${full_octets} ]]; then
        subnetmask+=255
    elif [[ ${i} == ${full_octets} ]]; then
        subnetmask+=$((256 - 2**(8-${partial_octet})))
    else
        subnetmask+=0
    fi
    [[ $i < 3 ]] && subnetmask+=.
  done
  echo ${subnetmask}
}

getroute() {
  echo ${1%/*} $(cidr2mask $1)
}

generate_full_server_url() {
  OVPN_SERVER_URL="${VPN_TRAFFIC_PROTOCOL}://${OVPN_CN}:${VPN_PORT}"
}
remove_old_ovpn_vars() {
  if [[ -f "${OVPN_ENV}" ]]; then
    rm "${OVPN_ENV}"
  fi
}

remove_old_ovpn_config() {
  if [[ -f "${configuration_file}" ]]; then
    rm ${configuration_file}
  fi
}

save_ovpn_vars() {
  (set | grep '^OVPN_') | while read -r var; do
    echo "declare -x ${var}"  >> "${OVPN_ENV}"
  done
}

save_ovpn_config() {
cat > "${configuration_file}" <<EOF
server $(getroute "${OVPN_SERVER}")
verb 3
key ${SHARD_VPN_VOLUME}/${SERVERNAME}.key
ca ${SHARD_VPN_VOLUME}/ca.crt
cert ${SHARD_VPN_VOLUME}/${SERVERNAME}.crt
dh ${SHARD_VPN_VOLUME}/dh.pem
tls-crypt ${SHARD_VPN_VOLUME}/ta.key
keepalive ${OVPN_KEEPALIVE}
persist-key
persist-tun

proto ${VPN_TRAFFIC_PROTOCOL}
# Rely on Docker to do port mapping, internally always 1194
port ${VPN_PORT}
dev ${OVPN_DEVICE}${OVPN_DEVICEN}
status /tmp/openvpn-status.log

user nobody
group nogroup
remote-cert-tls client # Ensure that only hosts with a client certificate may connect
ncp-ciphers AES-256-GCM:AES-128-GCM:AES-256-CBC # Allowed ciphers for data channel encryption
auth SHA256 # Algorithm for HMAC-authenticating data and control channel packets
EOF
}

process_push_config() {
  local ovpn_push_config=''
  ovpn_push_config="${1}"
  echo "Processing PUSH Config: '${ovpn_push_config}'"
  [[ -n ${ovpn_push_config} ]] && echo "push \"${ovpn_push_config}\"" >> "${TMP_PUSH_CONFIGFILE}"
}

append_push_commands() {
  [[ ${OVPN_DNS} == "1" ]] && for i in "${OVPN_DNS_SERVERS[@]}"; do
    process_push_config "dhcp-option DNS $i"
  done

  [[ ${#OVPN_PUSH[@]} > 0 ]] && for i in "${OVPN_PUSH[@]}"; do
    process_push_config "$i"
  done

  echo -e "\n### Push Configurations Below" >> "${configuration_file}"
  cat "${TMP_PUSH_CONFIGFILE}" >> "${configuration_file}"
}

# Enable debug mode if ENV variable DEBUG == 1
if [[ ${DEBUG:-} == "1" ]]; then
  set -x
fi

if [[ ! -f "${SHARD_VPN_VOLUME}/${SERVERNAME}.key" ]]; then
  echo "Server key at ${SHARD_VPN_VOLUME}/${SERVERNAME}.key not found"
  exit 1
fi

if [[ ! -f "${SHARD_VPN_VOLUME}/${SERVERNAME}.crt" ]]; then
  echo "Server certificate at ${SHARD_VPN_VOLUME}/${SERVERNAME}.crt not found"
  exit 1
fi

if [[ ! -f "${SHARD_VPN_VOLUME}/ca.crt" ]]; then
  echo "Certificate authority at ${SHARD_VPN_VOLUME}/ca.crt not found"
  exit 1
fi

if [[ ! -f "${SHARD_VPN_VOLUME}/ta.key" ]]; then
  echo "HMAC key at ${SHARD_VPN_VOLUME}/ta.key not found"
  exit 1
fi

OVPN_AUTH=''
OVPN_CIPHER=''
OVPN_CLIENT_TO_CLIENT=''
OVPN_CN=${SERVER_DOMAIN}
OVPN_COMP_LZO=0
OVPN_DEFROUTE=1
OVPN_DEVICE="tun"
OVPN_DEVICEN=0
OVPN_DISABLE_PUSH_BLOCK_DNS=0
OVPN_DNS=1
OVPN_DNS_SERVERS=([0]="8.8.8.8" [1]="8.8.4.4")
OVPN_FRAGMENT=''
OVPN_KEEPALIVE="10 60"
OVPN_MTU=''
OVPN_NAT=0
OVPN_PUSH=()
OVPN_SERVER=10.8.0.0/24
OVPN_SERVER_URL=''
OVPN_TLS_CIPHER=''

configuration_file="${OPENVPN}/openvpn.conf"

generate_full_server_url
remove_old_ovpn_vars
remove_old_ovpn_config
save_ovpn_vars
save_ovpn_config
process_push_config "redirect-gateway def1"
#process_push_config "block-outside-dns" # Windows-only config
append_push_commands

echo "Successfully generated openvpn server config for server at domain ${SERVER_DOMAIN}"
cat ${configuration_file}