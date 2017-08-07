#!/bin/bash

terraform apply
vpn_ip="$(terraform output public_ip)"
scp -oStrictHostKeyChecking=no -i ~/.ssh/terraform_rsa ubuntu@${vpn_ip}:client.ovpn ~/Desktop/