# Weekly end-to-end tests — plan

**Date:** 2026-08-06
**Branch:** `claude/weekly-e2e-tests-pkcxfy`
**Status:** plan only. Nothing here is built yet.

**Goal:** Find out that shardVPN has stopped working on a Sunday morning at
home, not on a Tuesday night in an airport.

## 1. Why the existing CI is not enough

`ci.yml` proves the code is internally consistent: the pure modules behave, the
stubs match the shapes botocore expects, the shell parses, the HCL validates.
All of it runs offline, which is exactly why none of it can fail for the reason
this system will actually fail. Every hard bug in the build so far came from
outside the repository — `dnf config-manager` missing from the base AMI, the
`networkd-dispatcher` hook that does not exist on AL2023, the
`InvalidPermission.Duplicate` on a fresh region's security group, the
`autogroup:internet` grant that the wildcard does not cover. A test suite with
no AWS account and no tailnet cannot see any of them.

What can rot between trips, and what would notice:

| Rots | Noticed by |
|---|---|
| AWS provider breaking change inside `~> 6.57` | `terraform init -upgrade` + `validate`, offline |
| Tailscale API shape (`/oauth/token`, `/tailnet/-/keys`, device fields) | a real `up` |
| AL2023 AMI alias, Tailscale dnf repo, GRO systemd unit | a real boot |
| Function URL reachable, secret populated, HMAC path, `kms:Decrypt` | a real signed request |
| Policy-file drift that silently kills exit routing | real traffic through the node |
| `python3.13` Lambda runtime deprecation | AWS emails; also a launch failure |

Rows 2–5 need a node that boots and carries a packet. That is the whole
justification for what follows.

## 2. Shape

One new workflow, `.github/workflows/e2e.yml`, on `schedule` (weekly) and
`workflow_dispatch`. Two jobs.

**`drift`** — no credentials, no cost. `terraform init -upgrade -backend=false`
then `validate`, plus the existing pytest/ruff/shellcheck suite. `-upgrade`
is the point: it resolves the newest provider allowed by `~> 6.57` rather than
the one `.terraform.lock.hcl` pins, so a provider-side removal surfaces here
instead of during an apply you are trying to do in a hurry.

**`live`** — the real thing:

1. `status`. **If a node is already up, skip the entire job and exit green.**
2. `up`, with `ttl: "30m"` and an explicit region.
3. Poll `status` until `state: running` and `tailnet: online`.
4. Join the runner to the tailnet as an ephemeral, tagged node.
5. Record the runner's egress IP, set the exit node, assert the egress IP
   changed and now equals `public_ip` from the status document.
6. `down`. Assert the response is `absent`.
7. `if: always()` cleanup, then a `DescribeInstances` assertion that no
   exit-node instance survives in the test region.

Step 1 is not optional. The `current-node` pointer is global and `down`
terminates whatever it finds in the pointed-at region — a test that runs while
you are using the VPN and does not check first will kill your connection from
under you. Skipping means a long trip produces no weekly signal; that is the
correct trade and worth stating out loud rather than discovering.

Step 5 is the only step that can catch policy-file drift. A node that boots,
joins, advertises and routes nothing looks completely healthy to `status` —
`tailnet: online` is true, the instance is `running`. Only pushing a packet
through it distinguishes the two.

## 3. Credentials

The v2 plan's global constraints say **"No AWS credentials in CI. Every job
runs offline."** This plan deliberately breaks that. It is the central trade
here and should be recorded as such rather than quietly dropped.

What survives is the constraint underneath it — *no long-lived AWS access keys
anywhere*, the one that made v1's decommission necessary. The `live` job
authenticates by GitHub OIDC to a dedicated role, so the credential is a
15-minute STS session minted per run, revocable by deleting one role, auditable
in CloudTrail. That is strictly better than the alternative it replaces (an
access key in GitHub secrets), and no worse than the phone, which holds a
signing secret in an app-wide keychain.

**No secret material goes into GitHub at all.** The only repository-level value
is `vars.AWS_ROLE_ARN`, which is not a secret. Everything else the job needs it
reads from SSM at runtime with the OIDC session:

- **Function URL** — `aws lambda get-function-url-config --function-name shardvpn`.
  Terraform state is local and gitignored, so CI cannot read the output; asking
  the Lambda service is the way to get it without a second copy anywhere.
- **Signing secret** — `/shardvpn/signing-secret`, the same parameter the
  handler reads.
