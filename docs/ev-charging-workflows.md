# EV charging: Emporia app vs. this integration, and how to improve the workflow

Scope: the two things actually used day to day — enabling/disabling charging and
setting the charge current in amps — plus everything Emporia's own app and cloud
offer around them. Written 2026-09-20.

## 1. What Emporia officially supports

Emporia's position on programmatic access, stated in their help center
("Developer Access & Future Integrations", updated 2026-03-13):

- **No public API.** "An official API remains a long-term goal, and we continue
  to evaluate options." PyEmVue and this integration are explicitly
  unsupported-but-acknowledged community work.
- **No local API.** "The devices do not expose a local API or direct network
  interface — all data flows through the Emporia cloud," with "no immediate
  plans" to change that.
- **No timeline** for Matter, HomeKit, OCPP, Home Assistant, IFTTT, SmartThings,
  Hubitat or ioBroker.
- **SmartCar EV integration discontinued** February 2024 (app v3.8.0). Accounts
  that already had a vehicle linked keep it; new links are not offered. This is
  why `customers/vehicles` still exists in the API but is empty for most accounts.

Practical consequence: every control path — app, this integration, Alexa — is a
round trip through `api.emporiaenergy.com`. There is no local fallback short of
[ESPHome firmware for the Vue](https://emporia-vue-local.github.io/), which
applies to the energy monitor only (not the EVSE) and voids the warranty plus
app/cloud features.

### App features that have no API equivalent

| App feature | What it does | Reachable from HA? |
| --- | --- | --- |
| Real-Time Charge Rate slider | 1 A steps, 6 A up to the configured max (40 A NEMA 14-50 / 48 A hardwired) | **Yes** — `number` entity / `set_charger_current` |
| On/off ("ready to charge" vs paused) | Gates charging when a car is plugged in | **Yes** — `switch` entity |
| Maximum Charge Rate (breaker size) | Permanent ceiling, PIN-protected, set at install | Read-only (`maxChargingRate`); the PUT body carries `breakerPIN` but PyEmVue never sets it |
| Smart Saver | Derives off-peak windows from your utility rate plan; default with no plan is weekdays 20:00–08:00, weekends always on | **No** — `offPeakSchedulesEnabled` is read by PyEmVue but never written |
| Custom Schedules | Explicit on/off events, repeat rules; an "on" with no matching "off" never turns back off | **No** — `custom_schedules` is parsed as an empty stub ("don't have support for schedules yet") |
| Excess Solar + Solar Window | Cloud-side ramping of charge rate to track surplus solar, optionally restricted to chosen hours; requires charger and monitor in the same Home | **No** — and it *competes* with HA automations (see §3) |
| PowerSmart load management | Throttles charging to keep the panel under its limit, using the paired Vue 3 | **No** — cloud-side only, invisible to HA |
| Charge-complete notification | Fires when draw falls below a threshold | Replaceable in HA from the status sensor |
| Session history / cost | Per-session energy and cost, filterable | Partially — HA has per-circuit energy, not per-session |
| Access control | Restricts who can start a charge | **No** |

## 2. What this integration exposes today

From `number.py`, `switch.py`, `charger_entity.py`, `sensor.py`:

- `number` **Current Limit** — 6 A to `max_charging_rate`, 1 A steps, optimistic
  value held until a refresh agrees (see [protocol.md](protocol.md)).
- `switch` — charger on/off, preserving the current charge rate.
- `sensor` **Status** — `Disconnected` / `Connected` / `Charging` / `Error`, with
  an IEC 61851 code attribute, mapped in `_map_charger_state`.
- `sensor` **EV Charge Time Needed** — only when a `vehicle_soc_sensor` option
  points at some *other* integration's SoC sensor.
- Action `emporia_vue.set_charger_current` — sets amps and on/off in one call.
- Four EV blueprints (solar excess, departure readiness, pre-sunset ramp,
  travel prompt).

Polling: `VueDeviceStatusCoordinator` runs on a 1-minute interval, and writes are
eventually consistent, so a manual change in the Emporia app takes up to a minute
to appear in HA.

## 3. The real workflow problem: three controllers, one charger

Emporia's cloud features and an HA automation are not aware of each other. All of
these write the same `chargingRate`/`chargerOn` fields:

1. Smart Saver / custom schedules (cloud, time-driven)
2. Excess Solar (cloud, ramps the rate every few minutes)
3. PowerSmart load management (cloud, panel-limit driven)
4. Home Assistant (this integration)

If Excess Solar is enabled on the charger **and** the bundled
`ev_solar_excess_charging.yaml` blueprint is running, the two fight: the cloud
ramps the rate toward its own estimate of surplus, HA overwrites it a minute
later from the Grid Export sensor, each reading the other's output as new state.
The same applies to Smart Saver turning the charger on at 20:00 against an HA
automation that wants it off.

**Recommendation: pick one controller per behavior.** If the charging policy
lives in HA, turn off Excess Solar and Smart Saver/custom schedules in the app
and leave only PowerSmart (a hardware safety limit, not a policy) enabled. If the
policy lives in Emporia, use HA for observation and manual override only.

There is also an unverified risk worth checking on a live account:
`ChargerDevice.as_dictionary()` sends only `deviceGid`, `loadGid`, `chargerOn`,
`chargingRate`, `maxChargingRate` (plus `breakerPIN` when set). It does **not**
echo `offPeakSchedulesEnabled`. If the API treats the PUT as a full replace, then
every write from HA silently disables off-peak scheduling. Test: enable Smart
Saver in the app, write a current from HA, re-check the app.

## 4. Improvements, ranked

### A. Make the charge-rate ceiling dynamic (small, fixes a real bug)
`EmporiaChargerCurrentNumber.__init__` freezes `_attr_native_max_value` from
`device.ev_charger.max_charging_rate` captured at setup. If the breaker size is
changed in the app, or PowerSmart reports a lower ceiling, the slider keeps the
stale bound until HA restarts. Make `native_max_value` a property reading the
coordinator's live `ChargerDevice.max_charging_rate`, falling back to the setup
value.

### B. Surface the cloud policy state so HA can defer to it (small)
`offPeakSchedulesEnabled` is already in every status poll and is thrown away. Add
a diagnostic `binary_sensor` (Off-Peak Schedule Active) and put it in the
charger's attributes. Automations can then condition on it instead of racing it,
and §3's conflict becomes visible rather than mysterious.

### C. Confirm writes instead of trusting them (medium)
Writes are eventually consistent and the status coordinator polls once a minute,
so a failed or rejected write looks identical to a slow one for up to 60 s. After
`update_charger`, schedule two or three fast follow-up refreshes (≈5 s, 15 s,
30 s) and log/raise if the reported `chargingRate` still disagrees. This also
tightens the feedback loop for any ramping automation.

### D. Ramp helper instead of raw amp writes (medium, biggest workflow win)
Every current blueprint does the same arithmetic inline: take a power sensor,
divide by volts, clamp to 6–max, avoid flapping. Ship that once as an action —
`emporia_vue.track_power` with `power_entity`, `voltage`, `min_current`,
`deadband`, `min_dwell` — that computes the target amps, applies hysteresis and a
minimum dwell time, and turns the charger **off** rather than writing below 6 A
(J1772 has no legal pilot below 6 A, so "charge at 3 A" silently means "charge at
6 A" or a fault). Blueprints shrink to a trigger plus one call, and the
anti-flapping logic stops being copy-pasted per user.

### E. Vehicle entities, where the account still has one (medium)
PyEmVue exposes `get_vehicles()` and `get_vehicle_status()` →
`batteryLevel`, `batteryRange`, `chargingState`, `chargeLimitPercent`,
`minutesToFullCharge`, `chargeCurrentRequest`, `chargeCurrentRequestMax`. That is
exactly what `vehicle_soc_sensor` currently asks the user to supply from another
integration, and `minutesToFullCharge` is a better departure-readiness input than
the kWh estimate in `EmporiaEVChargeTimeNeededSensor`.

Caveat: SmartCar was discontinued in Feb 2024, so this only populates for
accounts that linked a car before then. Build it as best-effort — create the
entities when `get_vehicles()` returns rows, skip silently otherwise, and keep
the `vehicle_soc_sensor` option as the fallback.

### F. Per-session energy (medium)
The app's session view has no API equivalent, but HA can reconstruct it: latch a
session start on Status → `Charging`, end on `Connected`/`Disconnected`, and
integrate the charger circuit's power over that window. Gives "energy delivered
last session" and "session cost" without Emporia.

### G. Departure target instead of a time-of-day ramp (larger)
The current blueprints are reactive (follow solar, ramp at sunset). The workflow
people actually want is declarative: *"85% by 07:00, cheapest/greenest way to get
there."* With C, D and E in place that is one scheduler: compute kWh needed,
compute hours available, prefer surplus solar, fall back to off-peak grid, and
force full rate only when the remaining time no longer covers the deficit —
which is what `ev_departure_readiness.yaml` approximates today with range sensors
and a manual overhead buffer.

### Not worth pursuing
- **Local control of the EVSE** — does not exist, and Emporia says it is not planned.
- **Writing schedules via the API** — the schedule payload shape is unknown, PyEmVue
  stubs it out, and §3's recommendation is to keep policy in one place anyway. If HA
  owns the policy, HA's own schedule helpers are the better tool.
- **OCPP** — no timeline from Emporia.

## 5. Open item: the app binary

The APK linked for this analysis
(`cm-aptoide-pt-12060-...apk?apk_name=emporia-energy`) is the **Aptoide store
client** (`cm.aptoide.pt`), not the Emporia app — the `apk_name` query parameter
is a label on the mirror's own installer. Nothing in it relates to Emporia;
confirmed from `AndroidManifest.xml`/`resources.arsc` and a strings sweep of all
three dex files (zero hits for `emporia`, `amazonaws` or `cognito`).

So the app-side rows in §1 come from Emporia's published help center, not from
the binary. A genuine `com.emporiaenergy.*` APK would let us confirm the parts
the help center does not document: the schedule payload shape (§4 "not worth
pursuing" may be wrong if it turns out simple), the Excess Solar ramp cadence and
hysteresis, whether the charger PUT is a full replace (§3), and whether
`breakerPIN` gates anything else.
