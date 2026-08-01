# shardVPN v2 — design

**Date:** 2026-08-01
**Branch:** `v2-tailscale-exit-node`
**Supersedes:** everything in v1 (`master` at `c381062`)

## 1. Goal

A personal VPN exit node that can be launched from a phone on cellular, used by
several devices at once with no per-device setup, and torn down on demand. Zero
cost between trips. No always-on component, no subscription, no AWS credentials
outside AWS.

## 2. What v1 was, and what happens to it

v1 was Terraform 0.11 + OpenVPN on EC2 in three stages: a root module creating
an S3 bucket, a `certifier/` instance running easy-rsa in Docker to mint a CA
and client/server certs into that bucket, and a `drive/` ECS-on-EC2 host running
a privileged OpenVPN container that pulled the certs back down. Six shell
scripts sequenced the applies. Ingress was 22, 443 and 1194 open to the world;
IAM was long-lived access keys carrying `AmazonEC2FullAccess`,
`AmazonS3FullAccess` and `IAMFullAccess`.

All of it is deleted. Nothing is edited or carried forward — the HCL predates
0.12 and won't parse against a current Terraform, and every AMI, instance type
and provider block in it is stale. v1 survives in git history on `master`.

## 3. Architecture

Three pieces, as the brief specifies.

```
  iPhone (Scriptable)                    AWS
        |                    ┌──────────────────────────────┐
        |  HTTPS + HMAC      │                              │
        └───────────────────►│  Lambda Function URL         │
                             │    handler.py                │
                             │      ├── ec2.py              │
        ┌────────────────────│      ├── tailscale.py        │
        │  hourly            │      └── watchdog.py         │
        │                    │                              │
   EventBridge Scheduler     │  SSM: secrets, config,       │
        │                    │       current-node pointer   │
        │                    │  SNS: idle notifications     │
        └───────────────────►│                              │
                             │  EC2 (any region, on demand) │
                             │    AL2023 arm64 t4g.small    │
                             │    cloud-init → tailscaled   │
                             └──────────────┬───────────────┘
                                            │ outbound only
                                     Tailscale coordination
                                            │
                                     laptop + phone
                                    (exit-node picker)
```

The Lambda is the entire control plane. Terraform provisions only static
scaffolding and is never used at runtime.

## 4. Repository layout

```
terraform/
  versions.tf          terraform >= 1.13, hashicorp/aws ~> 6.57
  lambda.tf            function, Function URL, reserved concurrency
  iam.tf               execution role, scheduler role
  scheduler.tf         aws_scheduler_schedule
  sns.tf               topic + email subscription
  ssm.tf               parameters (placeholder values)
  variables.tf outputs.tf
  terraform.tfvars.example
lambda/
  handler.py           auth, event routing, response shaping
  ec2.py               find / launch / terminate / security group
  tailscale.py         OAuth token, mint auth key
  watchdog.py          scheduled sweep
  userdata.sh          cloud-init (plain shell, string.Template placeholders)
tests/                 pytest, fully offline
docs/
  tailnet-setup.md     one-time: OAuth client, tag, autoApprovers ACL
  ios-shortcut.md      Scriptable script + home-screen wrapper
  ios/shardvpn.js      the Scriptable script
.github/workflows/ci.yml
CLAUDE.md  README.md  .gitignore  .trivyignore
```

Single Terraform root module. The v1 `certifier/` + `drive/` split existed to
sequence two applies; there is no longer a sequence.

## 5. Control plane

### 5.1 Request format

Lambda Function URL, `authorization_type = "NONE"`. Authentication is entirely
in the handler.

```
POST / HTTP/1.1
Content-Type: application/json
X-ShardVPN-Timestamp: 1753977600
X-ShardVPN-Signature: <hex sha256>

{"action":"up"}
```

`signature = HMAC-SHA256(secret, f"{timestamp}.{raw_body}")`

Verification order, strictly, before any AWS call or JSON parse:

1. Both headers present, timestamp parses as an integer.
2. `abs(now - timestamp) <= 120` seconds.
3. Recompute HMAC over the **raw body bytes exactly as received**.
   Function URLs deliver `event["body"]` base64-encoded when
   `isBase64Encoded` is true — decode to original bytes and sign those.
   Never sign re-serialized JSON; key order would differ.
4. `hmac.compare_digest`.

