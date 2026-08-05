# shardVPN

A personal, on-demand Tailscale exit node on EC2, triggered from your phone.
Nothing runs, and nothing costs anything, between trips.

ShardVPN is named for the Shards in Brandon Sanderson's series
_The Stormlight Archive_.

> Ten heartbeats.
> <br/>
> _One_.
> <br/>
> That was how long it took to summon a Shardblade. If Dalinar's heart was
> racing, the time was shorter. If he was relaxed, it took longer. _Two_.

\- _The Way of Kings_ by Brandon Sanderson, p. 202.

## What this is

Three actions — `up`, `down`, `status` — sent from a phone, over HTTPS, to a
Lambda that is the entire control plane. `up` launches a bare Amazon Linux
2023 instance that turns itself into a Tailscale exit node with no inbound
ports before it ever advertises itself as selectable; `down` tears it down
and removes it from the tailnet; `status` reports what's currently running.
An hourly sweep is the only thing that runs unattended — it reaps anything
past its TTL, kills duplicate nodes, and emails you if a node has gone
untouched for 24 hours, in case you forgot about it.

This is v2. v1 (Terraform 0.11, OpenVPN, a hand-run certificate authority,
manual SSH provisioning) is gone from this repository and lives only in git
history on `master`. If you ran v1, its AWS resources almost certainly still
exist — see step 1 below before doing anything else.

## What you need

- An **AWS account**, with credentials configured locally. Long-lived access
  keys are not required and not recommended — `aws login` (AWS CLI ≥ 2.32)
  signs you in through the browser and issues temporary credentials with
  nothing durable on disk. Whichever identity you use needs broad permissions
  for the initial apply: it creates a Lambda, IAM roles, SSM parameters, an
  SNS topic, an EventBridge schedule, CloudWatch alarms and a budget.
- A **Tailscale account** (the free plan is enough) with your devices already
  on the tailnet.
