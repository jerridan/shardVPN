# Secret parameters are created with placeholder values and never updated by
# Terraform. aws_ssm_parameter stores its value in state IN PLAINTEXT even for
# SecureString, so real values are written out of band:
#
#   aws ssm put-parameter --name /shardvpn/signing-secret --type SecureString \
#     --value "$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" \
#     --overwrite

locals {
  secret_parameters = [
    "/shardvpn/signing-secret",
    "/shardvpn/tailscale-client-id",
    "/shardvpn/tailscale-client-secret",
  ]
}

resource "aws_ssm_parameter" "secret" {
  for_each = toset(local.secret_parameters)

  name  = each.value
  type  = "SecureString"
  tier  = "Standard"
  value = "PLACEHOLDER-set-with-aws-ssm-put-parameter"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "default_region" {
  name  = "/shardvpn/default-region"
  type  = "String"
  value = var.default_node_region
}

resource "aws_ssm_parameter" "default_ttl" {
  name  = "/shardvpn/default-ttl"
  type  = "String"
  value = var.default_ttl
}

resource "aws_ssm_parameter" "instance_type" {
  name  = "/shardvpn/instance-type"
  type  = "String"
  value = var.instance_type
}

resource "aws_ssm_parameter" "idle_threshold_bytes" {
  name  = "/shardvpn/idle-threshold-bytes"
  type  = "String"
  value = tostring(var.idle_threshold_bytes)
}

resource "aws_ssm_parameter" "current_node" {
  name  = "/shardvpn/current-node"
  type  = "String"
  value = "none"

  lifecycle {
    ignore_changes = [value]
  }
}