Failure at any step returns an identical `403` with the same body — no oracle
distinguishing a missing header from a bad signature. Doing this before any
AWS call also caps denial-of-wallet from someone holding the URL but not the
secret.

**Accepted residual risk:** replay is possible within the 120-second window.
Not mitigated with a nonce store, because all actions are idempotent — a
replayed `up` returns the existing node, a replayed `down` terminates an
already-terminated one — and a nonce table would add permanent state to a
project whose premise is having nothing running.

### 5.2 Actions

```
{"action":"up"}                        launch in default region
{"action":"up","region":"eu-west-1"}   launch elsewhere
{"action":"up","ttl":"48h"}            this node self-destructs
{"action":"down"}                      terminate + leave tailnet
{"action":"status"}                    report
```

All three actions return the same document:

```json
{
  "state": "running",
  "region": "ca-central-1",
  "instance_id": "i-0abc123",
  "public_ip": "3.98.0.0",
  "tailnet": "online",
  "hostname": "shardvpn-ca-central-1-4f2a",
  "age": "2h14m",
  "expires_at": null,
  "idle_for": "6m"
}
```

`state` is one of `absent`, `pending`, `running`, `shutting-down`.
`tailnet` is `online`, `absent`, or `unknown`.
`expires_at` is `null` when the `shardvpn:expires-at` tag is `never`.
`idle_for` is derived from the same CloudWatch `NetworkOut` query the watchdog
uses (§8.2), so `status` makes one `GetMetricStatistics` call; it is `null`
when `state` is not `running`.

**`status` queries Tailscale as well as EC2.** `state: running` with
`tailnet: absent` means the instance booted but cloud-init failed to join the
tailnet — otherwise indistinguishable from "wait longer" when you are standing
in an airport. This is the primary defence against the silent-failure mode the
brief warns about.

### 5.3 Idempotency

`DescribeInstances` is regional, so "is a node already up?" cannot be answered
by looking in the region you were asked to launch in — that yields one node per
region. Instead:

- SSM parameter `/shardvpn/current-node` holds `{"region":..., "instance_id":...}`
  and is authoritative for the Function URL path. `up` reads it first and
  returns the existing node rather than launching. `down` and `status` use it
  to locate the node without the caller passing a region.
- **Reserved concurrency = 1** on the Lambda, so two simultaneous requests
  cannot both read "no node" and both launch.
- `RunInstances` is called with a `ClientToken` so an AWS-side retry cannot
  double-launch.

The pointer can drift if the Lambda dies between `RunInstances` and the SSM
write. Reconciling that is the watchdog's job, which scans all regions anyway.

### 5.4 Event routing

The scheduled sweep is invoked as `{"action":"sweep"}`. The handler routes on
**event shape first**: a Function URL event has `requestContext.http`; a
Scheduler event does not. `sweep` is therefore unreachable over HTTP regardless
of signature validity.

## 6. The node

Amazon Linux 2023, arm64, `t4g.small` by default. AMI resolved per-region from
the SSM public parameter
`/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64`, which
exists in every region — this is what makes region-as-a-request-parameter work
without a per-region AMI table. AL2023 over Ubuntu because of that parameter
plus an official Tailscale package repo at
`pkgs.tailscale.com/stable/amazon-linux/2023/`, so no third-party image.

Default VPC, default subnet, so no region needs pre-provisioning.

### 6.1 Security group

The Lambda ensures a dedicated `shardvpn-exit` security group exists in the
target region — `DescribeSecurityGroups` by name, `CreateSecurityGroup` if
absent — with **zero ingress rules** and all egress allowed.

The default VPC's default SG would be nearly equivalent (its only ingress is
from other members of itself, and our instance would be the sole member), but a
dedicated zero-ingress group matches the requirement literally and is
self-documenting to anyone reading a public repo. Cost is three additional EC2
permissions on the execution role.

### 6.2 cloud-init

`userdata.sh` is plain shell using `${VAR}` placeholders filled by Python's
`string.Template.safe_substitute`. It is therefore a valid shell script,
readable and `shellcheck`-able, rather than a template that only becomes real
at runtime.

Order matters and *is* the design:

1. Write `/etc/sysctl.d/99-tailscale.conf` with `net.ipv4.ip_forward = 1` and
   `net.ipv6.conf.all.forwarding = 1`; `sysctl --system`.
