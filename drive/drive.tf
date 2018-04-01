resource "aws_cloudwatch_log_group" "shard_vpn_drive_log_group" {
  name = "shard-vpn-drive-log-group"
  retention_in_days = 1
}

resource "aws_ecs_cluster" "shard_vpn_drive_cluster" {
  name = "shard-vpn-drive-cluster"
}

resource "aws_ecs_service" "shard_vpn_drive_service" {
  name = "shard-vpn-drive-service"
  cluster = "${aws_ecs_cluster.shard_vpn_drive_cluster.id}"
  task_definition = "${aws_ecs_task_definition.shard_vpn_drive_task.arn}"
  desired_count = 1
}

resource "aws_ecs_task_definition" "shard_vpn_drive_task" {
  family = "shard-vpn-drive-task"
  network_mode = "bridge"
  container_definitions = <<EOF
[
  {
    "name": "shard-vpn-drive-1",
    "image": "jerridan/shard-vpn-drive:test",
    "memory": 256,
    "cpu": 256,
    "privileged": true,
    "portMappings": [
      {
        "containerPort": ${lookup(var.port_numbers, var.traffic_protocol)},
        "hostPort": ${lookup(var.port_numbers, var.traffic_protocol)},
        "protocol": "${var.traffic_protocol}"
      }
    ],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "shard-vpn-drive-log-group",
        "awslogs-region": "ca-central-1",
        "awslogs-stream-prefix": "shard-vpn-drive"
      }
    },
    "environment": [
      {
        "name": "SERVER_DOMAIN",
        "value": "${aws_instance.shard_vpn_drive_host.public_ip}"
      },
      {
        "name": "VPN_TRAFFIC_PROTOCOL",
        "value": "${var.traffic_protocol}"
      },
      {
        "name": "VPN_PORT",
        "value": "${lookup(var.port_numbers, var.traffic_protocol)}"
      }
    ]
  }
]
EOF
}

resource "aws_instance" "shard_vpn_drive_host" {
  ami = "ami-5ac94e3e"
  instance_type = "t2.micro"
  key_name = "shard_vpn_drive_key_pair"
  depends_on = ["aws_security_group.shard_vpn_drive_security_group"]
  security_groups = ["shard_vpn_drive_security_group"]
  iam_instance_profile = "${aws_iam_instance_profile.shard_vpn_vpn_iam_profile.name}"
  user_data = "#!/bin/bash\necho ECS_CLUSTER=${aws_ecs_cluster.shard_vpn_drive_cluster.name} > /etc/ecs/ecs.config"
  tags {
    Name = "ShardVPNDrive"
  }
  connection = {
    type = "ssh"
    user = "ec2-user"
    timeout = "30s"
    private_key = "${file("~/.ssh/terraform_rsa")}"
  }
}

resource "aws_key_pair" "shard_vpn_drive_key_pair" {
  key_name = "shard_vpn_drive_key_pair"
  public_key = "${file("~/.ssh/terraform_rsa.pub")}"
}

resource "aws_security_group" "shard_vpn_drive_security_group" {
  name = "shard_vpn_drive_security_group"
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
    Name = "shard_vpn_drive_security_group"
  }
}

resource "aws_iam_instance_profile" "shard_vpn_vpn_iam_profile" {
  name = "shard_vpn_vpn_iam_profile"
  role = "${aws_iam_role.shard_vpn_vpn_ecs_instance_role.name}"
}

resource "aws_iam_role" "shard_vpn_vpn_ecs_instance_role" {
  name = "shard_vpn_vpn_ecs_instance_role"
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

resource "aws_iam_role_policy" "shard_vpn_vpn_ecs_instance_role_policy" {
  name = "shard_vpn_vpn_ecs_instance_role_policy"
  role = "${aws_iam_role.shard_vpn_vpn_ecs_instance_role.id}"
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
