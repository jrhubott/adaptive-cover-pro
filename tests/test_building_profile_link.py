"""Copy-on-link and linked-cover picker hiding for Building Profiles.

- Linking copies the profile's non-empty shared-sensor subset into the cover's
  own options (Q2 per-key fallback: a blank profile field never wipes the
  cover's locally-configured value), stamps ``CONF_BUILDING_PROFILE_ID``, and
  triggers the cover's self-reload via ``async_update_entry``.
- A linked cover's weather-override / light-cloud schemas omit the
  profile-owned sensor pickers while keeping thresholds, modes, and the
  weather-retraction toggle.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.config_dynamic import (
    behavior_schema,
    building_profile_sensors_schema,
    light_cloud_schema,
    temperature_climate_schema,
    weather_override_schema,
)
from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler
from custom_components.adaptive_cover_pro.const import (
    CONF_BUILDING_PROFILE_ID,
    CONF_CLIMATE_MODE,
    CONF_CLOUDY_POSITION,
    CONF_DAYTIME_GATE_SENSORS,
    CONF_DAYTIME_GATE_TEMPLATE,
    CONF_DAYTIME_GATE_TEMPLATE_MODE,
    CONF_INVERSE_STATE,
    CONF_IRRADIANCE_ENTITY,
    CONF_IS_SUNNY_TEMPLATE_MODE,
    CONF_LUX_ENTITY,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_TEMPLATE_MODE,
    CONF_SENSOR_TYPE,
    CONF_SUNRISE_OFFSET,
    CONF_SUNRISE_TIME_ENTITY,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_TIME_ENTITY,
    CONF_WEATHER_IS_RAINING_TEMPLATE_MODE,
    CONF_WEATHER_IS_WINDY_TEMPLATE_MODE,
    CONF_WEATHER_RAIN_SENSOR,
    CONF_WEATHER_RAIN_THRESHOLD,
    DOMAIN,
    CoverType,
)


def _schema_keys(schema):
    return {str(marker.schema) for marker in schema.schema}


@pytest.mark.integration
async def test_link_copies_nonempty_subset(hass) -> None:
    """Linking copies non-empty profile keys; blank profile fields fall back."""
    profile = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Bldg", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={CONF_LUX_ENTITY: "sensor.lux", CONF_IRRADIANCE_ENTITY: ""},
        entry_id="profile_1",
        title="Bldg Profile",
    )
    profile.add_to_hass(hass)
    cover = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "C1", CONF_SENSOR_TYPE: CoverType.BLIND},
        options={CONF_IRRADIANCE_ENTITY: "sensor.local_irr"},
        entry_id="cover_1",
        title="Cover One",
    )
    cover.add_to_hass(hass)

    flow = OptionsFlowHandler(cover)
    flow.hass = hass
    flow.async_step_init = AsyncMock(return_value={"type": "menu"})

    real_update = hass.config_entries.async_update_entry
    calls: list = []

    def _spy(entry, **kwargs):
        calls.append(entry.entry_id)
        return real_update(entry, **kwargs)

    hass.config_entries.async_update_entry = _spy
    try:
        await flow.async_step_building_profile({CONF_BUILDING_PROFILE_ID: "profile_1"})
    finally:
        hass.config_entries.async_update_entry = real_update

    # Copied (profile non-empty).
    assert cover.options[CONF_LUX_ENTITY] == "sensor.lux"
    # Retained (profile blank → fallback to local value).
    assert cover.options[CONF_IRRADIANCE_ENTITY] == "sensor.local_irr"
    # Link stamped.
    assert cover.options[CONF_BUILDING_PROFILE_ID] == "profile_1"
    # The cover entry was updated (fires its self-reload listener).
    assert "cover_1" in calls


def test_linked_cover_hides_profile_pickers() -> None:
    """Linked covers hide profile-owned pickers but keep per-cover thresholds."""
    linked = {CONF_BUILDING_PROFILE_ID: "profile_1"}
    unlinked = {}

    wo_linked = _schema_keys(weather_override_schema(None, linked))
    wo_unlinked = _schema_keys(weather_override_schema(None, unlinked))
    assert CONF_WEATHER_RAIN_SENSOR in wo_unlinked
    assert CONF_WEATHER_RAIN_SENSOR not in wo_linked
    # Thresholds stay per-cover.
    assert CONF_WEATHER_RAIN_THRESHOLD in wo_linked

    lc_linked = _schema_keys(light_cloud_schema(None, {CONF_BUILDING_PROFILE_ID: "p"}))
    lc_unlinked = _schema_keys(light_cloud_schema(None, {}))
    assert CONF_LUX_ENTITY in lc_unlinked
    assert CONF_LUX_ENTITY not in lc_linked
    # Non-profile field remains.
    assert CONF_CLOUDY_POSITION in lc_linked


# ---------------------------------------------------------------------------
# New tests for issue #720: template-mode keys become fully profile-owned
# ---------------------------------------------------------------------------


def test_template_modes_in_building_profile_sensors_schema() -> None:
    """All four *_template_mode keys must appear in building_profile_sensors_schema."""
    keys = _schema_keys(building_profile_sensors_schema())
    assert (
        CONF_WEATHER_IS_RAINING_TEMPLATE_MODE in keys
    ), "weather_is_raining_template_mode must render in profile screen"
    assert (
        CONF_WEATHER_IS_WINDY_TEMPLATE_MODE in keys
    ), "weather_is_windy_template_mode must render in profile screen"
    assert (
        CONF_IS_SUNNY_TEMPLATE_MODE in keys
    ), "is_sunny_template_mode must render in profile screen"
    assert (
        CONF_DAYTIME_GATE_TEMPLATE_MODE in keys
    ), "daytime_gate_template_mode must render in profile screen"


def test_template_modes_hidden_on_linked_weather_and_light_schemas() -> None:
    """Template-mode keys are hidden on per-cover weather/light screens when linked."""
    linked = {CONF_BUILDING_PROFILE_ID: "profile_1"}
    unlinked: dict = {}

    wo_linked = _schema_keys(weather_override_schema(None, linked))
    wo_unlinked = _schema_keys(weather_override_schema(None, unlinked))
    # Present when unlinked, absent when linked.
    assert CONF_WEATHER_IS_RAINING_TEMPLATE_MODE in wo_unlinked
    assert CONF_WEATHER_IS_RAINING_TEMPLATE_MODE not in wo_linked
    assert CONF_WEATHER_IS_WINDY_TEMPLATE_MODE in wo_unlinked
    assert CONF_WEATHER_IS_WINDY_TEMPLATE_MODE not in wo_linked

    lc_linked = _schema_keys(light_cloud_schema(None, linked))
    lc_unlinked = _schema_keys(light_cloud_schema(None, unlinked))
    assert CONF_IS_SUNNY_TEMPLATE_MODE in lc_unlinked
    assert CONF_IS_SUNNY_TEMPLATE_MODE not in lc_linked


def test_outsidetemp_hidden_on_linked_climate_schema() -> None:
    """CONF_OUTSIDETEMP_ENTITY must be absent from temperature_climate_schema when linked."""
    linked = {CONF_BUILDING_PROFILE_ID: "profile_1"}
    unlinked: dict = {}

    climate_linked = _schema_keys(temperature_climate_schema(None, linked))
    climate_unlinked = _schema_keys(temperature_climate_schema(None, unlinked))

    assert (
        CONF_OUTSIDETEMP_ENTITY in climate_unlinked
    ), "outsidetemp_entity must appear when unlinked"
    assert (
        CONF_OUTSIDETEMP_ENTITY not in climate_linked
    ), "outsidetemp_entity must be hidden when linked"
    # Per-cover climate fields must remain on linked covers.
    assert CONF_CLIMATE_MODE in climate_linked
    assert CONF_PRESENCE_TEMPLATE_MODE in climate_linked


def test_behavior_schema_hides_profile_keys_on_linked_cover() -> None:
    """behavior_schema() hides profile-owned behavior keys when cover is linked."""
    linked = {CONF_BUILDING_PROFILE_ID: "profile_1"}
    unlinked: dict = {}

    bh_linked = _schema_keys(behavior_schema(linked))
    bh_unlinked = _schema_keys(behavior_schema(unlinked))

    # All five profile-owned behavior keys hidden when linked.
    for key in (
        CONF_SUNSET_TIME_ENTITY,
        CONF_SUNRISE_TIME_ENTITY,
        CONF_DAYTIME_GATE_SENSORS,
        CONF_DAYTIME_GATE_TEMPLATE,
        CONF_DAYTIME_GATE_TEMPLATE_MODE,
    ):
        assert key in bh_unlinked, f"{key} should be present when unlinked"
        assert key not in bh_linked, f"{key} should be hidden when linked"

    # Per-cover behavior fields must remain on linked covers.
    assert CONF_INVERSE_STATE in bh_linked
    assert CONF_SUNSET_OFFSET in bh_linked
    assert CONF_SUNRISE_OFFSET in bh_linked
