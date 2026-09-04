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

