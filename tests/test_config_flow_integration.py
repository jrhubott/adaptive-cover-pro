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


import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from unittest.mock import patch

from custom_components.adaptive_cover_pro.const import (
    CONF_ARM_LENGTH,
    CONF_AWNING_MAX_ANGLE,
    CONF_AWNING_MIN_ANGLE,
    CONF_AZIMUTH,
    CONF_BUILDING_PROFILE_ID,
    CONF_CLIMATE_MODE,
    CONF_DEFAULT_HEIGHT,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_DEVICE_ID,
    CONF_DISTANCE,
    CONF_ENABLE_BLIND_SPOT,
    CONF_ENABLE_GLARE_ZONES,
    CONF_ENTITIES,
    CONF_FOV_COMPUTE,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_HEIGHT_WIN,
    CONF_MANUAL_IGNORE_INTERMEDIATE,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_MANUAL_OVERRIDE_RESET,
    CONF_MAX_ELEVATION,
    CONF_MAX_POSITION,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_VENETIAN_MODE,
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
    CONF_SYNC_SELECT_ALL,
    CONF_INVERSE_STATE,
    CONF_IS_SUNNY_SENSOR,
    CONF_LUX_ENTITY,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    CONF_WINDOW_DEPTH,
    CONF_WINDOW_WIDTH,
    CUSTOM_POSITION_SLOTS,
    DOMAIN,
    CoverType,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Per-window facing fields relocated from the sun-tracking step to the geometry
# step (#778): azimuth, FOV left/right, and shaded distance now submit on
# geometry alongside the window dimensions.
_WINDOW_FACING = {
    CONF_AZIMUTH: 180,
    CONF_FOV_LEFT: 45,
    CONF_FOV_RIGHT: 45,
    CONF_DISTANCE: 0.5,
}

_VERTICAL_GEOMETRY = {
    CONF_HEIGHT_WIN: 2.1,
    CONF_WINDOW_DEPTH: 0.0,
    CONF_SILL_HEIGHT: 0.0,
    **_WINDOW_FACING,
}

_SUN_TRACKING = {
    # Behavioural sun-tracking only — the window-facing fields moved to the
    # geometry step (#778). CONF_MIN_ELEVATION / CONF_MAX_ELEVATION are Optional.
    "blind_spot": False,
}

_SUN_TRACKING_VERTICAL = {
    **_SUN_TRACKING,
    "enable_glare_zones": False,
}

# L2a positions step (% values only, #613).
_POSITION = {
    CONF_DEFAULT_HEIGHT: 50,
    CONF_MIN_POSITION: 0,
    CONF_ENABLE_MIN_POSITION: False,
    CONF_MAX_POSITION: 100,
    CONF_ENABLE_MAX_POSITION: False,
    # CONF_SUNSET_POS is Optional — omit to use default
    "interp": False,
    "open_close_threshold": 50,
}

# L2b behavior step (timing & thresholds, #613).
_BEHAVIOR = {
    CONF_SUNSET_OFFSET: 0,
    CONF_SUNRISE_OFFSET: 0,
    CONF_RETURN_SUNSET: False,
    CONF_INVERSE_STATE: False,
}

_AUTOMATION = {
    CONF_DELTA_POSITION: 5,
    CONF_DELTA_TIME: 2,  # plain integer (minutes) per AUTOMATION_SCHEMA
    CONF_START_TIME: "08:00:00",
    CONF_END_TIME: "20:00:00",
    # start_entity / end_entity are Optional — omit
}

_MANUAL_OVERRIDE = {
    CONF_MANUAL_OVERRIDE_DURATION: {"hours": 1},
    CONF_MANUAL_OVERRIDE_RESET: False,
    # CONF_MANUAL_THRESHOLD is Optional — omit
    CONF_MANUAL_IGNORE_INTERMEDIATE: False,
}

# All Optional fields — send minimal required fields only, omit None-valued ones
_CUSTOM_POSITION = {}  # all Optional, submit empty to accept defaults

_MOTION_OVERRIDE = {
    "motion_sensors": [],
    "motion_timeout": 300,
}

# Threshold fields are multiline TextSelectors (#577): the frontend submits
# them as strings (a number or a Jinja2 template), so the simulated form
# submissions use string values too.
# The retraction sensor pickers are always rendered now, but they are optional
# entity selectors — the simulated submission leaves them empty and carries only
# the always-present threshold/position fields, which is a valid form submission.
_WEATHER_OVERRIDE = {
    "weather_bypass_auto_control": False,
    "weather_wind_speed_threshold": "50",
    "weather_wind_direction_tolerance": "45",
    "weather_rain_threshold": "1",
    "weather_override_position": 0,
}

_LIGHT_CLOUD = {
    "weather_state": [],
    "cloud_coverage_threshold": "75",
    "cloud_suppression": False,
}

_TEMPERATURE_CLIMATE = {
    CONF_CLIMATE_MODE: False,
    "temp_low": "20",
    "temp_high": "25",
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
    # The create menu is always shown (cover vs building profile); pick create_new.
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
        {"name": "Test Blind", CONF_MODE: CoverType.BLIND},
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

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    # Should be "create_entry"
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.data[CONF_SENSOR_TYPE] == CoverType.BLIND
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
        {"name": "Test Awning", CONF_MODE: CoverType.AWNING},
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
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    assert result["result"].data[CONF_SENSOR_TYPE] == CoverType.AWNING


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
        {"name": "Test Tilt", CONF_MODE: CoverType.TILT},
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
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    assert result["result"].data[CONF_SENSOR_TYPE] == CoverType.TILT


# ---------------------------------------------------------------------------
# Phase 2a: Quick-setup — oscillating awning (cover_oscillating_awning)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_quick_setup_oscillating_awning(hass: HomeAssistant) -> None:
    """Quick-setup path for an oscillating awning creates a config entry.

    Regression test for issue #530: async_step_update raised KeyError because the
    hardcoded type_mapping dict did not include 'cover_oscillating_awning'.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Test Oscillating", CONF_MODE: CoverType.OSCILLATING_AWNING},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "quick_setup"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITIES: []}
    )
    assert result["step_id"] == "geometry"
    # Oscillating awning geometry: arm length + min/max angle
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ARM_LENGTH: 0.8,
            CONF_AWNING_MIN_ANGLE: 0,
            CONF_AWNING_MAX_ANGLE: 175,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _SUN_TRACKING
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _POSITION
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    assert result["result"].data[CONF_SENSOR_TYPE] == CoverType.OSCILLATING_AWNING
    assert "Oscillating Awning" in result["title"]


@pytest.mark.integration
async def test_duplicate_oscillating_awning(hass: HomeAssistant) -> None:
    """Duplicate flow for an oscillating awning produces the correct title.

    Regression test for issue #530: async_step_duplicate_configure used .get()
    with a 'Cover' fallback, so oscillating awning copies were titled 'Cover <name>'
    instead of 'Oscillating Awning <name>'.
    """
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    # Seed an existing oscillating awning entry to duplicate from
    source_options = {
        **VERTICAL_OPTIONS,
        CONF_ARM_LENGTH: 0.8,
        CONF_AWNING_MIN_ANGLE: 0,
        CONF_AWNING_MAX_ANGLE: 175,
    }
    source_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "My Oscillating", CONF_SENSOR_TYPE: CoverType.OSCILLATING_AWNING},
        options=source_options,
        entry_id="osc_dup_src_01",
        title="Oscillating Awning My Oscillating",
    )
    source_entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(source_entry.entry_id)
        await hass.async_block_till_done()

    # Start the duplicate flow
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "duplicate_existing"}
        )

    # Select the source entry
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"source_entry": source_entry.entry_id}
    )

    # Configure the duplicate — provide name and azimuth
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "My Oscillating Copy", CONF_AZIMUTH: 180},
    )
    assert result["type"] == "create_entry"
    assert result["title"].startswith("Oscillating Awning")


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
        {"name": "Full Test Blind", CONF_MODE: CoverType.BLIND},
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
    assert result["step_id"] == "position"
    # 4-layer order (#613): after L1 (entities/geometry/window) and L2 (position),
    # the L3 handler steps run in pipeline-priority order, then L4 (automation).
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _POSITION
    )
    assert result["step_id"] == "behavior"  # L2b timing & thresholds
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _BEHAVIOR
    )
    assert result["step_id"] == "weather_override"  # L3 priority 90
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _WEATHER_OVERRIDE
    )
    assert result["step_id"] == "manual_override"  # L3 priority 80
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _MANUAL_OVERRIDE
    )
    assert result["step_id"] == "custom_position"  # L3 priority 1-100
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _CUSTOM_POSITION
    )
    assert result["step_id"] == "motion_override"  # L3 priority 75
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _MOTION_OVERRIDE
    )
    assert result["step_id"] == "light_cloud"  # L3 priority 60
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _LIGHT_CLOUD
    )
    assert result["step_id"] == "temperature_climate"  # L3 priority 50
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _TEMPERATURE_CLIMATE
    )
    assert result["step_id"] == "automation"  # L4 global motion constraints
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _AUTOMATION
    )
    # Summary step
    assert result["type"] == "form"
    assert result["step_id"] == "summary"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.data[CONF_SENSOR_TYPE] == CoverType.BLIND
    # All options keys present
    opts = entry.options
    assert CONF_AZIMUTH in opts
    assert CONF_FOV_LEFT in opts
    assert CONF_DEFAULT_HEIGHT in opts
    assert CONF_DELTA_POSITION in opts
    assert opts[CONF_DELTA_TIME] is not None
    assert opts[CONF_MANUAL_OVERRIDE_DURATION] is not None


# ---------------------------------------------------------------------------
# Phase 2b: Full-setup — building profile integration (issue #693)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_full_setup_includes_building_profile_step_when_profile_exists(
    hass: HomeAssistant,
) -> None:
    """Full-setup create flow surfaces building_profile step when profiles exist.

    Regression test for issue #693: the step was absent from ConfigFlow and
    only existed in OptionsFlowHandler.
    """
    # Register a building profile so _building_profile_entries(hass) is non-empty.
    profile = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "My Building", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={
            CONF_OUTSIDETEMP_ENTITY: "sensor.outside_temp",
            CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.p_wind",
            CONF_LUX_ENTITY: "sensor.p_lux",
        },
        entry_id="test_profile_693",
        title="My Building",
    )
    profile.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Profile Blind", CONF_MODE: CoverType.BLIND},
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
    # The building_profile step must appear when profiles exist.
    assert result["step_id"] == "building_profile"

    # Submit with a profile chosen.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BUILDING_PROFILE_ID: "test_profile_693"}
    )
    # blind_spot is False in _SUN_TRACKING → position is next.
    assert result["step_id"] == "position"

    # Walk through the remaining steps to create the entry.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _POSITION
    )
    assert result["step_id"] == "behavior"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _BEHAVIOR
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _WEATHER_OVERRIDE
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _MANUAL_OVERRIDE
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _CUSTOM_POSITION
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _MOTION_OVERRIDE
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _LIGHT_CLOUD
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _TEMPERATURE_CLIMATE
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _AUTOMATION
    )
    assert result["step_id"] == "summary"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"

    opts = result["result"].options
    assert opts.get(CONF_BUILDING_PROFILE_ID) == "test_profile_693"
    # All three profile-key categories persist through the create-link (issue
    # #851 / @FredM67's report): outsidetemp, weather-override, and light/cloud.
    assert opts.get(CONF_OUTSIDETEMP_ENTITY) == "sensor.outside_temp"
    assert opts.get(CONF_WEATHER_WIND_SPEED_SENSOR) == "sensor.p_wind"
    assert opts.get(CONF_LUX_ENTITY) == "sensor.p_lux"


@pytest.mark.integration
async def test_full_setup_skips_building_profile_step_when_no_profiles(
    hass: HomeAssistant,
) -> None:
    """Full-setup create flow skips building_profile step when no profiles exist.

    When _building_profile_entries(hass) is empty the flow jumps directly to
    position (or blind_spot), preserving the pre-#693 path.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "No Profile Blind", CONF_MODE: CoverType.BLIND},
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
    # No profiles registered → skip directly to position.
    assert result["step_id"] == "position"


@pytest.mark.integration
async def test_quick_setup_skips_building_profile_step_even_when_profiles_exist(
    hass: HomeAssistant,
) -> None:
    """Quick-setup path bypasses the building_profile step even when profiles exist."""
    profile = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Bldg", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={},
        entry_id="test_profile_quick",
        title="Bldg",
    )
    profile.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Quick Blind", CONF_MODE: CoverType.BLIND},
    )
    # Quick setup
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
    # Quick setup never shows the building_profile step.
    assert result["step_id"] == "position"


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
        result["flow_id"], {"name": "Err Test", CONF_MODE: CoverType.BLIND}
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
        result["flow_id"], {"name": "Regression", CONF_MODE: CoverType.BLIND}
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
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    opts = result["result"].options
    assert opts.get(CONF_DELTA_TIME) is not None, "CONF_DELTA_TIME must not be None"
    assert (
        opts.get(CONF_MANUAL_OVERRIDE_DURATION) is not None
    ), "CONF_MANUAL_OVERRIDE_DURATION must not be None"


