"""Unit tests for sensor.py uncovered branches."""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pytest

from homeassistant.components.sensor import SensorDeviceClass

from custom_components.adaptive_cover_pro.const import CONF_SENSOR_TYPE, CoverType
from custom_components.adaptive_cover_pro.sensor import (
    AdaptiveCoverClimateStatusSensor,
    AdaptiveCoverControlStatusSensor,
    AdaptiveCoverLastActionSensor,
    AdaptiveCoverSunPositionSensor,
    _DIAGNOSTIC_SPECS,
)


def _make_config_entry(sensor_type=CoverType.BLIND):
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"name": "Test", CONF_SENSOR_TYPE: sensor_type}
    entry.options = {}
    return entry


def _make_coordinator(diagnostics: dict | None = None, cover_type=CoverType.BLIND):
    coord = MagicMock()
    coord.logger = MagicMock()
    data = MagicMock()
    data.diagnostics = diagnostics
    data.states = {}
    coord.data = data
    # The control-status sensor now surfaces a cover_discovery descriptor built
    # from the coordinator (issue #725). Provide a real descriptor so
    # dataclasses.asdict works on the MagicMock coordinator.
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    coord.build_axis_discovery = MagicMock(
        side_effect=lambda labels=None: get_policy(cover_type).describe(
            caps={"has_set_position": True, "has_set_tilt_position": True}
        )
    )
    return coord


def _make_hass():
    hass = MagicMock()
    hass.config.units.temperature_unit = "°C"
    return hass


# ---------------------------------------------------------------------------
# AdaptiveCoverClimateStatusSensor.native_value
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_climate_status_native_value_summer_mode():
    """Returns 'summer_mode' slug when is_summer is True."""
    coord = _make_coordinator(
        diagnostics={"climate_conditions": {"is_summer": True, "is_winter": False}}
    )
    entry = _make_config_entry()
    sensor = AdaptiveCoverClimateStatusSensor(
        config_entry_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Climate Status",
        coordinator=coord,
        hass_ref=_make_hass(),
    )
    assert sensor.native_value == "summer_mode"


@pytest.mark.unit
def test_climate_status_native_value_winter_mode():
    """Returns 'winter_mode' slug when is_winter is True."""
    coord = _make_coordinator(
        diagnostics={"climate_conditions": {"is_summer": False, "is_winter": True}}
    )
    entry = _make_config_entry()
    sensor = AdaptiveCoverClimateStatusSensor(
        config_entry_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Climate Status",
        coordinator=coord,
        hass_ref=_make_hass(),
    )
    assert sensor.native_value == "winter_mode"


@pytest.mark.unit
def test_climate_status_native_value_intermediate():
    """Returns 'intermediate' slug when neither summer nor winter."""
    coord = _make_coordinator(
        diagnostics={"climate_conditions": {"is_summer": False, "is_winter": False}}
    )
    entry = _make_config_entry()
    sensor = AdaptiveCoverClimateStatusSensor(
        config_entry_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Climate Status",
        coordinator=coord,
        hass_ref=_make_hass(),
    )
    assert sensor.native_value == "intermediate"


@pytest.mark.unit
def test_climate_status_native_value_none_when_no_diagnostics():
    """Returns None when diagnostics is None."""
    coord = _make_coordinator(diagnostics=None)
    entry = _make_config_entry()
    sensor = AdaptiveCoverClimateStatusSensor(
        config_entry_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Climate Status",
        coordinator=coord,
        hass_ref=_make_hass(),
    )
    assert sensor.native_value is None


@pytest.mark.unit
def test_climate_status_native_value_none_when_no_climate_conditions():
    """Returns None when climate_conditions key is absent."""
    coord = _make_coordinator(diagnostics={"other_key": "value"})
    entry = _make_config_entry()
    sensor = AdaptiveCoverClimateStatusSensor(
        config_entry_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Climate Status",
        coordinator=coord,
        hass_ref=_make_hass(),
    )
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# AdaptiveCoverSunPositionSensor.extra_state_attributes — elevation limits
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sun_position_attributes_include_min_max_elevation():
    """extra_state_attributes includes min/max elevation when configured."""
    coord = _make_coordinator(
        diagnostics={
            "sun_azimuth": 180.0,
            "sun_elevation": 45.0,
            "gamma": 0.0,
            "configuration": {
                "min_elevation": 10.0,
                "max_elevation": 80.0,
                "azimuth": 180,
                "fov_left": 45,
                "fov_right": 45,
            },
        }
    )
    entry = _make_config_entry()
    sensor = AdaptiveCoverSunPositionSensor(
        unique_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )
    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert attrs.get("min_elevation") == 10.0
    assert attrs.get("max_elevation") == 80.0


