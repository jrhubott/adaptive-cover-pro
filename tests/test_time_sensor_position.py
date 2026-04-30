"""Tests for Start Sun / End Sun sensor position attributes.

The Start Sun and End Sun timestamp sensors should expose the sun's azimuth
and elevation at the moment of entry/exit, so the lovelace card can render a
wedge that matches today's actual active sun arc.
"""

from __future__ import annotations

from datetime import datetime, UTC
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import CONF_SENSOR_TYPE, SensorType
from custom_components.adaptive_cover_pro.sensor import AdaptiveCoverTimeSensorEntity


def _make_hass():
    hass = MagicMock()
    hass.config.units.temperature_unit = "°C"
    return hass


def _make_config_entry():
    entry = MagicMock()
    entry.entry_id = "test_position_entry"
    entry.data = {"name": "Test", CONF_SENSOR_TYPE: SensorType.BLIND}
    entry.options = {}
    return entry


def _make_coordinator(states: dict):
    coord = MagicMock()
    data = MagicMock()
    data.states = states
    coord.data = data
    coord.logger = MagicMock()
    coord.hass = _make_hass()
    return coord


def _make_time_sensor(key: str, sensor_name: str, states: dict):
    return AdaptiveCoverTimeSensorEntity(
        unique_id="test_position_entry",
        hass=_make_hass(),
        config_entry=_make_config_entry(),
        name="Test",
        sensor_name=sensor_name,
        key=key,
        icon="mdi:sun-clock",
        coordinator=_make_coordinator(states),
    )


@pytest.mark.unit
def test_start_sun_sensor_exposes_azimuth_and_elevation_attributes():
    """Start Sun sensor's extra_state_attributes returns azimuth and elevation."""
    now = datetime.now(UTC)
    states = {
        "start": now,
        "end": now,
        "start_position": {"azimuth": 120.5, "elevation": 25.4},
        "end_position": {"azimuth": 240.3, "elevation": 20.1},
    }
    sensor = _make_time_sensor("start", "Start Sun", states)
    attrs = sensor.extra_state_attributes
    assert attrs == {"azimuth": 120.5, "elevation": 25.4}


@pytest.mark.unit
def test_end_sun_sensor_exposes_azimuth_and_elevation_attributes():
    """End Sun sensor's extra_state_attributes returns azimuth and elevation."""
    now = datetime.now(UTC)
    states = {
        "start": now,
        "end": now,
        "start_position": {"azimuth": 120.5, "elevation": 25.4},
        "end_position": {"azimuth": 240.3, "elevation": 20.1},
    }
    sensor = _make_time_sensor("end", "End Sun", states)
    attrs = sensor.extra_state_attributes
    assert attrs == {"azimuth": 240.3, "elevation": 20.1}


@pytest.mark.unit
def test_time_sensor_returns_no_attributes_when_position_is_none():
    """When the sun never enters the FOV, position dicts are None and no attrs are exposed."""
    states = {
        "start": None,
        "end": None,
        "start_position": None,
        "end_position": None,
    }
    start_sensor = _make_time_sensor("start", "Start Sun", states)
    end_sensor = _make_time_sensor("end", "End Sun", states)
    assert start_sensor.extra_state_attributes is None
    assert end_sensor.extra_state_attributes is None
