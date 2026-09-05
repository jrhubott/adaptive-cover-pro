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
from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_STOP_COVER, Platform
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
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
from .cover_types.base import axis_inverted
from .helpers import (
    climate_mode_configured,
    climate_mode_from_diagnostics,
    usable_coordinator,
)
from .managers.cover_command import CoverCommandService
from .managers.cover_command.state_store import PositionContext
from .managers.grace_period import GracePeriodManager
from .pipeline.types import GroupIntent
from .position_utils import flip_if
from .state.area_resolver import area_device_ids, device_area_id

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


@dataclass(frozen=True, slots=True)
class _MemberSubscription:
    """One live subscription to a member coordinator's update notifications.

    The coordinator is kept alongside its unsub because *identity* is what
    ``_sync_member_subscriptions`` diffs on — a reloaded member keeps its entry
    id but gets a new coordinator object.
    """

    coordinator: object
    unsub: CALLBACK_TYPE


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
        self._unsub_entry_state: CALLBACK_TYPE | None = None
        self._member_subs: dict[str, _MemberSubscription] = {}
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
        """Every ``cover.`` entity in the area (own or device-inherited).

        Two passes over the registries' own indexes, replacing a scan of every
        entity in the install — deprecated, and removed in HA 2027.9.0
        (issue #1339). Three things about it are load-bearing:

        - ``include_disabled_entities=True`` on the per-device pass is
          deliberate and *not* the default. The two accessors are asymmetric:
          ``async_entries_for_area`` applies no ``disabled_by`` filter, while
          ``async_entries_for_device`` drops disabled entries unless told
          otherwise. Omitting the flag would silently strip disabled covers
          from area rosters.
        - ``reg_entry.area_id is None`` on the device pass preserves
          :meth:`_entity_area_id`'s entity-over-device precedence: a cover
          whose own area names a *different* area must not be collected via
          its device.
        - The passes are disjoint by construction — pass 1 requires a non-None
          own area, pass 2 requires ``None`` — and an entity has exactly one
          device, so no dedup is needed.
        """
        ent_reg = er.async_get(self.hass)
        entity_ids = [
            reg_entry.entity_id
            for reg_entry in er.async_entries_for_area(ent_reg, area_id)
            if reg_entry.domain == Platform.COVER
        ]
        for device_id in area_device_ids(self.hass, area_id):
            entity_ids.extend(
                reg_entry.entity_id
                for reg_entry in er.async_entries_for_device(
                    ent_reg, device_id, include_disabled_entities=True
                )
                if reg_entry.domain == Platform.COVER and reg_entry.area_id is None
            )
        return entity_ids

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
        """ACP members whose entry exists and whose coordinator is usable.

        A removed member's id may linger in the roster until the next options
        edit, and a member mid-reload exposes a coordinator whose entities are
        not up yet. Both are silently skipped — absence is non-membership for
        this cycle. ``helpers.usable_coordinator`` owns the predicate (and the
        reason a half-set-up entry must not be written to); the services path
        resolves through the same helper.
        """
        members: list[tuple[ConfigEntry, object]] = []
        for entry_id in self.member_entry_ids():
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if entry is None:
                continue
            coordinator = usable_coordinator(entry)
            if coordinator is None:
                _LOGGER.debug(
                    "Group %s: member %s has no usable coordinator; skipping",
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

    def _scene_intent(self, scene: GroupScene) -> GroupIntent:
        """Build this group's SCENE intent — the one construction site."""
        return GroupIntent(
            kind=GroupIntentKind.SCENE,
            scene=scene,
            priority=GROUP_SCENE_PRIORITY,
            group_id=self.entry.entry_id,
        )

    async def _push_member_intent(
        self, coordinator: object, intent: GroupIntent | None
    ) -> None:
        """Set (or clear) this group's intent on one member and re-evaluate it now.

        ``async_refresh``, deliberately never ``async_request_refresh``: the
        latter goes through HA's 10-second request debouncer, so a second group
        action inside that window leaves the member's pipeline still holding the
        PREVIOUS winner at the instant this group publishes to its own
        listeners. The who-won sensor snapshots that stale winner and keeps it
        until something unrelated happens to move a cover (issue #1082). The
        debounce also defers the member's re-evaluation itself, so the group
        action lands up to ten seconds late.

        Teardown uses this same path. Routing it through the request debouncer
        instead would not actually defer anything — that debouncer is
        ``immediate=True``, and every direct refresh cancels its cooldown timer,
        so it runs inline anyway — it would only make the intent clear's timing
        depend on how recently the last group action happened.
        """
        coordinator.set_group_intent(self.entry.entry_id, intent)
        await coordinator.async_refresh()

    async def _push_intent_to_members(self, intent: GroupIntent | None) -> None:
        """Push one intent — or the clear — to every resolvable ACP member."""
        for _entry, coordinator in self.resolved_members():
            await self._push_member_intent(coordinator, intent)

    async def async_activate_scene(self, scene: GroupScene) -> None:
        """Fan a scene out as a pipeline intent, resolved per member (Phase 2).

        ACP members get a SCENE intent + refresh — their pipeline arbitrates
        (weather and member safety still win). Generic members have no
        pipeline and are commanded directly with the adopt-policy target.
        Per-member opt-out and the stagger gap apply to both kinds.
        """
        intent = self._scene_intent(scene)
        adopt_target = self._adopt_policy.position_for_scene(scene)
        trigger = f"group_scene_{scene}"

        async def _member(_entry, coordinator) -> None:
            await self._push_member_intent(coordinator, intent)

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
            # Each member's OWN policy decides its rail order (issue #1115) —
            # a Model C day/night member inside a group sequences its rails
            # exactly as it does standalone, and names the number and frame this
            # loop fans out so the ordering view can tell a raise from a lower
            # (issue #1118). ``user_dispatch_position`` is the MEMBER's own
            # shared derivation — its floors, its frame — which is the same one
            # ``async_apply_user_position`` runs below; the group's would answer
            # for the wrong instance.
            ordered = coordinator._policy.order_for_dispatch(  # noqa: SLF001
                entry.options.get(CONF_ENTITIES, []),
                position=coordinator.user_dispatch_position(position),
                inverted=coordinator.position_axis_inverted,
            )
            for entity_id in ordered:
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
            # Same per-member ordered view as the position slider (issue #1115),
            # naming the tilt value for the direction check (issue #1118): on a
            # single-carriage type the tilt axis falls back to the position path
            # (#684), so that IS the number reaching ``_entity_target`` — via
            # the same ``async_apply_user_position`` tail, hence the same
            # ``user_dispatch_position`` derivation, floor clamp included. A
            # type with a real tilt axis ignores the pair entirely.
            ordered = coordinator._policy.order_for_dispatch(  # noqa: SLF001
                entry.options.get(CONF_ENTITIES, []),
                position=coordinator.user_dispatch_position(tilt),
                inverted=coordinator.position_axis_inverted,
            )
            for entity_id in ordered:
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
        await self._push_intent_to_members(None)
        self.active_scene = None
        await self.async_refresh()

    async def async_set_lock(self, locked: bool) -> None:
        """Push or release the group lock (LOCK intent at safety priority).

        Engaging ignores per-scene opt-out — it is a safety claim on every
        member — and needs no stagger, because ``GroupLockHandler`` holds
        position and emits no command. Releasing does move covers, so it
        staggers like any other fan-out.

        On release an active scene is re-pushed to each ACP member that has
        not opted out of it, so unlocking returns the room to the scene rather
        than to unmanaged state. Adopted (generic) covers are left where they
        are: the lock is a pipeline intent and never claimed them.
        """
        self.group_locked = locked
        if locked:
            await self._push_intent_to_members(
                GroupIntent(
                    kind=GroupIntentKind.LOCK,
                    scene=None,
                    priority=CUSTOM_POSITION_SAFETY_PRIORITY,
                    group_id=self.entry.entry_id,
                )
            )
        else:
            # Hand each member the intent it should END on, in ONE push. Clearing
            # first and re-pushing the scene second makes every member evaluate
            # once un-scened — solar or default wins and commands the cover — a
            # visible jog that the request-refresh debouncer used to swallow
            # (issue #1082). Opt-out still applies: a member that opted out of
            # the active scene ends on no intent rather than on the scene.
            scene = self.active_scene
            keep = self._scene_intent(scene) if scene is not None else None
            released = 0
            for entry, coordinator in self.resolved_members():
                opted_out = scene is not None and self._scene_opted_out(
                    entry.entry_id, scene
                )
                await self._stagger_gap(released)
                released += 1
                await self._push_member_intent(coordinator, None if opted_out else keep)
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
        """Bulk-enable/disable climate mode on every member configured for it.

        Members without climate mode in their config are skipped: they expose
        no Climate Mode switch, so nothing would persist the change, and their
        coordinator re-seeds ``switch_mode`` from the option at every startup.
        Commanding them would take real effect until the next restart and then
        silently evaporate — while this group's own switch, which restores,
        went on claiming it (issue #1063).

        The predicate is "configured for climate mode", not "has a live Climate
        Mode entity": a member whose switch the user disabled in the entity
        registry is still commanded here and still loses the value at the next
        restart, because a disabled entity never restores. That is an explicit
        user opt-out rather than a case worth more machinery.
        """
        for entry, coordinator in self.resolved_members():
            if not climate_mode_configured(entry.options):
                _LOGGER.debug(
                    "Group %s: member %s is not configured for climate mode; skipping",
                    self.entry.entry_id,
                    entry.entry_id,
                )
                continue
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
        into or out of the area re-resolve the rosters. The config-entry
        subscription keeps the *member coordinator* subscriptions live — see
        :meth:`_handle_config_entry_change`.
        """
        entities = self.member_cover_entities()
        if entities:
            self._unsub_state = async_track_state_change_event(
                self.hass, entities, self._handle_member_state_change
            )
        self._sync_member_subscriptions()
        self._unsub_entry_state = async_dispatcher_connect(
            self.hass, SIGNAL_CONFIG_ENTRY_CHANGED, self._handle_config_entry_change
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
        """Recompute aggregates when any member cover moves.

        Debounced on purpose, unlike the intent pushes: a scene fan-out emits
        one state event per member cover, and collapsing those into a single
        aggregate recompute is exactly what the request debouncer is for.
        """
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _sync_member_subscriptions(self) -> bool:
        """Subscribe to exactly the member coordinators that exist right now.

        Idempotent, so it can run on every config-entry state change. Returns
        whether the subscription set changed. The identity check — not the entry
        id — is what catches a member *reload*: the id is unchanged but
        ``runtime_data`` holds a brand-new coordinator, and a listener left on
        the old one is dead. ``resolved_members`` stays the single "is this
        member real yet" predicate (``helpers.usable_coordinator``, #1063).
        """
        live = {entry.entry_id: coord for entry, coord in self.resolved_members()}
        changed = False
        for entry_id, sub in list(self._member_subs.items()):
            if live.get(entry_id) is not sub.coordinator:
                sub.unsub()
                del self._member_subs[entry_id]
                changed = True
        for entry_id, coordinator in live.items():
            if entry_id in self._member_subs:
                continue
            self._member_subs[entry_id] = _MemberSubscription(
                coordinator=coordinator,
                unsub=coordinator.async_add_listener(self._handle_member_update),
            )
            changed = True
        return changed

    @callback
    def _handle_config_entry_change(self, _change: object, entry: ConfigEntry) -> None:
        """Re-sync member subscriptions when any ACP entry changes state.

        ``ConfigEntry._async_set_state`` dispatches on every transition, so this
        one subscription covers a member finishing setup after the group did, a
        member reload swapping its coordinator out, and an unload or removal.
        Roster *membership* changes need no hook here: an options edit and an
        area-registry change both reload this entry, which re-runs setup.
        """
        if entry.domain != DOMAIN:
            return
        if not self._sync_member_subscriptions():
            return
        # The roster itself moved, so the aggregates are stale too: a departed
        # member's covers linger in ``member_positions`` and a new one is
        # missing from it. Repaint the live-reading sensors now and let the
        # (debounced) refresh catch the snapshot up.
        self.hass.async_create_task(self.async_request_refresh())
        self.async_update_listeners()

    @callback
    def _handle_member_update(self) -> None:
        """Repaint the group's entities when a member coordinator updates.

        ``async_update_listeners``, not a refresh: ``member_winners`` and
        ``member_climate_modes`` read live off the member coordinators, and
        nothing in :class:`GroupAggregates` can have moved — member positions
        come from cover states, which ``_handle_member_state_change`` already
        watches. There is nothing to re-fetch, only to re-render, and routing
        this through the group's own request debouncer would reintroduce the
        staleness on the other side (issue #1082).
        """
        self.async_update_listeners()

    async def async_shutdown(self) -> None:
        """Tear down listeners, the command service, and any live intents.

        Clearing this group's intent from every member matters on reload and
        delete: a stale intent would keep claiming the member's pipeline for
        a group that no longer exists (#712/#714 lifecycle lesson).

        EVERY listener goes first. The intent clear makes members re-evaluate
        and command their covers, and both the member subscriptions and the
        cover-state subscription would otherwise feed that back into a
        coordinator that is mid-teardown.
        """
        if self._unsub_entry_state is not None:
            self._unsub_entry_state()
            self._unsub_entry_state = None
        for sub in self._member_subs.values():
            sub.unsub()
        self._member_subs = {}
        if self._unsub_state is not None:
            self._unsub_state()
            self._unsub_state = None
        for unsub in self._unsub_registry:
            unsub()
        self._unsub_registry = []
        await self._push_intent_to_members(None)
        self._cmd_svc.stop()
        await super().async_shutdown()

    async def _async_update_data(self) -> GroupAggregates:
        """Recompute the group position/state aggregates from member covers.

        Every reading is normalised to the LOGICAL frame before it is averaged
        or compared (#1027). A group cover is an ordinary HA cover: the number
        it publishes and the number its slider accepts are logical, and
        ``async_set_position`` hands that logical value to each ACP member,
        which re-frames it for its own hardware. Members can be configured
        differently — one on ``inverse_state``, one not — so the raw aggregate
        genuinely mixes frames and cannot be un-inverted after the fact.
        Normalising per member is the only thing that composes.
        """
        member_positions: dict[str, int | None] = {}
        for entry_id in self.member_entry_ids():
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if entry is None:
                continue
            policy = get_policy(entry.data[CONF_SENSOR_TYPE])
            # Same predicate the member's own coordinator uses, read from the
            # member's options rather than its runtime_data so a mid-reload
            # member still contributes its reading in the right frame.
            inverted = axis_inverted(policy.axes[0], entry.options)
            for entity_id in entry.options.get(CONF_ENTITIES, []):
                raw = policy.read_axis_value(self.hass, entity_id, caps=None)
                member_positions[entity_id] = (
                    flip_if(raw, inverted=inverted) if raw is not None else raw
                )
        # Generic (non-ACP) covers are adopted as-is — ACP holds no inversion
        # config for them, so their reading is already the logical one.
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
