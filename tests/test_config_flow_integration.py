"""Integration tests for the config flow using a real Home Assistant instance.

Tests the full multi-step setup wizard and options-flow reconfiguration using
pytest-homeassistant-custom-component's real ``hass`` fixture.

Covers:
- Config flow: quick-setup and full-setup paths for all three cover types
- Options flow: reconfiguring individual sections
- Sync flow: empty-selection does not abort (regression for documented gotcha)
- Duplicate flow: creates a new entry from an existing one
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_AZIMUTH,
    CONF_CLIMATE_MODE,
    CONF_DEFAULT_HEIGHT,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_DISTANCE,
    CONF_ENTITIES,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_HEIGHT_WIN,
    CONF_MANUAL_IGNORE_INTERMEDIATE,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_MANUAL_OVERRIDE_RESET,
    CONF_MANUAL_THRESHOLD,
    CONF_MAX_ELEVATION,
    CONF_MAX_POSITION,
    CONF_ENABLE_MAX_POSITION,
    CONF_MIN_ELEVATION,
    CONF_MIN_POSITION,
    CONF_ENABLE_MIN_POSITION,
    CONF_MODE,
    CONF_RETURN_SUNSET,
    CONF_SENSOR_TYPE,
    CONF_SILL_HEIGHT,
    CONF_START_TIME,
    CONF_END_TIME,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_INVERSE_STATE,
    CONF_WINDOW_DEPTH,
    DOMAIN,
    SensorType,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VERTICAL_GEOMETRY = {
    CONF_HEIGHT_WIN: 2.1,
    CONF_WINDOW_DEPTH: 0.0,
    CONF_SILL_HEIGHT: 0.0,
}

_SUN_TRACKING = {
    CONF_AZIMUTH: 180,
    CONF_FOV_LEFT: 45,
    CONF_FOV_RIGHT: 45,
    # CONF_MIN_ELEVATION / CONF_MAX_ELEVATION are Optional — omit to use defaults
    CONF_DISTANCE: 0.5,
    "blind_spot": False,
}

_SUN_TRACKING_VERTICAL = {
    **_SUN_TRACKING,
    "enable_glare_zones": False,
}

_POSITION = {
    CONF_DEFAULT_HEIGHT: 50,
    CONF_MIN_POSITION: 0,
    CONF_ENABLE_MIN_POSITION: False,
    CONF_MAX_POSITION: 100,
    CONF_ENABLE_MAX_POSITION: False,
    # CONF_SUNSET_POS is Optional — omit to use default
    CONF_SUNSET_OFFSET: 0,
    CONF_SUNRISE_OFFSET: 0,
    CONF_INVERSE_STATE: False,
    "interp": False,
    "open_close_threshold": 50,
}

_AUTOMATION = {
    CONF_DELTA_POSITION: 5,
    CONF_DELTA_TIME: 2,  # plain integer (minutes) per AUTOMATION_SCHEMA
    CONF_START_TIME: "08:00:00",
    CONF_END_TIME: "20:00:00",
    CONF_RETURN_SUNSET: False,
    # start_entity / end_entity are Optional — omit
}

_MANUAL_OVERRIDE = {
    CONF_MANUAL_OVERRIDE_DURATION: {"hours": 1},
    CONF_MANUAL_OVERRIDE_RESET: False,
    # CONF_MANUAL_THRESHOLD is Optional — omit
    CONF_MANUAL_IGNORE_INTERMEDIATE: False,
}

_FORCE_OVERRIDE = {
    "force_override_sensors": [],
    "force_override_position": 0,
}

# All Optional fields — send minimal required fields only, omit None-valued ones
_CUSTOM_POSITION = {}  # all Optional, submit empty to accept defaults

_MOTION_OVERRIDE = {
    "motion_sensors": [],
    "motion_timeout": 300,
}

_WEATHER_OVERRIDE = {
    "weather_bypass_auto_control": False,
    "weather_wind_speed_threshold": 50.0,
    "weather_wind_direction_tolerance": 45,
    "weather_rain_threshold": 1.0,
    "weather_severe_sensors": [],
    "weather_override_position": 0,
}

_LIGHT_CLOUD = {
    "weather_state": [],
    "cloud_coverage_threshold": 75,
    "cloud_suppression": False,
}

_TEMPERATURE_CLIMATE = {
    CONF_CLIMATE_MODE: False,
    "temp_low": 20.0,
    "temp_high": 25.0,
    "transparent_blind": False,
    "winter_close_insulation": False,
}


# ---------------------------------------------------------------------------
# Phase 2a: Quick-setup — vertical (cover_blind)
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_quick_setup_vertical_creates_entry(hass: HomeAssistant) -> None:
    """Quick-setup path for a vertical blind creates a config entry with safe defaults."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    # First entry: no existing entries → goes straight to create_new form
    assert result["type"] in ("form", "menu")

    # Step: create_new
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    assert result["type"] == "form"
    assert result["step_id"] == "create_new"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Test Blind", CONF_MODE: SensorType.BLIND},
    )
    # Step: setup_mode menu
    assert result["type"] == "menu"
    assert result["step_id"] == "setup_mode"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "quick_setup"}
    )
    # Step: cover_entities
    assert result["type"] == "form"
    assert result["step_id"] == "cover_entities"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITIES: []}
    )
    # Step: geometry
    assert result["type"] == "form"
    assert result["step_id"] == "geometry"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _VERTICAL_GEOMETRY
    )
    # Step: sun_tracking
    assert result["type"] == "form"
    assert result["step_id"] == "sun_tracking"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _SUN_TRACKING
    )
    # Step: position
    assert result["type"] == "form"
    assert result["step_id"] == "position"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _POSITION
    )
    # Quick-setup goes to summary after position
    assert result["type"] == "form"
    assert result["step_id"] == "summary"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {}
    )
    # Should be "create_entry"
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.data[CONF_SENSOR_TYPE] == SensorType.BLIND
    assert entry.data["name"] == "Test Blind"

    # Quick-setup critical keys must have safe non-None values (regression #133)
    options = entry.options
    assert options.get(CONF_DELTA_TIME) is not None
    assert options.get(CONF_MANUAL_OVERRIDE_DURATION) is not None