@pytest.mark.unit
def test_sun_position_attributes_no_min_max_when_not_configured():
    """extra_state_attributes omits min/max elevation when not in config."""
    coord = _make_coordinator(
        diagnostics={
            "sun_azimuth": 180.0,
            "sun_elevation": 45.0,
            "gamma": None,
            "configuration": {
                "azimuth": 180,
                "fov_left": 45,
                "fov_right": 45,
            },
        }
    )
    entry = _make_config_entry()
    sensor = AdaptiveCoverSunPositionSensor(
        unique_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )
    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert "min_elevation" not in attrs
    assert "max_elevation" not in attrs


@pytest.mark.unit
def test_sun_position_attributes_blind_spot_range_calculated():
    """extra_state_attributes includes blind_spot_range when blind spot is enabled."""
    coord = _make_coordinator(
        diagnostics={
            "sun_azimuth": 180.0,
            "sun_elevation": 45.0,
            "gamma": None,
            "configuration": {
                "azimuth": 180,
                "fov_left": 45,
                "fov_right": 45,
                "enable_blind_spot": True,
                "blind_spot_left": 10.0,
                "blind_spot_right": 5.0,
            },
        }
    )
    entry = _make_config_entry()
    sensor = AdaptiveCoverSunPositionSensor(
        unique_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )
    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert "blind_spot_range" in attrs
    # left_edge = fov_left - blind_spot_left = 45 - 10 = 35
    # right_edge = fov_left - blind_spot_right = 45 - 5 = 40
    assert attrs["blind_spot_range"] == [40.0, 35.0]


def test_sun_position_attributes_blind_spot_ranges_multi_slot():
    """blind_spot_ranges lists every active slot; blind_spot_range stays slot 1."""
    coord = _make_coordinator(
        diagnostics={
            "sun_azimuth": 180.0,
            "sun_elevation": 45.0,
            "gamma": None,
            "configuration": {
                "azimuth": 180,
                "fov_left": 45,
                "fov_right": 45,
                "enable_blind_spot": True,
                "blind_spot_left": 10.0,
                "blind_spot_right": 5.0,
                "blind_spot_left_2": 20.0,
                "blind_spot_right_2": 15.0,
            },
        }
    )
    entry = _make_config_entry()
    sensor = AdaptiveCoverSunPositionSensor(
        unique_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )
    attrs = sensor.extra_state_attributes
    assert attrs is not None
    # Back-compat single-slot attr unchanged (slot 1).
    assert attrs["blind_spot_range"] == [40.0, 35.0]
    # New multi-slot attr: slot 1 then slot 2.
    # slot 2: left_edge = 45 - 20 = 25, right_edge = 45 - 15 = 30
    assert attrs["blind_spot_ranges"] == [[40.0, 35.0], [30.0, 25.0]]


# ---------------------------------------------------------------------------
# AdaptiveCoverLastActionSensor
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_last_action_sensor_native_value_with_timestamp():
    """native_value formats timestamp correctly when action has a valid timestamp."""
    ts = "2024-06-21T14:30:00+00:00"
    coord = _make_coordinator(
        diagnostics={
            "last_cover_action": {
                "entity_id": "cover.test_blind",
                "service": "set_cover_position",
                "position": 50,
                "calculated_position": 50,
                "timestamp": ts,
            }
        }
    )
    entry = _make_config_entry()
    sensor = AdaptiveCoverLastActionSensor(
        config_entry_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )
    val = sensor.native_value
    assert val is not None
    assert "test_blind" in val
    assert "set_cover_position" in val
    assert "14:30:00" in val


@pytest.mark.unit
def test_last_action_sensor_native_value_without_timestamp():
    """native_value works when timestamp is absent."""
    coord = _make_coordinator(
        diagnostics={
            "last_cover_action": {
                "entity_id": "cover.test_blind",
                "service": "set_cover_position",
                "position": 50,
                "calculated_position": 50,
                "timestamp": "",  # empty timestamp
            }
        }
    )
    entry = _make_config_entry()
    sensor = AdaptiveCoverLastActionSensor(
        config_entry_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )
    val = sensor.native_value
    assert val == "set_cover_position → test_blind"


