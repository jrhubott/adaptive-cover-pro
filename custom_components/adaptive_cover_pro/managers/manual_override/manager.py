"""Manual-override engine for Adaptive Cover Pro.

``AdaptiveCoverManager`` is the stateful host: it owns per-entity override
state, the diagnostic ring buffer, the Issue-#33 suppression bookkeeping, and
the command-timing clock. It delegates the *decision* — is this state change a
manual override? — to a pluggable :class:`.detector.OverrideDetector` and
applies the returned :class:`.detector.OverrideDecision`. Edge transitions
(into/out of manual override) fire callbacks so command-side effects
(``discard_target``) wire once and every current and future detector inherits
them.
"""

from __future__ import annotations

import collections
import datetime as dt
import logging
from collections.abc import Callable

from homeassistant.core import HomeAssistant

from ...const import (
    CONF_VENETIAN_BACKROTATE_PUBLISH_LAG,
    DEFAULT_DEBUG_EVENT_BUFFER_SIZE,
)
from ...diagnostics.event_buffer import EventBuffer
from ...helpers import check_cover_features
from ..common import EventRecorder
from .detector import (
    DetectionContext,
    DetectorConfig,
    OverrideDecision,
    OverrideDetector,
    StopToMy,
    UserContextChange,
)
from .expiry import expiry_for_started_at, started_at_for_expiry
from .position_delta import PositionDeltaDetector
from .secondary_axis import SecondaryAxisCheck

_LOGGER = logging.getLogger(__name__)

# Issue #33 Phase 5: per-entity counters of primary-axis publish-lag
# suppressions are pruned to events newer than this. 24 h matches what the
# diagnostic file surfaces under ``primary_axis_suppression_last_24h``.
_PRIMARY_AXIS_SUPPRESSION_WINDOW = dt.timedelta(hours=24)
# WARN throttle: per-entity, fire on the first suppression then at most once
# per hour to keep the log readable for users with chronically slow actuators.
_PRIMARY_AXIS_SUPPRESSION_WARN_THROTTLE = dt.timedelta(hours=1)


def _never(_entity_id: str) -> bool:
    """Return False always — the default predicate when a gate is disabled."""
    return False


