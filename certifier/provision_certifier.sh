#!/bin/bash

set -e

SHARD_VPN_CERTIFIER_VERSION="1.1.0"
SHARD_VPN_KEY_DIRECTORY="/home/ec2-user/shard/keys"

sudo mkdir -p ${SHARD_VPN_KEY_DIRECTORY}
sudo docker run -v ${SHARD_VPN_KEY_DIRECTORY}:/etc/shardvpn jerridan/shard-vpn-certifier:${SHARD_VPN_CERTIFIER_VERSION}
sudo aws s3 sync ${SHARD_VPN_KEY_DIRECTORY} "s3://shard-vpn-keys"
