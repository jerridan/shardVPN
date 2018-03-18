#!/bin/bash

# Based on ovpn_genconfig file from https://github.com/kylemanna/docker-openvpn

set -e

TMP_PUSH_CONFIGFILE=$(mktemp -t vpn_push.XXXXXXX)
TMP_ROUTE_CONFIGFILE=$(mktemp -t vpn_route.XXXXXXX)

on_exit() {
  echo "Removing temporary files..."
  rm -f "${TMP_PUSH_CONFIGFILE}"
  rm -f "${TMP_ROUTE_CONFIGFILE}"
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

usage() {
  echo "Usage of ovpn_genconfig:"
  echo " -d Server public domain (ex. vpn.example.com)"
  echo
  echo "optional arguments:"
  echo " -p Server port. Default: 1194"
  echo " -t Server VPN protocol. Default: 'udp'"
}

generate_full_server_url() {
  if [[ -n "${OVPN_CN:-}" ]]; then
    OVPN_SERVER_URL="${OVPN_PROTO}://${OVPN_CN}:${OVPN_PORT}"
  else
    set +x
    echo "Domain name not specified, see '-d'"
    usage
    exit 1
  fi
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
key ${BLINK_VOLUME}/${SERVERNAME}.key
ca ${BLINK_VOLUME}/ca.crt
cert ${BLINK_VOLUME}/${SERVERNAME}.crt
dh ${BLINK_VOLUME}/dh.pem
tls-auth ${BLINK_VOLUME}/ta.key
key-direction 0
keepalive ${OVPN_KEEPALIVE}
persist-key
persist-tun

proto ${OVPN_PROTO}
# Rely on Docker to do port mapping, internally always 1194
port 1194
dev ${OVPN_DEVICE}${OVPN_DEVICEN}
status /tmp/openvpn-status.log

user nobody
group nogroup
EOF
}

process_push_config() {
  local ovpn_push_config=''
  ovpn_push_config="${1}"
  echo "Processing PUSH Config: '${ovpn_push_config}'"
  [[ -n ${ovpn_push_config} ]] && echo "push \"${ovpn_push_config}\"" >> "${TMP_PUSH_CONFIGFILE}"
}

process_route_config() {
  local ovpn_route_config=''
  ovpn_route_config="$1"
  # If user passed "0" skip this, assume no extra routes
  [[ "$ovpn_route_config" == "0" ]] && break;
  echo "Processing Route Config: '${ovpn_route_config}'"
  [[ -n ${ovpn_route_config} ]] && echo "route $(getroute ${ovpn_route_config})" >> "${TMP_ROUTE_CONFIGFILE}"
}

apply_default_routes() {
  OVPN_ROUTES+=("192.168.254.0/24")
}

append_route_commands() {
  if [[ ${#OVPN_ROUTES[@]} > 0 ]]; then
    for i in "${OVPN_ROUTES[@]}"; do
      process_route_config "$i"
    done
    echo -e "\n### Route Configurations Below" >> "${configuration_file}"
    cat "${TMP_ROUTE_CONFIGFILE}" >> "${configuration_file}"
  fi
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

if [[ ! -f "${BLINK_VOLUME}/${SERVERNAME}.key" ]]; then
  echo "Server key at ${BLINK_VOLUME}/${SERVERNAME}.key not found"
  exit 1
fi

if [[ ! -f "${BLINK_VOLUME}/${SERVERNAME}.crt" ]]; then
  echo "Server certificate at ${BLINK_VOLUME}/${SERVERNAME}.crt not found"
  exit 1
fi

if [[ ! -f "${BLINK_VOLUME}/ca.crt" ]]; then
  echo "Certificate authority at ${BLINK_VOLUME}/ca.crt not found"
  exit 1
fi

if [[ ! -f "${BLINK_VOLUME}/ta.key" ]]; then
  echo "HMAC key at ${BLINK_VOLUME}/ta.key not found"
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
OVPN_EXTRA_CLIENT_CONFIG=()
OVPN_EXTRA_SERVER_CONFIG=()
OVPN_FRAGMENT=''
OVPN_KEEPALIVE="10 60"
OVPN_MTU=''
OVPN_NAT=0
OVPN_PORT=1194
OVPN_PROTO="udp"
OVPN_PUSH=()
OVPN_ROUTES=()
OVPN_SERVER=192.168.255.0/24
OVPN_SERVER_URL=''
OVPN_TLS_CIPHER=''

# Parse arguments
while getopts ":d:p:t" opt; do
  case ${opt} in
    p)
      OVPN_PORT="${OPTARG}"
      ;;
    t)
      OVPN_PROTO="${OPTARG}"
      ;;
    \?)
      set +x
      echo "Invalid option: -${OPTARG}" >&2
      usage
      exit 1
      ;;
    :)
      set +x
      echo "Option -${OPTARG} requires an argument." >&2
      usage
      exit 1
      ;;
  esac
done

configuration_file="${OPENVPN}/openvpn.conf"

generate_full_server_url
remove_old_ovpn_vars
remove_old_ovpn_config
apply_default_routes
save_ovpn_vars
save_ovpn_config
append_route_commands
process_push_config "redirect-gateway def1"
#process_push_config "block-outside-dns" # Windows-only config
append_push_commands

echo "Successfully generated openvpn server config for server at domain ${SERVER_DOMAIN}"
cat ${configuration_file}