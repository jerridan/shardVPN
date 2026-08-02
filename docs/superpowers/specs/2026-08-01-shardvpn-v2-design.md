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
  alarms.tf            Lambda error/throttle alarms, budget
  variables.tf outputs.tf
  terraform.tfvars.example
lambda/
  shardvpn/            a package, not loose modules — see below
    __init__.py
    auth.py            HMAC verification (pure)
    ttl.py             TTL parsing and expiry (pure)
    statusdoc.py       the response document (pure)
    render.py          userdata rendering (pure)
    settings.py        SSM parameters and the current-node pointer
    tailscale.py       OAuth token, mint auth key, device lookup
    ec2ops.py          find / launch / terminate / security group
    watchdog.py        scheduled sweep
    handler.py         auth gate, event routing, action dispatch
    userdata.sh        cloud-init (plain shell, string.Template placeholders)
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

The Lambda code is a **package** rather than loose modules at the zip root.
Flat modules named `config`, `status` and `render` sit on `sys.path` ahead of
anything the runtime imports, which is a known shadowing footgun; a package
directory costs nothing and removes the class of problem. The handler is
`shardvpn.handler.lambda_handler`. `settings.py` and `statusdoc.py` are named
to avoid colliding with common module names even inside the package.

## 5. Control plane

### 5.1 Request format

Lambda Function URL, `authorization_type = "NONE"`. Authentication is entirely
in the handler.

**`NONE` still requires a resource-based policy.** Since October 2025 function
URLs need *both* `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction`, and
only the console and SAM create that policy for you — via Terraform you must
add it yourself or every request returns `403` from the Lambda service,
indistinguishable from a signature mismatch. Two `aws_lambda_permission`
resources are required: one for `lambda:InvokeFunctionUrl` with
`function_url_auth_type = "NONE"`, and one for `lambda:InvokeFunction` with
`invoked_via_function_url = true` so the public grant cannot be used to invoke
the function by any other route. That argument landed in AWS provider
**v6.28.0**, inside our `~> 6.57` constraint.

```
POST / HTTP/1.1
Content-Type: application/json
X-ShardVPN-Timestamp: 1753977600
X-ShardVPN-Signature: <hex sha256>

{"action":"up"}
```

`signature = HMAC-SHA256(secret, f"{timestamp}.{raw_body}")`

Verification order, strictly, before any AWS call or JSON parse:

1. Both headers present.
2. Timestamp matches `^[0-9]{1,11}$` **before** `int()`. Python's `int()`
   accepts surrounding whitespace, a leading `+`, underscore separators and
   non-ASCII digits; each would make client and server disagree about the
   canonical signed message for the same header. The character class must be
   `[0-9]` and not `\d` — Python's `\d` matches Unicode decimal digits, so it
   would admit the very Arabic-Indic numerals `int()` then parses happily.
3. Signature matches `^[0-9a-f]{64}$`. Without this check
   `hmac.compare_digest` raises `TypeError` on a non-ASCII header value,
   producing a `502` where every other bad request produces `403` — exactly
   the oracle this section forbids.
4. `abs(now - timestamp) <= 120` seconds.
5. Recompute HMAC over the **raw body bytes exactly as received**.
   Function URLs deliver `event["body"]` base64-encoded when
   `isBase64Encoded` is true — decode to original bytes and sign those.
   Never sign re-serialized JSON; key order would differ. A malformed base64
   body raises `binascii.Error`, which must be caught and converted to the
   same failure as any other.
6. `hmac.compare_digest`.

Failure at any step returns an identical `403` with the same body — no oracle
distinguishing a missing header from a bad signature, and no input that
produces a `5xx` instead of a `403`.

**The signing secret is cached in a module-level global with a 300-second
TTL.** Without it, every unauthenticated request costs an `ssm:GetParameter`
plus a `kms:Decrypt` before the signature is even checked, which contradicts
the denial-of-wallet claim this design makes. The TTL bounds how long a
rotated secret takes to take effect; §10 documents that.

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

`region` is validated against `DescribeRegions` before use and rejected with a
`400` listing valid values. It is otherwise interpolated straight into a
`boto3.client` call, the Tailscale hostname and the EC2 `Name` tag —
unvalidated, garbage yields a DNS failure and an opaque `502`, and a region
the account has not opted into yields an equally opaque `AuthFailure`.

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

`idle_for` is the time since the most recent `NetworkOut` datapoint above
zero, from the same `GetMetricStatistics` query the watchdog uses (§8.2). It
is `null` when `state` is not `running`, and **also `null` when the node is
younger than 15 minutes** — EC2 basic monitoring publishes on a 5-minute
period and lags several minutes behind, so a freshly launched node would
otherwise report itself as maximally idle on the one screen you are staring
at in an airport.

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
- **`up` fails closed on an unreadable pointer.** `ParameterNotFound` means
  "no node, safe to launch"; any other error returns `503` rather than
  launching. Treating a transient SSM error as "no node" would turn a blip
  into a second instance, and reserved concurrency serialises requests without
  making them idempotent.
