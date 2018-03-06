provider "aws" {
  region = "ca-central-1"
}

resource "aws_instance" "blink_certifier" {
  ami = "ami-03de5b67"
  instance_type = "t2.micro"
  key_name = "blink_certifier_key_pair"
  depends_on = ["aws_security_group.certifier_security_group"]
  security_groups = ["certifier_security_group"]
  iam_instance_profile = "${aws_iam_instance_profile.blink_certifier_iam_profile.name}"
  connection = {
    type = "ssh"
    user = "ec2-user"
    private_key = "${file("~/.ssh/terraform_rsa")}"
  }

  provisioner "remote-exec" {
    script = "./provision_certifier.sh"
  }
}

resource "aws_key_pair" "blink_certifier_key_pair" {
  key_name = "blink_certifier_key_pair"
  public_key = "${file("~/.ssh/terraform_rsa.pub")}"
}

resource "aws_security_group" "certifier_security_group" {
  name = "certifier_security_group"
  description = "Allow SSH in and HTTP/HTTPS traffic out"

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port = 80
    to_port = 80
    protocol = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port = 443
    to_port = 443
    protocol = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags {
    Name = "certifier_security_group"
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
    },
    {
      "Effect": "Allow",
      "Action": [
        "kms:ListKeyPolicies",
        "kms:GenerateRandom",
        "kms:ListRetirableGrants",
        "kms:GetKeyPolicy",
        "kms:ListResourceTags",
        "kms:ReEncryptFrom",
        "kms:ListGrants",
        "kms:GetParametersForImport",
        "kms:ListKeys",
        "kms:GetKeyRotationStatus",
        "kms:ListAliases",
        "kms:ReEncryptTo",
        "kms:DescribeKey"
      ],
      "Resource": "*"
    }
  ]
}
EOF
}
