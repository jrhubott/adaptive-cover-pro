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
    light_cloud_schema,
    weather_override_schema,
)
from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler
from custom_components.adaptive_cover_pro.const import (
    CONF_BUILDING_PROFILE_ID,
    CONF_CLOUDY_POSITION,
    CONF_IRRADIANCE_ENTITY,
    CONF_LUX_ENTITY,
    CONF_SENSOR_TYPE,
    CONF_SHOW_WEATHER_RETRACTION,
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
    """Linked covers hide profile-owned pickers but keep thresholds + toggle."""
    linked = {CONF_BUILDING_PROFILE_ID: "profile_1", CONF_SHOW_WEATHER_RETRACTION: True}
    unlinked = {CONF_SHOW_WEATHER_RETRACTION: True}

    wo_linked = _schema_keys(weather_override_schema(None, linked))
    wo_unlinked = _schema_keys(weather_override_schema(None, unlinked))
    assert CONF_WEATHER_RAIN_SENSOR in wo_unlinked
    assert CONF_WEATHER_RAIN_SENSOR not in wo_linked
    # Toggle + thresholds stay per-cover.
    assert CONF_SHOW_WEATHER_RETRACTION in wo_linked
    assert CONF_WEATHER_RAIN_THRESHOLD in wo_linked

    lc_linked = _schema_keys(light_cloud_schema(None, {CONF_BUILDING_PROFILE_ID: "p"}))
    lc_unlinked = _schema_keys(light_cloud_schema(None, {}))
    assert CONF_LUX_ENTITY in lc_unlinked
    assert CONF_LUX_ENTITY not in lc_linked
    # Non-profile field remains.
    assert CONF_CLOUDY_POSITION in lc_linked
