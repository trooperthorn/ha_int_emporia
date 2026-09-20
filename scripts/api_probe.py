#!/usr/bin/env python3
"""Probe of the Emporia cloud API.

Logs in as you, issues a GET against every endpoint listed in
docs/api-reference.md, and writes a JSON capture plus a human-readable log so
the undocumented parts of the surface can be pinned down with real responses.

**The default run is read-only.** Every probe is a GET. The only way to make
this script write anything is `--send-command`, which is opt-in, names the
single endpoint it will POST to, prints the exact body, and requires a typed
confirmation before sending. See "Sending a command" below.

Your password is read with getpass, is never passed on the command line, and
is never written to either output file. Tokens are redacted from the capture.

Usage
-----
    pip install pyemvue

    # email/password account
    python scripts/api_probe.py --email you@example.com

    # Google/Apple SSO account: paste the three tokens instead
    python scripts/api_probe.py --tokens

    # reuse a saved session so you only authenticate once
    python scripts/api_probe.py --email you@example.com --token-file ~/.emporia-probe.json
    python scripts/api_probe.py --token-file ~/.emporia-probe.json

    # compare two captures (see "The Excess Solar experiment" below)
    python scripts/api_probe.py --diff before.json after.json

The Excess Solar experiment
---------------------------
To establish which field reports the controller that owns the charging rate,
and what an override looks like on the wire:

    1. python scripts/api_probe.py --email you@... --label before-override
    2. In the Emporia app, open the charger, tap "Manage Charging" and choose
       "Charge at full power".
    3. python scripts/api_probe.py --token-file ... --label after-override
    4. python scripts/api_probe.py --diff <before>.json <after>.json

Repeat with Excess Solar toggled off for that energy monitor, and with a
schedule enabled, to separate the controllers.

Sending a command
-----------------
`POST /v1/customers/evse/control` takes `{"device_id": ..., "command": ...}`.
`ChargerControlCommand` has exactly three members — `TURN_ON`, `TURN_OFF` and
`CHARGE_AT_FULL_POWER` — established with `--discover-commands`, which maps the
enum without executing anything. There is no command that releases an
energy-management override.

    python scripts/api_probe.py --token-file ~/.emporia-probe.json \
        --send-command CHARGE_AT_FULL_POWER

This reads `loads[]` before, prints the request, asks you to type "yes", sends
it, then re-reads `loads[]` and shows what changed — which is how you tell
whether a command opened an energy-management override. It changes your
charger. Nothing else in this script does.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import difflib
import getpass
import glob as globlib
import json
import os
import re
import sys
import time
from typing import Any

try:
    import requests
    from pyemvue import PyEmVue
except ImportError:  # pragma: no cover - guidance for a bare environment
    sys.exit("Missing dependency. Run:  pip install pyemvue")

LEGACY_ORIGIN = "https://api.emporiaenergy.com"
V1_ORIGIN = "https://c-api.emporiaenergy.com"
MAINTENANCE_URL = (
    "https://s3.amazonaws.com/com.emporiaenergy.manual.ota/maintenance/maintenance.json"
)
USER_AGENT = "ha_int_emporia-api-probe/1"
TIMEOUT = 20

# Keys whose values are redacted unless --no-redact is passed. Device gids and
# serials are kept: the capture is for the account owner, and diffing needs
# stable identifiers. Scrub the file yourself before sharing it publicly.
#
# Applied only inside response bodies, never to the probe's own record fields —
# an earlier version matched the probe's `name` key and made the log unreadable.
REDACT_KEY = re.compile(
    r"(token|password|secret|authorization|breakerpin|breaker_pin|^pin$"
    r"|email|firstname|lastname|first_name|last_name"
    r"|display_name|displayname|device_name|devicename"
    r"|street|address|city|zip|postal|latitude|longitude|phone)",
    re.IGNORECASE,
)

# Emporia echoes the authenticated identity back inside some 4xx error strings
# (`{identity={sourceIp=..., email=...}}`), so key-based redaction alone leaks
# both. These scrub the values wherever they appear in any string.
REDACT_VALUE = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<email>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
]

# (label, origin, path, needs) — `needs` names the parameter set this probe
# requires; the probe is skipped with a reason when its inputs are unavailable.
# Required-parameter names below are quoted from the live 400 responses.
#
#   None          -> no parameters
#   "devices"     -> device_ids = every real device serial, + time window
#   "device_gids" -> device_gids = every numeric gid, + time window
#   "evse"        -> device_id  = the EV charger serial (singular), + window
#   "evses"       -> device_ids = EV charger serials, + window
#   "evse_only"   -> device_id  = the EV charger serial, no window
#   "monitor"     -> device_id  = each energy monitor serial, one request each
#   "gid"         -> each Emporia device gid substituted into the path
#   "load"        -> loadGid    = each controllable load, one request each
#   "gids_q"      -> deviceGids = comma-joined numeric gids (legacy spelling)
#   "tz"          -> timezone   = the account timezone
PROBES: list[tuple[str, str, str, str | None]] = [
    # --- legacy: what PyEmVue implements, plus the paths it does not ---
    ("legacy customer", LEGACY_ORIGIN, "/customers", None),
    ("legacy devices", LEGACY_ORIGIN, "/customers/devices", None),
    ("legacy device status", LEGACY_ORIGIN, "/customers/devices/status", None),
    ("legacy channel types", LEGACY_ORIGIN, "/devices/channels/channeltypes", None),
    ("legacy device channels", LEGACY_ORIGIN, "/customers/devices/channels", None),
    ("legacy vehicles", LEGACY_ORIGIN, "/customers/vehicles", None),
    ("legacy location properties", LEGACY_ORIGIN, "/devices/{gid}/locationProperties", "gid"),
    ("legacy time of use", LEGACY_ORIGIN, "/customers/timeofuse", None),
    ("legacy load management", LEGACY_ORIGIN, "/customers/loadmanagement", "load"),
    ("legacy derms", LEGACY_ORIGIN, "/customers/derms", None),
    ("legacy device schedule", LEGACY_ORIGIN, "/devices/schedule", "load"),
    ("legacy evcharger max rate", LEGACY_ORIGIN, "/devices/evcharger/maxchargingrate", None),
    ("legacy firmware up to date", LEGACY_ORIGIN, "/devices/firmwareuptodate", "gids_q"),
    # --- v1: account, sites, devices ---
    ("v1 customer", V1_ORIGIN, "/v1/customers", None),
    ("v1 sites", V1_ORIGIN, "/v1/customers/sites", None),
    ("v1 site members", V1_ORIGIN, "/v1/customers/sites/members", None),
    ("v1 devices", V1_ORIGIN, "/v1/customers/devices", None),
    ("v1 device status", V1_ORIGIN, "/v1/customers/devices/status", None),
    ("v1 device channels", V1_ORIGIN, "/v1/customers/devices/channels", None),
    ("v1 device settings", V1_ORIGIN, "/v1/customers/devices/settings", None),
    ("v1 third party access", V1_ORIGIN, "/v1/customers/devices/third-party-access", "evse_only"),
    ("v1 app preferences", V1_ORIGIN, "/v1/customers/app-preferences", None),
    ("v1 homepage summary", V1_ORIGIN, "/v1/customers/homepage/summary", "devices"),
    ("v1 homepage monitor card", V1_ORIGIN, "/v1/customers/homepage/monitor-card", "devices"),
    ("v1 savings", V1_ORIGIN, "/v1/customers/devices/savings", "tz"),
    # --- v1: batteries only, despite the generic path ---
    ("v1 device override", V1_ORIGIN, "/v1/customers/devices/override", "evse_only"),
    # --- v1: energy management controllers ---
    ("v1 excess generation", V1_ORIGIN, "/v1/customers/excess-generation", None),
    ("v1 excess generation monitor", V1_ORIGIN, "/v1/customers/energy-monitor/excess-generation", "monitor"),
    ("v1 power smart", V1_ORIGIN, "/v1/customers/power-smart", None),
    ("v1 power smart monitor", V1_ORIGIN, "/v1/customers/energy-monitor/power-smart", "monitor"),
    ("v1 peak demand", V1_ORIGIN, "/v1/customers/peak-demand", None),
    ("v1 peak demand monitor", V1_ORIGIN, "/v1/customers/energy-monitor/peak-demand", "monitor"),
    ("v1 load sharing", V1_ORIGIN, "/v1/customers/load-sharing", None),
    ("v1 load sharings", V1_ORIGIN, "/v1/customers/load-sharings", None),
    ("v1 derms", V1_ORIGIN, "/v1/customers/derms", None),
    ("v1 derms devices", V1_ORIGIN, "/v1/derms/devices", "devices"),
    # --- v1: EV charging ---
    ("v1 evses", V1_ORIGIN, "/v1/devices/evses", "evses"),
    ("v1 evse sessions", V1_ORIGIN, "/v1/devices/evses/sessions", "evses"),
    ("v1 evse charging history", V1_ORIGIN, "/v1/customers/evse/charging-history", "evse"),
    ("v1 ev charging report", V1_ORIGIN, "/v1/customers/ev-charging-report", "evse"),
    ("v1 vehicle brands", V1_ORIGIN, "/v1/vehicles/brands", None),
    # --- v1: rates and misc ---
    ("v1 utility rates", V1_ORIGIN, "/v1/utility-rates", None),
    ("v1 device utility rates", V1_ORIGIN, "/v1/devices/utility-rates", "evse_only"),
    ("v1 rate analysis", V1_ORIGIN, "/v1/customers/rate-analysis", None),
    ("v1 recommendations", V1_ORIGIN, "/v1/customers/recommendations", None),
    ("v1 device usages", V1_ORIGIN, "/v1/customers/devices/usages", "device_gids"),
]

# Probes that issue one request per discovered value rather than one in total.
PER_VALUE_NEEDS = {"gid", "monitor", "load"}


def scrub_text(value: str) -> str:
    """Strip identity values that appear inside free-text strings."""
    for pattern, replacement in REDACT_VALUE:
        value = pattern.sub(replacement, value)
    return value


def redact(value: Any, enabled: bool) -> Any:
    """Recursively blank out credential and personal fields."""
    if not enabled:
        return value
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if REDACT_KEY.search(str(key)) and item not in (None, "", [], {}):
                out[key] = "<redacted>"
            else:
                out[key] = redact(item, enabled)
        return out
    if isinstance(value, list):
        return [redact(item, enabled) for item in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


def redact_record(record: dict[str, Any], enabled: bool) -> dict[str, Any]:
    """Redact a probe result, leaving the probe's own bookkeeping fields alone."""
    out = dict(record)
    if "body" in out:
        out["body"] = redact(out["body"], enabled)
    for key in ("body_text", "error"):
        if isinstance(out.get(key), str):
            out[key] = scrub_text(out[key])
    return out