- **CI tailnet credentials** — a *second* OAuth client, restricted to
  `tag:shardvpn-ci` with `auth_keys` scope only, stored in two new SecureString
  parameters (`/shardvpn/ci-tailscale-client-id`, `-client-secret`) following
  the existing placeholder + `ignore_changes` + out-of-band `put-parameter`
  pattern. Separate from the production client so a leak from CI cannot mint
  keys for `tag:shardvpn-exit`, and so either can be rotated alone.

Values read from SSM must be passed through `::add-mask::` before being handed
to any action input, or a failure inside `tailscale/github-action` can echo
them into a public log.

### The role

New Terraform, gated behind a `variable "enable_ci_role"` defaulting to `false`
so the account is unchanged until you deliberately turn it on:

- `aws_iam_openid_connect_provider` for `token.actions.githubusercontent.com`.
- `aws_iam_role` trusting that provider with
  `sub = repo:jerridan/shardVPN:ref:refs/heads/master` — a branch-pinned
  condition, never `repo:jerridan/shardVPN:*`, which would let any ref in the
  repository assume it.
- Policy: `lambda:GetFunctionUrlConfig` on the function, `ssm:GetParameter` on
  `/shardvpn/signing-secret` and the two CI Tailscale parameters,
  `ec2:DescribeInstances` for the leak assertion, and `sns:Publish` on the
  existing topic so a failure lands in the same inbox as the idle alerts.

No `kms:Decrypt` statement — resolved 2026-08-03 for the Lambda role, and the
same `kms:ViaService` grant on `alias/aws/ssm` applies here.

**Say the residual risk plainly:** this role is a path to the signing secret for
anyone who can run a workflow on `master`. On a public repository, fork pull
requests get a restricted token and cannot assume it, and the branch-pinned
`sub` blocks other refs — so the exposure is to someone who can already push to
`master`, who could equally just read the parameter with your own credentials.
It does not widen the practical blast radius, but it does add a second door.

The alternative considered and rejected: grant CI `lambda:InvokeFunction` and
call the function directly with a synthesized event, avoiding the secret
entirely. Rejected because it skips the Function URL, the permission pair and
the HMAC path — four of the things most worth testing — in exchange for a
grant that is arguably larger.

## 4. Not leaking a node

Four independent layers, in order of how fast they act:

1. **`ttl: "30m"` on the launch.** If the runner is cancelled, the network
   drops, or the job dies between `up` and `down`, the existing hourly sweep
   reaps the node within the hour with no new machinery. This is the important
   one: it works when the workflow does not.
2. **`if: always()` cleanup step**, sending `down` even on failure.
3. **A `DescribeInstances` assertion** at the end, failing the job loudly if
   an exit-node instance is still there.
4. The existing 24h idle SNS email and the budget alarm, unchanged.

The `down` must assert that `status.instance_id` matches the ID this run
launched, and refuse to send `down` if it does not. Concurrency 1 plus the
pointer should make a mismatch impossible; if it happens anyway, terminating
something the test did not create is the wrong response.

A `concurrency` group on the workflow prevents a manual dispatch from
overlapping the scheduled run.

## 5. Layout

```
e2e/driver.py     # up / status / down over the Function URL, signed
e2e/README.md     # what this is, how to run it by hand
.github/workflows/e2e.yml
terraform/ci.tf   # OIDC provider + role, behind enable_ci_role
docs/e2e-setup.md # the one-time human setup
```

`e2e/`, not `tests/e2e/` — `uv run pytest` must stay offline and must not
collect anything that talks to AWS. The driver is stdlib-only, matching the
Lambda's constraint: `urllib` for the request, `hmac`/`hashlib` for the
signature, and the AWS CLI (preinstalled on the runner) for the SSM and Lambda
reads, so there is no boto3 dependency to install or pin.

Signing is the same construction as `auth.py` and the Scriptable client:
`HMAC-SHA256(secret, f"{ts}." + raw_body)`, hex, in `X-ShardVPN-Signature`,
with `X-ShardVPN-Timestamp` alongside. Sign the exact bytes sent — the
re-serialization trap in the gotchas list applies to this client too.

Running `e2e/driver.py` by hand from a laptop is a useful side effect: it is a
better debugging tool than curl, and it is the same code path CI exercises.

## 6. One-time human setup

Goes in `docs/e2e-setup.md`, in the style of `docs/tailnet-setup.md`.

**Tailnet.** Merge into the policy file:

```json
"tagOwners": {
    "tag:shardvpn-ci": ["autogroup:admin"],
},

"grants": [
    // The CI runner is a TAGGED device, and tagged devices are not
    // autogroup:member — the existing autogroup:internet grant does not
    // cover it. Without this the runner joins the tailnet, selects the exit
    // node without error, and routes nothing.
    {"src": ["tag:shardvpn-ci"], "dst": ["autogroup:internet"], "ip": ["*"]},
]
```

