# Decisions

## 2026-09-03: comment-to-docs pass, README changelog moved here

The README carried an inline changelog blurb (a "MODIFIED... via GEMINI Pro"
line) instead of documenting behavior. The line named a model, which this
project's house style excludes from committed files, and a changelog is not
the right place to explain current behavior to a new reader. The blurb is
preserved here as a dated-unknown historical record instead of being
discarded, and the README now describes what the integration does rather
than how it changed.

Historical changes, date unknown (inherited from the prior README text,
predating this pass):

- EV charging current slider, with the change visible live in the app.
- EV charging automation blueprints.
- Sensors formatted for the Home Assistant Energy dashboard category.
- A Balance sensor added to account for usage not covered by per-channel
  sensors.
- Sensor registration made to survive a Home Assistant restart.
- Calls to PyEmVue made async.
- Noted behavior: a day's totals do not carry over as "yesterday's total" at
  midnight when solar is present, because Home Assistant's Energy dashboard
  does not accept negative values.
- EV charging made more configurable, including settable amperage and a
  dynamic solar-charging automation.

## 2026-09-03: removed `single_config_entry` from the manifest

`manifest.json` declared `single_config_entry: true`, which blocks a second
config entry outright regardless of account. That contradicts the
integration's own duplicate-detection design: `ConfigFlow` sets the unique
ID to the Emporia `customer_gid` and calls `_abort_if_unique_id_configured`,
which only aborts a second entry for the *same* account, and
`quality_scale.yaml` already marks `unique-config-entry: done` on that
basis. A user with two Emporia accounts (for example a home and a rental
property) was unable to add the second one. The key was removed so multiple
accounts can coexist, each still deduplicated by `customer_gid`.

## 2026-09-03: version scheme note for the baseline release pipeline

The most recent published release is tagged `v2026.08.21.0`, and
`manifest.json` was set to `2026.08.12` (already stale relative to that
tag). The new release pipeline's version reader
(`scripts/release_config.py`) requires CalVer `YYYY.MM.DD` or
`YYYY.MM.DD.N` with `N` starting at 1, so it rejects a trailing `.0`.
`manifest.json` is set to `2026.08.21` (no sequence) rather than
`2026.08.21.0`, which does not exactly match the historic tag string but is
the same release date; sequence suffixes on future same-day releases start
at `.1`. This does not retag the existing `v2026.08.21.0` release.

## 2026-09-03: stopped swallowing AbortFlow in the config flow

`async_step_email_password` and `async_step_tokens` called
`_abort_if_unique_id_configured()` inside a `try` block that ended with a
bare `except Exception`. `_abort_if_unique_id_configured` raises
`AbortFlow` to end the flow with an "already configured" result, but the
bare except caught it, logged it as an unexpected exception, and showed the
form again with an "unknown" error instead of aborting. Both steps now
re-raise `AbortFlow` before the generic exception handler runs.

## Undated: Balance and Mains Import/Export sensors made unconditional

