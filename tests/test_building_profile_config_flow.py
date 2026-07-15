"""Config-flow surfaces for the Building Profile virtual entry type.

Two surfaces:
- Creating a ``cover_building_profile`` entry routes to a sensor-only step
  (no setup_mode / geometry / cover-entity selection) whose schema keys are
  exactly the ``BUILDING_PROFILE_SENSOR_KEYS`` pickers.
- A cover's options flow exposes a link selector listing profile entries
  (and a none/unlink choice), never other covers.
"""

from __future__ import annotations


import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.config_flow import (
    ConfigFlowHandler,
    OptionsFlowHandler,
)
from custom_components.adaptive_cover_pro.const import (
    BUILDING_PROFILE_SENSOR_KEYS,
    CONF_BUILDING_PROFILE_ID,
    CONF_LUX_ENTITY,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PROFILE_SENSOR_OVERRIDES,
    CONF_SENSOR_TYPE,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    DOMAIN,
    CoverType,
)


def _suggested_values(schema):
    """Map each schema key to its ``suggested_value`` (as options-flow prefill)."""
    return {
        str(m.schema): m.description.get("suggested_value")
        for m in schema.schema
        if hasattr(m, "description") and isinstance(m.description, dict)
    }


def _schema_keys(schema):
    return {str(marker.schema) for marker in schema.schema}


def _select_options(schema, key):
    """Return the SelectSelector option dicts for ``key`` in ``schema``."""
    for marker, sel in schema.schema.items():
        if str(marker.schema) == key:
            return sel.config["options"]
    raise AssertionError(f"{key} not in schema")


@pytest.mark.integration
async def test_create_building_profile(hass: HomeAssistant) -> None:
    """Building-profile creation is its own top-level menu option whose combined
    form collects the name and the building-level sensors in one step.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    # The cover-vs-profile menu is always shown.
    assert result["type"] == "menu"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "create_building_profile"}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "create_building_profile"
    # One combined form: the name field plus the building-profile sensor pickers.
    keys = _schema_keys(result["data_schema"])
    assert keys == {"name", *BUILDING_PROFILE_SENSOR_KEYS}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Main Building", CONF_LUX_ENTITY: "sensor.shared_lux"},
    )
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.data[CONF_SENSOR_TYPE] == CoverType.BUILDING_PROFILE
    assert entry.data["name"] == "Main Building"
    assert entry.options[CONF_LUX_ENTITY] == "sensor.shared_lux"


@pytest.mark.integration
async def test_building_profile_link_selector_lists_profiles(
    hass: HomeAssistant,
) -> None:
    """The cover link step lists profile entries and a none choice, not covers."""
    profile = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Bldg", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={},
        entry_id="profile_1",
        title="Bldg Profile",
    )
    profile.add_to_hass(hass)
    cover1 = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "C1", CONF_SENSOR_TYPE: CoverType.BLIND},
        options={},
        entry_id="cover_1",
        title="Cover One",
    )
    cover1.add_to_hass(hass)
    cover2 = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "C2", CONF_SENSOR_TYPE: CoverType.AWNING},
        options={},
        entry_id="cover_2",
        title="Cover Two",
    )
    cover2.add_to_hass(hass)

    flow = OptionsFlowHandler(cover1)
    flow.hass = hass

    result = await flow.async_step_building_profile()
    assert result["type"] == "form"
    assert result["step_id"] == "building_profile"

    opts = _select_options(result["data_schema"], CONF_BUILDING_PROFILE_ID)
    values = {o["value"] for o in opts}
    assert "profile_1" in values
    assert "cover_1" not in values
    assert "cover_2" not in values
    # A none/unlink choice is offered.
    assert "" in values or "__none__" in values


@pytest.mark.integration
async def test_building_profile_options_flow_shows_profile_menu(
    hass: HomeAssistant,
) -> None:
    """Clicking Configure on a Building Profile entry shows a small profile menu
    (shared sensors + overview + save), not the full cover-options menu.
    """
    profile = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Bldg", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={CONF_LUX_ENTITY: "sensor.existing_lux"},
        entry_id="profile_1",
        title="Main Building",
    )
    profile.add_to_hass(hass)

    flow = OptionsFlowHandler(profile)
    flow.hass = hass

    # async_step_init must NOT return a 14-item cover menu — it must return the
    # short profile menu.
    result = await flow.async_step_init()

    assert result["type"] == "menu"
    assert result["step_id"] == "init"
    assert result["menu_options"] == [
        "profile_sensors",
        "profile_overview",
        "profile_overrides",
        "done",
    ]


@pytest.mark.integration
async def test_building_profile_sensors_step_prefills_existing(
    hass: HomeAssistant,
) -> None:
    """The shared-sensors step shows only sensor pickers, pre-filled with values."""
    profile = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Bldg", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={CONF_LUX_ENTITY: "sensor.existing_lux"},
        entry_id="profile_1",
        title="Main Building",
    )
    profile.add_to_hass(hass)

    flow = OptionsFlowHandler(profile)
    flow.hass = hass

    result = await flow.async_step_profile_sensors()

    assert result["type"] == "form"
    assert result["step_id"] == "profile_sensors"
    schema_keys = _schema_keys(result["data_schema"])
    # Only sensor picker keys — no cover/geometry/handler keys.
    assert schema_keys <= BUILDING_PROFILE_SENSOR_KEYS
    # Existing sensor value is pre-filled via add_suggested_values_to_schema.
    suggested = {
        str(m.schema): m.description.get("suggested_value")
        for m in result["data_schema"].schema
        if hasattr(m, "description") and isinstance(m.description, dict)
    }
    assert suggested.get(CONF_LUX_ENTITY) == "sensor.existing_lux"


@pytest.mark.integration
async def test_building_profile_overview_step_renders(
    hass: HomeAssistant,
) -> None:
    """The overview step renders markdown scoped to this profile's linked covers."""
    profile = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Bldg", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={CONF_WEATHER_ENTITY: "weather.home"},
        entry_id="profile_1",
        title="Main Building",
    )
    profile.add_to_hass(hass)
    cover = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Living Room", CONF_SENSOR_TYPE: CoverType.BLIND},
        options={
            CONF_BUILDING_PROFILE_ID: "profile_1",
            CONF_WEATHER_ENTITY: "weather.home",
            "group": ["cover.living"],
        },
        entry_id="cover_1",
        title="Living Room",
    )
    cover.add_to_hass(hass)

    flow = OptionsFlowHandler(profile)
    flow.hass = hass

    result = await flow.async_step_profile_overview()

    assert result["type"] == "form"
    assert result["step_id"] == "profile_overview"
    overview = result["description_placeholders"]["overview"]
    assert "Shared sensors" in overview
    assert "Linked covers" in overview
    assert "Settings comparison" in overview
    assert "Living Room" in overview

    # Submitting returns to the menu.
    result = await flow.async_step_profile_overview({})
    assert result["type"] == "menu"


