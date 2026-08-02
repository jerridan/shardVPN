data "aws_caller_identity" "current" {}

locals {
  account = data.aws_caller_identity.current.account_id
}

resource "aws_iam_role" "lambda" {
  name = "shardvpn-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

data "aws_iam_policy_document" "lambda" {
  # Describe* and CloudWatch metrics do not support resource-level
  # permissions. Both are read-only; justified in .trivyignore.
  statement {
    sid = "ReadOnlyDiscovery"
    actions = [
      "ec2:DescribeInstances",
      "ec2:DescribeRegions",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeVpcs",
      "cloudwatch:GetMetricStatistics",
    ]
    resources = ["*"]
  }

  # The instance ARN carries the conditions. Without the request-tag
  # condition, a bug in the tag dictionary could create an instance that the
  # tag-conditioned TerminateInstances below can never reap. Without the
  # instance-type bound, nothing structural stops a p5.48xlarge.
  statement {
    sid       = "LaunchTaggedExitNodes"
    actions   = ["ec2:RunInstances"]
    resources = ["arn:aws:ec2:*:${local.account}:instance/*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/shardvpn:role"
      values   = ["exit-node"]
    }

    condition {
      test     = "StringLike"
      variable = "ec2:InstanceType"
      values   = ["t4g.*"]
    }
  }

  # RunInstances also touches these resource types, which do not accept
  # request-tag conditions.
  statement {
    sid     = "LaunchSupportingResources"
    actions = ["ec2:RunInstances"]
    resources = [
      "arn:aws:ec2:*::image/*",
      "arn:aws:ec2:*:${local.account}:volume/*",
      "arn:aws:ec2:*:${local.account}:network-interface/*",
      "arn:aws:ec2:*:${local.account}:security-group/*",
      "arn:aws:ec2:*:${local.account}:subnet/*",
    ]
  }

  statement {
    sid       = "TagOnlyAtLaunch"
    actions   = ["ec2:CreateTags"]
    resources = ["arn:aws:ec2:*:${local.account}:*/*"]

    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values   = ["RunInstances"]
    }
  }

  statement {
    sid       = "StampOurOwnInstances"
    actions   = ["ec2:CreateTags"]
    resources = ["arn:aws:ec2:*:${local.account}:instance/*"]

    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/shardvpn:role"
      values   = ["exit-node"]
    }
  }

  statement {
    sid       = "TerminateOnlyOurInstances"
    actions   = ["ec2:TerminateInstances"]
    resources = ["arn:aws:ec2:*:${local.account}:instance/*"]

    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/shardvpn:role"
      values   = ["exit-node"]
    }
  }

  statement {
    sid       = "CreateOurSecurityGroup"
    actions   = ["ec2:CreateSecurityGroup"]
    resources = ["*"]
  }

  statement {
    sid     = "ReadConfiguration"
    actions = ["ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:*:${local.account}:parameter/shardvpn/*",
      "arn:aws:ssm:*::parameter/aws/service/ami-amazon-linux-latest/*",
    ]
  }

  statement {
    sid       = "UpdateThePointer"
    actions   = ["ssm:PutParameter"]
    resources = ["arn:aws:ssm:*:${local.account}:parameter/shardvpn/current-node"]
  }

  statement {
    sid       = "Notify"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.notifications.arn]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.control_region}:${local.account}:log-group:/aws/lambda/shardvpn:*"]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "shardvpn-lambda"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

resource "aws_iam_role" "scheduler" {
  name = "shardvpn-scheduler"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      # Confused-deputy guard, per the EventBridge Scheduler docs.
      Condition = { StringEquals = { "aws:SourceAccount" = local.account } }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  name = "shardvpn-scheduler"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.shardvpn.arn
    }]
  })
}
