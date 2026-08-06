# Weekly end-to-end test — setup

`.github/workflows/e2e.yml` launches a real node every Sunday, pushes real
traffic through it from the runner, and tears it down. This page is the
one-time human setup it depends on. Until you do it, the workflow fails on its
first step with a missing `AWS_ROLE_ARN`; nothing here happens by accident.

Everything in `terraform/ci.tf` is behind `enable_ci_role`, which defaults to
`false`. Pulling this repository and running `terraform apply` does not grant
GitHub anything.

## Why this exists

`ci.yml` runs offline and proves the code is internally consistent. It cannot
fail for the reason this system will actually fail — the outside world moving
while the repository stands still. Every hard bug in this project so far came
from there: a package missing from the base AMI, a systemd hook that is an
Ubuntu-ism, a duplicate egress rule, a policy-file grant that the wildcard
above it did not cover. A test with no AWS account and no tailnet sees none of
them.

## What it costs

A `t4g.small` for about twelve minutes, once a week. Well under a dollar a
year. The budget alarm will not notice.

## 1. Tailnet

Merge into the policy file at
[login.tailscale.com/admin/acls/file](https://login.tailscale.com/admin/acls/file).
As in `docs/tailnet-setup.md`: **merge, do not replace.**

```json
{
    "tagOwners": {
        "tag:shardvpn-exit": ["autogroup:admin"],
        // The GitHub runner. Separate from the exit-node tag so the CI
        // credential cannot mint keys for real exit nodes.
        "tag:shardvpn-ci": ["autogroup:admin"],
    },

    "grants": [
        {"src": ["*"], "dst": ["*"], "ip": ["*"]},
        {"src": ["autogroup:member"], "dst": ["autogroup:internet"], "ip": ["*"]},

        // REQUIRED, and the line above does NOT cover it. The runner is a
        // TAGGED device, and tagged devices are not autogroup:member — so
        // without its own grant it joins the tailnet, selects the exit node
        // without error, and routes nothing. That failure is precisely what
        // the test is built to detect, so on the first run it is
        // indistinguishable from a real regression. Add this before you turn
        // the workflow on.
        {"src": ["tag:shardvpn-ci"], "dst": ["autogroup:internet"], "ip": ["*"]},
    ],
}
```

The runner reaching the exit node itself relies on the stock
`{"src": ["*"], "dst": ["*"], "ip": ["*"]}` grant. If you have narrowed it,
`tag:shardvpn-ci` also needs explicit reach to `tag:shardvpn-exit`.

Then create a **second** OAuth client (Settings → OAuth clients):

- Scope: **`auth_keys`** only.
- Restricted to `tag:shardvpn-ci`.

Not `devices:core:read` — the workflow asks the Lambda for tailnet state and
never calls the devices API itself. Keeping this client separate from the
Lambda's means a leak from a GitHub runner cannot mint a key for
`tag:shardvpn-exit`, and either can be rotated without touching the other.

## 2. AWS

```bash
cd terraform
terraform apply -var enable_ci_role=true
```

If the apply fails with `EntityAlreadyExists` on the OIDC provider, this
account already has GitHub's registered for something else. An account may only
hold one per URL, so reuse it:

```bash
aws iam list-open-id-connect-providers   # find the token.actions... one
terraform apply -var enable_ci_role=true \
  -var github_oidc_provider_arn='arn:aws:iam::<account>:oidc-provider/token.actions.githubusercontent.com'
```

Then populate the two new SecureStrings out of band, exactly as in
`docs/tailnet-setup.md` step 3 — Terraform writes placeholders and never the
real values, because `aws_ssm_parameter` stores them in state in plaintext even
for `SecureString`:

```bash
aws ssm put-parameter --name /shardvpn/ci-tailscale-client-id \
  --type SecureString --value '<ci client id>' --overwrite
aws ssm put-parameter --name /shardvpn/ci-tailscale-client-secret \
  --type SecureString --value '<ci client secret>' --overwrite
```

## 3. GitHub

One repository **variable** — Settings → Secrets and variables → Actions →
Variables. Not a secret; a role ARN is not sensitive.

```
AWS_ROLE_ARN = <the ci_role_arn terraform output>
```

Optionally `AWS_REGION` if your control region is not `ca-central-1`.

**No repository secrets.** The signing secret, the function URL and the CI
Tailscale client are all read from AWS at runtime with the OIDC session. There
is deliberately no second copy of any of them in GitHub.

## 4. First run

Run it by hand — Actions → e2e → Run workflow — and watch it. It takes about
twelve minutes.

Then do the step that actually proves the test works: **check that it fails
when it should.** Comment out the `sudo tailscale set --exit-node=` line, run
it again, and confirm the job goes red at the routing assertion. A test that
passes whether or not traffic goes through the node is worse than no test,
because it is trusted. Put the line back afterwards.

The scheduled run also commits a line to `docs/e2e-log.md`; manual runs do not,
so this first run leaves no entry.

## What each piece is protecting against

| Step | Fails when |
|---|---|
| Guard (`status` first) | never — it exists so the test cannot tear down a VPN you are using |
| `up` | the Tailscale OAuth or key-minting API changed; IAM drifted |
| Wait for online | the AMI, the Tailscale dnf repo, or cloud-init broke |
| Routing assertion | the policy file lost the exit-node grant; GRO/routing broke |
| `down` + leak check | terminate is failing, and something is billing |
| `drift` job | the AWS provider shipped a breaking change inside `~> 6.57` |

## Turning it off

```bash
terraform apply -var enable_ci_role=false
```

That deletes the role, so the workflow can no longer reach the account at all.
Disable the workflow in the Actions tab as well, or it will keep failing at the
assume-role step every Sunday.

## Known limits

- **A skipped run looks like a pass.** If a node is up when the schedule fires,
  the test skips. It publishes to SNS so the skip is visible, but a long trip
  means several weeks with no real signal.
- **Only one region is exercised.** `ensure_security_group`'s create path only
  runs in a region that has never launched a node, so the weekly run never
  covers it. Use the `region` input by hand before a trip somewhere new.
- **The CI role can read the signing secret.** Anyone able to run a workflow on
  `master` can therefore read it. The trust policy pins the repository, branch
  and audience, and fork pull requests cannot assume the role — so this is
  exposure to someone who can already push to `master`, who could equally read
  the parameter directly. It does add a second door to the same room.
- **One flaky Sunday is not a regression.** There is no automatic retry, on
  purpose: a retry that passes hides the flake rate, which is itself worth
  knowing. Treat one red run as "look", two consecutive as "something changed".