def field_paths(value: Any, prefix: str = "") -> set[str]:
    """Flatten a response into dotted field paths, collapsing list indices."""
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{prefix}.{key}" if prefix else str(key)
            paths.add(here)
            paths |= field_paths(item, here)
    elif isinstance(value, list) and value:
        paths |= field_paths(value[0], f"{prefix}[]")
    return paths


def authenticate(args: argparse.Namespace) -> PyEmVue:
    vue = PyEmVue()
    token_file = os.path.expanduser(args.token_file) if args.token_file else None

    if token_file and os.path.exists(token_file):
        print(f"Reusing saved session from {token_file}")
        if vue.login(token_storage_file=token_file):
            return vue
        print("Saved session was rejected; falling back to interactive login.")

    if args.tokens:
        print("Paste the three tokens from web.emporiaenergy.com (see README).")
        id_token = getpass.getpass("id_token: ").strip()
        access_token = getpass.getpass("access_token: ").strip()
        refresh_token = getpass.getpass("refresh_token: ").strip()
        ok = vue.login(
            id_token=id_token,
            access_token=access_token,
            refresh_token=refresh_token,
            token_storage_file=token_file,
        )
    else:
        email = args.email or input("Emporia email: ").strip()
        password = getpass.getpass("Emporia password (not echoed, not logged): ")
        ok = vue.login(username=email, password=password, token_storage_file=token_file)
        del password

    if not ok:
        sys.exit("Login failed.")
    return vue


