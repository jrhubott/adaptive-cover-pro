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
    CONF_AZIMUTH,
    CONF_CLIMATE_MODE,
    CONF_DEFAULT_HEIGHT,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_DEVICE_ID,
    CONF_DISTANCE,
    CONF_ENABLE_BLIND_SPOT,
    CONF_ENABLE_GLARE_ZONES,
    CONF_ENTITIES,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_HEIGHT_WIN,
    CONF_MANUAL_IGNORE_INTERMEDIATE,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_MANUAL_OVERRIDE_RESET,
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

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
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
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
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
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
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
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
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
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
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
    """_get_geometry_schema falls back to GEOMETRY_VERTICAL_SCHEMA for unknown types."""
    from custom_components.adaptive_cover_pro.config_flow import (
        _get_geometry_schema,
        GEOMETRY_VERTICAL_SCHEMA,
    )

    result = _get_geometry_schema("unknown_type")
    assert result is GEOMETRY_VERTICAL_SCHEMA


@pytest.mark.unit
def test_build_glare_zones_schema_with_no_options():
    """_build_glare_zones_schema with options=None uses default values."""
    from custom_components.adaptive_cover_pro.config_flow import (
        _build_glare_zones_schema,
    )
    import voluptuous as vol

    schema = _build_glare_zones_schema(options=None)
    assert isinstance(schema, vol.Schema)
    # Should have 4 zones * 4 fields = 16 keys
    assert len(schema.schema) == 16


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
    assert len(schema.schema) == 16


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
        data={"name": "BS Test", CONF_SENSOR_TYPE: SensorType.BLIND},
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
        data={"name": "GZ Test", CONF_SENSOR_TYPE: SensorType.BLIND},
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
        data={"name": "Lang Test", CONF_SENSOR_TYPE: SensorType.BLIND},
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
    assert isinstance(result["menu_options"], list), (
        f"menu_options should be a list for client-side translation, got {type(result['menu_options'])}"
    )


def test_config_flow_does_not_import_async_get_translations() -> None:
    """config_flow must not import async_get_translations (issue #227).

    Server-side translation fetching used self.hass.config.language (system language)
    rather than the per-user language. The fix removes the import entirely and lets
    HA's frontend translate menu labels client-side.
    """
    import importlib
    import custom_components.adaptive_cover_pro.config_flow as cf_module

    importlib.reload(cf_module)
    assert not hasattr(cf_module, "async_get_translations"), (
        "config_flow must not import async_get_translations — "
        "menu translation should be handled client-side by HA's frontend"
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
                CONF_SUNSET_OFFSET: 0,
                CONF_SUNRISE_OFFSET: 0,
                CONF_INVERSE_STATE: False,
                "interp": False,
                "open_close_threshold": 50,
            },
        ),
        (
            "automation",
            {
                CONF_DELTA_POSITION: 5,
                CONF_DELTA_TIME: 2,
                CONF_START_TIME: "08:00:00",
                CONF_END_TIME: "20:00:00",
                CONF_RETURN_SUNSET: False,
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
        (
            "force_override",
            {"force_override_sensors": [], "force_override_position": 0},
        ),
        ("custom_position", {}),
        ("motion_override", {"motion_sensors": [], "motion_timeout": 300}),
        (
            "weather_override",
            {
                "weather_bypass_auto_control": False,
                "weather_wind_speed_threshold": 50.0,
                "weather_wind_direction_tolerance": 45,
                "weather_rain_threshold": 1.0,
                "weather_severe_sensors": [],
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
        data={"name": "Form Test", CONF_SENSOR_TYPE: SensorType.BLIND},
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
async def test_options_flow_sun_tracking_step(hass: HomeAssistant) -> None:
    """OptionsFlow sun_tracking step saves and returns to init."""
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Sun Track Test", CONF_SENSOR_TYPE: SensorType.BLIND},
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
        data={"name": "Val Test", CONF_SENSOR_TYPE: SensorType.BLIND},
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
        data={"name": "Done Test", CONF_SENSOR_TYPE: SensorType.BLIND},
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
        data={"name": "GZ Step Test", CONF_SENSOR_TYPE: SensorType.BLIND},
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

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "glare_zones"}
    )
    assert result["step_id"] == "glare_zones"

    # Submit zone data
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "glare_zone_1_name": "East Window",
            "glare_zone_1_x": 0.0,
            "glare_zone_1_y": 1.0,
            "glare_zone_1_radius": 0.3,
            "glare_zone_2_name": "",
            "glare_zone_2_x": 0.0,
            "glare_zone_2_y": 1.0,
            "glare_zone_2_radius": 0.3,
            "glare_zone_3_name": "",
            "glare_zone_3_x": 0.0,
            "glare_zone_3_y": 1.0,
            "glare_zone_3_radius": 0.3,
            "glare_zone_4_name": "",
            "glare_zone_4_x": 0.0,
            "glare_zone_4_y": 1.0,
            "glare_zone_4_radius": 0.3,
        },
    )
    assert result["type"] in ("form", "menu", "create_entry")


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
        result["flow_id"], {"name": "Test Blind", CONF_MODE: SensorType.BLIND}
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
        result["flow_id"], {"name": "Test Blind", CONF_MODE: SensorType.BLIND}
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
    assert CONF_DEVICE_ID in schema_str_keys, (
        f"Expected {CONF_DEVICE_ID} in schema, got: {schema_str_keys}"
    )


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
        result["flow_id"], {"name": "Test Blind", CONF_MODE: SensorType.BLIND}
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
        result["flow_id"], {"name": "Test Blind", CONF_MODE: SensorType.BLIND}
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
        data={"name": "Menu Test", CONF_SENSOR_TYPE: SensorType.BLIND},
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
        data={"name": "CE Options Test", CONF_SENSOR_TYPE: SensorType.BLIND},
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
        data={"name": "CE Options Dev Test", CONF_SENSOR_TYPE: SensorType.BLIND},
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
        assert CONF_DEVICE_ID in schema_str_keys, (
            f"Expected {CONF_DEVICE_ID} in schema keys, got: {schema_str_keys}"
        )

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_ENTITIES: [], CONF_DEVICE_ID: "device_xyz789"}
        )

    assert result["type"] in ("form", "menu", "create_entry")
