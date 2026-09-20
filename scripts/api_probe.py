#!/usr/bin/env python3
"""Read-only probe of the Emporia cloud API.

Logs in as you, issues a GET against every endpoint listed in
docs/api-reference.md, and writes a JSON capture plus a human-readable log so
the undocumented parts of the surface can be pinned down with real responses.

This script only ever issues GET requests. There is no code path in it that
writes, controls, or changes anything on your account or hardware.

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

# (label, origin, path, needs) — `needs` names the discovered value this probe
# requires; the probe is skipped with a reason when it is unavailable.
#   "devices"  -> comma-joined list of every device id
#   "evse"     -> a single EV charger device id
#   "evses"    -> comma-joined list of EV charger device ids
#   "gid"      -> each Emporia device gid, one request per gid
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
    ("legacy load management", LEGACY_ORIGIN, "/customers/loadmanagement", None),
    ("legacy derms", LEGACY_ORIGIN, "/customers/derms", None),
    ("legacy device schedule", LEGACY_ORIGIN, "/devices/schedule", None),
    ("legacy evcharger max rate", LEGACY_ORIGIN, "/devices/evcharger/maxchargingrate", None),
    ("legacy firmware up to date", LEGACY_ORIGIN, "/devices/firmwareuptodate", None),
    # --- v1: account, sites, devices ---
    ("v1 customer", V1_ORIGIN, "/v1/customers", None),
    ("v1 sites", V1_ORIGIN, "/v1/customers/sites", None),
    ("v1 site members", V1_ORIGIN, "/v1/customers/sites/members", None),
    ("v1 devices", V1_ORIGIN, "/v1/customers/devices", None),
    ("v1 device status", V1_ORIGIN, "/v1/customers/devices/status", None),
    ("v1 device channels", V1_ORIGIN, "/v1/customers/devices/channels", None),
    ("v1 device settings", V1_ORIGIN, "/v1/customers/devices/settings", None),
    ("v1 third party access", V1_ORIGIN, "/v1/customers/devices/third-party-access", None),
    ("v1 app preferences", V1_ORIGIN, "/v1/customers/app-preferences", None),
    ("v1 homepage summary", V1_ORIGIN, "/v1/customers/homepage/summary", None),
    ("v1 homepage monitor card", V1_ORIGIN, "/v1/customers/homepage/monitor-card", None),
    ("v1 savings", V1_ORIGIN, "/v1/customers/devices/savings", None),
    # --- v1: the one that matters most ---
    ("v1 device override", V1_ORIGIN, "/v1/customers/devices/override", None),
    # --- v1: energy management controllers ---
    ("v1 excess generation", V1_ORIGIN, "/v1/customers/excess-generation", None),
    ("v1 excess generation monitor", V1_ORIGIN, "/v1/customers/energy-monitor/excess-generation", None),
    ("v1 power smart", V1_ORIGIN, "/v1/customers/power-smart", None),
    ("v1 power smart monitor", V1_ORIGIN, "/v1/customers/energy-monitor/power-smart", None),
    ("v1 peak demand", V1_ORIGIN, "/v1/customers/peak-demand", None),
    ("v1 peak demand monitor", V1_ORIGIN, "/v1/customers/energy-monitor/peak-demand", None),
    ("v1 load sharing", V1_ORIGIN, "/v1/customers/load-sharing", None),
    ("v1 load sharings", V1_ORIGIN, "/v1/customers/load-sharings", None),
    ("v1 derms", V1_ORIGIN, "/v1/customers/derms", None),
    ("v1 derms devices", V1_ORIGIN, "/v1/derms/devices", None),
    # --- v1: EV charging ---
    ("v1 evses", V1_ORIGIN, "/v1/devices/evses", "evses"),
    ("v1 evse sessions", V1_ORIGIN, "/v1/devices/evses/sessions", "evses"),
    ("v1 evse charging history", V1_ORIGIN, "/v1/customers/evse/charging-history", "evses"),
    ("v1 ev charging report", V1_ORIGIN, "/v1/customers/ev-charging-report", "evse"),
    ("v1 vehicle brands", V1_ORIGIN, "/v1/vehicles/brands", None),
    # --- v1: rates and misc ---
    ("v1 utility rates", V1_ORIGIN, "/v1/utility-rates", None),
    ("v1 device utility rates", V1_ORIGIN, "/v1/devices/utility-rates", None),
    ("v1 rate analysis", V1_ORIGIN, "/v1/customers/rate-analysis", None),
    ("v1 recommendations", V1_ORIGIN, "/v1/customers/recommendations", None),
    ("v1 device usages", V1_ORIGIN, "/v1/customers/devices/usages", "devices"),
]


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
    found: dict[str, Any] = {"gids": [], "device_ids": [], "evse_ids": []}
    try:
        devices = vue.get_devices()
    except Exception as err:  # noqa: BLE001 - discovery is best-effort
        print(f"  ! device discovery failed: {err}")
        return found
    for device in devices:
        if device.device_gid and device.device_gid not in found["gids"]:
            found["gids"].append(device.device_gid)
        serial = getattr(device, "manufacturer_id", None)
        if serial:
            found["device_ids"].append(serial)
            if device.ev_charger:
                found["evse_ids"].append(serial)
    return found


def build_params(need: str | None, found: dict[str, Any], days: int) -> dict[str, str] | None:
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
    if need == "evses":
        if not found["evse_ids"]:
            return None
        return {"device_ids": ",".join(found["evse_ids"]), **window}
    if need == "evse":
        if not found["evse_ids"]:
            return None
        return {"device_id": found["evse_ids"][0], **window}
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
        params = build_params(need, found, args.days)
        if params is None:
            results.append(
                {"name": name, "origin": origin, "path": path,
                 "skipped": f"no discovered value for '{need}'"}
            )
            print(f"  -  {name}: skipped (no {need})")
            continue

        targets = found["gids"] if need == "gid" else [None]
        for gid in targets:
            real_path = path.replace("{gid}", str(gid)) if gid is not None else path
            record = probe(session, origin, real_path, params, id_token)
            record["name"] = name if gid is None else f"{name} [{gid}]"
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
        description="Read-only probe of the Emporia cloud API.",
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
    args = parser.parse_args()

    if args.diff:
        return run_diff(*args.diff)
    if args.rescrub:
        return run_rescrub(args.rescrub)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
