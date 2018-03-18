provider "aws" {
  region = "ca-central-1"
}

resource "aws_cloudwatch_log_group" "blink_drive_log_group" {
  name = "blink-drive-log-group"
  retention_in_days = 1
}

resource "aws_ecs_cluster" "blink_drive_cluster" {
  name = "blink-drive-cluster"
}

resource "aws_ecs_service" "blink_drive_service" {
  name = "blink-drive-service"
  cluster = "${aws_ecs_cluster.blink_drive_cluster.id}"
  task_definition = "${aws_ecs_task_definition.blink_drive_task.arn}"
  desired_count = 1
}

resource "aws_ecs_task_definition" "blink_drive_task" {
  family = "blink-drive-task"
  network_mode = "bridge"
  container_definitions = <<EOF
[
  {
    "name": "blink-drive-1",
    "image": "jerridan/blink-drive:1.0.0",
    "memory": 512,
    "cpu": 10,
    "privileged": true,
    "portMappings": [
      {
        "containerPort": 443,
        "hostPort": 443,
        "protocol": "tcp"
      },
      {
        "containerPort": 1194,
        "hostPort": 1194,
        "protocol": "udp"
      }
    ],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "blink-drive-log-group",
        "awslogs-region": "ca-central-1",
        "awslogs-stream-prefix": "blink-drive"
      }
    },
    "environment": [
      {
        "name": "SERVER_DOMAIN",
        "value": "${aws_instance.blink_drive_host.public_ip}"
      }
    ]
  }
]
EOF
}

resource "aws_instance" "blink_drive_host" {
  ami = "ami-5ac94e3e"
  instance_type = "t2.micro"
  key_name = "blink_drive_key_pair"
  depends_on = ["aws_security_group.blink_drive_security_group"]
  security_groups = ["blink_drive_security_group"]
  iam_instance_profile = "${aws_iam_instance_profile.blink_vpn_iam_profile.name}"
  user_data = "#!/bin/bash\necho ECS_CLUSTER=${aws_ecs_cluster.blink_drive_cluster.name} > /etc/ecs/ecs.config"
  tags {
    Name = "BlinkDrive"
  }
  connection = {
    type = "ssh"
    user = "ec2-user"
    timeout = "30s"
    private_key = "${file("~/.ssh/terraform_rsa")}"
  }
}

resource "aws_key_pair" "blink_drive_key_pair" {
  key_name = "blink_drive_key_pair"
  public_key = "${file("~/.ssh/terraform_rsa.pub")}"
}

resource "aws_security_group" "blink_drive_security_group" {
  name = "blink_drive_security_group"
  description = "Allow ports 22 (ssh), 443 and 1194 (openvpn) inbound, and all ports outbound"

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

  ingress {
    from_port   = 1194
    to_port     = 1194
    protocol    = "udp"
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
    Name = "blink_drive_security_group"
  }
}

resource "aws_iam_instance_profile" "blink_vpn_iam_profile" {
  name = "blink_vpn_iam_profile"
  role = "${aws_iam_role.blink_vpn_ecs_instance_role.name}"
}

resource "aws_iam_role" "blink_vpn_ecs_instance_role" {
  name = "blink_vpn_ecs_instance_role"
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

resource "aws_iam_role_policy" "blink_vpn_ecs_instance_role_policy" {
  name = "blink_vpn_ecs_instance_role_policy"
  role = "${aws_iam_role.blink_vpn_ecs_instance_role.id}"
  policy = <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ecs:CreateCluster",
        "ecs:DeregisterContainerInstance",
        "ecs:DiscoverPollEndpoint",
        "ecs:Poll",
        "ecs:RegisterContainerInstance",
        "ecs:StartTelemetrySession",
        "ecs:UpdateContainerInstancesState",
        "ecs:Submit*",
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "s3:Get*",
        "s3:List*",
        "s3:PutObject*"
      ],
      "Resource": "*"
    }
  ]
}
EOF
}
