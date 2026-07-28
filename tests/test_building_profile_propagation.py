"""Live propagation and deletion cleanup for Building Profiles (Commit 4).

- When a Building Profile entry's options change, every cover linked to it
  (``CONF_BUILDING_PROFILE_ID == profile.entry_id``) receives the profile's
  non-empty shared-sensor subset and is reloaded via ``async_update_entry``.
  Unlinked covers are untouched.
- When a Building Profile entry is deleted while covers are still linked,
  ``async_remove_entry`` clears ``CONF_BUILDING_PROFILE_ID`` from every linked
  cover while leaving the last-copied sensor IDs in place (Q5 active sweep).
"""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro import (
    _async_profile_propagate,
    async_remove_entry,
)
from custom_components.adaptive_cover_pro.const import (
    BUILDING_PROFILE_SENSOR_KEYS,
    CONF_BUILDING_PROFILE_ID,
    CONF_IRRADIANCE_ENTITY,
    CONF_LUX_ENTITY,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PROFILE_SENSOR_OVERRIDES,
    CONF_SENSOR_TYPE,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_IS_WINDY_TEMPLATE,
    CONF_WEATHER_RAIN_SENSOR,
    DOMAIN,
    CoverType,
)

pytestmark = pytest.mark.integration


def _profile(hass, options):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Bldg", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options=options,
        entry_id="profile_1",
        title="Bldg Profile",
    )
    entry.add_to_hass(hass)
    return entry


def _cover(hass, entry_id, options):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": entry_id, CONF_SENSOR_TYPE: CoverType.BLIND},
        options=options,
        entry_id=entry_id,
        title=entry_id,
    )
    entry.add_to_hass(hass)
    return entry


async def test_profile_change_propagates_to_linked_covers(hass) -> None:
    """A profile change copies the new value into every linked cover only."""
    profile = _profile(hass, {CONF_LUX_ENTITY: "sensor.new_lux"})
    linked_a = _cover(
        hass,
        "cover_a",
        {CONF_BUILDING_PROFILE_ID: "profile_1", CONF_LUX_ENTITY: "sensor.old"},
    )
    linked_b = _cover(
        hass,
        "cover_b",
        {CONF_BUILDING_PROFILE_ID: "profile_1", CONF_LUX_ENTITY: "sensor.old"},
    )
    unlinked = _cover(hass, "cover_c", {CONF_LUX_ENTITY: "sensor.local"})

    real_update = hass.config_entries.async_update_entry
    updated: list[str] = []

    def _spy(entry, **kwargs):
        updated.append(entry.entry_id)
        return real_update(entry, **kwargs)

    hass.config_entries.async_update_entry = _spy
    try:
        await _async_profile_propagate(hass, profile)
    finally:
        hass.config_entries.async_update_entry = real_update

    # Both linked covers got the new profile value.
    assert linked_a.options[CONF_LUX_ENTITY] == "sensor.new_lux"
    assert linked_b.options[CONF_LUX_ENTITY] == "sensor.new_lux"
    # Each linked cover was updated (fires its self-reload listener).
    assert "cover_a" in updated
    assert "cover_b" in updated
    # Unlinked cover untouched.
    assert unlinked.options[CONF_LUX_ENTITY] == "sensor.local"
    assert "cover_c" not in updated


async def test_propagate_keeps_cover_values_for_never_set_profile_keys(hass) -> None:
    """A profile that leaves a field blank never wipes a cover's own value.

    After one submit of the profile's sensor form every shared key is present
    in the profile's options, almost all of them ``None`` — that is what
    ``optional_entities`` writes for a field the user did not fill in. The
    listener must not read those blanks as instructions to clear (issue #1085).
    """
    profile = _profile(
        hass,
        dict.fromkeys(BUILDING_PROFILE_SENSOR_KEYS)
        | {CONF_WEATHER_ENTITY: "weather.home"},
    )
    linked = _cover(
        hass,
        "cover_a",
        {
            CONF_BUILDING_PROFILE_ID: "profile_1",
            CONF_LUX_ENTITY: "sensor.local_lux",
            CONF_OUTSIDETEMP_ENTITY: "sensor.local_outside",
        },
    )

    await _async_profile_propagate(hass, profile)

    assert linked.options[CONF_WEATHER_ENTITY] == "weather.home"
    assert linked.options[CONF_LUX_ENTITY] == "sensor.local_lux"
    assert linked.options[CONF_OUTSIDETEMP_ENTITY] == "sensor.local_outside"