@pytest.mark.integration
async def test_profile_overrides_step_lists_and_clears(hass: HomeAssistant) -> None:
    """The Local Overrides step lists an override and clears it on submit."""
    profile = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Bldg", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={CONF_WEATHER_ENTITY: "weather.home"},
        entry_id="profile_1",
        title="Main Building",
    )
    profile.add_to_hass(hass)
    cover = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Bedroom", CONF_SENSOR_TYPE: CoverType.BLIND},
        options={
            CONF_BUILDING_PROFILE_ID: "profile_1",
            CONF_WEATHER_ENTITY: "weather.upstairs",
            CONF_PROFILE_SENSOR_OVERRIDES: [CONF_WEATHER_ENTITY],
            "group": ["cover.bed"],
        },
        entry_id="cover_1",
        title="Bedroom",
    )
    cover.add_to_hass(hass)

    flow = OptionsFlowHandler(profile)
    flow.hass = hass

    result = await flow.async_step_profile_overrides()
    assert result["step_id"] == "profile_overrides"
    # The overrides render once, as the clearable checkbox list (not duplicated
    # into the description).
    assert result["description_placeholders"]["overrides"] == ""
    schema = result["data_schema"].schema
    select_key = next(k for k in schema if str(k) == "clear_overrides")
    labels = [o["label"] for o in schema[select_key].config["options"]]
    assert any("Bedroom" in lbl and "Weather entity" in lbl for lbl in labels)

    # Clear the override → cover re-inherits the profile value, list emptied.
    await flow.async_step_profile_overrides(
        {"clear_overrides": ["cover_1|weather_entity"]}
    )
    updated = hass.config_entries.async_get_entry("cover_1")
    assert updated.options[CONF_WEATHER_ENTITY] == "weather.home"
    assert CONF_PROFILE_SENSOR_OVERRIDES not in updated.options


@pytest.mark.integration
async def test_profile_overrides_step_empty_state(hass: HomeAssistant) -> None:
    """With no overrides the step shows the empty-state message."""
    profile = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Bldg", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={CONF_WEATHER_ENTITY: "weather.home"},
        entry_id="profile_1",
        title="Main Building",
    )
    profile.add_to_hass(hass)
    flow = OptionsFlowHandler(profile)
    flow.hass = hass

    result = await flow.async_step_profile_overrides()
    assert result["step_id"] == "profile_overrides"
    assert "No local overrides" in result["description_placeholders"]["overrides"]


