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

# A NONE function URL is NOT public without an invoke permission, and since
# October 2025 both lambda:InvokeFunctionUrl and lambda:InvokeFunction are
# required. Without them every request returns 403 from the Lambda service —
# indistinguishable from a signature mismatch, which is the worst possible
# confusion for this system's only debugging path.
#
# Observed on the first real apply (2026-08-03), and worth knowing before you
# debug it: creating the function URL ALSO causes a third statement,
# `FunctionURLAllowInvokeAction`, to appear automatically. It duplicates
# `url_invoke_function` below. Harmless — identical Allow grants — but it
# means the docs' claim that only the console and SAM create the policy is no
# longer the whole story.
#
# Also observed: `aws_lambda_permission` retries through IAM propagation
# delay, and a retry can collide with its own earlier success, failing the
# apply with `ResourceConflictException: statement id already exists` after
# several minutes while the statement is in fact present. The fix is to
# import, not to change the config:
#
#   terraform import aws_lambda_permission.url_invoke shardvpn/FunctionURLAllowPublicAccess
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