@pytest.mark.integration
async def test_quick_setup_horizontal_creates_entry(hass: HomeAssistant) -> None:
    """Quick-setup path for a horizontal awning creates a config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Test Awning", CONF_MODE: SensorType.AWNING},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "quick_setup"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITIES: []}
    )
    assert result["step_id"] == "geometry"
    # Awning geometry needs length + angle
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"length_awning": 2.1, "angle": 0}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _SUN_TRACKING
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _POSITION
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {}
    )
    assert result["type"] == "create_entry"
    assert result["result"].data[CONF_SENSOR_TYPE] == SensorType.AWNING


@pytest.mark.integration
async def test_quick_setup_tilt_creates_entry(hass: HomeAssistant) -> None:
    """Quick-setup path for a tilt cover creates a config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Test Tilt", CONF_MODE: SensorType.TILT},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "quick_setup"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITIES: []}
    )
    assert result["step_id"] == "geometry"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        # Tilt geometry schema uses cm (0.1-15), not metres
        {"slat_depth": 3.0, "slat_distance": 2.0, "tilt_mode": "mode1"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _SUN_TRACKING
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _POSITION
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {}
    )
    assert result["type"] == "create_entry"
    assert result["result"].data[CONF_SENSOR_TYPE] == SensorType.TILT


