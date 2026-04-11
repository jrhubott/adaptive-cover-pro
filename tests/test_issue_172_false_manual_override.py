"""Tests for issue #172: integration-initiated moves trigger false manual override.

Root causes addressed:
1. process_entity_state_change() clears wait_for_target unconditionally when the
   grace period expires, even if the cover is still in transit toward the target.
   Fix: transit-aware detection using cover state ("opening"/"closing") and
   direction comparison, with a 45-second hard backstop (TRANSIT_TIMEOUT_SECONDS).

2. Custom position sensors not tracked as entity state changes.
   Fix: added CONF_CUSTOM_POSITION_SENSOR_1-4 to tracked entities in __init__.py.

3. state_change_data single-variable shared by all covers.
   Fix: _pending_cover_events list processed and drained per refresh cycle.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state_change_data(
    entity_id: str,
    new_position: int,
    old_position: int = 0,
    new_state_str: str = "opening",
):
    """Build a mock StateChangedData for a cover in transit."""
    event = MagicMock()
    event.entity_id = entity_id
    event.new_state = MagicMock()
    event.new_state.state = new_state_str
    event.new_state.attributes = {"current_position": new_position}
    event.new_state.last_updated = dt.datetime.now(dt.UTC)
    event.old_state = MagicMock()
    event.old_state.state = new_state_str
    event.old_state.attributes = {"current_position": old_position}
    return event


def _make_coordinator(
    entity_id: str,
    target_position: int,
    current_position: int,
    old_position: int = 0,
    *,
    grace_expired: bool = True,
    ignore_intermediate: bool = False,
    new_state_str: str = "opening",
    sent_seconds_ago: float = 10.0,
):
    """Build a minimal coordinator mock for process_entity_state_change tests.

    Args:
        entity_id: The cover entity being tracked.
        target_position: The position the integration commanded.
        current_position: The position the cover is now reporting.
        old_position: The position the cover had before this event.
        grace_expired: Whether the command grace period has expired.
        ignore_intermediate: Whether to ignore opening/closing states.
        new_state_str: The HA state string for the cover ("opening", "closing", "stopped", etc.)
        sent_seconds_ago: How many seconds ago the command was sent (for transit timeout).

    """
    from custom_components.adaptive_cover_pro.managers.cover_command import (
        CoverCommandService,
    )
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    coordinator = MagicMock()
    coordinator.state_change_data = _make_state_change_data(
        entity_id, current_position, old_position, new_state_str
    )
    coordinator.ignore_intermediate_states = ignore_intermediate
    coordinator._target_just_reached = set()

    grace_mgr = GracePeriodManager(logger=MagicMock(), command_grace_seconds=5.0)
    if not grace_expired:
        grace_mgr._command_timestamps[entity_id] = dt.datetime.now().timestamp()
    coordinator._grace_mgr = grace_mgr

    cmd_svc = MagicMock(spec=CoverCommandService)
    cmd_svc.wait_for_target = {entity_id: True}
    cmd_svc.target_call = {entity_id: target_position}
    cmd_svc._position_tolerance = 5

    # Record when the command was "sent"
    cmd_svc._sent_at = {
        entity_id: dt.datetime.now(dt.UTC) - dt.timedelta(seconds=sent_seconds_ago)
    }

    def _check_target_reached(eid, pos):
        if pos is None:
            return False
        if abs(pos - cmd_svc.target_call.get(eid, -999)) <= cmd_svc._position_tolerance:
            cmd_svc.wait_for_target[eid] = False
            return True
        return False

    cmd_svc.check_target_reached = MagicMock(side_effect=_check_target_reached)
    cmd_svc.get_cover_capabilities = MagicMock(return_value={"has_set_position": True})

    # read_position_with_capabilities returns current_position for new_state and
    # old_position for old_state (needed for transit direction checks).
    def _read_position(eid, caps, state_obj):
        if state_obj is coordinator.state_change_data.new_state:
            return current_position
        if state_obj is coordinator.state_change_data.old_state:
            return old_position
        return current_position

    cmd_svc.read_position_with_capabilities = MagicMock(side_effect=_read_position)
    coordinator._cmd_svc = cmd_svc

    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator._is_in_grace_period = lambda eid: (
        AdaptiveDataUpdateCoordinator._is_in_grace_period(coordinator, eid)
    )
    coordinator._start_grace_period = lambda eid: (
        AdaptiveDataUpdateCoordinator._start_grace_period(coordinator, eid)
    )

    return coordinator


def _call(coordinator):
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    AdaptiveDataUpdateCoordinator.process_entity_state_change(coordinator)


# ===========================================================================
# Transit detection: cover moving toward target keeps wait_for_target=True
# ===========================================================================


class TestTransitDetection:
    """process_entity_state_change must keep wait_for_target=True when the cover
    is actively moving toward its commanded target after grace expiry.
    """

    def test_cover_opening_toward_target_keeps_wait_for_target(self) -> None:
        """Cover moving toward target (opening) must not clear wait_for_target.

        Scenario: command sent to 75%, grace expired, cover is at 45% (was 35%).
        Distance to target decreased from 40 to 30 — still in transit.
        """
        entity_id = "cover.blind"
        coord = _make_coordinator(
            entity_id,
            target_position=75,
            current_position=45,
            old_position=35,
            new_state_str="opening",
        )
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is True

    def test_cover_closing_toward_target_keeps_wait_for_target(self) -> None:
        """Cover closing toward a lower target must not clear wait_for_target."""
        entity_id = "cover.awning"
        coord = _make_coordinator(
            entity_id,
            target_position=20,
            current_position=55,
            old_position=65,
            new_state_str="closing",
        )
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is True

    def test_full_slow_cover_sequence_no_false_override(self) -> None:
        """Repeated position reports during a slow transit must never clear wait_for_target.

        Simulates a cover moving from 30% → 75% in 10% increments, each reported
        after the grace period has expired.  All positions stay outside the 5%
        position tolerance (i.e., below 70), so check_target_reached returns False
        and the transit detection is what keeps wait_for_target=True.
        """
        entity_id = "cover.slow_blind"
        # All new positions are below 70 (target=75, tolerance=5 → threshold at 70).
        # At position 65 the cover is still in transit, not yet at target.
        positions = [(30, 40), (40, 50), (50, 60), (60, 65)]
        for old_pos, new_pos in positions:
            coord = _make_coordinator(
                entity_id,
                target_position=75,
                current_position=new_pos,
                old_position=old_pos,
                new_state_str="opening",
            )
            _call(coord)
            assert coord._cmd_svc.wait_for_target[entity_id] is True, (
                f"wait_for_target must stay True at position {new_pos} (was {old_pos})"
            )


# ===========================================================================
# Hardware startup delay (Issue #147 v2 regression guard)
# ===========================================================================


class TestHardwareStartupDelay:
    """Some covers have a hardware startup delay of 10–30+ seconds between
    receiving a command and physically beginning to move.  The first state-change
    event fires while the cover is still at its pre-command position
    (old_pos == new_pos == starting_pos).  Previously the strict ``new_dist < old_dist``
    check treated equal distances as "not moving" and cleared wait_for_target,
    triggering a false manual override on the very next automatic movement.

    The fix: when old_state was not transitional ("closed"/"open") and new_state
    just became transitional ("opening"), equal distances mean "motor just engaged
    but hasn't moved yet" — keep wait_for_target=True.
    """

    def test_first_event_startup_delay_keeps_wait_for_target(self) -> None:
        """First state event after a slow cover starts must not clear wait_for_target.

        Scenario (user's real bug): command sent to 100%, cover at 0%.
        24 seconds later the first event fires: state closed→opening, pos=0→0.
        old_distance == new_distance (both 100), but old_state was "closed"
        (non-transitional) so this is a startup delay, NOT a stall.
        """
        entity_id = "cover.slow_blind"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=0,
            old_position=0,
            new_state_str="opening",
            sent_seconds_ago=24.0,
        )
        # Override old_state to "closed" (non-transitional) to simulate state transition
        coord.state_change_data.old_state.state = "closed"
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is True

    def test_subsequent_startup_delay_event_still_opening(self) -> None:
        """Second state event (opening→opening, pos still 0) is a stall — must clear.

        If the cover was ALREADY "opening" and position still hasn't changed,
        that's a genuine stall — clear wait_for_target so the system can retry.
        """
        entity_id = "cover.slow_blind"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=0,
            old_position=0,
            new_state_str="opening",
            sent_seconds_ago=30.0,
        )
        # old_state already "opening" (was already transitional) → stall → clear
        coord.state_change_data.old_state.state = "opening"
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is False

    def test_full_scenario_cancel_manual_then_auto_move(self) -> None:
        """Full scenario: manual done, reset, integration commands cover, startup delay.

        After the reset button is pressed the integration sends a command.
        The cover has a ~24s startup delay before moving.  The first event
        (closed→opening, pos unchanged) must NOT be treated as a false override.
        """
        entity_id = "cover.blind"
        coord = _make_coordinator(
            entity_id,
            target_position=75,
            current_position=30,
            old_position=30,
            new_state_str="opening",
            sent_seconds_ago=20.0,
        )
        # Simulate startup: old state was "closed" before command started cover
        coord.state_change_data.old_state.state = "closed"
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is True

        # After cover actually starts moving (pos changes), transit detection works
        coord2 = _make_coordinator(
            entity_id,
            target_position=75,
            current_position=40,  # now moving
            old_position=30,
            new_state_str="opening",
            sent_seconds_ago=25.0,
        )
        coord2.state_change_data.old_state.state = "opening"
        _call(coord2)
        assert coord2._cmd_svc.wait_for_target[entity_id] is True  # still in transit


# ===========================================================================
# Stop detection: cover stops before reaching target
# ===========================================================================


class TestStopDetection:
    """When a cover transitions to a final state (stopped/open/closed), the
    wait_for_target flag must be cleared so manual override detection can run.
    """

    def test_cover_stopped_mid_transit_clears_wait_for_target(self) -> None:
        """Cover stopping before reaching target must clear wait_for_target immediately.

        Scenario: command sent to 75%, cover stopped at 50% (user stopped it).
        State is "stopped" — non-transitional — so wait_for_target is cleared.
        """
        entity_id = "cover.blind"
        coord = _make_coordinator(
            entity_id,
            target_position=75,
            current_position=50,
            old_position=48,
            new_state_str="stopped",
        )
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is False

    def test_cover_open_state_not_at_target_clears_wait_for_target(self) -> None:
        """Cover reporting 'open' at a non-target position clears wait when old state was also open.

        If the old state was not transitional (also "open"), the cover settled
        somewhere it shouldn't — not a mid-transit pause.
        """
        entity_id = "cover.blind"
        coord = _make_coordinator(
            entity_id,
            target_position=75,
            current_position=100,
            old_position=95,
            new_state_str="open",
        )
        # old_state defaults to same as new_state_str ("open") — not transitional
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is False

    def test_cover_closed_state_not_at_target_clears_wait_for_target(self) -> None:
        """Cover reporting 'closed' state at a position different from target clears wait."""
        entity_id = "cover.blind"
        coord = _make_coordinator(
            entity_id,
            target_position=50,
            current_position=0,
            old_position=5,
            new_state_str="closed",
        )
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is False


# ===========================================================================
# Issue #186: step-motor shades pause mid-transit (opening → open → opening)
# ===========================================================================


class TestStepMotorIntermediatePause:
    """Step-motor shades briefly report 'open' at an intermediate position between
    motor pulses.  This must NOT trigger a false manual override (Issue #186).

    The cover transitions opening→open at a position that is not the target.
    The fix: restart the grace period so the next opening burst is still protected.
    """

    @pytest.mark.asyncio
    async def test_opening_to_open_mid_transit_restarts_grace_period(self) -> None:
        """Cover pausing mid-transit (opening→open, not at target) keeps wait_for_target.

        Scenario: commanded to 100%, grace expired, cover was opening at 46%,
        now briefly settles to open at 51%.  Should restart grace period.
        """
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=51,
            old_position=46,
            new_state_str="open",
        )
        # Simulate the cover was in "opening" state before this event
        coord.state_change_data.old_state.state = "opening"
        _call(coord)
        # wait_for_target must remain True (grace period restarted)
        assert coord._cmd_svc.wait_for_target[entity_id] is True
        # Grace period must have been restarted
        assert coord._grace_mgr.is_in_command_grace_period(entity_id)

    @pytest.mark.asyncio
    async def test_closing_to_open_mid_transit_restarts_grace_period(self) -> None:
        """Closing cover pausing mid-transit (closing→open) also restarts grace period."""
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=20,
            current_position=40,
            old_position=50,
            new_state_str="open",
        )
        coord.state_change_data.old_state.state = "closing"
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is True
        assert coord._grace_mgr.is_in_command_grace_period(entity_id)

    def test_stall_opening_to_opening_unchanged_position_clears_wait_for_target(
        self,
    ) -> None:
        """A genuine stall (opening→opening, position unchanged) must still clear wait_for_target.

        This is the case where the cover reports opening twice at the same position —
        a hardware stall, not a motor-pulse pause.
        """
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=51,
            old_position=51,
            new_state_str="opening",
            sent_seconds_ago=15.0,
        )
        coord.state_change_data.old_state.state = "opening"
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is False

    def test_open_at_target_does_not_restart_grace_period(self) -> None:
        """Cover reaching target position must not restart grace period (normal arrival)."""
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=100,
            old_position=95,
            new_state_str="open",
        )
        coord.state_change_data.old_state.state = "opening"
        _call(coord)
        # Target reached — wait_for_target cleared by check_target_reached
        assert coord._cmd_svc.wait_for_target[entity_id] is False

    def test_open_from_open_not_at_target_clears_wait_for_target(self) -> None:
        """Cover reporting open→open (not opening→open) at non-target clears wait_for_target.

        Old state was also 'open' (not transitional), so this is not a mid-transit
        pause — it's a genuine stop.
        """
        entity_id = "cover.patio_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=51,
            old_position=51,
            new_state_str="open",
        )
        # old_state is "open" (not transitional) — not a step-motor pause
        coord.state_change_data.old_state.state = "open"
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is False


# ===========================================================================
# Issue #147 regression: user manually moves cover away from target
# ===========================================================================


class TestManualMoveDetection:
    """User-initiated moves away from the commanded target must still be
    detected after grace period expiry (Issue #147 regression guard).
    """

    def test_cover_moving_away_from_target_clears_wait_for_target(self) -> None:
        """Cover moving AWAY from target (user moved it) must clear wait_for_target.

        Scenario: command sent to 75%, grace expired, cover moved from 45% to 35%
        (user pushed it back down). Distance increased from 30 to 40 — not in transit.
        """
        entity_id = "cover.blind"
        coord = _make_coordinator(
            entity_id,
            target_position=75,
            current_position=35,
            old_position=45,
            new_state_str="opening",
        )
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is False

    def test_cover_stalled_same_position_clears_wait_for_target(self) -> None:
        """Cover reporting the same position twice (stalled) clears wait_for_target.

        Stall means distance to target didn't decrease — reconciliation will retry.
        """
        entity_id = "cover.blind"
        coord = _make_coordinator(
            entity_id,
            target_position=75,
            current_position=45,
            old_position=45,
            new_state_str="opening",
        )
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is False

    def test_actual_manual_override_still_detected_during_transit(self) -> None:
        """If user grabs cover and moves it backward, override must still be detected.

        Scenario: command to 75%, cover was at 60%, user pushed it to 30%.
        Distance increased (45 > 15) → wait_for_target cleared → override detectable.
        """
        entity_id = "cover.blind"
        coord = _make_coordinator(
            entity_id,
            target_position=75,
            current_position=30,
            old_position=60,
            new_state_str="opening",
        )
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is False


# ===========================================================================
# Transit timeout backstop (TRANSIT_TIMEOUT_SECONDS = 45)
# ===========================================================================


class TestTransitTimeout:
    """When a cover has been "opening"/"closing" for longer than
    TRANSIT_TIMEOUT_SECONDS, wait_for_target is cleared regardless of direction.
    """

    def test_transit_timeout_clears_wait_for_target(self) -> None:
        """Command older than 45s clears wait_for_target even if cover moves toward target."""
        from custom_components.adaptive_cover_pro.const import TRANSIT_TIMEOUT_SECONDS

        entity_id = "cover.blind"
        coord = _make_coordinator(
            entity_id,
            target_position=75,
            current_position=45,
            old_position=35,  # distance decreasing — would normally keep wait_for_target
            new_state_str="opening",
            sent_seconds_ago=TRANSIT_TIMEOUT_SECONDS + 1,
        )
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is False

    def test_within_transit_window_direction_detection_applies(self) -> None:
        """Command sent 10s ago (within 45s window) uses direction detection."""
        entity_id = "cover.blind"
        coord = _make_coordinator(
            entity_id,
            target_position=75,
            current_position=45,
            old_position=35,
            new_state_str="opening",
            sent_seconds_ago=10.0,
        )
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is True

    def test_transit_timeout_constant_is_documented(self) -> None:
        """TRANSIT_TIMEOUT_SECONDS must be defined in const.py and be 45."""
        from custom_components.adaptive_cover_pro.const import TRANSIT_TIMEOUT_SECONDS

        assert TRANSIT_TIMEOUT_SECONDS == 45


# ===========================================================================
# ignore_intermediate_states=True: falls back to current behavior
# ===========================================================================


class TestIgnoreIntermediateStates:
    """When ignore_intermediate_states=True, 'opening'/'closing' events are
    filtered before process_entity_state_change() runs.  The function therefore
    only sees final states and the transit logic is never reached — preserving
    existing behavior.
    """

    def test_opening_state_ignored_when_flag_set(self) -> None:
        """'opening' events are skipped entirely when ignore_intermediate_states=True."""
        entity_id = "cover.blind"
        coord = _make_coordinator(
            entity_id,
            target_position=75,
            current_position=45,
            old_position=35,
            new_state_str="opening",
            ignore_intermediate=True,
        )
        _call(coord)
        # Early return — wait_for_target not changed
        assert coord._cmd_svc.wait_for_target[entity_id] is True

    def test_closing_state_ignored_when_flag_set(self) -> None:
        """'closing' events are skipped entirely when ignore_intermediate_states=True."""
        entity_id = "cover.blind"
        coord = _make_coordinator(
            entity_id,
            target_position=20,
            current_position=55,
            old_position=65,
            new_state_str="closing",
            ignore_intermediate=True,
        )
        _call(coord)
        assert coord._cmd_svc.wait_for_target[entity_id] is True


# ===========================================================================
# Multiple cover events processed correctly (_pending_cover_events queue)
# ===========================================================================


@pytest.mark.asyncio
async def test_multiple_covers_both_processed() -> None:
    """Both covers' events must be processed, not just the last one.

    Previously state_change_data was a single variable; rapid events from two
    covers would cause the second to overwrite the first.  With _pending_cover_events
    as a list, both are processed.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    def _event(entity_id, position):
        e = MagicMock()
        e.entity_id = entity_id
        e.new_state = MagicMock()
        e.new_state.attributes = {"current_position": position}
        return e

    coordinator = MagicMock()
    coordinator.manual_toggle = True
    coordinator.automatic_control = True
    coordinator.target_call = {"cover.a": 72, "cover.b": 50}
    coordinator.wait_for_target = {"cover.a": False, "cover.b": False}
    coordinator._cover_type = "cover_blind"
    coordinator.manual_reset = False
    coordinator.manual_threshold = 5
    coordinator.logger = MagicMock()
    coordinator.manager = MagicMock()
    coordinator.cover_state_change = True
    coordinator._is_in_startup_grace_period = MagicMock(return_value=False)
    coordinator._target_just_reached = set()
    coordinator._pending_cover_events = [
        _event(
            "cover.a", 30
        ),  # 42% away from target 72 → should trigger override check
        _event(
            "cover.b", 20
        ),  # 30% away from target 50 → should trigger override check
    ]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 50)

    # Both entities must have been passed to handle_state_change
    assert coordinator.manager.handle_state_change.call_count == 2
    called_ids = {
        call.args[0].entity_id
        for call in coordinator.manager.handle_state_change.call_args_list
    }
    assert "cover.a" in called_ids
    assert "cover.b" in called_ids


@pytest.mark.asyncio
async def test_pending_cover_events_cleared_after_processing() -> None:
    """_pending_cover_events list must be empty after async_handle_cover_state_change runs."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator.manual_toggle = False  # manual toggle off — skips the inner block
    coordinator.cover_state_change = True
    coordinator.logger = MagicMock()
    coordinator._pending_cover_events = [MagicMock(), MagicMock()]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 50)

    assert coordinator._pending_cover_events == []