# ---------------------------------------------------------------------------
# Phase 2d: Options flow — reconfigure
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_options_flow_round_trips_input_entities(hass: HomeAssistant) -> None:
    """Options flow manual-override step accepts the input-sensor list (issue #688)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_MANUAL_OVERRIDE_INPUT_ENTITIES,
    )
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "My Blind", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="opts_mo_input_01",
        title="My Blind",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] in ("form", "menu")
    if result["type"] == "menu":
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "manual_override"}
        )
    assert result["step_id"] == "manual_override"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_MANUAL_OVERRIDE_INPUT_ENTITIES: ["binary_sensor.cover_input_0"]},
    )
    # Step accepted the new field and advanced (back to the menu / created entry).
    assert result["type"] in ("form", "menu", "create_entry")


async def test_options_flow_change_geometry(hass: HomeAssistant) -> None:
    """Options flow geometry step saves updated height to options."""
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "My Blind", CONF_SENSOR_TYPE: CoverType.BLIND},
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
        data={"name": "Sync Test", CONF_SENSOR_TYPE: CoverType.BLIND},
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


# ---------------------------------------------------------------------------
# Phase 2d-1: Options flow — sync "Select all covers" toggle (issue #772)
# ---------------------------------------------------------------------------


async def _setup_sync_select_all_entries(
    hass: HomeAssistant,
    *,
    source_options: dict | None = None,
    target1_options: dict | None = None,
    target2_options: dict | None = None,
):
    """Create a source + two same-type target entries for select-all sync tests.

    Only the source is fully set up (options flow requires a loaded entry);
    targets only need to be registered so they show up in ``other_entries``
    and can receive ``async_update_entry`` calls.
    """
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    source = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Sync Select All Source", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(source_options or VERTICAL_OPTIONS),
        entry_id="sync_sel_all_src",
        title="Sync Select All Source",
    )
    source.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(source.entry_id)
        await hass.async_block_till_done()

    target1 = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Target One", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(target1_options or VERTICAL_OPTIONS),
        entry_id="sync_sel_all_t1",
        title="Target One",
    )
    target1.add_to_hass(hass)

    target2 = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Target Two", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(target2_options or VERTICAL_OPTIONS),
        entry_id="sync_sel_all_t2",
        title="Target Two",
    )
    target2.add_to_hass(hass)

    return source, target1, target2


async def _navigate_to_sync_step(hass: HomeAssistant, entry_id: str):
    """Init the options flow for ``entry_id`` and navigate to the sync step."""
    result = await hass.config_entries.options.async_init(entry_id)
    if result["type"] == "menu":
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "sync"}
        )
    return result


@pytest.mark.integration
async def test_options_flow_sync_select_all_targets_all_same_type(
    hass: HomeAssistant,
) -> None:
    """Select-all with no exclusions targets every same-type other cover (#772)."""
    source, target1, target2 = await _setup_sync_select_all_entries(hass)

    result = await _navigate_to_sync_step(hass, source.entry_id)
    assert result["step_id"] == "sync"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"select_all_targets": True, "sync_categories": ["geometry"]},
    )

    assert result["step_id"] == "sync_confirm"
    summary = result["description_placeholders"]["entries_summary"]
    assert target1.title in summary
    assert target2.title in summary


