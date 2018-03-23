# BlinkVPN
This tool allows for the temporary creation of a VPN server. You can create it,use it for however long you need, and 
then promptly destroy it.

BlinkVPN has only been tested on macOS, and may or may not work as expected with other operating systems.
It is still under development, and may change drastically over time.

## Dependencies
Before you are able to use BlinkVPN, you will need to have the following required dependencies.
1. A copy of this repository on your local machine
1. An AWS account, with the proper credentials (see [Setting up AWS Credentials](#setting-up-aws-credentials))
1. Terraform installed on your local machine (see [Installing Terraform](#installing-terraform))
1. An RSA key pair stored on your local machine at `~/.ssh/terraform_rsa`
(see [Setting up the Key Pair](#setting-up-the-key-pair))

### Setting up AWS Credentials
BlinkVPN uses Terraform to create, set up, and tear down the necessary AWS resources on which the VPN runs. Terraform
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
  'blink_vpn'.
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


## Using BlinkVPN

### Running BlinkVPN
Once you have all of the dependencies set up, you can run BlinkVPN as follows:
1. Navigate to the main BlinkVPN folder.
1. Run `build_vpn_from_scratch.sh`.
1. Once the script has finished running, go to the S3 service in your AWS account.
1. In the 'blink_keys' bucket, you will see a number of files. Once your VPN server has initialized, a file with the 
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

### Stopping BlinkVPN
1. If you started BlinkVPN using the `build_vpn_from_scratch.sh` script, then run `destroy_drive.sh` from the main 
blinkVPN folder.

#### More about the `destroy_drive.sh` script
This script does the following:
1. Runs `terraform destroy` from the main folder, to remove the 'blink_keys' bucket.
1. Runs `terraform destroy` from the folder `drive`, which tears down the VPN server.

If you wish, you may run these commands manually to achieve the same result.