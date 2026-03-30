"""Tests for the export_config service."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.services.export_service import (
    async_handle_export,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_AZIMUTH,
    CONF_DEFAULT_HEIGHT,
    CONF_DISTANCE,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_HEIGHT_WIN,
    CONF_LENGTH_AWNING,
    CONF_AWNING_ANGLE,
    CONF_SENSOR_TYPE,
    CONF_SILL_HEIGHT,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_MODE,
    CONF_WINDOW_DEPTH,
    DOMAIN,
)


def make_hass(entry=None):
    """Build a minimal mocked hass with config + config_entries."""
    hass = MagicMock()
    hass.config.latitude = 32.939
    hass.config.longitude = -117.156
    hass.config.elevation = 10
    hass.config.time_zone = "America/Los_Angeles"
    hass.config_entries.async_get_entry.return_value = entry
    return hass


def make_entry(cover_type="cover_blind", name="Test Cover", options=None):
    """Build a minimal mocked config entry."""
    entry = MagicMock()
    entry.domain = DOMAIN
    entry.data = {"name": name, CONF_SENSOR_TYPE: cover_type}
    entry.options = options or {
        CONF_AZIMUTH: 180,
        CONF_FOV_LEFT: 45,
        CONF_FOV_RIGHT: 45,
        CONF_DEFAULT_HEIGHT: 60,
        CONF_DISTANCE: 1.0,
        CONF_HEIGHT_WIN: 2.0,
        CONF_WINDOW_DEPTH: 0.0,
        CONF_SILL_HEIGHT: 0.0,
        CONF_LENGTH_AWNING: 2.0,
        CONF_AWNING_ANGLE: 0,
        CONF_TILT_DISTANCE: 4.0,
        CONF_TILT_DEPTH: 6.0,
        CONF_TILT_MODE: "mode1",
    }
    return entry


def make_call(entry_id="test-entry-id"):
    """Build a minimal mocked service call."""
    call = MagicMock()
    call.data = {"config_entry_id": entry_id}
    return call


@pytest.mark.asyncio
async def test_export_returns_required_top_level_keys():
    """Exported dict contains all required top-level sections."""
    entry = make_entry()
    hass = make_hass(entry)
    call = make_call()

    result = await async_handle_export(hass, call)

    assert "export_version" in result
    assert "name" in result
    assert "cover_type" in result
    assert "location" in result
    assert "common" in result
    assert "vertical" in result
    assert "horizontal" in result
    assert "tilt" in result


@pytest.mark.asyncio
async def test_export_location_from_hass_config():
    """Location section is sourced from hass.config, not config_entry."""
    entry = make_entry()
    hass = make_hass(entry)
    call = make_call()

    result = await async_handle_export(hass, call)

    loc = result["location"]
    assert loc["latitude"] == 32.939
    assert loc["longitude"] == -117.156
    assert loc["elevation"] == 10
    assert loc["timezone"] == "America/Los_Angeles"


@pytest.mark.asyncio
async def test_export_name_and_cover_type():
    """Name and cover_type come from config_entry.data."""
    entry = make_entry(cover_type="cover_awning", name="My Awning")
    hass = make_hass(entry)
    call = make_call()

    result = await async_handle_export(hass, call)

    assert result["name"] == "My Awning"
    assert result["cover_type"] == "cover_awning"


@pytest.mark.asyncio
async def test_export_common_section_contains_all_fields():
    """Common section contains all expected option keys."""
    entry = make_entry()
    hass = make_hass(entry)
    call = make_call()

    result = await async_handle_export(hass, call)
    common = result["common"]

    assert CONF_AZIMUTH in common
    assert CONF_FOV_LEFT in common
    assert CONF_FOV_RIGHT in common
    assert CONF_DEFAULT_HEIGHT in common
    assert "sunset_position" in common
    assert "sunset_offset" in common
    assert "sunrise_offset" in common
    assert "max_position" in common
    assert "min_position" in common
    assert "enable_max_position" in common
    assert "enable_min_position" in common
    assert "blind_spot" in common


@pytest.mark.asyncio
async def test_export_vertical_section():
    """Vertical section contains cover-specific fields."""
    entry = make_entry()
    hass = make_hass(entry)
    call = make_call()

    result = await async_handle_export(hass, call)
    vert = result["vertical"]

    assert vert[CONF_DISTANCE] == 1.0
    assert vert[CONF_HEIGHT_WIN] == 2.0
    assert vert[CONF_WINDOW_DEPTH] == 0.0
    assert vert[CONF_SILL_HEIGHT] == 0.0


@pytest.mark.asyncio
async def test_export_tilt_values_stored_in_cm():
    """Tilt slat dimensions are stored in cm as entered in the UI (not meters)."""
    options = {
        CONF_TILT_DISTANCE: 4.0,  # cm
        CONF_TILT_DEPTH: 6.0,  # cm
        CONF_TILT_MODE: "mode1",
    }
    entry = make_entry(cover_type="cover_tilt", options=options)
    hass = make_hass(entry)
    call = make_call()

    result = await async_handle_export(hass, call)
    tilt = result["tilt"]

    # Must be 4.0 and 6.0 (cm), NOT 0.04 and 0.06 (meters)
    assert tilt[CONF_TILT_DISTANCE] == 4.0
    assert tilt[CONF_TILT_DEPTH] == 6.0


@pytest.mark.asyncio
async def test_export_defaults_applied_for_missing_options():
    """Optional fields get sensible defaults when missing from options."""
    entry = make_entry(
        options={
            CONF_AZIMUTH: 180,
            CONF_FOV_LEFT: 45,
            CONF_FOV_RIGHT: 45,
            CONF_DEFAULT_HEIGHT: 60,
        }
    )
    hass = make_hass(entry)
    call = make_call()

    result = await async_handle_export(hass, call)

    assert result["vertical"][CONF_WINDOW_DEPTH] == 0.0
    assert result["vertical"][CONF_SILL_HEIGHT] == 0.0
    assert result["common"]["max_position"] == 100
    assert result["common"]["min_position"] == 0
    assert result["common"]["enable_max_position"] is False
    assert result["common"]["enable_min_position"] is False


@pytest.mark.asyncio
async def test_export_invalid_entry_id_raises():
    """ServiceValidationError raised when config entry ID is not found."""
    from homeassistant.exceptions import ServiceValidationError

    hass = make_hass(entry=None)  # returns None for any entry_id
    call = make_call("nonexistent-id")

    with pytest.raises(ServiceValidationError):
        await async_handle_export(hass, call)


@pytest.mark.asyncio
async def test_export_wrong_domain_raises():
    """ServiceValidationError raised when entry belongs to a different domain."""
    from homeassistant.exceptions import ServiceValidationError

    entry = make_entry()
    entry.domain = "some_other_integration"
    hass = make_hass(entry)
    call = make_call()

    with pytest.raises(ServiceValidationError):
        await async_handle_export(hass, call)


@pytest.mark.asyncio
async def test_export_all_three_cover_types():
    """All three cover types produce a valid export dict."""
    for cover_type in ("cover_blind", "cover_awning", "cover_tilt"):
        entry = make_entry(cover_type=cover_type)
        hass = make_hass(entry)
        call = make_call()

        result = await async_handle_export(hass, call)

        assert result["cover_type"] == cover_type
        assert "location" in result
        assert "common" in result
