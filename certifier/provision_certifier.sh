#!/bin/bash

set -e

BLINK_CERTIFIER_VERSION="test"

sudo yum update -y
sudo yum install -y docker
sudo service docker start
sudo docker run jerridan/blink-certifier:${BLINK_CERTIFIER_VERSION}