@pytest.mark.integration
async def test_sync_select_all_excludes_checked_targets(hass: HomeAssistant) -> None:
    """Select-all removes any checked cover from the sync; the rest stay targeted (#772).

    The multi-select becomes an *exclude* list when the toggle is on, not an
    override — a checked cover is removed from the all-covers sync.
    """
    source, target1, target2 = await _setup_sync_select_all_entries(hass)

    result = await _navigate_to_sync_step(hass, source.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "select_all_targets": True,
            "target_entries": [target1.entry_id],
            "sync_categories": ["geometry"],
        },
    )

    assert result["step_id"] == "sync_confirm"
    summary = result["description_placeholders"]["entries_summary"]
    assert target1.title not in summary
    assert target2.title in summary


@pytest.mark.integration
async def test_sync_toggle_off_uses_explicit_targets(hass: HomeAssistant) -> None:
    """Toggle off keeps target_entries as an explicit include list (today's behavior, #772)."""
    source, target1, target2 = await _setup_sync_select_all_entries(hass)

    result = await _navigate_to_sync_step(hass, source.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "select_all_targets": False,
            "target_entries": [target1.entry_id],
            "sync_categories": ["geometry"],
        },
    )

    assert result["step_id"] == "sync_confirm"
    summary = result["description_placeholders"]["entries_summary"]
    assert target1.title in summary
    assert target2.title not in summary


@pytest.mark.integration
async def test_sync_step_toggle_field_rendered_first(hass: HomeAssistant) -> None:
    """The 'Select all covers' toggle renders above the cover list (#772 follow-up)."""
    source, target1, target2 = await _setup_sync_select_all_entries(hass)

    result = await _navigate_to_sync_step(hass, source.entry_id)
    assert result["step_id"] == "sync"

    field_names = [str(key) for key in result["data_schema"].schema]

    toggle_index = field_names.index(CONF_SYNC_SELECT_ALL)
    targets_index = field_names.index("target_entries")

    assert toggle_index < targets_index


@pytest.mark.integration
async def test_sync_select_all_e2e_copies_geometry_not_per_window_fields(
    hass: HomeAssistant,
) -> None:
    """End-to-end: select-all sync copies geometry, leaves azimuth/entities untouched (#772)."""
    from tests.ha_helpers import VERTICAL_OPTIONS

    source_options = {**VERTICAL_OPTIONS, CONF_HEIGHT_WIN: 3.5, CONF_AZIMUTH: 180}
    target_options = {
        **VERTICAL_OPTIONS,
        CONF_HEIGHT_WIN: 2.1,
        CONF_AZIMUTH: 90,
        CONF_ENTITIES: ["cover.target_blind"],
    }
    source, target1, target2 = await _setup_sync_select_all_entries(
        hass,
        source_options=source_options,
        target1_options=target_options,
        target2_options=target_options,
    )

    result = await _navigate_to_sync_step(hass, source.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"select_all_targets": True, "sync_categories": ["geometry"]},
    )
    assert result["step_id"] == "sync_confirm"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["type"] in ("form", "menu", "create_entry")

    updated_target1 = hass.config_entries.async_get_entry(target1.entry_id)
    updated_target2 = hass.config_entries.async_get_entry(target2.entry_id)
    for updated in (updated_target1, updated_target2):
        assert updated.options[CONF_HEIGHT_WIN] == 3.5
        assert updated.options[CONF_AZIMUTH] == 90  # per-window field, untouched
        assert updated.options[CONF_ENTITIES] == ["cover.target_blind"]  # untouched


# ---------------------------------------------------------------------------
# Module-level helpers: _get_azimuth_edges, _get_geometry_schema,
#                       _build_glare_zones_schema
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_azimuth_edges_sums_fov():
    """_get_azimuth_edges returns fov_left + fov_right."""
    from custom_components.adaptive_cover_pro.config_flow import _get_azimuth_edges
    from custom_components.adaptive_cover_pro.const import CONF_FOV_LEFT, CONF_FOV_RIGHT

    result = _get_azimuth_edges({CONF_FOV_LEFT: 30, CONF_FOV_RIGHT: 45})
    assert result == 75


@pytest.mark.unit
def test_get_geometry_schema_unknown_type_returns_vertical():
    """_get_geometry_schema falls back to the vertical geometry for unknown types.

    The window-facing fields (azimuth / FOV / distance) are composed on for every
    type (#778), so the result is the vertical geometry schema extended with those
    fields rather than the bare GEOMETRY_VERTICAL_SCHEMA identity.
    """
    from custom_components.adaptive_cover_pro.config_flow import (
        _get_geometry_schema,
        GEOMETRY_VERTICAL_SCHEMA,
    )

    result = _get_geometry_schema("unknown_type")
    keys = {str(k) for k in result.schema}
    assert {str(k) for k in GEOMETRY_VERTICAL_SCHEMA.schema} <= keys
    for facing in (CONF_AZIMUTH, CONF_FOV_LEFT, CONF_FOV_RIGHT, CONF_DISTANCE):
        assert facing in keys
    # Unknown type has no policy → the FOV button is not added.
    assert CONF_FOV_COMPUTE not in keys


@pytest.mark.unit
def test_get_geometry_schema_unknown_type_with_hass_locale_composes_window_facing():
    """Unknown type + hass composes window-facing fields via the locale fallback.

    The locale-aware fallback still adds azimuth / FOV / distance with no button.
    """
    from unittest.mock import MagicMock

    from homeassistant.util.unit_system import METRIC_SYSTEM

    from custom_components.adaptive_cover_pro.config_flow import _get_geometry_schema

    hass = MagicMock()
    hass.config.units = METRIC_SYSTEM
    hass.states.get.return_value = None

    keys = {str(k) for k in _get_geometry_schema("unknown_type", hass).schema}
    for facing in (CONF_AZIMUTH, CONF_FOV_LEFT, CONF_FOV_RIGHT, CONF_DISTANCE):
        assert facing in keys
    assert CONF_FOV_COMPUTE not in keys


@pytest.mark.unit
def test_build_glare_zones_schema_with_no_options():
    """_build_glare_zones_schema with options=None uses default values."""
    from custom_components.adaptive_cover_pro.config_flow import (
        _build_glare_zones_schema,
    )
    import voluptuous as vol

    schema = _build_glare_zones_schema(options=None)
    assert isinstance(schema, vol.Schema)
    # Should have 4 zones * 5 fields (name, x, y, radius, z) = 20 keys
    assert len(schema.schema) == 20


@pytest.mark.unit
def test_build_glare_zones_schema_with_existing_options():
    """_build_glare_zones_schema uses existing option values as defaults."""
    from custom_components.adaptive_cover_pro.config_flow import (
        _build_glare_zones_schema,
    )
    import voluptuous as vol

    options = {"glare_zone_1_name": "My Zone", "glare_zone_1_x": 1.0}
    schema = _build_glare_zones_schema(options=options)
    assert isinstance(schema, vol.Schema)
    assert len(schema.schema) == 20


@pytest.mark.unit
def test_optional_entities_sets_missing_keys_to_none():
    """optional_entities sets keys not in user_input to None."""
    from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler

    flow = object.__new__(OptionsFlowHandler)
    user_input = {"present_key": "value"}
    flow.optional_entities(["present_key", "missing_key"], user_input)

    assert user_input["present_key"] == "value"
    assert user_input["missing_key"] is None


