# Tailnet setup

Done once, by hand, before the SSM parameters are populated. shardVPN's
Lambda never touches your tailnet's admin console — it authenticates as a
scoped OAuth client and does everything else through `tailscale up`/`set`/
`logout` running on the node itself. This page sets up the tailnet side of
that contract.

## 1. Tag the exit node

Every node shardVPN launches is tagged `tag:shardvpn-exit`. It's what the
OAuth client below is restricted to, what `autoApprovers` keys off, and what
the Lambda searches for when `status` looks up device state. Add it to your
tailnet's policy file (admin console → Access Controls) with an owner:

```json
{
  "tagOwners": {
    "tag:shardvpn-exit": ["autogroup:admin"]
  }
}
```

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
step 5, and nowhere else.

## 3. Auto-approve the exit node

Exit-node advertisement normally waits for a click in the admin console.
Nobody is at a console mid-trip, so auto-approve it for the tag instead —
add to the policy file:

```json
{
  "autoApprovers": {
    "exitNode": ["tag:shardvpn-exit"]
  }
}
```

Without this, `tailscale set --advertise-exit-node` on the node succeeds but
the advertisement sits pending, `userdata.sh`'s verification loop times out,
and the node fails closed (logs out and shuts itself down) rather than
sitting there billing while silently unusable.

## 4. Grant SSH to the tagged node

The node's security group has zero ingress rules — no port 22, nothing
listening. The only way in is `tailscale ssh`, and `tailscale up --ssh`
(which `userdata.sh` runs on every launch) grants nothing on its own without
a matching `ssh` stanza in the policy file:

```json
{
  "ssh": [
    {
      "action": "accept",
      "src":    ["autogroup:admin"],
      "dst":    ["tag:shardvpn-exit"],
      "users":  ["autogroup:nonroot", "root"]
    }
  ]
}
```

This is also the only diagnostic path if cloud-init fails partway through:
`tailscale ssh <node-name>`, then `cat /var/log/shardvpn-init.log`.

## 5. Store the credentials in SSM

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
