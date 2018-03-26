#!/bin/bash

set -e

PROJECT_DIR=$( cd $( dirname ${BASH_SOURCE[0]} ) && pwd )

cd ${PROJECT_DIR}/drive
terraform apply -auto-approve -var-file=${PROJECT_DIR}/settings.tfvars

echo "ShardVPN drive activated successfully"
