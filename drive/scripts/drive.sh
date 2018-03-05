#!/bin/bash

set -e

get_keys_from_s3() {
  aws s3 sync "s3://blink-keys" "${BLINK_VOLUME}"
}

generate_openvpn_server_config() {
  server_domain="$1"
  ovpn_generate_server_config.sh
}

generate_openvpn_client_config() {
  ovpn_generate_client_config.sh
}

start_openvpn() {
  run_openvpn.sh
}

get_keys_from_s3
generate_openvpn_server_config blink-drive.vpn.example
generate_openvpn_client_config
start_openvpn