# ---------------------------------------------------------------------------
# OptionsFlow: init menu conditionals (blind_spot, glare_zones)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_options_flow_menu_includes_blind_spot_when_enabled(
    hass: HomeAssistant,
) -> None:
    """OptionsFlow init menu includes blind_spot when CONF_ENABLE_BLIND_SPOT is True."""
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    options = dict(VERTICAL_OPTIONS)
    options[CONF_ENABLE_BLIND_SPOT] = True

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "BS Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=options,
        entry_id="bs_menu_01",
        title="BS Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert "blind_spot" in result.get("menu_options", [])


@pytest.mark.integration
async def test_options_flow_menu_includes_glare_zones_for_blind_cover(
    hass: HomeAssistant,
) -> None:
    """OptionsFlow init menu includes glare_zones for cover_blind with CONF_ENABLE_GLARE_ZONES."""
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    options = dict(VERTICAL_OPTIONS)
    options[CONF_ENABLE_GLARE_ZONES] = True

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "GZ Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=options,
        entry_id="gz_menu_01",
        title="GZ Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert "glare_zones" in result.get("menu_options", [])


@pytest.mark.integration
async def test_options_flow_menu_returns_list_not_dict(
    hass: HomeAssistant,
) -> None:
    """menu_options must be a list so HA translates client-side (issue #227)."""
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Lang Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="lang_menu_01",
        title="Lang Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert isinstance(
        result["menu_options"], list
    ), f"menu_options should be a list for client-side translation, got {type(result['menu_options'])}"


@pytest.mark.integration
async def test_options_menu_order_follows_pipeline_layers(
    hass: HomeAssistant,
) -> None:
    """Options menu is ordered by the 4-layer pipeline model (#613).

    L1 physical (cover_entities, geometry, sun_tracking, blind_spot) →
    L2 positions (position, interp) →
    L3 handlers in priority order (weather 90, manual 80, custom, motion 75,
    cloud/light 60, climate/temperature 50, glare 45) →
    L4 global motion constraints (automation) → admin (sync, summary, …).
    """
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    options = dict(VERTICAL_OPTIONS)
    options[CONF_ENABLE_BLIND_SPOT] = True
    options[CONF_ENABLE_GLARE_ZONES] = True

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Order Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=options,
        entry_id="order_menu_01",
        title="Order Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    menu = result["menu_options"]

    def idx(step: str) -> int:
        assert step in menu, f"{step} missing from menu {menu}"
        return menu.index(step)

    # L1 → L2
    assert idx("cover_entities") < idx("geometry") < idx("sun_tracking")
    assert idx("sun_tracking") < idx("blind_spot") < idx("position")
    # L2a positions → L2b behavior
    assert idx("position") < idx("behavior")
    # L3 handlers in priority-descending order
    assert (
        idx("behavior")
        < idx("weather_override")
        < idx("manual_override")
        < idx("custom_position")
        < idx("motion_override")
        < idx("light_cloud")
        < idx("temperature_climate")
        < idx("glare_zones")
    )
    # L4 after all handlers, admin last
    assert idx("glare_zones") < idx("automation")
    assert idx("automation") < idx("summary")


@pytest.mark.integration
async def test_custom_position_step_exposes_priority_scale(
    hass: HomeAssistant,
) -> None:
    """The custom_position step renders the inline priority-scale visual (#613)."""
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Scale Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="scale_custom_01",
        title="Scale Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    if result["type"] == "menu":
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "custom_position"}
        )
    # custom_position is now a slot sub-menu (#945); the priority-scale visual
    # moved onto the focused per-slot page. Open the first free slot via "Add".
    assert result["step_id"] == "custom_position"
    assert result["type"] == "menu"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_custom_position"}
    )
    assert result["step_id"] == "custom_position_slot"
    scale = result["description_placeholders"]["priority_scale"]
    assert "Weather" in scale and "Default" in scale


@pytest.mark.integration
async def test_options_menu_every_entry_has_a_label(
    hass: HomeAssistant,
) -> None:
    """Every options-menu step must have a menu_options label (#613).

    Guards against a menu entry rendering as a blank row — the symptom when a
    new step (e.g. ``behavior``) is added to the menu but missing from
    ``options.step.init.menu_options`` in en.json.
    """
    import json
    import os

    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    from custom_components.adaptive_cover_pro.const import CONF_INTERP

    options = dict(VERTICAL_OPTIONS)
    options[CONF_ENABLE_BLIND_SPOT] = True
    options[CONF_ENABLE_GLARE_ZONES] = True
    options[CONF_INTERP] = True

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Label Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=options,
        entry_id="menu_label_01",
        title="Label Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    menu = result["menu_options"]

    en_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "custom_components",
        "adaptive_cover_pro",
        "translations",
        "en.json",
    )
    with open(en_path, encoding="utf-8") as f:
        labels = json.load(f)["options"]["step"]["init"]["menu_options"]

    missing = [k for k in menu if k not in labels]
    assert not missing, f"menu entries with no label (blank rows): {missing}"


def test_config_flow_does_not_use_system_language() -> None:
    """config_flow must not read the system language outside one sanctioned spot
    (issue #227).

    The #227 bug was server-side translation fetching keyed on
    ``self.hass.config.language`` (the system language) instead of the per-user
    flow language. Menu/step labels must always select the language via
    ``self.context.get("language", ...)`` — never ``hass.config.language``.

    Issue #905: HA never actually populates ``context["language"]`` for
    config/options flows, so the server-rendered config-summary block (issue
    #258's ``summary_i18n/`` bundle, narrative text that cannot be
    client-translated the way menu/step labels are) needs a best-effort
    fallback to the HA instance language. That fallback is deliberately
    isolated in the single ``_resolve_summary_language`` helper. Guard the
    rest of the module's source text so any *other* reintroduction of the
    system-language read — e.g. in menu or step label selection, the actual
    #227 failure mode — fails here.
    """
    import inspect

    import custom_components.adaptive_cover_pro.config_flow as cf_module

    source = inspect.getsource(cf_module)
    sanctioned_source = inspect.getsource(cf_module._resolve_summary_language)
    assert "config.language" in sanctioned_source, (
        "expected the sanctioned system-language fallback to live inside "
        "_resolve_summary_language (issue #905) — the guard below assumes it "
        "does"
    )
    source_outside_helper = source.replace(sanctioned_source, "")
    assert "config.language" not in source_outside_helper, (
        "config_flow must not read hass.config.language (system language) "
        "outside _resolve_summary_language — the flow user's language comes "
        "from self.context['language'] (issue #227); the one documented "
        "exception is the config-summary fallback (issue #905)"
    )


# ---------------------------------------------------------------------------
# OptionsFlow: parameterized form steps
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "step_id,user_input",
    [
        ("cover_entities", {CONF_ENTITIES: ["cover.test_blind"]}),
        (
            "geometry",
            {CONF_HEIGHT_WIN: 2.5, CONF_WINDOW_DEPTH: 0.0, CONF_SILL_HEIGHT: 0.0},
        ),
        (
            "position",
            {
                CONF_DEFAULT_HEIGHT: 60,
                CONF_MIN_POSITION: 0,
                CONF_ENABLE_MIN_POSITION: False,
                CONF_MAX_POSITION: 100,
                CONF_ENABLE_MAX_POSITION: False,
                "interp": False,
                "open_close_threshold": 50,
            },
        ),
        (
            "behavior",
            {
                CONF_SUNSET_OFFSET: 0,
                CONF_SUNRISE_OFFSET: 0,
                CONF_RETURN_SUNSET: False,
                CONF_INVERSE_STATE: False,
            },
        ),
        (
            "automation",
            {
                CONF_DELTA_POSITION: 5,
                CONF_DELTA_TIME: 2,
                CONF_START_TIME: "08:00:00",
                CONF_END_TIME: "20:00:00",
            },
        ),
        (
            "manual_override",
            {
                CONF_MANUAL_OVERRIDE_DURATION: {"hours": 1},
                CONF_MANUAL_OVERRIDE_RESET: False,
                CONF_MANUAL_IGNORE_INTERMEDIATE: False,
            },
        ),
        # custom_position is a slot sub-menu (#945), not a form step — its
        # save/clear round-trips are covered by the dedicated paged-flow tests
        # below and by tests/test_per_slot_config_pages.py.
        ("motion_override", {"motion_sensors": [], "motion_timeout": 300}),
        (
            "weather_override",
            {
                "weather_bypass_auto_control": False,
                "weather_wind_speed_threshold": "50",
                "weather_wind_direction_tolerance": "45",
                "weather_rain_threshold": "1",
                "weather_override_position": 0,
            },
        ),
    ],
)
async def test_options_flow_form_step_saves_and_returns_to_init(
    hass: HomeAssistant, step_id: str, user_input: dict
) -> None:
    """Each OptionsFlow form step saves input and returns to the init menu."""
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Form Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id=f"form_{step_id}_01",
        title="Form Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] in ("form", "menu")

    if result["type"] == "menu":
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": step_id}
        )

    assert result["step_id"] == step_id

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input
    )
    # Should return to init menu after saving
    assert result["type"] in ("form", "menu", "create_entry")


