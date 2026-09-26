"""Unit tests for the pure data-shaping helpers in sensor.py.

These don't touch the network or a running HA instance. They cover:

- `_map_charger_state`: the charger status/IEC-code mapping, including every
  (icon_name, message, icon_label) combination Emporia was observed sending
  on the garage EV charger's live payload between 2026-09-12 and 2026-09-26.
- `_car_accepting_charge`: the "car at its own limit/timer" attribute.
- `_device_is_true_mains_panel`: the guard that keeps Balance/Grid
  Import-Export sensors from being synthesized for EV chargers, Smart
  Plugs/outlets, and single-CT solar monitors.
"""

import pytest

from pyemvue.device import ChargerDevice, OutletDevice, VueDevice, VueDeviceChannel

from custom_components.emporia_vue.sensor import (
    _car_accepting_charge,
    _device_is_true_mains_panel,
    _map_charger_state,
)

# Live combos observed on device_gid 371548 (garage EV charger),
# 2026-09-12..26: (icon_name, message, icon_label, status) -> expected
# (state, iec_code). The two rows marked WAS BUGGY used to come back
# "Disconnected" under the old message-only heuristic even with a car
# plugged in.
LIVE_CHARGER_COMBOS = [
    pytest.param("CarConnected", "Charging", "On", "Charging", "Charging", "C", id="charging"),
    pytest.param("CarConnected", "Connected to EV", "Paused", "Standby", "Connected", "B", id="connected_paused"),
    pytest.param("CarNotConnected", "Off", "Paused", "Standby", "Disconnected", "A", id="not_connected_paused"),
    pytest.param("CarNotConnected", "Ready", "Ready", "Standby", "Disconnected", "A", id="not_connected_ready"),
    pytest.param("CarConnected", "EV is not accepting charge", "Offering Charge", "Standby", "Connected", "B", id="connected_not_accepting"),
    pytest.param("CarConnected", "Please Wait", "Turning Off", "Standby", "Connected", "B", id="connected_turning_off_WAS_BUGGY"),
    pytest.param("CarConnected", "Please Wait", "Preparing", "Standby", "Connected", "B", id="connected_preparing_WAS_BUGGY"),
    pytest.param("CarNotConnected", "Please Wait", "Turning On", "Standby", "Disconnected", "A", id="not_connected_turning_on"),
    pytest.param("CarNotConnected", "Off", "Pausing", "Standby", "Disconnected", "A", id="not_connected_pausing"),
    pytest.param("DeviceNotConnected", "Offline", "Offline", "DeviceNotConnected", "Disconnected", "A", id="device_offline"),
]


@pytest.mark.parametrize(
    "icon_name, message, icon_label, status, expected_state, expected_iec", LIVE_CHARGER_COMBOS
)
def test_map_charger_state_live_combos(
    icon_name, message, icon_label, status, expected_state, expected_iec
):
    """Every combo seen on the live charger payload maps correctly, using icon_name."""
    state, iec = _map_charger_state(status, message, fault_text=None, icon_name=icon_name)
    assert (state, iec) == (expected_state, expected_iec)


def test_map_charger_state_error_takes_precedence_over_icon_name():
    """A fault always wins, even if icon_name says a car is connected."""
    state, iec = _map_charger_state(
        "Error", "Ground fault", fault_text="GFCI trip", icon_name="CarConnected"
    )
    assert (state, iec) == ("Error", "F")


def test_map_charger_state_fault_text_alone_triggers_error():
    """A non-empty fault_text is an error regardless of status/message."""
    state, iec = _map_charger_state("Standby", "Ready", fault_text="E4", icon_name="CarNotConnected")
    assert (state, iec) == ("Error", "F")


def test_map_charger_state_charging_status_wins_over_icon_name():
    """status == 'Charging' short-circuits before icon_name is even consulted."""
    state, iec = _map_charger_state("Charging", "Charging", fault_text=None, icon_name="CarNotConnected")
    assert (state, iec) == ("Charging", "C")


def test_map_charger_state_unrecognized_icon_name_falls_back_to_message():
    """An icon_name this integration doesn't recognize falls back to the message heuristic."""
    state, iec = _map_charger_state("Standby", "Ready", fault_text=None, icon_name="SomeNewIcon")
    assert (state, iec) == ("Disconnected", "A")


