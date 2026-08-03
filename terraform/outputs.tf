output "function_url" {
  description = "Endpoint for the phone client. Unguessable but not secret; keep it out of commits."
  value       = aws_lambda_function_url.shardvpn.function_url
  sensitive   = true
}

output "sns_topic_arn" {
  # Not secret, but learning it is exactly what the aws:SourceAccount
  # condition in sns.tf's topic policy defends against (a cross-account
  # CloudWatch alarm publishing into this system's one notification
  # channel) — no reason to make that ARN any easier to find than it has
  # to be.
  value     = aws_sns_topic.notifications.arn
  sensitive = true
}
