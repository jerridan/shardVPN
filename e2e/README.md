# e2e

`driver.py` is a signed client for the shardVPN Function URL — the same three
actions the phone sends, from a terminal. `.github/workflows/e2e.yml` drives it
weekly; you can also run it by hand, and it is a better debugging tool than
curl because it signs correctly and explains the terse statuses the handler
returns by design.

Stdlib only, like the Lambda. No `uv sync`, no install.

Not under `tests/` on purpose: `uv run pytest` must stay hermetic, and anything
it collects that can reach AWS breaks that. `tests/test_e2e_driver.py` covers
the signing offline, by pinning it against the handler's own `verify()`.

## Use

```bash
export SHARDVPN_URL="$(cd terraform && terraform output -raw function_url)"
export SHARDVPN_SECRET="$(aws ssm get-parameter --name /shardvpn/signing-secret \
  --with-decryption --query Parameter.Value --output text)"

python3 e2e/driver.py status
python3 e2e/driver.py up --region ca-central-1 --ttl 2h
python3 e2e/driver.py status --wait-online
python3 e2e/driver.py down --expect i-0123456789abcdef0
```

JSON goes to stdout, progress to stderr, so `| jq` works.

Exit codes: `0` success, `1` failure, `2` refused — a guard tripped and nothing
was sent.

## Two flags worth knowing

`--ttl` defaults to `30m` rather than the system default of `none`. If whatever
is driving the driver dies between `up` and `down`, the hourly sweep reaps the
node. A launch with no expiry is one cancelled job away from billing forever.

`--expect` makes `down` verify the pointer names the instance you think it
does, and refuse otherwise. `down` terminates whatever it finds in the
pointed-at region, and the pointer is global — this is what stops an automated
teardown from disconnecting someone mid-call.