- **A regional `DescribeInstances` runs immediately before `RunInstances`.**
  This does not close the global window — the scan is regional — but it
  eliminates the overwhelmingly common double-tap-in-the-same-region case.
- **Reserved concurrency = 1** on the Lambda, so two simultaneous requests
  cannot both read "no node" and both launch.
- `RunInstances` is called with a `ClientToken` so an AWS-side retry cannot
  double-launch.

The pointer can drift if the Lambda dies between `RunInstances` and the SSM
write. Reconciling that is the watchdog's job, which scans all regions anyway.

**Duplicates are terminated, not merely reported.** If the sweep finds more
than one live node it keeps the newest by `shardvpn:launched-at` and
terminates the rest, and `down` terminates every live node rather than the
first one it happens to see. Reconciling the *pointer* without reconciling
*reality* would leave a duplicate running at $0.52/day until a human read an
email — and with TTL defaulting to `none`, forever if they did not.

Reserved concurrency 1 means a running sweep makes the Function URL return
`429`. The sweep is parallelised (§8.2) to keep that window to seconds, and
the phone client retries with backoff on `429` and `5xx`.

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
target region — `DescribeSecurityGroups` filtered by name **and by the default
VPC's id**, `CreateSecurityGroup` if absent — with **zero ingress rules** and
all egress allowed.

Two details that are easy to get wrong:

- The lookup must filter on `vpc-id`, resolved via `DescribeVpcs` with
  `isDefault=true`. Group names are unique per VPC, not per region, so an
  unfiltered lookup can return a group in a non-default VPC and
  `RunInstances` then fails with `InvalidParameterValue: Security group does
  not belong to VPC`.
- **`CreateSecurityGroup` already attaches an allow-all egress rule.** Calling
  `AuthorizeSecurityGroupEgress` with the same rule afterwards returns
  `InvalidPermission.Duplicate` and fails every first launch in a region. The
  egress intent is recorded in the group description and a comment, not by
  re-adding a rule AWS created.

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
3. `dnf install -y dnf-plugins-core ethtool`, **then** add the Tailscale repo
   and install it. `dnf config-manager` lives in `dnf-plugins-core`, which is
   not reliably present on the base AL2023 AMI. Under `set -euo pipefail` a
   missing `config-manager` aborts the script *after* the GRO unit is
   installed and *before* Tailscale is — leaving a running, billed instance
   that never joins the tailnet.
4. Install a `Type=oneshot` + `RemainAfterExit=yes` unit whose `ExecStop` runs
   `tailscale logout`, so the node removes itself from the tailnet on
   termination (see §7.2). It is ordered `After=tailscaled.service` and
   `After=network-online.target` so it stops *before* both — a unit with no
   network ordering can be stopped after networking is torn down, at which
   point `tailscale logout` cannot reach the coordination server. It sets
   `TimeoutStopSec=20` so a hung logout cannot consume EC2's finite
   termination grace.
5. `tailscale up --auth-key=${TS_AUTHKEY} --advertise-exit-node --ssh
   --hostname=${TS_HOSTNAME}`.
6. **Verify before advertising, and fail closed.** `tailscale up` runs
   *without* `--advertise-exit-node`; forwarding is asserted first; only then
   is `tailscale set --advertise-exit-node` issued; and the advertisement is
   confirmed in a **bounded retry loop** (`tailscale up` returns once
   authenticated, but the netmap may not yet reflect the advertisement, so a
   single-shot check would fail healthy nodes and become the silent failure it
   exists to catch).

   On any failure the node **withdraws the advertisement, leaves the tailnet,
   and shuts down** — it does not merely log. Logging alone leaves an instance
   that is joined, advertised, billing, and indistinguishable from healthy on
   the exit-node picker, which is precisely the failure being guarded against;
   a nonzero cloud-init exit affects nothing on its own, since EC2 health
   checks are hypervisor-level.

   The confirmation must be scoped to the node's **own `Self` entry**, parsed
   as JSON rather than grepped across the whole document. An unscoped match
   is satisfied by *any* peer advertising as an exit node — including the
   30-60 minute ephemeral remnant of a node this service just replaced during
   a region switch.

   Because the failure path shuts down, `RunInstances` sets
   `InstanceInitiatedShutdownBehavior = terminate`: the EBS-backed default is
   *stop*, and a stopped instance is invisible to the pending/running filter
   the reaper uses, so it would linger unreaped while EBS billed.

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

