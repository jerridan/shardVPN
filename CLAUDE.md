# shardVPN

On-demand Tailscale exit node on EC2, triggered from a phone. v2 rewrite;
v1 (Terraform 0.11 + OpenVPN + a certificate authority) is dead and lives only
in git history on `master`. Do not use it as a reference for anything —
architecture, permissions model, or process.

## Architecture

Terraform provisions static scaffolding once and is not used at runtime. A
single Python 3.13 Lambda is the whole control plane: a Function URL serves
`up`/`down`/`status` from an HMAC-signed phone request, and EventBridge
Scheduler invokes the same function hourly with `sweep`. Cloud-init turns a
bare AL2023 arm64 instance into a Tailscale exit node with no inbound ports.

## Layout

- `lambda/shardvpn/` — a package, not loose modules (avoids shadowing at the
  zip root). `auth`, `ttl`, `statusdoc` and `render` are pure and fully
  unit-tested; `settings`, `ec2ops`, `tailscale` and `watchdog` touch the
  outside world.
- `lambda/shardvpn/userdata.sh` — cloud-init. A real shell script under
  `shellcheck`, not a template; `render.py` fills `${TS_AUTHKEY}` and
  `${TS_HOSTNAME}` via `string.Template`.
- `terraform/` — single root module.
- `docs/ios/shardvpn.js` — Scriptable client.
- `docs/tailnet-setup.md`, `docs/ios-shortcut.md` — the one-time human setup
  this code depends on but cannot do for itself.
- `docs/superpowers/specs/2026-08-01-shardvpn-v2-design.md` — the design and
  the reasoning behind every tradeoff. Read this before changing behaviour,
  not just this file.

## Running and verifying

```bash
uv sync --group dev
uv run ruff check . && uv run ruff format --check . && uv run pytest -v
cd terraform && terraform init -backend=false && terraform validate
shellcheck lambda/shardvpn/userdata.sh
trivy config terraform/
```

Tests are offline: `botocore.stub.Stubber` for AWS, `urlopen` patched for
Tailscale, a client factory for `watchdog.sweep`. There is no integration
test — the real check is the end-to-end run (`curl ifconfig.me` through the
node from two devices at once).

`ruff format` formats Python code fences inside Markdown files too, not just
`.py` files — any Python you put in a doc has to be ruff-clean, or write it
inside a non-`python`-tagged fence (e.g. embed it in a `bash` heredoc/one-liner
instead) if it's illustrative shell usage rather than a real module.

## Settled

- Tailscale, not OpenVPN. No per-trip key distribution.
- Lambda control plane; nothing runs between trips.
- HMAC + timestamp auth on a Function URL with `authorization_type = NONE`,
  which still needs two `aws_lambda_permission` resources to be reachable.
  120s skew. Replay inside that window is accepted; all actions are idempotent.
- Idempotency is the SSM pointer `/shardvpn/current-node` plus reserved
  concurrency 1. `up` fails closed if the pointer cannot be read.
- TTL defaults to `none`. Forgotten nodes are surfaced by a 24h idle SNS
  email, not killed. Duplicates ARE killed.
- Python 3.13, no third-party deps, runtime-provided boto3, no build step.
  Tradeoff recorded in spec §12.
- OAuth client gets `auth_keys` + `devices:core:read`. The node removes itself
  from the tailnet via a shutdown-ordered `tailscale logout`.
- IPv6-only rejected: default VPCs have no IPv6 CIDR.

## Open

- The exact `tailscale status --json` key for exit-node advertisement
  (`Self.ExitNodeOption`, checked in `userdata.sh`) is an **unverified
  guess**, pending live end-to-end testing. If a node advertises correctly
  but the cloud-init verification loop still times out and shuts it down,
  check this key first against real `tailscale status --json` output.
- The idle threshold in `/shardvpn/idle-threshold-bytes` (currently a
  round-number default) is a guess pending measurement of real idle
  `NetworkOut` during an end-to-end run.
- Whether Scriptable exposes `crypto` or `TextEncoder` is unprobed — both
  are vendored in `docs/ios/shardvpn.js` on the assumption it does not.
  If a live run shows they exist, the vendored versions are extra code, not
  a bug, but could be deleted.
- Whether Scriptable Keychain values sync to iCloud is unconfirmed either
  way. Until it's known, assume every device sharing iCloud Keychain with
  the configured phone can control the VPN.
- Whether `kms:Decrypt` must be granted explicitly for `alias/aws/ssm`.
  The README's setup step 3 has a `curl` that answers this: `403` means the
  secret decrypted, `503` means the role needs the grant. Do NOT try to
  verify by assuming the `shardvpn-lambda` role — its trust policy admits
  only `lambda.amazonaws.com`, so you get an `AccessDenied` about the wrong
  thing entirely.
- `archive_file`'s `excludes = ["**/__pycache__/**"]` was verified against
  the provider's `doublestar` matching by reading its source, not by a real
  `terraform apply`. If a stray `.pyc` ever lands in the deployment zip,
  start there.

## Known and accepted

Findings from the final whole-branch review that were triaged as ship-as-is.
Recorded so nobody rediscovers them as if they were new:

- **`down` only scans the default region when the SSM pointer is unreadable.**
  If a launch in a non-default region loses its pointer write, `down` reports
  `absent` while that node keeps billing. The hourly sweep repairs the pointer
  within an hour, after which a second `down` works. A full cross-region scan
  on `down` would close it.
- **Concurrent `up` requests can still race.** The regional `find_nodes` check
  runs before two Tailscale round-trips, so two near-simultaneous launches can
  both pass it. Bounded and self-healing: the sweep terminates all but the
  newest.
