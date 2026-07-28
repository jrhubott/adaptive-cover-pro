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
    CONF_BUILDING_PROFILE_ID,
    CONF_IRRADIANCE_ENTITY,
    CONF_LUX_ENTITY,
    CONF_SENSOR_TYPE,
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
