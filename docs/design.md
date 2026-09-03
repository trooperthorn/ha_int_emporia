# Architecture and design rationale

## Virtual and synthetic channels

The Emporia API's per-device channel list does not include the two
computed metrics the integration derives itself: `TotalUsage` and
`Balance`. `async_setup_entry` in `sensor.py` seeds these virtual channel
IDs into each scale coordinator's data before building entities, so the
entity list is stable across scales even before the first refresh
populates real values.

`CurrentVuePowerSensor.__init__` then needs a `VueDeviceChannel` object to
read metadata (name, multiplier) from for these virtual channels, but no
such channel exists on the real device. It builds a placeholder
`VueDeviceChannel` for `TotalUsage` and `Balance` so the rest of the entity
construction path does not need a separate code path for virtual versus
real channels.

## Entities are enabled by default

`CurrentVuePowerSensor` sets `entity_registry_enabled_default` from
`force_enabled` rather than from whether the channel already has usage
data. An entity that starts disabled because it has no data yet at
startup would stay hidden until a user manually enables it, so entities
are always enabled by default and rely on Home Assistant's normal
unavailable state instead.

## Balance and Mains Import/Export sensors are unconditional

`VueBalanceSensor` and `VueMainsSplitSensor` are created for every device,
not only devices recognized as a true mains/panel device
(`_device_is_true_mains_panel`). Restricting them to true mains panels
would block Balance and Mains generation on monitors that handle solar or
net metering but do not match that device shape; see
[decisions.md](decisions.md).

## Write concurrency on number and switch entities

`number.py` and `switch.py` set `PARALLEL_UPDATES = 1` because both issue
direct writes to the Emporia API (charger current, outlet/charger on-off).
Limiting the platform to one in-flight write at a time prevents two
near-simultaneous changes from racing each other against the API. Sensor
entities are read-only and backed by coordinators that already serialize
their own API calls, so `sensor.py` sets `PARALLEL_UPDATES = 0`.
