"""Platform for binary sensor integration."""

import logging

from pyemvue.device import VueDevice

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
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
    coordinator = runtime.coordinator_device_status
    device_information: dict[int, VueDevice] = runtime.device_information

    if coordinator is None or coordinator.data is None:
        return

    entities = [
        EmporiaChargerCloudManagedBinarySensor(coordinator, device_information[int(gid)])
        for gid in coordinator.data
        if int(gid) in device_information and device_information[int(gid)].ev_charger
    ]
    async_add_entities(entities)


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
