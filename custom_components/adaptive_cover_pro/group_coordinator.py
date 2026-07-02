"""Coordinator for the virtual Cover Group entry type (issue #790, Phase 1).

Orchestrates a roster of member covers:

* **ACP members** (config entries) are commanded through each member's own
  coordinator (``async_apply_user_position`` / ``async_reset_manual_overrides``
  / the ``automatic_control`` toggle) so the member's gates, inverse-state,
  and override semantics all apply.
* **Generic members** (plain ``cover.*`` entity_ids, "adopt mode") are
  commanded through a group-owned ``CoverCommandService`` so capability
  fallback (open/close-only covers), unavailable-cover skips, and no-op
  suppression come for free.

Phase 1 acts only on explicit user actions (scene buttons/select, bulk
switches); it never moves covers autonomously, so there is no boot-time
fan-out path. Scene targets are resolved per member via the member policy's
``position_for_scene`` — a scene is an intent, not a shared absolute
position.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_ENTITIES,
    CONF_MEMBER_COVERS,
    CONF_MEMBER_ENTRIES,
    CONF_SENSOR_TYPE,
    DEFAULT_DELTA_POSITION,
    DEFAULT_DELTA_TIME,
    DOMAIN,
    POSITION_CLOSED,
    POSITION_OPEN,
    CoverType,
    GroupScene,
    GroupState,
)
from .cover_types import get_policy
from .managers.cover_command import CoverCommandService
from .managers.cover_command.state_store import PositionContext
from .managers.grace_period import GracePeriodManager

_LOGGER = logging.getLogger(__name__)

# Generic ``cover.*`` members carry no ACP geometry; adopt mode drives them as
# plain HA position covers — the vertical-blind policy's axis semantics
# (position attribute, open/close fallback, no inversion) are exactly that.
_ADOPT_COVER_TYPE = CoverType.BLIND


@dataclass(frozen=True, slots=True)
class GroupAggregates:
    """Aggregate view over the group's member covers, read by the sensors."""

    position: int | None
    state: GroupState
    member_positions: dict[str, int | None]


