# shardVPN

A personal, on-demand Tailscale exit node on EC2, triggered from an iPhone.
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

## One-time setup

Do these in order. Steps 1–4 are infrastructure; steps 5–6 wire up Tailscale
and the phone.

### 1. Decommission v1 — do this first

**If you ever ran this repo's v1, do this before anything else.** Until it is
done, the security posture described further down is not the one you have.
v1's setup instructions had you create an IAM user
carrying `IAMFullAccess`, `AmazonEC2FullAccess` and `AmazonS3FullAccess`,
plus a long-lived access key in `~/.aws/credentials`. That user, its key,
and an S3 bucket named `shard-vpn-keys` still exist in your account until
you remove them — the shell scripts that knew how to tear v1 down were
deleted along with the rest of v1's code, so this has to happen with
plain AWS CLI / console commands, not this repo's tooling.

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
# edit terraform.tfvars: set notification_email
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

### 4. Confirm the SNS email subscription

`terraform apply` creates an email subscription to the notifications topic
but AWS requires a one-time confirmation click. Check the inbox for the
address you set as `notification_email` and confirm it — until you do, idle
alerts, orphan-node alerts, and the CloudWatch/budget alarms all go nowhere.

### 5. Tailnet setup

One-time, done by hand in the Tailscale admin console: tagging, an OAuth
client, auto-approval for exit-node advertisement, and an SSH grant. See
[`docs/tailnet-setup.md`](docs/tailnet-setup.md).

### 6. Phone setup

Install Scriptable, add the client script, wire up three Shortcuts. See
[`docs/ios-shortcut.md`](docs/ios-shortcut.md).

## Daily use

Tap a home-screen shortcut:

- **Up** — launches a node in the default region (or send a custom region /
  TTL by editing the script's action, if you need that). Usually selectable
  as an exit node on your devices within a couple of minutes.
- **Status** — current state, region, public IP, whether it's on the
  tailnet yet, how long it's been idle.
- **Down** — terminates the node and removes it from the tailnet.

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
services and regions (confirmed against AWS's EC2 on-demand pricing page,
not quoted from memory) — normal VPN usage over a weekend trip is well
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

169 tests, fully offline: `botocore.stub.Stubber` for every AWS call,
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
