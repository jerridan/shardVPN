# Scaffolding for the weekly end-to-end test (.github/workflows/e2e.yml).
#
# Everything here is behind `enable_ci_role`, which defaults to false: cloning
# this repo and running `terraform apply` must not silently hand a GitHub
# repository the ability to read secrets out of this account. Turn it on
# deliberately, per docs/e2e-setup.md.
#
# This is the one place the project's "no AWS credentials in CI" rule is
# relaxed, and the relaxation is narrow: no long-lived access key exists at
# any point. GitHub presents a signed OIDC token, STS exchanges it for a
# session that expires in an hour, and the trust policy below pins that
# exchange to one repository, one branch, and one audience.

locals {
  # An account may hold only ONE OIDC provider per URL. If something else
  # (another repo's deploy pipeline) already registered GitHub's, creating a
  # second fails the apply with EntityAlreadyExists — pass its ARN in
  # `github_oidc_provider_arn` and this config will reuse it instead.
  github_oidc_arn = (
    var.github_oidc_provider_arn != ""
    ? var.github_oidc_provider_arn
    : one(aws_iam_openid_connect_provider.github[*].arn)
  )

  create_oidc_provider = var.enable_ci_role && var.github_oidc_provider_arn == ""

  # StringEquals is case-SENSITIVE and GitHub emits the repository in its
  # canonical casing, so this must match github.com exactly — "shardVPN", not
  # "shardvpn". Getting it wrong fails loudly at the first run with an STS
  # AccessDenied rather than granting anything it shouldn't, but it is an
  # annoying half-hour if you don't know to look here.
  ci_subject = "repo:${var.github_repository}:ref:refs/heads/${var.github_default_branch}"
}

resource "aws_iam_openid_connect_provider" "github" {
  count = local.create_oidc_provider ? 1 : 0

  url = "https://token.actions.githubusercontent.com"

  # Restricting the audience to STS is half of what stops a token minted for
  # some other service from being replayed here; the sub condition below is
  # the other half.
  client_id_list = ["sts.amazonaws.com"]

  # No thumbprint_list. Since 2023 IAM validates GitHub's OIDC certificate
  # against its own trusted root CAs and ignores the thumbprint for this
  # provider, so pinning one buys nothing and creates a rotation chore that
  # breaks authentication when it is missed.
}

# Counted, not just the resources below it. With enable_ci_role = false,
# local.github_oidc_arn is null, and an ungated policy document would still
# be evaluated — failing the plan with "Invalid value for identifiers" for a
# feature that is switched off.
data "aws_iam_policy_document" "ci_assume" {
  count = var.enable_ci_role ? 1 : 0

  statement {
    sid     = "GitHubActionsOIDC"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Pinned to one branch, never `repo:owner/name:*`. With a wildcard, any
    # ref in the repository could assume this role — including a branch
    # pushed by anyone who gains write access, and a tag, which is far easier
    # to create unnoticed than a push to the default branch.
    #
    # Repositories created on github.com from 15 July 2026 emit an immutable
    # sub carrying @<ORG_ID>/@<REPO_ID> suffixes, and older repositories can
    # opt in. shardVPN predates that and has not opted in, so the classic
    # form below is what its tokens actually carry. If you ever opt in, this
    # condition stops matching and the weekly run fails closed.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.ci_subject]
    }
  }
}

resource "aws_iam_role" "ci" {
  count = var.enable_ci_role ? 1 : 0

  name               = "shardvpn-ci"
  assume_role_policy = data.aws_iam_policy_document.ci_assume[0].json

  # The weekly job runs for ~15 minutes. An hour is the shortest AWS allows
  # here without extra configuration and is already far tighter than the
  # thing this replaces (a permanent access key in GitHub secrets).
  max_session_duration = 3600
}

data "aws_iam_policy_document" "ci" {
  count = var.enable_ci_role ? 1 : 0

  # The workflow asks the Lambda service where the function URL is rather
  # than keeping a copy in GitHub. Terraform's state is local and gitignored,
  # so the output in outputs.tf is not reachable from a runner.
  statement {
    sid       = "DiscoverTheFunctionUrl"
    actions   = ["lambda:GetFunctionUrlConfig"]
    resources = [aws_lambda_function.shardvpn.arn]
  }

  # The signing secret, and the CI-only Tailscale OAuth client. Enumerated
  # one ARN at a time rather than /shardvpn/* so this role cannot read the
  # PRODUCTION Tailscale client, which can mint keys for tag:shardvpn-exit.
  statement {
    sid     = "ReadTheCredentialsTheTestNeeds"
    actions = ["ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${var.control_region}:${local.account}:parameter/shardvpn/signing-secret",
      "arn:aws:ssm:${var.control_region}:${local.account}:parameter/shardvpn/ci-tailscale-client-id",
      "arn:aws:ssm:${var.control_region}:${local.account}:parameter/shardvpn/ci-tailscale-client-secret",
    ]
  }

  # No kms:Decrypt statement. Resolved 2026-08-03 for the Lambda role and the
  # same reasoning applies: alias/aws/ssm's AWS-managed key policy grants
  # account principals through kms:ViaService.

  # The final leak assertion. DescribeInstances does not support
  # resource-level permissions, and it is read-only.
  statement {
    sid       = "ConfirmNothingWasLeftRunning"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }

  # A failed or skipped weekly run publishes here, so it lands in the same
  # inbox as the idle-node and alarm notifications instead of relying on
  # GitHub's email being noticed.
  statement {
    sid       = "ReportTheResult"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.notifications.arn]
  }
}

resource "aws_iam_role_policy" "ci" {
  count = var.enable_ci_role ? 1 : 0

  name   = "shardvpn-ci"
  role   = aws_iam_role.ci[0].id
  policy = data.aws_iam_policy_document.ci[0].json
}

# A SECOND Tailscale OAuth client, distinct from the one the Lambda uses.
# Restricted to tag:shardvpn-ci, so a leak from a GitHub runner cannot mint
# keys for tag:shardvpn-exit, and either client can be rotated without
# touching the other.
#
# Same handling as ssm.tf's secrets: placeholder value, ignore_changes, and
# the real value written out of band. aws_ssm_parameter puts its value in
# state in plaintext even for SecureString.
resource "aws_ssm_parameter" "ci_tailscale" {
  for_each = var.enable_ci_role ? toset([
    "/shardvpn/ci-tailscale-client-id",
    "/shardvpn/ci-tailscale-client-secret",
  ]) : toset([])

  name  = each.value
  type  = "SecureString"
  tier  = "Standard"
  value = "PLACEHOLDER-set-with-aws-ssm-put-parameter"

  lifecycle {
    ignore_changes = [value]
  }
}

output "ci_role_arn" {
  description = "Set this as the AWS_ROLE_ARN repository variable in GitHub. Not a secret."
  value       = one(aws_iam_role.ci[*].arn)
}
