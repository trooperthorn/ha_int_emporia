"""Interpretation of Emporia's `loads[]` energy-management state.

One place decides what a `loads[]` entry means, so the sensor, the binary
sensor and the write guards cannot drift apart.

The raw entry comes from `GET /customers/devices/status` and looks like:

```json
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
```

See docs/api-reference.md for how each field was verified.
"""

from __future__ import annotations

from typing import Any, Final

# Controller states, most specific first. These are the `sensor` entity's
# options, so changing a string changes a user-visible state.
STATE_OVERRIDDEN: Final = "overridden"
STATE_EXCESS_SOLAR: Final = "excess_solar"
STATE_PEAK_DEMAND: Final = "peak_demand"
STATE_SCHEDULE: Final = "schedule"
STATE_MANUAL: Final = "manual"

ENERGY_MANAGEMENT_STATES: Final[list[str]] = [
    STATE_MANUAL,
    STATE_EXCESS_SOLAR,
    STATE_PEAK_DEMAND,
    STATE_SCHEDULE,
    STATE_OVERRIDDEN,
]


def controller(load: dict[str, Any] | None) -> str | None:
    """Return which feature currently owns the load's charging rate.

    `None` means Emporia did not report a `loads[]` entry for this device, so
    nothing is known — deliberately distinct from `manual`, which means the
    cloud reported that no feature is managing it.

    An active override wins: while one is running no controller is applying
    its own rate, even though the controller stays configured. That is why
    `overridden` is checked before the individual feature flags.
    """
    if load is None:
        return None
    if load.get("energyManagementOverridden"):
        return STATE_OVERRIDDEN
    if load.get("excessGenerationEnabled"):
        return STATE_EXCESS_SOLAR
    if load.get("peakDemandEnabled"):
        return STATE_PEAK_DEMAND
    if load.get("schedulesEnabled"):
        return STATE_SCHEDULE
    return STATE_MANUAL


def is_cloud_managed(load: dict[str, Any] | None) -> bool:
    """True when a cloud feature is actively setting the charging rate.

    This is the signal that a written setpoint will not stick: Emporia writes
    the same `chargingRate` field the number entity writes, and re-writes it
    continuously while a controller is active.

    False while an override is running, because the override is precisely the
    state in which no controller is adjusting the rate.
    """
    return controller(load) in (STATE_EXCESS_SOLAR, STATE_PEAK_DEMAND, STATE_SCHEDULE)


def attributes(load: dict[str, Any] | None) -> dict[str, Any]:
    """Return the human-readable detail Emporia supplies alongside the flags.

    `energyManagementText` is prose, and the only place the override window
    appears — there is no structured expiry field on either API host. It is
    exposed verbatim rather than parsed: the wording is Emporia's to change,
    and the times are rendered in the account's locale.
    """
    if load is None:
        return {}
    return {
        "load_gid": load.get("loadGid"),
        "status_text": load.get("energyManagementText"),
        "next_scheduled_event": load.get("nextScheduledEventText"),
        "warning": load.get("warningText"),
        "excess_solar_enabled": load.get("excessGenerationEnabled"),
        "peak_demand_enabled": load.get("peakDemandEnabled"),
        "schedules_enabled": load.get("schedulesEnabled"),
        "overridden": load.get("energyManagementOverridden"),
    }
