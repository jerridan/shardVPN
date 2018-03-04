#NOTE: This repository is under construction, and so blinkVPN will NOT currently work.

## BlinkVPN
This tool allows for the temporary creation of a VPN server. You can create it,
use it for however long you need, and then promptly destroy it.

### Dependencies
#### AWS Login and Credentials
BlinkVPN uses AWS to set up an EC2 micro instance as the VPN server. Therefore,
must have:
1. Signed up for an AWS account
1. Created IAM credentials and saved them to your local machine.

To set up IAM credentials, create an IAM user in your AWS console and grant
that user full access to EC2 instances. You will be given a set of credentials
to be entered into a file `~/.aws/credentials` as follows:
```
[default]
aws_access_key_id = YOUR_AWS_ACCESS_KEY_ID
aws_secret_access_key = YOUR_AWS_SECRET_ACCESS_KEY
```

#### OpenVPN Terminal Client
You must use a client-side application to connect to the VPN server using an
OpenVPN (*ovpn) configuration file. Several options exist (ex. Tunnelblick),
but this setup assumes the use of the OpenVPN Terminal Client.

Install the OpenVPN terminal client on your local system: `brew install openvpn`

*Note: You may need to add the installation location to your `PATH`*

#### SSH key 
BlinkVPN uses SSH to communicate with the created VPN server. Specifically,
it expects the presence of a key at `~/.ssh/terraform`.

You must ensure that there is an SSH at this path. For help with this, I
have found [this guide](https://help.github.com/articles/generating-a-new-ssh-key-and-adding-it-to-the-ssh-agent/)
to be useful.

### Creating and connecting to a VPN server
    
1. Create the VPN server using the `create_vpn` script: 
    
   `./create_vpn.sh`
   
   If the setup goes smoothly, an OpenVPN config file will appear on your desktop 

1. Connect to the VPN server: 
    
   `sudo openvpn --config ~/Desktop/client.ovpn`
   
   Use Ctrl+C to disconnect.
   
### Destroying a VPN server
When you are finished with a VPN server, destroy it by running:
`./destroy_vpn.sh`
 
### Specifications
* Protocol: TCP
* Port: 443
* Server location: Montreal, Canada
* Server outbound rules: All TCP and UDP connections, ICMP protocols 0 and 8
* Server inbound rules: TCP ports 22 and 443

### Troubleshooting
If you are receiving the following error message while trying to connect
to the VPN server:

`Can't assign requested address`

You need to flush your routing table. The simple way is to restart your
local machine. Another way is to run the following commands:

1. `sudo ifconfig en1 down` (or `en0` if on an ethernet connection)
1. `sudo route flush`
1. `sudo ifconfig en1 up`