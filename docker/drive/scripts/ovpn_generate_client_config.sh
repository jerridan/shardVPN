#!/bin/bash

# Based on ovpn_getclient file from https://github.com/kylemanna/docker-openvpn

set -e

# Enable debug mode if ENV variable DEBUG == 1
if [ "${DEBUG:-}" == "1" ]; then
  set -x
fi

