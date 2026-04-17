"""Tests for the one-shot entity registry migration (issue #219).

Verifies that async_prune_legacy_entities removes the two known-legacy
binary_sensor orphans left over from the v2.14.3 unique_id rename, leaves all
other entities untouched, and sets the prune flag so it never runs again.

These tests exercise the migration function directly (not via async_setup_entry)
so they control flag state independently of integration setup.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_SENSOR_TYPE,
    DOMAIN,
    SensorType,
)
from custom_components.adaptive_cover_pro.migrations import (
    _LEGACY_BINARY_SENSOR_SUFFIXES,
    _PRUNE_V1_FLAG,
    async_prune_legacy_entities,
)
from tests.ha_helpers import VERTICAL_OPTIONS

pytestmark = pytest.mark.integration

ENTRY_ID = "migrate_test_01"


def _make_entry(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    """Create and add a config entry to hass without running async_setup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Migration Test", CONF_SENSOR_TYPE: SensorType.BLIND},
        options=dict(VERTICAL_OPTIONS) if options is None else options,
        entry_id=ENTRY_ID,
        title="Migration Test",
    )
    entry.add_to_hass(hass)
    return entry


def _seed_legacy_orphans(
    hass: HomeAssistant, entry: MockConfigEntry
) -> list[er.RegistryEntry]:
    """Inject the two legacy binary_sensor orphans into the entity registry."""
    registry = er.async_get(hass)
    orphans = []
    for suffix, suggested_id in [
        ("_Sun Infront", "legacy_sun_infront"),
        ("_Manual Override", "legacy_manual_override"),
    ]:
        orphan = registry.async_get_or_create(
            "binary_sensor",
            DOMAIN,
            f"{entry.entry_id}{suffix}",
            config_entry=entry,
            suggested_object_id=suggested_id,
        )
        orphans.append(orphan)
    return orphans


def _seed_modern_entity(
    hass: HomeAssistant, entry: MockConfigEntry
) -> er.RegistryEntry:
    """Inject a modern binary_sensor entity (should never be removed)."""
    registry = er.async_get(hass)
    return registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{entry.entry_id}_sun_motion",
        config_entry=entry,
        suggested_object_id="modern_sun_infront",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_prune_removes_legacy_orphans(hass: HomeAssistant) -> None:
    """Legacy orphan binary_sensors are removed by the migration."""
    entry = _make_entry(hass)
    orphans = _seed_legacy_orphans(hass, entry)
    orphan_entity_ids = [o.entity_id for o in orphans]

    registry = er.async_get(hass)
    for eid in orphan_entity_ids:
        assert registry.async_get(eid) is not None, (
            f"Orphan {eid} should exist pre-migration"
        )

    await async_prune_legacy_entities(hass, entry)
    await hass.async_block_till_done()

    for eid in orphan_entity_ids:
        assert registry.async_get(eid) is None, (
            f"Orphan {eid} should be removed post-migration"
        )


@pytest.mark.integration
async def test_prune_leaves_modern_entities_intact(hass: HomeAssistant) -> None:
    """Modern binary_sensor entities (with key-based unique_ids) are never removed."""
    entry = _make_entry(hass)
    _seed_legacy_orphans(hass, entry)
    modern = _seed_modern_entity(hass, entry)

    await async_prune_legacy_entities(hass, entry)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert registry.async_get(modern.entity_id) is not None, (
        f"Modern entity {modern.entity_id} should not be removed"
    )


@pytest.mark.integration
async def test_prune_sets_flag(hass: HomeAssistant) -> None:
    """Migration writes the _orphan_prune_v1 flag to entry.options."""
    entry = _make_entry(hass)
    assert not entry.options.get(_PRUNE_V1_FLAG), (
        "Flag should not be set before migration"
    )

    await async_prune_legacy_entities(hass, entry)
    await hass.async_block_till_done()

    updated_entry = hass.config_entries.async_get_entry(ENTRY_ID)
    assert updated_entry.options.get(_PRUNE_V1_FLAG) is True, (
        "Flag should be set after migration"
    )


@pytest.mark.integration
async def test_prune_is_idempotent(hass: HomeAssistant) -> None:
    """Running the migration twice is safe — second run is a no-op due to flag."""
    entry = _make_entry(hass)
    _seed_legacy_orphans(hass, entry)

    # First run — removes orphans + sets flag
    await async_prune_legacy_entities(hass, entry)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entries_after_first = {
        e.entity_id for e in er.async_entries_for_config_entry(registry, ENTRY_ID)
    }

    # Second run — flag is set, should be a no-op
    updated_entry = hass.config_entries.async_get_entry(ENTRY_ID)
    await async_prune_legacy_entities(hass, updated_entry)
    await hass.async_block_till_done()

    entries_after_second = {
        e.entity_id for e in er.async_entries_for_config_entry(registry, ENTRY_ID)
    }
    assert entries_after_first == entries_after_second, (
        "Second migration run should not change the entity registry"
    )


@pytest.mark.integration
async def test_prune_no_effect_when_no_orphans(hass: HomeAssistant) -> None:
    """Migration with no orphans present still sets flag and removes nothing."""
    entry = _make_entry(hass)
    modern = _seed_modern_entity(hass, entry)

    await async_prune_legacy_entities(hass, entry)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert registry.async_get(modern.entity_id) is not None, (
        "Modern entity should still exist"
    )
    updated_entry = hass.config_entries.async_get_entry(ENTRY_ID)
    assert updated_entry.options.get(_PRUNE_V1_FLAG) is True


@pytest.mark.integration
async def test_prune_does_not_touch_non_binary_sensor_domains(
    hass: HomeAssistant,
) -> None:
    """Entities on sensor, switch, and button domains are never touched."""
    entry = _make_entry(hass)
    registry = er.async_get(hass)

    # Seed a sensor and a switch with legacy-looking names to confirm they're safe
    sensor_entry = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{ENTRY_ID}_Manual Override",
        config_entry=entry,
        suggested_object_id="manual_override_sensor",
    )
    switch_entry = registry.async_get_or_create(
        "switch",
        DOMAIN,
        f"{ENTRY_ID}_Sun Infront",
        config_entry=entry,
        suggested_object_id="sun_infront_switch",
    )

    await async_prune_legacy_entities(hass, entry)
    await hass.async_block_till_done()

    assert registry.async_get(sensor_entry.entity_id) is not None, (
        "Sensor entity must not be removed"
    )
    assert registry.async_get(switch_entry.entity_id) is not None, (
        "Switch entity must not be removed"
    )


@pytest.mark.integration
async def test_legacy_suffixes_cover_both_known_patterns() -> None:
    """Sanity-check: the constant contains both expected legacy suffixes."""
    assert "_Sun Infront" in _LEGACY_BINARY_SENSOR_SUFFIXES
    assert "_Manual Override" in _LEGACY_BINARY_SENSOR_SUFFIXES
    assert len(_LEGACY_BINARY_SENSOR_SUFFIXES) == 2, (
        "Update this test if new legacy suffixes are added"
    )
