# Phone setup

shardVPN is controlled from an iPhone via [Scriptable](https://scriptable.app)
(free, App Store) running a small script that signs requests and a thin
Shortcuts wrapper for each action, pinned to the home screen. Shortcuts alone
can't do this — HMAC signing needs actual code, and Shortcuts has no HMAC
action.

## 1. Install Scriptable and add the script

1. Install Scriptable from the App Store.
2. Open it, tap **+** to create a new script.
3. Name it exactly `shardVPN`. The name matters — it's what the Shortcuts
   wrapper in step 3 refers to.
4. Delete the placeholder contents and paste in the entire contents of
   `docs/ios/shardvpn.js` from this repo.
5. Save.

The script is entirely self-contained: it vendors its own HMAC-SHA256 and
UTF-8 encoder rather than relying on `crypto` or `TextEncoder`, because
neither is confirmed to exist in Scriptable's JavaScriptCore environment
(see `CLAUDE.md`'s Open section). No network calls happen at load time.

## 2. First run: populate the keychain

Run the script directly from Scriptable (tap the script, or the play button)
once. With nothing stored yet, it prompts twice:

1. **Lambda Function URL** — the `function_url` Terraform output
   (`terraform output function_url`, or `terraform output -raw function_url`
   to get it without the surrounding quotes).
2. **Signing secret** — the value you put in `/shardvpn/signing-secret` in
   `docs/tailnet-setup.md` step 5.

Both are stored in the iOS keychain (`Keychain.set`, keys `shardvpn.url` and
`shardvpn.secret`) and are not asked for again — later runs read them
straight from the keychain. If you ever need to change either value, delete
and reinstall the script, or clear those two keychain entries some other
way; the script has no built-in "reconfigure" prompt.

**Getting them onto the phone.** Don't retype either — both are long and a
single wrong character produces an opaque `403`. Pipe each to `pbcopy` on the
Mac and paste via Universal Clipboard, or store them in a password manager
and paste from there on the phone. The password manager is worth it
regardless: the signing secret then exists in SSM, the phone keychain, and
one recoverable place, rather than only the first two. Losing the phone
otherwise means minting a new secret.

**The Function URL is stable.** Its id is fixed when the URL config is
created and survives every code deploy — verified by observing the config's
`LastModifiedTime` stay put across a function update. It changes only if the
URL config is destroyed and recreated (`terraform destroy` then re-apply, or
deleting the function), in which case the phone needs the new value.

With no parameter, running the script directly shows an action sheet —
`status`, `up`, `down` — and a result notification. That's enough to test the
whole path before wiring up Shortcuts.

## 3. Add three Shortcuts

In the Shortcuts app, create three shortcuts, one per action:

1. Add action **Scriptable → Run Script**.
2. Script: `shardVPN`.
3. Set the script's parameter (the "Text" input on the Run Script action) to
   `up`, `down`, or `status` depending on which shortcut this is — the
   script reads it as `args.shortcutParameter` and skips the action-sheet
   prompt when it's present.
4. Name the shortcut something recognizable — `VPN Up`, `VPN Down`,
   `VPN Status` works fine.

## 4. Add each to the home screen

For each shortcut: **Share** → **Add to Home Screen**. Pick an icon and name
if you want one that reads clearly on the lock screen / home screen at a
glance (a launched, still-running node is a $0.52/day node — status should
be one tap away, not buried in the Shortcuts app).

## What to expect

- A result notification appears after each run: the node's `state`,
  `region`, and `tailnet` status on success, or `error: ...` on failure.
  `console.log` in Scriptable's own log shows the full JSON response if you
  need more than the notification gives you.
- **A `429` means a concurrent watchdog sweep, not a failure.** The Lambda's
  reserved concurrency is 1, so the hourly sweep and a phone request can
  collide for the few seconds the sweep takes to run. The script retries
  automatically with backoff (up to 4 attempts) before surfacing an error —
  you shouldn't normally see this at all, and if you do, it resolves itself
  within seconds without you doing anything.
- `5xx` responses are retried the same way; only a `4xx` other than `429`
  (bad request, unknown region, malformed TTL) is surfaced immediately, since
  retrying it would just fail again.

## Security notes

- **The signing secret lives only in the iOS keychain.** It is never
  visible in a shortcut's configuration, never in Scriptable's console log,
  and never sent anywhere except as the HMAC signature over each request —
  the secret itself never leaves the phone. Whether Scriptable's keychain
  entries sync via iCloud Keychain is not confirmed one way or the other;
  treat every device that shares iCloud Keychain with this phone as a device
  that can control the VPN.
- **The Function URL must not be shared.** It's unguessable (a random
  subdomain under `lambda-url.<region>.on.aws`) but not secret, and it can't
  be rotated independently of the whole function — anyone who has it can
  send it signed-looking requests all day; the HMAC check is what actually
  keeps them out, not the URL's obscurity. Don't paste it into a screenshot,
  a commit, or a chat log. It lives in exactly one place: this script's
  keychain entry.
