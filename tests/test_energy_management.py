"""Tests for interpreting Emporia's `loads[]` energy-management state.

The sample payloads here are real, captured on 2026-09-20 and recorded in
docs/api-reference.md.
"""

import pytest

from custom_components.emporia_vue.energy_management import (
    STATE_EXCESS_SOLAR,
    STATE_MANUAL,
    STATE_OVERRIDDEN,
    STATE_PEAK_DEMAND,
    STATE_SCHEDULE,
    attributes,
    controller,
    is_cloud_managed,
)

# Captured while Excess Solar was driving the charger.
EXCESS_SOLAR = {
    "loadGid": 225125,
    "schedulesEnabled": False,
    "nextScheduledEventText": None,
    "peakDemandEnabled": False,
    "excessGenerationEnabled": True,
    "energyManagementText": "Charging with Excess Solar since 10:17 am.",
    "energyManagementOverridden": False,
    "warningText": None,
}

# The same load after "Charge at full power" opened a three-hour override.
OVERRIDDEN = {
    **EXCESS_SOLAR,
    "energyManagementText": (
        "Charging with Excess Solar since 10:17 am.\n"
        "Overridden from 11:40 am to 2:40 pm."
    ),
    "energyManagementOverridden": True,
}

NOTHING_ENABLED = {
    **EXCESS_SOLAR,
    "excessGenerationEnabled": False,
    "energyManagementText": None,
}


def test_controller_reports_the_active_feature() -> None:
    """The enabled feature is named."""
    assert controller(EXCESS_SOLAR) == STATE_EXCESS_SOLAR


def test_override_takes_precedence_over_the_configured_feature() -> None:
    """While overridden, no controller is applying its own rate.

    excessGenerationEnabled stays true throughout — the feature is still
    configured, it is just not in control — so reading the feature flags
    without checking the override first reports the wrong owner.
    """
    assert OVERRIDDEN["excessGenerationEnabled"] is True
    assert controller(OVERRIDDEN) == STATE_OVERRIDDEN


@pytest.mark.parametrize(
    ("flag", "expected"),
    [
        ("peakDemandEnabled", STATE_PEAK_DEMAND),
        ("schedulesEnabled", STATE_SCHEDULE),
    ],
)
def test_other_features_are_named(flag: str, expected: str) -> None:
    """Peak Demand and schedules are reported when they are the only one on."""
    load = {**NOTHING_ENABLED, flag: True}
    assert controller(load) == expected


def test_no_feature_enabled_is_manual() -> None:
    """The cloud reporting nothing enabled is 'manual', not unknown."""
    assert controller(NOTHING_ENABLED) == STATE_MANUAL


def test_missing_load_is_unknown_not_manual() -> None:
    """A device with no loads[] entry is unknown, which is not 'manual'.

    'manual' is a positive statement that nothing is managing the load.
    Absence of data is not that statement, and the entities use the
    difference to go unavailable rather than claim local control.
    """
    assert controller(None) is None


def test_cloud_managed_only_while_a_feature_is_in_control() -> None:
    """The write-contention signal follows control, not configuration."""
    assert is_cloud_managed(EXCESS_SOLAR) is True
    # An override means the feature is configured but not driving the rate,
    # so a written setpoint is not contended.
    assert is_cloud_managed(OVERRIDDEN) is False
    assert is_cloud_managed(NOTHING_ENABLED) is False
    assert is_cloud_managed(None) is False


def test_attributes_expose_the_override_window_verbatim() -> None:
    """The window exists only in prose, so it is passed through unparsed."""
    attrs = attributes(OVERRIDDEN)
    assert attrs["overridden"] is True
    assert "Overridden from 11:40 am to 2:40 pm." in attrs["status_text"]
    assert attrs["load_gid"] == 225125
    assert attrs["excess_solar_enabled"] is True


def test_attributes_of_missing_load_are_empty() -> None:
    """No load entry means no attributes, rather than a dict of Nones."""
    assert attributes(None) == {}
