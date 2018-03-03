#!/bin/bash

set -e

TERRAFORM_VERSION="0.11.3"

curl -o "${DOWNLOADS}/terraform_install.zip" \
  "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip"
unzip -d "${DOWNLOADS}" "${DOWNLOADS}/terraform_install.zip"
mv "${DOWNLOADS}/terraform" "/usr/local/bin/terraform"