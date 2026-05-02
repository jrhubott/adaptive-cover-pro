"""Tests for manual override detection during active sun tracking.

Issue #147: When the sun tracking sends a command (wait_for_target=True), a
user manually moving the cover after the grace period expires is not detected
as a manual override.

Root cause: process_entity_state_change() left wait_for_target=True when the
grace period expired but the cover hadn't reached the target.  This caused
handle_state_change() to skip manual override detection (it guards on
wait_target_call).

Fix: clear wait_for_target when the grace period has expired and the cover is
not at the commanded target — allowing handle_state_change() to evaluate the
position change normally.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state_change_data(entity_id: str, new_position: int):
    """Build a mock StateChangedData with a cover reporting *new_position*."""
    event = MagicMock()
    event.entity_id = entity_id
    event.new_state = MagicMock()
    event.new_state.state = "stopped"
    event.new_state.attributes = {"current_position": new_position}
    event.new_state.last_updated = dt.datetime.now(dt.UTC)
    return event


def _make_coordinator(
    entity_id: str,
    target_position: int,
    current_position: int,
    *,
    grace_expired: bool = True,
    ignore_intermediate: bool = False,
):
    """Build a minimal coordinator mock suitable for process_entity_state_change.

    Args:
        entity_id: The cover entity being tracked.
        target_position: The position the integration commanded.
        current_position: The position the cover is now reporting.
        grace_expired: Whether the command grace period has expired.
        ignore_intermediate: Whether to ignore opening/closing states.

    """
    from custom_components.adaptive_cover_pro.managers.cover_command import (
        CoverCommandService,
    )
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    coordinator = MagicMock()
    coordinator.state_change_data = _make_state_change_data(entity_id, current_position)
    coordinator.ignore_intermediate_states = ignore_intermediate
    coordinator._target_just_reached = set()

    # GracePeriodManager — expired means no timestamp recorded
    grace_mgr = GracePeriodManager(logger=MagicMock(), command_grace_seconds=5.0)
    if not grace_expired:
        # Simulate an active (fresh) grace period
        grace_mgr._command_timestamps[entity_id] = dt.datetime.now().timestamp()
    coordinator._grace_mgr = grace_mgr

    # CoverCommandService — has a target and wait_for_target=True
    cmd_svc = CoverCommandService(
        hass=MagicMock(),
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=grace_mgr,
        position_tolerance=5,
    )
    cmd_svc.set_target(entity_id, target_position)
    cmd_svc.set_waiting(entity_id, True)

    cmd_svc.get_cover_capabilities = lambda eid: {"has_set_position": True}
    cmd_svc.read_position_with_capabilities = (
        lambda eid, caps, state_obj: current_position
    )
    coordinator._cmd_svc = cmd_svc

    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator._is_in_grace_period = lambda eid: (
        AdaptiveDataUpdateCoordinator._is_in_grace_period(coordinator, eid)
    )

    return coordinator


# ---------------------------------------------------------------------------
# Tests for process_entity_state_change
# ---------------------------------------------------------------------------


class TestProcessEntityStateChange:
    """process_entity_state_change must clear wait_for_target when the grace
    period expires but the cover is NOT at the commanded target.
    """

    def _call(self, coordinator):
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        AdaptiveDataUpdateCoordinator.process_entity_state_change(coordinator)

    # ------------------------------------------------------------------
    # Behaviour that must NOT change (regression guards)
    # ------------------------------------------------------------------

    def test_grace_period_active_leaves_wait_for_target_true(self) -> None:
        """While in grace period, wait_for_target must remain True."""
        entity_id = "cover.test"
        coord = _make_coordinator(
            entity_id,
            target_position=30,
            current_position=50,
            grace_expired=False,
        )
        self._call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is True

    def test_target_reached_marks_target_just_reached(self) -> None:
        """When cover arrives at the target, entity is added to _target_just_reached."""
        entity_id = "cover.test"
        coord = _make_coordinator(
            entity_id,
            target_position=30,
            current_position=32,  # within tolerance
            grace_expired=True,
        )
        self._call(coord)
        assert entity_id in coord._target_just_reached
        # wait_for_target cleared by check_target_reached
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is False

    def test_intermediate_state_ignored_when_flag_set(self) -> None:
        """'opening'/'closing' states are skipped when ignore_intermediate_states=True."""
        entity_id = "cover.test"
        coord = _make_coordinator(
            entity_id,
            target_position=30,
            current_position=50,
            grace_expired=True,
        )
        coord.state_change_data.new_state.state = "opening"
        coord.ignore_intermediate_states = True
        self._call(coord)
        # Nothing should have changed
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is True
        assert entity_id not in coord._target_just_reached

    # ------------------------------------------------------------------
    # The fix: clearing wait_for_target on target-not-reached
    # ------------------------------------------------------------------

    def test_grace_expired_target_not_reached_clears_wait_for_target(self) -> None:
        """Grace period expired and cover is NOT at target → wait_for_target cleared.

        This is the core fix for Issue #147.  The cover diverged from the
        commanded target (user manual move), so we must clear wait_for_target
        to allow handle_state_change() to detect the manual override.
        """
        entity_id = "cover.test"
        coord = _make_coordinator(
            entity_id,
            target_position=30,
            current_position=95,  # user moved to 95%
            grace_expired=True,
        )
        self._call(coord)

        assert (
            coord._cmd_svc.is_waiting_for_target(entity_id) is False
        ), "wait_for_target must be cleared when grace expires and cover is not at target"

    def test_grace_expired_target_not_reached_does_not_add_to_target_just_reached(
        self,
    ) -> None:
        """When target is not reached, entity must NOT be added to _target_just_reached."""
        entity_id = "cover.test"
        coord = _make_coordinator(
            entity_id,
            target_position=30,
            current_position=95,
            grace_expired=True,
        )
        self._call(coord)
        assert entity_id not in coord._target_just_reached


# ---------------------------------------------------------------------------
# Integration: after fix, manual override detection works
# ---------------------------------------------------------------------------


class TestManualOverrideAfterGracePeriod:
    """With the fix, handle_state_change can detect manual overrides after
    the grace period expires — even when wait_for_target was originally True.
    """

    def test_manual_move_detected_after_grace_period(self) -> None:
        """User moving cover significantly away from target must set manual control.

        Scenario:
        1. Sun tracking sends command to 30% → wait_for_target=True
        2. Grace period (5 s) expires
        3. Cover is at 95% (user moved it via wall switch)
        4. After process_entity_state_change clears wait_for_target,
           handle_state_change must flag the entity as manually controlled.
        """
        from custom_components.adaptive_cover_pro.managers.manual_override import (
            AdaptiveCoverManager,
        )

        entity_id = "cover.living_room"
        target = 30
        user_position = 95

        # Real manager to detect the override
        manager = AdaptiveCoverManager(
            hass=MagicMock(),
            reset_duration={"hours": 1},
            logger=MagicMock(),
        )
        manager.add_covers([entity_id])

        event = _make_state_change_data(entity_id, user_position)

        # After the fix, wait_for_target is cleared; handle_state_change sees False
        manager.handle_state_change(
            states_data=event,
            our_state=target,
            blind_type="cover_blind",
            allow_reset=True,
            is_waiting=lambda _eid: False,
            manual_threshold=5,
        )

        assert manager.is_cover_manual(
            entity_id
        ), "Manual override must be detected when cover is far from commanded target"

    def test_small_delta_not_detected_as_manual(self) -> None:
        """Position change smaller than threshold is not a manual override."""
        from custom_components.adaptive_cover_pro.managers.manual_override import (
            AdaptiveCoverManager,
        )

        entity_id = "cover.bedroom"
        target = 30
        motor_rounding = 32  # 2% difference — below threshold

        manager = AdaptiveCoverManager(
            hass=MagicMock(),
            reset_duration={"hours": 1},
            logger=MagicMock(),
        )
        manager.add_covers([entity_id])

        event = _make_state_change_data(entity_id, motor_rounding)

        manager.handle_state_change(
            states_data=event,
            our_state=target,
            blind_type="cover_blind",
            allow_reset=True,
            is_waiting=lambda _eid: False,
            manual_threshold=5,
        )

        assert not manager.is_cover_manual(
            entity_id
        ), "Small motor-rounding differences must not trigger manual override"

    def test_move_during_grace_period_not_detected(self) -> None:
        """Position changes during the grace period must never trigger manual detection.

        Even if the position diverges significantly, we cannot trust the reading
        while the motor is still responding to our command.
        """
        from custom_components.adaptive_cover_pro.managers.manual_override import (
            AdaptiveCoverManager,
        )

        entity_id = "cover.kitchen"
        target = 30
        user_position = 95

        manager = AdaptiveCoverManager(
            hass=MagicMock(),
            reset_duration={"hours": 1},
            logger=MagicMock(),
        )
        manager.add_covers([entity_id])

        event = _make_state_change_data(entity_id, user_position)
        # wait_for_target still True (grace period active — coordinator bails early,
        # so handle_state_change is never reached)
        manager.handle_state_change(
            states_data=event,
            our_state=target,
            blind_type="cover_blind",
            allow_reset=True,
            is_waiting=lambda _eid: True,
            manual_threshold=5,
        )

        # Guard still in handle_state_change for any call paths that bypass
        # the coordinator's early-return
        assert not manager.is_cover_manual(entity_id)
