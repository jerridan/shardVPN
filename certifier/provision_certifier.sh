#!/bin/bash

set -e

BLINK_CERTIFIER_VERSION="1.1.0"
BLINK_KEY_DIRECTORY="/home/ec2-user/blink/keys"

sudo mkdir -p ${BLINK_KEY_DIRECTORY}
sudo docker run -v ${BLINK_KEY_DIRECTORY}:/etc/blinkvpn jerridan/blink-certifier:${BLINK_CERTIFIER_VERSION}
sudo aws s3 sync ${BLINK_KEY_DIRECTORY} "s3://blink-keys"