2. Install a systemd oneshot running
   `ethtool -K $(ip -o route get 8.8.8.8 | cut -f5 -d" ") rx-udp-gro-forwarding on rx-gro-list off`,
   ordered `Before=tailscaled.service`.
   **Tailscale's documented recipe for persisting this is a
   `networkd-dispatcher` hook, which is Ubuntu-only — AL2023 has no
   `networkd-dispatcher`.** Copying their snippet would silently leave the node
   with no GRO tuning.
3. Install `tailscale` from the AL2023 repo; `systemctl enable --now tailscaled`.
4. Install a systemd unit ordered against `shutdown.target` whose `ExecStop`
   runs `tailscale logout`, so the node removes itself from the tailnet on
   termination (see §7.2).
5. `tailscale up --auth-key=${TS_AUTHKEY} --advertise-exit-node --ssh
   --hostname=${TS_HOSTNAME}`.
6. Assert `/proc/sys/net/ipv4/ip_forward` reads `1` and `tailscale status
   --json` shows a self node advertising the exit-node capability. Log loudly
   on failure.

### 6.3 Auth key handling

The auth key travels in instance user data, readable by anyone holding
`ec2:DescribeInstanceAttribute` on the account. Mitigated by minting it
per-launch as **single-use, pre-authorized, ephemeral, 10-minute expiry** — it
is spent before it is realistically readable.

### 6.4 Tags

| Tag | Value |
|---|---|
| `Name` | `shardvpn-exit-<region>-<suffix>` |
| `shardvpn:role` | `exit-node` |
| `shardvpn:launched-at` | RFC3339 |
| `shardvpn:expires-at` | RFC3339, or `never` |
| `shardvpn:last-idle-alert` | RFC3339, set by the watchdog |
| `shardvpn:ts-hostname` | the Tailscale hostname assigned |

## 7. Tailscale integration

### 7.1 Credential scope

An OAuth client with **`auth_keys` + `devices:core:read`**, restricted to
`tag:shardvpn-exit`. Client ID and secret live in SSM as SecureStrings.

`devices:core:read` is required for the `tailnet` field of `status` (§5.2) —
without it there is no way to tell "booted but never joined the tailnet" from
"still booting". It is strictly read-only: a leaked secret could enumerate
devices but could not remove, authorize, or retag any of them. The read-only
variant is confirmed to exist alongside the read-write `devices:core`.

The Lambda exchanges them at `POST /api/v2/oauth/token` (form-encoded
`client_id`, `client_secret`), then mints a key at
`POST /api/v2/tailnet/-/keys`:

```json
{
  "capabilities": {
    "devices": {
      "create": {
        "reusable": false,
        "ephemeral": true,
        "preauthorized": true,
        "tags": ["tag:shardvpn-exit"]
      }
    }
  },
  "expirySeconds": 600,
  "description": "shardvpn <region> <timestamp>"
}
```

The `-` tailnet alias is used deliberately so the tailnet name never appears in
a public repo.

### 7.2 Why read-write `devices:core` is not granted

