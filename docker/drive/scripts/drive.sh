#!/bin/bash

set -e

generate_server_key() {
  easyrsa --batch --req-cn=blink-drive gen-req blink-drive nopass
  cp $EASYRSA_PKI/private/blink-drive.key $OPENVPN/server/blink-drive.key
  echo "Server certificate and key pair created successfully."
}

easyrsa init-pki
generate_server_key
cat $OPENVPN/server/blink-drive.key
cat $EASYRSA_PKI/reqs/blink-drive.req
