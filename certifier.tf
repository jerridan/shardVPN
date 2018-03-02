provider "aws" {
  region = "ca-central-1"
}

resource "aws_instance" "blink_certifier" {
  ami = "ami-a954d1cd"
  instance_type = "t2.micro"
  key_name = "blink_certifier_key_pair"
  depends_on = ["aws_security_group.vpn_security_group"]
  security_groups = ["vpn_security_group"]
  iam_instance_profile = "${aws_iam_instance_profile.blink_certifier_iam_profile.name}"
  connection = {
    type = "ssh"
    user = "ec2-user"
    private_key = "${file("~/.ssh/terraform_rsa")}"
  }

  provisioner "remote-exec" {
    inline = [
      "sudo yum update -y",
      "sudo yum install -y docker",
      "sudo service docker start",
      "sudo usermod -a -G docker ec2-user",
      "sudo mkdir -p ~/blink/keys",
      "sudo mkdir -p ~/blink/terraform",
      "sudo mkdir -p ~/blink/downloads",
      "sudo docker run -v ~/blink/keys:/etc/blinkvpn jerridan/blink-certifier",
      "sudo chown -R ec2-user:ec2-user ~/blink",
      "curl -o ~/blink/downloads/terraform_install.zip https://releases.hashicorp.com/terraform/0.11.3/terraform_0.11.3_linux_amd64.zip",
      "unzip -d ~/blink/downloads ~/blink/downloads/terraform_install.zip",
      "sudo mv ~/blink/downloads/terraform /usr/bin/terraform",
    ]
  }

  provisioner "file" {
    source = "./certifier/s3.tf",
    destination = "~/blink/terraform/s3.tf"
  }

  provisioner "remote-exec" {
    inline = [
      "cd ~/blink/terraform",
      "terraform init",
      "terraform apply -auto-approve"
    ]
  }
}

resource "aws_s3_bucket" "blink_keys" {
  bucket = "blink-keys"
  acl = "private"
  force_destroy = "true"
}

resource "aws_key_pair" "blink_certifier_key_pair" {
  key_name = "blink_certifier_key_pair"
  public_key = "${file("~/.ssh/terraform_rsa.pub")}"
}

resource "aws_security_group" "vpn_security_group" {
  name = "vpn_security_group"
  description = "Open up all ports for now"

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port = 0
    to_port = 65535
    protocol = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port = 0
    to_port = 65535
    protocol = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port = 0
    to_port = 0
    protocol = "icmp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port = 8
    to_port = 0
    protocol = "icmp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags {
    Name = "vpn_security_group"
  }
}

resource "aws_iam_instance_profile" "blink_certifier_iam_profile" {
  name = "blink_certifier_iam_profile"
  role = "${aws_iam_role.blink_certifier_role.name}"
}

resource "aws_iam_role" "blink_certifier_role" {
  name = "blink_certifier_role"
  assume_role_policy = <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Action": "sts:AssumeRole",
      "Principal": {
        "Service": "ec2.amazonaws.com"
      },
      "Effect": "Allow",
      "Sid": ""
    }
  ]
}
EOF
}

resource "aws_iam_role_policy" "blink_certifier_policy" {
  name = "blink_certifier_policy"
  role = "${aws_iam_role.blink_certifier_role.id}"
  policy = <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "s3:*",
      "Resource": "*"
    }
  ]
}
EOF
}

output "public_ip" {
  value = "${aws_instance.blink_certifier.public_ip}"
}