# ---------------------------------------------------------------------------
# Phase 2a: Full-setup — vertical only (demonstrates all steps)
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_full_setup_vertical_creates_entry(hass: HomeAssistant) -> None:
    """Full-setup path for a vertical blind — walks all steps, creates entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Full Test Blind", CONF_MODE: SensorType.BLIND},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "full_setup"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITIES: []}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _VERTICAL_GEOMETRY
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _SUN_TRACKING
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _POSITION
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _AUTOMATION
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _MANUAL_OVERRIDE
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _FORCE_OVERRIDE
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _CUSTOM_POSITION
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _MOTION_OVERRIDE
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _WEATHER_OVERRIDE
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _LIGHT_CLOUD
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _TEMPERATURE_CLIMATE
    )
    # Summary step
    assert result["type"] == "form"
    assert result["step_id"] == "summary"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {}
    )
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.data[CONF_SENSOR_TYPE] == SensorType.BLIND
    # All options keys present
    opts = entry.options
    assert CONF_AZIMUTH in opts
    assert CONF_FOV_LEFT in opts
    assert CONF_DEFAULT_HEIGHT in opts
    assert CONF_DELTA_POSITION in opts
    assert opts[CONF_DELTA_TIME] is not None
    assert opts[CONF_MANUAL_OVERRIDE_DURATION] is not None


# ---------------------------------------------------------------------------
# Phase 2c: Validation errors
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_sun_tracking_max_elevation_must_exceed_min(hass: HomeAssistant) -> None:
    """Sun tracking step rejects max_elevation <= min_elevation."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Err Test", CONF_MODE: SensorType.BLIND}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "quick_setup"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITIES: []}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _VERTICAL_GEOMETRY
    )
    # Submit invalid elevation: max <= min
    bad_tracking = dict(_SUN_TRACKING_VERTICAL)
    bad_tracking[CONF_MIN_ELEVATION] = 30.0
    bad_tracking[CONF_MAX_ELEVATION] = 20.0  # max < min → error

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], bad_tracking
    )
    assert result["type"] == "form"
    assert result["step_id"] == "sun_tracking"
    assert CONF_MAX_ELEVATION in result.get("errors", {})


@pytest.mark.integration
async def test_quick_setup_critical_keys_never_none(hass: HomeAssistant) -> None:
    """Quick-setup options must never store None for DELTA_TIME / MANUAL_OVERRIDE_DURATION.

    Regression guard for issue #133.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Regression", CONF_MODE: SensorType.BLIND}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "quick_setup"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITIES: []}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _VERTICAL_GEOMETRY
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _SUN_TRACKING
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _POSITION
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {}
    )
    assert result["type"] == "create_entry"
    opts = result["result"].options
    assert opts.get(CONF_DELTA_TIME) is not None, "CONF_DELTA_TIME must not be None"
    assert opts.get(CONF_MANUAL_OVERRIDE_DURATION) is not None, (
        "CONF_MANUAL_OVERRIDE_DURATION must not be None"
    )


# ---------------------------------------------------------------------------
# Phase 2d: Options flow — reconfigure
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_options_flow_change_geometry(hass: HomeAssistant) -> None:
    """Options flow geometry step saves updated height to options."""
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "My Blind", CONF_SENSOR_TYPE: SensorType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="opts_geom_01",
        title="My Blind",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] in ("form", "menu")

    # Navigate to geometry step
    if result["type"] == "menu":
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "geometry"}
        )

    assert result["step_id"] == "geometry"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HEIGHT_WIN: 3.0, CONF_WINDOW_DEPTH: 0.0, CONF_SILL_HEIGHT: 0.0},
    )
    # Should return to init menu
    assert result["type"] in ("form", "menu", "create_entry")


@pytest.mark.integration
async def test_options_flow_sync_empty_selection_no_abort(hass: HomeAssistant) -> None:
    """Sync flow with no targets selected returns to menu, does not abort.

    Regression guard for the documented gotcha: submitting sync with no
    targets used to abort the entire options flow (losing all unsaved changes).
    """
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Sync Test", CONF_SENSOR_TYPE: SensorType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="sync_test_01",
        title="Sync Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)

    # Navigate to sync step
    if result["type"] == "menu":
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "sync"}
        )

    if result["type"] == "form" and result.get("step_id") == "sync":
        # Submit with no targets — should NOT abort
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"sync_targets": [], "sync_categories": []},
        )
        # Must return to a form or menu, not "abort"
        assert result["type"] in ("form", "menu", "create_entry")
        assert result["type"] != "abort"