@pytest.mark.unit
def test_last_action_sensor_extra_state_attributes():
    """extra_state_attributes returns full action dict."""
    coord = _make_coordinator(
        diagnostics={
            "last_cover_action": {
                "entity_id": "cover.test_blind",
                "service": "set_cover_position",
                "position": 50,
                "calculated_position": 50,
                "inverse_state_applied": False,
                "timestamp": "2024-06-21T14:30:00+00:00",
                "covers_controlled": 2,
                "threshold_used": 50,
            }
        }
    )
    entry = _make_config_entry()
    sensor = AdaptiveCoverLastActionSensor(
        config_entry_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )
    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert attrs["entity_id"] == "cover.test_blind"
    assert attrs["service"] == "set_cover_position"
    assert attrs["position"] == 50
    assert attrs["covers_controlled"] == 2
    assert "threshold_used" in attrs
    assert "threshold_comparison" in attrs


@pytest.mark.unit
def test_last_action_sensor_extra_state_attributes_no_action():
    """extra_state_attributes returns None when no action recorded."""
    coord = _make_coordinator(diagnostics={"last_cover_action": {}})
    entry = _make_config_entry()
    sensor = AdaptiveCoverLastActionSensor(
        config_entry_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )
    assert sensor.extra_state_attributes is None


# ---------------------------------------------------------------------------
# AdaptiveCoverControlStatusSensor.extra_state_attributes — cover_type
# ---------------------------------------------------------------------------
# The companion Lovelace card reads `cover_type` from this sensor's attributes
# to flip cover-fill wedge polarity for awnings (extended = full, retracted =
# empty) vs blinds (closed = full, open = empty). Card PR #56 added the flip
# logic but it never triggered in production because this attribute was missing
# — every cover fell back to `cover_blind` and the wedge rendered backwards for
# awnings.


@pytest.mark.unit
@pytest.mark.parametrize(
    "sensor_type",
    [CoverType.BLIND, CoverType.AWNING, CoverType.TILT, CoverType.VENETIAN],
)
def test_control_status_attrs_expose_cover_type(sensor_type):
    """cover_type is exposed so the Lovelace card can branch on it."""
    coord = _make_coordinator(diagnostics={"control_status": "active"})
    entry = _make_config_entry(sensor_type=sensor_type)
    sensor = AdaptiveCoverControlStatusSensor(
        unique_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )
    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert attrs.get("cover_type") == sensor_type


# ---------------------------------------------------------------------------
# AdaptiveCoverControlStatusSensor.extra_state_attributes — cover_discovery
# ---------------------------------------------------------------------------
# Issue #725: an additive self-discovery descriptor sits alongside the existing
# cover_type key. Old card versions still read cover_type; new ones read
# cover_discovery for the full axis list + labels + ranges + supported flags.


@pytest.mark.unit
def test_control_status_attrs_expose_cover_discovery_blind():
    """A blind exposes cover_discovery with a single position axis; cover_type
    stays present and unchanged (additive).
    """
    coord = _make_coordinator(
        diagnostics={"control_status": "active"}, cover_type=CoverType.BLIND
    )
    entry = _make_config_entry(sensor_type=CoverType.BLIND)
    sensor = AdaptiveCoverControlStatusSensor(
        unique_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )
    attrs = sensor.extra_state_attributes
    assert attrs is not None
    # Additive: cover_type still present.
    assert attrs.get("cover_type") == CoverType.BLIND
    discovery = attrs.get("cover_discovery")
    assert discovery is not None
    assert discovery["cover_type"] == "cover_blind"
    assert [a["id"] for a in discovery["axes"]] == ["position"]
    axis = discovery["axes"][0]
    assert axis["label"] == "Position"
    assert axis["min"] == 0
    assert axis["max"] == 100
    assert axis["unit"] == "%"
    assert axis["supported"] is True


@pytest.mark.unit
def test_control_status_attrs_expose_cover_discovery_venetian():
    """A venetian exposes cover_discovery with position + tilt axes."""
    coord = _make_coordinator(
        diagnostics={"control_status": "active"}, cover_type=CoverType.VENETIAN
    )
    entry = _make_config_entry(sensor_type=CoverType.VENETIAN)
    sensor = AdaptiveCoverControlStatusSensor(
        unique_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )
    attrs = sensor.extra_state_attributes
    assert attrs is not None
    discovery = attrs.get("cover_discovery")
    assert discovery is not None
    assert discovery["cover_type"] == "cover_venetian"
    ids = [a["id"] for a in discovery["axes"]]
    assert ids == ["position", "tilt"]
    labels = {a["id"]: a["label"] for a in discovery["axes"]}
    assert labels == {"position": "Position", "tilt": "Tilt"}


