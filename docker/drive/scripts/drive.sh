#!/bin/bash

set -e

generate_server_key() {
  easyrsa --batch --req-cn=blink-drive gen-req blink-drive nopass
  cp $EASYRSA_PKI/private/blink-drive.key $OPENVPN/server/blink-drive.key
  echo "Server certificate and key pair created successfully."
}

generate_diffie_hellman_params() {
  openssl dhparam -out $OPENVPN/server/dh.pem 2048
  echo "Diffie Hellman parameters generated successfully."
}

generate_hmac_key() {
  openvpn --genkey --secret $OPENVPN/server/ta.key
  echo "HMAC key generated successfully."
}

easyrsa init-pki
generate_server_key
generate_diffie_hellman_params
generate_hmac_key
