#!/bin/bash

set -e

BLINK_CERTIFIER_VERSION="1.0.0"

sudo yum update -y
sudo yum install -y docker
sudo service docker start
sudo mkdir -p ~/blink/keys
sudo docker run -v ~/blink/keys:/etc/blinkvpn jerridan/blink-certifier:${BLINK_CERTIFIER_VERSION}