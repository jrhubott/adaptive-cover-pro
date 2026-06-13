"""Tests for the `sun_state` attribute on the decision_trace sensor.

The companion card reads `sun_state` (one of "outside_fov" / "in_fov_not_valid"
/ "hitting") off the decision_trace sensor to colour its sky-compass sun dot
from a single authoritative source. The value is derived in the diagnostics
builder and stored in the `sun_validity` dict; the sensor must promote it onto
the wire so the card can read it. Follows #552 / #553; supports card issue #137.
"""

from __future__ import annotations

from unittest.mock import MagicMock


from custom_components.adaptive_cover_pro.const import CONF_SENSOR_TYPE, CoverType
from custom_components.adaptive_cover_pro.sensor import AdaptiveCoverDecisionTraceSensor


def _make_hass():
    hass = MagicMock()
    hass.config.units.temperature_unit = "°C"
    return hass


def _make_config_entry(options: dict | None = None):
    entry = MagicMock()
    entry.entry_id = "test_sun_state_entry"
    entry.data = {"name": "Test", CONF_SENSOR_TYPE: CoverType.BLIND}
    entry.options = options or {}
    return entry


def _make_coordinator(sun_validity: dict | None = None):
    coord = MagicMock()
    coord._pipeline_result = None
    coord.logger = MagicMock()
    coord.hass = _make_hass()
    coord.check_adaptive_time = True
    if sun_validity is None:
        coord.data = None
    else:
        coord.data = MagicMock(diagnostics={"sun_validity": sun_validity})
        coord._cover_data = MagicMock(direct_sun_valid=True)
    return coord


def _make_sensor(sun_validity: dict | None = None) -> AdaptiveCoverDecisionTraceSensor:
    return AdaptiveCoverDecisionTraceSensor(
        "test_sun_state_entry",
        _make_hass(),
        _make_config_entry(),
        "Test",
        _make_coordinator(sun_validity),
    )


def test_sun_state_surfaced_on_decision_trace():
    """sun_state from the sun_validity dict is exposed as an entity attribute."""
    sensor = _make_sensor(
        {
            "valid": True,
            "valid_elevation": True,
            "in_blind_spot": False,
            "sunset_window_active": False,
            "sun_state": "hitting",
        }
    )
    attrs = sensor.extra_state_attributes or {}
    assert attrs["sun_state"] == "hitting"


def test_sun_state_absent_degrades_gracefully():
    """When sun_validity has no sun_state key, the attr is None and no KeyError."""
    sensor = _make_sensor(
        {
            "valid": True,
            "valid_elevation": True,
            "in_blind_spot": False,
            "sunset_window_active": False,
        }
    )
    attrs = sensor.extra_state_attributes or {}
    assert attrs.get("sun_state") is None