def test_map_charger_state_no_icon_name_uses_message_fallback_disconnected():
    """With icon_name absent entirely, the old message-based heuristic still applies."""
    state, iec = _map_charger_state("Standby", "Please Wait", fault_text=None, icon_name=None)
    assert (state, iec) == ("Disconnected", "A")


def test_map_charger_state_no_icon_name_uses_message_fallback_connected():
    """Fallback path still reports Connected for messages outside the disconnect list."""
    state, iec = _map_charger_state(
        "Standby", "EV is not accepting charge", fault_text=None, icon_name=""
    )
    assert (state, iec) == ("Connected", "B")


def test_map_charger_state_empty_status_is_disconnected():
    """No status at all (e.g. never reported) is Disconnected."""
    state, iec = _map_charger_state(None, None, fault_text=None, icon_name=None)
    assert (state, iec) == ("Disconnected", "A")


@pytest.mark.parametrize(
    "message, expected",
    [
        ("EV is not accepting charge", False),
        ("ev is not accepting charge", False),
        ("  EV is not accepting charge  ", False),
        ("Charging", True),
        ("Connected to EV", True),
        (None, True),
        ("", True),
    ],
)
def test_car_accepting_charge(message, expected):
    """car_accepting_charge is False only for the exact 'not accepting' message."""
    assert _car_accepting_charge(message) is expected


def _make_device(device_gid, channel_nums, ev_charger=None, outlet=None) -> VueDevice:
    device = VueDevice(gid=device_gid)
    device.device_name = f"Device {device_gid}"
    device.channels = [
        VueDeviceChannel(gid=device_gid, channelNum=num) for num in channel_nums
    ]
    device.ev_charger = ev_charger
    device.outlet = outlet
    return device


def test_true_mains_panel_with_branch_circuits_qualifies():
    """A real Vue panel (combined channel plus real branch circuits) qualifies."""
    device = _make_device(42441, ["1,2,3", "4", "5", "6"])
    assert _device_is_true_mains_panel(device) is True


def test_true_mains_panel_bare_combined_channel_only_does_not_qualify():
    """A single-CT monitor (e.g. dedicated solar meter) reporting only '1,2,3' is excluded."""
    device = _make_device(168944, ["1,2,3"])
    assert _device_is_true_mains_panel(device) is False


def test_true_mains_panel_missing_combined_channel_does_not_qualify():
    """A device without the combined channel at all is never a mains panel."""
    device = _make_device(99999, ["4", "5"])
    assert _device_is_true_mains_panel(device) is False


def test_ev_charger_reporting_only_combined_channel_is_excluded():
    """The garage EV charger (device_gid 371548) reports only '1,2,3' and has
    device.ev_charger set; it must never qualify for Balance/Grid synthesis,
    even though the bare-channel shape alone wouldn't have qualified it
    anyway. This pins the explicit ev_charger exclusion, not just the
    channel-shape side effect.
    """
    device = _make_device(371548, ["1,2,3"], ev_charger=ChargerDevice(gid=371548))
    assert _device_is_true_mains_panel(device) is False


def test_ev_charger_with_extra_channels_is_still_excluded():
    """Even if an EV charger somehow reports extra channels (e.g. native
    MainsFromGrid/MainsToGrid), the ev_charger exclusion still applies and
    takes priority over the channel-shape check.
    """
    device = _make_device(
        371548,
        ["1,2,3", "MainsFromGrid", "MainsToGrid"],
        ev_charger=ChargerDevice(gid=371548),
    )
    assert _device_is_true_mains_panel(device) is False


def test_outlet_device_is_excluded():
    """A Smart Plug/outlet device is excluded the same way as an EV charger."""
    device = _make_device(55555, ["1,2,3"], outlet=OutletDevice(gid=55555))
    assert _device_is_true_mains_panel(device) is False


def test_outlet_device_with_extra_channels_is_still_excluded():
    """The outlet exclusion applies regardless of channel shape."""
    device = _make_device(55555, ["1,2,3", "4"], outlet=OutletDevice(gid=55555))
    assert _device_is_true_mains_panel(device) is False
