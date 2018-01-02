#!/bin/bash

# Based on ovpn_getclient file from https://github.com/kylemanna/docker-openvpn

set -e

# Enable debug mode if ENV variable DEBUG == 1
if [ "${DEBUG:-}" == "1" ]; then
  set -x
fi

if ! source "${OVPN_ENV}"; then
  echo "File ${OVPN_ENV} does not exist."
fi

if [ ! -f "${OPENVPN}/client/${CLIENTNAME}.key" ]; then
  echo "Client key at ${OPENVPN}/client/${CLIENTNAME}.key not found"
  exit 1
fi

# Will come from the certifier
if [ ! -f "${OPENVPN}/test_data/${CLIENTNAME}.crt" ]; then
  echo "Client certificate at ${OPENVPN}/test_data/${CLIENTNAME}.crt not found"
  exit 1
fi

# Will come from the certifier
if [ ! -f "${OPENVPN}/test_data/ca.crt" ]; then
  echo "Certificate authority at ${OPENVPN}/test_data/ca.crt not found"
  exit 1
fi

if [ ! -f "${OPENVPN}/server/ta.key" ]; then
  echo "HMAC key at ${OPENVPN}/server/ta.key not found"
  exit 1
fi

configuration_file="${CLIENTNAME}.ovpn"

add_basic_ovpn_protocols() {
cat >> ${configuration_file} <<EOF
client
nobind
dev ${OVPN_DEVICE}
remote-cert-tls server
remote ${OVPN_CN} ${OVPN_PORT} ${OVPN_PROTO}
key-direction 1
EOF
}

add_certificates_and_keys() {
cat >> ${configuration_file} <<EOF
<key>
$(cat ${OPENVPN}/client/${CLIENTNAME}.key)
</key>
<cert>
$(openssl x509 -in ${OPENVPN}/test_data/${CLIENTNAME}.crt)
</cert>
<ca>
$(cat ${OPENVPN}/test_data/ca.crt)
</ca>
<tls-auth>
$(cat ${OPENVPN}/server/ta.key)
</tls-auth>
EOF
}

add_config_options() {
  if [ "${OVPN_DEFROUTE}" != "0" ];then
    echo "redirect-gateway def1" >> ${configuration_file}
  fi

  if [ -n "${OVPN_MTU}" ]; then
    echo "tun-mtu ${OVPN_MTU}" >> ${configuration_file}
  fi

  if [ -n "${OVPN_TLS_CIPHER}" ]; then
    echo "tls-cipher ${OVPN_TLS_CIPHER}" >> ${configuration_file}
  fi

  if [ -n "${OVPN_CIPHER}" ]; then
    echo "cipher ${OVPN_CIPHER}" >> ${configuration_file}
  fi

  if [ -n "${OVPN_AUTH}" ]; then
    echo "auth ${OVPN_AUTH}" >> ${configuration_file}
  fi

  if [ -n "${OVPN_OTP_AUTH}" ]; then
    echo "auth-user-pass" >> ${configuration_file}
    echo "auth-nocache" >> ${configuration_file}
  fi

  if [ "${OVPN_COMP_LZO}" == "1" ]; then
    echo "comp-lzo" >> ${configuration_file}
  fi

  if [ -n "${OVPN_OTP_AUTH}" ]; then
    echo "reneg-sec 0" >> ${configuration_file}
  fi
}

add_extra_client_config() {
  for config in "${OVPN_EXTRA_CLIENT_CONFIG[@]}"; do
    echo "${config}" >> ${configuration_file}
  done
}

add_basic_ovpn_protocols
add_config_options
add_extra_client_config
add_certificates_and_keys