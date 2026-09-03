# Emporia API and device facts

Verified rows come from reading the integration's own code and the
[PyEmVue](https://github.com/magico13/PyEmVue) client it depends on.
Unverified rows are stated because they affect behavior but have not been
confirmed against a live account or Emporia's own documentation.

| Fact | Status |
| --- | --- |
| A device's combined "1,2,3" channel is the physical 3-phase mains/grid CT reading. | Verified (`MAINS_CHANNEL_NUMS` in `const.py`) |
| `channel_type_gid` 13 identifies a solar production channel. | Verified (`SOLAR_CHANNEL_TYPE_GID` in `sensor.py`) |
| Most accounts/hardware do not expose a native gross Import/Export split for the mains channel. | Unverified (true for the accounts this integration has been tested against; not confirmed as universal) |
| Some accounts/hardware expose `MainsFromGrid` and `MainsToGrid` channels directly. | Unverified (the integration detects and prefers these when present; see `_coordinator_has_native_mains_split`) |

## Derived Mains Import/Export split

When a device does not expose native `MainsFromGrid`/`MainsToGrid` channels,
the integration derives an Import/Export split from the combined "1,2,3"
mains channel (`add_minute_mains_split` in `coordinator.py`):

- At MINUTE scale, a single instant only ever flows in one direction, so the
  sign of that minute's power value is a reliable split.
- At DAY/MONTH scale, taking the sign of the period's net total is not valid:
  a day with both import and export periods nets out to a misleading single
  number. Instead the integration accumulates minute-by-minute, adding each
  minute's usage to the Import or Export running total based on that
  minute's sign, and resets the totals at midnight/billing-cycle start the
  same way `last_day_data`/`last_month_data` already do.

If a device's hardware/account exposes native `MainsFromGrid`/`MainsToGrid`
channels (visible in the "Unused data found during update" log line), those
are a more authoritative source, and the derived-split logic for that device
becomes unnecessary; the integration already prefers native channels when
`_coordinator_has_native_mains_split` finds them.

## Solar and the Balance calculation

Solar production channels (`channel_type_gid` 13) are excluded from the
Balance calculation (`VueBalanceSensor.native_value`) to avoid double-
subtracting solar: solar already nets out of the Mains reading physically,
because the Mains CTs sit upstream of the solar interconnection point.

## Charger writes are eventually consistent

Writing a new charging current to a charger device
(`EmporiaChargerCurrentNumber` in `number.py`) does not guarantee that the
next coordinator refresh reflects the new value immediately. The integration
holds an optimistic value until a refresh reports data that agrees with what
was written, rather than clearing the optimistic override on the first
refresh after the write.
