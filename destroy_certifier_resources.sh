#!/bin/bash

set -e

PROJECT_DIR=$( cd $( dirname ${BASH_SOURCE[0]} ) && pwd )

cd ${PROJECT_DIR}/certifier
terraform destroy -force

echo "ShardVPN certifier destroyed successfully"
