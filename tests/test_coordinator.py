"""Unit tests for the pure data-shaping helpers in coordinator.py.

These don't touch the network or a running HA instance — they test the
math/logic that's easy to get subtly wrong: sign handling, reset-window
detection, and the derived Mains Import/Export split.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.emporia_vue.coordinator import (
    BoundedLastKnownGoodMixin,
    UpdateFailed,
    add_minute_mains_split,
    apply_api_update_debounce,
    carry_forward_mains_split,
    determine_reset_datetime,
    fix_usage_sign,
    get_sample_timestamp,
    is_in_reset_debounce_window,
    is_newer_sample,
    merged_channel_is_bidirectional,
    retry_after_seconds,
    should_serve_last_known_good,
)


def test_fix_usage_sign_solar_inverted():
    """Solar channels flip sign when invert_solar is True."""
    assert fix_usage_sign("4", 5.0, False, True, True) == -5.0


def test_fix_usage_sign_solar_not_inverted():
    """Solar channels keep their sign when invert_solar is False."""
    assert fix_usage_sign("4", 5.0, False, True, False) == 5.0


def test_fix_usage_sign_branch_circuit_forced_positive():
    """Non-mains, non-bidirectional branch circuits are always positive."""
    assert fix_usage_sign("4", -5.0, False, False, True) == 5.0


def test_fix_usage_sign_mains_keeps_sign():
    """The combined mains channel keeps its sign (needed for import/export math)."""
    assert fix_usage_sign("1,2,3", -5.0, False, False, True) == -5.0


def test_fix_usage_sign_bidirectional_keeps_sign():
    """Bidirectional channels (e.g. a battery) keep their sign."""
    assert fix_usage_sign("5", -5.0, True, False, True) == -5.0


def test_determine_reset_datetime_day():
    """Day reset is always local midnight."""
    local_time = datetime(2026, 8, 21, 14, 30, tzinfo=UTC)
    reset = determine_reset_datetime(local_time, monthly_cycle_start=1, is_month=False)
    assert reset == datetime(2026, 8, 21, 0, 0, tzinfo=UTC)


def test_determine_reset_datetime_month_before_cycle_start():
    """Before this month's cycle-start day, the reset is last month's cycle-start."""
    local_time = datetime(2026, 8, 5, tzinfo=UTC)
    reset = determine_reset_datetime(local_time, monthly_cycle_start=15, is_month=True)
    assert reset == datetime(2026, 7, 15, tzinfo=UTC)


def test_determine_reset_datetime_month_after_cycle_start():
    """After this month's cycle-start day, the reset is this month's cycle-start."""
    local_time = datetime(2026, 8, 20, tzinfo=UTC)
    reset = determine_reset_datetime(local_time, monthly_cycle_start=15, is_month=True)
    assert reset == datetime(2026, 8, 15, tzinfo=UTC)


def test_determine_reset_datetime_month_clamps_short_month():
    """A cycle-start day past the end of a short month clamps to the last day."""
    local_time = datetime(2026, 3, 5, tzinfo=UTC)
    reset = determine_reset_datetime(local_time, monthly_cycle_start=31, is_month=True)
    assert reset == datetime(2026, 2, 28, tzinfo=UTC)


def test_is_in_reset_debounce_window_true_just_after_reset():
    """A timestamp a few minutes after reset is inside the debounce window."""
    reset = datetime(2026, 8, 21, 0, 0, tzinfo=UTC)
    local_time = reset + timedelta(minutes=5)
    assert is_in_reset_debounce_window(local_time, reset, "day") is True


def test_is_in_reset_debounce_window_false_well_after_reset():
    """A timestamp well after reset is outside the debounce window."""
    reset = datetime(2026, 8, 21, 0, 0, tzinfo=UTC)
    local_time = reset + timedelta(hours=2)
    assert is_in_reset_debounce_window(local_time, reset, "day") is False


def test_apply_api_update_debounce_bounds_inflated_total():
    """During the debounce window, a lower existing value wins over an inflated new total."""
    reset = datetime(2026, 8, 21, 0, 0, tzinfo=UTC)
    timestamp = reset + timedelta(minutes=5)
    updated = {"a": {"usage": 10.0, "reset": reset, "timestamp": timestamp}}
    existing = {"a": {"usage": 2.0}}
    apply_api_update_debounce(updated, existing, "day")
    assert updated["a"]["usage"] == 2.0


def test_apply_api_update_debounce_leaves_data_outside_window():
    """Outside the debounce window, the updated value is left untouched."""
    reset = datetime(2026, 8, 21, 0, 0, tzinfo=UTC)
    timestamp = reset + timedelta(hours=2)
    updated = {"a": {"usage": 10.0, "reset": reset, "timestamp": timestamp}}
    existing = {"a": {"usage": 2.0}}
    apply_api_update_debounce(updated, existing, "day")
    assert updated["a"]["usage"] == 10.0


def test_add_minute_mains_split_import():
    """Positive combined-mains usage becomes an Import entry, zero Export."""
    data = {
        "123-1,2,3-1MIN": {
            "device_gid": "123",
            "channel_num": "1,2,3",
            "usage": 500.0,
            "scale": "1MIN",
        }
    }
    add_minute_mains_split(data)
    assert data["123-MainsImport-1MIN"]["usage"] == 500.0
    assert data["123-MainsExport-1MIN"]["usage"] == 0.0


def test_add_minute_mains_split_export():
    """Negative combined-mains usage becomes an Export entry, zero Import."""
    data = {
        "123-1,2,3-1MIN": {
            "device_gid": "123",
            "channel_num": "1,2,3",
            "usage": -300.0,
            "scale": "1MIN",
        }
    }
    add_minute_mains_split(data)
    assert data["123-MainsImport-1MIN"]["usage"] == 0.0
    assert data["123-MainsExport-1MIN"]["usage"] == 300.0


def test_carry_forward_mains_split_preserves_running_totals():
    """Derived Import/Export totals survive a fresh API response that omits them."""
    old_data = {
        "123-MainsImport-1D": {"usage": 12.5},
        "123-MainsExport-1D": {"usage": 3.0},
        "123-1,2,3-1D": {"usage": 9.5},
    }
    new_data = {"123-1,2,3-1D": {"usage": 10.0}}
    carry_forward_mains_split(old_data, new_data)
    assert new_data["123-MainsImport-1D"]["usage"] == 12.5
    assert new_data["123-MainsExport-1D"]["usage"] == 3.0
    # A key already present in new_data (a real API channel) is never overwritten.
    assert new_data["123-1,2,3-1D"]["usage"] == 10.0


def test_is_newer_sample_true_when_no_previous():
    """Any timestamped sample is newer than no previous sample."""
    candidate = datetime(2026, 8, 21, 0, 1, tzinfo=UTC)
    assert is_newer_sample(candidate, None) is True


def test_is_newer_sample_false_when_candidate_missing():
    """A sample with no timestamp is never treated as newer."""
    assert is_newer_sample(None, datetime(2026, 8, 21, 0, 1, tzinfo=UTC)) is False


def test_is_newer_sample_false_when_not_advanced():
    """A repeated (or older) sample is not newer, guarding against double-counting."""
    previous = datetime(2026, 8, 21, 0, 1, tzinfo=UTC)
    assert is_newer_sample(previous, previous) is False
    assert is_newer_sample(previous - timedelta(minutes=1), previous) is False


def test_is_newer_sample_true_when_advanced():
    """A later timestamp is newer than the previous one."""
    previous = datetime(2026, 8, 21, 0, 1, tzinfo=UTC)
    candidate = previous + timedelta(minutes=1)
    assert is_newer_sample(candidate, previous) is True


def test_get_sample_timestamp_returns_first_present():
    """The batch timestamp is read from whichever entry has one."""
    timestamp = datetime(2026, 8, 21, 0, 1, tzinfo=UTC)
    data = {
        "123-1-1MIN": {"usage": 1.0, "timestamp": timestamp},
        "123-2-1MIN": {"usage": 2.0, "timestamp": timestamp},
    }
    assert get_sample_timestamp(data) == timestamp


def test_get_sample_timestamp_none_when_empty_or_missing():
    """An empty batch, or one with no timestamped entries, yields None."""
    assert get_sample_timestamp({}) is None
    assert get_sample_timestamp({"123-1-1MIN": {"usage": 1.0}}) is None


@dataclass
class _FakeChannel:
    """Minimal stand-in for pyemvue's VueDeviceChannel in these tests."""

    channel_num: str
    type: str
    parent_channel_num: str | None = None


