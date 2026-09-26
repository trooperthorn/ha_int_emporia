"""Platform for binary sensor integration."""

from datetime import UTC, datetime
import logging

from pyemvue.device import VueDevice

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .energy_management import attributes, is_cloud_managed

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    runtime = config_entry.runtime_data
    device_information: dict[int, VueDevice] = runtime.device_information

    entities: list[BinarySensorEntity] = []

    # The minute coordinator has the tightest polling cadence (1 minute) and
    # shares the same authenticated session every other coordinator uses, so
    # it's the most timely proxy for "is the Emporia cloud reachable right
    # now". See docs/decisions.md.
    minute_coordinator = runtime.coordinator_1min
    if minute_coordinator is not None:
        entities.append(
            EmporiaCloudConnectivityBinarySensor(minute_coordinator, config_entry.entry_id)
        )

    status_coordinator = runtime.coordinator_device_status
    if status_coordinator is not None and status_coordinator.data is not None:
        entities.extend(
            EmporiaChargerCloudManagedBinarySensor(
                status_coordinator, device_information[int(gid)]
            )
            for gid in status_coordinator.data
            if int(gid) in device_information and device_information[int(gid)].ev_charger
        )

    async_add_entities(entities)


class EmporiaCloudConnectivityBinarySensor(CoordinatorEntity, BinarySensorEntity):  # type: ignore
    """Whether the last successful Emporia cloud fetch is still within its grace window.

    `off` means the minute coordinator has been serving bounded
    last-known-good data (or has gone unavailable) for longer than its
    grace window -- i.e. telemetry and switch/charger states elsewhere in
    this integration may be stale. See docs/decisions.md for the grace
    windows and coordinator.py's BoundedLastKnownGoodMixin.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "cloud_connection"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, entry_id: str) -> None:
        """Initialize the cloud-connectivity binary sensor."""
        super().__init__(coordinator)
        self._entry_id = entry_id

    @property
    def is_on(self) -> bool:
        """Return True while the last successful fetch is within grace."""
        last_success: datetime | None = getattr(self.coordinator, "last_success", None)
        if last_success is None:
            return False
        return (datetime.now(UTC) - last_success) < self.coordinator.lkg_grace

    @property
    def available(self) -> bool:
        """Always available: this entity reports connectivity, not data."""
        return True

    @property
    def extra_state_attributes(self) -> dict:
        """Return the last successful fetch time and the grace window."""
        last_success: datetime | None = getattr(self.coordinator, "last_success", None)
        return {
            "last_successful_update": last_success.isoformat() if last_success else None,
            "grace_period_seconds": self.coordinator.lkg_grace.total_seconds(),
        }

    @property
    def unique_id(self) -> str:
        """Unique ID for the cloud-connectivity binary sensor."""
        return f"emporia_vue.cloud_connection_{self._entry_id}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device information for the account-level cloud device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}-cloud")},
            name="Emporia Cloud Connection",
            manufacturer="Emporia",
            entry_type=DeviceEntryType.SERVICE,
        )


class EmporiaChargerCloudManagedBinarySensor(CoordinatorEntity, BinarySensorEntity):  # type: ignore
    """Whether an Emporia cloud feature is currently setting the charging rate.

    `on` means a written setpoint will not stick: Emporia writes the same
    `chargingRate` field this integration writes, and keeps re-writing it while
    a controller is active. Automations should gate rate writes on this being
    `off`.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "cloud_managed"
    _attr_icon = "mdi:cloud-sync"

    def __init__(self, coordinator, device: VueDevice) -> None:
        """Initialize the cloud-managed binary sensor."""
        super().__init__(coordinator)
        self._device = device
        self._device_gid = str(device.device_gid)

    @property
    def _load(self) -> dict | None:
        return self.coordinator.load_for(self.coordinator.data.get(self._device_gid))

    @property
    def available(self) -> bool:
        """Unavailable when Emporia reports no load-management entry at all."""
        return super().available and self._load is not None

    @property
    def is_on(self) -> bool:
        """Return True while a cloud feature owns the charging rate."""
        return is_cloud_managed(self._load)

    @property
    def extra_state_attributes(self) -> dict:
        """Return the detail Emporia supplies alongside the flags."""
        return attributes(self._load)

    @property
    def unique_id(self) -> str:
        """Unique ID for the cloud-managed binary sensor."""
        return f"emporia_vue.charger_cloud_managed_{self._device_gid}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._device_gid}-1,2,3")},
            name=self._device.device_name,
            model=self._device.model,
            sw_version=self._device.firmware,
            manufacturer="Emporia",
        )
