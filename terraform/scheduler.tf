resource "aws_scheduler_schedule" "watchdog" {
  name                = "shardvpn-watchdog"
  schedule_expression = "rate(1 hour)"

  flexible_time_window {
    mode                      = "FLEXIBLE"
    maximum_window_in_minutes = 15
  }

  target {
    arn      = aws_lambda_function.shardvpn.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ action = "sweep" })
  }
}
