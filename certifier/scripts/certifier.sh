#!/bin/bash

set -e

generate_keys() {
  generate_keys.sh
}

move_keys_to_s3() {
  aws s3 sync "${BLINK_KEYS}" "s3://blink-keys"
}

generate_keys
move_keys_to_s3