"""Tests for the cm → metres config-entry migration (VERSION 1 → 2).

Exercises async_migrate_entry directly to verify:
- v1 entries (window_width and glare-zone coords in cm) are divided by 100
- Entries already in metres (sentinel ≤ 5) are not re-divided (idempotent)
- Version is bumped to 2 in every case
- Entries with no affected fields are left unchanged aside from the version bump
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro import async_migrate_entry
from custom_components.adaptive_cover_pro.const import (
    CONF_SENSOR_TYPE,
    CONF_WINDOW_WIDTH,
    DOMAIN,
    SensorType,
)

pytestmark = pytest.mark.integration


def _make_entry(
    hass: HomeAssistant, options: dict, version: int = 1
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Migration Test", CONF_SENSOR_TYPE: SensorType.BLIND},
        options=options,
        version=version,
        title="Migration Test",
    )
    entry.add_to_hass(hass)
    return entry


async def test_v1_window_width_converted_to_metres(hass: HomeAssistant) -> None:
    """CONF_WINDOW_WIDTH of 100 cm becomes 1.0 m."""
    entry = _make_entry(hass, {CONF_WINDOW_WIDTH: 100})
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options[CONF_WINDOW_WIDTH] == 1.0
    assert entry.version == 2


async def test_v1_glare_zone_coordinates_converted_to_metres(
    hass: HomeAssistant,
) -> None:
    """All four glare-zone slot coordinates are divided by 100."""
    options = {
        CONF_WINDOW_WIDTH: 150,
        "glare_zone_1_name": "Desk",
        "glare_zone_1_x": 50,
        "glare_zone_1_y": 200,
        "glare_zone_1_radius": 30,
        "glare_zone_2_x": -80,
        "glare_zone_2_y": 300,
        "glare_zone_2_radius": 50,
    }
    entry = _make_entry(hass, options)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options[CONF_WINDOW_WIDTH] == 1.5
    assert entry.options["glare_zone_1_x"] == 0.5
    assert entry.options["glare_zone_1_y"] == 2.0
    assert entry.options["glare_zone_1_radius"] == 0.3
    assert entry.options["glare_zone_2_x"] == -0.8
    assert entry.options["glare_zone_2_y"] == 3.0
    assert entry.options["glare_zone_2_radius"] == 0.5
    # Name is untouched by the numeric migration
    assert entry.options["glare_zone_1_name"] == "Desk"


async def test_values_at_or_below_sentinel_left_alone(hass: HomeAssistant) -> None:
    """Stored values ≤ 5 are assumed to already be metres and not re-divided."""
    options = {
        CONF_WINDOW_WIDTH: 1.2,  # already metres
        "glare_zone_1_x": 0.5,
        "glare_zone_1_y": 2.0,
        "glare_zone_1_radius": 0.3,
    }
    entry = _make_entry(hass, options)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options[CONF_WINDOW_WIDTH] == 1.2
    assert entry.options["glare_zone_1_x"] == 0.5
    assert entry.options["glare_zone_1_y"] == 2.0
    assert entry.options["glare_zone_1_radius"] == 0.3
    assert entry.version == 2


async def test_migration_is_idempotent(hass: HomeAssistant) -> None:
    """Running the migration twice (second time on a v2 entry) is a no-op."""
    entry = _make_entry(
        hass,
        {CONF_WINDOW_WIDTH: 200, "glare_zone_1_y": 150},
    )
    await async_migrate_entry(hass, entry)
    snapshot = dict(entry.options)
    # Second run — entry is already v2 so migration short-circuits
    await async_migrate_entry(hass, entry)
    assert entry.options == snapshot
    assert entry.version == 2


async def test_migration_with_no_affected_fields_only_bumps_version(
    hass: HomeAssistant,
) -> None:
    """An entry with no window_width or glare zones just gets its version bumped."""
    options = {"azimuth": 180, "fov_left": 90, "fov_right": 90}
    entry = _make_entry(hass, options)
    await async_migrate_entry(hass, entry)
    assert entry.options == options
    assert entry.version == 2


async def test_negative_x_coordinate_migrated(hass: HomeAssistant) -> None:
    """Negative cm values (offset left of window centre) migrate correctly."""
    entry = _make_entry(hass, {"glare_zone_1_x": -150})
    await async_migrate_entry(hass, entry)
    assert entry.options["glare_zone_1_x"] == -1.5


async def test_zero_values_preserved(hass: HomeAssistant) -> None:
    """A value of 0 is within the sentinel band and stays 0."""
    entry = _make_entry(
        hass,
        {"glare_zone_1_x": 0, "glare_zone_1_y": 0, CONF_WINDOW_WIDTH: 120},
    )
    await async_migrate_entry(hass, entry)
    assert entry.options["glare_zone_1_x"] == 0
    assert entry.options["glare_zone_1_y"] == 0
    assert entry.options[CONF_WINDOW_WIDTH] == 1.2
