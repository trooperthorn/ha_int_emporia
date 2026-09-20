"""Contention check shared by the charger's writable entities.

Emporia's cloud writes the same `chargingRate` field this integration writes,
and keeps re-writing it while an energy-management feature is active — a live
capture showed the rate move 35 → 33 → 35 inside 90 seconds. A setpoint written
from Home Assistant during that period does not stick, and nothing in the API
reports that it was discarded.

This module makes that visible. It does not block by default: refusing writes
would change the behaviour of existing automations on upgrade, and there are
legitimate reasons to write anyway (the value is still the ceiling the
controller modulates under). Enable the `block_contended_writes` option to turn
the warning into an error.

See docs/api-reference.md for the evidence behind all of this.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .const import BLOCK_CONTENDED_WRITES, DOMAIN
from .energy_management import controller, is_cloud_managed

_LOGGER: logging.Logger = logging.getLogger(__name__)


def check_contended_write(entity: Any, description: str) -> None:
    """Warn, or raise, when a cloud feature currently owns the charging rate.

    `description` names the write in progress, e.g. "charging current" — it
    appears in the log line and in the error shown to the user.
    """
    coordinator = entity.coordinator
    load_for = getattr(coordinator, "load_for", None)
    if load_for is None:  # pragma: no cover - older coordinator, nothing to say
        return

    load = load_for(coordinator.data.get(entity.device_gid))
    if not is_cloud_managed(load):
        return

    owner = controller(load)
    detail = (load or {}).get("energyManagementText") or ""

    config_entry = getattr(entity.platform, "config_entry", None)
    options = config_entry.options if config_entry else {}

    if options.get(BLOCK_CONTENDED_WRITES, False):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="write_contended",
            translation_placeholders={
                "description": description,
                "controller": owner or "unknown",
                "detail": detail,
            },
        )

    _LOGGER.warning(
        "Writing %s while Emporia's %s is managing this charger. The cloud "
        "rewrites the same field continuously, so this value may not stick. "
        "Emporia reports: %s. Gate automations on the Cloud Managed binary "
        "sensor, or enable 'Block writes while cloud-managed' in the "
        "integration options to turn this into an error",
        description,
        owner,
        detail or "(no detail)",
    )