**Device lookup matches on the `tags` array, not on `hostname`.** The device
object carries both `name` (a MagicDNS FQDN) and `hostname` (the machine's
reported hostname), and whether `tailscale up --hostname=X` surfaces verbatim
as `hostname` is not something to bet on — if it is sanitised or differs, an
exact-string match returns `None` forever and `status` reports `tailnet:
absent` for a perfectly healthy node. Membership of `tag:shardvpn-exit` is
guaranteed by the auth key we minted. Hostname is used only to disambiguate
when more than one tagged device is present.

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
- Add an `ssh` stanza granting access to `tag:shardvpn-exit`. `tailscale up
  --ssh` does nothing without one, and reading
  `/var/log/shardvpn-init.log` over `tailscale ssh` is the only diagnostic
  path into a node with no inbound ports.

## 8. TTL and the watchdog

### 8.1 TTL

**Default is no expiry.** TTL is a per-launch value, not a build-time constant:
the request may carry `ttl`, the default comes from SSM
`/shardvpn/default-ttl` (initially `none`), and whatever is used is written to
the `shardvpn:expires-at` tag.

Two spellings mean "no expiry" and both must parse. `none` is the
configuration and request spelling — the SSM parameter and the JSON `ttl`
field; `never` is the stored tag value. Accepting only one of them breaks the
default path: the handler passes the SSM value straight to the TTL parser
whenever a request omits `ttl`, which is the normal case, so rejecting `none`
would return `400` on every launch under default configuration. Changing the default later is one
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
   `shardvpn:role = exit-node` and states `pending`/`running`. **Parallelised
   with a `ThreadPoolExecutor`** (stdlib, no dependency) over a cached
   per-region client map. There are roughly 34 enabled regions; done
   sequentially, with client construction and endpoint resolution per
   iteration, this does not reliably fit in a 60-second timeout — and a
   timed-out sweep is retried twice by the async invoke policy and then
   dropped silently. Function timeout is 300s, which costs nothing.
2. Reconcile `/shardvpn/current-node`.
3. Terminate anything past `shardvpn:expires-at` (nothing, by default).
4. For each live node, sum CloudWatch `NetworkOut` over the trailing 24 hours.
   If below the threshold in `/shardvpn/idle-threshold-bytes` **and**
   `shardvpn:last-idle-alert` is absent or older than 24 hours, publish to SNS
   and stamp the tag.
5. Flag orphans — a tagged node in a region the pointer does not know about is
   either drift or a launch you did not make. **The two are worded
   differently.** "The pointer disagrees" and "the pointer could not be read"
   are distinct conditions, and only the first warrants suggesting a secret
   rotation. A tripwire that cries intrusion on your own transient SSM errors
   is a tripwire you learn to ignore.
6. Terminate duplicates, keeping the newest by `shardvpn:launched-at`.

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
- `ec2:RunInstances` — **split into two statements**. The `instance/*` ARN
  carries `aws:RequestTag/shardvpn:role = exit-node` and a `StringLike` bound
  of `t4g.*` on `ec2:InstanceType`; a second statement covers the supporting
  resource types (image, volume, network-interface, security-group, subnet)
  where request-tag conditions do not apply uniformly.

  Both halves matter. Without the request-tag condition, a bug in the tag
  dictionary could produce an untagged instance that the tag-conditioned
  `TerminateInstances` can **never** reap — neither `down` nor the watchdog
  could remove it. Without the instance-type bound, nothing structural stops a
  `p5.48xlarge`; the type comes from SSM today, but IAM is the only cap that
  does not depend on this code being correct.
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

- `ec2:DescribeInstances`, `ec2:DescribeRegions`, `ec2:DescribeSecurityGroups`,
  `ec2:DescribeVpcs` — EC2 `Describe*` does not support resource-level
  permissions.
- `cloudwatch:GetMetricStatistics` — CloudWatch metrics do not support
  resource-level permissions.

Both are read-only. There is no `iam:*` anywhere, no S3, and no long-lived
access keys.

`kms:Decrypt` on the SSM-managed key is probably unnecessary — the AWS-managed
`alias/aws/ssm` key policy grants account principals via a `kms:ViaService`
condition — but "probably" is not good enough for something on the
authentication hot path, where a failure surfaces as an opaque `502`.
Verification is an explicit deployment step: assume the `shardvpn-lambda` role
and run `aws ssm get-parameter --with-decryption` against the signing secret.
Two minutes, and it removes the unknown.

### 9.1 Operational safety net

The premise of this project is that nothing runs between trips, which means
nothing is watching. The watchdog is the only mechanism bounding cost, and it
can fail permanently and silently: an async invoke retries twice and then
disappears. Two CloudWatch alarms on the function — `Errors > 0` and
`Throttles > 0`, both publishing to the existing SNS topic — close that hole
for about twenty lines of Terraform.

