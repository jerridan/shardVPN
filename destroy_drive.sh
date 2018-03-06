#!/bin/bash

set -e

PROJECT_DIR=$( cd $( dirname ${BASH_SOURCE[0]} ) && pwd )

cd ${PROJECT_DIR}/drive
terraform destroy -force

echo "Blink drive destroyed successfully"