async def test_propagate_profile_clears_removes_only_inherited_keys(hass) -> None:
    """A key the profile save emptied leaves the covers inheriting it (#1085).

    ``_copy_profile_to_cover`` only ever applies non-empty profile keys, so
    on its own a cleared field would leave the last-copied sensor sitting on
    every linked cover. A cover carrying a genuine local override on the same
    key keeps its own choice, and an unlinked cover is never touched.
    """
    from custom_components.adaptive_cover_pro.profile_link import (
        classify_profile_sensor_source,
        propagate_profile_clears,
    )

    profile = _profile(hass, {CONF_LUX_ENTITY: "sensor.lux"})
    inheriting = _cover(
        hass,
        "cover_a",
        {
            CONF_BUILDING_PROFILE_ID: "profile_1",
            CONF_WEATHER_RAIN_SENSOR: "sensor.rain",
            CONF_WEATHER_IS_WINDY_TEMPLATE: "{{ 'off' }}",
            CONF_LUX_ENTITY: "sensor.lux",
        },
    )
    overriding = _cover(
        hass,
        "cover_b",
        {
            CONF_BUILDING_PROFILE_ID: "profile_1",
            CONF_WEATHER_RAIN_SENSOR: "sensor.own_rain",
            CONF_PROFILE_SENSOR_OVERRIDES: [CONF_WEATHER_RAIN_SENSOR],
        },
    )
    unlinked = _cover(hass, "cover_c", {CONF_WEATHER_RAIN_SENSOR: "sensor.rain"})

    propagate_profile_clears(
        hass,
        profile,
        frozenset({CONF_WEATHER_RAIN_SENSOR, CONF_WEATHER_IS_WINDY_TEMPLATE}),
    )

    # Inheriting cover: the cleared keys are gone, not merely stale. Keys the
    # save did not touch stay put.
    assert CONF_WEATHER_RAIN_SENSOR not in inheriting.options
    assert CONF_WEATHER_IS_WINDY_TEMPLATE not in inheriting.options
    assert inheriting.options[CONF_LUX_ENTITY] == "sensor.lux"
    # Overriding cover: its genuine local override survives a profile clear.
    assert overriding.options[CONF_WEATHER_RAIN_SENSOR] == "sensor.own_rain"
    # Unlinked cover: out of scope entirely.
    assert unlinked.options[CONF_WEATHER_RAIN_SENSOR] == "sensor.rain"
    # Nothing holds the sensor any more, so diagnostics and the Building
    # Overview classify it exactly as one that was never configured.
    assert classify_profile_sensor_source(
        CONF_WEATHER_RAIN_SENSOR, dict(inheriting.options), dict(profile.options)
    ) == ("local", None)


async def test_propagate_profile_clears_writes_nothing_when_nothing_cleared(
    hass,
) -> None:
    """The common save — nothing cleared — must not write a single entry."""
    from custom_components.adaptive_cover_pro.profile_link import (
        propagate_profile_clears,
    )

    profile = _profile(hass, {CONF_LUX_ENTITY: "sensor.lux"})
    _cover(
        hass,
        "cover_a",
        {CONF_BUILDING_PROFILE_ID: "profile_1", CONF_LUX_ENTITY: "sensor.lux"},
    )

    real_update = hass.config_entries.async_update_entry
    updated: list[str] = []

    def _spy(entry, **kwargs):
        updated.append(entry.entry_id)
        return real_update(entry, **kwargs)

    hass.config_entries.async_update_entry = _spy
    try:
        propagate_profile_clears(hass, profile, frozenset())
        # A cleared key no cover actually holds is not a reason to write either.
        propagate_profile_clears(hass, profile, frozenset({CONF_WEATHER_RAIN_SENSOR}))
    finally:
        hass.config_entries.async_update_entry = real_update

    assert updated == []


async def test_profile_delete_clears_linked_cover_ids(hass) -> None:
    """Deleting a profile clears the link but keeps last-copied sensor IDs."""
    profile = _profile(hass, {CONF_LUX_ENTITY: "sensor.lux"})
    linked_a = _cover(
        hass,
        "cover_a",
        {
            CONF_BUILDING_PROFILE_ID: "profile_1",
            CONF_LUX_ENTITY: "sensor.lux",
            CONF_IRRADIANCE_ENTITY: "sensor.irr",
        },
    )
    linked_b = _cover(
        hass,
        "cover_b",
        {CONF_BUILDING_PROFILE_ID: "profile_1", CONF_LUX_ENTITY: "sensor.lux"},
    )

    await async_remove_entry(hass, profile)

    # Link cleared on both covers.
    assert CONF_BUILDING_PROFILE_ID not in linked_a.options
    assert CONF_BUILDING_PROFILE_ID not in linked_b.options
    # Last-copied sensor IDs left in place — covers keep functioning.
    assert linked_a.options[CONF_LUX_ENTITY] == "sensor.lux"
    assert linked_a.options[CONF_IRRADIANCE_ENTITY] == "sensor.irr"
    assert linked_b.options[CONF_LUX_ENTITY] == "sensor.lux"