- **Something to trigger it from.** The control plane is a plain HTTPS
  endpoint: POST a JSON body with an `X-ShardVPN-Timestamp` header and an
  `X-ShardVPN-Signature` header holding `HMAC-SHA256(secret, "<timestamp>.<body>")`
  in lowercase hex. Anything that can do that is a valid client — Android via
  Termux or HTTP Shortcuts, a shell function on a laptop, a watch complication,
  a shell script over SSH.

  What ships here is an iOS client, because that is what the author carries:
  [Scriptable](https://scriptable.app) running
  [`docs/ios/shardvpn.js`](docs/ios/shardvpn.js). If you are writing your own,
  that file is also the reference implementation — it vendors HMAC-SHA256 in
  ~90 lines of dependency-free JavaScript, and `docs/ios-shortcut.md` documents
  the wire format it produces.
- Locally: `terraform` ≥ 1.13, [`uv`](https://docs.astral.sh/uv/), and the
  AWS CLI v2. `shellcheck` and `trivy` only if you want to run the full CI
  checks by hand.

**Pick your region before you apply.** `terraform/variables.tf` defaults to
`ca-central-1` (Montréal) because that is near the author. Set
`default_node_region` in `terraform.tfvars` to somewhere near *you* — it is
where `up` launches when a request doesn't name a region, and it can be
changed later without redeploying (see Daily use).

## One-time setup

Do these in order. Step 1 only applies if you ran this repo's v1. Note that
step 3 needs credentials produced in step 5 — either do 5 first, or come back.

### 1. Decommission v1 — only if you ran it

> Not applicable to a fresh deployment. This repo had a previous life; if you
> are new here, skip to step 2.

**If you ever ran this repo's v1, do this before anything else.** Until it is
done, the security posture described further down is not the one you have.
v1's setup instructions had you create an IAM user carrying `IAMFullAccess`,
`AmazonEC2FullAccess` and `AmazonS3FullAccess`, plus a long-lived access key
in `~/.aws/credentials`. The shell scripts that knew how to tear v1 down were
deleted with the rest of v1's code, so this happens with plain AWS CLI or
console commands, not this repo's tooling.

**Do not assume the permissions are attached to the user.** In the original
account they were not — they came from a shared IAM *group*, and three
unrelated users were in it, each with an active `IAMFullAccess` key. Deleting
the VPN user alone would have left three identical escalation paths open
while looking finished. Enumerate before deleting, and check group
membership both ways.

```bash
# Find what v1 left behind
aws iam list-users --query 'Users[?contains(UserName, `shard`)].UserName'
aws s3 ls | grep shard-vpn
aws ec2 describe-instances --region ca-central-1 \
  --filters "Name=tag:Name,Values=ShardVPNDrive,ShardVPNCertifier" \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output table

# Remove the IAM user and its access key
aws iam list-access-keys --user-name <v1-user>
aws iam delete-access-key --user-name <v1-user> --access-key-id <key-id>
aws iam list-attached-user-policies --user-name <v1-user>
# detach each policy and remove from any group, then:
aws iam delete-user --user-name <v1-user>

# Remove the bucket, and any lingering instances found above
aws s3 rb s3://shard-vpn-keys --force
```

Then delete the corresponding profile block from `~/.aws/credentials`.
Confirm clean: no `shard`-named IAM user, no `shard-vpn-keys` bucket, no
running v1 instances. Nothing about v2 is true — "no long-lived AWS access
keys anywhere" specifically — until this is done.

### 2. `terraform apply`

All tooling runs through **[uv](https://docs.astral.sh/uv/)**. Terraform
itself now installs from HashiCorp's own tap, not homebrew-core:

```bash
brew install hashicorp/tap/terraform
```

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars:
#   notification_email  — where idle/orphan/alarm mail goes (required)
#   default_node_region — where `up` launches by default (defaults to
#                         ca-central-1; change it to somewhere near you)
terraform init
terraform apply
```

This creates the Lambda, its Function URL, the EventBridge Scheduler rule,
the SNS topic, the SSM parameters (with placeholder values for the three
secrets — see step 3), and the CloudWatch alarms and budget.

**A brand-new AWS account may fail this apply.** The Lambda is deployed
with `reserved_concurrent_executions = 1`, and AWS enforces that reserving
concurrency for one function cannot push your account's *unreserved*
concurrent-execution pool below 100. A freshly created account can start
below that floor. If `apply` fails on this, request a service-quota increase
for Lambda concurrent executions in the target region before retrying.

`terraform output function_url` gives you the value the phone client needs
in step 6. Keep it out of commits, issues, and screenshots — see Security
posture below.

### 3. Populate the SecureString parameters out of band

Terraform deliberately does *not* set real values for the three secret SSM
parameters — `aws_ssm_parameter` stores its value in Terraform state **in
plaintext even for `SecureString`**, so committing that value to state would
defeat the point of the type. Set them directly instead:

```bash
aws ssm put-parameter --name /shardvpn/tailscale-client-id \
  --type SecureString --value '<id>' --overwrite
aws ssm put-parameter --name /shardvpn/tailscale-client-secret \
  --type SecureString --value '<secret>' --overwrite
aws ssm put-parameter --name /shardvpn/signing-secret --type SecureString \
  --value "$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" --overwrite
```

The Tailscale client ID and secret come from step 5 below; do that first if
you're following this list top to bottom, or come back here after.

**Verify `kms:Decrypt` works before relying on it.** The Lambda's IAM role
(`iam.tf`) does not grant `kms:Decrypt` on the AWS-managed `alias/aws/ssm`
key explicitly — the design assumes that key's own policy already lets
account principals decrypt via a `kms:ViaService` condition. That held in the
account this was first deployed to, but it depends on your key policy, and it
sits on the authentication hot path: if it's wrong, every `up`/`down`/`status`
request returns a 503 with nothing else to explain why. Thirty seconds to
check.
The `shardvpn-lambda` role can't be assumed directly to check by hand — its
trust policy only allows `lambda.amazonaws.com` — so verify it the way a
real request actually exercises it instead: `handler.py` fetches and
decrypts the signing secret *before* checking the signature, so hitting the
Function URL with a deliberately wrong signature isolates the KMS step from
everything else:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$(terraform -chdir=terraform output -raw function_url)" \
  -H 'x-shardvpn-timestamp: 1700000000' \
  -H "x-shardvpn-signature: $(printf '0%.0s' {1..64})" \
  -d '{"action":"status"}'
```

`403` means the secret was retrieved and decrypted fine — the request was
simply, correctly, rejected for a bad signature. A `503` needs one more
look at the response body before you touch `iam.tf`, because `handler.py`
returns it for two different reasons:

- `{"error": "cannot verify request"}` — the `GetParameter`/KMS call itself
  failed. This is the case that needs an explicit `kms:Decrypt` statement
  scoped to `alias/aws/ssm` added to `iam.tf`.
- `{"error": "signing secret not configured"}` — decryption worked fine, but
  the value is still the literal placeholder `ssm.tf` seeds the parameter
  with. `kms:Decrypt` is not the problem here; you skipped (or mistargeted —
  wrong region or account) the `put-parameter --overwrite` earlier in this
  step. Re-run it and retry the `curl`.

### 4. Confirm the SNS email subscription

`terraform apply` creates an email subscription to the notifications topic
but AWS requires a one-time confirmation click. Check the inbox for the
address you set as `notification_email` and confirm it — until you do, idle
alerts, orphan-node alerts, and the CloudWatch/budget alarms all go nowhere.

### 5. Tailnet setup

One-time, done by hand in the Tailscale admin console: tagging, an OAuth
client, auto-approval for exit-node advertisement, and an SSH grant. See
[`docs/tailnet-setup.md`](docs/tailnet-setup.md).

### 6. Client setup

**On iOS**, install Scriptable and paste in the client script — copy it from
the raw URL on the phone rather than syncing via iCloud Drive, which silently
stops delivering updates. Optionally wrap each action in a Shortcut for a
home-screen icon. See [`docs/ios-shortcut.md`](docs/ios-shortcut.md).

**Anywhere else**, sign the request yourself. This is the whole client, and
it works from any shell with `curl` and `openssl`:

```bash
#!/bin/bash
set -euo pipefail
BODY="$1"                                   # e.g. '{"action":"up"}'
URL=$(terraform -chdir=terraform output -raw function_url)
SECRET=$(aws ssm get-parameter --name /shardvpn/signing-secret \
           --with-decryption --query 'Parameter.Value' --output text)
TS=$(date +%s)
SIG=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex | awk '{print $NF}')
curl -s -X POST "$URL" -H 'content-type: application/json' \
  -H "x-shardvpn-timestamp: $TS" -H "x-shardvpn-signature: $SIG" -d "$BODY"
```

Note this version reads the secret from SSM each call, so it needs AWS
credentials — fine on a laptop, wrong for a phone, which is why the shipped
client keeps the secret in the device keychain instead and never talks to AWS.

## Daily use

Run the script from Scriptable and pick an action, or tap a home-screen
shortcut if you made them:

- **Up** — launches a node in the default region, usually selectable as an
  exit node within a couple of minutes. **Up elsewhere…** prompts for a
  region code instead, for when the default isn't near you.
- **Status** — current state, region, public IP, whether it's on the
  tailnet yet, how long since it last carried real traffic.
- **Down** — terminates the node and removes it from the tailnet.

To move the default permanently, no redeploy needed — the Lambda reads it
per request:

```bash
aws ssm put-parameter --name /shardvpn/default-region \
  --value eu-west-1 --overwrite
```

Calling `up` while a node is already running just returns that node — it's
idempotent, not an error. `down` with nothing running is a no-op. Forgetting
to call `down` costs about $0.52/day and gets you an email after 24 hours of
inactivity; nothing terminates it for you unless you set a TTL.

## Cost

Nothing runs between trips.

| Item | Cost |
|---|---|
| `t4g.small` (while a node is up) | $0.0168/hr |
| Public IPv4 (while a node is up) | $0.005/hr |
| **Running total while a node is up** | **$0.0218/hr ≈ $0.52/day ≈ $3.67/week** |
| Lambda, EventBridge Scheduler, SNS, SSM Standard, CloudWatch basic monitoring | $0 at this volume |

Data egress is the one variable cost. AWS's free tier for data transfer out
to the internet is currently **100 GB/month**, aggregated across all AWS
services and regions — normal VPN usage over a weekend trip is well
under that; sustained heavy transfer is not.

## Security posture

This repository is public. The design assumes that and is built around it:

- **No long-lived AWS access keys anywhere**, once step 1 above is done.
  The Lambda's IAM role is scoped tightly: `TerminateInstances` and
  `RunInstances`'s tagging condition are both bound to
  `shardvpn:role = exit-node`, `RunInstances` is bound to `t4g.*` instance
  types, and the only `Resource: "*"` grants are read-only EC2 `Describe*`
  and CloudWatch `GetMetricStatistics` calls, which don't support
  resource-level permissions at all.
- **No secret ever reaches Terraform state or a commit.** The three secret
  SSM parameters are created with placeholders and `ignore_changes`, and
  populated out of band (step 3). `.gitignore` excludes `*.tfstate*`,
  `.terraform/`, and `*.tfvars` (the example file is committed;
  the real one, carrying your email address, is not).
- **Authentication is an HMAC signature, not an AWS credential.** The
  Function URL runs with `authorization_type = "NONE"`; every request is
  verified inside the handler against a shared signing secret, over the raw
  request bytes, with a 120-second timestamp window. Every failure — bad
  signature, missing header, expired timestamp — returns an identical `403`
  with no detail, so there's no oracle telling an attacker which check
  failed.
- **The exit node has zero inbound security group rules.** The only way in
  is `tailscale ssh`, which requires both an `ssh` grant in the tailnet
  policy and being on the tailnet in the first place.
- **The Tailscale OAuth client is read-mostly.** It can mint auth keys
  tagged `tag:shardvpn-exit` and read device state; it cannot remove or
  retag any device. A leaked OAuth secret can create noise, not damage — see
  `docs/tailnet-setup.md` for why read-write `devices:core` is deliberately
  not granted.
- **Nothing sensitive is ever logged.** The handler logs exception *types*,
  never exception messages or tracebacks — botocore's `ParamValidationError`
  can embed the instance `UserData`, which carries a live (if short-lived
  and single-use) Tailscale auth key.
- **The Function URL itself is not secret**, just unguessable and not
  independently rotatable. Treat it like a password anyway: it stays out of
  commits, issues, and screenshots.

### Branch protection

GitHub setting, not something committed. Under Settings → Branches, add a
protection rule for `master` requiring the four CI jobs — `python`, `shell`,
`terraform`, `scan` — to pass before merging, and require branches to be up
to date before merging. CI itself needs no AWS credentials (every check runs
`-backend=false`/offline); if that ever changes, use GitHub OIDC into a
scoped role, not stored keys.

## Running the tests

All Python tooling runs through **uv** — never `pip`, `python -m venv`, or a
hand-activated `.venv`.

```bash
uv sync --group dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -v
```

191 tests, fully offline: `botocore.stub.Stubber` for every AWS call,
`urlopen` patched for Tailscale, a client factory for the watchdog sweep.
There is no integration test suite — the real verification is an end-to-end
run against live AWS and a live tailnet (`curl ifconfig.me` through the node
from two devices at once), which is inherently manual.

Also checked in CI, if you have the tools locally:

```bash
cd terraform && terraform init -backend=false && terraform validate
shellcheck lambda/shardvpn/userdata.sh
trivy config terraform/
```
