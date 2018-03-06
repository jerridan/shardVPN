#!/bin/bash

set -e

PROJECT_DIR=$( cd $( dirname ${BASH_SOURCE[0]} ) && pwd )
cd ${PROJECT_DIR}

# Create S3 bucket for key storage
terraform apply -auto-approve

# Generate the keys
cd ${PROJECT_DIR}/certifier
terraform apply -auto-approve

echo "BlinkVPN keys successfully generated"
