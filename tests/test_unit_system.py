"""Unit tests for the unit_system helper module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM

from custom_components.adaptive_cover_pro import unit_system


def _hass(is_imperial: bool):
    """Return a MagicMock hass whose config.units is the requested system."""
    hass = MagicMock()
    hass.config.units = US_CUSTOMARY_SYSTEM if is_imperial else METRIC_SYSTEM
    hass.states.get.return_value = None
    return hass


@pytest.mark.unit
class TestModeDetection:
    """is_imperial and the display-unit labels."""

    def test_metric_mode(self):
        hass = _hass(is_imperial=False)
        assert unit_system.is_imperial(hass) is False
        assert unit_system.length_display_unit(hass) == "m"
        assert unit_system.slat_display_unit(hass) == "cm"

    def test_imperial_mode(self):
        hass = _hass(is_imperial=True)
        assert unit_system.is_imperial(hass) is True
        # Inches, not feet — see module docstring.
        assert unit_system.length_display_unit(hass) == "in"
        assert unit_system.slat_display_unit(hass) == "in"


@pytest.mark.unit
class TestLengthConversion:
    """to/from_display_length round-trips and known values."""

    def test_metric_is_identity(self):
        hass = _hass(is_imperial=False)
        assert unit_system.to_display_length(2.1, hass) == 2.1
        assert unit_system.from_display_length(2.1, hass) == 2.1

    def test_imperial_known_value(self):
        hass = _hass(is_imperial=True)
        # 2.1 m == 82.677... in
        assert unit_system.to_display_length(2.1, hass) == pytest.approx(
            82.677, rel=1e-3
        )
        assert unit_system.from_display_length(82.677, hass) == pytest.approx(
            2.1, rel=1e-3
        )

    def test_roundtrip(self):
        hass = _hass(is_imperial=True)
        for v in (0.1, 0.5, 1.0, 2.1, 6.0, 50.0):
            displayed = unit_system.to_display_length(v, hass)
            back = unit_system.from_display_length(displayed, hass)
            assert back == pytest.approx(v, rel=1e-9, abs=1e-9)


@pytest.mark.unit
class TestSlatConversion:
    """to/from_display_slat round-trips."""

    def test_metric_is_identity(self):
        hass = _hass(is_imperial=False)
        assert unit_system.to_display_slat(5.0, hass) == 5.0
        assert unit_system.from_display_slat(5.0, hass) == 5.0

    def test_imperial_known_value(self):
        hass = _hass(is_imperial=True)
        # 2.54 cm == 1 in exactly
        assert unit_system.to_display_slat(2.54, hass) == pytest.approx(1.0, rel=1e-9)
        assert unit_system.from_display_slat(1.0, hass) == pytest.approx(2.54, rel=1e-9)

    def test_roundtrip(self):
        hass = _hass(is_imperial=True)
        for v in (0.1, 2.5, 5.0, 7.5, 15.0):
            displayed = unit_system.to_display_slat(v, hass)
            back = unit_system.from_display_slat(displayed, hass)
            assert back == pytest.approx(v, rel=1e-9, abs=1e-9)


@pytest.mark.unit
class TestSensorUnitLabel:
    """sensor_unit_label reads the sensor's unit, with fallback."""

    def test_no_entity_id_returns_fallback(self):
        hass = _hass(is_imperial=False)
        assert unit_system.sensor_unit_label(hass, None, "°C") == "°C"
        assert unit_system.sensor_unit_label(hass, "", "°C") == "°C"

    def test_unknown_entity_returns_fallback(self):
        hass = _hass(is_imperial=False)
        hass.states.get.return_value = None
        assert unit_system.sensor_unit_label(hass, "sensor.missing", "°C") == "°C"

    def test_entity_without_uom_attr_returns_fallback(self):
        hass = _hass(is_imperial=False)
        state = MagicMock()
        state.attributes = {}
        hass.states.get.return_value = state
        assert unit_system.sensor_unit_label(hass, "sensor.x", "°C") == "°C"

    def test_entity_with_uom_attr_returns_sensor_unit(self):
        hass = _hass(is_imperial=False)
        state = MagicMock()
        state.attributes = {"unit_of_measurement": "°F"}
        hass.states.get.return_value = state
        # Sensor reports °F even though HA locale is metric.
        assert unit_system.sensor_unit_label(hass, "sensor.x", "°C") == "°F"


