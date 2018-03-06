#!/bin/bash

set -e

PROJECT_DIR=$( cd $( dirname ${BASH_SOURCE[0]} ) && pwd )

bash ${PROJECT_DIR}/generate_keys.sh
bash ${PROJECT_DIR}/activate_drive.sh
bash ${PROJECT_DIR}/destroy_certifier_resources.sh

echo "BlinkVPN created successfully"