class AdaptiveCoverManager:
    """Track position changes and manage manual override detection.

    Monitors cover position changes to detect user-initiated manual overrides.
    Maintains per-cover manual control state with configurable duration and
    reset behavior. The detection decision is delegated to a pluggable
    :class:`.detector.OverrideDetector` (default
    :class:`.position_delta.PositionDeltaDetector`); this class owns all state
    and side effects.

    """

    def __init__(
        self,
        hass: HomeAssistant,
        reset_duration: dict[str, int],
        logger,
        *,
        event_buffer: EventBuffer | None = None,
        detector: OverrideDetector | None = None,
        on_engaged: Callable[[str], None] | None = None,
        on_cleared: Callable[[list[str]], None] | None = None,
    ) -> None:
        """Initialize the AdaptiveCoverManager.

        Args:
            hass: Home Assistant instance
            reset_duration: Duration dict (e.g., {"minutes": 15}) for auto-reset
            logger: Logger instance for debug output
            event_buffer: Shared ring buffer owned by the coordinator. When None
                a private buffer is created (useful for unit tests).
            detector: Active detection strategy. Defaults to
                ``PositionDeltaDetector`` (the historical behaviour).
            on_engaged: Callback fired with the entity_id whenever a cover
                transitions into manual override via a detection channel.
            on_cleared: Callback fired with a list of entity_ids whenever
                manual override is cleared (reset or auto-expiry).

        """
        self.hass = hass
        self.covers: set[str] = set()

        self.manual_control: dict[str, bool] = {}
        self.manual_control_time: dict[str, dt.datetime] = {}
        self.reset_duration = dt.timedelta(**reset_duration)
        self.logger = logger
        self._event_buffer: EventBuffer = (
            event_buffer
            if event_buffer is not None
            else EventBuffer(maxlen=DEFAULT_DEBUG_EVENT_BUFFER_SIZE)
        )
        self._events = EventRecorder(self._event_buffer)
        # Issue #33 Phase 5: rolling per-entity log of primary-axis publish-lag
        # suppressions and a per-entity WARN throttle. Both live on the
        # manager (per-instance state, side-effect bookkeeping); the
        # predicate that decides whether to fire lives on the cover-type
        # policy (cover-type-specific behaviour). Per CODING_GUIDELINES.md §
        # "Managers Hold State, Policies Hold Behavior".
        self._primary_axis_suppression_counts: dict[
            str, collections.deque[dt.datetime]
        ] = {}
        self._last_suppression_warn_at: dict[str, dt.datetime] = {}

        self._detector: OverrideDetector = (
            detector if detector is not None else PositionDeltaDetector()
        )
        self._on_engaged = on_engaged
        self._on_cleared = on_cleared
        # Last ACP command time per entity (float UTC timestamp), feeding the
        # ``seconds_since_command`` context field for time-based detectors.
        self._last_command_at: dict[str, float] = {}
        # ACP-origin predicate over a context id; the coordinator wires the
        # real one. Default treats nothing as ACP-originated.
        self._is_acp_context_fn: Callable[[str | None], bool] = lambda _cid: False
        # Display-only assumed-position invalidator (issue #888). The coordinator
        # wires this to ``CoverCommandService.clear_assumed_position`` so the
        # stashed My/assumed value is dropped the moment the override resets or a
        # real numeric position read arrives. Default is a no-op.
        self._invalidate_assumed: Callable[[str], None] = lambda _eid: None

    # --- wiring -----------------------------------------------------------

    def set_transition_callbacks(
        self,
        *,
        on_engaged: Callable[[str], None] | None = None,
        on_cleared: Callable[[list[str]], None] | None = None,
    ) -> None:
        """Register edge-transition callbacks after construction."""
        self._on_engaged = on_engaged
        self._on_cleared = on_cleared

    def set_acp_context_predicate(self, fn: Callable[[str | None], bool]) -> None:
        """Register the predicate that recognises ACP-originated context ids."""
        self._is_acp_context_fn = fn

    def set_assumed_invalidator(self, fn: Callable[[str], None]) -> None:
        """Register the callback that drops a cover's assumed display position (#888)."""
        self._invalidate_assumed = fn

    def update_config(self, config: DetectorConfig) -> None:
        """Apply an options change at runtime (no reload).

        Refreshes the auto-reset duration and forwards the config to the active
        detector. This is what lets the manual-override duration take effect
        without a config-entry reload.
        """
        self.reset_duration = dt.timedelta(**config.duration)
        self._detector.update_config(config)

    @property
    def detector(self) -> OverrideDetector:
        """Return the active detection strategy."""
        return self._detector

    # --- diagnostics bookkeeping -----------------------------------------

    def _record_primary_axis_suppression(self, entity_id: str, *, delta: float) -> None:
        """Log a primary-axis publish-lag suppression and throttle the WARN.

        Issue #33 Phase 5 (cross-axis): the position-axis equivalent of
        ``manual_override_rejected_tilt_suppression``. Keeps a 24 h
        rolling window of suppressions per entity (surfaced under
        ``primary_axis_suppression_last_24h`` in diagnostics) and emits a
        WARN line on the first hit per entity plus at most one per hour.

        Why a counter at all: a user whose actuator publishes its
        post-move state inside the default 45 s window is silent —
        suppression does the right thing. A user whose actuator publishes
        at 60+ s gets repeated rejections, and the counter is the only
        signal in diagnostics that tells them to bump
        ``venetian_backrotate_publish_lag``. The throttled WARN log makes
        the same suggestion visible in the HA log.
        """
        now = dt.datetime.now(dt.UTC)
        deque = self._primary_axis_suppression_counts.setdefault(
            entity_id, collections.deque()
        )
        deque.append(now)
        cutoff = now - _PRIMARY_AXIS_SUPPRESSION_WINDOW
        while deque and deque[0] < cutoff:
            deque.popleft()

        last_warn = self._last_suppression_warn_at.get(entity_id)
        if (
            last_warn is None
            or (now - last_warn) >= _PRIMARY_AXIS_SUPPRESSION_WARN_THROTTLE
        ):
            _LOGGER.warning(
                "Primary-axis manual override suppressed for %s "
                "(publish-lag, delta=%.1f%%, count_last_24h=%d). "
                "If this fires repeatedly for the same actuator, "
                "increase '%s' in options.",
                entity_id,
                delta,
                len(deque),
                CONF_VENETIAN_BACKROTATE_PUBLISH_LAG,
            )
            self._last_suppression_warn_at[entity_id] = now

    def primary_axis_suppression_counts(self) -> dict[str, int]:
        """Return per-entity counts of primary-axis suppressions in the last 24 h.

        Used by ``DiagnosticsBuilder._build_debug_info`` to surface the
        cross-axis publish-lag guard's activity. Empty entries are pruned
        so the returned dict only carries entities with at least one
        suppression in the window.
        """
        return {
            eid: len(deque)
            for eid, deque in self._primary_axis_suppression_counts.items()
            if deque
        }

    def _record_event(
        self,
        entity_id: str,
        event_name: str,
        *,
        our_state,
        new_position,
        effective_threshold=None,
        reason: str = "",
    ) -> None:
        """Append a manual-override decision event to the shared ring buffer."""
        self._events.record(
            event_name,
            entity_id=entity_id,
            our_state=our_state,
            new_position=new_position,
            effective_threshold=effective_threshold,
            reason=reason,
        )

    def get_event_buffer(self) -> list[dict]:
        """Return a snapshot of the ring buffer (backward-compat wrapper)."""
        return self._event_buffer.snapshot()

    def resize_event_buffer(self, size: int) -> None:
        """Resize the ring buffer, preserving the most-recent events (backward-compat wrapper)."""
        self._event_buffer.resize(size)

    def add_covers(self, entity):
        """Add covers to tracking.

        Updates the set of tracked cover entities. Called during coordinator
        updates to ensure all configured covers are being monitored.

        Args:
            entity: List or set of cover entity IDs to track

        """
        self.covers.update(entity)
        self._detector.on_covers_added(entity)

    # --- command-timing clock --------------------------------------------

    def note_command_sent(self, entity_id: str) -> None:
        """Record that ACP just issued a command to ``entity_id``.

        Stamps the post-command settle clock (feeding ``seconds_since_command``
        on the detection context) and notifies the active detector.
        """
        now = dt.datetime.now(dt.UTC)
        self._last_command_at[entity_id] = now.timestamp()
        self._detector.note_command_sent(entity_id, now)

    def _seconds_since_command(self, entity_id: str, now: dt.datetime) -> float | None:
        """Seconds since the last ACP command for ``entity_id`` (None if never)."""
        ts = self._last_command_at.get(entity_id)
        return (now.timestamp() - ts) if ts is not None else None

    # --- decision application --------------------------------------------

    def _apply_decision(
        self,
        entity_id: str,
        decision: OverrideDecision,
        *,
        set_timestamp: Callable[[], None],
    ) -> None:
        """Apply a detector decision: record events, suppression, mark, fire edge.

        ``set_timestamp`` is invoked (only when marking) to record the
        per-channel ``manual_control_time``. The not-manual→manual edge fires
        ``on_marked`` + the ``on_engaged`` callback.
        """
        if decision.event_name is not None:
            self._record_event(
                entity_id, decision.event_name, **(decision.event_kwargs or {})
            )
        if decision.record_primary_axis_suppression:
            self._record_primary_axis_suppression(
                entity_id, delta=decision.suppression_delta
            )
        if decision.mark_manual:
            was_manual = self.is_cover_manual(entity_id)
            self.mark_manual_control(entity_id)
            set_timestamp()
            if not was_manual:
                self._detector.on_marked(entity_id)
                if self._on_engaged is not None:
                    self._on_engaged(entity_id)

    def handle_state_change(
        self,
        states_data,
        our_state,
        policy,
        allow_reset,
        is_waiting,
        manual_threshold,
        *,
        has_recorded_target: bool = True,
        secondary_axis_check: SecondaryAxisCheck | None = None,
        is_in_command_grace: Callable[[str], bool] | None = None,
        is_in_transit: Callable[[str], bool] | None = None,
    ):
        """Process state change for manual override.

        Runs the upstream gates (tracked / wait-for-target / command-grace /
        secondary axis), resolves the primary-axis position, then delegates the
        position decision to the active detector and applies its verdict.

        Args:
            states_data: StateChangedData with entity_id, old_state, new_state
            our_state: Expected position from coordinator calculation
            policy: ``CoverTypePolicy`` describing the cover's axes. Used to
                read the new entity position via the same axis-routing rule
                that drives ``CoverCommandService`` and ``CoverProvider``.
            allow_reset: If True, updates timestamp on subsequent changes
            is_waiting: Callable(entity_id) -> bool indicating whether the cover
                is currently expected to be moving toward a commanded target.
            manual_threshold: Minimum position delta to trigger manual detection
            has_recorded_target: Whether ACP has a recorded command target for
                this entity. When False, ``our_state`` is the pipeline's
                theoretical default rather than a commanded position, so the
                numeric detector suppresses override detection (issue #546).
                The user-context and stop-to-My channels are separate methods
                and remain unaffected.
            secondary_axis_check: Optional ``SecondaryAxisCheck`` supplied by
                the cover-type policy. When provided, the secondary axis is
                evaluated up front and a manual-override match short-circuits
                the position-axis check.
            is_in_command_grace: Optional callable ``(entity_id) -> bool`` that
                returns True while the command-grace window is active. When
                True, override detection is suppressed.
            is_in_transit: Optional callable ``(entity_id) -> bool`` returning
                True when HA reports the cover state as ``opening``/``closing``;
                passed through to the detector via the context (issue #271).

        """
        event = states_data
        if event is None:
            return
        entity_id = event.entity_id
        if entity_id not in self.covers:
            return
        if is_waiting(entity_id):
            self._record_event(
                entity_id,
                "manual_override_rejected_wait_for_target",
                our_state=our_state,
                new_position=None,
                reason="wait_for_target active",
            )
            return
        if is_in_command_grace is not None and is_in_command_grace(entity_id):
            self._record_event(
                entity_id,
                "manual_override_rejected_command_grace",
                our_state=our_state,
                new_position=None,
                reason="command grace period active",
            )
            return

        new_state = event.new_state

        if secondary_axis_check is not None:
            res = secondary_axis_check.evaluate(entity_id, new_state, manual_threshold)
            if res.is_manual:
                self.logger.debug(
                    "Manual %s change for %s: ours=%s, new=%s",
                    secondary_axis_check.label,
                    entity_id,
                    secondary_axis_check.expected,
                    new_state.attributes.get(secondary_axis_check.attribute),
                )
            self._apply_decision(
                entity_id,
                OverrideDecision(
                    mark_manual=res.is_manual,
                    event_name=res.event_name,
                    event_kwargs=res.event_kwargs,
                ),
                set_timestamp=lambda: self.set_last_updated(
                    entity_id, new_state, allow_reset
                ),
            )
            if res.consumed:
                return

        # Single source of truth for "which axis carries the current value
        # on this entity?" — same path used by CoverCommandService and
        # CoverProvider, so manual-override detection sees the same number
        # the coordinator commanded against.
        caps = check_cover_features(self.hass, entity_id)
        new_position = policy.read_axis_value(
            self.hass, entity_id, caps, state_obj=new_state
        )
        # Issue #888: a genuine numeric position read supersedes any display-only
        # assumed value, so drop it. Reads that resolve to None (the Somfy-RTS
        # unknown state that made the assumed value necessary) leave it intact.
        if new_position is not None:
            self._invalidate_assumed(entity_id)
        old_state_obj = getattr(event, "old_state", None)
        old_position = (
            policy.read_axis_value(self.hass, entity_id, caps, state_obj=old_state_obj)
            if old_state_obj is not None
            else None
        )

        now = dt.datetime.now(dt.UTC)
        ctx_obj = getattr(new_state, "context", None)
        context_user_id = getattr(ctx_obj, "user_id", None) if ctx_obj else None
        context_id = getattr(ctx_obj, "id", None) if ctx_obj else None
        context = DetectionContext(
            entity_id=entity_id,
            our_state=our_state,
            new_state=new_state,
            old_state=old_state_obj,
            new_position=new_position,
            old_position=old_position,
            caps=caps,
            policy=policy,
            manual_threshold=manual_threshold,
            has_recorded_target=has_recorded_target,
            allow_reset=allow_reset,
            is_acp_context=self._is_acp_context_fn(context_id),
            context_user_id=context_user_id,
            context_id=context_id,
            seconds_since_command=self._seconds_since_command(entity_id, now),
            secondary_axis_check=secondary_axis_check,
            is_waiting=is_waiting,
            is_in_command_grace=is_in_command_grace or _never,
            is_in_transit=is_in_transit or _never,
            now=now,
        )
        decision = self._detector.detect(context)
        resolved_allow_reset = (
            decision.allow_reset if decision.allow_reset is not None else allow_reset
        )
        self._apply_decision(
            entity_id,
            decision,
            set_timestamp=lambda: self.set_last_updated(
                entity_id, new_state, resolved_allow_reset
            ),
        )

    def handle_user_initiated_state_change(
        self,
        entity_id: str,
        new_state,
        allow_reset: bool,
        *,
        context_user_id: str | None,
        context_id: str | None,
    ) -> bool:
        """Mark manual override for a state change confirmed user-initiated by HA context.

        Called from the coordinator when ``new_state.context`` carries a non-None
        ``user_id`` and ``context.id`` is **not** in the ACP position-context
        tracker. Delegates to the active detector's ``on_user_context_change``
        hook (default: mark manual). This path bypasses the position-math
        comparison because the math is unreliable for assumed-state and
        OPEN/CLOSE-only covers.

        Returns True when the override was set, False when the entity is not
        tracked (or the detector declined).
        """
        if entity_id not in self.covers:
            return False
        decision = self._detector.on_user_context_change(
            UserContextChange(
                entity_id=entity_id,
                new_state=new_state,
                allow_reset=allow_reset,
                context_user_id=context_user_id,
                context_id=context_id,
            )
        )
        if decision is None:
            return False
        self.logger.debug(
            "Manual override via user-initiated state change for %s "
            "(context user_id=%s, id=%s)",
            entity_id,
            context_user_id,
            context_id,
        )
        resolved_allow_reset = (
            decision.allow_reset if decision.allow_reset is not None else allow_reset
        )
        self._apply_decision(
            entity_id,
            decision,
            set_timestamp=lambda: self.set_last_updated(
                entity_id, new_state, resolved_allow_reset
            ),
        )
        return True

    def handle_stop_service_call(
        self,
        entity_id: str,
        my_position_value: int,
        is_waiting,
        *,
        context_user_id: str | None = None,
        context_id: str | None = None,
        context_parent_id: str | None = None,
    ) -> bool:
        """Mark manual override when a user-initiated cover.stop_cover is detected.

        Delegates to the active detector's ``on_stop_to_my`` hook (default: mark
        unless the cover is still moving toward a commanded target). Covers
        non-position-capable covers (e.g. Somfy RTS) where pressing STOP moves
        the cover to the hardware "My" preset but never reports a new position.

        Args:
            entity_id:        Cover entity_id that received stop_cover.
            my_position_value: The position (0–100) the My preset represents.
            is_waiting:       Callable(entity_id) -> bool from
                              CoverCommandService.
            context_user_id:  user_id of the stop_cover call's originating HA
                              Context, if any (issue #875 diagnostic capture).
            context_id:       id of the stop_cover call's originating HA Context.
            context_parent_id: parent_id of the originating HA Context, if any.

        Returns:
            True when the stop engaged the manual override (the cover is taken to
            be at My), False when it was ignored (untracked, or the #875
            wait-for-target gate declined). The coordinator uses this to decide
            whether to record the display-only assumed My position (#888).

        """
        if entity_id not in self.covers:
            return False
        decision = self._detector.on_stop_to_my(
            StopToMy(
                entity_id=entity_id,
                my_position_value=my_position_value,
                is_waiting=is_waiting,
                context_user_id=context_user_id,
                context_id=context_id,
                context_parent_id=context_parent_id,
            )
        )
        if decision is None:
            self.logger.debug(
                "handle_stop_service_call: ignoring stop for %s — wait_for_target active",
                entity_id,
            )
            return False
        self.logger.debug(
            "Manual override via user stop_cover for %s (My position = %d%%)",
            entity_id,
            my_position_value,
        )
        self._apply_decision(
            entity_id,
            decision,
            set_timestamp=lambda: self.manual_control_time.setdefault(
                entity_id, dt.datetime.now(dt.UTC)
            ),
        )
        return True

    def set_last_updated(self, entity_id, new_state, allow_reset):
        """Set last updated time for manual control.

        Records timestamp of manual override detection for duration tracking.
        Behavior depends on allow_reset setting: if True, updates timestamp
        on each manual change; if False, keeps original timestamp to prevent
        duration extension.

        Args:
            entity_id: Cover entity ID
            new_state: New state object containing last_updated timestamp
            allow_reset: If True, updates timestamp on subsequent changes

        """
        if entity_id not in self.manual_control_time or allow_reset:
            last_updated = new_state.last_updated
            self.manual_control_time[entity_id] = last_updated
            self.logger.debug(
                "Updating last updated for manual control to %s for %s. Allow reset:%s",
                last_updated,
                entity_id,
                allow_reset,
            )
        elif not allow_reset:
            self.logger.debug(
                "Already manual control time specified for %s, reset is not allowed by user setting:%s",
                entity_id,
                allow_reset,
            )

    def mark_manual_control(self, cover: str) -> None:
        """Mark cover as manual.

        Sets manual control flag for cover. Called when manual override is
        detected. Prevents automatic position commands until reset.

        Args:
            cover: Cover entity ID to mark

        """
        self.manual_control[cover] = True

    def _engage(self, entity_id: str, *, timestamp: dt.datetime, reason: str) -> None:
        """Engage manual override on one cover without commanding movement.

        Shared engine for the wall-switch / input-sensor path
        (:meth:`engage_manual_override_from_external`) and the
        ``engage_manual_override`` service (:meth:`engage_override`) — both
        delegate here rather than re-implementing the mark/event/edge block
        (no-duplication rule). Routes through :meth:`_apply_decision` with
        ``mark_manual=True`` and a ``set_timestamp`` that **overwrites**
        ``manual_control_time`` with ``timestamp`` (extend semantics — the
        overwrite lands even when the cover was already manual). Sends no cover
        command; the not-manual→manual edge fires ``on_engaged`` so any latched
        target is discarded.

        Args:
            entity_id: Cover entity ID to engage.
            timestamp: Value written to ``manual_control_time`` — the derived
                end time is ``timestamp + reset_duration``.
            reason: Short label recorded into the diagnostic event buffer.

        """
        self._apply_decision(
            entity_id,
            OverrideDecision(
                mark_manual=True,
                event_name="manual_override_set",
                event_kwargs={
                    "our_state": None,
                    "new_position": None,
                    "reason": reason,
                },
            ),
            set_timestamp=lambda: self.manual_control_time.__setitem__(
                entity_id, timestamp
            ),
        )

    def engage_manual_override_from_external(self, reason: str) -> None:
        """Engage manual override on every tracked cover from an external trigger.

        Wall-switch / input-sensor path (issue #688): an off→on edge on a
        configured input binary sensor means the user physically operated the
        cover, so ACP pauses auto-control on *every* cover in this instance and
        drops any latched target. Each cover is engaged via :meth:`_engage`
        with a fresh ``now`` timestamp, re-arming the full override duration on
        every press (overwrite, not ``setdefault``) and firing the
        ``on_engaged`` edge on the fresh transition.

        Args:
            reason: Short label recorded into the diagnostic event buffer
                (e.g. ``"input_sensor"``).

        """
        now = dt.datetime.now(dt.UTC)
        for entity_id in self.covers:
            self._engage(entity_id, timestamp=now, reason=reason)

    def engage_override(
        self,
        entity_id: str,
        *,
        end_time: dt.datetime | None = None,
        duration: dt.timedelta | None = None,
        reason: str,
    ) -> None:
        """Engage or extend manual override on one cover, without moving it.

        Backs the ``adaptive_cover_pro.engage_manual_override`` service. Sends
        no cover command — engages purely through the override state machine via
        :meth:`_engage`. Never raises: an invalid ``end_time`` / ``duration``
        falls through to the next rule.

        Precedence (first valid wins):

        1. ``end_time`` present and strictly in the future → the override ends
           at ``end_time``; ``duration`` is ignored (logged at debug). A naive
           ``end_time`` is treated as UTC.
        2. else ``duration`` positive → EXTEND-BY: if the cover is already under
           manual override, add ``duration`` to its **current** end; otherwise
           engage fresh for ``now + duration``.
        3. else (both absent/invalid) → fall back to ``now + reset_duration``
           (i.e. ``manual_control_time = now``).

        The chosen target end is converted to the stored start via the SSOT
        inverse :func:`.expiry.started_at_for_expiry`; the timestamp is
        overwritten so a later call can move the end.

        Args:
            entity_id: Cover entity ID to engage.
            end_time: Optional absolute end (aware or naive-UTC datetime).
            duration: Optional relative extend-by timedelta.
            reason: Short label recorded into the diagnostic event buffer.

        """
        now = dt.datetime.now(dt.UTC)
        if end_time is not None and end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=dt.UTC)

        target_expiry: dt.datetime | None = None
        if end_time is not None and end_time > now:
            target_expiry = end_time
            if duration is not None:
                self.logger.debug(
                    "engage_override: both end_time and duration supplied for "
                    "%s — using end_time, ignoring duration",
                    entity_id,
                )
        elif duration is not None and duration > dt.timedelta(0):
            if self.is_cover_manual(entity_id):
                current_end = expiry_for_started_at(
                    self.manual_control_time[entity_id], self.reset_duration
                )
                target_expiry = current_end + duration
            else:
                target_expiry = now + duration

        if target_expiry is not None:
            timestamp = started_at_for_expiry(target_expiry, self.reset_duration)
        else:
            timestamp = now
        self._engage(entity_id, timestamp=timestamp, reason=reason)

    def mark_user_command(self, entity_id: str, *, reason: str) -> None:
        """Engage manual override pre-emptively from an ACP-owned surface.

        Pre-emptive analog of :meth:`handle_user_initiated_state_change` which
        is post-facto: called from the proxy cover entity and the
        ``adaptive_cover_pro.set_position`` service before the cover even
        moves so the next coordinator cycle does not yank the cover off the
        user's set point.

        Uses ``setdefault`` for ``manual_control_time`` so successive drags
        do not extend the override window (matches ``allow_reset=False``
        semantics).  Does not require the entity to be in ``self.covers`` —
        the proxy may dispatch before ``add_covers`` runs. Does NOT fire the
        ``on_engaged`` edge callback: these ACP-routed paths set a command
        target immediately afterward, so the command-side discard would be
        counter-productive.

        Args:
            entity_id: Cover entity ID to mark as manually overridden.
            reason: Short label recorded into the diagnostic event buffer
                (e.g. ``"proxy_managed"``, ``"set_position"``).

        """
        self.manual_control[entity_id] = True
        self.manual_control_time.setdefault(entity_id, dt.datetime.now(dt.UTC))
        self._record_event(
            entity_id,
            "manual_override_set",
            our_state=None,
            new_position=None,
            reason=reason,
        )

    async def reset_if_needed(self) -> set[str]:
        """Reset expired manual overrides.

        Checks all covers with manual control timestamps and resets those where
        configured duration has elapsed. Called on every coordinator update to
        ensure timely automatic reset.

        Returns:
            Set of entity IDs whose manual override just expired this call.
            Empty set when nothing changed. The coordinator uses this to
            proactively send the current pipeline position to those covers
            so they don't linger at the user-moved position.

        """
        expired: set[str] = set()
        current_time = dt.datetime.now(dt.UTC)
        manual_control_time_copy = dict(self.manual_control_time)
        for entity_id, last_updated in manual_control_time_copy.items():
            if current_time - last_updated > self.reset_duration:
                self.logger.debug(
                    "Resetting manual override for %s, because duration has elapsed",
                    entity_id,
                )
                self.reset(entity_id)
                expired.add(entity_id)
        return expired

    def reset(self, entity_id):
        """Reset manual control.

        Clears manual control flag and timestamp for cover. Called when duration
        expires, user presses reset button, or manual detection is disabled.
        Re-enables automatic position commands. Notifies the active detector and
        fires the ``on_cleared`` edge callback.

        Args:
            entity_id: Cover entity ID to reset

        """
        self.manual_control[entity_id] = False
        self.manual_control_time.pop(entity_id, None)
        # Issue #888: the assumed My/display position was a stand-in while the
        # override held the cover; clearing the override retires it too.
        self._invalidate_assumed(entity_id)
        self.logger.debug("Reset manual override for %s", entity_id)
        self._record_event(
            entity_id,
            "manual_override_reset",
            our_state=None,
            new_position=None,
            reason="manual override cleared",
        )
        self._detector.on_reset(entity_id)
        if self._on_cleared is not None:
            self._on_cleared([entity_id])

    def is_cover_manual(self, entity_id):
        """Check if cover is manual.

        Args:
            entity_id: Cover entity ID to check

        Returns:
            True if cover is under manual control, False otherwise

        """
        return self.manual_control.get(entity_id, False)

    @property
    def binary_cover_manual(self):
        """Check if any cover is manual.

        Returns:
            True if at least one tracked cover is under manual control,
            False if all covers are under automatic control

        """
        return any(value for value in self.manual_control.values())

    @property
    def manual_controlled(self):
        """Get list of manual covers.

        Returns:
            List of cover entity IDs currently under manual control

        """
        return [k for k, v in self.manual_control.items() if v]


def inverse_state(state: int) -> int:
    """Inverse state."""
    return 100 - state
