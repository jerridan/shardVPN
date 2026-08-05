# The premise of this project is that nothing runs between trips, which means
# nothing is watching. The watchdog is the only thing bounding cost, and an
# async invoke that errors is retried twice and then vanishes silently.

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "shardvpn-lambda-errors"
  alarm_description   = "The control plane is failing. The watchdog may not be reaping."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.shardvpn.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.notifications.arn]
}

resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  alarm_name          = "shardvpn-lambda-throttles"
  alarm_description   = "Requests are being rejected; reserved concurrency may be contended."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  dimensions          = { FunctionName = aws_lambda_function.shardvpn.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.notifications.arn]
}

# The only defence that does not depend on this code being correct.
resource "aws_budgets_budget" "monthly" {
  name         = "shardvpn-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.notification_email]
  }
}
