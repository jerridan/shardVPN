#!/bin/bash

set -e

generate_server_key() {
  easyrsa --batch --req-cn=blink-drive gen-req blink-drive nopass
  cp $EASYRSA_PKI/private/$SERVERNAME.key $OPENVPN/server/$SERVERNAME.key
  echo "Server certificate and key pair created successfully."
}

generate_client_key() {
  easyrsa --batch --req-cn=blink-client gen-req blink-client nopass
  cp $EASYRSA_PKI/private/$CLIENTNAME.key $OPENVPN/client/$CLIENTNAME.key
  echo "Client certificate and key pair created successfully."
}

generate_diffie_hellman_params() {
  openssl dhparam -out $OPENVPN/server/dh.pem 2048
  echo "Diffie Hellman parameters generated successfully."
}

generate_hmac_key() {
  openvpn --genkey --secret $OPENVPN/server/ta.key
  echo "HMAC key generated successfully."
}

generate_openvpn_server_config() {
  server_domain="$1"
  ovpn_generate_server_config.sh -d server_domain
}

generate_openvpn_client_config() {
  ovpn_generate_client_config.sh
}

easyrsa init-pki
generate_server_key
generate_client_key
generate_diffie_hellman_params
generate_hmac_key
generate_openvpn_server_config blink-drive.vpn.example
generate_openvpn_client_config