def test_merged_channel_is_bidirectional_when_all_children_are():
    """A Merged parent with only bidirectional direct children is bidirectional."""
    merged = _FakeChannel(channel_num="97", type="Merged")
    children = [
        _FakeChannel(channel_num="12", type="FiftyAmpBidirectional", parent_channel_num="97"),
        _FakeChannel(channel_num="13", type="FiftyAmpBidirectional", parent_channel_num="97"),
    ]
    assert merged_channel_is_bidirectional(merged, [merged, *children]) is True


def test_merged_channel_not_bidirectional_with_mixed_children():
    """A Merged parent is not bidirectional if any direct child isn't."""
    merged = _FakeChannel(channel_num="97", type="Merged")
    children = [
        _FakeChannel(channel_num="12", type="FiftyAmpBidirectional", parent_channel_num="97"),
        _FakeChannel(channel_num="13", type="FiftyAmp", parent_channel_num="97"),
    ]
    assert merged_channel_is_bidirectional(merged, [merged, *children]) is False


def test_merged_channel_not_bidirectional_with_no_children():
    """A childless Merged channel is not classified as bidirectional."""
    merged = _FakeChannel(channel_num="97", type="Merged")
    assert merged_channel_is_bidirectional(merged, [merged]) is False


def test_non_merged_channel_ignores_child_metadata():
    """A non-Merged channel is never classified by child metadata."""
    branch = _FakeChannel(channel_num="12", type="FiftyAmp")
    child = _FakeChannel(channel_num="13", type="FiftyAmpBidirectional", parent_channel_num="12")
    assert merged_channel_is_bidirectional(branch, [branch, child]) is False


