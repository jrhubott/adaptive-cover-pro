"""A cover-group member coordinator stub with the real HA update machinery.

The suite's usual member stub is a bare ``MagicMock``. That swallows
``async_add_listener`` / ``async_update_listeners`` and replaces the refresh with
an ``AsyncMock``, so neither the group's member-notification wiring nor Home
Assistant's 10-second request-refresh debounce is observable through one — which
is exactly why both halves of issue #1082 went unnoticed. Tests that assert on
either need a coordinator that really has them.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.adaptive_cover_pro.const import GroupIntentKind
from custom_components.adaptive_cover_pro.pipeline.handlers import (
    GroupLockHandler,
    GroupSceneHandler,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.adaptive_cover_pro.pipeline.types import GroupIntent

_LOGGER = logging.getLogger(__name__)

# The winner this stub's pipeline settles on when no group intent claims it.
# Any non-group handler name works; this one matches the live system the issue
# was reported from.
UNCLAIMED_WINNER = "motion_timeout"


@dataclass(frozen=True, slots=True)
class MemberData:
    """The one field of a member's ``coordinator.data`` the group reads."""

    diagnostics: dict | None


class RealMemberCoordinator(DataUpdateCoordinator[MemberData]):
    """Minimal ACP member: real listeners, real debouncer, fake pipeline.

    ``_async_update_data`` stands in for the pipeline closely enough for the
    group's purposes — a live group intent wins and reports the matching group
    handler, otherwise a local handler does. ``diagnostics`` is a plain
    attribute so a test can move the member's climate mode and then notify.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize with the member entry as the coordinator's config entry."""
        super().__init__(hass, _LOGGER, name=entry.entry_id, config_entry=entry)
        self._group_intents: dict[str, GroupIntent] = {}
        self.pipeline_winner_name: str | None = None
        self.diagnostics: dict | None = None

    def set_group_intent(self, group_id: str, intent: GroupIntent | None) -> None:
        """Mirror the real coordinator's per-group intent registry."""
        if intent is None:
            self._group_intents.pop(group_id, None)
        else:
            self._group_intents[group_id] = intent

    async def _async_update_data(self) -> MemberData:
        """Re-evaluate the winner from the currently-claimed intents."""
        intent = next(iter(self._group_intents.values()), None)
        if intent is None:
            self.pipeline_winner_name = UNCLAIMED_WINNER
        elif intent.kind is GroupIntentKind.LOCK:
            self.pipeline_winner_name = GroupLockHandler.name
        else:
            self.pipeline_winner_name = GroupSceneHandler.name
        return MemberData(diagnostics=self.diagnostics)
