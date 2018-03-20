#!/bin/bash

# Based on ovpn_getclient file from https://github.com/kylemanna/docker-openvpn

set -e

add_basic_ovpn_protocols() {
cat >> ${configuration_file} <<EOF
client
nobind
dev ${OVPN_DEVICE}
remote-cert-tls server # Ensure that the host being connected to is a server
remote ${OVPN_CN} ${OVPN_PORT} ${OVPN_PROTO}
ncp-ciphers AES-256-GCM:AES-128-GCM:AES-256-CBC # Allowed ciphers for data channel encryption
auth SHA256 # Algorithm for HMAC-authenticating data and control channel packets
EOF
}

add_certificates_and_keys() {
cat >> ${configuration_file} <<EOF
<key>
$(cat ${BLINK_VOLUME}/${CLIENTNAME}.key)
</key>
<cert>
$(openssl x509 -in ${BLINK_VOLUME}/${CLIENTNAME}.crt)
</cert>
<ca>
$(cat ${BLINK_VOLUME}/ca.crt)
</ca>
<tls-crypt>
$(cat ${BLINK_VOLUME}/ta.key)
</tls-crypt>
EOF
}

copy_client_config_to_s3() {
  aws s3 cp "${configuration_file}" s3://blink-keys/client.ovpn
}

# Enable debug mode if ENV variable DEBUG == 1
if [[ ${DEBUG:-} == "1" ]]; then
  set -x
fi

if ! source "${OVPN_ENV}"; then
  echo "File ${OVPN_ENV} does not exist."
fi

if [[ ! -f "${BLINK_VOLUME}/${CLIENTNAME}.key" ]]; then
  echo "Client key at ${BLINK_VOLUME}/${CLIENTNAME}.key not found"
  exit 1
fi

if [[ ! -f "${BLINK_VOLUME}/${CLIENTNAME}.crt" ]]; then
  echo "Client certificate at ${BLINK_VOLUME}/${CLIENTNAME}.crt not found"
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

configuration_file="${BLINK_VOLUME}/${CLIENTNAME}.ovpn"

if [[ -f "${configuration_file}" ]]; then
  rm "${configuration_file}"
fi

add_basic_ovpn_protocols
add_certificates_and_keys

echo "Successfully generated openvpn client config"

copy_client_config_to_s3

echo "Client config uploaded to s3"