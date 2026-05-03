"""Manual override management for Adaptive Cover Pro."""

from __future__ import annotations

import datetime as dt

from homeassistant.core import HomeAssistant

from ..const import DEFAULT_DEBUG_EVENT_BUFFER_SIZE, POSITION_TOLERANCE_PERCENT
from ..diagnostics.event_buffer import EventBuffer
from ..helpers import check_cover_features, get_open_close_state, should_use_tilt


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
        blind_type,
        allow_reset,
        is_waiting,
        manual_threshold,
    ):
        """Process state change for manual override.

        Examines cover position changes to detect manual overrides by comparing
        new position to expected position. Ignores changes during grace periods
        (wait_for_target) and below threshold. Marks cover as manual and records
        timestamp when manual change detected.

        Args:
            states_data: StateChangedData with entity_id, old_state, new_state
            our_state: Expected position from coordinator calculation
            blind_type: Cover type (cover_blind, cover_awning, cover_tilt)
            allow_reset: If True, updates timestamp on subsequent changes
            is_waiting: Callable(entity_id) -> bool indicating whether the cover
                is currently expected to be moving toward a commanded target.
            manual_threshold: Minimum position delta to trigger manual detection

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

        caps = check_cover_features(self.hass, entity_id)
        if should_use_tilt(
            blind_type == "cover_tilt", caps if caps is not None else {}
        ):
            new_position = new_state.attributes.get("current_tilt_position")
        else:
            new_position = new_state.attributes.get("current_position")

        # If position is None, try mapping from open/close state
        if new_position is None:
            new_position = get_open_close_state(self.hass, entity_id)

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

        if new_position != our_state:
            # Use the larger of the user-configured threshold and the position
            # tolerance constant as the minimum detectable change.  This prevents
            # motor rounding and position-reporting imprecision (up to
            # POSITION_TOLERANCE_PERCENT) from triggering false manual overrides
            # even when the user has not configured an explicit threshold.
            effective_threshold = max(
                manual_threshold if manual_threshold is not None else 0,
                POSITION_TOLERANCE_PERCENT,
            )
            if abs(our_state - new_position) < effective_threshold:
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
