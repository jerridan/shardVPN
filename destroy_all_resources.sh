#!/bin/bash

set -e

PROJECT_DIR=$( cd $( dirname ${BASH_SOURCE[0]} ) && pwd )

terraform destroy -force

cd ${PROJECT_DIR}/certifier
terraform destroy -force

cd ${PROJECT_DIR}/drive
terraform destroy -force

echo "ShardVPN resources destroyed successfully"