`VueBalanceSensor` and `VueMainsSplitSensor` were previously created only for
devices recognized as a true mains/panel device
(`_device_is_true_mains_panel`). That restriction blocked Balance and Mains
generation on monitors that handle solar or net metering but do not match
the true-mains-panel shape. The restriction was removed so those monitors
also get Balance and Mains Import/Export sensors; see
[design.md](design.md#balance-and-mains-importexport-sensors-are-unconditional).

## 2026-09-04: boto3 is pinned to core's own constraint

`boto3==1.42.97` replaces the `>=1.37.1,<1.43.0` range so installs are
reproducible; the value is the one core 2026.9.0 pins in
`package_constraints.txt`, which sits inside the range pyemvue accepts.
Rejected: keeping the range, which let two installs of the same version
resolve different boto3 releases.

## 2026-09-04: the config flow keeps `async_update_reload_and_abort`

The scanner flags the two reload sites because combining them with a config
entry update listener becomes an error in core 2026.12 (developer blog
2026-05-07). This integration registers no update listener, so the flow's
reloads are the only reload path and the rule does not apply.


## 2026-09-06: boto3 is a range again, bounded by core's constraint

This supersedes the 2026-09-04 entry above. Core passes `package_constraints.txt` to pip
as a constraint when it installs a custom integration's requirements, so the exact
`boto3==1.42.97` pin only installed while it equalled core's pin; the first core point
release to move boto3 would have failed setup with "Requirements for emporia_vue not
found", which is exactly what core 2026.9.1 did to the Elk-M1 and Davis integrations
through `serialx` on 2026-09-06. The manifest now declares `boto3>=1.42.97,<2`. The
reproducibility argument of the earlier entry does not hold: on any given core the
constraint file fixes the version, so a range resolves to the same release every time.
Rejected: keeping the exact pin and bumping it after each core release.

## 2026-09-20: the write guard warns by default rather than blocking

Emporia's cloud writes the same `chargingRate` field this integration writes,
and keeps re-writing it while an energy-management feature is active (see
[api-reference.md](api-reference.md)). A setpoint written from Home Assistant
during that period is likely to be discarded within the minute.

The integration now detects that and, by default, **still performs the write**
and logs a warning naming the feature that owns the rate. Blocking is available
behind the `block_contended_writes` option.

Blocking by default was rejected. It would change the behaviour of every
existing automation on upgrade, turning a write that previously appeared to
succeed into a raised `HomeAssistantError` — a silent no-op becoming a loud
failure is an improvement, but not one to impose without consent. There is also
a legitimate reason to write while contended: the value is still the ceiling the
controller modulates underneath, so setting it is not meaningless, merely not
authoritative.

The actionable signal lives in the `Cloud Managed` binary sensor, so automations
can avoid the contention rather than discover it.

## 2026-09-20: `loads[]` is read by issuing the request directly

`VueDeviceStatusCoordinator` no longer calls `PyEmVue.get_devices_status()`. That
helper parses `outlets`, `evChargers` and `devicesConnected` out of the response
and discards the `loads` array, which is the only place Emporia reports which
energy-management feature owns a load and whether an override is suppressing it.

The coordinator issues the same single request through `vue.auth.request()` —
which keeps PyEmVue's token refresh and retry behaviour — and parses all four
keys itself. The API request count is unchanged.

Rejected: forking PyEmVue to add `loads` to its model. It would put a
first-party fork in the dependency chain for one field, and this integration
already carries a documented plan to replace PyEmVue with a direct client.

Rejected: a second request to the same endpoint for the loads data alone. It
would double the poll rate against an undocumented cloud API for no benefit.

## 2026-09-26: a scoped shim for pycognito's at_hash TypeError on Python 3.14

Token-based setup (pasting Google/Apple Cognito tokens, `AUTH_METHOD_TOKENS`)
failed on Python 3.14 with `TypeError: Cannot convert str to buffer` whenever
the ID token carried an OIDC `at_hash` claim, which Cognito Hosted-UI tokens
do. Root cause: pycognito 2024.5.1 (the current release; there has been no
release since 2024-05) hashes the access token as a `str` in
`Cognito.verify_token`, and PyJWT's `compute_hash_digest` on Python 3.14
requires bytes. Reproduced locally in
`tests/test_pycognito_compat.py::test_unpatched_pycognito_fails_to_verify_at_hash_token`
with a self-signed RS256 JWT/JWKS pair, no network involved. Plain
email/password login does not go through Cognito Hosted UI and is
unaffected.

Upstream context: magico13/ha-emporia-vue#454 and #439 report the same
failure, #461 and #458 are candidate fixes, and NabuCasa/pycognito#339 is the
real, unreleased fix. sgorilla/ha-emporia-vue ships an equivalent shim.

Fix landed: `custom_components/emporia_vue/pycognito_compat.py`. It rebinds
only `pyemvue.auth.Cognito` (the name PyEmVue's own `Auth.__init__` calls) to
a subclass whose `verify_token` is copied from pycognito 2024.5.1 with the
at_hash block corrected per PR #339 (encode the access token to UTF-8 bytes
before hashing, then decode the resulting hash back to `str` before comparing
it to the claim). `pycognito.Cognito` itself is never modified, so this
cannot affect any other integration or library sharing the same pycognito
install.

Rejected: monkeypatching `jwt.algorithms.Algorithm.compute_hash_digest` and
`base64.urlsafe_b64encode` process-wide for the duration of the call, the
approach in ha-emporia-vue#458. It works, but it temporarily replaces
process-global functions during every token verification, a wider blast
radius than this bug needs when the narrower per-class patch (#461's
approach) is just as effective.

The patch gates itself on a runtime probe (`_at_hash_hashing_is_broken()`)
that actually calls `compute_hash_digest` with a `str` and checks whether it
raises, rather than comparing pycognito's version string. A version pin
would stop protecting the integration the day pycognito ships a fix under a
version number nobody can predict in advance; the probe instead becomes a
no-op automatically the moment the underlying bug is gone, and the whole
module can then be deleted without side effects. Verified the shim does not
weaken verification:
`tests/test_pycognito_compat.py::test_patched_cognito_still_rejects_wrong_at_hash`
confirms a mismatched at_hash still raises `TokenVerificationException`.

Unverified: whether Cognito ever issues federated (Google/Apple) tokens
without an `at_hash` claim in some flow this integration doesn't exercise;
the shim only changes behavior when `at_hash` is present, matching stock
pycognito's own conditional.

## 2026-09-26: bounded last-known-good fallback, replacing indefinite fallback

On 2026-09-25 the house internet/DNS was down for about an hour. The minute,
day, month, and device-status coordinators all held onto `self.data`
indefinitely on any failure (added for brief cloud blips; see the bd361f6
history in this file's earlier entries), so `switch.ev_charger` kept reporting
"Charging" the entire time and automations acted on hour-old state. Each
failed cycle also logged at both ERROR (inside `update_sensors`) and WARNING
(inside the coordinator), every minute, for the whole outage.

The fallback is now time-bounded via `BoundedLastKnownGoodMixin` in
`coordinator.py`. Each coordinator tracks `_degraded_since` (set on the first
failure after a success) and keeps serving `self.data` only while
`datetime.now(UTC) - _degraded_since < lkg_grace`; once that elapses, the
triggering `UpdateFailed` is (re)raised and entities go unavailable, same as
if no fallback existed at all. `is_newer_sample`'s double-count guard in the
day/month coordinators is unchanged by this.

Grace windows, chosen relative to each coordinator's own fetch cadence:

- Minute power and device status (charger/outlet): **5 minutes**. Both poll
  every cycle (1 minute), so this tolerates 5 consecutive failed polls, long
  enough for a brief DNS/network blip, short enough that a real outage
  doesn't leave a stale charger/switch state feeding automations for long.
- Day energy: **30 minutes**. The day coordinator only calls the API every 15
  minutes (the rest of each cycle integrates local minute data), so 30
  minutes covers two missed API refreshes rather than five 1-minute polls.
- Month energy: **60 minutes**, by the same reasoning against its 30-minute
  API refresh cadence.

Also fixed as part of the same change: `update_sensors()` and
`VueDeviceStatusCoordinator._async_update_data()` no longer log at
ERROR/WARNING on every failed cycle. `DataUpdateCoordinator` itself already
logs once (ERROR) when a raised `UpdateFailed` flips `last_update_success` to
False, and once more (INFO) on recovery
(`homeassistant.helpers.update_coordinator._async_refresh`); combined with a
single WARNING logged by the mixin at the moment the degraded window opens,
this replaces per-cycle log spam with exactly two log lines per outage.

Rejected: the upstream magico13/ha-emporia-vue PR #452 approach (tolerate a
fixed *count* of consecutive failures, e.g. 2, rather than a time window).
A count is agnostic to how long each attempt takes, which matters less for
the minute coordinator (fixed 1-minute cadence) but doesn't map cleanly onto
the day/month coordinators, whose "failure" only happens once per 15/30
minute API refresh; a time-bound expressed directly in minutes was clearer
here and is what this fork's evidence and task called for.

## 2026-09-26: connectivity diagnostics use the minute coordinator as the signal

Added a diagnostic `binary_sensor.emporia_vue_cloud_connection`
(`device_class: connectivity`) and `sensor.emporia_vue_cloud_last_update`
(`device_class: timestamp`), both `entity_category: diagnostic`, grouped
under a synthetic "Emporia Cloud Connection" service device
(`DeviceEntryType.SERVICE`) rather than any specific Vue panel/circuit
device, since cloud reachability is account-wide, not per-device.

Both track `VueMinuteCoordinator` specifically rather than aggregating across
all four coordinators: it has the tightest polling cadence (1 minute) and
shares the same authenticated PyEmVue session every other coordinator uses,
so it is the most timely proxy for "is the Emporia cloud reachable right
now." The day/month coordinators would lag this signal by up to 30/60
minutes purely due to their own cadence, which would make the connectivity
sensor slower to report a real outage, not more accurate.

Compared against upstream PR #452, which instead exposes retry-count and
API-latency sensors (`Emporia API Retries`, `Emporia API Latency`) built on a
count-based `TolerantUpdateMethod` wrapper. Not ported:

- The retry-count/latency sensors themselves. They fit a count-based
  tolerance model; this fork's time-bounded model is more directly expressed
  as "are we currently within grace" (the connectivity binary sensor) and
  "when did we last succeed" (the timestamp sensor), which is also what the
  task asked for.
- `TolerantUpdateMethod`, a generic wrapper class around a coordinator's
  `update_method`. This fork's coordinators already have bespoke
  `_async_update_data` methods (day/month interleave local minute
  integration with periodic API refreshes) rather than a single wrapped
  callable, so `BoundedLastKnownGoodMixin` (a mixin the coordinator classes
  opt into directly) fit the existing architecture better than introducing a
  parallel update-method wrapper.

Borrowed in spirit: PR #452's core idea of bounding "how long is brief"
before falling back, and its README documentation pattern for the resulting
diagnostics.

## 2026-09-26: login errors only reauth on a genuine Cognito auth failure

On 2026-09-16 Emporia's `/customers` endpoint returned HTTP 400 for about
2h40m. `async_setup_entry` caught *any* exception from `vue.login` (which
also calls `get_customer_details()` internally) and turned it into
`ConfigEntryAuthFailed`, which forces Home Assistant's reauth flow — so every
affected user was prompted to re-enter credentials for an outage that had
nothing to do with credentials. The same blanket catch would also convert a
Cognito connect timeout (the 2026-09-25 evidence) into a reauth prompt.

`async_setup_entry` now only raises `ConfigEntryAuthFailed` for:

- `vue.login()` returning `False` (pyemvue's own signal for bad
  credentials; it already catches Cognito's `NotAuthorizedException`
  internally and returns `False` for it), or
- a `botocore.exceptions.ClientError` whose `response["Error"]["Code"]` is
  exactly `NotAuthorizedException` (kept as a defensive case in
  `_is_cognito_not_authorized`, in case a future pyemvue version stops
  swallowing it, or another Cognito call path raises it directly).

Everything else — connect timeouts, other botocore `ClientError` codes
(rate limiting, internal errors), and `requests.exceptions.HTTPError` from
Emporia's own API after a successful Cognito step — now raises
`ConfigEntryNotReady`, which Home Assistant retries automatically with
backoff instead of demanding reauth.

Rejected: upstream magico13/ha-emporia-vue PR #467's fix, which changes the
generic `except Exception` to always raise `ConfigEntryNotReady` (with no
narrower case for auth failures at all). Since pyemvue's `login()` still
returns `True`/`False` rather than always raising, PR #467's `except
Exception` block is never reached for the ordinary bad-password case, so in
practice it isn't a regression in that codebase either — but it also does
nothing to positively assert "genuine auth failure still means auth failure"
if pyemvue's exception-swallowing behavior ever changes. This fork's
`_is_cognito_not_authorized` check is that explicit, testable statement of
the distinction.
