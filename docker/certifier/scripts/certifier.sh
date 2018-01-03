#!/bin/bash

set -e

generate_certificate_authority() {
  easyrsa --batch --req-cn=blink-certifier build-ca nopass
  cp $EASYRSA_PKI/ca.crt $BLINK_VOLUME/ca.crt
  echo "Certificate Authority successfully created"
}

generate_server_key_and_certificate() {
  easyrsa --batch --req-cn=$SERVERNAME gen-req $SERVERNAME nopass
  easyrsa --batch --req-cn=$SERVERNAME sign-req server $SERVERNAME
  cp $EASYRSA_PKI/private/$SERVERNAME.key $BLINK_VOLUME/$SERVERNAME.key
  cp $EASYRSA_PKI/issued/$SERVERNAME.crt $BLINK_VOLUME/$SERVERNAME.crt
  echo "Server certificate and key pair created successfully."
}

generate_client_key_and_certificate() {
  easyrsa --batch --req-cn=$CLIENTNAME gen-req $CLIENTNAME nopass
  easyrsa --batch --req-cn=$CLIENTNAME sign-req client $CLIENTNAME
  cp $EASYRSA_PKI/private/$CLIENTNAME.key $BLINK_VOLUME/$CLIENTNAME.key
  cp $EASYRSA_PKI/issued/$CLIENTNAME.crt $BLINK_VOLUME/$CLIENTNAME.crt
  echo "Client certificate and key pair created successfully."
}

generate_diffie_hellman_params() {
  openssl dhparam -out $BLINK_VOLUME/dh.pem 2048
  echo "Diffie Hellman parameters generated successfully."
}

generate_hmac_key() {
  openvpn --genkey --secret $BLINK_VOLUME/ta.key
  echo "HMAC key generated successfully."
}

easyrsa init-pki
generate_certificate_authority
generate_server_key_and_certificate
generate_client_key_and_certificate
generate_diffie_hellman_params
generate_hmac_key

echo "Certifier tasks complete"
ls -la $BLINK_VOLUME