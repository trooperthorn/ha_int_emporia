# Operations

## Running the test suite locally

`pytest-homeassistant-custom-component` is extracted from a specific core
release, sometimes a beta, and pulls that exact core version in as a
dependency. Installing it in the same resolver pass as
`requirements_test.txt` can produce a version conflict, because
`requirements_test.txt` pins the stable `homeassistant` release. Install in
two steps so the harness installs first and the stable pin re-resolves it
afterward:

```bash
pip install pytest-homeassistant-custom-component==0.13.362
pip install -r requirements_test.txt
python -m pytest tests/ -q
```

## Windows development note

`pytest-homeassistant-custom-component` imports `fcntl`, which is
Unix-only. Run the test suite from WSL (or another Linux/macOS
environment) on a Windows development machine; it cannot run under the
native Windows Python.

A ready venv exists at `~/emporiavenv` inside WSL, pinned to the versions CI
asserts (Python 3.14, `homeassistant` 2026.9.0,
`pytest-homeassistant-custom-component` 0.13.362):

```bash
wsl -e bash -lc 'cd /mnt/c/Users/sean.LAB/repos/ha_int_emporia && ~/emporiavenv/bin/python -m pytest tests/ -q'
```

## Probing the Emporia cloud API

`scripts/api_probe.py` authenticates as you and issues a read-only GET against
every endpoint catalogued in [api-reference.md](api-reference.md), writing a
JSON capture and a human-readable log. Use it to turn a **B-apk** row in that
document into a verified one.

It issues GET requests only; there is no write path in it. Your password is
read with `getpass`, never passed on the command line, and never written to
either output file. Personal fields are redacted from the capture by default —
device gids and serials are not, so scrub a capture before sharing it.

It runs on Windows natively; unlike the test suite it has no `fcntl`
dependency. It needs `pyemvue`, which it uses purely for the Cognito login.

```bash
pip install pyemvue
python scripts/api_probe.py --email you@example.com --token-file ~/.emporia-probe.json
```

To work out which cloud feature owns the EV charging rate, capture, change one
setting in the Emporia app, capture again, and diff:

```bash
python scripts/api_probe.py --token-file ~/.emporia-probe.json --label before
# ... change one thing in the app ...
python scripts/api_probe.py --token-file ~/.emporia-probe.json --label after
python scripts/api_probe.py --diff emporia-probe-*-before.json emporia-probe-*-after.json
```

Captures are gitignored, but keep them out of the working tree anyway.

Redaction blanks personal *keys* and also scrubs email addresses and IPv4
addresses out of *values*, because Emporia's legacy 4xx bodies echo the
authenticated identity back (`{identity={sourceIp=..., email=...}}`). A capture
taken before that existed can be cleaned in place:

```bash
python scripts/api_probe.py --rescrub emporia-probe-<stamp>.json
```

That rewrites the JSON only. Delete the matching `.log` and re-run to
regenerate it.

### Sending a charger command

`scripts/api_probe.py --send-command` is the one part of the probe that writes.
It POSTs a single command to `/v1/customers/evse/control` and shows the legacy
`loads[]` array before and after, which is how you tell whether a command
opened an energy-management override.

```bash
python scripts/api_probe.py --token-file ~/.emporia-probe.json \
    --send-command CHARGE_AT_FULL_POWER
```

It prints the exact request, warns when the command value has never been
observed on the wire, and requires you to type `yes`. `--yes` skips the prompt;
`--force-command` allows a value outside the known list.

`TURN_ON` and `TURN_OFF` are captured from the official web app. The rest are
enum-shaped strings taken from the app binary and are unverified until someone
sends one — see [api-reference.md](api-reference.md#still-uncaptured).

**This changes your EV charger.** Nothing else in the script does.

### Mapping the command enum

`--discover-commands` works out which values `ChargerControlCommand` accepts
**without executing any of them**:

```bash
python scripts/api_probe.py --token-file ~/.emporia-probe.json --discover-commands
```

The control endpoint deserializes the request body before validating it, so a
body carrying a `command` but **no `device_id`** fails either at JSON parse
(the value is not in the enum) or later on the missing device (it is). Neither
path reaches a charger.

The run verifies that oracle before trusting it — a known-good value and a
nonsense value must fail in visibly different ways — and aborts if they do not.
