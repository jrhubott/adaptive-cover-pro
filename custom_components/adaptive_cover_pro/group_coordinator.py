"""Coordinator for the virtual Cover Group entry type (issue #790).

Orchestrates a roster of member covers:

* **ACP members** (config entries): scenes and the group lock are pushed as
  a :class:`~.pipeline.types.GroupIntent` into each member coordinator
  (``set_group_intent`` + refresh, Phase 2) — the member's pipeline
  arbitrates, so weather safety still outranks a scene and a member's own
  safety slot outranks the group lock. Bulk operations
  (``async_reset_manual_overrides``, the ``automatic_control`` toggle) call
  the member's own entry points.
* **Generic members** (plain ``cover.*`` entity_ids, "adopt mode") have no
  pipeline: they are commanded directly through a group-owned
  ``CoverCommandService`` so capability fallback (open/close-only covers),
  unavailable-cover skips, and no-op suppression come for free.

The group acts only on explicit user actions (scene buttons/select, bulk
switches); it never moves covers autonomously and its intents are not
persisted, so there is no boot-time fan-out path. Scene targets resolve per
member via the member policy's ``position_for_scene`` — a scene is an
intent, not a shared absolute position.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging

from homeassistant.components.cover import (
    ATTR_TILT_POSITION,
    DOMAIN as COVER_DOMAIN,
    SERVICE_SET_COVER_TILT_POSITION,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_STOP_COVER, Platform
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_ENTITIES,
    CONF_GROUP_AREA,
    CONF_GROUP_MEMBER_OPT_OUT,
    CONF_GROUP_STAGGER_DELAY,
    CONF_MEMBER_COVERS,
    CONF_MEMBER_ENTRIES,
    CONF_SENSOR_TYPE,
    CUSTOM_POSITION_SAFETY_PRIORITY,
    DEFAULT_DELTA_POSITION,
    DEFAULT_DELTA_TIME,
    DEFAULT_GROUP_STAGGER_DELAY,
    DOMAIN,
    GROUP_SCENE_PRIORITY,
    OPT_OUT_ALL_SCENES,
    POSITION_CLOSED,
    POSITION_OPEN,
    TRIGGER_GROUP_COVER,
    TRIGGER_GROUP_COVER_TILT,
    CoverType,
    GroupIntentKind,
    GroupScene,
    GroupState,
)
from .cover_types import get_policy
from .helpers import climate_mode_from_diagnostics
from .managers.cover_command import CoverCommandService
from .managers.cover_command.state_store import PositionContext
from .managers.grace_period import GracePeriodManager
from .pipeline.types import GroupIntent
from .state.area_resolver import device_area_id

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
        self.group_locked: bool = False
        self._unsub_state: CALLBACK_TYPE | None = None
        self._unsub_registry: list[CALLBACK_TYPE] = []
        self._roster_snapshot: tuple[tuple[str, ...], tuple[str, ...]] | None = None
        self._adopt_policy = get_policy(_ADOPT_COVER_TYPE)
        self._grace_mgr = GracePeriodManager(_LOGGER)
        self._cmd_svc = CoverCommandService(
            hass,
            _LOGGER,
            _ADOPT_COVER_TYPE,
            self._grace_mgr,
        )

    # ---- Roster resolution ------------------------------------------------ #

    def _entity_area_id(self, entity_id: str) -> str | None:
        """Return the entity's area — its own, or inherited from its device.

        The device→area hop delegates to the shared
        :func:`state.area_resolver.device_area_id` helper so the registry
        lookup lives in exactly one place (also used by the area-based temp
        sensor resolver, issue #786).
        """
        reg_entry = er.async_get(self.hass).async_get(entity_id)
        if reg_entry is None:
            return None
        if reg_entry.area_id:
            return reg_entry.area_id
        return device_area_id(self.hass, reg_entry.device_id)

    def _cover_entities_in_area(self, area_id: str) -> list[str]:
        """Every ``cover.`` entity in the area (own or device-inherited)."""
        ent_reg = er.async_get(self.hass)
        return [
            reg_entry.entity_id
            for reg_entry in ent_reg.entities.values()
            if reg_entry.domain == Platform.COVER
            and self._entity_area_id(reg_entry.entity_id) == area_id
        ]

    def member_entry_ids(self) -> list[str]:
        """Effective ACP member entry ids: static roster ∪ area members.

        An ACP entry belongs to the area when any of its controlled covers is
        in it (its own registry area or its device's). Order: static first,
        area additions after, deduped, self-excluded. With no area set this
        is exactly the stored roster — static groups are unchanged.
        """
        ids = list(
            dict.fromkeys(
                entry_id
                for entry_id in self.entry.options.get(CONF_MEMBER_ENTRIES, [])
                if entry_id != self.entry.entry_id
            )
        )
        area_id = self.entry.options.get(CONF_GROUP_AREA)
        if area_id:
            from .profile_link import _cover_entries  # noqa: PLC0415

            for entry in _cover_entries(self.hass):
                if entry.entry_id in ids:
                    continue
                if any(
                    self._entity_area_id(entity_id) == area_id
                    for entity_id in entry.options.get(CONF_ENTITIES, [])
                ):
                    ids.append(entry.entry_id)
        return ids

    def generic_cover_ids(self) -> list[str]:
        """Effective generic cover ids: static roster ∪ free area covers.

        A cover controlled by ANY ACP entry is excluded (orchestration wins
        over adoption), as are ACP's own entities (the proxy covers). Order:
        static first, area additions after, deduped.
        """
        ids = list(dict.fromkeys(self.entry.options.get(CONF_MEMBER_COVERS, [])))
        area_id = self.entry.options.get(CONF_GROUP_AREA)
        if area_id:
            from .profile_link import _cover_entries  # noqa: PLC0415

            owned = {
                entity_id
                for entry in _cover_entries(self.hass)
                for entity_id in entry.options.get(CONF_ENTITIES, [])
            }
            ent_reg = er.async_get(self.hass)
            for entity_id in self._cover_entities_in_area(area_id):
                if entity_id in ids or entity_id in owned:
                    continue
                reg_entry = ent_reg.async_get(entity_id)
                if reg_entry is not None and reg_entry.platform == DOMAIN:
                    continue
                ids.append(entity_id)
        return ids

    def resolved_members(self) -> list[tuple[ConfigEntry, object]]:
        """ACP members whose entry exists and whose coordinator is loaded.

        Every ``runtime_data`` access is null-guarded: during a member reload
        the attribute is briefly unset, and a removed member's id may linger
        in the roster until the next options edit. Both are silently skipped —
        absence is non-membership for this cycle.
        """
        members: list[tuple[ConfigEntry, object]] = []
        for entry_id in self.member_entry_ids():
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
        for entry_id in self.member_entry_ids():
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if entry is None:
                continue
            entities.extend(entry.options.get(CONF_ENTITIES, []))
        entities.extend(self.generic_cover_ids())
        return entities

    # ---- Fan-out operations ------------------------------------------------ #

    def _scene_opted_out(self, member_entry_id: str, scene: GroupScene) -> bool:
        """Whether the member opted out of this scene (or all scenes)."""
        opted = self.entry.options.get(CONF_GROUP_MEMBER_OPT_OUT, {}).get(
            member_entry_id, []
        )
        return OPT_OUT_ALL_SCENES in opted or str(scene) in opted

    async def _stagger_gap(self, commands_sent: int) -> None:
        """Sleep the configured stagger before every command but the first."""
        stagger = float(
            self.entry.options.get(
                CONF_GROUP_STAGGER_DELAY, DEFAULT_GROUP_STAGGER_DELAY
            )
        )
        if commands_sent and stagger > 0:
            await asyncio.sleep(stagger)

    async def _fan_out_commands(
        self,
        member_action,
        generic_action,
        *,
        scene_filter: GroupScene | None = None,
    ) -> None:
        """Run one action per ACP member and per generic cover, staggered.

        The single fan-out loop shared by scene activation and the group
        cover's user commands: roster iteration, per-scene opt-out (when
        ``scene_filter`` is given), and the stagger gap between successive
        commands all live here exactly once.
        """
        commands = 0
        for entry, coordinator in self.resolved_members():
            if scene_filter is not None and self._scene_opted_out(
                entry.entry_id, scene_filter
            ):
                continue
            await self._stagger_gap(commands)
            commands += 1
            await member_action(entry, coordinator)
        for entity_id in self.generic_cover_ids():
            await self._stagger_gap(commands)
            commands += 1
            await generic_action(entity_id)

    async def async_activate_scene(self, scene: GroupScene) -> None:
        """Fan a scene out as a pipeline intent, resolved per member (Phase 2).

        ACP members get a SCENE intent + refresh — their pipeline arbitrates
        (weather and member safety still win). Generic members have no
        pipeline and are commanded directly with the adopt-policy target.
        Per-member opt-out and the stagger gap apply to both kinds.
        """
        intent = GroupIntent(
            kind=GroupIntentKind.SCENE,
            scene=scene,
            priority=GROUP_SCENE_PRIORITY,
            group_id=self.entry.entry_id,
        )
        adopt_target = self._adopt_policy.position_for_scene(scene)
        trigger = f"group_scene_{scene}"

        async def _member(_entry, coordinator) -> None:
            coordinator.set_group_intent(self.entry.entry_id, intent)
            await coordinator.async_request_refresh()

        async def _generic(entity_id: str) -> None:
            await self._cmd_svc.apply_position(
                entity_id, adopt_target, trigger, context=self._adopt_context()
            )

        await self._fan_out_commands(_member, _generic, scene_filter=scene)
        self.active_scene = scene
        await self.async_refresh()

    async def async_set_position(self, position: int) -> None:
        """Fan a user position out to every member (group cover slider).

        A group-cover drag is a user action: ACP members ride their own
        user-position path (manual-override engagement and floor clamps
        apply, exactly like the per-cover proxy); generic covers go through
        the adopt-mode command service. Stagger applies.
        """

        async def _member(entry, coordinator) -> None:
            for entity_id in entry.options.get(CONF_ENTITIES, []):
                await coordinator.async_apply_user_position(
                    entity_id, position, trigger=TRIGGER_GROUP_COVER
                )

        async def _generic(entity_id: str) -> None:
            await self._cmd_svc.apply_position(
                entity_id, position, TRIGGER_GROUP_COVER, context=self._adopt_context()
            )

        await self._fan_out_commands(_member, _generic)
        await self.async_refresh()

    async def async_set_tilt(self, tilt: int) -> None:
        """Fan a user tilt out to every member (group cover tilt slider).

        ACP members ride the dedicated tilt path so dual-axis covers move
        only their slats (#684); generic covers get the plain tilt service.
        """

        async def _member(entry, coordinator) -> None:
            for entity_id in entry.options.get(CONF_ENTITIES, []):
                await coordinator.async_apply_user_tilt(
                    entity_id, tilt, trigger=TRIGGER_GROUP_COVER_TILT
                )

        async def _generic(entity_id: str) -> None:
            await self.hass.services.async_call(
                COVER_DOMAIN,
                SERVICE_SET_COVER_TILT_POSITION,
                {ATTR_ENTITY_ID: entity_id, ATTR_TILT_POSITION: tilt},
                blocking=False,
            )

        await self._fan_out_commands(_member, _generic)
        await self.async_refresh()

    async def async_stop(self) -> None:
        """Stop every member cover immediately — no stagger, no gates.

        Mirrors the proxy cover's stop: a plain ``cover.stop_cover`` per
        member entity, ACP and generic alike.
        """
        for entity_id in self.member_cover_entities():
            await self.hass.services.async_call(
                COVER_DOMAIN,
                SERVICE_STOP_COVER,
                {ATTR_ENTITY_ID: entity_id},
                blocking=False,
            )

    async def async_clear_scene(self) -> None:
        """Release this group's scene claim — members return to their pipeline."""
        for _entry, coordinator in self.resolved_members():
            coordinator.set_group_intent(self.entry.entry_id, None)
            await coordinator.async_request_refresh()
        self.active_scene = None
        await self.async_refresh()

    async def async_set_lock(self, locked: bool) -> None:
        """Push or release the group lock (LOCK intent at safety priority).

        The lock ignores per-scene opt-out — it is a safety claim on every
        member — and applies immediately (no stagger; nothing moves). On
        release, an active scene is re-pushed so unlocking returns the room
        to the scene, not to unmanaged state.
        """
        self.group_locked = locked
        if locked:
            intent = GroupIntent(
                kind=GroupIntentKind.LOCK,
                scene=None,
                priority=CUSTOM_POSITION_SAFETY_PRIORITY,
                group_id=self.entry.entry_id,
            )
            for _entry, coordinator in self.resolved_members():
                coordinator.set_group_intent(self.entry.entry_id, intent)
                await coordinator.async_request_refresh()
        else:
            for _entry, coordinator in self.resolved_members():
                coordinator.set_group_intent(self.entry.entry_id, None)
                await coordinator.async_request_refresh()
            if self.active_scene is not None:
                await self.async_activate_scene(self.active_scene)
        await self.async_refresh()

    def member_winners(self) -> dict[str, str | None]:
        """Who-won: each ACP member cover mapped to its pipeline's winner."""
        winners: dict[str, str | None] = {}
        for entry, coordinator in self.resolved_members():
            winner = getattr(coordinator, "pipeline_winner_name", None)
            for entity_id in entry.options.get(CONF_ENTITIES, []):
                winners[entity_id] = winner
        return winners

    def all_members_tilt(self) -> bool:
        """Whether every member — ACP and generic — has a tilt axis.

        Gates the group cover's tilt features (issue #790 §3): ACP members
        are checked via their policy's declared axes, generic covers via the
        HA ``supported_features`` tilt bit. An empty roster is not tiltable.
        """
        from homeassistant.components.cover import CoverEntityFeature

        from .cover_types.base import AXIS_NAME_TILT

        member_ids = self.member_entry_ids()
        generic = self.generic_cover_ids()
        if not member_ids and not generic:
            return False
        for entry_id in member_ids:
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if entry is None:
                continue
            policy = get_policy(entry.data[CONF_SENSOR_TYPE])
            if not any(axis.name == AXIS_NAME_TILT for axis in policy.axes):
                return False
        for entity_id in generic:
            state = self.hass.states.get(entity_id)
            features = (
                int(state.attributes.get("supported_features", 0)) if state else 0
            )
            if not features & CoverEntityFeature.SET_TILT_POSITION:
                return False
        return True

    def member_climate_modes(self) -> dict[str, str | None]:
        """Climate rollup: each ACP member cover mapped to its climate mode.

        Read-only view over the same diagnostics the member's own Climate
        Status sensor renders — the group shares no climate inputs (that is
        Building Profile's job); it only reports. Generic covers have no
        pipeline and are excluded.
        """
        modes: dict[str, str | None] = {}
        for entry, coordinator in self.resolved_members():
            diagnostics = getattr(
                getattr(coordinator, "data", None), "diagnostics", None
            )
            mode = climate_mode_from_diagnostics(diagnostics)
            for entity_id in entry.options.get(CONF_ENTITIES, []):
                modes[entity_id] = mode
        return modes

    async def async_set_automation(self, enabled: bool) -> None:
        """Bulk-enable/disable sun-tracking automation on every ACP member."""
        for _entry, coordinator in self.resolved_members():
            coordinator.automatic_control = enabled
            await coordinator.async_refresh()

    async def async_set_climate_mode(self, enabled: bool) -> None:
        """Bulk-enable/disable climate mode on every ACP member."""
        for _entry, coordinator in self.resolved_members():
            coordinator.switch_mode = enabled
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
        """Subscribe to member-state and (with an area) registry changes.

        The registry subscriptions keep area membership live: covers moved
        into or out of the area re-resolve the rosters.
        """
        entities = self.member_cover_entities()
        if entities:
            self._unsub_state = async_track_state_change_event(
                self.hass, entities, self._handle_member_state_change
            )
        if self.entry.options.get(CONF_GROUP_AREA):
            self._roster_snapshot = self._current_roster_snapshot()
            self._unsub_registry = [
                self.hass.bus.async_listen(
                    er.EVENT_ENTITY_REGISTRY_UPDATED, self._handle_registry_change
                ),
                self.hass.bus.async_listen(
                    ar.EVENT_AREA_REGISTRY_UPDATED, self._handle_registry_change
                ),
            ]

    def _current_roster_snapshot(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return a comparable snapshot of the effective rosters."""
        return (tuple(self.member_entry_ids()), tuple(self.generic_cover_ids()))

    @callback
    def _handle_registry_change(self, _event: Event) -> None:
        """Reload the group when a registry change alters area membership.

        The changed-guard makes registry chatter free: only an actual roster
        difference triggers the (single) reload, which rebuilds listeners and
        per-member entities consistently.
        """
        current = self._current_roster_snapshot()
        if current == self._roster_snapshot:
            return
        self._roster_snapshot = current
        _LOGGER.debug(
            "Group %s: area membership changed — reloading entry",
            self.entry.entry_id,
        )
        self.hass.async_create_task(
            self.hass.config_entries.async_reload(self.entry.entry_id)
        )

    @callback
    def _handle_member_state_change(self, _event: Event) -> None:
        """Recompute aggregates when any member cover moves."""
        self.hass.async_create_task(self.async_request_refresh())

    async def async_shutdown(self) -> None:
        """Tear down listeners, the command service, and any live intents.

        Clearing this group's intent from every member matters on reload and
        delete: a stale intent would keep claiming the member's pipeline for
        a group that no longer exists (#712/#714 lifecycle lesson).
        """
        for _entry, coordinator in self.resolved_members():
            coordinator.set_group_intent(self.entry.entry_id, None)
            await coordinator.async_request_refresh()
        if self._unsub_state is not None:
            self._unsub_state()
            self._unsub_state = None
        for unsub in self._unsub_registry:
            unsub()
        self._unsub_registry = []
        self._cmd_svc.stop()
        await super().async_shutdown()

    async def _async_update_data(self) -> GroupAggregates:
        """Recompute the group position/state aggregates from member covers."""
        member_positions: dict[str, int | None] = {}
        for entry_id in self.member_entry_ids():
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if entry is None:
                continue
            policy = get_policy(entry.data[CONF_SENSOR_TYPE])
            for entity_id in entry.options.get(CONF_ENTITIES, []):
                member_positions[entity_id] = policy.read_axis_value(
                    self.hass, entity_id, caps=None
                )
        for entity_id in self.generic_cover_ids():
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
