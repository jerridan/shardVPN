provider "aws" {
  region = "${var.region}"
}

resource "aws_instance" "vpn" {
  ami = "${lookup(var.amis, var.region)}"
  instance_type = "t2.micro"
  key_name = "deployer"
  depends_on = ["aws_security_group.vpn_security_group"]
  security_groups = ["vpn_security_group"]
  connection = {
    type = "ssh"
    user = "ubuntu"
    timeout = "30s"
    private_key = "${file("~/.ssh/terraform_rsa")}"
  }

  provisioner "file" {
    source = "setup.sh"
    destination = "/tmp/setup.sh"
  }

  provisioner "file" {
    source = "make_config.sh"
    destination = "/tmp/make_config.sh"
  }

  provisioner "remote-exec" {
    inline = [
      "chmod +x /tmp/setup.sh",
      "/tmp/setup.sh"
    ]
  }
}

resource "aws_key_pair" "deployer" {
  key_name = "deployer"
  public_key = "${file("~/.ssh/terraform_rsa.pub")}"
}

resource "aws_security_group" "vpn_security_group" {
  name = "vpn_security_group"
  description = "Allow ports 22 (ssh) and 1194 (openvpn)"

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

output "public_ip" {
  value = "${aws_instance.vpn.public_ip}"
}