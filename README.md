## BlinkVPN
This tool allows for the temporary creation of a VPN server. You can create it,
use it for however long you need, and then promptly destroy it.

### Dependencies
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
* Port: 1194
* Server location: Montreal, Canada
* Server outbound rules: All TCP and UDP connections, ICMP protocols 0 and 8
* Server inbound rules: TCP ports 22 and 1194