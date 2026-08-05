resource "aws_sns_topic" "notifications" {
  name = "shardvpn-notifications"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.notifications.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

# CloudWatch alarms publish to this topic; AWS Budgets does not — alarms.tf's
# aws_budgets_budget notifies via subscriber_email_addresses directly and
# never references this topic, so budgets.amazonaws.com has no reason to be
# a principal here.
#
# The topic ARN is a non-sensitive Terraform output (see outputs.tf), so
# without the aws:SourceAccount condition below, anyone who learns it could
# point a CloudWatch alarm in their own account at it and inject text into
# the one channel this system uses to tell the owner a node is still
# billing. iam.tf's scheduler role guards against the identical
# confused-deputy class the same way.
data "aws_iam_policy_document" "notifications" {
  statement {
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.notifications.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

resource "aws_sns_topic_policy" "notifications" {
  arn    = aws_sns_topic.notifications.arn
  policy = data.aws_iam_policy_document.notifications.json
}
