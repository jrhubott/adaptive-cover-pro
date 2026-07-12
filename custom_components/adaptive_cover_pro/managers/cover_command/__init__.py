"""Cover command service for Adaptive Cover Pro."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any

from homeassistant.components.cover.const import DOMAIN as COVER_DOMAIN
from homeassistant.const import STATE_CLOSED, STATE_OPEN, STATE_UNAVAILABLE
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval

from ...const import (
    DEFAULT_ENDPOINT_USE_OPEN_CLOSE,
    DEFAULT_TRANSIT_TIMEOUT_SECONDS,
    MAX_POSITION_RETRIES,
    POSITION_CHECK_INTERVAL_MINUTES,
    POSITION_CLOSED,
    POSITION_OPEN,
    POSITION_TOLERANCE_PERCENT,
)
from ...cover_types.base import (
    CAP_HAS_STOP,
    caps_get,
)
from ...diagnostics.event_buffer import EventBuffer
from ...helpers import (
    check_cover_features,
    get_last_updated,
)
from . import gates
from .diagnostics import DiagnosticsRecorder
from .position_context import PositionContextTracker
from .routing import ServiceCallPlan, build_special_positions, route_service_call
from .state_classifier import StateClassifier
from .state_store import PerEntityState, PositionContext
from .stop import StopTracker

__all__ = [
    "CoverCommandService",
    "PerEntityState",
    "PositionContext",
    "ServiceCallPlan",
    "build_special_positions",
    "route_service_call",
]


class CoverCommandService:
    """Self-contained service for positioning cover entities.

    Owns the full cover positioning lifecycle:
    - Gate checks (auto control, time window, delta, time, manual override)
    - Service call preparation and execution
    - Per-entity state via ``PerEntityState`` (target, waiting, retry_count, ...)
    - Reconciliation timer: every minute, re-sends target if cover missed it
    - Diagnostic tracking (last action, last skipped action)

    Usage:
        1. Call ``start()`` after HA is ready (first refresh).
        2. Call ``apply_position(entity_id, position, reason, context=ctx)``
           whenever the desired position changes.
        3. Call ``stop()`` on shutdown/unload.
        4. Call ``check_target_reached(entity_id, reported_position)`` from
           the coordinator's cover-state-change handler.

    """

    # Default capabilities for covers when entity not ready
    _DEFAULT_CAPABILITIES = {
        "has_set_position": True,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }

    def __init__(
        self,
        hass: HomeAssistant,
        logger,
        cover_type: str,
        grace_mgr,
        open_close_threshold: int = 50,
        check_interval_minutes: int = POSITION_CHECK_INTERVAL_MINUTES,
        position_tolerance: int = POSITION_TOLERANCE_PERCENT,
        max_retries: int = MAX_POSITION_RETRIES,
        transit_timeout_seconds: int = DEFAULT_TRANSIT_TIMEOUT_SECONDS,
        on_tick=None,
        endpoint_use_open_close: bool = DEFAULT_ENDPOINT_USE_OPEN_CLOSE,
        *,
        event_buffer=None,
        debug_log=None,
        on_command_sent=None,
    ) -> None:
        """Initialize the CoverCommandService.

        Args:
            hass: Home Assistant instance
            logger: Logger instance
            cover_type: Cover type string (cover_blind, cover_awning, cover_tilt)
            grace_mgr: GracePeriodManager instance
            open_close_threshold: Threshold (0-100) for open/close-only covers
            endpoint_use_open_close: When True (issue #697), a final target of
                100 fires cover.open_cover and 0 fires cover.close_cover on
                position-capable covers, instead of set_cover_position(100/0).
            check_interval_minutes: How often reconciliation runs (minutes)
            position_tolerance: Allowed deviation between target and actual (%)
            max_retries: Max reconciliation attempts per target before giving up
            transit_timeout_seconds: Seconds without forward progress before the
                wait_for_target backstop fires.  Defaults to DEFAULT_TRANSIT_TIMEOUT_SECONDS
                (45s).  Set higher for slow covers that take longer to complete a traverse.
            on_tick: Optional async callable(now) invoked at the start of each
                reconciliation tick. Use for coordinator-level periodic work
                (e.g. time window transition checks) that must run on the same
                interval without an extra timer.
            event_buffer: Shared diagnostic ring buffer (optional). When provided,
                cover_command_sent and cover_command_skipped events are appended.
            debug_log: Optional ``(category, msg, *args) -> None`` callable used
                by the manual-override classifier so its diagnostic lines respect
                the coordinator's debug-categories gate.  Defaults to plain
                ``logger.debug`` when omitted.
            on_command_sent: Optional ``(entity_id) -> None`` callable invoked
                whenever an outbound position command is dispatched (alongside
                the command grace period start).  The coordinator wires this to
                ``AdaptiveCoverManager.note_command_sent`` so time-based
                manual-override detectors can clock the post-command window.

        """
        # Local import: ``cover_types.venetian.sequencer`` imports
        # ``managers.cover_command.gates`` (a sibling module) for the tilt
        # min-delta check, so a module-level ``from ...cover_types import
        # get_policy`` here can still close a partial-init loop on first
        # load. The policy is only consulted at construction time and
        # afterwards through ``self._policy``, so the local import is cheap.
        from ...cover_types import get_policy

        self._hass = hass
        self._logger = logger
        self._cover_type = cover_type
        # Resolve once at construction time so internal call sites read
        # ``self._policy`` instead of comparing ``cover_type`` strings. The
        # policy carries the axis descriptors that control which HA service
        # this manager calls — see ``_prepare_service_call`` and
        # ``_read_position_with_capabilities``.
        self._policy = get_policy(cover_type)
        self._grace_mgr = grace_mgr
        self._open_close_threshold = open_close_threshold
        self._endpoint_use_open_close = endpoint_use_open_close
        self._check_interval_minutes = check_interval_minutes
        self._position_tolerance = position_tolerance
        self._max_retries = max_retries
        self._wait_for_target_timeout_seconds = transit_timeout_seconds
        self._on_tick = on_tick
        self._on_command_sent = on_command_sent

        # Per-entity positioning state — single source of truth.
        # All previously-parallel dicts/sets (target_call, _sent_at,
        # wait_for_target, _last_progress_at, _retry_counts, _gave_up,
        # _safety_targets, _last_reconcile_time) live as fields on
        # PerEntityState. External callers go through the typed accessors
        # (get_target/set_target/is_waiting_for_target/...) or via state()
        # for white-box / test access.
        self._state: dict[str, PerEntityState] = {}

        # Stop tracker owns the ACP-originated cover.stop_cover deque plus the
        # try_stop_one orchestration. The EVENT_CALL_SERVICE listener in the
        # coordinator uses ``was_acp_stop_context`` to distinguish our own stop
        # commands from user-initiated stops.
        self._stop_tracker = StopTracker(
            hass,
            logger,
            dry_run_fn=lambda: self._dry_run,
            is_in_transit_fn=self._is_cover_in_transit,
        )

        # Position-context tracker mirrors the stop tracker for the
        # set_cover_position / open_cover / close_cover service calls. The
        # coordinator's state-change handler uses ``was_acp_position_context``
        # to fast-path user-initiated state changes into manual override
        # detection (assumed-state and OPEN/CLOSE-only covers can't be detected
        # via position math alone — see #manual-override-assumed-state fix).
        self._position_context_tracker = PositionContextTracker()

        # Entities currently under manual override — reconciliation skips these
        # so it doesn't fight the user by resending the old integration target.
        # Updated by the coordinator after every manual override state change.
        # Safety handlers (force override, weather) overwrite target_call via
        # apply_position(is_safety=True) so they always take effect regardless.
        self._manual_override_entities: set[str] = set()

        # Whether automatic control is currently enabled.  Synced by the
        # coordinator each update cycle (alongside manual_override_entities).
        # Reconciliation skips non-safety targets when this is False so it
        # doesn't fight the user's intention to pause automation.
        self._auto_control_enabled: bool = True

        # Whether the coordinator's operational time window is currently active.
        # Synced by the coordinator each update cycle (alongside auto_control_enabled).
        # Reconciliation skips non-safety targets when this is False so stale
        # daytime targets are not resent overnight.
        self._in_time_window: bool = True

        # Master kill switch — when False, ALL outbound cover commands are blocked,
        # including safety handlers (force override, weather) and reconciliation.
        # Synced by the coordinator each update cycle from the Integration Enabled switch.
        self._enabled: bool = True

        # When True, the reconciliation pass resends on a position mismatch until
        # the cover reaches the target. When False (the default), the cover is
        # commanded once and left where it lands; a settled landing-delta then
        # surfaces as a manual override instead of a retry (issue #591). Synced by
        # the coordinator each update cycle.
        self._enable_position_matching: bool = False

        # Dry-run mode — when True, no outbound cover commands are sent, but the
        # full update cycle (pipeline, diagnostics, sensors) runs normally.
        # Synced by the coordinator each update cycle from the Debug & Diagnostics option.
        self._dry_run: bool = False

        # Diagnostic recorder owns last_cover_action / last_skipped_action
        # snapshots and pushes cover_command_sent / cover_command_skipped
        # events into the shared event buffer.
        self._event_buffer: EventBuffer | None = event_buffer
        self._diag = DiagnosticsRecorder(event_buffer=event_buffer)

        # Manual-override state classifier — the per-event "is this our own
        # transit or a user move" decision (issues #147, #172, #186, #271,
        # #285).  Body was extracted verbatim from the coordinator in Phase F.
        # ``debug_log`` defaults to a plain logger.debug; the coordinator
        # passes its own _debug_log so debug_mode + debug_categories still
        # gate INFO-level emission.
        if debug_log is None:

            def debug_log(_category, msg, *args):
                logger.debug(msg, *args)

        self._state_classifier = StateClassifier(
            self,
            event_buffer=event_buffer,
            debug_log=debug_log,
        )

        # Reconciliation timer handle (async_track_time_interval unsubscribe fn)
        self._reconcile_unsub = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Start the internal reconciliation timer.

        Call once after first refresh. Safe to call multiple times (no-op if
        already running).

        """
        if self._reconcile_unsub is not None:
            return  # Already started

        interval = dt.timedelta(minutes=self._check_interval_minutes)
        self._reconcile_unsub = async_track_time_interval(
            self._hass,
            self.run_reconciliation_pass,
            interval,
        )
        self._logger.debug(
            "CoverCommandService: reconciliation timer started (interval: %s)", interval
        )

    def stop(self) -> None:
        """Stop the internal reconciliation timer.

        Call on integration unload / coordinator shutdown.

        """
        if self._reconcile_unsub is not None:
            self._reconcile_unsub()
            self._reconcile_unsub = None
            self._logger.debug("CoverCommandService: reconciliation timer stopped")

    # ------------------------------------------------------------------ #
    # Per-entity state — typed accessors over PerEntityState.
    # The single backing store is ``self._state: dict[str, PerEntityState]``.
    # Every read or write — internal, external, or test — goes through the
    # methods below. There is no dict-shaped facade and no parallel state.
    # White-box tests that need to set seldom-touched fields use ``state()``
    # to obtain the live record and assign fields directly.
    # ------------------------------------------------------------------ #

    def state(self, entity_id: str) -> PerEntityState:
        """Return the live per-entity record, creating one if it does not exist.

        Mutations to the returned record are persisted in the service's
        backing dict. Use this for white-box / test access; production code
        should prefer the typed methods (``get_target``, ``set_waiting``,
        etc.) when available.
        """
        s = self._state.get(entity_id)
        if s is None:
            s = PerEntityState()
            self._state[entity_id] = s
        return s

    def _get(self, entity_id: str) -> PerEntityState:
        """Return the existing record, or a fresh empty one (does NOT insert).

        For read-only callers that should not pollute ``_state`` with empty
        records. The returned object is a transient when missing — mutations
        won't persist.
        """
        return self._state.get(entity_id) or PerEntityState()

    def has_target(self, entity_id: str) -> bool:
        """Return True if a target is currently recorded for ``entity_id``."""
        s = self._state.get(entity_id)
        return s is not None and s.target is not None

    def get_target(self, entity_id: str) -> int | None:
        """Return the most recently commanded target position, or None if unset."""
        s = self._state.get(entity_id)
        return None if s is None else s.target

    def set_target(self, entity_id: str, position: int | None) -> None:
        """Set the commanded target position. ``None`` clears the target."""
        self.state(entity_id).target = position

    def iter_targets(self) -> Iterator[tuple[str, int]]:
        """Yield (entity_id, target) for every entity with a recorded target."""
        for eid, s in list(self._state.items()):
            if s.target is not None:
                yield eid, s.target

    def is_waiting_for_target(self, entity_id: str) -> bool:
        """Return True if the cover is currently expected to be moving toward target."""
        s = self._state.get(entity_id)
        return bool(s and s.waiting)

    def set_waiting(self, entity_id: str, value: bool) -> None:
        """Mark an entity as waiting (or no-longer-waiting) for its target.

        Clearing ``waiting`` also drops the synthetic transit direction so the
        opening/closing indicator disappears the moment the transit window
        closes (the ``transit_states()`` surface is gated on ``waiting``).
        """
        s = self.state(entity_id)
        if value:
            s.waiting = True
        else:
            self._clear_waiting(s)

    def _clear_waiting(self, s: PerEntityState) -> None:
        """Clear the waiting flag and its synthetic transit direction together.

        Single funnel for every "no longer in transit" site so the
        opening/closing indicator (``transit_direction``) never outlives the
        ``waiting`` window it is gated on.
        """
        s.waiting = False
        s.transit_direction = None

    def waiting_entities(self) -> list[str]:
        """Return all entities currently in ``waiting=True``."""
        return [eid for eid, s in self._state.items() if s.waiting]

    def is_safety_target(self, entity_id: str) -> bool:
        """Return True if this entity's current target was set via a safety override."""
        s = self._state.get(entity_id)
        return bool(s and s.is_safety)

    def clear_safety_targets(self) -> None:
        """Clear the safety flag on every tracked entity."""
        for s in self._state.values():
            s.is_safety = False

    # ------------------------------------------------------------------ #
    # Assumed display position (issue #888) — display-only fallback for
    # open/close-only covers with no native position feedback.
    # ------------------------------------------------------------------ #

    def get_assumed_position(self, entity_id: str) -> int | None:
        """Return the assumed display position for ``entity_id``, or None.

        Display-only (issue #888): the reported-position surfaces
        (``CoverProvider.read_positions``) fall back to this ONLY when the live
        HA read is None. NEVER consulted by the command-dispatch read path
        (``_read_position_with_capabilities`` / ``get_current_position``) — see
        §3b — so the delta / same-position / endpoint gates stay raw.
        """
        s = self._state.get(entity_id)
        return None if s is None else s.assumed_position

    def record_assumed_position(self, entity_id: str, value: int) -> None:
        """Store an assumed display position for ``entity_id``."""
        self.state(entity_id).assumed_position = value

    def clear_assumed_position(self, entity_id: str) -> None:
        """Drop any stored assumed display position for ``entity_id``."""
        s = self._state.get(entity_id)
        if s is not None:
            s.assumed_position = None

    def _record_assumed_if_blind(
        self, entity_id: str, routed_target: int | None, caps: Any | None = None
    ) -> None:
        """Record (or clear) the assumed display position after ACP drives a cover.

        Single shared write for both My paths (``send_my_position`` and the
        command-sent tail of ``apply_position``) and the coordinator's external
        stop→My path. Gated on ``not policy.position_axis_supported(caps)`` so
        the assumed value is confined to open/close-only covers that report no
        native position:

        - open/close-only cover → stash ``routed_target`` as the assumed value
          (the routed My target, or the routed open/close endpoint).
        - position-capable cover → clear any stale assumed value so a real read
          can never be masked (dedupes the clear-on-native-command path).

        Never touches the command-dispatch read path (§3b); recording happens
        only after the command has already dispatched, so this cycle's gates —
        which read raw HA state — are unaffected.
        """
        if caps is None:
            caps = self.get_cover_capabilities(entity_id)
        if self._policy.position_axis_supported(caps):
            self.clear_assumed_position(entity_id)
            return
        if routed_target is not None:
            self.record_assumed_position(entity_id, routed_target)

    # ------------------------------------------------------------------ #
    # Transit direction (opening/closing indicator) — display-only, for
    # no-feedback open/close-only covers that report no position and no
    # opening/closing state. Surfaced only during the transit-timeout window
    # (gated on ``waiting``) so the companion card can show motion.
    # ------------------------------------------------------------------ #

    def get_transit_direction(self, entity_id: str) -> str | None:
        """Return the synthetic travel direction for ``entity_id``, or None.

        ``"opening"`` / ``"closing"`` while ACP believes an open/close-only
        cover is mid-transit; ``None`` otherwise.
        """
        s = self._state.get(entity_id)
        return None if s is None else s.transit_direction

    def clear_transit_direction(self, entity_id: str) -> None:
        """Drop any stored synthetic travel direction for ``entity_id``."""
        s = self._state.get(entity_id)
        if s is not None:
            s.transit_direction = None

    def transit_states(self) -> dict[str, str]:
        """Return ``{entity_id: direction}`` for every cover mid-transit.

        Gated on ``waiting`` so an entry clears exactly when the transit-timeout
        window closes — a direction recorded on a settled cover is not surfaced.
        """
        return {
            eid: s.transit_direction
            for eid, s in self._state.items()
            if s.waiting and s.transit_direction
        }

    def _set_transit_direction_if_blind(
        self,
        entity_id: str,
        routed_target: int | None,
        prior_position: int | None,
        caps: Any | None = None,
    ) -> None:
        """Record (or clear) the synthetic travel direction after ACP drives a cover.

        Confined to open/close-only covers (no native position axis): a
        position-reporting cover animates via % and never gets a synthetic
        direction, so its stale value is cleared. The direction falls out of a
        raw target-vs-prior comparison in the same non-inverted display frame as
        ``cover_positions`` (100=open, 0=closed) — higher target than prior is
        "opening". Inverse state is NOT applied here.
        """
        if caps is None:
            caps = self.get_cover_capabilities(entity_id)
        if self._policy.position_axis_supported(caps):
            self.clear_transit_direction(entity_id)
            return
        if routed_target is None or prior_position is None:
            self.clear_transit_direction(entity_id)
            return
        if routed_target > prior_position:
            self.state(entity_id).transit_direction = "opening"
        elif routed_target < prior_position:
            self.state(entity_id).transit_direction = "closing"
        else:
            self.clear_transit_direction(entity_id)

    def begin_transit(self, entity_id: str, direction: str | None) -> None:
        """Open a transit-timeout window with a pre-computed direction.

        Used by the coordinator's user-stop→My path: the My move never flows
        through ``_prepare_service_call`` (it's a bare ``stop_cover``), so this
        marks the entity ``waiting`` and stamps ``sent_at`` + ``transit_direction``
        so the ~45s reconciliation timer runs and the card shows motion.
        """
        s = self.state(entity_id)
        s.waiting = True
        s.sent_at = dt.datetime.now(dt.UTC)
        s.transit_direction = direction

    # ------------------------------------------------------------------ #
    # Properties
    # ------------------------------------------------------------------ #

    @property
    def is_tilt_cover(self) -> bool:
        """Whether this cover's primary axis is the tilt axis.

        Kept as a thin wrapper over the policy so existing tests / callers that
        introspect this property keep working. New code should reach for the
        cover-type policy (``self._policy.select_default_axis(caps)``) directly
        because the answer to "use the tilt service?" depends on the entity's
        capabilities, not just on the configured cover type.
        """
        from ...cover_types.base import AXIS_NAME_TILT

        return self._policy.axes[0].name == AXIS_NAME_TILT

    @property
    def manual_override_entities(self) -> set[str]:
        """Return the set of entities currently under manual override."""
        return self._manual_override_entities

    @manual_override_entities.setter
    def manual_override_entities(self, entities: set[str]) -> None:
        """Update the set of entities under manual override.

        Called by the coordinator after each update cycle so reconciliation
        knows which entities to skip.  Safety handlers (force override,
        weather) overwrite target_call via apply_position(is_safety=True) so
        they always take effect regardless of this set.
        """
        self._manual_override_entities = set(entities)

    @property
    def auto_control_enabled(self) -> bool:
        """Whether automatic control is currently enabled."""
        return self._auto_control_enabled

    @auto_control_enabled.setter
    def auto_control_enabled(self, value: bool) -> None:
        """Update the automatic control flag.

        Called by the coordinator each update cycle so reconciliation knows
        whether to resend non-safety targets.  When False, only targets that
        were sent via apply_position(is_safety=True) — i.e. safety overrides —
        are eligible for reconciliation resends.
        """
        self._auto_control_enabled = value

    @property
    def in_time_window(self) -> bool:
        """Whether the coordinator's operational time window is currently active."""
        return self._in_time_window

    @in_time_window.setter
    def in_time_window(self, value: bool) -> None:
        """Update the time window flag.

        Called by the coordinator each update cycle so reconciliation knows
        whether to resend non-safety targets.  When False, only safety targets
        (sent via apply_position(is_safety=True)) are eligible for reconciliation.
        """
        self._in_time_window = value

    @property
    def enabled(self) -> bool:
        """Whether the integration is enabled (master kill switch)."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Update the integration enabled flag.

        When False, ALL outbound cover commands are blocked — including safety
        handlers (force override, weather) and reconciliation.  Synced by the
        coordinator each update cycle from the Integration Enabled switch.
        """
        self._enabled = value

    @property
    def enable_position_matching(self) -> bool:
        """Whether the reconciliation pass resends until the cover arrives (#591)."""
        return self._enable_position_matching

    @enable_position_matching.setter
    def enable_position_matching(self, value: bool) -> None:
        """Update the enable-position-matching flag.

        When False (the default), the reconciliation pass never resends on a
        mismatch — the cover is commanded once and left where it lands.  When
        True, the pass resends until the cover reaches the target.  Synced by
        the coordinator each update cycle from the runtime config.
        """
        self._enable_position_matching = value

    @property
    def dry_run(self) -> bool:
        """Whether dry-run mode is active (no cover commands sent)."""
        return self._dry_run

    @dry_run.setter
    def dry_run(self, value: bool) -> None:
        """Update the dry-run flag.

        When True, the full update cycle runs normally (pipeline, diagnostics,
        sensors) but no outbound cover commands are sent.  Synced by the
        coordinator each update cycle from the Debug & Diagnostics option.
        """
        self._dry_run = value

    @property
    def transit_timeout_seconds(self) -> int:
        """Configured transit-timeout used by manual-override transit backstop."""
        return self._wait_for_target_timeout_seconds

    @property
    def last_cover_action(self) -> dict[str, Any]:
        """Snapshot of the most recent cover command sent (for diagnostics)."""
        return self._diag.last_cover_action

    @property
    def last_skipped_action(self) -> dict[str, Any]:
        """Snapshot of the most recent skipped cover action (for diagnostics)."""
        return self._diag.last_skipped_action

    def get_entity_state_snapshot(self, entity_id: str) -> dict:
        """Return a diagnostic snapshot of per-entity positioning state."""
        s = self._get(entity_id)
        return {
            "target_call": s.target,
            "wait_for_target": s.waiting,
            "retry_count": s.retry_count,
            "gave_up": s.gave_up,
            "last_command_sent_at": s.sent_at.isoformat() if s.sent_at else None,
            "in_manual_override_set": entity_id in self._manual_override_entities,
            "safety_target": s.is_safety,
            "last_reconcile_time": (
                s.last_reconcile_at.isoformat() if s.last_reconcile_at else None
            ),
        }

    def get_all_entity_state_snapshots(self) -> dict[str, dict]:
        """Return diagnostic snapshots for all tracked entities."""
        return {eid: self.get_entity_state_snapshot(eid) for eid in sorted(self._state)}

    def clear_non_safety_targets(self) -> None:
        """Remove non-safety target_call entries so stale targets cannot be resent.

        Called by the coordinator when the time window transitions from
        active to inactive.  Safety targets (force override, weather) are
        preserved so reconciliation can still drive covers to their safe
        position.
        """
        stale = [
            eid
            for eid, s in self._state.items()
            if s.target is not None and not s.is_safety
        ]
        for eid in stale:
            s = self._state[eid]
            s.target = None
            self._clear_waiting(s)
            s.retry_count = 0
            s.gave_up = False
        if stale:
            self._logger.debug(
                "Cleared %d stale non-safety target(s) on window close: %s",
                len(stale),
                stale,
            )

    def discard_target(self, entity_id: str) -> None:
        """Remove all tracking state for an entity, including safety targets.

        Called when a manual override starts for an entity so that any
        pre-existing integration target (including safety-tagged end-time
        defaults) cannot be resurrected by reconciliation while — or after
        — the user is controlling the cover.

        Args:
            entity_id: Cover entity ID to clear.

        """
        existing = self._state.pop(entity_id, None)
        if existing is not None and existing.target is not None:
            self._logger.debug(
                "Discarded stale target for %s on manual override start",
                entity_id,
            )

    # ------------------------------------------------------------------ #
    # Progress-aware transit tracking
    # ------------------------------------------------------------------ #

    def record_progress(self, entity_id: str, now: dt.datetime) -> None:
        """Record that the cover made forward progress toward its target at `now`.

        Called by the coordinator whenever a state-change event shows the cover
        moving closer to the commanded target (new_distance < old_distance).
        Resets the transit-timeout clock so slow-but-moving covers are not
        prematurely cleared by the backstop.
        """
        self.state(entity_id).last_progress_at = now

    def _transit_elapsed_without_progress(
        self, entity_id: str, now: dt.datetime
    ) -> float | None:
        """Seconds since the cover last made forward progress (or since sent_at).

        Returns the elapsed time the transit backstop should compare against the
        configured timeout. Uses ``last_progress_at`` as the reference when
        forward progress has been recorded; falls back to ``sent_at`` when no
        progress has been observed yet (covers that don't report intermediate
        positions, or the very first position event).

        Returns None if no sent_at is recorded for this entity (no command sent).
        """
        s = self._get(entity_id)
        reference = s.last_progress_at or s.sent_at
        if reference is None:
            return None
        return (now - reference).total_seconds()

    def transit_elapsed_without_progress(
        self, entity_id: str, now: dt.datetime
    ) -> float | None:
        """Public surface for the transit backstop's elapsed-since-progress reading.

        Delegates to :meth:`_transit_elapsed_without_progress` so existing tests
        that mock the private name keep working until the cover_command split
        replaces them in commit 4.
        """
        return self._transit_elapsed_without_progress(entity_id, now)

    async def apply_user_stop(self, entity_id: str) -> tuple[str, str]:
        """Send an ACP-context-stamped ``cover.stop_cover`` for a user-initiated stop.

        Routes through ``_stop_tracker.call_stop_cover`` so the resulting
        EVENT_CALL_SERVICE is recognised as ACP-originated and ignored by
        the coordinator's service-call listener.
        """
        await self._stop_tracker.call_stop_cover(entity_id)
        return "sent", "stop_cover"

    def was_acp_stop_context(self, context_id: str) -> bool:
        """Whether ``context_id`` belongs to an ACP-originated cover.stop_cover call.

        The coordinator's EVENT_CALL_SERVICE listener uses this predicate to
        skip stop_cover events that ACP itself triggered (so they don't get
        misread as user-initiated manual overrides).
        """
        return self._stop_tracker.was_acp_stop_context(context_id)

    def acp_stop_context_count(self, *, unique: bool = False) -> int:
        """Return the number of recorded ACP-originated stop_cover context ids.

        With ``unique=True`` returns the count of distinct ids, which lets
        callers verify production code minted a fresh context per stop call
        without inspecting the underlying deque.
        """
        return self._stop_tracker.acp_stop_context_count(unique=unique)

    def was_acp_position_context(self, context_id: str) -> bool:
        """Whether ``context_id`` belongs to an ACP-originated position-command call.

        Covers ``cover.set_cover_position`` / ``cover.open_cover`` /
        ``cover.close_cover`` issued by ``apply_position`` or reconciliation.
        The coordinator's state-change handler uses this predicate to skip
        ACP's own state changes when fast-pathing user-initiated events into
        manual-override detection.
        """
        return self._position_context_tracker.was_acp_position_context(context_id)

    def acp_position_context_count(self, *, unique: bool = False) -> int:
        """Return the number of recorded ACP-originated position-command context ids."""
        return self._position_context_tracker.acp_position_context_count(unique=unique)

    # ------------------------------------------------------------------ #
    # Stop helpers — bypass _enabled gate (shutdown / emergency paths)
    # ------------------------------------------------------------------ #

    async def stop_in_flight(self, entities: set[str] | None = None) -> list[str]:
        """Send stop_cover to every ACP-in-flight entity that supports STOP.

        Intentionally bypasses the ``_enabled`` gate — this IS the shutdown path
        and must fire before the gate closes.

        Args:
            entities: Optional subset of entity_ids to consider.  None = all
                      entries in wait_for_target.

        Returns:
            List of entity_ids that were actually stopped.

        """
        stopped: list[str] = []
        candidates = {
            eid
            for eid, s in self._state.items()
            if s.waiting and (entities is None or eid in entities)
        }
        for eid in candidates:
            s = self.state(eid)
            caps = check_cover_features(self._hass, eid)
            sent = await self._stop_tracker.try_stop_one(
                eid, caps, label="stop_in_flight"
            )
            # Whether we sent the stop or only logged "not in motion", the
            # entity is no longer in flight from ACP's perspective — clear
            # the waiting flag so the next reconciliation cycle does not
            # think a fresh command is still travelling.
            self._clear_waiting(s)
            s.sent_at = None
            if sent:
                stopped.append(eid)
        return stopped

    async def stop_all(self, entity_ids: list[str]) -> list[str]:
        """Send stop_cover to every entity in entity_ids that supports STOP.

        Used by emergency_stop — does NOT check wait_for_target (blanket stop).
        Intentionally bypasses the ``_enabled`` gate.

        Args:
            entity_ids: List of cover entity_ids to stop.

        Returns:
            List of entity_ids that were actually stopped.

        """
        stopped: list[str] = []
        for eid in entity_ids:
            caps = check_cover_features(self._hass, eid)
            if await self._stop_tracker.try_stop_one(eid, caps, label="stop_all"):
                stopped.append(eid)
        return stopped

    # ------------------------------------------------------------------ #
    # "My" position (Somfy / favorite preset)
    # ------------------------------------------------------------------ #

    async def send_my_position(self, entity_id: str, target: int) -> bool:
        """Trigger the cover's hardware-stored "My" preset via cover.stop_cover.

        Unlike stop_all/stop_in_flight this DELIBERATELY sends stop_cover to a
        stationary cover — Somfy RTS motors interpret stop-while-stationary as
        "move to My".  The caller has already verified the cover lacks
        set_cover_position and that has_stop is True.

        Records target_call / wait_for_target / _sent_at so reconciliation
        and delta logic treat this exactly like any other positioning command.

        Note: _is_cover_in_motion() is intentionally NOT called here.  That
        gate belongs to the shutdown paths (stop_all / stop_in_flight).
        Sending stop_cover to a stationary cover is the entire point of this
        method — the two paths have opposite requirements.

        Args:
            entity_id: Cover entity_id to trigger.
            target:    The position (0–100) that My represents (user-configured).

        Returns:
            True if the command was sent (or dry-run logged), False if the
            cover lacks has_stop capability.

        """
        caps = check_cover_features(self._hass, entity_id)
        if not caps_get(caps, CAP_HAS_STOP):
            self._logger.debug(
                "send_my_position: skipping %s — cover does not support STOP", entity_id
            )
            return False
        if self._dry_run:
            self._logger.info(
                "[dry_run] would stop_cover %s (My position = %d%%)", entity_id, target
            )
        else:
            await self._stop_tracker.call_stop_cover(entity_id)
        now = dt.datetime.now(dt.UTC)
        # Synthetic travel direction: read the raw prior position BEFORE the
        # target is overwritten so the open/close-only card can show motion
        # toward My during the transit window.
        prior_position = self._get_current_position(entity_id)
        s = self.state(entity_id)
        s.target = target
        s.waiting = True
        s.sent_at = now
        s.last_progress_at = None
        s.retry_count = 0
        s.gave_up = False
        # Issue #888: on covers with no native position axis, stash the My value
        # as the display-only assumed position so the card shows My rather than
        # ``—``. Caps was already fetched above; the helper gates on the policy
        # predicate (no-op for position-capable covers).
        self._record_assumed_if_blind(entity_id, target, caps)
        self._set_transit_direction_if_blind(entity_id, target, prior_position, caps)
        self._logger.debug(
            "send_my_position: stop_cover sent to %s (My = %d%%)", entity_id, target
        )
        return True

    # ------------------------------------------------------------------ #
    # Threshold update (called by coordinator on options change)
    # ------------------------------------------------------------------ #

    def update_threshold(self, threshold: int) -> None:
        """Update the open/close threshold.

        Args:
            threshold: New threshold value (0-100)

        """
        self._open_close_threshold = threshold

    def update_endpoint_use_open_close(self, value: bool) -> None:
        """Update the endpoint open/close substitution flag (issue #697).

        Args:
            value: When True, final targets of 100/0 fire open_cover/close_cover
                on position-capable covers instead of set_cover_position.

        """
        self._endpoint_use_open_close = value

    def update_position_tolerance(self, value: int) -> None:
        """Update the position-match (reconciliation) tolerance.

        Args:
            value: Allowed deviation between target and reported position (%).

        """
        self._position_tolerance = value

    # ------------------------------------------------------------------ #
    # State classification (manual-override detection)
    # ------------------------------------------------------------------ #

    def classify_state_change(
        self,
        event,
        *,
        ignore_intermediate_states: bool,
        target_just_reached: set[str],
        grace_mgr,
    ) -> None:
        """Classify a post-command cover state change.

        Delegates to :class:`StateClassifier`.  Mutates
        ``target_just_reached`` in place when the cover reaches its
        commanded position; clears ``wait_for_target`` (via
        :meth:`set_waiting`) when the cover has settled or stalled long
        enough that manual-override detection should run on the next
        event.  See the classifier's docstring for the full decision
        tree and the issue numbers each branch closes.
        """
        self._state_classifier.classify(
            event,
            ignore_intermediate_states=ignore_intermediate_states,
            target_just_reached=target_just_reached,
            grace_mgr=grace_mgr,
        )

    # ------------------------------------------------------------------ #
    # Capability detection
    # ------------------------------------------------------------------ #

    def get_cover_capabilities(self, entity: str) -> dict[str, bool]:
        """Get cover capabilities with fallback to safe defaults."""
        caps = check_cover_features(self._hass, entity)
        if caps is None:
            self._logger.debug("Cover %s not ready, using safe defaults", entity)
            return self._DEFAULT_CAPABILITIES.copy()
        return caps

    # ------------------------------------------------------------------ #
    # Position reading
    # ------------------------------------------------------------------ #

    def _read_position_with_capabilities(
        self, entity: str, caps: dict[str, bool], state_obj=None
    ) -> int | None:
        """Read position based on cover type and capabilities."""
        return self._policy.read_axis_value(
            self._hass, entity, caps, state_obj=state_obj
        )

    def read_position_with_capabilities(
        self, entity: str, caps: dict[str, bool], state_obj=None
    ) -> int | None:
        """Public wrapper for reading position based on cover capabilities."""
        return self._read_position_with_capabilities(entity, caps, state_obj)

    def _get_current_position(self, entity: str) -> int | None:
        """Get current position of cover (position-capable or open/close-only)."""
        caps = self.get_cover_capabilities(entity)
        return self._read_position_with_capabilities(entity, caps)

    def get_current_position(self, entity: str) -> int | None:
        """Public surface for reading the cover's current position.

        Delegates to :meth:`_get_current_position` so existing tests that mock
        the private name keep working until the cover_command split replaces
        them in commit 4.
        """
        return self._get_current_position(entity)

    def _is_cover_in_transit(self, entity_id: str) -> bool:
        """Return True when HA reports the cover as actively opening or closing.

        Thin wrapper over :func:`managers.cover_command.transit.is_state_in_transit`
        so the cover-command service, the dual-axis sequencer, and the
        state classifier all consult the same string-membership rule (issue
        #33 Phase 5). Callers that need to guard against stale position
        reads during a transit move delegate here rather than inlining the
        state check.
        """
        from .transit import is_state_in_transit

        state_obj = self._hass.states.get(entity_id)
        return is_state_in_transit(state_obj.state if state_obj is not None else None)

    # ------------------------------------------------------------------ #
    # Gate checks (used internally by apply_position)
    # ------------------------------------------------------------------ #

    def _check_position_delta(
        self,
        entity: str,
        target: int,
        min_change: int,
        special_positions: list[int],
        sun_just_appeared: bool = False,
    ) -> bool:
        """Return True if a command should be sent based on position delta."""
        return gates.check_position_delta(
            entity,
            target,
            min_change,
            special_positions,
            position=self._get_current_position(entity),
            logger=self._logger,
            sun_just_appeared=sun_just_appeared,
        )

    def _check_time_delta(self, entity: str, time_threshold: int) -> bool:
        """Return True if enough time has passed since last command."""
        return gates.check_time_delta(
            entity,
            time_threshold,
            last_updated=get_last_updated(entity, self._hass),
            logger=self._logger,
        )

    def _same_position_via_target_fallback(
        self,
        entity_id: str,
        position: int,
        context: PositionContext,
    ) -> bool:
        """Same-position gate fallback for an unresolved current position (issue #779).

        Somfy RTS (and any open/close-only cover without genuine position
        feedback) reports HA state ``unknown``/``unavailable`` forever, so
        ``_get_current_position`` always returns ``None`` and the
        same-position gate in ``apply_position`` never sees a genuine
        reading to compare against — the exact same command gets resent on
        every update cycle even though the cover was already commanded to
        (and mechanically at) that state.

        Falls back to the last *commanded* target (``get_target``) compared
        against this cycle's *routed decision* rather than the raw
        ``position``. Delegates to :func:`route_service_call` — the single
        source of truth for routing — instead of re-deriving the threshold
        math, so every axis (position-capable, My-preset ``stop_cover``, and
        open/close endpoint) is compared using the exact same rule that
        ``_prepare_service_call`` uses to build the outbound command
        (issue #779 follow-up regression from PR #781, which hand-rolled a
        partial copy of this math and ignored ``use_my_position``).

        Only consulted when ``_current is None``; the delta/time gates and
        reconciliation intentionally keep reading the real (unknown) current
        position and are untouched by this fallback.
        """
        last_target = self.get_target(entity_id)
        if last_target is None:
            return False

        caps = self.get_cover_capabilities(entity_id)
        axis = self._policy.select_default_axis(caps)
        plan = route_service_call(
            entity_id,
            position,
            caps,
            axis=axis,
            use_my_position=context.use_my_position,
            open_close_threshold=self._open_close_threshold,
            endpoint_use_open_close=self._endpoint_use_open_close,
        )
        return last_target == plan.routed_target

    # ------------------------------------------------------------------ #
    # Primary entry point
    # ------------------------------------------------------------------ #

    async def apply_position(
        self,
        entity_id: str,
        position: int,
        reason: str,
        context: PositionContext,
    ) -> tuple[str, str]:
        """Evaluate gates and send a cover position command if appropriate.

        This is the single entry point for all cover positioning.  The
        coordinator calls this method from every code path that wants to
        move a cover (solar update, startup, sunset, reconciliation retry,
        motion/weather timeout callbacks, etc.).

        Args:
            entity_id: Cover entity ID to control
            position: Desired target position (0-100, post-interpolation,
                post-inverse already applied by the time it arrives here)
            reason: Human-readable source ("solar", "startup", "sunset",
                "reconciliation", "force_override", ...)
            context: Current coordinator state used for gate checks

        Returns:
            Tuple of (outcome, detail) where outcome is "sent" or "skipped"
            and detail is the service name or skip reason.

        """
        # ----- gate checks -----
        # Three bypass channels (in order of priority):
        #   - is_safety=True: genuine safety override (force_override, weather)
        #   - bypass_auto_control=True: sanctioned one-shot transition (switch
        #     return-to-default at the moment auto_control toggles off)
        #   - force=True alone: bypasses delta/time/manual_override BUT NOT
        #     auto_control (issue #293)
        _trigger = reason
        _inverse = context.inverse_state

        # Cover-loaded boundary check (issue #342). HA may register the
        # integration before the underlying cover platform finishes loading
        # (e.g. Homematic IP) — issuing set_cover_position before the entity
        # exists triggers a HA warning and, on platforms that queue commands,
        # replays the wrong target once the entity comes online. Bypasses none
        # of the other gates: even is_safety / force=True must wait for the
        # entity to register.
        state_obj = self._hass.states.get(entity_id)
        if state_obj is None or state_obj.state == STATE_UNAVAILABLE:
            return self._skip(
                entity_id,
                "cover_unavailable",
                position,
                trigger=_trigger,
                inverse_state=_inverse,
                current_position=None,
            )

        _current = self._get_current_position(entity_id)

        # Full mechanical endpoint forcing (issue #755, generalized to any
        # position-capable cover by #897). When the owning cover-type policy
        # flagged this update as a full endpoint (single-axis 0/100, or a
        # venetian's paired 0/0 or 100/100), the endpoint_use_open_close feature
        # is on, and the cover is not actually parked at the mechanical stop (HA
        # state not closed/open — NOT a position tolerance), bypass the
        # same-position band and the delta/time gates so route_service_call can
        # emit close_cover/open_cover (#697). This is cover-type-agnostic: the
        # flag carries the decision, the manager never inspects cover type.
        #
        # forced_endpoint is the anti-relay latch (#897/#507): a cover that
        # settles a step short and never reports the mechanical state (state
        # stays "open" at 2%) would otherwise re-force every cycle. We read the
        # latch here (old value) and write it only after a successful send, so
        # the endpoint is forced exactly once per transition. != position lets a
        # flip to the other endpoint re-fire.
        force_endpoint = (
            context.full_endpoint_target
            and self._endpoint_use_open_close
            and not self._is_at_mechanical_stop(state_obj, position)
            and self._get(entity_id).forced_endpoint != position
        )

        # Hard kill switch — blocks ALL commands, including safety overrides and
        # force=True calls.  Must be checked before any bypass branch.
        if not self._enabled:
            return self._skip(
                entity_id,
                "integration_disabled",
                position,
                trigger=_trigger,
                inverse_state=_inverse,
                current_position=_current,
            )

        # auto_control gate — bypassed only by is_safety or bypass_auto_control,
        # NOT by plain force=True (issue #293).
        if (
            not context.is_safety
            and not context.bypass_auto_control
            and not context.auto_control
        ):
            return self._skip(
                entity_id,
                "auto_control_off",
                position,
                trigger=_trigger,
                inverse_state=_inverse,
                current_position=_current,
            )

        # Same-position band — applies to ALL callers, including force=True and
        # is_safety=True.  Issuing set_cover_position when the cover is already
        # at the target is a true no-op that causes audible relay clicks on many
        # motors (issue #290), so we suppress it here.
        #
        # For non-endpoint targets (normal solar tracking moves) this gate uses
        # EXACT equality only — it is NOT a hysteresis band.  Movement hysteresis
        # (how big a move must be before we re-command) is owned solely by
        # _check_position_delta below, governed by the user's CONF_DELTA_POSITION.
        # Using the reconciliation tolerance for all targets conflated the two
        # concepts and suppressed legitimate small tracking moves (issue #567).
        #
        # For the two hard mechanical endpoints (0 and 100) the delta gate is
        # bypassed entirely (special-target bypass, issue #629/#127), so there is
        # no hysteresis fallback.  A motor that physically settles a few percent
        # short of a full-open/full-close endpoint would therefore be re-commanded
        # every update cycle, causing audible relay clicks (issue #507).  To
        # prevent this we apply _position_tolerance here, but ONLY when the target
        # is 0 or 100 — the literal mechanical stops, not mid-range setpoints.
        # Mid-range specials (default_height, sunset_pos, my_position) keep exact
        # equality so a within-tolerance drift from those still triggers a move.
        #
        # sun_just_appeared is the one exception: the sun transitioning in/out of
        # validity is a sentinel that we must re-confirm the cover position even
        # if it hasn't changed numerically.
        #
        # force_endpoint is the other exception (issue #755, generalized to any
        # position-capable cover by #897): a cover whose desired final state is a
        # full mechanical endpoint (single-axis 0/100, or a venetian's paired
        # 0/0 or 100/100) must NOT be swallowed by this _position_tolerance
        # carve-out — it falls through to routing so close_cover/open_cover
        # fires. Idempotency is owned by the HA-state mechanical-stop check plus
        # the forced_endpoint latch above (covers that never report the
        # mechanical state), not by tolerance, so the gap can't be reintroduced
        # here nor can it relay-click every cycle.
        #
        # _current is None is its own exception (issue #779): Somfy RTS covers
        # (and any open/close-only cover without genuine position feedback) sit
        # at HA state unknown/unavailable forever, so _current never resolves
        # and this gate would never fire — the same command gets resent every
        # cycle. _same_position_via_target_fallback compares the last
        # *commanded* target against this cycle's *routed* decision (via
        # route_service_call, see its docstring) instead of a live reading, so
        # position-capable, My-preset stop_cover, and open/close endpoint
        # covers are all handled by the one routing source of truth. Delta/time
        # gates and reconciliation deliberately keep reading the real (unknown)
        # _current — this fallback is scoped to the same-position gate only.
        if (
            not context.sun_just_appeared
            and not force_endpoint
            and (
                (
                    _current is not None
                    and (
                        _current == position
                        or (
                            position in (0, 100) and self._at_target(_current, position)
                        )
                    )
                )
                or (
                    _current is None
                    and self._same_position_via_target_fallback(
                        entity_id, position, context
                    )
                )
            )
        ):
            if context.policy is not None and context.tilt is not None:
                await context.policy.maybe_update_tilt_only(
                    entity_id,
                    current_position=_current,
                    context=context,
                    reason=_trigger,
                )
            return self._skip(
                entity_id,
                "same_position",
                position,
                trigger=_trigger,
                inverse_state=_inverse,
                current_position=_current,
            )

        if not context.force and not force_endpoint:
            if not self._check_position_delta(
                entity_id,
                position,
                context.min_change,
                context.special_positions,
                sun_just_appeared=context.sun_just_appeared,
            ):
                _delta = abs(_current - position) if _current is not None else None
                return self._skip(
                    entity_id,
                    "delta_too_small",
                    position,
                    trigger=_trigger,
                    inverse_state=_inverse,
                    current_position=_current,
                    extras={
                        "position_delta": _delta,
                        "min_delta_required": context.min_change,
                    },
                )

            if not self._check_time_delta(entity_id, context.time_threshold):
                _elapsed = self._elapsed_minutes(entity_id)
                return self._skip(
                    entity_id,
                    "time_delta_too_small",
                    position,
                    trigger=_trigger,
                    inverse_state=_inverse,
                    current_position=_current,
                    extras={
                        "elapsed_minutes": _elapsed,
                        "time_threshold_minutes": context.time_threshold,
                    },
                )

            if context.manual_override:
                return self._skip(
                    entity_id,
                    "manual_override",
                    position,
                    trigger=_trigger,
                    inverse_state=_inverse,
                    current_position=_current,
                )

        # ----- send command -----
        service, service_data, supports_position = self._prepare_service_call(
            entity_id,
            position,
            context.inverse_state,
            is_safety=context.is_safety,
            use_my_position=context.use_my_position,
        )
        if service is None:
            return self._skip(
                entity_id,
                "no_capable_service",
                position,
                trigger=_trigger,
                inverse_state=_inverse,
                current_position=_current,
            )

        # ----- dry-run gate -----
        if self._dry_run:
            self._logger.info(
                "[dry_run] would send cover.%s %s → %s%%",
                service,
                entity_id,
                position,
            )
            self._track_action(
                entity_id, service, position, supports_position, context.inverse_state
            )
            self._diag.last_cover_action["dry_run"] = True
            return self._skip(
                entity_id,
                "dry_run",
                position,
                trigger=_trigger,
                inverse_state=_inverse,
                current_position=_current,
                extras={"would_send_service": service},
            )

        self._logger.info(
            "[%s] Positioning %s → %s%%",
            reason,
            entity_id,
            position,
        )

        # Cover-type policy hook: dual-axis covers (venetian) pre-send tilt
        # on opening transitions so the actuator's slats are at the target
        # angle before the carriage starts moving (issue #33). Default
        # policies are no-ops.
        if context.policy is not None:
            await context.policy.before_position_command(
                self,
                entity_id,
                service=service,
                position=position,
                context=context,
                reason=reason,
            )

        ctx = Context()
        self._position_context_tracker.record(ctx.id)
        try:
            await self._hass.services.async_call(
                COVER_DOMAIN, service, service_data, context=ctx
            )
        except HomeAssistantError as err:
            self._logger.warning(
                "Service call %s.%s failed for %s: %s",
                COVER_DOMAIN,
                service,
                entity_id,
                err,
            )
            return self._skip(
                entity_id,
                "service_call_failed",
                position,
                trigger=_trigger,
                inverse_state=_inverse,
                current_position=_current,
            )

        self._track_action(
            entity_id, service, position, supports_position, context.inverse_state
        )

        # Anti-relay latch bookkeeping (#897/#507). Arm the latch to the forced
        # endpoint so a cover that never reports the mechanical state (state
        # stays "open" a step short of 0) isn't re-forced next cycle. Clear it on
        # any non-endpoint move so a later endpoint command re-fires exactly once.
        if force_endpoint:
            self.state(entity_id).forced_endpoint = position
        elif position not in (POSITION_CLOSED, POSITION_OPEN):
            self.state(entity_id).forced_endpoint = None

        # Cover-type policy hook: dual-axis covers (venetian) run their
        # settle+tilt sequence here. Default policies are no-ops, so vertical /
        # awning / tilt covers carry zero overhead.
        if context.policy is not None:
            await context.policy.after_position_command(
                self,
                entity_id,
                service=service,
                position=position,
                context=context,
                reason=reason,
            )

        return "sent", service

    # ------------------------------------------------------------------ #
    # Position-tolerance helpers
    # ------------------------------------------------------------------ #

    def _at_target(self, actual: int, target: int) -> bool:
        """Return True if *actual* is within ``_position_tolerance`` of *target*.

        Single source of truth for the "close enough" predicate used by
        check_target_reached, run_reconciliation_pass, get_diagnostics, and
        the same-position gate in apply_position (endpoint-only tolerance).
        """
        return abs(actual - target) <= self._position_tolerance

    def _is_at_mechanical_stop(self, state_obj, position: int) -> bool:
        """Return True if the cover is parked at the full mechanical stop.

        Idempotency check for the issue #755 endpoint forcing: uses the HA
        entity *state* (``closed`` for 0, ``open`` for 100) rather than position
        tolerance, matching hardware that reports closed/open only at the exact
        endpoint. Returns False for any non-endpoint target.
        """
        if position == POSITION_CLOSED:
            return state_obj is not None and state_obj.state == STATE_CLOSED
        if position == POSITION_OPEN:
            return state_obj is not None and state_obj.state == STATE_OPEN
        return False

    # ------------------------------------------------------------------ #
    # Target-reached notification (called by coordinator state-change handler)
    # ------------------------------------------------------------------ #

    def check_target_reached(
        self, entity_id: str, reported_position: int | None
    ) -> bool:
        """Check whether cover has reached its target within tolerance.

        Called from the coordinator's cover-state-change handler whenever
        the cover entity reports a new position.  Uses tolerance instead of
        exact equality so covers that round to 5% increments don't get
        stuck with ``wait_for_target=True`` forever.

        Args:
            entity_id: Cover entity ID
            reported_position: Position reported by the cover entity

        Returns:
            True if target reached (wait_for_target cleared), False otherwise.

        """
        s = self._state.get(entity_id)
        if s is None or s.target is None:
            return False

        if reported_position is None:
            return False

        target = s.target
        if self._at_target(reported_position, target):
            self._clear_waiting(s)
            s.retry_count = 0
            self._logger.debug(
                "Target reached for %s (reported=%s target=%s)",
                entity_id,
                reported_position,
                target,
            )
            return True

        return False

    # ------------------------------------------------------------------ #
    # Reconciliation timer
    # ------------------------------------------------------------------ #

    async def run_reconciliation_pass(self, now: dt.datetime) -> None:
        """Periodic reconciliation: re-send target if cover missed it.

        Runs every ``check_interval_minutes``. Calls the optional ``on_tick``
        callback first (used by coordinator for time window transition checks).

        For each tracked entity:

        1. If ``wait_for_target`` has been True for >30 s → force-clear it
           (timeout fallback for covers that never report final position).
        2. If ``wait_for_target`` is still True → cover is moving, skip.
        3. If entity is in ``_manual_override_entities`` → skip resend so
           reconciliation does not fight the user's intentional move.
           Safety handlers (force override, weather) overwrite ``target_call``
           via ``apply_position(is_safety=True)`` so they are always protected.
        4. If ``_auto_control_enabled`` is False and the entity is not in
           ``_safety_targets`` → skip.  Safety targets (set via
           ``apply_position(is_safety=True)``) are still resent so covers reach
           a safe position regardless of the automatic control toggle.
        5. If ``_in_time_window`` is False and entity is not in ``_safety_targets``
           → skip.  Prevents stale daytime targets from being resent overnight.
        6. Compare actual position to ``target_call`` within tolerance.
        7. If match → reset retry count, done.
        8. If mismatch → resend the same target (up to ``max_retries``).

        Note: reconciliation does *not* go through gate checks — the target
        was already validated when ``apply_position`` was called.

        """
        # Coordinator hook: time window transition checks, etc.
        if self._on_tick is not None:
            await self._on_tick(now)

        # Hard kill switch — skip ALL reconciliation when integration is disabled.
        if not self._enabled:
            return

        for entity_id, target in list(self.iter_targets()):
            s = self.state(entity_id)
            s.last_reconcile_at = now

            # 1. Timeout: clear stuck wait_for_target
            if s.waiting:
                elapsed = self._transit_elapsed_without_progress(entity_id, now)
                if elapsed is not None:
                    if elapsed > self._wait_for_target_timeout_seconds:
                        self._logger.debug(
                            "wait_for_target timeout for %s (elapsed %.0fs > %ds) — clearing",
                            entity_id,
                            elapsed,
                            self._wait_for_target_timeout_seconds,
                        )
                        self._clear_waiting(s)
                    else:
                        # Cover still expected to be moving
                        continue
                else:
                    continue  # No sent_at recorded yet

            # 2. Skip entities under manual override — the user moved the cover
            # intentionally; resending the integration's stale target would fight
            # the user.  Safety handlers (force override, weather) bypass this by
            # calling apply_position(is_safety=True) which overwrites target
            # with the safety position, so they are always protected by reconciliation.
            if entity_id in self._manual_override_entities:
                self._logger.debug(
                    "Reconcile: %s in manual override — skipping resend", entity_id
                )
                continue

            # 3. Skip non-safety targets when automatic control is off.  Safety
            # targets (force override, weather) are still resent because they
            # were placed via apply_position(is_safety=True) and have
            # is_safety=True — covers must reach a safe position regardless of
            # the automatic control toggle.
            if not self._auto_control_enabled and not s.is_safety:
                self._logger.debug(
                    "Reconcile: %s skipped — automatic control off", entity_id
                )
                continue

            # 4. Skip non-safety targets outside the operational time window.
            # Prevents stale daytime targets from being resent overnight.
            # Safety targets (force override, weather, end_time_default) are
            # always resent regardless of the time window.
            if not self._in_time_window and not s.is_safety:
                self._logger.debug(
                    "Reconcile: %s skipped — outside time window", entity_id
                )
                continue

            # 5. Skip entities that are actively moving — HA's reported position
            # can lag the physical position during a transit, so a retry sent
            # now would race the in-flight command and produce a double-move.
            # The cover will emit another state-change event when it stops;
            # that tick runs the full reconciliation path.
            if self._is_cover_in_transit(entity_id):
                cover_state = getattr(
                    self._hass.states.get(entity_id), "state", "unknown"
                )
                self._logger.debug(
                    "Reconcile: %s in transit (state=%s) — skipping resend",
                    entity_id,
                    cover_state,
                )
                if self._event_buffer is not None:
                    self._event_buffer.record(
                        {
                            "ts": dt.datetime.now(dt.UTC).isoformat(),
                            "event": "reconcile_skipped_in_transit",
                            "entity_id": entity_id,
                            "target_position": target,
                            "cover_state": cover_state,
                        }
                    )
                continue

            # 6. Read actual position
            actual = self._get_current_position(entity_id)
            if actual is None:
                self._logger.debug(
                    "Reconcile: cannot read position for %s, skipping", entity_id
                )
                continue

            # 7. Check match
            if self._at_target(actual, target):
                s.retry_count = 0
                self._logger.debug(
                    "Reconcile: %s at target (actual=%s target=%s)",
                    entity_id,
                    actual,
                    target,
                )
                continue

            # 8. Mismatch. Unless position matching is enabled, never resend
            #    (issue #591): the cover is commanded once and left where it
            #    lands; a settled landing-delta surfaces as a manual override
            #    via the position-delta detector instead of a retry. Step 1's
            #    wait_for_target timeout-clear above still runs, so the detector
            #    stays reachable.
            if not self._enable_position_matching:
                self._logger.debug(
                    "Reconcile: %s off target (actual=%s target=%s) — position "
                    "matching disabled, leaving cover where it landed",
                    entity_id,
                    actual,
                    target,
                )
                continue

            # Otherwise retry up to max_retries.
            if s.retry_count >= self._max_retries:
                if not s.gave_up:
                    # Log warning exactly once; subsequent ticks are silent
                    self._logger.warning(
                        "Reconcile: max retries (%d) exceeded for %s "
                        "(actual=%s target=%s) — giving up until next target change",
                        self._max_retries,
                        entity_id,
                        actual,
                        target,
                    )
                    if self._event_buffer is not None:
                        self._event_buffer.record(
                            {
                                "ts": dt.datetime.now(dt.UTC).isoformat(),
                                "event": "reconcile_gave_up",
                                "entity_id": entity_id,
                                "actual_position": actual,
                                "target_position": target,
                                "max_retries": self._max_retries,
                            }
                        )
                    s.gave_up = True
                else:
                    self._logger.debug(
                        "Reconcile: %s still off target (actual=%s target=%s), max retries reached",
                        entity_id,
                        actual,
                        target,
                    )
                continue

            s.retry_count += 1
            self._logger.debug(
                "Reconcile: %s missed target (actual=%s target=%s) — retry %d/%d",
                entity_id,
                actual,
                target,
                s.retry_count,
                self._max_retries,
            )
            await self._execute_command(entity_id, target)

    # ------------------------------------------------------------------ #
    # Diagnostic helpers
    # ------------------------------------------------------------------ #

    def get_diagnostics(self, entity_id: str) -> dict[str, Any]:
        """Return per-entity positioning diagnostics for sensor display.

        Args:
            entity_id: Cover entity ID

        Returns:
            Dict with target, actual, at_target, retry_count,
            last_reconcile_time, wait_for_target.

        """
        actual = self._get_current_position(entity_id)
        s = self._get(entity_id)
        target = s.target
        at_target = (
            target is not None
            and actual is not None
            and self._at_target(actual, target)
        )
        return {
            "target": target,
            "actual": actual,
            "at_target": at_target,
            "retry_count": s.retry_count,
            "last_reconcile_time": (
                s.last_reconcile_at.isoformat() if s.last_reconcile_at else None
            ),
            "wait_for_target": s.waiting,
        }

    def record_preempted_skip(
        self,
        entity_id: str,
        position: int,
        *,
        trigger: str,
        winner_name: str,
    ) -> None:
        """Record a user move preempted by a higher-priority pipeline handler.

        Surfaces a "preempted_by_handler" skip in ``last_skipped_action`` so
        the existing Skipped Action diagnostic sensor labels the reason
        (e.g. "Proxy managed to 30 preempted by weather override"). Used by
        :meth:`Coordinator.async_apply_user_position` when the proxy cover
        or ``set_position`` service is overruled by force_override / weather
        / a custom-position slot with priority > 80.
        """
        current_position = self._get_current_position(entity_id)
        self._diag.record_skipped_action(
            entity_id,
            "preempted_by_handler",
            position,
            trigger=trigger,
            current_position=current_position,
            inverse_state=False,
            extras={"winner": winner_name},
        )
        self._diag.record_skip_event(
            entity_id,
            "preempted_by_handler",
            position,
            trigger=trigger,
            inverse_state=False,
            current_position=current_position,
            extras={"winner": winner_name},
        )

    def record_skipped_action(
        self,
        entity: str,
        reason: str,
        state: int,
        *,
        trigger: str = "",
        current_position: int | None = None,
        inverse_state: bool = False,
        extras: dict | None = None,
    ) -> None:
        """Record a skipped cover action for diagnostic tracking.

        Kept as a public method so the coordinator can still record skips that
        happen before apply_position is reached (e.g. outside time window checks
        done at a higher level).

        Args:
            entity: Cover entity ID.
            reason: Machine-readable skip reason code.
            state: Calculated target position that was skipped.
            trigger: Source that triggered the positioning attempt
                (e.g. "solar", "startup", "sunset").  Empty string when unknown.
            current_position: Actual cover position at skip time, or None if unknown.
            inverse_state: Whether inverse-state mapping was in effect.
            extras: Optional dict of reason-specific context fields (e.g.
                position_delta, elapsed_minutes) merged into the record.

        """
        self._diag.record_skipped_action(
            entity,
            reason,
            state,
            trigger=trigger,
            current_position=current_position,
            inverse_state=inverse_state,
            extras=extras,
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _elapsed_minutes(self, entity_id: str) -> float | None:
        """Return minutes elapsed since last command to entity_id, or None."""
        return gates.elapsed_minutes(get_last_updated(entity_id, self._hass))

    def _skip(
        self,
        entity_id: str,
        reason: str,
        position: int,
        *,
        trigger: str = "",
        inverse_state: bool = False,
        current_position: int | None = None,
        extras: dict | None = None,
    ) -> tuple[str, str]:
        """Record and return a skip result.

        Args:
            entity_id: Cover entity that was skipped.
            reason: Machine-readable skip reason code.
            position: Calculated target position that would have been sent.
            trigger: Source that triggered the positioning attempt.
            inverse_state: Whether inverse-state mapping was in effect.
            current_position: Actual cover position at skip time.
            extras: Reason-specific diagnostic fields merged into the record.

        """
        self._logger.debug(
            "Skipped %s → %s%% (%s) [trigger=%s]", entity_id, position, reason, trigger
        )
        self._diag.record_skipped_action(
            entity_id,
            reason,
            position,
            trigger=trigger,
            current_position=current_position,
            inverse_state=inverse_state,
            extras=extras,
        )
        self._diag.record_skip_event(
            entity_id,
            reason,
            position,
            trigger=trigger,
            inverse_state=inverse_state,
            current_position=current_position,
            extras=extras,
        )
        return "skipped", reason

    def _prepare_service_call(
        self,
        entity: str,
        state: int,
        inverse_state: bool = False,  # noqa: FBT001 — kept for signature clarity
        caps: dict[str, bool] | None = None,
        reset_retries: bool = True,
        is_safety: bool = False,
        use_my_position: bool = False,  # noqa: FBT001
    ) -> tuple[str | None, dict | None, bool]:
        """Build the HA service call for this cover/state.

        Updates ``wait_for_target``, ``target_call``, ``_sent_at``, and
        starts the command grace period.

        Args:
            entity: Cover entity ID
            state: Target position (0-100)
            inverse_state: Whether inverse state is applied (for tracking)
            caps: Pre-fetched capabilities dict; fetched internally if None
            reset_retries: If True (default), clears retry count and gave_up flag
                for this entity when a new target is recorded. Pass False from
                ``_execute_command`` so reconciliation retries do not reset the
                counter they themselves manage.
            is_safety: If True, this target was set via a safety override
                (force override, weather handler).  Adds the entity to
                ``_safety_targets`` so reconciliation will resend it even when
                automatic control is off or outside the time window.
                Non-safety targets remove the entity from ``_safety_targets``.
            use_my_position: If True and the cover lacks set_cover_position,
                send cover.stop_cover to trigger the hardware My preset instead
                of falling back to open/close threshold routing.

        Returns:
            (service_name, service_data, supports_position).
            (None, None, False) if cover is not capable.

        """
        if caps is None:
            caps = self.get_cover_capabilities(entity)

        # Pick the axis the policy targets by default for this entity. Single-axis
        # policies (blind/awning/tilt) always return the same axis; venetian
        # returns its position axis here — its tilt axis is dispatched separately
        # through ``after_position_command`` and the DualAxisSequencer.
        axis = self._policy.select_default_axis(caps)

        plan = route_service_call(
            entity,
            state,
            caps,
            axis=axis,
            use_my_position=use_my_position,
            open_close_threshold=self._open_close_threshold,
            endpoint_use_open_close=self._endpoint_use_open_close,
        )

        self._logger.debug(
            "Prepare service call: %s supports_position=%s caps=%s",
            entity,
            plan.supports_position,
            caps,
        )

        if plan.service is None:
            self._logger.warning(
                "Cover %s does not support both open and close. Skipping.", entity
            )
            return None, None, False

        if plan.service == "stop_cover":
            self._logger.debug(
                "My-position routing: stop_cover → %s (My = %d%%)", entity, state
            )
        elif plan.service in ("open_cover", "close_cover"):
            self._logger.debug(
                "Open/close control: state=%s threshold=%s service=%s",
                state,
                self._open_close_threshold,
                plan.service,
            )

        # State mutation: record the outbound command so reconciliation, manual
        # override detection, and the grace-period manager all see the same
        # target/timestamp.
        now = dt.datetime.now(dt.UTC)
        # Synthetic travel direction (open/close-only covers): compute BEFORE the
        # target is overwritten, from the cover's current raw position vs the
        # routed target. Uses the command-gate read (no assumed fallback) so the
        # comparison stays in the raw display frame.
        prior_position = self._read_position_with_capabilities(entity, caps)
        s = self.state(entity)
        s.target = plan.routed_target
        self._set_transit_direction_if_blind(
            entity, plan.routed_target, prior_position, caps
        )
        # Issue #888: this is the single chokepoint every dispatched command
        # (apply_position + reconciliation) flows through, so refresh the
        # display-only assumed position here. Open/close-only covers with no
        # native feedback (Somfy RTS) stash the routed target so the card shows
        # where ACP drove them; position-capable covers clear any stale assumed
        # value (dedupes clear-on-native-command). It only writes/clears the
        # assumed store — never read by the gates, which already ran — so §3b
        # holds. Runs before the dry-run early return so a dry-run cycle keeps
        # the display honest too.
        self._record_assumed_if_blind(entity, plan.routed_target, caps)
        s.waiting = True
        s.sent_at = now
        s.last_progress_at = None
        if reset_retries:
            s.retry_count = 0  # New target resets retry count
            s.gave_up = False  # Allow warnings again for new target
        # Track whether this target was set by a safety override so
        # reconciliation knows whether to resend it when auto_control is off.
        s.is_safety = is_safety
        self._grace_mgr.start_command_grace_period(entity)
        if self._on_command_sent is not None:
            self._on_command_sent(entity)

        return plan.service, plan.service_data, plan.supports_position

    async def _execute_command(self, entity_id: str, target: int) -> None:
        """Send command directly, bypassing gate checks (reconciliation use only).

        Does NOT reset the retry count — the caller
        (``run_reconciliation_pass``) owns that.

        NB: callers are responsible for entity-loaded-ness. Reconciliation only
        runs for entities that already passed the cover_unavailable gate in
        ``apply_position`` (issue #342), so no duplicate gate is needed here.
        """
        service, service_data, _ = self._prepare_service_call(
            entity_id, target, reset_retries=False
        )
        if service is None:
            return
        if self._dry_run:
            self._logger.info(
                "[dry_run] reconciliation would send cover.%s %s → %s%%",
                service,
                entity_id,
                target,
            )
            return
        ctx = Context()
        self._position_context_tracker.record(ctx.id)
        try:
            await self._hass.services.async_call(
                COVER_DOMAIN, service, service_data, context=ctx
            )
        except HomeAssistantError as err:
            self._logger.warning(
                "Reconciliation service call %s.%s failed for %s: %s",
                COVER_DOMAIN,
                service,
                entity_id,
                err,
            )

    def _track_action(
        self,
        entity: str,
        service: str,
        state: int,
        supports_position: bool,
        inverse_state: bool = False,
        *,
        target_source: str = "",
        force: bool = False,
        is_safety: bool = False,
        trigger: str = "",
        auto_control_at_call: bool | None = None,
        manual_override_at_call: bool | None = None,
        in_time_window_at_call: bool | None = None,
        enabled_at_call: bool | None = None,
        pipeline_handler: str | None = None,
        pipeline_control_method: str | None = None,
        pipeline_bypass_auto_control: bool | None = None,
        decision_trace_at_call: list | None = None,
        gates_evaluated: dict | None = None,
    ) -> None:
        """Update last_cover_action diagnostic dict and record to event buffer."""
        self._diag.record_action(
            entity,
            service,
            state,
            supports_position,
            threshold_used=(
                self._open_close_threshold if not supports_position else None
            ),
            recorded_target=self._get(entity).target,
            inverse_state=inverse_state,
            target_source=target_source,
            force=force,
            is_safety=is_safety,
            trigger=trigger,
            auto_control_at_call=auto_control_at_call,
            manual_override_at_call=manual_override_at_call,
            in_time_window_at_call=in_time_window_at_call,
            enabled_at_call=enabled_at_call,
            pipeline_handler=pipeline_handler,
            pipeline_control_method=pipeline_control_method,
            pipeline_bypass_auto_control=pipeline_bypass_auto_control,
            decision_trace_at_call=decision_trace_at_call,
            gates_evaluated=gates_evaluated,
        )
