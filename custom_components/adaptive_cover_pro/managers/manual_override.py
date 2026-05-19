"""Manual override management for Adaptive Cover Pro."""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant

from ..const import DEFAULT_DEBUG_EVENT_BUFFER_SIZE, POSITION_TOLERANCE_PERCENT
from ..diagnostics.event_buffer import EventBuffer
from ..helpers import check_cover_features


def effective_manual_threshold(user_threshold: int | None) -> int:
    """Resolve the effective manual-override threshold for a delta comparison.

    Floored at ``POSITION_TOLERANCE_PERCENT`` so motor rounding and reporting
    imprecision can't trip false positives even when the user configures
    ``manual_threshold = 0`` or leaves it unset. Both the primary-axis check
    in ``handle_state_change`` and the secondary-axis check in
    ``SecondaryAxisCheck.evaluate`` delegate here; keeping the two in sync
    via a single helper prevents the formula from drifting (e.g. the day
    ``POSITION_TOLERANCE_PERCENT`` changes).
    """
    return max(
        user_threshold if user_threshold is not None else 0, POSITION_TOLERANCE_PERCENT
    )


@dataclasses.dataclass(frozen=True, slots=True)
class SecondaryAxisResult:
    """Outcome of evaluating a non-primary axis for manual-override drift.

    ``consumed`` short-circuits the position-axis check (caller returns
    immediately). ``is_manual`` triggers ``mark_manual_control`` +
    ``set_last_updated``. ``event_name`` (with ``event_kwargs``) appends a
    record to the diagnostics ring buffer.
    """

    consumed: bool = False
    is_manual: bool = False
    event_name: str | None = None
    event_kwargs: dict[str, Any] | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class SecondaryAxisCheck:
    """Per-cover-type plug for manual-override evaluation on a secondary axis.

    Built once per cover-state-change cycle by ``CoverTypePolicy.secondary_axis_check``.
    Encapsulates the expected value (e.g. tilt resolved by the engine), the
    HA state attribute to read, an optional suppression callback (e.g.
    venetian's motor back-rotate window), and a label that flavours the
    diagnostic event names. ``handle_state_change`` calls ``evaluate`` once
    and dispatches on the returned ``SecondaryAxisResult`` — the manager
    itself stays ignorant of which axis is being checked.
    """

    expected: int
    attribute: str  # e.g. "current_tilt_position"
    label: str  # e.g. "tilt" — flavours the rejection-reason text
    suppression: Callable[[str, float], bool] | None = None

    def evaluate(
        self,
        entity_id: str,
        new_state,
        manual_threshold: int | None,
    ) -> SecondaryAxisResult:
        """Decide what (if anything) the secondary axis tells the manager to do."""
        new_value = new_state.attributes.get(self.attribute)
        if new_value is None:
            return SecondaryAxisResult()

        effective_threshold = effective_manual_threshold(manual_threshold)
        delta = abs(self.expected - new_value)

        # Check suppression BEFORE the on-target short-circuit. When the motor
        # back-drives the position axis during tilt settling, tilt may arrive
        # exactly on target while the position axis still shows back-drive drift.
        # Returning consumed=False here would let the position-axis check run and
        # falsely trip manual override on that drift.
        if self.suppression is not None and self.suppression(entity_id, delta):
            return SecondaryAxisResult(
                consumed=True,
                event_name="manual_override_rejected_tilt_suppression",
                event_kwargs={
                    "our_state": self.expected,
                    "new_position": new_value,
                    "effective_threshold": effective_threshold,
                    "reason": (
                        f"{self.label} delta {delta:.1f}% within venetian "
                        "back-rotate window; suppressing both tilt and position checks"
                    ),
                },
            )

        if new_value == self.expected:
            return SecondaryAxisResult()

        if delta >= effective_threshold:
            return SecondaryAxisResult(
                consumed=True,
                is_manual=True,
                event_name="manual_override_set",
                event_kwargs={
                    "our_state": self.expected,
                    "new_position": new_value,
                    "effective_threshold": effective_threshold,
                    "reason": (
                        f"{self.label} delta {delta:.1f}% >= threshold {effective_threshold}%"
                    ),
                },
            )

        # Below threshold and not suppressed — preserve the legacy "silent
        # fall-through" behavior so the position-axis check still runs.
        return SecondaryAxisResult()