class GroupCoordinator(DataUpdateCoordinator[GroupAggregates]):
    """Runtime orchestrator for one cover-group config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the group coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_group_{entry.entry_id}",
            config_entry=entry,
        )
        self.entry = entry
        self.active_scene: GroupScene | None = None
        self._unsub_state: CALLBACK_TYPE | None = None
        self._adopt_policy = get_policy(_ADOPT_COVER_TYPE)
        self._grace_mgr = GracePeriodManager(_LOGGER)
        self._cmd_svc = CoverCommandService(
            hass,
            _LOGGER,
            _ADOPT_COVER_TYPE,
            self._grace_mgr,
        )

    # ---- Roster resolution ------------------------------------------------ #

    def resolved_members(self) -> list[tuple[ConfigEntry, object]]:
        """ACP members whose entry exists and whose coordinator is loaded.

        Every ``runtime_data`` access is null-guarded: during a member reload
        the attribute is briefly unset, and a removed member's id may linger
        in the roster until the next options edit. Both are silently skipped —
        absence is non-membership for this cycle.
        """
        members: list[tuple[ConfigEntry, object]] = []
        for entry_id in self.entry.options.get(CONF_MEMBER_ENTRIES, []):
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if entry is None:
                continue
            coordinator = getattr(entry, "runtime_data", None)
            if coordinator is None:
                _LOGGER.debug(
                    "Group %s: member %s has no loaded coordinator; skipping",
                    self.entry.entry_id,
                    entry_id,
                )
                continue
            members.append((entry, coordinator))
        return members

    def member_cover_entities(self) -> list[str]:
        """All member cover entity_ids: ACP members' covers, then generic."""
        entities: list[str] = []
        for entry_id in self.entry.options.get(CONF_MEMBER_ENTRIES, []):
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if entry is None:
                continue
            entities.extend(entry.options.get(CONF_ENTITIES, []))
        entities.extend(self.entry.options.get(CONF_MEMBER_COVERS, []))
        return entities

    # ---- Fan-out operations ------------------------------------------------ #

    async def async_activate_scene(self, scene: GroupScene) -> None:
        """Fan a scene out to every member, resolving the target per policy."""
        trigger = f"group_scene_{scene}"
        for entry, coordinator in self.resolved_members():
            policy = get_policy(entry.data[CONF_SENSOR_TYPE])
            target = policy.position_for_scene(scene)
            for entity_id in entry.options.get(CONF_ENTITIES, []):
                await coordinator.async_apply_user_position(
                    entity_id,
                    target,
                    trigger=trigger,
                )
        adopt_target = self._adopt_policy.position_for_scene(scene)
        for entity_id in self.entry.options.get(CONF_MEMBER_COVERS, []):
            await self._cmd_svc.apply_position(
                entity_id,
                adopt_target,
                trigger,
                context=self._adopt_context(),
            )
        self.active_scene = scene
        await self.async_refresh()

    async def async_set_automation(self, enabled: bool) -> None:
        """Bulk-enable/disable sun-tracking automation on every ACP member."""
        for _entry, coordinator in self.resolved_members():
            coordinator.automatic_control = enabled
            await coordinator.async_refresh()

    async def async_clear_overrides(self) -> None:
        """Clear manual overrides on every ACP member via its shared reset path."""
        for _entry, coordinator in self.resolved_members():
            await coordinator.async_reset_manual_overrides(
                trigger="group_clear_overrides"
            )

    def _adopt_context(self) -> PositionContext:
        """Command context for adopt-mode (generic cover) dispatches.

        Scene activation is an explicit user action: ``force=True`` bypasses
        the delta/time/manual gates (the group has no such config in Phase 1)
        while the unavailable-cover skip and same-position no-op suppression
        still apply inside ``apply_position``.
        """
        return PositionContext(
            auto_control=True,
            manual_override=False,
            sun_just_appeared=False,
            min_change=DEFAULT_DELTA_POSITION,
            time_threshold=DEFAULT_DELTA_TIME,
            special_positions=[],
            force=True,
            policy=self._adopt_policy,
        )

    # ---- Aggregates --------------------------------------------------------- #

    async def _async_setup(self) -> None:
        """Subscribe to member cover state changes to keep aggregates live."""
        entities = self.member_cover_entities()
        if entities:
            self._unsub_state = async_track_state_change_event(
                self.hass, entities, self._handle_member_state_change
            )

    @callback
    def _handle_member_state_change(self, _event: Event) -> None:
        """Recompute aggregates when any member cover moves."""
        self.hass.async_create_task(self.async_request_refresh())

    async def async_shutdown(self) -> None:
        """Tear down listeners and the adopt-mode command service."""
        if self._unsub_state is not None:
            self._unsub_state()
            self._unsub_state = None
        self._cmd_svc.stop()
        await super().async_shutdown()

    async def _async_update_data(self) -> GroupAggregates:
        """Recompute the group position/state aggregates from member covers."""
        member_positions: dict[str, int | None] = {}
        for entry_id in self.entry.options.get(CONF_MEMBER_ENTRIES, []):
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if entry is None:
                continue
            policy = get_policy(entry.data[CONF_SENSOR_TYPE])
            for entity_id in entry.options.get(CONF_ENTITIES, []):
                member_positions[entity_id] = policy.read_axis_value(
                    self.hass, entity_id, caps=None
                )
        for entity_id in self.entry.options.get(CONF_MEMBER_COVERS, []):
            member_positions[entity_id] = self._adopt_policy.read_axis_value(
                self.hass, entity_id, caps=None
            )

        readable = [pos for pos in member_positions.values() if pos is not None]
        if not readable:
            return GroupAggregates(
                position=None,
                state=GroupState.UNKNOWN,
                member_positions=member_positions,
            )
        if all(pos == POSITION_OPEN for pos in readable):
            state = GroupState.OPEN
        elif all(pos == POSITION_CLOSED for pos in readable):
            state = GroupState.CLOSED
        else:
            state = GroupState.MIXED
        return GroupAggregates(
            position=int(round(sum(readable) / len(readable))),
            state=state,
            member_positions=member_positions,
        )
