#!/bin/bash

set -e

PROJECT_DIR=$( cd $( dirname ${BASH_SOURCE[0]} ) && pwd )

terraform init

cd "${PROJECT_DIR}"/certifier
terraform init

cd "${PROJECT_DIR}"/drive
terraform init