def discover(vue: PyEmVue) -> dict[str, Any]:
    """Pull the identifiers the parameterised probes need."""
    found: dict[str, Any] = {
        "gids": [],
        "device_ids": [],
        "evse_ids": [],
        "monitor_ids": [],
        "load_gids": [],
        "timezone": None,
    }
    try:
        devices = vue.get_devices()
    except Exception as err:  # noqa: BLE001 - discovery is best-effort
        print(f"  ! device discovery failed: {err}")
        return found

    for device in devices:
        if device.device_gid and device.device_gid not in found["gids"]:
            found["gids"].append(device.device_gid)

        # Nested "SX…" entries are the monitor's own CT sub-device, not a
        # device the API accepts in device_ids. Skip them.
        serial = getattr(device, "manufacturer_id", None)
        if not serial or serial.startswith("SX"):
            continue
        if serial not in found["device_ids"]:
            found["device_ids"].append(serial)

        if device.ev_charger:
            found["evse_ids"].append(serial)
            load = getattr(device.ev_charger, "load_gid", None)
            if load:
                found["load_gids"].append(load)
        elif device.outlet:
            load = getattr(device.outlet, "load_gid", None)
            if load:
                found["load_gids"].append(load)
        else:
            found["monitor_ids"].append(serial)

        if not found["timezone"]:
            found["timezone"] = getattr(device, "time_zone", None) or None

    return found