@pytest.mark.integration
async def test_options_flow_position_saves_position_tolerance(
    hass: HomeAssistant,
) -> None:
    """Submitting the behavior step persists CONF_POSITION_TOLERANCE (#591/#613)."""
    from custom_components.adaptive_cover_pro.const import CONF_POSITION_TOLERANCE
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Tol Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="tol_round_trip_01",
        title="Tol Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.options.async_init(entry.entry_id)
        if result["type"] == "menu":
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {"next_step_id": "behavior"}
            )
        assert result["step_id"] == "behavior"

        # position_tolerance now lives on the L2b behavior step; returns to menu.
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_SUNSET_OFFSET: 0,
                CONF_SUNRISE_OFFSET: 0,
                CONF_RETURN_SUNSET: False,
                CONF_INVERSE_STATE: False,
                CONF_POSITION_TOLERANCE: 8,
            },
        )
        # Finish the flow so the accumulated options are written to the entry.
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "done"}
        )
        await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert entry.options[CONF_POSITION_TOLERANCE] == 8


@pytest.mark.integration
async def test_options_flow_sun_tracking_step(hass: HomeAssistant) -> None:
    """OptionsFlow sun_tracking step saves and returns to init."""
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Sun Track Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="sun_track_01",
        title="Sun Track Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    if result["type"] == "menu":
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "sun_tracking"}
        )

    assert result["step_id"] == "sun_tracking"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**_SUN_TRACKING, "enable_glare_zones": False}
    )
    assert result["type"] in ("form", "menu", "create_entry")


@pytest.mark.integration
async def test_options_flow_sun_tracking_validation_error(hass: HomeAssistant) -> None:
    """OptionsFlow sun_tracking validation rejects max_elevation <= min_elevation."""
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Val Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="sun_val_01",
        title="Val Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    if result["type"] == "menu":
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "sun_tracking"}
        )

    bad_input = {
        **_SUN_TRACKING,
        "enable_glare_zones": False,
        CONF_MIN_ELEVATION: 40.0,
        CONF_MAX_ELEVATION: 30.0,  # max < min → error
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], bad_input
    )
    assert result["type"] == "form"
    assert result["step_id"] == "sun_tracking"
    assert CONF_MAX_ELEVATION in result.get("errors", {})


@pytest.mark.integration
async def test_options_flow_done_step_saves_entry(hass: HomeAssistant) -> None:
    """OptionsFlow done step creates a config entry with updated options."""
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Done Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="done_test_01",
        title="Done Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    if result["type"] == "menu":
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "done"}
        )

    assert result["type"] == "create_entry"


@pytest.mark.integration
async def test_options_flow_glare_zones_step_saves(hass: HomeAssistant) -> None:
    """OptionsFlow glare_zones step accepts input and returns to init."""
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    options = dict(VERTICAL_OPTIONS)
    options[CONF_ENABLE_GLARE_ZONES] = True

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "GZ Step Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=options,
        entry_id="gz_step_01",
        title="GZ Step Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert "glare_zones" in result.get("menu_options", [])

    # glare_zones is now a zone sub-menu (#945): open a zone via "Add", then
    # submit one zone under the generic un-suffixed field keys.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "glare_zones"}
    )
    assert result["step_id"] == "glare_zones"
    assert result["type"] == "menu"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_glare_zone"}
    )
    assert result["step_id"] == "glare_zone_slot"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "glare_zone_name": "East Window",
            "glare_zone_x": 0.0,
            "glare_zone_y": 1.0,
            "glare_zone_radius": 0.3,
            "glare_zone_z": 0.0,
        },
    )
    # Saving returns to the zone sub-menu; go Back to init then Save & Close so
    # the options entry commits, and confirm zone 1's suffixed keys are stored
    # (and no generic key leaked into storage).
    assert result["type"] == "menu"
    assert result["step_id"] == "glare_zones"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "init"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "done"}
    )
    assert result["type"] == "create_entry"
    assert result["data"]["glare_zone_1_name"] == "East Window"
    assert "glare_zone_name" not in result["data"]


@pytest.mark.integration
async def test_options_flow_custom_position_paged_save(hass: HomeAssistant) -> None:
    """A custom-position slot page saves under the slot's suffixed keys (#945).

    End-to-end through the real flow manager: init menu → custom_position sub-
    menu → "Add" opens the lowest free slot page (generic keys) → submitting it
    writes slot 1's suffixed storage keys and returns to the sub-menu.
    """
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Paged CP", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="paged_cp_01",
        title="Paged CP",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "custom_position"}
    )
    assert result["step_id"] == "custom_position"
    # No slots configured yet → only the Add + Back entries are listed.
    assert "custom_position_slot_1" not in result.get("menu_options", {})
    assert "add_custom_position" in result.get("menu_options", {})

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_custom_position"}
    )
    assert result["step_id"] == "custom_position_slot"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "custom_position_sensors": ["binary_sensor.alarm"],
            "custom_position": 100,
            "custom_position_priority": 100,
        },
    )
    # Back at the sub-menu, slot 1 is now listed and committed on Save & Close.
    assert result["type"] == "menu"
    assert result["step_id"] == "custom_position"
    assert "custom_position_slot_1" in result.get("menu_options", {})

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "init"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "done"}
    )
    assert result["type"] == "create_entry"
    saved = result["data"]
    assert saved["custom_position_1"] == 100
    assert saved["custom_position_priority_1"] == 100
    assert saved["custom_position_sensors_1"] == ["binary_sensor.alarm"]
    # No generic key leaked into storage.
    assert "custom_position" not in saved
    assert "custom_position_priority" not in saved


# ---------------------------------------------------------------------------
# Merged cover_entities + device association screen
# ---------------------------------------------------------------------------


def _mock_devices_from_entities(devices: dict):
    """Return a coroutine that always returns ``devices`` for _get_devices_from_entities."""

    async def _fake(*_args, **_kwargs):
        return devices

    return _fake


@pytest.mark.integration
async def test_config_flow_cover_entities_no_devices_skips_device_selector(
    hass: HomeAssistant,
) -> None:
    """When selected entities have no associated devices, cover_entities shows only once."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Test Blind", CONF_MODE: CoverType.BLIND}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "quick_setup"}
    )
    assert result["step_id"] == "cover_entities"

    with patch(
        "custom_components.adaptive_cover_pro.config_flow._get_devices_from_entities",
        side_effect=_mock_devices_from_entities({}),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ENTITIES: []}
        )

    assert result["step_id"] == "geometry"


@pytest.mark.integration
async def test_config_flow_cover_entities_with_devices_shows_device_selector(
    hass: HomeAssistant,
) -> None:
    """When entities have associated devices, cover_entities re-renders with device selector."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Test Blind", CONF_MODE: CoverType.BLIND}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "quick_setup"}
    )
    assert result["step_id"] == "cover_entities"

    devices = {"device_abc123": "My Blind Motor"}

    with patch(
        "custom_components.adaptive_cover_pro.config_flow._get_devices_from_entities",
        side_effect=_mock_devices_from_entities(devices),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ENTITIES: []}
        )

    assert result["type"] == "form"
    assert result["step_id"] == "cover_entities"
    schema_str_keys = [str(k) for k in result["data_schema"].schema]
    assert (
        CONF_DEVICE_ID in schema_str_keys
    ), f"Expected {CONF_DEVICE_ID} in schema, got: {schema_str_keys}"


