data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda"
  output_path = "${path.module}/.build/shardvpn.zip"
  # Directory names alone don't match anything here — excludes wants a glob.
  # Without the doublestar wildcard, `pytest` writing
  # lambda/shardvpn/__pycache__/*.pyc silently landed in the zip, churning
  # source_code_hash and triggering a needless Lambda redeploy on the next
  # apply.
  excludes = ["**/__pycache__/**"]
}

resource "aws_cloudwatch_log_group" "shardvpn" {
  name              = "/aws/lambda/shardvpn"
  retention_in_days = 14
}

resource "aws_lambda_function" "shardvpn" {
  function_name = "shardvpn"
  role          = aws_iam_role.lambda.arn
  handler       = "shardvpn.handler.lambda_handler"
  runtime       = "python3.13"
  architectures = ["arm64"]

  # The sweep scans ~34 regions. Parallelised it is quick, but a higher
  # ceiling costs nothing and a timed-out sweep is retried twice then dropped.
  timeout     = 300
  memory_size = 256

  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  # A single-user control plane. Also the hard cap that stops a leaked
  # Function URL from racing two launches past the pointer check.
  reserved_concurrent_executions = 1

  environment {
    variables = {
      CONTROL_REGION = var.control_region
      TOPIC_ARN      = aws_sns_topic.notifications.arn
      TS_TAG         = var.tailscale_tag
    }
  }

  depends_on = [aws_cloudwatch_log_group.shardvpn]
}

resource "aws_lambda_function_url" "shardvpn" {
  function_name = aws_lambda_function.shardvpn.function_name

  # Authentication is an HMAC signature verified inside the handler. IAM auth
  # would require SigV4 signing from a phone; see design spec §5.1.
  authorization_type = "NONE"
}

# A NONE function URL is NOT public without these. Only the console and SAM
# create the resource policy automatically; via Terraform you must add it or
# every request returns 403 from the Lambda service — indistinguishable from a
# signature mismatch. Since October 2025 BOTH permissions are required.
resource "aws_lambda_permission" "url_invoke" {
  statement_id           = "FunctionURLAllowPublicAccess"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.shardvpn.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "url_invoke_function" {
  statement_id  = "FunctionURLInvokeAllowPublicAccess"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.shardvpn.function_name
  principal     = "*"

  # Restricts the public grant to function-URL calls only.
  invoked_via_function_url = true
}
