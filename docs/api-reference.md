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

Last extracted: 2026-09-20, against Emporia Android app 5.1.0 (versionCode
11308), PyEmVue 0.18.9, and `emporia-mcp` 0.2.1.

## Verification legend

Every row carries one of these. Do not promote a row to a stronger level
without re-verifying it.

| Level | Meaning |
| --- | --- |
| **V-lib** | Verified against [PyEmVue](https://github.com/magico13/PyEmVue) 0.18.9 source: path, method and response field names all read from code this integration actually executes. |
| **V-mcp** | Verified against Emporia's own open-source MCP server, [`emporiaenergy/emporia-mcp`](https://github.com/emporiaenergy/emporia-mcp) (BETA): path and response TypeScript interface read from first-party source. |
| **B-apk** | Path string extracted from the Emporia Android app v5.1.0 (`com.emporiaenergy.emporos.v2`, Flutter, `lib/arm64-v8a/libapp.so` in the arm64 split APK). **The path is certain. The HTTP method, request body and response shape are not** — they are inferred from neighbouring Dart method-name strings and from the app's observed behaviour. |

## Hosts

| Host | Role | Level |
| --- | --- | --- |
| `https://api.emporiaenergy.com` | Legacy API. Everything PyEmVue — and therefore this integration — talks to. | V-lib |
| `https://c-api.emporiaenergy.com` | Modern `/v1/...` API. What the current app and Emporia's MCP server use. This integration does not touch it. | V-mcp (`src/config.ts`), B-apk |
| `https://auth.emporiaenergy.com` | OAuth2 authorize/token (`/oauth2/authorize`, `/oauth2/token`). | B-apk |
| `https://mcp.emporiaenergy.com` | Emporia's hosted MCP endpoint (`/sse`, `/streamable`). | V-mcp |
| `https://cognito-idp.us-east-2.amazonaws.com/` | AWS Cognito user pool. Both the app and PyEmVue authenticate here. | V-lib, V-mcp |
| `https://s3.amazonaws.com/com.emporiaenergy.manual.ota/maintenance/maintenance.json` | Maintenance banner. Unauthenticated. | V-lib |
| `https://dev3api.emporiaenergy.com`, `https://dev3-c-api.emporiaenergy.com`, `https://dev3-auth.emporiaenergy.com` | Emporia development environment. Present in the shipping binary; do not use. | B-apk |

Cognito client id `4qte47jbstod8apnfic0bunmrq` is hardcoded in both PyEmVue and
Emporia's MCP server. Authentication yields `id_token` / `access_token` /
`refresh_token`. Emporia's MCP server notes it "will not work if you've created
an Emporia Account using Google or Apple" — the same limitation that drives
this integration's token-paste auth path.

---

## Legacy API — `api.emporiaenergy.com`

This is the entire surface PyEmVue 0.18.9 implements. "Used here" means this
integration calls the PyEmVue method in question.

| Endpoint | Verb | PyEmVue method | Used here | Purpose |
| --- | --- | --- | --- | --- |
| `/customers?email={email}` | GET | `get_customer_details` | No | Resolve a customer record from an email address. The integration gets the customer from `login()` instead. |
| `/customers/devices` | GET | `get_devices` | **Yes** | Device inventory. The integration's entire device/channel model is built from this. |
| `/customers/devices/status` | GET | `get_devices_status`, `get_outlets`, `get_chargers` | **Yes** | Live outlet + EV charger state and per-device connectivity. Polled every 60 s by `VueDeviceStatusCoordinator`. `get_outlets`/`get_chargers` are deprecated aliases hitting the same path. |
| `/devices/{deviceGid}/locationProperties` | GET | `populate_device_properties` | No | Timezone, zip, utility rate, billing cycle day, lat/long. |
| `/devices/channels` | PUT | `update_channel` | No | Rename or retype a CT channel. |
| `/devices/channels/channeltypes` | GET | `get_channel_types` | No | Lookup table of channel type gids. The integration hardcodes gid 13 = solar instead. |
| `/AppAPI?apiMethod=getDeviceListUsages&deviceGids={gids}&instant={ts}&scale={scale}&energyUnit={unit}` | GET | `get_device_list_usage` | **Yes** | The workhorse. All power and energy sensors at every scale come from here. |
| `/AppAPI?apiMethod=getChartUsage&deviceGid={gid}&channel={ch}&start={start}&end={end}&scale={scale}&energyUnit={unit}` | GET | `get_chart_usage` | No | Time series for one channel over a range. Would back historical backfill. |
| `/devices/outlet` | PUT | `update_outlet` | **Yes** | Turn a smart plug on/off. |
| `/devices/evcharger` | PUT | `update_charger` | **Yes** | Set charger on/off and charging rate. The only write this integration makes to a charger. |
| `/customers/vehicles` | GET | `get_vehicles` | No | Linked vehicles. See [Vehicles](#vehicles--legacy). |
| `/vehicles/v2/settings?vehicleGid={gid}` | GET | `get_vehicle_status` | No | Vehicle SoC, range, charge limit, minutes to full. See [Vehicles](#vehicles--legacy). |

PyEmVue also exposes `login`, `login_simulator` and `down_for_maintenance`.
The integration uses `login` and `login_simulator`; it never checks the
maintenance banner.

### Legacy paths in the app but not in PyEmVue

Present as strings in the app binary against the legacy host. Not implemented
by PyEmVue, so not reachable from this integration today. All B-apk.

| Endpoint | Purpose (inferred) | Adjacent Dart names |
| --- | --- | --- |
| `/devices/evcharger/maxchargingrate` | Set the breaker-derived maximum charge rate. PIN-gated in the app. | `putMaxChargingRate`, `saveMaxChargingRate`, `setMaxChargingRateRequest` |
| `/devices/schedule` | Device schedules. | `getDeviceSchedule`, `putDeviceSchedule`, `saveSchedule`, `deleteSchedule`, `cancelSchedule` |
| `/customers/timeofuse` | Time-of-use rate schedule. | `touSchedules`, `touSchedulesEnabled`, `enableTouSchedule` |
| `/customers/loadmanagement` | Legacy load management. | — |
| `/customers/derms` | Demand-response (DERMS) programme enrolment. | `getDermsForCustomer` |
| `/customers/termsofservice` | ToS acceptance state. | — |
| `/devices/firmwareuptodate` | Firmware currency check. | — |
| `/devices/configuremains` | Mains CT configuration. | `configureSolar`, `configureSolarInvalidChannelsMessage` |
| `/devices/usage/export` | CSV export ("Export Raw Data to CSV" in the app menu). | `getDeviceUsageExport` |
| `/devices/battery/schedule`, `/devices/battery/faults` | Home battery scheduling and faults. | `batteryAppStatusFromScheduleJson` |
| `/customersToDevices`, `/customersToDevices/manual` | Device claiming / sharing. | — |
| `/customers/devices/channels` | Channel list, legacy form. | — |

---

## Modern API — `c-api.emporiaenergy.com/v1`

The current app and Emporia's MCP server both use this. **This integration uses
none of it.** Rows marked V-mcp have a first-party response type; the rest are
app-binary strings only.

### Account, sites and devices

| Endpoint | Verb | Purpose | Level |
| --- | --- | --- | --- |
| `/v1/customers` | GET | Customer record: `customer_gid`, `first_name`, `last_name`, `email`, `created_at`. | V-mcp |
| `/v1/customers/devices` | GET | Site-aware device inventory. Supersedes legacy `/customers/devices`, which "predates the sites concept and does not return devices that belong to a site". | V-mcp |
| `/v1/customers/sites` | GET | Sites ("Homes") with member devices and utility/rate metadata. | V-mcp |
| `/v1/customers/sites/members` | — | Site membership management. | B-apk |
| `/v1/customers/devices/status` | GET | Live status grouped by category: `batteries`, `evses`, `outlets`, `thermostats`, `appliances`, `vehicles`, `monitors`, plus `devices_connected`. | V-mcp |
| `/v1/customers/devices/channels` | GET | Channel/circuit configuration, including merged circuits and nested devices. | V-mcp |
| `/v1/customers/devices/settings` | — | Per-device settings. | B-apk |
| `/v1/customers/devices/third-party-access` | — | Backs "Third Party Access" in device settings. | B-apk |
| `/v1/customers/app-preferences` | — | App-level preferences. | B-apk |
| `/v1/devices/merged-channel` | — | Create/edit merged circuits. | B-apk |
| `/v1/devices/fifty-amp-bidirectionality` | — | 50 A bidirectional capability flag. | B-apk |
| `/v1/auth/grant`, `/v1/third-party`, `/v1/third-party/oauth/callback/alexa` | — | Third-party grants; Alexa account linking. | B-apk |
| `/v1/customers/intercom-token`, `/v1/customers/notifications/token` | — | Support chat and push registration. | B-apk |
| `/v1/customers/stream` | — | Live stream, almost certainly SSE. A push alternative to 60 s polling. | B-apk |

### Usage

| Endpoint | Verb | Purpose | Level |
| --- | --- | --- | --- |
| `/v1/devices/{energy-monitors\|outlets\|evses\|batteries\|utility-connects}` | GET | Per-device-type live detail. The type is selected by the first character of the serial: `0`/`X` = Vue 1, `A` = Vue 2, `F` = Vue 3, `Z` = Utility Connect, `B` = smart plug, `D` = EVSE, `S` = battery. | V-mcp |
| `/v1/devices/{type}/energy`, `/v1/devices/{type}/power` | GET | Energy (`energy_kwhs`) and power (`power_watts`) series. Params: `device_ids`, `start`, `end`, `energy_resolution`/`power_resolution`, plus `circuit_ids` for energy monitors only. | V-mcp |
| `/v1/devices/batteries/state-of-charge` | GET | Battery state-of-charge series. | V-mcp |
| `/v1/customers/devices/usages` | — | Aggregate usage. | B-apk |
| `/v1/migrated/app-api/chart-usage` | — | The legacy `getChartUsage` migrated onto v1. | B-apk |
| `/v1/customers/homepage/summary`, `/v1/customers/homepage/monitor-card` | — | Home tab aggregates. | B-apk |
| `/v1/customers/devices/savings` | — | Savings figures shown on the Home tab. | B-apk |

`MeteredTimeResolution` is one of `MINUTES`, `FIFTEEN_MINUTES`, `HOURS`,
`DAYS`, `WEEKS`, `MONTHS`, `YEARS`. Power resolution is limited to `MINUTES`
or `FIFTEEN_MINUTES`. (V-mcp)

### EV charging

The part that matters most for this integration.

| Endpoint | Verb | Purpose | Level |
| --- | --- | --- | --- |
| `/v1/devices/evses` | GET | EVSE live detail. | V-mcp |
| `/v1/customers/evse/control` | POST (inferred) | **Modern charger control.** Supersedes legacy `PUT /devices/evcharger`. Adjacent: `postEVChargerControlCommand`, `sendChargerControlCommand`, `chargerCommandSubmission`, `applyChargeRate`, `saveActionAndChargeRate`, `chargerControlOperation`, `chargerControlRepository`. | B-apk |
| `/v1/customers/evse/charging-history` | GET | Per-session history: plug-in/plug-out, per-session energy, cost, duration, start and stop reason. Adjacent: `getChargingHistory`. | B-apk |
| `/v1/customers/ev-charging-report` | GET | Cost/energy report over an interval, with daily totals and plug-in sessions. Response type verified. | V-mcp |
| `/v1/devices/evses/sessions` | GET | Plug-in sessions with nested charging sessions. Response type verified. | V-mcp |
| `/v1/vehicles/brands` | GET | Vehicle make list for the cosmetic "Vehicle Make" field on the charger. | B-apk |

### Energy management — the four cloud controllers

These modulate charging current out from under any client that writes a
setpoint. Each has a customer-level and an energy-monitor-level endpoint. All
B-apk.

| Endpoint | Purpose | Adjacent Dart names |
| --- | --- | --- |
| `/v1/customers/excess-generation` | Excess Solar configuration: which devices participate, their priority order, and the solar window. | `getExcessGeneration`, `loadExcessSolar`, `toggleSolarWindow`, `useSolarWindow`, `clearSolarWindowTimeCollision` |
| `/v1/customers/energy-monitor/excess-generation` | Per-monitor Excess Solar enable. Backs the per-monitor Enabled/Unavailable toggle in the app. | `getExcessGenerationMonitor`, `postExcessGenerationMonitor` |
| `/v1/customers/power-smart`, `/v1/customers/energy-monitor/power-smart` | PowerSmart (panel-load-aware charging). Gated behind a paid firmware upgrade. | `getPowerSmart`, `getPowerSmartConfiguration`, `postPowerSmartConfiguration`, `getPowerSmartUpgradePrice`, `unlockPowerSmartUpgrade`, `powerSmartPinOperation` |
| `/v1/customers/peak-demand`, `/v1/customers/energy-monitor/peak-demand` | Peak Demand Management. | `getPeakDemand`, `getPeakDemandMonitor`, `postPeakDemandMonitor`, `peakDemandOrderedDeviceIds` |
| `/v1/customers/load-sharing`, `/v1/customers/load-sharings` | Intelligent Load Sharing between multiple chargers. | `getLoadSharingNetwork`, `postLoadSharingNetwork`, `saveLoadSharingNetwork`, `deleteLoadSharingNetwork` |
| **`/v1/customers/devices/override`** | **Time-boxed override of whichever controller currently owns the device.** | `startOverride`, `endOverride`, `deleteDeviceOverride`, `isOverrideActive`, `getOverrideAction`, `getOverrideActionWait`, `handleOverride`, `pendingOverrideAction`, `activeOverride`, `overrideUntilTime` |
| `/v1/customers/derms`, `/v1/derms/devices`, `/v1/derms/postal-code` | Utility demand-response programmes. | `getDerms`, `putDerms`, `getDermsForCustomer` |

Related fields: `override_allowed`, `override_expires_at`,
`overridden_energy_managements`, `active_energy_managements`,
`excessSolarPausedUntil`.

### Rates, retail energy and misc

All B-apk.

| Endpoint | Purpose |
| --- | --- |
| `/v1/utility-rate`, `/v1/utility-rates`, `/v1/utility-rates/company-names`, `/v1/devices/utility-rates` | Utility rate plan catalogue and selection. Drives the cost figures in the app. |
| `/v1/customers/rate-analysis`, `/v1/customers/devices/rate-analysis*`, `/v1/customers/rate-analysis/notification` | Rate Analyzer. |
| `/v1/customers/rep/account`, `/rep/account/devices`, `/rep/plan`, `/rep/plan-groups`, `/rep/embedded-flow` | Emporia's own retail electricity plans (Texas). |
| `/v1/customers/recommendations`, `/v1/customers/recommendations/status` | The "Recommendations" megaphone icon. |
| `/v1/customers/energy-monitor/combine` | Combine monitors into one logical unit. |
| `/v1/battery/install-inquiry`, `/v1/battery/install-inquiry/file` | Battery sales lead capture. |
| `/v1/proxy/places`, `/v1/proxy/places/autocomplete` | Google Places proxy for address entry. |

---

## Data formats

### Charger object — legacy `/customers/devices/status` → `evChargers[]`

V-lib. This is the only charger payload the integration sees.

```json
{
  "deviceGid": 0,
  "loadGid": 0,
  "chargerOn": true,
  "message": "Charging",
  "status": "Charging",
  "icon": "CarConnected",
  "iconLabel": "On",
  "iconDetailText": null,
  "faultText": null,
  "chargingRate": 40,
  "maxChargingRate": 40,
  "offPeakSchedulesEnabled": false,
  "debugCode": "",
  "proControlCode": null,
  "breakerPIN": null
}
```

`update_charger` PUTs back only `deviceGid`, `loadGid`, `chargerOn`,
`chargingRate`, `maxChargingRate`, plus `breakerPIN` when set. PyEmVue parses
`offPeakSchedulesEnabled` and discards `customSchedules` entirely — its source
says "don't have support for schedules yet".

Two fields Emporia's own MCP server types on this object that PyEmVue does not
model, and which this integration therefore never sees (V-mcp):

- `loadManagementEnabled: boolean`
- `hideChargeRateSliderText: string | null` — the app uses this to suppress the
  charge-rate slider when a cloud controller owns the rate.

`status` and `message` are free text. `sensor.py::_map_charger_state` maps the
known values to a four-state enum plus an IEC 61851 code; anything unmapped
logs at debug and falls through to `Connected` / `B`.

### Outlet object — legacy `/customers/devices/status` → `outlets[]`

V-lib. `{"deviceGid": 0, "outletOn": true, "loadGid": 0}`.

### Device object — legacy `/customers/devices`

V-lib. Fields: `deviceGid`, `manufacturerDeviceId`, `model`, `firmware`,
`parentDeviceGid`, `parentChannelNum`, `locationProperties`, `outlet`,
`evCharger`, `deviceConnected`, `devices[]` (nested), `channels[]`.

`locationProperties` carries `deviceName`, `displayName`, `zipCode`,
`timeZone`, `usageCentPerKwHour`, `peakDemandDollarPerKw`,
`billingCycleStartDay`, `solar`, `utilityRateGid`, `locationInformation`,
`latitudeLongitude`.

`deviceConnected` carries `deviceGid`, `connected`, `offlineSince`.

Each channel: `deviceGid`, `name`, `channelNum`, `channelMultiplier`,
`channelTypeGid`, `type`, `parentChannelNum`. `channelTypeGid` 13 is solar
(see [design.md](design.md)); `channelNum` `"1,2,3"` is the combined mains CT
(see [protocol.md](protocol.md)).

### Usage — legacy `getDeviceListUsages`

V-lib. Per device: `deviceGid` plus `channelUsages[]`, each with `name`,
`deviceGid`, `channelNum`, `usage`, `percentage`, and optional
`nestedDevices[]`. The response also carries an `instant` timestamp. `usage`
units depend on the `scale` and `energyUnit` requested.

The response has no per-device error channel. A partial or empty response is
indistinguishable from a real zero, which is why the coordinators carry
duplicate-sample and last-known-good guards (see [design.md](design.md)).

### EV charging report — v1 `/v1/customers/ev-charging-report`

V-mcp. Params: `device_id`, `start`, `end`.

```ts
{
  device_id: string;
  interval: { start: string; end: string };
  report_description?: string;
  call_to_action_type?: "SET_TIMEZONE" | "SET_UTILITY_RATE" | "SET_SCHEDULE";
  energy_kwhs?: number;
  charging_cost?: number;
  daily_charging_totals: Array<{
    date: string;
    energy_kwhs?: number;
    charging_cost?: number;
    savings?: number;
    potential_savings?: number;
  }>;
  plug_in_sessions: Array<{
    interval: { start: string; end?: string };
    charging_sessions: Array<{
      interval: { start: string; end?: string };
      energy_kwhs?: number;
      charging_cost?: number;
      savings?: number;
      potential_savings?: number;
    }>;
  }>;
}
```

### EVSE sessions — v1 `/v1/devices/evses/sessions`

V-mcp. Params: `device_ids`, `start`, `end`.

```ts
{
  success: Array<{
    device_id: string;
    sessions: Array<{
      plug_in: string;
      plug_out?: string;
      sessions?: Array<{
        interval: { start: string; end?: string };
        energy_kwhs?: number;
      }>;
    }>;
  }>;
  error: Array<{ device_id: string; message: string }>;
}
```

Note the two-level model: a **plug-in session** contains zero or more
**charging sessions**. That is what lets the app say "plugged in at 21:55" and
separately account for charging that started later.

### EVSE control item — v1

V-mcp types this as the per-device shape:

```ts
{ device_id: string; charger_on: boolean; charge_rate_amps: number }
```

All v1 device endpoints use a consistent
`{ success: [...], error: [{ device_id, message }] }` envelope — a per-device
partial-failure model the legacy API does not have.

### Vehicles — legacy

V-lib. `/customers/vehicles` returns `vehicleGid`, `vendor`, `apiId`,
`displayName`, `loadGid`, `make`, `model`, `year`.

`/vehicles/v2/settings?vehicleGid={gid}` returns a `settings` object with
`vehicleGid`, `vehicleState`, `batteryLevel`, `batteryRange`, `chargingState`,
`chargeLimitPercent`, `minutesToFullCharge`, `chargeCurrentRequest`,
`chargeCurrentRequestMax`.

**This path is very likely dead.** Emporia's help centre states the SmartCar EV
integration ended in February 2024 (app version 3.8.0), with access retained
only for vehicles added before then. Current app builds offer only a cosmetic
"Vehicle Make" text field on the charger. Do not build the integration's
vehicle-SoC feature on this without confirming a live response first.

---

## Field vocabulary

Field names present in the app binary (B-apk). The v1 API is consistently
snake_case; the legacy API is camelCase. **Endpoint attribution is not
established** — the Dart object pool is not laid out by compilation unit, so
proximity grouping produced noise. Treat this as a glossary of what the cloud
models, not as per-endpoint schemas.

**Charger state and rate**
`charging_rate`, `charging_rate_amps`, `charging_rate_kw`, `charge_rate_amps`,
`max_charging_rate`, `max_charge_rate_amps`, `breaker_size_amps`,
`panel_limit_amps`, `offered_amps`, `guaranteed_amps`, `reserved_amps`,
`additional_amps`, `allocatedChargingAmps`, `charger_status`, `charging_state`,
`charging_action`, `active_charging_minutes`, `energy_delivered`,
`charging_cost`, `charging_sessions`, `daily_charging_totals`,
`amps_12` … `amps_48` (discrete breaker sizes), `temperature_derated`,
`minimumGuaranteedChargeRateToolTip`.

**Who owns the rate** — the key group for any client that writes a setpoint:
`charging_rate_controller`, `charge_rate_controller`,
`charging_rate_controller_warning`, `chargingRateVia`,
`active_energy_managements`, `energy_managements_active`,
`energy_managements_configured`, `supported_energy_managements`,
`overridden_energy_managements`, `override_allowed`, `override_expires_at`,
`isOverrideActive`, `excessSolarPausedUntil`, `hideChargeRateSliderText`.

**Excess Solar**
`only_charge_from_excess_solar`, `excess_solar_configured`,
`excess_solar_cost_dollars`, `solar_window`, `solar_captured_percentage`,
`solar_ownership`, `has_solar`, `has_solar_buyback`, `generation_kwh`,
`net_kwh`, `buyback_rate_cents_per_kwh`, `set_solar_buyback_rate`.

**PowerSmart / Peak Demand / Load Sharing**
`power_smart_configuration`, `power_smart_monitor_device_id`,
`power_smart_upgrade`, `power_smart_upgraded`, `peak_demand_configured`,
`peak_demand_limit_kw`, `demand_goal_kwatts`, `demand_weekday_schedule`,
`demand_weekend_schedule`, `flat_demand_schedule`, `load_sharing_gid`,
`loadManagementEnabled`, `evseSupportsLoadManagement`.

**Schedules**
`schedule_gid`, `scheduled_time`, `scheduled_off_hours`,
`next_scheduled_change`, `next_scheduled_event`, `current_scheduled_mode`,
`partner_schedule_status`, `ev_charge_hour_start`, `ev_charge_hour_end`,
`off_peak_end_charge_duration`, `off_peak_end_charge_rate_kw`,
`smartSaverEnabledDeviceIds`, `energy_weekday_schedule`,
`energy_weekend_schedule`.

**Hardware descriptors**
`evse_model`, `evse_product`, `evse_branding`, `evse_color`, `evse_gun_type`,
`evse_power_source`.

**Vehicle**
`vehicle_connected`, `vehicle_charging`, `vehicle_state`,
`charge_limit_percent`, `minutes_to_full_charge`,
`charge_current_request_amps`, `state_of_charge`, `primary_vehicle_brand`.

**Rates and sites**
`utility_rate_gid`, `utility_rate_mods`, `utility_provider`,
`usage_cents_per_kwh`, `delivery_rate_cents_per_kwh`,
`export_rate_cents_per_kwh`, `energy_rate_structure`, `demand_rate_structure`,
`has_demand_charge`, `fixed_charge`, `rate_plan`, `rate_tier`, `site_gid`,
`billing_cycle_start_day`.

---

## What this integration does not use

Every capability below exists in the cloud and is absent from the integration.
Ordered by how much a Home Assistant user would feel the gap.

| Gap | Where it lives | Why it matters |
| --- | --- | --- |
| No indication of **which controller owns the charging rate** | `charging_rate_controller`, `active_energy_managements` | The `number` entity writes a setpoint that Excess Solar, PowerSmart, Peak Demand or Load Sharing may immediately override. Neither the entity nor the user is told. |
| No **override** mechanism | `/v1/customers/devices/override` | There is no way to make an automation's setpoint authoritative. The app does this with a time-boxed override; the integration has no equivalent. |
| No **actual delivered current or power** from the charger | v1 EVSE detail | The `number` entity reports the setpoint. The app additionally shows what the vehicle is actually drawing, which can be lower. |
| No **session data** | `/v1/customers/evse/charging-history`, `/v1/devices/evses/sessions` | No per-session energy, cost, duration, plug-in time, or start/stop reason. |
| No **charging report / cost** | `/v1/customers/ev-charging-report` | No cost attribution against the account's utility rate plan. |
| No **schedule** support | `offPeakSchedulesEnabled` (parsed by PyEmVue and discarded), `/devices/schedule` | Smart Saver and custom schedules are invisible. A schedule can start or stop charging with no HA-visible cause. |
| No **breaker / max charge rate** entity | `/devices/evcharger/maxchargingrate` | `maxChargingRate` is read and used as the slider ceiling but is never settable. PIN-gated. |
| No **push** | `/v1/customers/stream` | Everything is 60 s polling. |
| No **v1 usage** endpoints | `/v1/devices/{type}/energy` and `/power` | The v1 series API has explicit intervals, resolutions and a per-device error envelope; `getDeviceListUsages` has none of that, which is why the coordinators carry their own guards. |
| No **sites** awareness | `/v1/customers/sites` | Legacy `/customers/devices` "does not return devices that belong to a site" (Emporia's own words). Multi-home accounts may be incompletely enumerated. |
| No **battery**, thermostat, appliance or Utility Connect support | v1 category endpoints | Out of scope today, but the v1 surface covers them. |

## Filling in the gaps

[`scripts/api_probe.py`](../scripts/api_probe.py) issues a read-only GET
against every endpoint in this document and writes a JSON capture plus a
human-readable log. It is the intended way to promote a **B-apk** row to a
verified one: run it, read the real response, and update the table.

It only ever issues GET requests. Its `--diff` mode compares two captures,
which is how the energy-management semantics can be pinned down — capture,
change one thing in the app, capture again, diff. See the module docstring for
the Excess Solar experiment.

```bash
pip install pyemvue
python scripts/api_probe.py --email you@example.com --label baseline
```

## Reproducing this

For when a new app version ships:

```bash
adb shell pm path com.emporiaenergy.emporos.v2
adb pull <.../split_config.arm64_v8a.apk> arm64.apk
unzip -p arm64.apk lib/arm64-v8a/libapp.so > libapp.so
# endpoint paths
strings -n 4 libapp.so | grep -E '^/(v1|customers|devices|AppAPI)'
# field vocabulary
strings -n 5 libapp.so | grep -E '^[a-z][a-z0-9]*(_[a-z0-9]+){1,6}$' | sort -u
```

The base APK is Flutter, so no endpoint strings live in the dex files — they
are all in `libapp.so` inside the ABI split.

First-party sources worth re-reading when they change:

- [`emporiaenergy/emporia-mcp`](https://github.com/emporiaenergy/emporia-mcp) —
  `src/config.ts` (hosts), `src/services/api.ts` (paths), `src/types/api.ts`
  (response shapes), `src/constants/deviceTypes.ts` (serial-prefix to
  device-type routing). Its comments reference an internal OpenAPI spec that
  Emporia has not published.
- [PyEmVue `api_docs.md`](https://github.com/magico13/PyEmVue/blob/master/api_docs.md)
  — legacy only, and incomplete: it documents no session, schedule, or energy
  management endpoints.
- [Developer Access & Future Integrations](https://help.emporiaenergy.com/en/articles/13297125-developer-access-future-integrations)
  — Emporia's official position on APIs and local access.