@pytest.mark.integration
async def test_config_flow_cover_entities_standalone_selection_proceeds_to_geometry(
    hass: HomeAssistant,
) -> None:
    """Selecting 'None (standalone device)' proceeds to geometry without storing CONF_DEVICE_ID."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Test Blind", CONF_MODE: CoverType.BLIND}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "quick_setup"}
    )

    devices = {"device_abc123": "My Blind Motor"}

    with patch(
        "custom_components.adaptive_cover_pro.config_flow._get_devices_from_entities",
        side_effect=_mock_devices_from_entities(devices),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ENTITIES: []}
        )

    # Pass 2: select standalone
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ENTITIES: [], CONF_DEVICE_ID: "__standalone__"},
    )
    assert result["step_id"] == "geometry"


@pytest.mark.integration
async def test_config_flow_cover_entities_real_device_selection_stores_device_id(
    hass: HomeAssistant,
) -> None:
    """Selecting a real device stores CONF_DEVICE_ID and proceeds to geometry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Test Blind", CONF_MODE: CoverType.BLIND}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "quick_setup"}
    )

    devices = {"device_abc123": "My Blind Motor"}

    with patch(
        "custom_components.adaptive_cover_pro.config_flow._get_devices_from_entities",
        side_effect=_mock_devices_from_entities(devices),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ENTITIES: []}
        )

    flow = hass.config_entries.flow._progress.get(result["flow_id"])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ENTITIES: [], CONF_DEVICE_ID: "device_abc123"},
    )
    assert result["step_id"] == "geometry"
    if flow is not None:
        assert flow.config.get(CONF_DEVICE_ID) == "device_abc123"


@pytest.mark.integration
async def test_options_flow_cover_entities_no_device_in_menu(
    hass: HomeAssistant,
) -> None:
    """The 'device' menu item no longer appears in the options flow init menu."""
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Menu Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="no_device_menu_01",
        title="Menu Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert "device" not in result.get("menu_options", [])


@pytest.mark.integration
async def test_options_flow_cover_entities_combined_form_no_devices(
    hass: HomeAssistant,
) -> None:
    """Options cover_entities step shows only entity selector when no devices are available."""
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "CE Options Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="ce_opts_nodev_01",
        title="CE Options Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    if result["type"] == "menu":
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "cover_entities"}
        )

    assert result["step_id"] == "cover_entities"
    schema_str_keys = [str(k) for k in result["data_schema"].schema]
    # device selector should NOT be present when no devices are found
    assert CONF_DEVICE_ID not in schema_str_keys

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ENTITIES: []}
    )
    assert result["type"] in ("form", "menu", "create_entry")


@pytest.mark.integration
async def test_options_flow_cover_entities_combined_form_with_devices(
    hass: HomeAssistant,
) -> None:
    """Options cover_entities step includes device selector when devices are available."""
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "CE Options Dev Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="ce_opts_dev_01",
        title="CE Options Dev Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    devices = {"device_xyz789": "Smart Blind Motor"}
    with patch(
        "custom_components.adaptive_cover_pro.config_flow._get_devices_from_entities",
        side_effect=_mock_devices_from_entities(devices),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        if result["type"] == "menu":
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {"next_step_id": "cover_entities"}
            )

        assert result["step_id"] == "cover_entities"
        schema_str_keys = [str(k) for k in result["data_schema"].schema]
        assert (
            CONF_DEVICE_ID in schema_str_keys
        ), f"Expected {CONF_DEVICE_ID} in schema keys, got: {schema_str_keys}"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_ENTITIES: [], CONF_DEVICE_ID: "device_xyz789"}
        )

    assert result["type"] in ("form", "menu", "create_entry")


# ---------------------------------------------------------------------------
# Regression: clearing custom position slots (issue #323)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_options_flow_custom_position_clears_sensor_position_and_priority(
    hass: HomeAssistant,
) -> None:
    """Clearing a custom-position slot page must set its keys to None.

    Regression for issue #323 on the paged flow (#945): opening a configured
    slot and submitting it empty must overwrite the slot's stored values with
    None, not leave the old values in place.
    """
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    pre_options = dict(VERTICAL_OPTIONS)
    slot1 = CUSTOM_POSITION_SLOTS[1]
    pre_options[slot1["sensor"]] = "binary_sensor.slot_1"
    pre_options[slot1["position"]] = 25
    pre_options[slot1["priority"]] = 60

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Clear Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=pre_options,
        entry_id="custom_pos_clear_01",
        title="Clear Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "custom_position"}
    )
    assert result["step_id"] == "custom_position"
    # The configured slot 1 is listed; open it and submit the page empty.
    assert "custom_position_slot_1" in result.get("menu_options", {})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "custom_position_slot_1"}
    )
    assert result["step_id"] == "custom_position_slot"

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == "menu"  # back to the slot sub-menu

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "init"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "done"}
    )
    assert result["type"] == "create_entry"

    saved = result["data"]
    assert saved.get(slot1["sensor"]) is None, "legacy sensor mirror should be None"
    assert (
        saved.get(slot1["position"]) is None
    ), "position should be None after clearing"
    assert (
        saved.get(slot1["priority"]) is None
    ), "priority should be None after clearing"


@pytest.mark.integration
async def test_cleared_start_time_persists_blank(hass: HomeAssistant) -> None:
    """Clearing the start time in the automation step must not persist '00:00:00'.

    Regression for issue #492: a previously-saved start_time of '08:00:00' that
    the user clears (the form omits the key) must end up absent/None in stored
    options, never the blank sentinel '00:00:00' — otherwise the night position
    is suppressed every night after midnight.
    """
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    pre_options = dict(VERTICAL_OPTIONS)  # start_time = "08:00:00"
    pre_options[CONF_START_TIME] = "08:00:00"
    pre_options[CONF_END_TIME] = "20:00:00"

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Clear Time", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=pre_options,
        entry_id="clear_start_time_01",
        title="Clear Time",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "automation"}
    )
    assert result["step_id"] == "automation"

    # Submit the automation step omitting the time keys (cleared TimeSelectors).
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_DELTA_POSITION: 5, CONF_DELTA_TIME: 2},
    )
    assert result["type"] in ("form", "menu")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "done"}
    )
    assert result["type"] == "create_entry"

    saved = result["data"]
    assert (
        saved.get(CONF_START_TIME) is None
    ), f"start_time should be absent/None, got {saved.get(CONF_START_TIME)!r}"
    assert (
        saved.get(CONF_END_TIME) is None
    ), f"end_time should be absent/None, got {saved.get(CONF_END_TIME)!r}"


# ---------------------------------------------------------------------------
# Regression: clearing Is Sunny sensor in light_cloud step (issue #377)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_options_flow_light_cloud_clears_is_sunny_sensor(
    hass: HomeAssistant,
) -> None:
    """Clearing the Is Sunny binary sensor in options flow must set the key to None.

    Regression for issue #377: submitting an empty light_cloud form while a
    previously-saved CONF_IS_SUNNY_SENSOR exists must overwrite it with None, not
    leave the old entity_id in place. Same class of bug as #323 — the
    `optional_entities()` call site omitted the key.
    """
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    pre_options = dict(VERTICAL_OPTIONS)
    pre_options[CONF_IS_SUNNY_SENSOR] = "binary_sensor.sunny"

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Is Sunny Clear", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=pre_options,
        entry_id="is_sunny_clear_01",
        title="Is Sunny Clear",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "light_cloud"}
    )
    assert result["step_id"] == "light_cloud"

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] in ("form", "menu")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "done"}
    )
    assert result["type"] == "create_entry"

    assert (
        result["data"].get(CONF_IS_SUNNY_SENSOR) is None
    ), "CONF_IS_SUNNY_SENSOR should be None after clearing, not 'binary_sensor.sunny'"