This depends on the stock `{"src": ["*"], "dst": ["*"], "ip": ["*"]}` grant
still being present for runner-to-node traffic. If that has been narrowed, the
CI node needs explicit reach to `tag:shardvpn-exit`.

Then a second OAuth client: scope `auth_keys` only, restricted to
`tag:shardvpn-ci`. Not `devices:core:read` — the driver asks the Lambda for
tailnet state, so CI never calls the devices API itself.

**AWS.** `terraform apply -var enable_ci_role=true`, then populate the two new
SecureStrings out of band exactly as `docs/tailnet-setup.md` step 3 does.

**GitHub.** One repository variable, `AWS_ROLE_ARN`. No secrets.

## 7. Cost

A `t4g.small` in `ca-central-1` for roughly 12 minutes is about half a cent.
Fifty-two runs is well under a dollar a year, plus negligible transfer. The
budget alarm stays at $10 and will not notice.

## 8. Known problems with this design

- **GitHub disables scheduled workflows after 60 days of repository
  inactivity.** This repo goes quiet between trips, so it will hit that, and a
  disabled test is worse than no test because it looks like a pass. Proposed
  fix: the workflow appends a one-line result to `docs/e2e-log.md` and commits
  it with `[skip ci]` (needs `contents: write` on that step alone), which
  doubles as a run history. Whether a `GITHUB_TOKEN`-authored commit resets the
  60-day clock is **not confirmed** — verify at build time; the fallback is
  that GitHub emails before disabling and re-enabling is one click.
- **A skipped run looks like a passing run.** If the VPN is up for three
  straight Sundays, nothing is tested and nothing says so. Mitigation: the
  skip path publishes to SNS too, so a skip is visible rather than silent.
- **One flaky Sunday is not a regression.** Transient Tailscale or EC2 blips
  will occasionally fail this. Treat a single red run as "look at it", a second
  consecutive one as "something changed". Deliberately not auto-retrying: a
  retry that passes hides the flake rate, which is itself the signal.
- **Only the default region is exercised.** `ensure_security_group`'s create
  path — the `InvalidPermission.Duplicate` gotcha — only runs in a region that
  has never launched a node, so the weekly run never covers it. Optional: a
  `workflow_dispatch` region input, used by hand before a trip somewhere new.
- **The `drift` job's `-upgrade` will eventually fail for reasons unrelated to
  this repo**, e.g. a provider yanked from the registry. Same triage as above.

## 9. Open questions to settle while building

- The current major tag of `tailscale/github-action` (repo constraint: pin
  every third-party action to a release tag, never `@master`). Verify against
  the tag list the way `astral-sh/setup-uv@v9.0.0` had to be.
- Whether `tailscale set --exit-node=` takes the bare hostname or wants the
  MagicDNS name / tailnet IP. The status document gives the hostname; if that
  is not accepted, the driver needs to return the tailnet IP instead, which
  means the Lambda would have to surface it.
- Whether a bot commit resets the 60-day scheduled-workflow clock (§8).

## 10. Tasks

- [ ] **1.** `e2e/driver.py`: `up` / `status --wait-online` / `down --expect <id>`,
      stdlib only, signing identical to `auth.py`. Unit-test the signing helper
      against the same vectors `tests/test_auth.py` uses.
- [ ] **2.** `terraform/ci.tf`: OIDC provider + branch-pinned role + scoped
      policy, behind `enable_ci_role` defaulting to `false`. Two new SecureString
      parameters. Run `trivy config terraform/` — expect findings on the new IAM
      and justify or suppress them in `.trivyignore` (bare ID lines, comments
      above, per the gotcha).
- [ ] **3.** `.github/workflows/e2e.yml`: `drift` job.
- [ ] **4.** `.github/workflows/e2e.yml`: `live` job, guard first, cleanup
      `if: always()`, leak assertion last, SNS on failure and on skip.
- [ ] **5.** `docs/e2e-setup.md`, and a pointer to it from `README.md`.
- [ ] **6.** Update `CLAUDE.md`: the "no AWS credentials in CI" constraint is
      now qualified, and `tag:shardvpn-ci` joins the tailnet contract.
- [ ] **7.** First run by `workflow_dispatch`, watched end to end. Confirm the
      egress IP assertion actually flips — a test that would pass without the
      exit node set is worthless, so verify it fails when it should by
      running it once with the exit-node step disabled.

Task 7 is the one that matters. Everything before it is untested by
construction: this workflow cannot be verified by the offline suite, and its
first real run is its only proof.
