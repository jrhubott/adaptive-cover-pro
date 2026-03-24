"""Tests for duplicate and sync cover features."""

from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.config_flow import _extract_shared_options
from custom_components.adaptive_cover_pro.const import (
    CONF_AZIMUTH,
    CONF_CLIMATE_MODE,
    CONF_DELTA_POSITION,
    CONF_DEVICE_ID,
    CONF_ENABLE_BLIND_SPOT,
    CONF_ENTITIES,
    CONF_HEIGHT_WIN,
    CONF_MIN_POSITION,
    CONF_MOTION_SENSORS,
)


def _make_entry(options: dict) -> MagicMock:
    entry = MagicMock()
    entry.options = options
    return entry


class TestExtractSharedOptions:
    """Tests for _extract_shared_options."""

    def test_excludes_entities(self):
        """Verify CONF_ENTITIES is not present in the returned dict."""
        entry = _make_entry({CONF_ENTITIES: ["cover.test"], CONF_HEIGHT_WIN: 2.1})
        result = _extract_shared_options(entry)
        assert CONF_ENTITIES not in result

    def test_excludes_azimuth(self):
        """Verify CONF_AZIMUTH is not present in the returned dict."""
        entry = _make_entry({CONF_AZIMUTH: 180, CONF_HEIGHT_WIN: 2.1})
        result = _extract_shared_options(entry)
        assert CONF_AZIMUTH not in result

    def test_excludes_device_id(self):
        """Verify CONF_DEVICE_ID is not present in the returned dict."""
        entry = _make_entry({CONF_DEVICE_ID: "abc123", CONF_HEIGHT_WIN: 2.1})
        result = _extract_shared_options(entry)
        assert CONF_DEVICE_ID not in result

    def test_includes_window_dimensions(self):
        """Verify window dimension options are included in the returned dict."""
        entry = _make_entry({CONF_HEIGHT_WIN: 2.1, CONF_AZIMUTH: 180})
        result = _extract_shared_options(entry)
        assert result[CONF_HEIGHT_WIN] == 2.1

    def test_includes_automation_settings(self):
        """Verify automation settings are included in the returned dict."""
        entry = _make_entry({CONF_DELTA_POSITION: 5, CONF_AZIMUTH: 180})
        result = _extract_shared_options(entry)
        assert result[CONF_DELTA_POSITION] == 5

    def test_includes_climate_mode(self):
        """Verify climate mode setting is included in the returned dict."""
        entry = _make_entry({CONF_CLIMATE_MODE: True, CONF_AZIMUTH: 180})
        result = _extract_shared_options(entry)
        assert result[CONF_CLIMATE_MODE] is True

    def test_includes_position_limits(self):
        """Verify position limit options are included in the returned dict."""
        entry = _make_entry({CONF_MIN_POSITION: 10, CONF_AZIMUTH: 180})
        result = _extract_shared_options(entry)
        assert result[CONF_MIN_POSITION] == 10

    def test_includes_motion_sensors(self):
        """Verify motion sensor options are included in the returned dict."""
        entry = _make_entry({CONF_MOTION_SENSORS: ["binary_sensor.motion"], CONF_AZIMUTH: 180})
        result = _extract_shared_options(entry)
        assert result[CONF_MOTION_SENSORS] == ["binary_sensor.motion"]

    def test_includes_blind_spot(self):
        """Verify blind spot options are included in the returned dict."""
        entry = _make_entry({CONF_ENABLE_BLIND_SPOT: True, CONF_AZIMUTH: 180})
        result = _extract_shared_options(entry)
        assert result[CONF_ENABLE_BLIND_SPOT] is True

    def test_empty_options_returns_empty(self):
        """Verify empty options dict returns empty result."""
        entry = _make_entry({})
        result = _extract_shared_options(entry)
        assert result == {}

    def test_only_excluded_fields_returns_empty(self):
        """Verify a dict containing only excluded fields returns empty."""
        entry = _make_entry({
            CONF_ENTITIES: ["cover.test"],
            CONF_AZIMUTH: 180,
            CONF_DEVICE_ID: "abc",
        })
        result = _extract_shared_options(entry)
        assert result == {}

    def test_returns_copy_not_reference(self):
        """Verify the returned dict is a copy, not a reference to entry.options."""
        options = {CONF_HEIGHT_WIN: 2.1}
        entry = _make_entry(options)
        result = _extract_shared_options(entry)
        result[CONF_HEIGHT_WIN] = 99.0
        assert entry.options[CONF_HEIGHT_WIN] == 2.1
