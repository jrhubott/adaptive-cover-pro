"""Entity unique_id uniqueness guard.

Catches unique_id collisions across all platforms for a single config entry.
A duplicate unique_id silently breaks the HA entity registry — one entity gets
the registration, the other is orphaned with no error logged.

Fails when any two entities (sensor, switch, binary_sensor, button) share a
unique_id. Adding a new entity spec that reuses an existing suffix or name will
be caught here immediately.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from tests.ha_helpers import VERTICAL_OPTIONS, setup_integration

pytestmark = pytest.mark.integration


@pytest.mark.integration
async def test_all_entity_unique_ids_are_unique(hass: HomeAssistant) -> None:
    """All entities for a single config entry must have distinct unique_ids."""
    entry = await setup_integration(hass, entry_id="uniqueness_guard_01")

    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    unique_ids = [e.unique_id for e in entries]

    duplicates = [uid for uid in set(unique_ids) if unique_ids.count(uid) > 1]
    assert (
        not duplicates
    ), f"Duplicate unique_ids for config entry {entry.entry_id!r}:\n" + "\n".join(
        f"  {uid!r}" for uid in sorted(duplicates)
    )


@pytest.mark.integration
async def test_unique_ids_do_not_change_across_cover_types(
    hass: HomeAssistant,
) -> None:
    """Unique_ids must be unique within each cover type's entity set.

    Registers one entry per cover type and asserts no intra-entry collisions.
    Cross-entry collisions are intentional (same suffix, different entry_id prefix).
    """
    from custom_components.adaptive_cover_pro.const import SensorType
    from tests.ha_helpers import HORIZONTAL_OPTIONS, TILT_OPTIONS

    registry = er.async_get(hass)

    for cover_type, opts in [
        (SensorType.BLIND, dict(VERTICAL_OPTIONS)),
        (SensorType.AWNING, dict(HORIZONTAL_OPTIONS)),
        (SensorType.TILT, dict(TILT_OPTIONS)),
    ]:
        entry = await setup_integration(
            hass,
            cover_type=cover_type,
            options=opts,
            entry_id=f"uniqueness_guard_{cover_type}",
        )
        entries = er.async_entries_for_config_entry(registry, entry.entry_id)
        unique_ids = [e.unique_id for e in entries]

        duplicates = [uid for uid in set(unique_ids) if unique_ids.count(uid) > 1]
        assert not duplicates, (
            f"Duplicate unique_ids for {cover_type} entry {entry.entry_id!r}:\n"
            + "\n".join(f"  {uid!r}" for uid in sorted(duplicates))
        )