Tag restrictions genuinely constrain `auth_keys`, but tag-scoping for device
*operations* is an open feature request
([tailscale/tailscale#10702](https://github.com/tailscale/tailscale/issues/10702)) —
read-write `devices:core` is therefore tailnet-wide: it can remove machines and
manipulate tags on every device, not just ours. Granting it so the Lambda could
delete one exit node would mean a leaked OAuth secret could remove any device
from the tailnet. The read-only `devices:core:read` carries no such risk, which
is why the split matters.

Instead the node removes itself: the shutdown-ordered unit from §6.2 runs
`tailscale logout` on termination, which removes an ephemeral node immediately.
Best-effort — a hard terminate or a failed hook skips it — with Tailscale's
own ephemeral expiry (30–60 minutes after last activity) as backstop. Both
paths converge on a clean exit-node picker, and the Lambda holds no device
permission at all.

### 7.3 One-time tailnet setup

Documented in `docs/tailnet-setup.md`, done once by hand:

- Create the OAuth client with scopes `auth_keys` and `devices:core:read`, and
  tag `tag:shardvpn-exit`.
- Define `tag:shardvpn-exit` in the policy file with an appropriate owner.
- Add `"autoApprovers": {"exitNode": ["tag:shardvpn-exit"]}` so exit nodes are
  approved without opening the admin console mid-trip.

## 8. TTL and the watchdog

### 8.1 TTL

**Default is no expiry.** TTL is a per-launch value, not a build-time constant:
the request may carry `ttl`, the default comes from SSM
`/shardvpn/default-ttl` (initially `none`), and whatever is used is written to
the `shardvpn:expires-at` tag. Changing the default later is one
`aws ssm put-parameter`, not a code change or a re-apply.

The reaping path is built and deployed from day one even though nothing expires
by default, because the scheduled-invoke path is the fiddly part to add later
(separate execution role, `scheduler.amazonaws.com` trust, a second route
through the handler) and because it is where the idle notification hangs.

### 8.2 Watchdog

`aws_scheduler_schedule`, `rate(1 hour)`, `flexible_time_window` mode
`FLEXIBLE` with a 15-minute window. Its own IAM role trusting
`scheduler.amazonaws.com` with `lambda:InvokeFunction` on the one function.

Each run:

1. `DescribeRegions`, then `DescribeInstances` per region filtered on
   `shardvpn:role = exit-node` and states `pending`/`running`. Parallelised.
2. Reconcile `/shardvpn/current-node`.
3. Terminate anything past `shardvpn:expires-at` (nothing, by default).
4. For each live node, sum CloudWatch `NetworkOut` over the trailing 24 hours.
   If below the threshold in `/shardvpn/idle-threshold-bytes` **and**
   `shardvpn:last-idle-alert` is absent or older than 24 hours, publish to SNS
   and stamp the tag.
5. Flag orphans — a tagged node in a region the pointer does not know about is
   either drift or a launch you did not make.

`NetworkOut` is the right signal because exit-node traffic leaves the instance
toward the client. It is part of EC2 basic monitoring: free, 5-minute
datapoints. Tailscale's `lastSeen` is *not* usable — it is only populated once
a node goes offline, so a running exit node never appears idle by that measure.

**The idle threshold is set from measurement during the end-to-end test, not
guessed.** An idle `tailscaled` still emits control-plane and DERP keepalives;
the number goes in SSM so it is tunable without a redeploy.

Step 5 doubles as an intrusion tripwire: a node someone else launched with a
leaked secret shows up as an unused node and generates an email within 24 hours.

## 9. IAM

Execution role. Honest about what does and does not scope:

**Scoped tightly**

- `ec2:TerminateInstances` — condition `ec2:ResourceTag/shardvpn:role = exit-node`.
  The destructive one, and it does support resource conditions.
- `ec2:RunInstances` — resource ARNs for instance, volume, network-interface,
  security-group, subnet, image; condition `aws:RequestTag/shardvpn:role` on
  the instance resource.
- `ec2:CreateTags` — condition `ec2:CreateAction = RunInstances`, so it can
  only tag at launch. Second narrow statement for stamping
  `shardvpn:last-idle-alert`, conditioned on `ec2:ResourceTag/shardvpn:role`.
- `ec2:CreateSecurityGroup`, `ec2:AuthorizeSecurityGroupEgress` — for §6.1.
- `ssm:GetParameter` on the named parameter ARNs, plus
  `arn:aws:ssm:*::parameter/aws/service/ami-amazon-linux-latest/*` for the
  public AMI parameter (note the empty account field).
- `ssm:PutParameter` on `/shardvpn/current-node` alone.
- `sns:Publish` on the one topic ARN.
- `logs:CreateLogStream`, `logs:PutLogEvents` on the function's own log group.

**Cannot be scoped — `Resource: "*"`, and the spec says so rather than
pretending otherwise**

- `ec2:DescribeInstances`, `ec2:DescribeRegions`, `ec2:DescribeSecurityGroups`
  — EC2 `Describe*` does not support resource-level permissions.
- `cloudwatch:GetMetricStatistics` — CloudWatch metrics do not support
  resource-level permissions.

Both are read-only. There is no `iam:*` anywhere, no S3, and no long-lived
access keys. `kms:Decrypt` on the SSM-managed key is added only if the
AWS-managed `alias/aws/ssm` key policy turns out to require it — to be
confirmed at implementation, not assumed.

## 10. Public-repo security posture

The repository is public. Nothing in the code needs to be secret; the risk is
what lands next to it.

- **Terraform must not hold secret values.** `aws_ssm_parameter` stores its
  value in state **in plaintext even for `SecureString`**. Parameters are
  therefore declared with placeholder values and
  `lifecycle { ignore_changes = [value] }`, and populated once out of band with
  `aws ssm put-parameter --overwrite`. No secret enters state, a plan output,
  or the repo.
- `.gitignore` covers `*.tfstate*`, `.terraform/`, `*.tfvars` (with
  `terraform.tfvars.example` committed). `.terraform.lock.hcl` **is** committed.
- The email address for SNS lives in gitignored tfvars, not the repo.
- The Function URL is unguessable but not secret and not independently
  rotatable. It stays out of the README, commits, and screenshots; it lives in
  the Scriptable script's config.
- Never log the signing secret, the OAuth secret, or a minted auth key —
  debugging output is what gets pasted into public issues.
- Secret rotation is documented as a two-step operation
  (`aws ssm put-parameter --overwrite`, update the phone) with no redeploy, so
  it is cheap enough to actually do.

## 11. Phone client

Scriptable rather than Shortcuts, because HMAC signing needs code and Shortcuts
has no HMAC action. Verified from Scriptable's docs: `Keychain.set/get/
contains/remove` and `Request` with custom headers and a string body.

This is also better for secret storage — the signing secret goes in Keychain
rather than a shortcut body. The script runs from a thin Shortcuts wrapper on
the home screen, so it stays one tap.

To confirm during implementation, not assumed: whether Scriptable exposes any
crypto primitive (its API index lists none, so the plan is a vendored ~60-line
pure-JS HMAC-SHA256 with no dependencies; its `WebView` could reach WebCrypto
as a fallback), and whether Keychain values sync to iCloud.

## 12. Runtime and dependencies

**Python 3.13**, zero third-party dependencies: `hmac`/`hashlib`,
`urllib.request`, `json`, `datetime`, plus the runtime's bundled `boto3`.

This means the deployment artifact is `data "archive_file"` over `lambda/` with
**no build step** — no Docker, no `pip install -t`, no layer, no bundler.
Nothing to rot between trips.

**Accepted tradeoff, recorded because it is real.** AWS recommends bundling the
SDK, and the docs state the included version depends on runtime version *and*
region, discoverable only by deploying a function that prints it. Automatic
runtime updates are the default, so AWS can change SDK behaviour without a
deploy — precedent exists (boto3 1.36 changed S3 default integrity behaviour;
runtimes have jumped 1.26 → 1.34). The failure mode is the function erroring
when triggered from an airport.

Accepted because every call here is EC2/SSM/SNS/CloudWatch —
`RunInstances`, `DescribeInstances`, `TerminateInstances`, `GetParameter`,
`Publish`, `GetMetricStatistics` — none of which touch S3, where the churn has
been. Failures are loud rather than silently wrong. Bundling remains a
`requirements.txt` plus one CI step away if drift ever bites.

`runtime_management_config = FunctionUpdate` was considered and rejected: it
freezes the whole runtime including Python and OS security patches, and this
function is internet-reachable. Prompt patching outweighs SDK stability here.

Languages: Python (Lambda), HCL (Terraform), bash (cloud-init), JavaScript
(Scriptable).

## 13. CI

`.github/workflows/ci.yml`, on `pull_request` and pushes to `master`.
`actions/checkout@v7`, `actions/setup-python@v7`.

| Job | Runs |
|---|---|
| `python` | `ruff check`, `ruff format --check`, `pytest` on Python 3.13 — pinned to the Lambda runtime so CI cannot pass on a version the function will not run |
| `terraform` | `fmt -check -recursive`, `init -backend=false`, `validate` |
| `shell` | `shellcheck lambda/userdata.sh` |
| `scan` | `trivy config terraform/` (tfsec is deprecated; its checks moved to Trivy), plus `gitleaks` |

**No AWS credentials in CI.** Every job is offline — `-backend=false` is what
makes `validate` work without them. Adding static keys to GitHub would violate
the constraint the brief is most emphatic about. If plan-on-PR is ever wanted,
the route is GitHub OIDC federating into a role, never stored keys.

Trivy findings that reflect deliberate choices (the `Resource: "*"` on
`Describe*`) go in `.trivyignore` with a written justification per entry, which
doubles as a public record of accepted risk.

Dependabot keeps action versions current. Branch protection requiring these
checks is a GitHub setting, so it is documented in the README rather than
committed.

## 14. Testing

**Unit — offline, fast, `pytest` + `botocore.stub.Stubber`** (not moto: no
version drift). Covering:

- Signature verification: valid, wrong secret, missing headers, expired
  timestamp, future timestamp, base64-encoded body.
- `expires-at` parsing and the `never` case.
- The idle decision over a synthetic metric series, including the
  `last-idle-alert` debounce.
- Pointer reconciliation, including drift and orphan cases.
- Response shaping for each `state`.
- Tailscale calls mocked at the `urllib` boundary.

**Terraform** — `fmt`, `validate`, `trivy`.

**End-to-end, manual, and the one that actually decides whether this works:**

1. `up` from the phone on cellular.
2. Poll `status` until `tailnet: online`. Record wall-clock time; target is
   under two minutes.
3. Select the node on laptop and phone simultaneously.
4. `curl ifconfig.me` from both — must return the instance's public IP. This
   is the proof, per the brief.
5. `down`. Confirm the instance is terminated **and** the device has left the
   exit-node picker.

Two deliberate exercises during that run:

- **Measure the idle `NetworkOut` baseline** over an hour to set
  `/shardvpn/idle-threshold-bytes` from data.
- **Launch once with the sysctl step removed**, to see what a forwarding
  failure looks like from the client side — at a desk, rather than in an
  airport. This is the failure the brief specifically warns is silent.

## 15. Cost

Zero between trips; nothing runs.

| Item | Cost |
|---|---|
| `t4g.small` | $0.0168/hr |
| Public IPv4 | $0.005/hr |
| **Running total** | **$0.0218/hr = $0.52/day = $3.67/week** |
| Lambda, EventBridge Scheduler, SNS, SSM standard, CloudWatch basic | $0 at this volume |

SNS free tier covers 1M API requests and 1,000 email deliveries/month.
CloudWatch's 1M free API requests covers `GetMetricStatistics`;
`GetMetricData` would not be free, so `GetMetricStatistics` is used
deliberately. Data egress is the one variable — the current free monthly
allowance is to be confirmed when writing the README rather than quoted from
memory.

## 16. Versions targeted

Verified against current documentation on 2026-07-31/08-01, not from memory.

| Component | Version |
|---|---|
| Terraform core | 1.15.8 — constraint `>= 1.13` |
| `hashicorp/aws` | 6.57.1 — constraint `~> 6.57` |
| Lambda runtime | `python3.13` (AL2023, deprecates 2029-06-30) |
| Node OS | Amazon Linux 2023, arm64 |
| Instance | `t4g.small` |
| `actions/checkout` | v7 |
| `actions/setup-python` | v7 |

## 17. Decisions

**Settled**

- Tailscale, not raw WireGuard or OpenVPN.
- Lambda control plane; no always-on component; no GitHub Actions trigger.
- No home exit node.
- Zero inbound security group rules, via a dedicated group.
- No long-lived AWS access keys anywhere.
- Region is a request parameter; default `ca-central-1`; AMI resolved
  dynamically; default VPC.
- arm64, small by default.
- Tailscale credentials in SSM SecureString, standard tier; short-lived tagged
  pre-authorized keys minted per launch.
- Exit-node approval automatic via tag + `autoApprovers`.
- Single POST endpoint with an `action` field; HMAC + timestamp auth.
- TTL configurable per launch, defaulting to no expiry; idle notification via
  SNS email instead of forced teardown.
- Python 3.13, runtime-provided boto3, no build step.
- **IPv6-only rejected**: default VPCs have no IPv6 CIDR, so it would require
  pre-provisioning every region, contradicting the design's core simplification.
  Saving is $0.12/day.

**Open, to resolve during implementation**

- The idle threshold value (set by measurement).
- Whether Scriptable exposes a crypto primitive, or a vendored HMAC is needed.
- Whether Keychain values sync to iCloud.
- Whether `kms:Decrypt` must be granted explicitly for `alias/aws/ssm`.
- Current AWS free egress allowance, for the README.

## 18. Out of scope

Anything running continuously. Anything with a monthly fee. Multi-user support.
Plan-on-PR in CI. IPv6-only instances.

## 19. Done looks like

- Trigger from the phone on cellular; node selectable within a couple of minutes.
- Laptop and phone use it simultaneously with no per-device setup.
- Nothing listening on a public port.
- $0 between trips, a few dollars for a week.
- A forgotten node generates an email within 24 hours of going idle.