@pytest.mark.integration
async def test_cover_save_records_profile_override(hass: HomeAssistant) -> None:
    """Saving a linked cover whose sensor differs records it as an override."""
    profile = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Bldg", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={CONF_WEATHER_ENTITY: "weather.home"},
        entry_id="profile_1",
        title="Main Building",
    )
    profile.add_to_hass(hass)
    cover = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Bedroom", CONF_SENSOR_TYPE: CoverType.BLIND},
        options={CONF_BUILDING_PROFILE_ID: "profile_1", "group": ["cover.bed"]},
        entry_id="cover_1",
        title="Bedroom",
    )
    cover.add_to_hass(hass)

    flow = OptionsFlowHandler(cover)
    flow.hass = hass
    # Cover overrides the profile's weather entity locally.
    flow.options[CONF_WEATHER_ENTITY] = "weather.upstairs"
    result = await flow.async_step_done()

    assert result["type"] == "create_entry"
    assert result["data"][CONF_PROFILE_SENSOR_OVERRIDES] == [CONF_WEATHER_ENTITY]

    # Re-saving with the inherited value clears the override record.
    flow2 = OptionsFlowHandler(cover)
    flow2.hass = hass
    flow2.options[CONF_WEATHER_ENTITY] = "weather.home"
    result2 = await flow2.async_step_done()
    assert CONF_PROFILE_SENSOR_OVERRIDES not in result2["data"]


@pytest.mark.integration
async def test_building_profile_options_flow_saves_sensors(
    hass: HomeAssistant,
) -> None:
    """Submitting the profile_sensors step saves options and closes the flow."""
    profile = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Bldg", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={},
        entry_id="profile_1",
        title="Main Building",
    )
    profile.add_to_hass(hass)

    flow = OptionsFlowHandler(profile)
    flow.hass = hass

    # Submitting sensor data must produce create_entry (save).
    result = await flow.async_step_profile_sensors({CONF_LUX_ENTITY: "sensor.new_lux"})
    assert result["type"] == "create_entry"
    assert result["data"][CONF_LUX_ENTITY] == "sensor.new_lux"


# ---------------------------------------------------------------------------
# profile_line placeholder in options init step (issue #720 Part 3)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_init_step_profile_line_populated_for_linked_cover(
    hass: HomeAssistant,
) -> None:
    """async_step_init must populate profile_line with the profile title."""
    profile = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Bldg", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={},
        entry_id="profile_1",
        title="Main House",
    )
    profile.add_to_hass(hass)
    cover = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "C1", CONF_SENSOR_TYPE: CoverType.BLIND},
        options={CONF_BUILDING_PROFILE_ID: "profile_1"},
        entry_id="cover_1",
        title="Cover One",
    )
    cover.add_to_hass(hass)

    flow = OptionsFlowHandler(cover)
    flow.hass = hass
    # HA's OptionsFlow.config_entry resolves via self.handler (the entry_id).
    flow.handler = cover.entry_id

    result = await flow.async_step_init()

    placeholders = result.get("description_placeholders", {})
    assert (
        "profile_line" in placeholders
    ), "description_placeholders must include 'profile_line'"
    assert (
        "Main House" in placeholders["profile_line"]
    ), "profile_line must contain the linked profile's title"


@pytest.mark.integration
async def test_init_step_profile_line_empty_for_unlinked_cover(
    hass: HomeAssistant,
) -> None:
    """async_step_init must have an empty profile_line when cover is not linked."""
    cover = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "C1", CONF_SENSOR_TYPE: CoverType.BLIND},
        options={},
        entry_id="cover_1",
        title="Cover One",
    )
    cover.add_to_hass(hass)

    flow = OptionsFlowHandler(cover)
    flow.hass = hass
    # HA's OptionsFlow.config_entry resolves via self.handler (the entry_id).
    flow.handler = cover.entry_id

    result = await flow.async_step_init()

    placeholders = result.get("description_placeholders", {})
    profile_line = placeholders.get("profile_line", "MISSING")
    assert (
        profile_line == ""
    ), f"profile_line must be empty for unlinked cover, got: {profile_line!r}"


# ---------------------------------------------------------------------------
# Duplicate menu visibility — issue #732
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_user_menu_hides_duplicate_when_only_building_profile(
    hass: HomeAssistant,
) -> None:
    """'duplicate_existing' must not appear when only Building Profile entries exist.

    Building Profile entries (controls_cover=False) are not valid duplicate sources,
    so the option must be hidden rather than leading to source_not_found.
    """
    profile = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "My Smart Home", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={},
        entry_id="profile_1",
        title="Building Profile My Smart Home",
    )
    profile.add_to_hass(hass)

    handler = ConfigFlowHandler()
    handler.hass = hass

    result = await handler.async_step_user()

    assert result["type"] == "menu"
    assert "duplicate_existing" not in result["menu_options"]


# ---------------------------------------------------------------------------
# Create-wizard profile prefill — issue #851
# ---------------------------------------------------------------------------


def _profile_with_three_categories(hass: HomeAssistant) -> MockConfigEntry:
    """Profile carrying one sensor key from each of the three step categories."""
    profile = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Bldg", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={
            CONF_OUTSIDETEMP_ENTITY: "sensor.p_temp",
            CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.p_wind",
            CONF_LUX_ENTITY: "sensor.p_lux",
        },
        entry_id="profile_1",
        title="Main Building",
    )
    profile.add_to_hass(hass)
    return profile
