output "function_url" {
  description = "Endpoint for the phone client. Unguessable but not secret; keep it out of commits."
  value       = aws_lambda_function_url.shardvpn.function_url
  sensitive   = true
}

output "sns_topic_arn" {
  value = aws_sns_topic.notifications.arn
}
