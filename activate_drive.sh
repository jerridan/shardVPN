#!/bin/bash

set -e

PROJECT_DIR=$( cd $( dirname ${BASH_SOURCE[0]} ) && pwd )

cd ${PROJECT_DIR}/drive
terraform apply -auto-approve

echo "Blink drive activated successfully"
