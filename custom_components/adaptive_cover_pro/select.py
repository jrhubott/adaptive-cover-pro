"""Select platform for the Adaptive Cover Pro integration (issue #790).

Group-only today: the scene picker on Cover Group entries. The platform is
forwarded only for group entries (``GROUP_PLATFORMS`` in ``__init__``), but
the capability guard stays so a future forward for cover entries cannot
accidentally create a select.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_SENSOR_TYPE
from .coordinator import AdaptiveConfigEntry
from .cover_types import get_policy
from .group_entities import build_group_selects


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: AdaptiveConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the select platform (cover groups only)."""
    if not get_policy(config_entry.data.get(CONF_SENSOR_TYPE)).is_orchestrator:
        return
    async_add_entities(
        build_group_selects(
            config_entry.entry_id, hass, config_entry, config_entry.runtime_data
        )
    )
