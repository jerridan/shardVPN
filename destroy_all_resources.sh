#!/bin/bash

set -e

PROJECT_DIR=$( cd $( dirname ${BASH_SOURCE[0]} ) && pwd )

terraform destroy -force -var-file=${PROJECT_DIR}/settings.tfvars

cd ${PROJECT_DIR}/certifier
terraform destroy -force -var-file=${PROJECT_DIR}/settings.tfvars

cd ${PROJECT_DIR}/drive
terraform destroy -force -var-file=${PROJECT_DIR}/settings.tfvars

echo "ShardVPN resources destroyed successfully"