An `aws_budgets_budget` at a low monthly threshold, alarming to the same
topic, is another ten lines and is the only defence that does not depend on
any of this code being correct.

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
  `terraform.tfvars.example` committed) and `terraform/.build/`, where
  `archive_file` writes the Lambda zip at apply time. `.terraform.lock.hcl`
  **is** committed.
- **v1 must be decommissioned before its code is deleted.** v1's README
  instructs the operator to create an IAM user with `IAMFullAccess`,
  `AmazonEC2FullAccess` and `AmazonS3FullAccess`, and a long-lived access key
  in `~/.aws/credentials`. That user, its key, the `shard-vpn-keys` bucket and
  any surviving certifier or drive instances all still exist. The claim "no
  long-lived AWS access keys anywhere" is false until they are gone, and the
  only tooling that tears v1 down is the shell scripts this rewrite deletes.
  Decommissioning is therefore the first task, not a cleanup afterthought.
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

The client **retries with backoff on `429` and `5xx`**. Reserved concurrency 1
means a concurrent sweep returns `429`, and a client that renders that as
`undefined` on the notification would be indistinguishable from a real
failure at exactly the wrong moment.

To confirm during implementation, not assumed: whether Scriptable exposes any
crypto primitive (its API index lists none, so the plan is a vendored ~60-line
pure-JS HMAC-SHA256 with no dependencies; its `WebView` could reach WebCrypto
as a fallback), **whether `TextEncoder` exists** (JavaScriptCore does not
provide it natively and Scriptable's API index does not list it — its absence
breaks the client outright with a `ReferenceError`, so the byte encoding must
fall back to a manual UTF-8 encoder), and whether Keychain values sync to
iCloud.

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

**Each job is added by the task that creates its input, not up front.** A CI
pipeline that is deliberately red for most of the build gates nothing — nobody
can distinguish an expected failure from a real regression, which is the
opposite of the point. The `python` job exists from the first commit and stays
green; `shell`, `terraform` and `scan` arrive with the files they check.

Third-party actions are pinned to release tags, never `@master` — an unpinned
ref in the job whose purpose is supply-chain hygiene is self-defeating, and
Dependabot cannot update it either.

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
- Pointer reconciliation, including drift, orphan and duplicate cases.
- Response shaping for each `state`.
- Tailscale calls mocked at the `urllib` boundary.

`sweep` is the most complex function in the system and the only one that runs
unattended, so it must be genuinely testable rather than nominally covered.
It therefore **takes a client factory as a parameter** (`sweep(now,
client_factory=boto3.client)`) instead of constructing its own clients — one
argument, and the multi-region scan, reap, orphan, duplicate and debounce
paths all become stubbable.

Every action path is wrapped in a top-level handler that logs the exception
type and returns a `500` with the request id. Two reasons: an unhandled
`ClientError` otherwise becomes a bodyless `502` that the phone renders as
`undefined`, and botocore's `ParamValidationError` can embed offending
parameter values — which for `run_instances` means the `UserData` carrying a
minted auth key. Catching around the launch and logging only the exception
type is what keeps the "never log an auth key" constraint true.

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
| `hashicorp/aws` | 6.57.1 — constraint `~> 6.57`. Floor is really 6.28, which added `invoked_via_function_url` |
| Lambda runtime | `python3.13` (AL2023, deprecates 2029-06-30) |
| Node OS | Amazon Linux 2023, arm64 |
| Instance | `t4g.small` |
| `actions/checkout` | v7 — v7.0.1, published 2026-07-20, re-confirmed against the releases API |
| `actions/setup-python` | v7 — v7.0.0, published 2026-07-20 |

`hashicorp/setup-terraform`, `aquasecurity/trivy-action` and
`gitleaks/gitleaks-action` majors are to be checked against their release
pages when the workflow is written, and pinned to tags.

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

Added after external review:

- v1 is decommissioned — IAM user, access key, S3 bucket and instances — as
  the first task, before the code that tears it down is deleted.
- Lambda code ships as a package, not loose modules.
- Two `aws_lambda_permission` resources; a `NONE` function URL is not public
  without them.
- `up` fails closed on an unreadable pointer; duplicates are terminated, not
  just reported.
- IAM bounds `ec2:InstanceType` to `t4g.*` and conditions `RunInstances` on
  the role request tag.
- CloudWatch alarms on function errors and throttles, plus a monthly budget.

**Open, to resolve during implementation**

- The idle threshold value (set by measurement).
- Whether Scriptable exposes a crypto primitive, and whether `TextEncoder`
  exists there.
- Whether Keychain values sync to iCloud.
- Whether `kms:Decrypt` must be granted explicitly for `alias/aws/ssm` —
  now with an explicit verification step rather than an assumption.
- Whether `tailscale up --hostname` surfaces verbatim as the API device
  object's `hostname`. Mitigated by matching on tags instead.
- The exact `tailscale status --json` key for exit-node advertisement.
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
