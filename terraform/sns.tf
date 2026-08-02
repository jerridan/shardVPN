resource "aws_sns_topic" "notifications" {
  name = "shardvpn-notifications"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.notifications.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

# CloudWatch alarms and AWS Budgets both publish to this topic, so it must
# accept them as principals.
data "aws_iam_policy_document" "notifications" {
  statement {
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.notifications.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com", "budgets.amazonaws.com"]
    }
  }
}

resource "aws_sns_topic_policy" "notifications" {
  arn    = aws_sns_topic.notifications.arn
  policy = data.aws_iam_policy_document.notifications.json
}
