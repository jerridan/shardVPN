#!/bin/bash

set -e

generate_openvpn_server_config() {
  server_domain="$1"
  ovpn_generate_server_config.sh -d server_domain
}

generate_openvpn_client_config() {
  ovpn_generate_client_config.sh
}

generate_openvpn_server_config blink-drive.vpn.example
generate_openvpn_client_config