- **A saturated Function URL is a denial of control.** Reserved concurrency is
  1, so anyone holding the URL can occupy the slot with junk and stall your
  own `down`. The `Throttles` alarm fires, and Scheduler retries the sweep.
- **`find_nodes` filters `pending`/`running`**, so an instance stopped by hand
  in the console is invisible to both `down` and the sweep while EBS bills.
  The in-guest path is covered by `InstanceInitiatedShutdownBehavior`.
- **The unresolved-duplicates case emits two SNS emails** — its own specific
  message plus the generic failure aggregate.
- **`tests/test_watchdog.py` uses `MagicMock`, not `Stubber`**, so watchdog AWS
  calls get no request-shape validation. It is the only module where that is
  true, and it is the unattended one.
- **The orphan alert has no debounce.** If `ssm:PutParameter` is denied, it
  emails hourly, indefinitely.

## Gotchas

Real ones, each hit during this build:

- `aws_ssm_parameter` stores its value in Terraform state in plaintext even
  for `SecureString`. Secret parameters use a placeholder value plus
  `lifecycle { ignore_changes = [value] }`, and are populated for real with
  `aws ssm put-parameter --overwrite`, never through Terraform.
- Tailscale's documented recipe for persisting UDP GRO settings uses a
  `networkd-dispatcher` hook, which does not exist on AL2023 (it's an
  Ubuntu-ism). We use a systemd oneshot ordered `Before=tailscaled.service`
  instead. Getting the ordering wrong doesn't error — it just leaves GRO
  untuned and the node routes traffic, slowly, with no diagnostic.
- `dnf config-manager` lives in `dnf-plugins-core`, which is not reliably
  present on the base AL2023 AMI. Install it *before* `dnf config-manager
  --add-repo` for the Tailscale repo, or `set -euo pipefail` aborts the
  script partway — after the GRO unit exists, before Tailscale is installed
  — leaving a running, billing instance that never joins the tailnet.
- `CreateSecurityGroup` already attaches an allow-all egress rule. Calling
  `authorize_security_group_egress` with the same rule afterwards returns
  `InvalidPermission.Duplicate` and fails every first launch in a region.
- Sign over the raw request bytes, not re-serialized JSON. Function URLs
  base64-encode `event["body"]` when `isBase64Encoded` is set; decode to the
  original bytes before computing/verifying the HMAC, or client and server
  disagree about what was actually signed.
- Validate that the signature header is 64 lowercase hex characters *before*
  calling `hmac.compare_digest` — it raises `TypeError` on a non-ASCII
  string, which without the pre-check turns into an unhandled exception and
  a bodyless `502`, where every other malformed request correctly gets `403`.
  That's an oracle, and also just a worse error for a legitimate bad client.
- Use `[0-9]`, not `\d`, in every input-validation regex in this codebase.
  Python's `\d` matches Unicode decimal digits (Arabic-Indic, fullwidth,
  etc.), which `int()` then parses without complaint — `\d` would silently
  accept inputs `[0-9]` correctly rejects.
- **Never call `log.exception` in the handler.** It appends a traceback
  whose last line is `str(exc)`, and botocore's `ParamValidationError` can
  embed the offending parameter value — for `run_instances`, that's
  `UserData`, which carries a minted, live (if short-lived) Tailscale auth
  key. Use `log.error(..., type(exc).__name__)` and nothing else.
- `botocore.stub.ANY` works as a value *inside* `expected_params` (e.g.
  `{"InstanceIds": ANY}`) but not as the whole `expected_params` argument.
  Omit `expected_params` entirely to skip request-shape assertions for a
  stubbed call.
- In `.trivyignore`, a `#`-commented finding ID suppresses nothing — Trivy
  only reads bare ID lines. Comments belong on the lines *above* the ID, not
  prefixed onto it; `# AWS-0017` on its own line is a no-op, not a
  suppression.
- Terraform now installs from HashiCorp's own tap, not homebrew-core:
  `brew install hashicorp/tap/terraform`. `brew install terraform` alone no
  longer works.
- Address the tailnet as `-` in every Tailscale API call
  (`/api/v2/tailnet/-/...`), never by its real name — this repository is
  public, and the tailnet name isn't secret but has no reason to be in it.

## History

- **v1 was decommissioned on 2026-08-03.** The `shard_vpn` IAM user and its
  2018-vintage access key are deleted. Its compute was already gone before
  the sweep — no instances in any region, no ECS cluster, no key pairs, no
  security groups, no volumes, and the `shard-vpn-keys` bucket had been
  removed at some earlier point.

  Two things the original decommission note got wrong, worth knowing if you
  ever read it in git history:

  - The permissions were never attached to the user. They came from a shared
    IAM **group** called `vpn` (`IAMFullAccess`, `AmazonEC2FullAccess`,
    `AmazonS3FullAccess`, `AmazonEC2ContainerServiceFullAccess`,
    `CloudWatchLogsFullAccess`, plus an inline CloudFormation policy).
  - **Three unrelated users shared that group** — `hackintosh`,
    `nulogy-laptop`, `windows-desktop` — each with an active, unused,
    MFA-less access key carrying `IAMFullAccess`, i.e. self-escalation to
    administrator. Deleting `shard_vpn` alone would have closed one of four
    identical holes while reporting the job done.

  Those three keys were set to `Inactive` (reversible; none had been used
  since 2018–2020, and none of the users has a console password, so they now
  have no access path at all). The `vpn` group itself still exists with
  `IAMFullAccess` attached — reactivating any of those keys reopens the
  escalation. Detaching `IAMFullAccess` from the group, or deleting the
  group and its users outright, is unfinished business unrelated to shardVPN.
