#!/bin/bash

set -e

generate_keys() {
  generate_keys.sh
}

move_keys_to_s3() {
  cd "${TERRAFORM}"
  terraform init
  terraform apply -auto-approve
}

generate_keys
move_keys_to_s3