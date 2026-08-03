# Tailnet setup

Done once, by hand, before the SSM parameters are populated. shardVPN's
Lambda never touches your tailnet's admin console — it authenticates as a
scoped OAuth client and does everything else through `tailscale up`/`set`/
`logout` running on the node itself. This page sets up the tailnet side of
that contract.

## 1. Policy file

Everything on the tailnet side is one edit to the policy file, at
[login.tailscale.com/admin/acls/file](https://login.tailscale.com/admin/acls/file).

**Merge these sections into what is already there — do not replace the file.**
Tailscale is deny-by-default: drop the existing `grants` (or `acls`, on older
tailnets) and every device stops reaching every other one, including the exit
node you are about to build.

Four things are needed. Against Tailscale's current default policy the result
looks like this — the `grants` wildcard and the first `ssh` rule are stock,
the four commented additions are shardVPN's:

```json
{
    "tagOwners": {
        // Every node shardVPN launches carries this tag. The OAuth client is
        // restricted to it, autoApprovers keys off it, and `status` finds the
        // node by it.
        "tag:shardvpn-exit": ["autogroup:admin"],
    },

    // Exit-node advertisement normally waits for a click in the admin console.
    // Nobody is at a console mid-trip, so auto-approve it for the tag. Without
    // this, `tailscale set --advertise-exit-node` succeeds but sits pending,
    // cloud-init's verification loop times out, and the node fails closed —
    // logs out and terminates itself rather than billing while unusable.
    "autoApprovers": {
        "exitNode": ["tag:shardvpn-exit"],
    },

    "grants": [
        {"src": ["*"], "dst": ["*"], "ip": ["*"]},

        // REQUIRED, and the wildcard above does NOT cover it. Device-to-device
        // traffic and internet egress through an exit node are separate
        // permissions; only devices granted `autogroup:internet` can use an
        // exit node at all. Omit this and the node boots, joins, advertises,
        // and appears selectable in the picker while routing nothing — a
        // failure cloud-init cannot detect, because the node is genuinely
        // healthy and the block is here in the policy file.
        {"src": ["autogroup:member"], "dst": ["autogroup:internet"], "ip": ["*"]},
    ],

    "ssh": [
        {
            "action": "check",
            "src":    ["autogroup:member"],
            "dst":    ["autogroup:self"],
            "users":  ["autogroup:nonroot", "root"],
        },

        // The node has zero inbound security-group rules, so `tailscale ssh`
        // is the only way in — and the only diagnostic path if cloud-init
        // fails partway. `accept` rather than `check` deliberately: a browser
        // re-auth prompt is useless when debugging from an airport.
        //
        // `autogroup:member` rather than `autogroup:admin`: Tailscale has
        // rejected `autogroup:admin` in `ssh` src (tailscale/tailscale#8194),
        // and the current syntax reference disagrees with the SSH KB page
        // about whether that still holds. `autogroup:member` is unambiguous —
        // it appears in this position in Tailscale's own default file — and on
        // a single-person tailnet the two mean the same thing.
        {
            "action": "accept",
            "src":    ["autogroup:member"],
            "dst":    ["tag:shardvpn-exit"],
            "users":  ["autogroup:nonroot", "root"],
        },
    ],
}
```

The file is HuJSON, so comments and trailing commas are legal. The editor
validates before saving; a rejected policy is never applied.

Older tailnets use `acls` instead of `grants`. Both work — add the
`autogroup:internet` permission in whichever form the file already uses,
rather than mixing the two.

## 2. Create a scoped OAuth client

Admin console → Settings → OAuth clients → Generate OAuth client.

- Scopes: **`auth_keys`** (write) and **`devices:core:read`**.
- Restrict the client to `tag:shardvpn-exit`.

Note what's deliberately *not* granted: read-write `devices:core`. Tag
restrictions genuinely constrain key-minting (`auth_keys`), but tag-scoping
does not apply to Tailscale's device-operations API — a read-write
`devices:core` grant would be tailnet-wide, letting a leaked OAuth secret
remove or retag *any* device, not just shardVPN's own. `devices:core:read`
carries no such risk, and it's all `status` needs to tell "the node is on the
tailnet" from "the node booted but never joined." Instead, the node removes
itself: `userdata.sh` installs a shutdown-ordered systemd unit that runs
`tailscale logout` when the instance terminates, with Tailscale's own
ephemeral-node expiry (30–60 minutes after last activity) as a backstop if
that unit doesn't get to run.

Save the client ID and secret somewhere temporary — they go into SSM in
step 3, and nowhere else.

## 3. Store the credentials in SSM

Terraform creates three `SecureString` parameters with placeholder values
and never writes to them again — `aws_ssm_parameter` stores its value in
Terraform state in plaintext even for `SecureString`, so the real secrets
are populated out of band instead, after `terraform apply`:

```bash
aws ssm put-parameter --name /shardvpn/tailscale-client-id \
  --type SecureString --value '<id>' --overwrite
aws ssm put-parameter --name /shardvpn/tailscale-client-secret \
  --type SecureString --value '<secret>' --overwrite
aws ssm put-parameter --name /shardvpn/signing-secret --type SecureString \
  --value "$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" --overwrite
```

The signing secret isn't a Tailscale credential — it's the HMAC key the
phone client and the Lambda share to authenticate Function URL requests (see
`docs/ios-shortcut.md`). It's generated here because this is the point in
setup where all three `SecureString` values get populated together; nothing
about it is tailnet-specific.

Once all three are set, the tailnet side of setup is done. The signing
secret also needs to reach the phone — that's `docs/ios-shortcut.md`.
