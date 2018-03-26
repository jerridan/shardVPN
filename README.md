# ShardVPN
This tool allows for the temporary creation of a VPN server. You can create it, use it for however long you need, and 
then promptly destroy it.

ShardVPN has only been tested on macOS, and may or may not work as expected with other operating systems.
It is still under development, and may change drastically over time.

ShardVPN is named for the Shards in Brandon Sanderson's series _The Stormlight Archive_.
> Ten heartbeats.
> <br/>
> _One_.
> <br/>
> That was how long it took to summon a Shardblade. If Dalinar's heart was racing, the time was shorter. If he was 
> relaxed, it took longer. _Two_.

\- _The Way of Kings_ by Brandon Sanderson, p. 202.

## Contents
- [Dependencies](#dependencies)
  - [Setting up AWS Credentials](#setting-up-aws-credentials)
  - [Installing Terraform](#installing-terraform)
  - [Setting up the Key Pair](#setting-up-the-key-pair)
- [Using ShardVPN](#using-shardvpn)
  - [Running ShardVPN](#running-shardvpn)
  - [Stopping ShardVPN](#stopping-shardvpn)
- [Security](#security)
- [Troubleshooting](#troubleshooting)

## Dependencies
Before you are able to use ShardVPN, you will need to have the following required dependencies.
1. A copy of this repository on your local machine
1. An AWS account, with the proper credentials (see [Setting up AWS Credentials](#setting-up-aws-credentials))
1. Terraform installed on your local machine (see [Installing Terraform](#installing-terraform))
1. An RSA key pair stored on your local machine at `~/.ssh/terraform_rsa`
(see [Setting up the Key Pair](#setting-up-the-key-pair))

### Setting up AWS Credentials
ShardVPN uses Terraform to create, set up, and tear down the necessary AWS resources on which the VPN runs. Terraform
 can only operate with the resources on your account that you grant it access to. At this time, the following 
 permissions are required:
 - AmazonS3FullAccess
 - AmazonEC2FullAccess
 - IAMFullAccess
 - CloudWatchLogsFullAccess
 - AmazonEC2ContainerServiceFullAccess
 
In the end, your default AWS credentials on your local system must have the above permissions. I recommend setting up
  these permissions in the following manner:
  1. Go to the IAM service in your AWS account.
  1. Create a user. You may name it whatever you like.
  1. Create a group. You may name this whatever you like, but I recommend giving it an identifiable name, like 
  'shard_vpn'.
  1. Assign the user to the group.
  1. In the group, attach the permission policies listed above.
  1. In the user, go to the 'Security Credentials' page and create an access key.
  1. With the access key ID and the secret key, create a file at `~/.aws/credentials` that looks like the following:
      ```
      [default]
      aws_access_key_id = YOUR_AWS_ACCESS_KEY_ID
      aws_secret_access_key = YOUR_AWS_SECRET_ACCESS_KEY
      ```

### Installing Terraform
On macOS, I find using Homebrew to be the easiest way to install Terraform: 
```
brew install terraform
```
Otherwise, see https://www.terraform.io/ for installation instructions.

### Setting up the Key Pair
At one point, Terraform needs to SSH into an EC2 instance in order to upload and run a script. It does this using an 
RSA key. The key pair must be stored on your local system at `~/.ssh/terraform_rsa`. If you need instructions for 
generating this key, I have always found
[this link](https://help.github.com/articles/generating-a-new-ssh-key-and-adding-it-to-the-ssh-agent/#generating-a-new-ssh-key)
to be helpful.


## Using ShardVPN

### Running ShardVPN
Once you have all of the dependencies set up, you can run ShardVPN as follows:
1. Navigate to the main ShardVPN folder.
1. Run `build_vpn_from_scratch.sh`.
1. Once the script has finished running, go to the S3 service in your AWS account.
1. In the 'shard-vpn-keys' bucket, you will see a number of files. Once your VPN server has initialized, a file with 
the 
name `client.ovpn` will appear. This takes ~3 minutes. 
1. Download the `client.ovpn` file.
1. Use the `client.ovpn` file with your favourite OpenVPN-compatible VPN software to establish a connection with the 
server.
    - If you are using macOS, I recommend [Tunnelblick](https://tunnelblick.net/)
1. Presto! You should now be connected to your own private VPN server!

#### More about the `build_vpn_from_scratch.sh` script
This script does the following:
1. Runs `terraform apply` from the main folder, which create the S3 bucket where the certificates and keys are stored.
1. Runs `terraform apply` from the folder `certifier`, which creates an EC2 instance that generates all of the 
necessary keys and certificates required for a client - server VPN connection.
1. Runs `terraform destroy` from the folder `certifier` to tear down that EC2 server, as it is no longer needed.
1. Runs `terraform apply` from the folder `drive`, which creates the VPN server that your local machine will connect
 to.
 
If you wish, you may run these commands manually to achieve the same result. You can even SSH into the servers yourself 
using the RSA key you 
generated, if you want to have a look around.

### Stopping ShardVPN
1. If you started ShardVPN using the `build_vpn_from_scratch.sh` script, then run `destroy_drive.sh` from the main folder.

#### More about the `destroy_drive.sh` script
This script does the following:
1. Runs `terraform destroy` from the main folder, to remove the 'shard-vpn-keys' bucket.
1. Runs `terraform destroy` from the folder `drive`, which tears down the VPN server.

If you wish, you may run these commands manually to achieve the same result.

## Security
Below are some of the security measures that ShardVPN is built with.

**Separation of the Certificate Authority from the VPN server**

The Certificate Authority (CA) is used to sign the server and client keys and certificates. This identifies them as 
trustworthy (i.e. the server knows the client is trustworthy, and vice versa). If an outside source were to gain 
access to the CA key, new keys could be generated to gain access to the VPN.

In order to mitigate this risk, once the keys and certificates are generated, the certifier server, which contains 
the CA key, is torn down. No further keys or certificates can be generated at this point beyond what has been placed 
in the S3 bucket.

Note that while there is a CA certificate placed in the S3 bucket, this is NOT the CA key. The CA certificate is only 
used to verify that other certificates were signed by the CA key, and cannot itself sign anything.

**SHA256 Authentication of all Data**

All data packets passing between the ShardVPN client and server are signed and authenticated using the SSL SHA-256 
cryptographic hash algorithm. 

**SHA256 Encryption of all Control Channel Packets**

A control channel packet is a packet of data sent between the client and server during the initial connection and 
later shutdown phases.

All control channel packets are encrypted using a SSL SHA-256 hash algorithm in order to mask their contents. This 
helps to prevent Man-in-the-Middle attacks, and also helps to keep these packets from being identified as being part
of a VPN.

**Encryption of all Data Channel Packets**

A data channel packet is any packet of data sent over the VPN after a connection has been initialized.

All data channel packets are encrypted with one of the following ciphers, in order of descending preference:
1. AES-256-GCM
1. AES-128-GCM
1. AES-256-CBC

If your OpenVPN software is using OpenVPN 2.4 or higher, AES-256-GCM will be the default. For older versions, 
AES-256-CBC will be used.

## Troubleshooting

**DNS options are not being set properly**

You must ensure that the client configuration file has permission to manually set network settings on your local 
machine. This is because ShardVPN will set your machine to use Google's DNS servers.

To fix this in Tunnelblick, simply open Tunnelblick, go to "Advanced", and make sure that "Allow changes to 
manually-set network settings" is selected.