# ---------------------------------------------------------------------------
# AdaptiveCoverControlStatusSensor — schedule_start / schedule_end attrs
# ---------------------------------------------------------------------------


def _make_control_status_sensor(diagnostics: dict):
    """Build a control_status sensor with the given diagnostics dict."""
    coord = _make_coordinator(diagnostics=diagnostics)
    entry = _make_config_entry()
    return AdaptiveCoverControlStatusSensor(
        unique_id="test_entry",
        hass=_make_hass(),
        config_entry=entry,
        name="Test",
        coordinator=coord,
    )


@pytest.mark.unit
def test_control_status_attrs_schedule_start_end_static():
    """schedule_start and schedule_end are tz-aware ISO strings for static times."""
    # Use naive-local datetimes as TimeWindowManager produces them
    start_naive = dt.datetime(2026, 6, 6, 6, 30, 0)  # 06:30 naive-local
    end_naive = dt.datetime(2026, 6, 6, 21, 0, 0)  # 21:00 naive-local
    sensor = _make_control_status_sensor(
        {
            "control_status": "active",
            "time_window": {
                "check_adaptive_time": True,
                "after_start_time": True,
                "before_end_time": True,
                "start_time": start_naive,
                "end_time": end_naive,
            },
        }
    )
    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert "schedule_start" in attrs
    assert "schedule_end" in attrs
    # Must be tz-aware ISO strings (contain "+" or "Z" or offset info, not bare naive)
    assert attrs["schedule_start"] is not None
    assert attrs["schedule_end"] is not None
    # Parse back and verify the local time component is preserved
    parsed_start = dt.datetime.fromisoformat(attrs["schedule_start"])
    parsed_end = dt.datetime.fromisoformat(attrs["schedule_end"])
    assert parsed_start.tzinfo is not None, "schedule_start must be tz-aware"
    assert parsed_end.tzinfo is not None, "schedule_end must be tz-aware"
    assert parsed_start.hour == 6 and parsed_start.minute == 30
    assert parsed_end.hour == 21 and parsed_end.minute == 0


@pytest.mark.unit
def test_control_status_attrs_schedule_start_none_when_blank():
    """schedule_start is None when no start time is configured (blank start)."""
    sensor = _make_control_status_sensor(
        {
            "control_status": "active",
            "time_window": {
                "check_adaptive_time": True,
                "after_start_time": True,
                "before_end_time": True,
                "start_time": None,
                "end_time": dt.datetime(2026, 6, 6, 21, 0, 0),
            },
        }
    )
    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert attrs["schedule_start"] is None


@pytest.mark.unit
def test_control_status_attrs_schedule_end_none_when_not_configured():
    """schedule_end is None when no end time is configured."""
    sensor = _make_control_status_sensor(
        {
            "control_status": "active",
            "time_window": {
                "check_adaptive_time": True,
                "after_start_time": True,
                "before_end_time": True,
                "start_time": dt.datetime(2026, 6, 6, 6, 30, 0),
                "end_time": None,
            },
        }
    )
    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert attrs["schedule_end"] is None


@pytest.mark.unit
def test_control_status_attrs_schedule_end_midnight_next_day():
    """schedule_end with midnight end (00:00) reflects next-day datetime."""
    # TimeWindowManager rolls midnight end to next day: 00:00 next day
    next_day_midnight = dt.datetime(2026, 6, 7, 0, 0, 0)  # next day 00:00
    sensor = _make_control_status_sensor(
        {
            "control_status": "active",
            "time_window": {
                "check_adaptive_time": True,
                "after_start_time": True,
                "before_end_time": True,
                "start_time": dt.datetime(2026, 6, 6, 6, 30, 0),
                "end_time": next_day_midnight,
            },
        }
    )
    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert attrs["schedule_end"] is not None
    parsed = dt.datetime.fromisoformat(attrs["schedule_end"])
    assert parsed.tzinfo is not None
    # The day is next day (June 7) and time is midnight
    assert parsed.day == 7
    assert parsed.hour == 0 and parsed.minute == 0


# ---------------------------------------------------------------------------
# climate_status _SensorSpec — translation_key, device_class, options
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_climate_status_spec_properties():
    """climate_status spec must declare translation_key, ENUM device_class, and all three slugs."""
    spec = next(s for s in _DIAGNOSTIC_SPECS if s.suffix == "climate_status")
    assert spec.translation_key == "climate_status"
    assert spec.device_class == SensorDeviceClass.ENUM
    assert set(spec.options) == {"summer_mode", "winter_mode", "intermediate"}
