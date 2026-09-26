"""Integration-level tests for the bounded last-known-good coordinators.

Exercises VueMinuteCoordinator and VueDeviceStatusCoordinator end to end
(against a real `hass` fixture) rather than just the pure
BoundedLastKnownGoodMixin helpers in test_coordinator.py, to catch wiring
mistakes (e.g. forgetting to call `_mark_lkg_success`, or not assigning
`self.data` the way DataUpdateCoordinator itself does).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.emporia_vue.coordinator import (
    VueDeviceStatusCoordinator,
    VueMinuteCoordinator,
    VueRuntimeData,
)

GOOD_MINUTE_DATA = {
    "1-1,2,3-1MIN": {
        "usage": 10.0,
        "channel_num": "1,2,3",
        "device_gid": "1",
        "scale": "1MIN",
        "timestamp": datetime(2026, 9, 25, 21, 0, tzinfo=UTC),
    }
}


async def test_minute_coordinator_serves_lkg_within_grace_then_raises_past_it(
    hass: HomeAssistant,
) -> None:
    """A minute-coordinator failure is tolerated briefly, then goes unavailable."""
    runtime = VueRuntimeData(vue=MagicMock())
    coordinator = VueMinuteCoordinator(hass, runtime)

    runtime.update_sensors = AsyncMock(return_value=dict(GOOD_MINUTE_DATA))
    coordinator.data = await coordinator._async_update_data()
    assert coordinator.last_success is not None
    first_success = coordinator.last_success

    # A failure right after a success is tolerated: last-known-good is served.
    runtime.update_sensors = AsyncMock(side_effect=UpdateFailed("boom"))
    result = await coordinator._async_update_data()
    assert result == coordinator.data
    assert coordinator._degraded_since is not None
    # Serving LKG doesn't count as a new success.
    assert coordinator.last_success == first_success

    # Once the grace window has elapsed, the failure is no longer hidden.
    coordinator._degraded_since = datetime.now(UTC) - coordinator.lkg_grace - timedelta(
        seconds=1
    )
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    # A subsequent success clears the degraded window.
    runtime.update_sensors = AsyncMock(return_value=dict(GOOD_MINUTE_DATA))
    coordinator.data = await coordinator._async_update_data()
    assert coordinator._degraded_since is None
    assert coordinator.last_success is not None
    assert coordinator.last_success >= first_success


async def test_minute_coordinator_raises_immediately_with_no_prior_data(
    hass: HomeAssistant,
) -> None:
    """The very first fetch failing (nothing cached yet) is never hidden."""
    runtime = VueRuntimeData(vue=MagicMock())
    coordinator = VueMinuteCoordinator(hass, runtime)
    runtime.update_sensors = AsyncMock(side_effect=UpdateFailed("boom"))

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_device_status_coordinator_wraps_generic_errors_and_recovers(
    hass: HomeAssistant,
) -> None:
    """Non-UpdateFailed exceptions from the raw status fetch use the same LKG path."""
    coordinator = VueDeviceStatusCoordinator(hass, MagicMock())
    raw_status = {
        "outlets": [{"deviceGid": 1, "outletOn": True}],
        "evChargers": [],
        "loads": [],
    }
    coordinator._fetch_status = MagicMock(return_value=raw_status)

    coordinator.data = await coordinator._async_update_data()
    assert coordinator.data  # non-empty: a real fetch found the outlet
    assert coordinator.last_success is not None

    coordinator._fetch_status = MagicMock(side_effect=ConnectionError("network down"))
    result = await coordinator._async_update_data()
    assert result == coordinator.data  # served from LKG, not raised

    coordinator._degraded_since = datetime.now(UTC) - coordinator.lkg_grace - timedelta(
        seconds=1
    )
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
