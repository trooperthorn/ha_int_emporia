# Investigating the Emporia API: methods and traps

[api-reference.md](api-reference.md) records *what* the API does.
This records *how to find out more*, and the mistakes that cost time on
2026-09-20 so the next person — human or agent — does not repeat them.

## Where to get information, in order of cost

1. **[PyEmVue](https://github.com/magico13/PyEmVue) source.** Free, but only
   covers the legacy host and silently drops fields it does not model. It
   discards the entire `loads[]` array. **Never assume PyEmVue's model is the
   response.**
2. **[`emporiaenergy/emporia-mcp`](https://github.com/emporiaenergy/emporia-mcp).**
   First-party, open source, and the only published description of the `/v1`
   surface. Its TypeScript types are in places **narrower than the real
   responses** — the sessions response carries `session_id`, `start_reason` and
   `end_reason` that its types omit. Treat as a floor, not a spec.
3. **`scripts/api_probe.py`.** Read-only by default. Run it first; it answers
   most questions in 30 seconds.
4. **The Android app binary.** Good for discovering *names*. See below.
5. **The web app.** The only practical way to capture a **write**. See below.

## Mining the app binary

The app is **Flutter**, so no endpoint strings live in the dex files — they are
all in `lib/arm64-v8a/libapp.so` inside the ABI split APK, not `base.apk`.

```bash
adb shell pm path com.emporiaenergy.emporos.v2
adb pull <.../split_config.arm64_v8a.apk> arm64.apk
unzip -p arm64.apk lib/arm64-v8a/libapp.so > libapp.so
strings -n 4 libapp.so | grep -E '^/(v1|customers|devices|AppAPI)'
```

**What works:** extracting path strings, enum-shaped `SCREAMING_SNAKE` values,
and Dart method names. Every real `ChargerControlCommand` member was present in
the binary.

**What does not work:** inferring structure from proximity. The Dart AOT object
pool is *not* laid out by compilation unit, so strings near an anchor are
unrelated. Two attempts to group field names around an endpoint produced pure
noise. Do not build schemas this way.

**What actively misleads:** related-looking names. Every override symbol that
looked like it applied to chargers — `BatteryOverrideRequest`,
`postBatteryDeviceOverride`, `startBatteryOverride`, `batteryOverrideEndsAt` —
was battery-only, and `/v1/customers/devices/override` answers
`"Device overrides are only supported for batteries"`. A name in the binary
proves the string exists, nothing more.

## Capturing a write

**The phone app cannot be intercepted.** Flutter's `dart:io` HTTP client uses
BoringSSL with a compiled-in root store and ignores Android's user CA store, so
mitmproxy and friends fail regardless of how the certificate is installed.
Patching the APK is out of scope.

**`web.emporiaenergy.com` is the same Flutter application compiled for the
browser**, and its XHR/fetch calls are readable with no interception at all.
Hook `window.fetch` *and* `XMLHttpRequest.prototype.send` — which transport the
build uses varies — then drive the UI. Bodies arrive as a `Uint8Array`, not a
string; decode with `TextDecoder`. Never log the `Authorization` header.

Caveat: the web build is **not** feature-complete. It has no "Manage Charging"
sheet, only Pause/Resume, so the override-creating action cannot be reached
there.

## Probing an enum without executing it

The control endpoint deserializes the request body *before* validating it. So a
body carrying a `command` but **no `device_id`**:

- fails at JSON parse (`"Unexpected value 'X'"`) when the value is not a member
- fails later on the missing device when it is

Neither path reaches a charger. `api_probe.py --discover-commands` uses this to
map `ChargerControlCommand` exactly, with zero side effects. It verifies the
oracle first — a known-good and a nonsense value must fail in visibly different
ways — and aborts rather than reporting nonsense.

This trick should generalise to any other Jackson-backed enum on this API.

## Traps that cost real time

**Do not test a command from a state that is already its target.** Four separate
runs of `CHARGE_AT_FULL_POWER` produced nothing, because each was sent while an
override was already active — the command asked for the state the charger was
already in, so a no-op was guaranteed. Establish the *opposite* precondition
first, or the run cannot distinguish "did nothing" from "did the thing".

**Absent data is not a negative result.** A clean-state check written as
`not is_overridden(loads)` returned true when the endpoint returned no `loads[]`
at all, and fired a command mid-override. Require a *positive* report — a
non-empty list with no entry overridden. The same rule applies in the
integration: `energy_management.controller()` returns `None` for a missing load
rather than `manual`, and the entities go unavailable rather than claim local
control.

**A 200 is not proof of effect.** `CHARGE_AT_FULL_POWER` returns `200` with an
empty body. The endpoint returns no result payload and no echo of resulting
state, so the only way to learn what a command did is to re-read
`/customers/devices/status` afterwards.

**The UI describes configuration, not live state.** The Charge Rate screen says
"Managed by Excess Solar" whenever the feature is *configured*, including while
an override has suspended it. Reading that as live state produced a wrong
conclusion that reached the docs before a probe contradicted it.

**Check what a window says, not just that a flag is set.** Whether a command
created a *new* override versus leaving an existing one alone is only visible in
the times inside `energyManagementText`. A restarted window means a new
override; an unchanged one means a no-op.

## Tooling notes

- **PowerShell does not expand globs.** `--diff *-before.json` arrives at the
  script literally. `api_probe.py` expands them in-process.
- **Legacy 4xx bodies echo the caller's identity** (`sourceIp`, `email`), so
  redaction must scrub *values*, not just keys.
- **`logo_bytes` in `/v1/derms/devices`** is a base64 PNG that bloats a capture.
- **Rate limits** were never hit: ~50 sequential requests with a 0.3 s delay ran
  clean, repeatedly.

## Running the tests

The HA test harness imports `fcntl`, so the suite cannot run on native Windows.
A ready venv exists in WSL at `~/emporiavenv` (Python 3.14, `homeassistant`
2026.9.0, `pytest-homeassistant-custom-component` 0.13.362 — the versions CI
asserts):

```bash
wsl -e bash -lc 'cd /mnt/c/Users/sean.LAB/repos/ha_int_emporia && \
  ~/emporiavenv/bin/python -m pytest tests/ -q'
```

To rebuild it, follow the two-step install in
[operations.md](operations.md#running-the-test-suite) — the order matters, and
the resulting pip conflict warning about `homeassistant==2026.9.0b6` is
expected.

## Keeping the reference honest

[api-reference.md](api-reference.md) labels every row with how it was verified
(`V-live`, `V-lib`, `V-mcp`, `B-apk`). That distinction earned its keep: three
claims in it turned out to be wrong and were caught precisely because the
evidence behind each was written down. Corrections are left visible in the
document rather than edited out, so a reader can see which way an error pointed.

If you promote a row to a stronger level, say what you observed.
