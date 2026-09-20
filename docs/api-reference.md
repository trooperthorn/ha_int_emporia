# Emporia cloud API reference

A catalogue of every Emporia cloud endpoint this project knows about, what it
returns, and whether this integration uses it. Written so a future reader does
not have to re-derive the surface from scratch.

Emporia publishes no public API. Their own help centre states that "Emporia
Energy does not currently offer a public API for direct programmatic access to
device or account data", that an official API "remains a long-term goal", and
that "direct local access to Emporia device data is not available on any
Emporia product" — no device exposes a local API or network interface. Home
Assistant is named explicitly as an unsupported platform. Everything below is
therefore undocumented surface that can change without notice. Treat it as a
map, not a contract.

Last verified: 2026-09-20, against a live account with two Vue 2 monitors and
one Classic EV charger, using [`scripts/api_probe.py`](../scripts/api_probe.py).
App version 5.1.0 (versionCode 11308), PyEmVue 0.18.9, `emporia-mcp` 0.2.1.

## The headline finding

`GET /customers/devices/status` — the **legacy** endpoint this integration
already polls every 60 seconds — returns a top-level `loads` array that
PyEmVue parses out of the response and discards:

```json
"loads": [
  {
    "loadGid": 225125,
    "schedulesEnabled": false,
    "nextScheduledEventText": null,
    "peakDemandEnabled": false,
    "excessGenerationEnabled": true,
    "energyManagementText": "Charging with Excess Solar since 10:17 am.",
    "energyManagementOverridden": false,
    "warningText": null
  }
]
```

That is the answer to "which cloud feature currently owns the charging rate,
and has it been overridden" — already on the wire, already paid for, on an
endpoint the integration calls today. `pyemvue.get_devices_status()` reads
`evChargers`, `outlets` and `devicesConnected` from this response and ignores
`loads` entirely.

The same capture confirms **why a written setpoint does not stick**: with
Excess Solar active, `evChargers[0].chargingRate` read **35** against a
`maxChargingRate` of **40**. Emporia's cloud writes the same `chargingRate`
field the integration's `number` entity writes. They are not two settings that
coexist; they are one field with two writers. Within a single probe run
(~90 seconds) the cloud moved the rate 35 → 33 → 35, so the contention is
continuous, not occasional.

### The override is visible there too

A before/after capture across the app's **Manage Charging → "Charge at full
power"** confirms that a manual override is fully observable on the same
legacy response. Nothing else on the account changed between the two runs.

| Field | Before | After |
| --- | --- | --- |
| `loads[].energyManagementOverridden` | `false` | **`true`** |
| `loads[].energyManagementText` | `"Charging with Excess Solar since 10:17 am."` | `"Charging with Excess Solar since 10:17 am.\nOverridden from 11:40 am to 2:40 pm."` |
| `evChargers[].chargingRate` | `35` | `40` |
| `evChargers[].chargerOn` | `true` | `true` (unchanged) |

So "Charge at full power" is a **three-hour time-boxed override** that releases
the rate to `maxChargingRate` and hands control back afterwards. The window is
Emporia's default, not something the app asked for.

Everything an integration needs to *see* an override — that one is active, what
it suppressed, and when it lapses — is therefore available on the legacy host
with no new request. Only *setting* one still needs an uncaptured write.

## Verification legend

