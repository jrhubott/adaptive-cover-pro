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

from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler
from custom_components.adaptive_cover_pro.const import (
    BUILDING_PROFILE_SENSOR_KEYS,
    CONF_BUILDING_PROFILE_ID,
    CONF_LUX_ENTITY,
    CONF_MODE,
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
)


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
    """A building_profile selection routes to the sensor-only creation step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    if result["type"] == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_new"}
        )
    assert result["step_id"] == "create_new"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Main Building", CONF_MODE: CoverType.BUILDING_PROFILE},
    )
    # Routes to the sensor-only step, NOT the normal setup_mode path.
    assert result["type"] == "form"
    assert result["step_id"] == "building_profile_sensors"
    assert _schema_keys(result["data_schema"]) == set(BUILDING_PROFILE_SENSOR_KEYS)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LUX_ENTITY: "sensor.shared_lux"}
    )
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.data[CONF_SENSOR_TYPE] == CoverType.BUILDING_PROFILE
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