def build_params(
    need: str | None, found: dict[str, Any], days: int, value: Any = None
) -> dict[str, str] | None:
    """Return query params for a probe, or None when its inputs are missing."""
    if need is None:
        return {}

    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=days)
    window = {
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if need == "devices":
        if not found["device_ids"]:
            return None
        return {"device_ids": ",".join(found["device_ids"]), **window}
    if need == "device_gids":
        if not found["gids"]:
            return None
        return {"device_gids": ",".join(str(g) for g in found["gids"]), **window}
    if need == "gids_q":
        if not found["gids"]:
            return None
        return {"deviceGids": ",".join(str(g) for g in found["gids"])}
    if need == "evses":
        if not found["evse_ids"]:
            return None
        return {"device_ids": ",".join(found["evse_ids"]), **window}
    if need == "evse":
        if not found["evse_ids"]:
            return None
        return {"device_id": found["evse_ids"][0], **window}
    if need == "evse_only":
        if not found["evse_ids"]:
            return None
        return {"device_id": found["evse_ids"][0]}
    if need == "tz":
        if not found["timezone"]:
            return None
        return {"timezone": found["timezone"]}
    if need == "monitor":
        return None if value is None else {"device_id": value}
    if need == "load":
        return None if value is None else {"loadGid": str(value)}
    if need == "gid":
        return {}
    return {}


def probe(session: requests.Session, origin: str, path: str, params: dict[str, str],
          id_token: str) -> dict[str, Any]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    # Emporia's own client sends the id token as `AuthToken` on the legacy host
    # and as a bare `Authorization` value (no "Bearer" prefix) on c-api.
    if origin == LEGACY_ORIGIN:
        headers["AuthToken"] = id_token
    else:
        headers["Authorization"] = id_token

    started = time.monotonic()
    record: dict[str, Any] = {"origin": origin, "path": path, "params": params}
    try:
        response = session.get(
            origin + path, headers=headers, params=params or None, timeout=TIMEOUT
        )
    except Exception as err:  # noqa: BLE001 - a transport failure is a result
        record["error"] = f"{type(err).__name__}: {err}"
        record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        return record

    record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    record["status"] = response.status_code
    record["content_type"] = response.headers.get("Content-Type", "")
    body = response.text
    if "json" in record["content_type"] and body:
        try:
            record["body"] = response.json()
        except ValueError:
            record["body_text"] = body[:4000]
    elif body:
        record["body_text"] = body[:4000]
    return record


def run(args: argparse.Namespace) -> int:
    vue = authenticate(args)
    id_token = vue.auth.tokens["id_token"]

    print("Discovering devices...")
    found = discover(vue)
    print(
        f"  {len(found['gids'])} device gid(s), "
        f"{len(found['evse_ids'])} EV charger(s)"
    )

    session = requests.Session()
    # Local time deliberately: this only names the output files, so they sort
    # the way you experienced the runs. `captured_at` inside is UTC.
    stamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    label = f"-{args.label}" if args.label else ""
    os.makedirs(args.out, exist_ok=True)
    base = os.path.join(args.out, f"emporia-probe-{stamp}{label}")

    results: list[dict[str, Any]] = []

    unauth = probe(session, MAINTENANCE_URL, "", {}, "")
    unauth["name"] = "maintenance banner"
    results.append(unauth)

    for name, origin, path, need in PROBES:
        if need in PER_VALUE_NEEDS:
            targets = {
                "gid": found["gids"],
                "monitor": found["monitor_ids"],
                "load": found["load_gids"],
            }[need]
        else:
            targets = [None]

        if not targets:
            results.append(
                {"name": name, "origin": origin, "path": path,
                 "skipped": f"nothing discovered for '{need}'"}
            )
            print(f"  -  {name}: skipped (no {need})")
            continue

        for value in targets:
            params = build_params(need, found, args.days, value)
            if params is None:
                results.append(
                    {"name": name, "origin": origin, "path": path,
                     "skipped": f"no discovered value for '{need}'"}
                )
                print(f"  -  {name}: skipped (no {need})")
                break

            real_path = path.replace("{gid}", str(value)) if need == "gid" else path
            record = probe(session, origin, real_path, params, id_token)
            record["name"] = name if value is None else f"{name} [{value}]"
            results.append(record)
            status = record.get("status", record.get("error", "?"))
            print(f"  {status}  {record['name']}  ({record.get('elapsed_ms', '?')} ms)")
            time.sleep(args.delay)

    capture = {
        "probe_version": 1,
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "label": args.label,
        "redacted": not args.no_redact,
        "window_days": args.days,
        "results": [
            redact_record(r, not args.no_redact) for r in copy.deepcopy(results)
        ],
    }

    json_path = f"{base}.json"
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(capture, handle, indent=2, sort_keys=False, default=str)

    log_path = f"{base}.log"
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write(f"Emporia API probe — {capture['captured_at']}\n")
        handle.write(f"redacted: {capture['redacted']}   window: {args.days}d\n")
        handle.write("=" * 78 + "\n\n")
        for record in capture["results"]:
            handle.write(f"### {record.get('name')}\n")
            if "skipped" in record:
                handle.write(f"    SKIPPED: {record['skipped']}\n\n")
                continue
            handle.write(f"    {record.get('origin', '')}{record.get('path', '')}\n")
            if record.get("params"):
                handle.write(f"    params: {record['params']}\n")
            if "error" in record:
                handle.write(f"    ERROR: {record['error']}\n\n")
                continue
            handle.write(
                f"    status: {record.get('status')}   "
                f"{record.get('elapsed_ms')} ms   "
                f"{record.get('content_type')}\n"
            )
            body = record.get("body")
            if body is not None:
                paths = sorted(field_paths(body))
                handle.write(f"    fields ({len(paths)}):\n")
                handle.writelines(f"      {item}\n" for item in paths)
                handle.write("    body:\n")
                handle.writelines(f"      {line}\n" for line in json.dumps(body, indent=2, default=str).splitlines())
            elif record.get("body_text"):
                handle.write(f"    body (raw): {record['body_text'][:1000]}\n")
            handle.write("\n")

    ok = sum(1 for r in results if r.get("status") == 200)
    print(f"\n{ok}/{len(results)} probes returned 200")
    print(f"JSON: {json_path}")
    print(f"Log:  {log_path}")
    if not args.no_redact:
        print("Personal fields are redacted. Device gids and serials are not — "
              "scrub those before sharing publicly.")
    return 0


def resolve(pattern: str) -> str:
    """Expand a glob to a single path.

    PowerShell does not expand wildcards before handing arguments to a program,
    so `--diff *-before.json *-after.json` arrives here literally. Expand it
    ourselves rather than failing with a confusing OSError.
    """
    if not any(ch in pattern for ch in "*?["):
        return pattern
    matches = sorted(globlib.glob(pattern))
    if not matches:
        sys.exit(f"No file matches {pattern!r}")
    if len(matches) > 1:
        listing = "\n  ".join(matches)
        sys.exit(f"{pattern!r} matches more than one file; name one:\n  {listing}")
    return matches[0]


def run_diff(before_path: str, after_path: str) -> int:
    before_path, after_path = resolve(before_path), resolve(after_path)
    with open(before_path, encoding="utf-8") as handle:
        before = json.load(handle)
    with open(after_path, encoding="utf-8") as handle:
        after = json.load(handle)

    def by_name(capture: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {r.get("name", "?"): r for r in capture.get("results", [])}

    left, right = by_name(before), by_name(after)
    print(f"before: {before.get('captured_at')}  ({before.get('label') or 'no label'})")
    print(f"after:  {after.get('captured_at')}  ({after.get('label') or 'no label'})")
    print("=" * 78)

    changed = 0
    for name in sorted(set(left) | set(right)):
        a, b = left.get(name), right.get(name)
        if a is None or b is None:
            print(f"\n### {name}\n    only present in {'after' if a is None else 'before'}")
            changed += 1
            continue
        notes: list[str] = []
        if a.get("status") != b.get("status"):
            notes.append(f"status {a.get('status')} -> {b.get('status')}")

        lines_a = json.dumps(a.get("body"), indent=2, sort_keys=True, default=str).splitlines()
        lines_b = json.dumps(b.get("body"), indent=2, sort_keys=True, default=str).splitlines()
        if lines_a != lines_b:
            delta = [
                line
                for line in difflib.unified_diff(lines_a, lines_b, lineterm="", n=1)
                if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
            ]
            notes.extend(delta[:60])
            if len(delta) > 60:
                notes.append(f"... {len(delta) - 60} more changed lines")

        if notes:
            changed += 1
            print(f"\n### {name}")
            for line in notes:
                print(f"    {line}")

    print(f"\n{changed} endpoint(s) differ.")
    return 0


CONTROL_PATH = "/v1/customers/evse/control"

# Three confidence levels, deliberately kept apart:
#   OBSERVED  — captured leaving the official app, so both the value and its
#               meaning are known.
#   ACCEPTED  — we sent it and the API answered 200 rather than 400, so the
#               enum member is real; what it *does* is still unproven.
#   CANDIDATE — an enum-shaped string from the app binary, never sent.
OBSERVED_COMMANDS = {"TURN_ON", "TURN_OFF"}
ACCEPTED_COMMANDS = {"CHARGE_AT_FULL_POWER"}
CANDIDATE_COMMANDS: set[str] = set()
KNOWN_COMMANDS = OBSERVED_COMMANDS | ACCEPTED_COMMANDS | CANDIDATE_COMMANDS

# Rejected outright: 400 "Unexpected value" from ChargerControlCommand.
REJECTED_COMMANDS = {"CHARGE_WITH_EXCESS_SOLAR"}

# Values to test for enum membership with --discover-commands. Drawn from
# enum-shaped strings in the app binary plus obvious naming variants.
ENUM_CANDIDATES = [
    # Confirmed members.
    "TURN_ON", "TURN_OFF", "CHARGE_AT_FULL_POWER",
    # Confirmed non-members, kept so a future API change shows up as a change.
    "CHARGE_NOW", "CHARGE_BOOST", "CHARGE_TO_STATE_OF_CHARGE",
    "CHARGE_DURING_PEAK", "CHARGE", "PAUSE", "RESUME", "STOP", "START",
    "CHARGE_WITH_EXCESS_SOLAR", "EXCESS_SOLAR", "OVERRIDE_EXCESS_SOLAR",
    "OVERRIDE_PEAK_DEMAND_CHARGE_OR_PAUSE", "OVERRIDE_PEAK_DEMAND_RESUME",
    "OVERRIDE_UTILITY", "MANUAL_ECO",
    "CLEAR_OVERRIDE", "END_OVERRIDE", "CANCEL_OVERRIDE", "REMOVE_OVERRIDE",
    "RESUME_ENERGY_MANAGEMENT", "RESUME_SCHEDULE", "AUTO", "AUTOMATIC",
    "SMART_CHARGING", "ECO", "DEFAULT", "NONE",
    # Second sweep: naming variants for a release/full-power action.
    "FULL_POWER", "CHARGE_AT_MAX_POWER", "CHARGE_AT_FULL_SPEED", "FULL_SPEED",
    "MAX_POWER", "MAX", "BOOST", "UNPAUSE", "ON", "OFF", "ENABLE", "DISABLE",
    "RESET", "CANCEL", "OVERRIDE", "SCHEDULE", "SOLAR", "SMART", "GREEN",
    "ENERGY_MANAGEMENT", "RESUME_AUTOMATION", "CHARGE_IMMEDIATELY",
]

# Substring of the Jackson error that means "not a member of the enum".
NOT_A_MEMBER = "Unexpected value"


def run_discover_commands(args: argparse.Namespace) -> int:
    """Enumerate ChargerControlCommand members without executing anything.

    The control endpoint deserializes the request body before it validates it.
    A body carrying a `command` but **no `device_id`** therefore fails one of
    two ways: at JSON parse, if the enum value does not exist, or later on the
    missing device. Neither path reaches a charger, so this maps the enum
    without changing any state.
    """
    vue = authenticate(args)
    id_token = vue.auth.tokens["id_token"]
    session = requests.Session()
    headers = {
        "AuthToken": id_token,
        "Authorization": id_token,
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }

    def ask(command: str) -> tuple[int, str]:
        body = {"command": command}
        assert "device_id" not in body, "enum probe must never carry a device"
        response = session.post(
            LEGACY_ORIGIN + CONTROL_PATH, headers=headers, json=body, timeout=TIMEOUT
        )
        return response.status_code, response.text

    print(
        "Mapping ChargerControlCommand.\n"
        "Every request omits device_id, so none of them can reach the charger.\n"
    )

    # Verify the oracle before trusting it: a known-good value and a nonsense
    # value must fail in visibly different ways.
    good_status, good_text = ask("TURN_ON")
    bad_status, bad_text = ask("ZZ_NOT_A_REAL_COMMAND")
    if NOT_A_MEMBER in good_text or NOT_A_MEMBER not in bad_text:
        print("Oracle check FAILED — results would be meaningless. Aborting.")
        print(f"  TURN_ON            -> {good_status} {good_text[:200]}")
        print(f"  ZZ_NOT_A_REAL_...  -> {bad_status} {bad_text[:200]}")
        return 2
    print("Oracle check passed:")
    print(f"  valid value   -> {good_status} {scrub_text(good_text)[:160]}")
    print(f"  invalid value -> {bad_status} {scrub_text(bad_text)[:160]}\n")

    members, rejected = [], []
    for command in ENUM_CANDIDATES:
        status, text = ask(command)
        if NOT_A_MEMBER in text:
            rejected.append(command)
            print(f"  --      {command}")
        else:
            members.append(command)
            print(f"  MEMBER  {command}   ({status})")
        time.sleep(args.delay)

    print(f"\n{len(members)} member(s): {', '.join(members)}")
    print(f"{len(rejected)} rejected.")
    print("\nNothing was executed: no request carried a device_id.")
    return 0


def read_load_state(session: requests.Session, id_token: str) -> Any:
    """Return the legacy loads[] array, which carries override state."""
    record = probe(session, LEGACY_ORIGIN, "/customers/devices/status", {}, id_token)
    body = record.get("body") or {}
    return body.get("loads")


def run_send_command(args: argparse.Namespace) -> int:
    """POST one command to the EVSE control endpoint, with before/after state."""
    command = args.send_command.strip().upper()
    if command not in KNOWN_COMMANDS:
        print(f"Unknown command {command!r}. Known values:")
        print("  observed:  " + ", ".join(sorted(OBSERVED_COMMANDS)))
        print("  accepted:  " + ", ".join(sorted(ACCEPTED_COMMANDS)))
        print("  candidate: " + ", ".join(sorted(CANDIDATE_COMMANDS)))
        print("Pass --force-command to send it anyway.")
        if not args.force_command:
            return 2

    vue = authenticate(args)
    id_token = vue.auth.tokens["id_token"]
    session = requests.Session()

    device_id = args.device_id
    if not device_id:
        found = discover(vue)
        if not found["evse_ids"]:
            sys.exit("No EV charger found; pass --device-id explicitly.")
        device_id = found["evse_ids"][0]

    before = read_load_state(session, id_token)
    print("\nloads[] before:")
    print(json.dumps(before, indent=2))

    # A command that asks for the state the charger is already in is a no-op,
    # and the run tells you nothing. Say so before the write, not after.
    overridden = any(
        entry.get("energyManagementOverridden") for entry in (before or [])
    )
    if overridden:
        print(
            "\n  CAUTION: an energy-management override is ALREADY active.\n"
            "  A command that requests the state it already produces will look\n"
            "  like a no-op, and this run will not tell you whether the command\n"
            "  creates an override. To test that, either release the override\n"
            "  first or wait for it to lapse, then re-run from a clean state."
        )

    body = {"device_id": device_id, "command": command}
    print(f"\nAbout to send  >>> {command} <<<")
    print(f"  POST {LEGACY_ORIGIN}{CONTROL_PATH}\n  {json.dumps(body)}")
    if command in ACCEPTED_COMMANDS:
        print("  NOTE: the API accepts this value, but its effect is unproven.")
    elif command not in OBSERVED_COMMANDS:
        print("  NOTE: this command value has never been sent or observed.")
    print("\nThis changes your EV charger.")

    if not args.yes and input('Type "yes" to send: ').strip().lower() != "yes":
        print("Aborted. Nothing was sent.")
        return 1

    started = time.monotonic()
    response = session.post(
        LEGACY_ORIGIN + CONTROL_PATH,
        headers={
            "AuthToken": id_token,
            "Authorization": id_token,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        json=body,
        timeout=TIMEOUT,
    )
    elapsed = round((time.monotonic() - started) * 1000)
    print(f"\n{response.status_code} in {elapsed} ms")
    print(scrub_text(response.text[:2000]) or "(empty body)")

    # The cloud needs a moment to reflect a command in loads[].
    for attempt in range(6):
        time.sleep(2)
        after = read_load_state(session, id_token)
        if after != before:
            print(f"\nloads[] changed after ~{(attempt + 1) * 2}s:")
            print(json.dumps(after, indent=2))
            return 0

    print("\nloads[] unchanged after 12s:")
    print(json.dumps(after, indent=2))
    return 0


def run_rescrub(path: str) -> int:
    """Re-apply redaction to an existing capture, in place.

    For captures written before value-level scrubbing existed: Emporia echoes
    the authenticated email and source IP inside some 4xx error strings, which
    key-based redaction alone did not catch.
    """
    path = resolve(path)
    with open(path, encoding="utf-8") as handle:
        capture = json.load(handle)

    before = json.dumps(capture)
    capture["results"] = [redact_record(r, True) for r in capture["results"]]
    capture["redacted"] = True
    after = json.dumps(capture)

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(capture, handle, indent=2, default=str)

    print(f"Rescrubbed {path}")
    print("  changed" if before != after else "  already clean")
    print("  The matching .log is NOT rewritten — delete it and re-run to "
          "regenerate, or scrub it by hand.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe of the Emporia cloud API. Read-only unless "
                    "--send-command is used.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--email", help="Emporia account email (prompted if omitted)")
    parser.add_argument(
        "--tokens",
        action="store_true",
        help="paste id/access/refresh tokens instead (Google/Apple accounts)",
    )
    parser.add_argument(
        "--token-file",
        help="save/reuse the session here so you authenticate only once",
    )
    parser.add_argument("--out", default=".", help="output directory (default: cwd)")
    parser.add_argument("--label", help="suffix for the output filenames")
    parser.add_argument(
        "--days", type=int, default=7, help="lookback window for history endpoints"
    )
    parser.add_argument(
        "--delay", type=float, default=0.3, help="seconds between requests"
    )
    parser.add_argument(
        "--no-redact",
        action="store_true",
        help="keep personal fields in the capture (tokens are still never written)",
    )
    parser.add_argument(
        "--diff", nargs=2, metavar=("BEFORE.json", "AFTER.json"),
        help="compare two captures instead of probing",
    )
    parser.add_argument(
        "--rescrub", metavar="CAPTURE.json",
        help="re-apply redaction to an existing capture, in place",
    )
    parser.add_argument(
        "--send-command", metavar="COMMAND",
        help="POST one command to /v1/customers/evse/control. THIS WRITES. "
             "Shows loads[] before and after so you can see whether it opened "
             "an energy-management override.",
    )
    parser.add_argument(
        "--discover-commands", action="store_true",
        help="map the ChargerControlCommand enum. Sends POSTs with no device_id, "
             "so nothing can reach the charger and no state changes.",
    )
    parser.add_argument(
        "--device-id", help="charger serial for --send-command (default: discovered)",
    )
    parser.add_argument(
        "--force-command", action="store_true",
        help="allow a --send-command value not in the known list",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="skip the typed confirmation for --send-command",
    )
    args = parser.parse_args()

    if args.diff:
        return run_diff(*args.diff)
    if args.rescrub:
        return run_rescrub(args.rescrub)
    if args.discover_commands:
        return run_discover_commands(args)
    if args.send_command:
        return run_send_command(args)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