| Level | Meaning |
| --- | --- |
| **V-live** | Observed on a real account on 2026-09-20. Path, method, status, required parameters and response fields are all real. |
| **V-lib** | Read from [PyEmVue](https://github.com/magico13/PyEmVue) 0.18.9 source. |
| **V-mcp** | Read from Emporia's own open-source MCP server, [`emporiaenergy/emporia-mcp`](https://github.com/emporiaenergy/emporia-mcp) (BETA). Note that its TypeScript types are in places **narrower than the real responses** — see [EVSE sessions](#evse-sessions--v1devicesevsessessions). |
| **B-apk** | Path string extracted from the Android app binary (Flutter, `lib/arm64-v8a/libapp.so`). Path is certain; method and payload are not. Not yet probed. |

## Hosts

| Host | Role | Level |
| --- | --- | --- |
| `https://api.emporiaenergy.com` | Legacy API. Everything PyEmVue — and therefore this integration — talks to. | V-live |
| `https://c-api.emporiaenergy.com` | Modern `/v1/...` API. What the current app and Emporia's MCP server use. This integration does not touch it. | V-live |
| `https://auth.emporiaenergy.com` | OAuth2 authorize/token. | B-apk |
| `https://mcp.emporiaenergy.com` | Emporia's hosted MCP endpoint (`/sse`, `/streamable`). | V-mcp |
| `https://cognito-idp.us-east-2.amazonaws.com/` | AWS Cognito user pool. Both the app and PyEmVue authenticate here. | V-lib, V-mcp |
| `https://s3.amazonaws.com/.../maintenance/maintenance.json` | Maintenance banner. Unauthenticated. | V-lib |
| `dev3api`, `dev3-c-api`, `dev3-auth.emporiaenergy.com` | Emporia development environment. Present in the shipping binary; do not use. | B-apk |

**Auth differs per host** (V-live, confirmed by a working probe against both):
the Cognito **id token** goes in an `AuthToken` header on the legacy host, and
in a bare `Authorization` header — **no `Bearer` prefix** — on `c-api`.
Cognito client id `4qte47jbstod8apnfic0bunmrq`. Emporia's MCP server notes it
"will not work if you've created an Emporia Account using Google or Apple",
the same limitation behind this integration's token-paste auth path.

Error shapes differ too. Legacy returns `{"message": "..."}` or `text/plain`;
`c-api` returns `{"error_code", "error_message", "user_message"}`. Legacy 4xx
messages **echo the authenticated identity back** (`{identity={sourceIp=...,
email=...}}`), which is why the probe scrubs values, not just keys.

---

## Legacy API — `api.emporiaenergy.com`

"Used here" means this integration calls the PyEmVue method.

| Endpoint | Verb | PyEmVue method | Used here | Live result | Purpose |
| --- | --- | --- | --- | --- | --- |
| `/customers?email={email}` | GET | `get_customer_details` | No | **200** | Customer record. `customerGid`, `email`, `firstName`, `lastName`, `createdAt`, `actor`. |
| `/customers/devices` | GET | `get_devices` | **Yes** | **200** | Device inventory. The integration's whole device/channel model. |
| `/customers/devices/status` | GET | `get_devices_status` | **Yes** | **200** | Outlet + charger state, connectivity, **and `loads[]`**. Polled every 60 s. |
| `/devices/{deviceGid}/locationProperties` | GET | `populate_device_properties` | No | **200** | Timezone, utility rate, billing day, lat/long, `primaryVehicleBrand`. |
| `/devices/channels` | PUT | `update_channel` | No | not probed (write) | Rename or retype a CT channel. |
| `/devices/channels/channeltypes` | GET | `get_channel_types` | No | **200** | 28 channel types. Confirms gid **13 = Solar/Generation** and gid **25 = EV Charger**. |
| `/AppAPI?apiMethod=getDeviceListUsages&...` | GET | `get_device_list_usage` | **Yes** | not probed | All power and energy sensors at every scale. |
| `/AppAPI?apiMethod=getChartUsage&...` | GET | `get_chart_usage` | No | not probed | Time series for one channel over a range. |
| `/devices/outlet` | PUT | `update_outlet` | **Yes** | not probed (write) | Smart plug on/off. |
| `/devices/evcharger` | PUT | `update_charger` | **Yes** | not probed (write) | Charger on/off + charging rate. The only charger write this integration makes. |
| `/customers/vehicles` | GET | `get_vehicles` | No | **200 → `[]`** | **Confirmed dead.** See [Vehicles](#vehicles--legacy). |
| `/vehicles/v2/settings?vehicleGid=` | GET | `get_vehicle_status` | No | unreachable (no vehicles) | Vehicle SoC/range/charge limit. |
| `/customers/timeofuse` | GET | — | No | **200** | **Works.** Full TOU rate plan and Smart Saver schedules. See [Time of use](#time-of-use--customerstimeofuse). |
| `/customers/derms` | GET | — | No | **200 → `[]`** | Demand-response enrolment. |
| `/customers/loadmanagement` | GET | — | No | **400** | Requires `loadGid`. |
| `/devices/schedule` | GET | — | No | **400** | Requires `loadGid`. |
| `/devices/firmwareuptodate` | GET | — | No | **400** | Requires `deviceGids`. |
| `/devices/evcharger/maxchargingrate` | — | — | No | **404 on GET** | Write-only; the breaker max is PUT, not GET. |
| `/customers/devices/channels` | GET | — | No | **404** | **Does not exist on the legacy host.** This is a `/v1` path only. |

Not probed, from the app binary (B-apk): `/devices/configuremains`,
`/devices/usage/export`, `/devices/battery/schedule`, `/devices/battery/faults`,
`/customersToDevices`, `/customersToDevices/manual`, `/customers/termsofservice`.

### Fields PyEmVue drops

The live `/customers/devices` and `/customers/devices/status` responses carry
more than `pyemvue.device` models:

- **`loads[]`** on `/customers/devices/status` — the entire array (see above).
- `evChargers[].loadManagementEnabled` and `evChargers[].hideChargeRateSliderText`.
  The app uses the latter to hide the rate slider when a controller owns the rate.
- `channels[].channelId` (`"Mains"`, `"Branch_7"`) and `channels[].mergedChannelId`.
  `channelId` is the identifier the whole `/v1` API keys on; PyEmVue exposes only
  `channelNum`.
- `devices[].combinedVue`, `devices[].primaryVueGid`.
- `locationProperties.locationInformation.primaryVehicleBrand`.
- On channel types: `directionality` (`CONSUMPTION_ONLY` / `GENERATION_ONLY` /
  `BIDIRECTIONAL`) and `allowsBidirectional`. A real signal for the solar-invert
  option, which this integration currently handles with a manual checkbox.

---

## Modern API — `c-api.emporiaenergy.com/v1`

**This integration uses none of it.** Required parameters below are quoted from
the live 400 responses.

### Account, sites and devices

| Endpoint | Live result | Notes |
| --- | --- | --- |
| `/v1/customers` | **200** | Adds `user_type`, `email_marketing_status`, `can_toggle_email_marketing` over the MCP type. |
| `/v1/customers/sites` | **200 → `{"sites": []}`** | This account has no sites, so legacy device enumeration is not losing anything *here*. |
| `/v1/customers/devices` | **200** | Site-aware inventory. Carries `energy_managements_configured` and `supported_energy_managements` per device. |
| `/v1/customers/devices/status` | **200** | Live status by category. The `evses[]` entry is the richest charger view available — see below. |
| `/v1/customers/devices/channels` | **200** | Nested channel tree keyed on `channel_id`, with `sub_type` giving the channel type as a display string. |
| `/v1/customers/sites/members` | **405** | `"Request method 'GET' is not supported"` — write-only. |
| `/v1/customers/devices/settings` | **405** | Write-only. |
| `/v1/customers/devices/third-party-access` | **400** | Requires `device_id`. |
| `/v1/customers/app-preferences` | **406** | `"No acceptable representation"` — needs a specific `Accept` header. |
| `/v1/customers/homepage/summary` | **400** | Requires `device_ids`. |
| `/v1/customers/homepage/monitor-card` | **400** | Requires `device_ids`. |
| `/v1/customers/devices/savings` | **400** | Requires `timezone`. |
| `/v1/customers/devices/usages` | **400** | Requires **`device_gids`** — numeric gids, not the `device_ids` serials every other v1 endpoint takes. |

### Energy management — the four controllers

| Endpoint | Live result | Response / requirement |
| --- | --- | --- |
| **`/v1/customers/devices/override`** | **400** | Requires **`device_id`**. The override read exists and is per-device; re-probe with the parameter to capture its shape. |
| `/v1/customers/excess-generation` | **200** | `{"monitors": [{"device_id", "enabled"}], "unavailable_monitors": ["..."]}`. **This is the Excess Solar on/off state.** |
| `/v1/customers/energy-monitor/excess-generation` | **400** | Requires `device_id`. The per-monitor read; `postExcessGenerationMonitor` is the write. |
| `/v1/customers/power-smart` | **200** | `{"monitors": [{"monitor_gid", "enabled"}]}`. Note `monitor_gid` (numeric) here versus `device_id` (serial) on excess-generation — the v1 API is not internally consistent. |
| `/v1/customers/energy-monitor/power-smart` | **500** | Missing `device_id` produces an *Internal Server Error* rather than a 400. Server-side bug; pass the parameter. |
| `/v1/customers/peak-demand` | **200** | `{"peak_demands": [{"device_id", "enabled", "demand_goal_kwatts"}]}`. |
| `/v1/customers/energy-monitor/peak-demand` | **400** | Requires `device_id`. |
| `/v1/customers/load-sharing` | **400** | Requires `load_sharing_gid`. |
| `/v1/customers/load-sharings` | **200 → `{"networks": []}`** | The list form; use it to discover gids for the singular endpoint. |
| `/v1/customers/derms` | **200 → `{"enrollment_statuses": []}`** | |
| `/v1/derms/devices` | **400** | Requires `device_ids`. |

### EV charging

| Endpoint | Live result | Notes |
| --- | --- | --- |
| `/v1/devices/evses` | **200** | Params `device_ids`, `start`, `end`. The full charger record. |
| `/v1/devices/evses/sessions` | **200** | Params `device_ids`, `start`, `end`. Per-session history with **start/stop reasons**. |
| `/v1/customers/ev-charging-report` | **200** | Params `device_id`, `start`, `end`. Cost against the account's real rate plan. |
| `/v1/customers/evse/charging-history` | **400** | Requires **`device_id`** singular, not `device_ids`. |
| `/v1/vehicles/brands` | **200** | 40 strings, for the cosmetic "Vehicle Make" field. |

### Rates and misc

| Endpoint | Live result |
| --- | --- |
| `/v1/customers/rate-analysis` | **200 → `{"device_results": []}`** |
| `/v1/customers/recommendations` | **200** — array of `{recommendation_gid, title, body, call_to_action, call_to_action_data, device_id, site_gid, status, created_at}`. `call_to_action` values seen: `ENROLL_IN_DERMS_PROGRAM`, `SET_SOLAR_BUYBACK_RATE`. |
| `/v1/utility-rates` | **400** — `"At least one of either eiaid or utilityCompanyGid must be provided"` |
| `/v1/devices/utility-rates` | **400** — `"Only one of device_id, device_gid, load_gid or postal_code should be specified"` |

Not probed (B-apk): `/v1/customers/stream` (likely SSE), `/v1/migrated/app-api/chart-usage`,
`/v1/devices/merged-channel`, `/v1/devices/fifty-amp-bidirectionality`,
`/v1/customers/rep/*`, `/v1/battery/install-inquiry`, `/v1/proxy/places`,
`/v1/customers/energy-monitor/combine`, `/v1/auth/grant`, `/v1/third-party`,
`/v1/customers/intercom-token`, `/v1/customers/notifications/token`,
`/v1/devices/{type}/energy` and `/power`, `/v1/devices/batteries/state-of-charge`.

---

## Data formats

### Charger object — legacy `/customers/devices/status` → `evChargers[]`

V-live.

```json
{
  "deviceGid": 371548,
  "loadGid": 225125,
  "message": "Charging",
  "status": "Charging",
  "icon": "CarConnected",
  "iconLabel": "On",
  "debugCode": "411",
  "iconDetailText": null,
  "faultText": null,
  "proControlCode": null,
  "breakerPIN": "…",
  "chargerOn": true,
  "chargingRate": 35,
  "maxChargingRate": 40,
  "loadManagementEnabled": false,
  "hideChargeRateSliderText": null
}
```

`chargingRate` is **not** a user setting the cloud leaves alone — it read 35
against a 40 ceiling while Excess Solar was driving. `update_charger` PUTs this
same field back.

`status` and `message` are free text. `sensor.py::_map_charger_state` maps them
to a four-state enum plus an IEC 61851 code.

### Load status — legacy `/customers/devices/status` → `loads[]`

V-live. **Not modelled by PyEmVue.** One entry per controllable load, keyed by
`loadGid` (which `evChargers[]` and `outlets[]` both carry).

| Field | Type | Meaning |
| --- | --- | --- |
| `loadGid` | int | Join key to the charger/outlet. |
| `schedulesEnabled` | bool | A schedule (Smart Saver or custom) is active. |
| `nextScheduledEventText` | string \| null | Human text for the next scheduled change. |
| `peakDemandEnabled` | bool | Peak Demand Management is on for this load. |
| `excessGenerationEnabled` | bool | Excess Solar is on for this load. |
| `energyManagementText` | string \| null | e.g. `"Charging with Excess Solar since 10:17 am."` |
| `energyManagementOverridden` | bool | **A manual override is currently suppressing the controller.** Verified to flip `false` → `true` on the app's "Charge at full power". |
| `warningText` | string \| null | e.g. meter-disconnect warnings. |

`energyManagementText` is prose and multi-line. Observed forms:

```
Charging with Excess Solar since 10:17 am.
Charging with Excess Solar since 10:17 am.\nOverridden from 11:40 am to 2:40 pm.
```

The override window appears **only here**, not in any structured field on
either host. Surfacing the string verbatim as an attribute is safe; parsing
times out of it is not, and would break the moment Emporia rewords it or the
account locale changes.

### EVSE detail — v1 `/v1/devices/evses`

V-live. Params `device_ids` (comma-joined), `start`, `end`.

```json
{
  "success": [{
    "device_id": "D…", "category": "EVSE", "name": "…",
    "device_description": null, "connected": true,
    "connected_changed_time": "2026-09-15T18:29:39.486Z",
    "time_zone": "America/Chicago", "billing_cycle_start_day": 13,
    "firmware": "EVCharger-812",
    "parent_device_id": "A…", "parent_channel_num": 10,
    "excess_solar_configured": true,
    "peak_demand_configured": false,
    "energy_management_active": "EXCESS_SOLAR",
    "energy_managements_configured": ["EXCESS_SOLAR"],
    "energy_managements_active": ["EXCESS_SOLAR"],
    "evse_product": "C1", "evse_model": "Classic",
    "evse_branding": "EMPORIA", "evse_power_source": "NEMA",
    "evse_color": "WHITE", "evse_gun_type": "J1772",
    "breaker_pin": "…",
    "charger_on": true,
    "charge_rate_amps": 35,
    "max_charge_rate_amps": 40,
    "power_smart_upgraded": false,
    "power_smart_configuration": null,
    "vehicle_connected": true,
    "vehicle_charging": true,
    "partner_schedule_status": "INELIGIBLE"
  }],
  "error": []
}
```

`vehicle_connected` and `vehicle_charging` are separate booleans — a cleaner
plugged-in signal than inferring it from `status`/`message` strings.

**Do not trust `parent_channel_num` from this endpoint.** It reported `10`
while legacy `/customers/devices` reported `parentChannelNum: "7"` and
`/v1/customers/devices` reported `parent_channel_id: "Branch_7"` for the same
charger. Two of three agree; this one does not.

### EVSE live status — v1 `/v1/customers/devices/status` → `evses[]`

V-live.

```json
{
  "active_energy_managements": [
    { "type": "EXCESS_SOLAR", "active_since": "2026-09-20T15:17:10.855122Z" }
  ],
  "overridden_energy_managements": [],
  "next_scheduled_event": null,
  "connected": true, "offline_since": null,
  "device_id": "D…", "device_gid": 371548, "load_gid": 225125,
  "charger_status": "CHARGING",
  "charging_rate": 35,
  "max_charging_rate": 40,
  "temperature_derated": false,
  "override": null
}
```

`charger_status` is a clean uppercase enum here, unlike the legacy free-text
`status`/`message` pair. `active_since` gives the controller's start time as a
real timestamp rather than the legacy prose.

**Under an override** (V-live, captured across the app's "Charge at full
power"), the controller moves between the two arrays:

```json
"active_energy_managements": [],
"overridden_energy_managements": [
  { "type": "EXCESS_SOLAR", "override_expires_at": null }
],
"charging_rate": 40
```

and on `/v1/devices/evses`, `energy_management_active` flips from
`"EXCESS_SOLAR"` to `"NONE"` with `energy_managements_active: []`.

Two cautions:

- **`override_expires_at` was `null` while an override was demonstrably
  running** with a 2:40 pm expiry. The expiry exists only in the legacy
  `energyManagementText` prose. Do not treat this field as the source of truth
  for when an override lapses; today it is not one.
- `override` stayed `null` in both captures, including while overridden. Its
  populated shape remains uncaptured, and it may only be set by a different
  override type than the one the app's button creates.

Together these make the **legacy `loads[]` array the better source** for
override state, which is convenient: it is the endpoint already polled.

### Device inventory — v1 `/v1/customers/devices`

V-live. Every device carries:

- `energy_managements_configured` — what is switched on. Observed:
  `["EXCESS_SOLAR"]` on the charger and on the solar-side monitor, `[]` on the other.
- `supported_energy_managements` — what the hardware could do. Observed on the
  charger: `["LOAD_SHARING", "POWER_SMART", "PEAK_DEMAND", "EXCESS_SOLAR"]`;
  on a Vue 2 monitor: `["POWER_SMART", "EXCESS_SOLAR", "PEAK_DEMAND"]`.

EV chargers additionally carry `load_gid`, `evse_model`, `evse_branding`,
`evse_power_source`, `evse_color`, `evse_gun_type`, `invite2charge_code`,
`breaker_pin`, `primary_vehicle_brand`, `power_smart_monitor_device_id`,
`power_smart_upgraded`.

Monitor channels carry `channel_id`, `parent_channel_id`, `multiplier`,
`channel_type_gid`, `channel_classification` (`MAIN` / `FIFTY_AMP`), and
**`has_data`** — which flags unconfigured CT channels directly. The integration
currently infers that.

Note the v1 mains split: `Mains` with children `Mains_A`, `Mains_B`, `Mains_C`.
On a split-phase install `Mains_C` reports `has_data: false`. This is a real
per-leg breakdown the legacy `"1,2,3"` combined channel does not expose.

### EVSE sessions — v1 `/v1/devices/evses/sessions`

V-live, and **richer than Emporia's own MCP TypeScript types**, which omit
`session_id`, `start_reason` and `end_reason`.

```json
{
  "success": [{
    "device_id": "D…",
    "sessions": [{
      "session_id": "NDEzMDQ5MTE=",
      "plug_in": "2026-09-13T17:21:02Z",
      "plug_out": "2026-09-14T12:04:44Z",
      "sessions": [{
        "session_id": "ODQ0NTQ4NDY=",
        "interval": { "start": "…", "end": "…" },
        "energy_kwhs": 1.7912786745352551,
        "start_reason": "ExcessSolar",
        "end_reason": "ExcessSolar"
      }]
    }]
  }],
  "error": []
}
```

Two levels: a **plug-in session** containing zero or more **charging sessions**.
`session_id` is base64. `start_reason` / `end_reason` observed: `ExcessSolar`.
The app additionally surfaces "Automatic at plug-in" and "Vehicle unplugged",
so expect more values.

Under Excess Solar the charging sessions are numerous and short — one plug-in
session produced dozens of sub-sessions of one to twenty minutes as cloud
control cycled the charger. Any HA session sensor must aggregate at the
**plug-in** level, not the charging-session level, or it will flap.

### EV charging report — v1 `/v1/customers/ev-charging-report`

V-live. Params `device_id`, `start`, `end`. Adds per-session **cost** on top of
the sessions endpoint, plus `daily_charging_totals[]` and a prose
`report_description` naming the account's actual rate plan. `savings` was
`null` and `potential_savings` `0.0` throughout — those likely need a TOU plan
or Smart Saver enabled to populate.

Note the interval echo came back shifted by the account timezone: a requested
`…T16:36:34Z` window was echoed as `…T21:36:34Z`.

### Time of use — legacy `/customers/timeofuse`

V-live. Carries both the rate plan and the Smart Saver schedules.

`timeOfUseManagement.touDevices[]`: `loadGid`, `touSchedulesEnabled`,
`deviceName`, `loadType` (`"EVCharger"`), `touRatePlan`
(`utilityRateGid`, `utilityRateName`, `weekdayPeriods[]`, `weekendPeriods[]`
with `ratePeriod` ∈ `OffPeak`/`Peak`/`FlatRate`, `cost`, and
`intervalStart`/`intervalEnd` as `{hour, minute, second, nano}`).

`timeOfUseManagement.touSchedules[]`: `scheduleGid`, `loadGid`, `deviceOn`,
`enabled`, `anchor` (`"MIDNIGHT"`), `offsetFromAnchor` (`"08:00:00"`),
`daysOfWeek[]`, `readOnly`, `dermsProgramGid`.

A gotcha worth knowing: schedule entries can read `enabled: true` while the
device-level `touSchedulesEnabled` is `false`. The device-level flag is the one
that decides whether they run.

### Device object — legacy `/customers/devices`

V-lib plus V-live. `deviceGid`, `manufacturerDeviceId`, `model`, `firmware`,
`parentDeviceGid`, `parentChannelNum`, `locationProperties`, `outlet`,
`evCharger`, `battery`, `deviceConnected`, `devices[]` (nested), `channels[]`,
`combinedVue`, `primaryVueGid`.

Channel fields: `deviceGid`, `name`, `channelNum`, `channelMultiplier`,
`channelTypeGid`, `parentChannelNum`, `type`, `channelId`, `mergedChannelId`.

### Usage — legacy `getDeviceListUsages`

V-lib. Per device: `deviceGid` plus `channelUsages[]` with `name`, `deviceGid`,
`channelNum`, `usage`, `percentage`, optional `nestedDevices[]`; plus an
`instant` timestamp. No per-device error channel — a partial response is
indistinguishable from a real zero, which is why the coordinators carry
duplicate-sample and last-known-good guards.

### Vehicles — legacy

**Confirmed dead on this account: `GET /customers/vehicles` → `200 []`.**
Emporia's help centre states the SmartCar EV integration ended in February 2024
(app 3.8.0), with access retained only for vehicles added before then. The
charger exposes only a cosmetic `primary_vehicle_brand`. Do not build the
integration's vehicle-SoC feature on this path.

For reference, the shape PyEmVue still models: `/customers/vehicles` →
`vehicleGid`, `vendor`, `apiId`, `displayName`, `loadGid`, `make`, `model`,
`year`; `/vehicles/v2/settings?vehicleGid=` → a `settings` object with
`vehicleState`, `batteryLevel`, `batteryRange`, `chargingState`,
`chargeLimitPercent`, `minutesToFullCharge`, `chargeCurrentRequest`,
`chargeCurrentRequestMax`.

---

## What this integration does not use

| Gap | Where it lives | Cost to close |
| --- | --- | --- |
| **Which controller owns the rate** | `loads[].energyManagementText`, `.excessGenerationEnabled`, `.peakDemandEnabled`, `.schedulesEnabled` | **None.** Already in a response polled every 60 s; PyEmVue discards it. |
| **Whether an override is active, and its window** | `loads[].energyManagementOverridden`, `.energyManagementText` | **None.** Same response. Verified end to end against the app's "Charge at full power". |
| **Next scheduled change** | `loads[].nextScheduledEventText` | **None.** Same response. |
| Load-management flag, slider-suppression hint | `evChargers[].loadManagementEnabled`, `.hideChargeRateSliderText` | None — same response, PyEmVue model change only. |
| Clean charger status enum, controller start time | v1 `evses[].charger_status`, `.active_energy_managements[].active_since` | v1 client. |
| Plugged-in / charging as booleans | v1 `evses[].vehicle_connected`, `.vehicle_charging` | v1 client. |
| Session history with start/stop reasons | `/v1/devices/evses/sessions` | v1 client. |
| Per-session cost against the real rate plan | `/v1/customers/ev-charging-report` | v1 client. |
| Setting an override | `/v1/customers/devices/override` (needs `device_id`) | v1 client **plus** an uncaptured write contract. |
| Turning Excess Solar off | `/v1/customers/energy-monitor/excess-generation` | v1 client plus an uncaptured write contract. |
| Schedules / Smart Saver | legacy `/customers/timeofuse` (GET works today) | Low — legacy host, no v1 needed. |
| Per-leg mains (`Mains_A/B/C`) | v1 `/v1/customers/devices` channels | v1 client. |
| Unconfigured-channel detection | v1 `channels[].has_data` | v1 client. Currently inferred. |
| Channel directionality for solar invert | legacy `/devices/channels/channeltypes` `directionality` | None — one extra call at setup, replaces a manual checkbox. |
| Breaker / max charge rate write | `/devices/evcharger/maxchargingrate` (PUT) | Uncaptured write contract. PIN-gated. |
| Push instead of 60 s polling | `/v1/customers/stream` | Unprobed. |

## Still unknown

Override **visibility** is settled (see above). What remains is three write
contracts, none of which a GET-only probe can capture:

1. **`POST /v1/customers/devices/override`** — the request body. The endpoint
   requires `device_id`; how the duration is expressed (or whether three hours
   is fixed server-side) is unknown.
2. **`POST /v1/customers/energy-monitor/excess-generation`** — the durable
   Excess Solar enable/disable.
3. **`PUT /devices/evcharger/maxchargingrate`** — breaker max, PIN-gated.

Also open: whether `PUT /devices/evcharger` with a `chargingRate` — the write
this integration already makes — itself creates an override, or is simply
overwritten by the next cloud adjustment. The evidence points at the latter,
but it has not been tested directly, and testing it means making a write.

## Other observations

- **`supported_energy_managements` array order is not stable** between calls.
  Compare as a set.
- **The maintenance banner returns `403 AccessDenied` (XML)**, not 404.
  PyEmVue's `down_for_maintenance()` treats only 404 as "no maintenance" and
  otherwise calls `.json()` on the body, which would raise on this XML. The
  integration never calls it, so this is latent rather than live.
- **`/v1/customers/ev-charging-report` echoes its interval shifted by the
  account timezone.** A window requested as `2026-09-13T16:36:34Z` came back as
  `2026-09-13T21:36:34Z` — exactly the `America/Chicago` offset. It appears to
  read the timestamp as local and re-serialize it as UTC. Send times in the
  account's timezone, and do not trust the echoed interval as confirmation of
  what was asked for.

## Reproducing this

[`scripts/api_probe.py`](../scripts/api_probe.py) re-runs every GET above and
writes a JSON capture plus a readable log. See
[operations.md](operations.md#probing-the-emporia-cloud-api).

For a new app version, to pick up endpoints that did not exist before:

```bash
adb shell pm path com.emporiaenergy.emporos.v2
adb pull <.../split_config.arm64_v8a.apk> arm64.apk
unzip -p arm64.apk lib/arm64-v8a/libapp.so > libapp.so
strings -n 4 libapp.so | grep -E '^/(v1|customers|devices|AppAPI)'
strings -n 5 libapp.so | grep -E '^[a-z][a-z0-9]*(_[a-z0-9]+){1,6}$' | sort -u
```

The base APK is Flutter, so no endpoint strings live in the dex files — they
are all in `libapp.so` inside the ABI split.

First-party sources worth re-reading when they change:

- [`emporiaenergy/emporia-mcp`](https://github.com/emporiaenergy/emporia-mcp) —
  `src/config.ts` (hosts), `src/services/api.ts` (paths and auth headers),
  `src/types/api.ts` (response shapes, but see the caveat in the legend),
  `src/constants/deviceTypes.ts` (serial-prefix routing: `0`/`X` Vue 1, `A`
  Vue 2, `F` Vue 3, `Z` Utility Connect, `B` smart plug, `D` EVSE, `S` battery).
  Its comments reference an internal OpenAPI spec Emporia has not published.
- [PyEmVue `api_docs.md`](https://github.com/magico13/PyEmVue/blob/master/api_docs.md)
  — legacy only, and incomplete: no session, schedule, or energy-management
  endpoints, and no mention of `loads[]`.
- [Developer Access & Future Integrations](https://help.emporiaenergy.com/en/articles/13297125-developer-access-future-integrations)
  — Emporia's official position on APIs and local access.
