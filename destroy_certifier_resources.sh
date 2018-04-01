#!/bin/bash

set -e

PROJECT_DIR=$( cd $( dirname ${BASH_SOURCE[0]} ) && pwd )

cd ${PROJECT_DIR}/certifier
terraform destroy -force -var-file=${PROJECT_DIR}/settings.tfvars

echo "ShardVPN certifier destroyed successfully"