@pytest.mark.unit
class TestLengthSelector:
    """length_selector min/max/step conversion."""

    def test_metric_passes_through(self):
        hass = _hass(is_imperial=False)
        sel = unit_system.length_selector(hass, min_m=0.1, max_m=50.0, metric_step=0.01)
        cfg = sel.config
        assert cfg["min"] == 0.1
        assert cfg["max"] == 50.0
        assert cfg["step"] == 0.01
        assert cfg["unit_of_measurement"] == "m"

    def test_imperial_converts_bounds_and_step(self):
        hass = _hass(is_imperial=True)
        sel = unit_system.length_selector(
            hass, min_m=0.1, max_m=50.0, imperial_step=0.5
        )
        cfg = sel.config
        # 0.1 m == 3.937 in; rounded DOWN to 0.5 step → 3.5 in
        assert cfg["min"] == pytest.approx(3.5, abs=0.01)
        # 50 m == 1968.5 in; rounded UP to 0.5 step → 1968.5 in (already a multiple) or 1969 in
        assert cfg["max"] >= 1968.5
        assert cfg["step"] == 0.5
        assert cfg["unit_of_measurement"] == "in"


@pytest.mark.unit
class TestSlatSelector:
    """slat_selector min/max/step conversion."""

    def test_metric_passes_through(self):
        hass = _hass(is_imperial=False)
        sel = unit_system.slat_selector(hass, min_cm=0.1, max_cm=15.0)
        cfg = sel.config
        assert cfg["min"] == 0.1
        assert cfg["max"] == 15.0
        assert cfg["unit_of_measurement"] == "cm"

    def test_imperial_converts(self):
        hass = _hass(is_imperial=True)
        sel = unit_system.slat_selector(
            hass, min_cm=0.1, max_cm=15.0, imperial_step=0.05
        )
        cfg = sel.config
        # 0.1 cm == 0.039 in; rounded DOWN to 0.05 step → 0.0 in
        assert cfg["min"] == pytest.approx(0.0, abs=0.01)
        # 15 cm == 5.905 in; rounded UP to 0.05 step → 5.95 in
        assert cfg["max"] >= 5.9
        assert cfg["step"] == 0.05
        assert cfg["unit_of_measurement"] == "in"


@pytest.mark.unit
class TestDefaults:
    """length_default / slat_default in display units, rounded to step."""

    def test_metric_default_unchanged(self):
        hass = _hass(is_imperial=False)
        assert unit_system.length_default(2.1, hass) == 2.1
        assert unit_system.slat_default(3.0, hass) == 3.0

    def test_imperial_default_rounded_to_step(self):
        hass = _hass(is_imperial=True)
        # 2.1 m == 82.677 in; rounded to 0.5 step → 82.5 in
        assert unit_system.length_default(2.1, hass) == pytest.approx(82.5, abs=0.01)
        # 3 cm == 1.181 in; rounded to 0.05 step → 1.20 in
        assert unit_system.slat_default(3.0, hass) == pytest.approx(1.20, abs=0.01)


@pytest.mark.unit
class TestDictConversion:
    """options_to_display and user_input_to_canonical."""

    def test_metric_no_op(self):
        hass = _hass(is_imperial=False)
        opts = {"window_height": 2.1, "slat_depth": 3.0, "other": "x"}
        result = unit_system.options_to_display(
            hass, opts, length_keys=["window_height"], slat_keys=["slat_depth"]
        )
        assert result == opts
        assert result is not opts  # always a copy

    def test_imperial_converts_marked_keys(self):
        hass = _hass(is_imperial=True)
        opts = {"window_height": 2.1, "slat_depth": 3.0, "other": "x"}
        result = unit_system.options_to_display(
            hass, opts, length_keys=["window_height"], slat_keys=["slat_depth"]
        )
        # 2.1 m → ~82.7 in (rounded to 1 decimal)
        assert result["window_height"] == pytest.approx(82.7, abs=0.05)
        # 3.0 cm → ~1.2 in
        assert result["slat_depth"] == pytest.approx(1.2, abs=0.05)
        # Untouched fields preserved.
        assert result["other"] == "x"

    def test_canonical_roundtrip(self):
        hass = _hass(is_imperial=True)
        original = {"window_height": 2.1, "slat_depth": 3.0}
        displayed = unit_system.options_to_display(
            hass,
            original,
            length_keys=["window_height"],
            slat_keys=["slat_depth"],
            display_precision=6,
        )
        back = unit_system.user_input_to_canonical(
            hass,
            displayed,
            length_keys=["window_height"],
            slat_keys=["slat_depth"],
        )
        assert back["window_height"] == pytest.approx(2.1, rel=1e-5)
        assert back["slat_depth"] == pytest.approx(3.0, rel=1e-5)

    def test_none_values_pass_through(self):
        hass = _hass(is_imperial=True)
        opts = {"window_height": None}
        result = unit_system.options_to_display(
            hass, opts, length_keys=["window_height"]
        )
        assert result["window_height"] is None

    def test_missing_keys_ignored(self):
        hass = _hass(is_imperial=True)
        result = unit_system.user_input_to_canonical(
            hass, {"unrelated": 5}, length_keys=["window_height"]
        )
        assert result == {"unrelated": 5}