class AdaptiveCoverManager:
    """Track position changes and manage manual override detection.

    Monitors cover position changes to detect user-initiated manual overrides.
    Maintains per-cover manual control state with configurable duration and
    reset behavior. Provides methods to check, set, and reset manual override
    status for individual covers or all tracked covers.

    """

    def __init__(
        self,
        hass: HomeAssistant,
        reset_duration: dict[str, int],
        logger,
        *,
        event_buffer: EventBuffer | None = None,
    ) -> None:
        """Initialize the AdaptiveCoverManager.

        Args:
            hass: Home Assistant instance
            reset_duration: Duration dict (e.g., {"minutes": 15}) for auto-reset
            logger: Logger instance for debug output
            event_buffer: Shared ring buffer owned by the coordinator. When None
                a private buffer is created (useful for unit tests).

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
        self._event_buffer.record(
            {
                "ts": dt.datetime.now(dt.UTC).isoformat(),
                "event": event_name,
                "entity_id": entity_id,
                "our_state": our_state,
                "new_position": new_position,
                "effective_threshold": effective_threshold,
                "reason": reason,
            }
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

    def handle_state_change(
        self,
        states_data,
        our_state,
        policy,
        allow_reset,
        is_waiting,
        manual_threshold,
        *,
        secondary_axis_check: SecondaryAxisCheck | None = None,
    ):
        """Process state change for manual override.

        Examines cover position changes to detect manual overrides by comparing
        new position to expected position. Ignores changes during grace periods
        (wait_for_target) and below threshold. Marks cover as manual and records
        timestamp when manual change detected.

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
            secondary_axis_check: Optional ``SecondaryAxisCheck`` supplied by
                the cover-type policy (see ``CoverTypePolicy.secondary_axis_check``).
                When provided, the secondary axis is evaluated up front and a
                manual-override match short-circuits the position-axis check.

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

        new_state = event.new_state

        if secondary_axis_check is not None:
            res = secondary_axis_check.evaluate(entity_id, new_state, manual_threshold)
            if res.event_name is not None:
                self._record_event(
                    entity_id, res.event_name, **(res.event_kwargs or {})
                )
            if res.is_manual:
                self.logger.debug(
                    "Manual %s change for %s: ours=%s, new=%s",
                    secondary_axis_check.label,
                    entity_id,
                    secondary_axis_check.expected,
                    new_state.attributes.get(secondary_axis_check.attribute),
                )
                self.mark_manual_control(entity_id)
                self.set_last_updated(entity_id, new_state, allow_reset)
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

        # Position still unavailable (entity in transient state like "opening")
        # — nothing to compare against, skip override detection.
        if new_position is None:
            self.logger.debug(
                "Position unavailable for %s (entity in transient state), skipping override check",
                entity_id,
            )
            self._record_event(
                entity_id,
                "manual_override_rejected_position_unavailable",
                our_state=our_state,
                new_position=None,
                reason="position unavailable (transient state)",
            )
            return

        # Cover's own state attribute says it's still in transit. The
        # current_position it just reported can lag the actual physical
        # position — Zigbee covers that emit a single end-of-move report
        # look like a stale-position event with state=closing/opening.
        # Wait for the next event when the cover stops; that event runs
        # the full position-math path.
        new_state_str = getattr(new_state, "state", None)
        if new_state_str in ("opening", "closing"):
            self._record_event(
                entity_id,
                "manual_override_rejected_in_transit",
                our_state=our_state,
                new_position=new_position,
                reason=f"cover state '{new_state_str}' indicates in-transit",
            )
            return

        if new_position != our_state:
            # Floor the threshold at POSITION_TOLERANCE_PERCENT so motor rounding
            # / position-reporting imprecision can't trip false positives even
            # when the user leaves manual_threshold unset. See
            # ``effective_manual_threshold`` for the single-source-of-truth.
            effective_threshold = effective_manual_threshold(manual_threshold)
            if abs(our_state - new_position) <= effective_threshold:
                self.logger.debug(
                    "Position change %s%% is less than effective threshold %s%% for %s (user threshold=%s, tolerance floor=%s)",
                    abs(our_state - new_position),
                    effective_threshold,
                    entity_id,
                    manual_threshold,
                    POSITION_TOLERANCE_PERCENT,
                )
                self._record_event(
                    entity_id,
                    "manual_override_rejected_within_threshold",
                    our_state=our_state,
                    new_position=new_position,
                    effective_threshold=effective_threshold,
                    reason=f"delta {abs(our_state - new_position):.1f}% < threshold {effective_threshold}%",
                )
                return
            self.logger.debug(
                "Manual change detected for %s. Our state: %s, new state: %s",
                entity_id,
                our_state,
                new_position,
            )
            self.logger.debug(
                "Set manual control for %s, for at least %s seconds, reset_allowed: %s",
                entity_id,
                self.reset_duration.total_seconds(),
                allow_reset,
            )
            self._record_event(
                entity_id,
                "manual_override_set",
                our_state=our_state,
                new_position=new_position,
                effective_threshold=effective_threshold,
                reason=f"delta {abs(our_state - new_position):.1f}% >= threshold {effective_threshold}%",
            )
            self.mark_manual_control(entity_id)
            self.set_last_updated(entity_id, new_state, allow_reset)

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
        tracker — i.e. a real user took action via the HA dashboard, voice
        assistant, or another front-end. This path bypasses the position-math
        comparison in :meth:`handle_state_change` because the math is unreliable
        for assumed-state and OPEN/CLOSE-only covers (the live ``current_position``
        either doesn't exist or has already been overwritten by ACP's
        reconciliation by the time the queued event is drained).

        Returns True when the override was set, False when the entity is not
        tracked.
        """
        if entity_id not in self.covers:
            return False
        self.logger.debug(
            "Manual override via user-initiated state change for %s "
            "(context user_id=%s, id=%s)",
            entity_id,
            context_user_id,
            context_id,
        )
        self._record_event(
            entity_id,
            "manual_override_set",
            our_state=None,
            new_position=None,
            reason=(
                f"user-initiated state change "
                f"(context user_id={context_user_id}, id={context_id})"
            ),
        )
        self.mark_manual_control(entity_id)
        self.set_last_updated(entity_id, new_state, allow_reset)
        return True

    def handle_stop_service_call(
        self,
        entity_id: str,
        my_position_value: int,
        is_waiting,
    ) -> None:
        """Mark manual override when a user-initiated cover.stop_cover is detected.

        Called by the coordinator's EVENT_CALL_SERVICE handler after confirming
        the stop was NOT originated by ACP (context-id check). This path covers
        non-position-capable covers (e.g. Somfy RTS) where pressing STOP moves
        the cover to the hardware "My" preset but never reports a new position,
        so the normal state-change detection path is blind to it.

        Args:
            entity_id:        Cover entity_id that received stop_cover.
            my_position_value: The position (0–100) the My preset represents.
            is_waiting:       Callable(entity_id) -> bool from
                              CoverCommandService; used as a belt-and-braces
                              guard on top of the context-id filter.

        """
        if entity_id not in self.covers:
            return
        if is_waiting(entity_id):
            self.logger.debug(
                "handle_stop_service_call: ignoring stop for %s — wait_for_target active",
                entity_id,
            )
            return
        self.logger.debug(
            "Manual override via user stop_cover for %s (My position = %d%%)",
            entity_id,
            my_position_value,
        )
        self._record_event(
            entity_id,
            "manual_override_set",
            our_state=my_position_value,
            new_position=my_position_value,
            reason="user stop_cover to My position",
        )
        self.mark_manual_control(entity_id)
        self.manual_control_time.setdefault(entity_id, dt.datetime.now(dt.UTC))

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
        the proxy may dispatch before ``add_covers`` runs.

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
        Re-enables automatic position commands.

        Args:
            entity_id: Cover entity ID to reset

        """
        self.manual_control[entity_id] = False
        self.manual_control_time.pop(entity_id, None)
        self.logger.debug("Reset manual override for %s", entity_id)
        self._record_event(
            entity_id,
            "manual_override_reset",
            our_state=None,
            new_position=None,
            reason="manual override cleared",
        )

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
