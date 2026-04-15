"""One-shot entity registry migrations for Adaptive Cover Pro.

Each migration runs at most once per config entry, tracked by a flag stored in
entry.options.  Migrations must be idempotent — safe to call again if the flag
is somehow missing.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

# Option key written after the prune runs so it never fires again.
_PRUNE_V1_FLAG = "_orphan_prune_v1"

# Legacy unique_ids that became orphaned when binary_sensor.py was changed from
# using display names to internal keys (commit c8c064b, v2.14.3, issue #154).
# Format: suffix appended to entry_id (i.e. the part after the first underscore).
_LEGACY_BINARY_SENSOR_SUFFIXES = frozenset(
    [
        "_Sun Infront",   # superseded by _sun_motion
        "_Manual Override",  # superseded by _manual_override
    ]
)


async def async_prune_legacy_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove orphaned binary_sensor registry rows left over from the v2.14.3 unique_id rename.

    Targets only the two known-legacy patterns on the binary_sensor platform.
    Writes a flag to entry.options so this runs exactly once per config entry.
    """
    if entry.options.get(_PRUNE_V1_FLAG):
        return

    registry = er.async_get(hass)
    removed: list[str] = []

    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity_entry.domain != "binary_sensor":
            continue
        uid: str = entity_entry.unique_id or ""
        if any(uid.endswith(suffix) for suffix in _LEGACY_BINARY_SENSOR_SUFFIXES):
            _LOGGER.info(
                "Removing legacy orphaned entity %s (unique_id=%s)",
                entity_entry.entity_id,
                uid,
            )
            registry.async_remove(entity_entry.entity_id)
            removed.append(entity_entry.entity_id)

    if removed:
        _LOGGER.info(
            "Pruned %d legacy orphaned entity/entities for config entry %s: %s",
            len(removed),
            entry.entry_id,
            removed,
        )

    # Mark as done whether or not anything was removed — prevents repeated scans.
    hass.config_entries.async_update_entry(
        entry,
        options={**entry.options, _PRUNE_V1_FLAG: True},
    )
