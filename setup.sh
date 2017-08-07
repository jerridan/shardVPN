#!/bin/bash

(
set -e

configure_ca_variable ()  {
  key=$1
  value=$2
  sudo sed -i "s/^export $key.*/export $key=\"$value\"/" ~/openvpn-ca/vars
}

comment_lines_with_prefix () {
  line_prefix=$1
  file=$2
  comment_symbol=$3
  sudo sed -i "s/$line_prefix/$comment_symbol$line_prefix/g" $file
}

uncomment_lines_with_prefix () {
  line_prefix=$1
  file=$2
  comment_symbol=$3
  sudo sed -i "s/$comment_symbol$line_prefix/$line_prefix/g" $file
}

insert_line_before () {
  line_to_insert=$1
  match=$2
  file=$3
  sudo sed -i "/$match/i $line_to_insert" $file
}

insert_line_after () {
  line_to_insert=$1
  match=$2
  file=$3
  sudo sed -i "/$match/a $line_to_insert" $file
}

# Set up OpenVPN
sudo apt-get -y -qq update
sudo apt-get -y -qq install openvpn easy-rsa

# Set up the CA directory
make-cadir ~/openvpn-ca

# Configure CA Variables
configure_ca_variable "KEY_COUNTRY" "CA"
configure_ca_variable "KEY_PROVINCE" "ON"
configure_ca_variable "KEY_CITY" "Toronto"
configure_ca_variable "KEY_ORG" "my_organization"
configure_ca_variable "KEY_EMAIL" "user@email.com"
configure_ca_variable "KEY_OU" "my_organizational_unit"
configure_ca_variable "KEY_NAME" "vpn_server"

# Switch to the openvpn-ca directory
cd ~/openvpn-ca

# Build the Certificate Authority
source vars
./clean-all
./build-ca --batch

# Create the server certificate, key pair and encryption files
./build-key-server --batch server
./build-dh
openvpn --genkey --secret keys/ta.key

# Create a client certificate and key pair
cd ~/openvpn-ca
source vars
./build-key --batch client

# Configure the OpenVPN service
cd ~/openvpn-ca/keys
sudo cp ca.crt ca.key server.crt server.key ta.key dh2048.pem /etc/openvpn
gunzip -c /usr/share/doc/openvpn/examples/sample-config-files/server.conf.gz | sudo tee /etc/openvpn/server.conf
uncomment_lines_with_prefix "tls-auth ta.key" "/etc/openvpn/server.conf" ";"
insert_line_after "key-direction 0" "tls-auth ta.key" "/etc/openvpn/server.conf"
uncomment_lines_with_prefix "cipher AES-128-CBC" "/etc/openvpn/server.conf" ";"
insert_line_after "auth SHA256" "cipher AES-128-CBC" "/etc/openvpn/server.conf"
uncomment_lines_with_prefix "user nobody" "/etc/openvpn/server.conf" ";"
uncomment_lines_with_prefix "group nogroup" "/etc/openvpn/server.conf" ";"
uncomment_lines_with_prefix "push \"redirect-gateway" "/etc/openvpn/server.conf" ";"
uncomment_lines_with_prefix "push \"dhcp-option" "/etc/openvpn/server.conf" ";"

# Use the TCP protocol
comment_lines_with_prefix "proto udp" "/etc/openvpn/server.conf" ";"
uncomment_lines_with_prefix "proto tcp" "/etc/openvpn/server.conf" ";"

# Allow IP Forwarding
uncomment_lines_with_prefix "net.ipv4.ip_forward=1" "/etc/sysctl.conf" "#"
sudo sysctl -p

# Adjust the UFW Rules to masquerade client connections
sudo sed -i "/Don't delete these required lines.*/i\
# START OPENVPN RULES\n\
# NAT table rules\n\
*nat\n\
:POSTROUTING ACCEPT [0:0]\n\
# Allow traffic from OpenVPN client to eth0\n\
-A POSTROUTING -s 10.8.0.0/8 -o eth0 -j MASQUERADE\n\
COMMIT\n\
# END OPENVPN RULES\n" /etc/ufw/before.rules

sudo sed -i "s/DEFAULT_FORWARD_POLICY=\"DROP\"/DEFAULT_FORWARD_POLICY=\"ACCEPT\"/" /etc/default/ufw

# Open the OpenVPN port and enable changes
sudo ufw allow 1194/tcp
sudo ufw allow OpenSSH
sudo ufw disable
sudo ufw --force enable

# Start and enable the OpenVPN service
sudo systemctl start openvpn@server
sudo systemctl enable openvpn@server

# Create client config directory structure
sudo mkdir -p ~/client-configs/files
sudo chmod 700 ~/client-configs/files

# Create base configuration
sudo cp /usr/share/doc/openvpn/examples/sample-config-files/client.conf ~/client-configs/base.conf
public_ip="$(curl ipinfo.io/ip)"
sudo sed -i "s/remote my-server-1 1194/remote $public_ip 1194/" ~/client-configs/base.conf
comment_lines_with_prefix "proto udp" ~/client-configs/base.conf  ";"
uncomment_lines_with_prefix "proto tcp" ~/client-configs/base.conf ";"
uncomment_lines_with_prefix "user nobody" ~/client-configs/base.conf ";"
uncomment_lines_with_prefix "group nogroup" ~/client-configs/base.conf ";"
comment_lines_with_prefix "ca ca.crt" ~/client-configs/base.conf ";"
comment_lines_with_prefix "cert client.crt" ~/client-configs/base.conf ";"
comment_lines_with_prefix "key client.key" ~/client-configs/base.conf ";"
sudo chmod 666 ~/client-configs/base.conf
sudo echo "cipher AES-128-CBC" >> ~/client-configs/base.conf
sudo echo "auth SHA256" >> ~/client-configs/base.conf
sudo echo "key-direction 1" >> ~/client-configs/base.conf
sudo echo "# script-security 2" >> ~/client-configs/base.conf
sudo echo "# up /etc/openvpn/update-resolv-conf" >> ~/client-configs/base.conf
sudo echo "# down /etc/openvpn/update-resolv-conf" >> ~/client-configs/base.conf

# Create a configuration generation script
sudo cp /tmp/make_config.sh ~/client-configs/make_config.sh
sudo chmod 755 ~/client-configs/make_config.sh

# Generate client configurations
cd ~/client-configs
sudo ./make_config.sh client

# Move client ovpn file to home directory to allow file transfer access
sudo cp ~/client-configs/files/client.ovpn ~/client.ovpn
)