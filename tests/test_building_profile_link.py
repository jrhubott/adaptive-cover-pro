"""Copy-on-link and the inherit/override model for Building Profiles.

- Linking copies the profile's non-empty shared-sensor subset into the cover's
  own options (a blank profile field never wipes the cover's locally-configured
  value), stamps ``CONF_BUILDING_PROFILE_ID``, and triggers the cover's
  self-reload via ``async_update_entry``.
- Under the inherit/override model a linked cover SHOWS all profile-owned sensor
  pickers (pre-filled with the inherited value); changing one records a local
  override (``CONF_PROFILE_SENSOR_OVERRIDES``) that propagation must not wipe.
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


def test_linked_cover_shows_profile_pickers() -> None:
    """Inherit/override model: linked covers SHOW profile-owned pickers too.

    The pickers are pre-filled with the inherited value at the call site; the
    schema itself no longer drops them, so a cover can set a local override.
    """
    linked = {CONF_BUILDING_PROFILE_ID: "profile_1"}
    unlinked = {}

    wo_linked = _schema_keys(weather_override_schema(None, linked))
    wo_unlinked = _schema_keys(weather_override_schema(None, unlinked))
    assert CONF_WEATHER_RAIN_SENSOR in wo_unlinked
    assert CONF_WEATHER_RAIN_SENSOR in wo_linked
    assert CONF_WEATHER_RAIN_THRESHOLD in wo_linked

    lc_linked = _schema_keys(light_cloud_schema(None, {CONF_BUILDING_PROFILE_ID: "p"}))
    lc_unlinked = _schema_keys(light_cloud_schema(None, {}))
    assert CONF_LUX_ENTITY in lc_unlinked
    assert CONF_LUX_ENTITY in lc_linked
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


def test_template_modes_shown_on_linked_weather_and_light_schemas() -> None:
    """Template-mode keys render on per-cover weather/light screens when linked."""
    linked = {CONF_BUILDING_PROFILE_ID: "profile_1"}

    wo_linked = _schema_keys(weather_override_schema(None, linked))
    assert CONF_WEATHER_IS_RAINING_TEMPLATE_MODE in wo_linked
    assert CONF_WEATHER_IS_WINDY_TEMPLATE_MODE in wo_linked

    lc_linked = _schema_keys(light_cloud_schema(None, linked))
    assert CONF_IS_SUNNY_TEMPLATE_MODE in lc_linked


def test_outsidetemp_shown_on_linked_climate_schema() -> None:
    """CONF_OUTSIDETEMP_ENTITY renders on temperature_climate_schema when linked."""
    linked = {CONF_BUILDING_PROFILE_ID: "profile_1"}

    climate_linked = _schema_keys(temperature_climate_schema(None, linked))
    assert CONF_OUTSIDETEMP_ENTITY in climate_linked
    assert CONF_CLIMATE_MODE in climate_linked
    assert CONF_PRESENCE_TEMPLATE_MODE in climate_linked


def test_behavior_schema_shows_profile_keys_on_linked_cover() -> None:
    """behavior_schema() shows profile-owned behavior keys for linked covers too."""
    linked = {CONF_BUILDING_PROFILE_ID: "profile_1"}

    bh_linked = _schema_keys(behavior_schema(linked))

    for key in (
        CONF_SUNSET_TIME_ENTITY,
        CONF_SUNRISE_TIME_ENTITY,
        CONF_DAYTIME_GATE_SENSORS,
        CONF_DAYTIME_GATE_TEMPLATE,
        CONF_DAYTIME_GATE_TEMPLATE_MODE,
    ):
        assert key in bh_linked, f"{key} should render for a linked cover"

    assert CONF_INVERSE_STATE in bh_linked
    assert CONF_SUNSET_OFFSET in bh_linked
    assert CONF_SUNRISE_OFFSET in bh_linked


# ---------------------------------------------------------------------------
# Inherit/override helpers
# ---------------------------------------------------------------------------


def test_compute_override_keys() -> None:
    """Only profile-defined keys whose cover value differs are override keys."""
    from custom_components.adaptive_cover_pro.profile_link import compute_override_keys

    profile = {CONF_LUX_ENTITY: "sensor.roof", CONF_OUTSIDETEMP_ENTITY: "sensor.out"}
    cover = {
        CONF_LUX_ENTITY: "sensor.office",  # overridden
        CONF_OUTSIDETEMP_ENTITY: "sensor.out",  # inherited (==profile) → not override
        CONF_IRRADIANCE_ENTITY: "sensor.irr",  # profile blank → not an override key
    }
    assert compute_override_keys(cover, profile) == [CONF_LUX_ENTITY]
    # Nothing diverges → empty.
    assert compute_override_keys(dict(profile), profile) == []


def test_copy_profile_to_cover_skips_overrides() -> None:
    """Propagation re-copies inherited keys but never an overridden one."""
    from unittest.mock import MagicMock

    from custom_components.adaptive_cover_pro.const import (
        CONF_PROFILE_SENSOR_OVERRIDES,
    )
    from custom_components.adaptive_cover_pro.profile_link import _copy_profile_to_cover

    profile = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Bldg", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={
            CONF_LUX_ENTITY: "sensor.new_roof",
            CONF_OUTSIDETEMP_ENTITY: "sensor.out",
        },
        entry_id="profile_1",
    )
    cover = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "C", CONF_SENSOR_TYPE: CoverType.BLIND},
        options={
            CONF_BUILDING_PROFILE_ID: "profile_1",
            CONF_LUX_ENTITY: "sensor.office",  # overridden — must survive
            CONF_PROFILE_SENSOR_OVERRIDES: [CONF_LUX_ENTITY],
        },
        entry_id="cover_1",
    )
    hass = MagicMock()
    captured = {}
    hass.config_entries.async_update_entry = lambda entry, **kw: captured.update(kw)

    _copy_profile_to_cover(hass, profile, cover)

    opts = captured["options"]
    assert opts[CONF_LUX_ENTITY] == "sensor.office"  # override preserved
    assert opts[CONF_OUTSIDETEMP_ENTITY] == "sensor.out"  # inherited key copied
    assert opts[CONF_PROFILE_SENSOR_OVERRIDES] == [CONF_LUX_ENTITY]


def test_clear_cover_override_reinherits_and_removes() -> None:
    """Clearing re-inherits a profile-defined key and removes a profile-blank one."""
    from unittest.mock import MagicMock

    from custom_components.adaptive_cover_pro.const import (
        CONF_PROFILE_SENSOR_OVERRIDES,
    )
    from custom_components.adaptive_cover_pro.profile_link import clear_cover_override

    profile = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Bldg", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options={CONF_LUX_ENTITY: "sensor.roof"},
        entry_id="profile_1",
    )

    # Re-inherit: profile defines lux → cover value reset to the profile's.
    cover = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "C", CONF_SENSOR_TYPE: CoverType.BLIND},
        options={
            CONF_BUILDING_PROFILE_ID: "profile_1",
            CONF_LUX_ENTITY: "sensor.office",
            CONF_PROFILE_SENSOR_OVERRIDES: [CONF_LUX_ENTITY],
        },
        entry_id="cover_1",
    )
    hass = MagicMock()
    captured = {}
    hass.config_entries.async_update_entry = lambda entry, **kw: captured.update(kw)
    clear_cover_override(hass, profile, cover, CONF_LUX_ENTITY)
    assert captured["options"][CONF_LUX_ENTITY] == "sensor.roof"
    assert CONF_PROFILE_SENSOR_OVERRIDES not in captured["options"]

    # Remove: profile leaves irradiance blank → the local key is dropped.
    cover2 = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "C", CONF_SENSOR_TYPE: CoverType.BLIND},
        options={
            CONF_BUILDING_PROFILE_ID: "profile_1",
            CONF_IRRADIANCE_ENTITY: "sensor.local_irr",
        },
        entry_id="cover_2",
    )
    captured2 = {}
    hass.config_entries.async_update_entry = lambda entry, **kw: captured2.update(kw)
    clear_cover_override(hass, profile, cover2, CONF_IRRADIANCE_ENTITY)
    assert CONF_IRRADIANCE_ENTITY not in captured2["options"]