class _FakeResponse:
    """Minimal stand-in for a requests.Response in these tests."""

    def __init__(self, status_code: int, headers: dict | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


class _FakeHttpError(Exception):
    """Minimal stand-in for requests.exceptions.HTTPError in these tests."""

    def __init__(self, response: _FakeResponse) -> None:
        super().__init__("http error")
        self.response = response


def test_retry_after_seconds_uses_header_on_429():
    """A 429 with a Retry-After header uses that value."""
    err = _FakeHttpError(_FakeResponse(429, {"Retry-After": "12"}))
    assert retry_after_seconds(err) == 12.0


def test_retry_after_seconds_defaults_on_5xx_without_header():
    """A 5xx with no Retry-After header falls back to 30 seconds."""
    err = _FakeHttpError(_FakeResponse(503))
    assert retry_after_seconds(err) == 30.0


def test_retry_after_seconds_none_for_other_4xx():
    """A non-429 4xx (e.g. the 2026-09-16 outage's HTTP 400) has no backoff."""
    err = _FakeHttpError(_FakeResponse(400))
    assert retry_after_seconds(err) is None


def test_retry_after_seconds_none_without_response():
    """A plain exception with no `.response` (e.g. a timeout) has no backoff."""
    assert retry_after_seconds(TimeoutError("connect timeout")) is None


def test_should_serve_last_known_good_true_on_first_failure():
    """The very first failure is always tolerated (nothing to compare yet)."""
    now = datetime(2026, 9, 25, 21, 0, tzinfo=UTC)
    assert should_serve_last_known_good(None, now, timedelta(minutes=5)) is True


def test_should_serve_last_known_good_true_within_grace():
    """A failure within the grace window since the first failure is tolerated."""
    degraded_since = datetime(2026, 9, 25, 21, 0, tzinfo=UTC)
    now = degraded_since + timedelta(minutes=4)
    assert should_serve_last_known_good(degraded_since, now, timedelta(minutes=5)) is True


def test_should_serve_last_known_good_false_past_grace():
    """A failure past the grace window is no longer tolerated."""
    degraded_since = datetime(2026, 9, 25, 21, 0, tzinfo=UTC)
    now = degraded_since + timedelta(minutes=6)
    assert should_serve_last_known_good(degraded_since, now, timedelta(minutes=5)) is False


class _FakeLkgCoordinator(BoundedLastKnownGoodMixin):
    """Exercises BoundedLastKnownGoodMixin without a real DataUpdateCoordinator."""

    def __init__(self, lkg_grace: timedelta) -> None:
        self.name = "test"
        self.lkg_grace = lkg_grace
        self.data = None
        self._init_lkg_state()


def test_bounded_lkg_raises_immediately_without_prior_data():
    """With no previous data there's nothing to fall back to; raise right away."""
    coordinator = _FakeLkgCoordinator(timedelta(minutes=5))
    with pytest.raises(UpdateFailed):
        coordinator._serve_lkg_or_raise(UpdateFailed("boom"))


def test_bounded_lkg_serves_data_within_grace():
    """A failure with prior data, within grace, serves the last-known-good data."""
    coordinator = _FakeLkgCoordinator(timedelta(minutes=5))
    coordinator.data = {"a": 1}
    result = coordinator._serve_lkg_or_raise(UpdateFailed("boom"))
    assert result == {"a": 1}
    assert coordinator._degraded_since is not None


def test_bounded_lkg_raises_once_grace_elapses():
    """Once the grace window (measured from the first failure) elapses, raise."""
    coordinator = _FakeLkgCoordinator(timedelta(minutes=5))
    coordinator.data = {"a": 1}
    coordinator._degraded_since = datetime.now(UTC) - timedelta(minutes=10)
    with pytest.raises(UpdateFailed):
        coordinator._serve_lkg_or_raise(UpdateFailed("boom"))


def test_bounded_lkg_success_clears_degraded_state():
    """A successful fetch clears the degraded window and records the time."""
    coordinator = _FakeLkgCoordinator(timedelta(minutes=5))
    coordinator.data = {"a": 1}
    coordinator._serve_lkg_or_raise(UpdateFailed("boom"))
    assert coordinator._degraded_since is not None
    assert coordinator.last_success is None

    coordinator._mark_lkg_success()
    assert coordinator._degraded_since is None
    assert coordinator.last_success is not None