@pytest.mark.integration
async def test_options_flow_venetian_geometry_saves_mode(hass: HomeAssistant) -> None:
    """Venetian geometry step saves venetian_mode to config entry options.

    Regression guard: if the geometry schema stops including CONF_VENETIAN_MODE,
    the saved options will silently drop the user's mode choice on reconfigure.
    """
    from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_VENETIAN_MODE] = VENETIAN_MODE_TILT_ONLY

    hass.states.async_set(
        "cover.test_blind",
        "open",
        {
            "current_position": 100,
            "current_tilt_position": 50,
            "supported_features": 143,
        },
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Venetian CF Test", CONF_SENSOR_TYPE: CoverType.VENETIAN},
        options=opts,
        entry_id="venetian_cf_01",
        title="Venetian CF Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] in ("form", "menu")

    if result["type"] == "menu":
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "geometry"}
        )

    assert result["step_id"] == "geometry"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_HEIGHT_WIN: 2.1,
            CONF_WINDOW_DEPTH: 0.0,
            CONF_SILL_HEIGHT: 0.0,
            CONF_VENETIAN_MODE: VENETIAN_MODE_TILT_ONLY,
        },
    )
    assert result["type"] in ("form", "menu", "create_entry")

    if result["type"] == "create_entry":
        assert result["data"].get(CONF_VENETIAN_MODE) == VENETIAN_MODE_TILT_ONLY


# ---------------------------------------------------------------------------
# Default-name derivation (device name vs. entity name vs. user-typed)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_create_new_schema_name_field_is_optional() -> None:
    """The "name" key in CONFIG_SCHEMA must be Optional, not Required (#771).

    ha-form enforces a voluptuous ``Required`` marker client-side and blocks
    submission of a blank text field before the request ever reaches the
    flow handler, so the existing device/entity-name auto-fill logic can
    never run. The field must be ``Optional`` so a blank "name" can reach
    Python and trigger the auto-fill.
    """
    import voluptuous as vol

    from custom_components.adaptive_cover_pro.config_flow import CONFIG_SCHEMA

    marker = next(k for k in CONFIG_SCHEMA.schema if k == "name")
    assert isinstance(marker, vol.Optional)
    assert not isinstance(marker, vol.Required)


def _register_cover_with_device(
    hass: HomeAssistant,
    *,
    device_name: str | None,
    entity_original_name: str | None = None,
    unique_id: str = "0x0001",
    object_id: str = "patio_stairs_shade",
) -> str:
    """Register a cover entity (optionally linked to a named device) and return its entity_id."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    anchor = MockConfigEntry(
        domain="zha",
        data={},
        entry_id=f"anchor_{unique_id}",
        title="anchor",
    )
    anchor.add_to_hass(hass)

    device_id: str | None = None
    if device_name is not None:
        device_reg = dr.async_get(hass)
        device = device_reg.async_get_or_create(
            config_entry_id=anchor.entry_id,
            identifiers={("zha", unique_id)},
            name=device_name,
        )
        device_id = device.id

    entity_reg = er.async_get(hass)
    entry = entity_reg.async_get_or_create(
        "cover",
        "zha",
        unique_id,
        suggested_object_id=object_id,
        device_id=device_id,
        original_name=entity_original_name,
        config_entry=anchor,
    )
    return entry.entity_id


@pytest.mark.integration
async def test_create_flow_title_uses_device_name_when_attached(
    hass: HomeAssistant,
) -> None:
    """If user leaves name blank and the first cover has an attached named device,
    the entry title is the device name verbatim (no type prefix, no 'Adaptive' word).
    """
    entity_id = _register_cover_with_device(
        hass, device_name="Patio Stairs Shade", entity_original_name="Patio Stairs"
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    # Submit create_new with an empty name — triggers auto-naming downstream
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "", CONF_MODE: CoverType.BLIND}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "quick_setup"}
    )
    assert result["step_id"] == "cover_entities"

    # Pass 1: submit the cover entity. The flow looks up the device and stores the
    # device name as the default; because _get_devices_from_entities also finds the
    # device, the form re-renders with a device picker.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITIES: [entity_id]}
    )
    assert result["step_id"] == "cover_entities"

    # Pass 2: pick standalone (we only care about the title, not device linking).
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ENTITIES: [entity_id], CONF_DEVICE_ID: "__standalone__"},
    )
    # Walk the remaining quick-setup steps
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _VERTICAL_GEOMETRY
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _SUN_TRACKING
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _POSITION
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.title == "Patio Stairs Shade"
    assert entry.data["name"] == "Patio Stairs Shade"
    # Marker must not be persisted to data/options
    assert "_title_is_device_name" not in entry.data
    assert "_title_is_device_name" not in entry.options


@pytest.mark.integration
async def test_create_flow_title_falls_back_to_adaptive_prefix_without_device(
    hass: HomeAssistant,
) -> None:
    """If the selected cover has no attached device, fall back to existing 'Adaptive {name}'
    title with the cover-type prefix attached.
    """
    entity_id = _register_cover_with_device(
        hass,
        device_name=None,
        entity_original_name="Living Room Blind",
        unique_id="0x0002",
        object_id="living_room_blind",
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "", CONF_MODE: CoverType.BLIND}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "quick_setup"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITIES: [entity_id]}
    )
    # No device → step proceeds straight to geometry
    assert result["step_id"] == "geometry"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _VERTICAL_GEOMETRY
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _SUN_TRACKING
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _POSITION
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.title == "Vertical Blind Adaptive Living Room Blind"
    assert entry.data["name"] == "Adaptive Living Room Blind"


@pytest.mark.integration
async def test_create_flow_user_typed_name_overrides_device_name(
    hass: HomeAssistant,
) -> None:
    """If the user types a name in create_new, it is respected and the type prefix
    is applied as usual — device-derived naming does NOT kick in.
    """
    entity_id = _register_cover_with_device(
        hass,
        device_name="Patio Stairs Shade",
        entity_original_name="Patio Stairs",
        unique_id="0x0003",
        object_id="patio_stairs_shade_user",
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    # User explicitly provides a name
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "My Cover", CONF_MODE: CoverType.BLIND}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "quick_setup"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITIES: [entity_id]}
    )
    # Device exists → form re-renders with device picker
    assert result["step_id"] == "cover_entities"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ENTITIES: [entity_id], CONF_DEVICE_ID: "__standalone__"},
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
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    entry = result["result"]
    # User name wins — device name is ignored
    assert entry.title == "Vertical Blind My Cover"
    assert entry.data["name"] == "My Cover"


@pytest.mark.integration
async def test_create_flow_blank_name_no_entities_gets_fallback_name(
    hass: HomeAssistant,
) -> None:
    """Blank name + zero cover entities selected must not reach finalization
    with an empty name (#771).

    The auto-fill in ``cover_entities`` Pass 1 only runs when at least one
    entity is selected; a user can legitimately submit zero entities (add the
    cover entity later via Configure). Now that "name" is Optional, this
    combination is reachable via the real UI and must still produce a sane
    non-empty fallback name/title instead of the malformed
    ``"Vertical Blind "`` (trailing space) title and empty
    ``entry.data["name"]``.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "", CONF_MODE: CoverType.BLIND}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "quick_setup"}
    )
    assert result["step_id"] == "cover_entities"

    # Zero entities selected — auto-fill in Pass 1 never runs.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITIES: []}
    )
    # No entities → no devices to discover → proceeds straight to geometry.
    assert result["step_id"] == "geometry"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _VERTICAL_GEOMETRY
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _SUN_TRACKING
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _POSITION
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.data["name"]
    assert entry.title != "Vertical Blind "


