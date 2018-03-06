#!/bin/bash

set -e

BLINK_CERTIFIER_VERSION="1.1.0"

sudo yum update -y
sudo yum install -y docker
sudo service docker start
sudo docker run jerridan/blink-certifier:${BLINK_CERTIFIER_VERSION}