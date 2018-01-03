#!/bin/bash

set -e

easyrsa init-pki
easyrsa --batch --req-cn=blink-certifier build-ca nopass

cp $EASYRSA_PKI/ca.crt $BLINK/ca.crt

echo "Certificate Authority successfully created"
ls $BLINK/ca.crt