# ---------------------------------------------------------------------------
# OptionsFlow: position step exposes the My-preset entities toggle
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_options_flow_position_step_exposes_my_position_toggle(
    hass: HomeAssistant,
) -> None:
    """Position step must expose CONF_ENABLE_MY_POSITION_ENTITIES with default False."""
    import voluptuous as vol

    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MY_POSITION_ENTITIES,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "My-toggle Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="my_pos_toggle_01",
        title="My-toggle Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Navigate into the position step.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    if result["type"] == "menu":
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "position"}
        )
    assert result["step_id"] == "position"

    # Schema must contain the new toggle key with default False.
    schema_keys = result["data_schema"].schema
    matching = [
        k
        for k in schema_keys
        if isinstance(k, vol.Marker) and k.schema == CONF_ENABLE_MY_POSITION_ENTITIES
    ]
    assert (
        len(matching) == 1
    ), f"Expected exactly one schema entry for {CONF_ENABLE_MY_POSITION_ENTITIES}"
    assert matching[0].default() is False

    # Submitting the form with the toggle on must land True in the entry options.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_DEFAULT_HEIGHT: 60,
            CONF_MIN_POSITION: 0,
            CONF_ENABLE_MIN_POSITION: False,
            CONF_MAX_POSITION: 100,
            CONF_ENABLE_MAX_POSITION: False,
            "interp": False,
            "open_close_threshold": 50,
            CONF_ENABLE_MY_POSITION_ENTITIES: True,
        },
    )
    assert result["type"] in ("form", "menu", "create_entry")

    # Close the options flow to persist the changes via the done step.
    if result["type"] == "menu":
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "done"}
        )
    assert entry.options[CONF_ENABLE_MY_POSITION_ENTITIES] is True


@pytest.mark.integration
async def test_options_flow_position_step_clears_sunset_pos_when_omitted(
    hass: HomeAssistant,
) -> None:
    """Clearing sunset_position in options flow must write None, not keep old 0.

    Regression for issue #439: submitting the position form without
    CONF_SUNSET_POS while a prior value of 0 is stored must overwrite it
    with None, not leave 0 in place.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MY_POSITION_ENTITIES,
        CONF_SUNSET_POS,
        CONF_SUNSET_USE_MY,
    )
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    pre_options = dict(VERTICAL_OPTIONS)
    pre_options[CONF_SUNSET_POS] = 0  # seed the bug scenario

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Sunset Clear Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=pre_options,
        entry_id="sunset_clear_01",
        title="Sunset Clear Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "position"}
    )
    assert result["step_id"] == "position"

    # Submit position form WITHOUT CONF_SUNSET_POS (user cleared the field)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_DEFAULT_HEIGHT: 60,
            CONF_MIN_POSITION: 0,
            CONF_ENABLE_MIN_POSITION: False,
            CONF_MAX_POSITION: 100,
            CONF_ENABLE_MAX_POSITION: False,
            "interp": False,
            "open_close_threshold": 50,
            CONF_ENABLE_MY_POSITION_ENTITIES: False,
            CONF_SUNSET_USE_MY: False,
            # CONF_SUNSET_POS deliberately omitted — simulates user clearing the field
        },
    )
    # Navigate to done
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "done"}
    )
    assert result["type"] == "create_entry"

    saved = result["data"]
    assert (
        saved.get(CONF_SUNSET_POS) is None
    ), "sunset_position must be None after clearing, not retain previous value 0"


# ---------------------------------------------------------------------------
# Regression tests for issue #565 — create-flow persistence drop (Defect A & B)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_full_setup_persists_fov_and_window_width(
    hass: HomeAssistant,
) -> None:
    """Create flow must persist CONF_FOV_* and CONF_WINDOW_WIDTH to entry.options.

    Regression guard for issue #565 Defect A: the hardcoded 73-key allowlist in
    async_step_update silently dropped keys not in the list, including the fov
    values and window_width. This also exercises the FOV-from-measurements button
    re-render path (a transient ``fov_compute`` toggle that must NOT persist).
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Persist Test Blind", CONF_MODE: CoverType.BLIND},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "full_setup"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITIES: []}
    )
    # Geometry step now carries the window dims AND the FOV button (#778). First
    # submit: feed width + reveal depth and tick "Generate FOV from measurements"
    # → re-render on the same geometry step with the derived fov filled in.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **_VERTICAL_GEOMETRY,
            CONF_WINDOW_WIDTH: 1.6,
            CONF_WINDOW_DEPTH: 0.5,
            CONF_FOV_LEFT: 90,
            CONF_FOV_RIGHT: 90,
            CONF_FOV_COMPUTE: True,
        },
    )
    assert (
        result.get("step_id") == "geometry"
    ), f"expected re-render on button press, got {result!r}"
    # Second geometry submit without the button: keep the angles and advance.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **_VERTICAL_GEOMETRY,
            CONF_WINDOW_WIDTH: 1.6,
            CONF_WINDOW_DEPTH: 0.5,
            CONF_FOV_LEFT: 45,
            CONF_FOV_RIGHT: 45,
        },
    )
    # Then the behavioural sun-tracking step.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _SUN_TRACKING_VERTICAL
    )
    # 4-layer order (#613): position → behavior → L3 handlers → L4 automation.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _POSITION
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _BEHAVIOR
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _WEATHER_OVERRIDE
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _MANUAL_OVERRIDE
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _CUSTOM_POSITION
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _MOTION_OVERRIDE
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _LIGHT_CLOUD
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _TEMPERATURE_CLIMATE
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _AUTOMATION
    )
    assert result["type"] == "form"
    assert result["step_id"] == "summary"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"

    opts = result["result"].options
    assert opts.get(CONF_FOV_LEFT) == 45, (
        f"CONF_FOV_LEFT was dropped by async_step_update allowlist; "
        f"got {opts.get(CONF_FOV_LEFT)!r}"
    )
    assert opts.get(CONF_WINDOW_WIDTH) == 1.6, (
        f"CONF_WINDOW_WIDTH was dropped by async_step_update allowlist; "
        f"got {opts.get(CONF_WINDOW_WIDTH)!r}"
    )
    # The transient toggle must never reach persisted options.
    assert CONF_FOV_COMPUTE not in opts
    assert "fov_mode" not in opts


@pytest.mark.asyncio
async def test_create_flow_geometry_rerender_keeps_typed_azimuth() -> None:
    """Create-flow button re-render must not discard the azimuth the user typed.

    Regression guard for issue #565 Defect B, now on the geometry step (#778):
    ``_show_geometry_form`` must re-feed the just-submitted values via
    add_suggested_values_to_schema so a button re-render redraws with the user's
    typed azimuth, not the bare schema default (DEFAULT_WINDOW_AZIMUTH).
    """
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.adaptive_cover_pro.config_flow import ConfigFlowHandler

    flow = ConfigFlowHandler.__new__(ConfigFlowHandler)
    flow.hass = MagicMock()
    flow.hass.config.units = MagicMock()
    flow.hass.config.units.is_metric = True
    flow.hass.states.get.return_value = None
    flow.type_blind = CoverType.BLIND
    flow.config = {}
    flow.async_step_sun_tracking = AsyncMock(
        return_value={"type": "form", "step_id": "sun_tracking"}
    )

    # First submit: the user types azimuth 137 and presses the FOV-from-
    # measurements button → re-render path on the geometry step.
    result = await flow.async_step_geometry(
        {
            CONF_WINDOW_WIDTH: 2.0,
            CONF_WINDOW_DEPTH: 0.5,
            CONF_AZIMUTH: 137,
            CONF_FOV_LEFT: 30,
            CONF_FOV_RIGHT: 30,
            CONF_FOV_COMPUTE: True,
            CONF_DISTANCE: 0.5,
        }
    )
    assert result["type"] == "form", "expected re-render on button press"
    assert result["step_id"] == "geometry"

    # The re-rendered form must carry 137 as suggested_value for CONF_AZIMUTH —
    # the user's just-typed input must not be discarded.
    schema = result.get("data_schema")
    assert schema is not None, "re-render must return a data_schema"
    suggested_azimuth = None
    for marker in schema.schema:
        if str(marker) == CONF_AZIMUTH and marker.description:
            suggested_azimuth = marker.description.get("suggested_value")
            break
    assert suggested_azimuth == 137, (
        f"Re-rendered form must carry typed azimuth 137 as suggested_value, "
        f"got {suggested_azimuth!r}. "
        "Defect B: _show_geometry_form discards typed input."
    )
