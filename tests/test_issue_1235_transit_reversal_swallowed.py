"""Tests for issue #1235: user reversal during transit is never detected as manual.

Root cause: the step-motor pause guard (issue #186) restarts the command grace
period whenever a cover transitions from a moving state ("opening"/"closing")
to a settled state at a position that is not the target — without checking
whether the event shows any progress TOWARD the target. A cover that a user
physically reverses (wall switch / remote) reports exactly that signature:
``opening`` → ``closed`` with zero (or negative) progress. The guard keeps
``wait_for_target`` alive, the manual-override detector never runs, ACP
re-sends the command, and the loop repeats — the override can structurally
never engage (reported with a Velux roof window via KLF-200, which publishes
no intermediate positions: every cycle is ``opening``(0) → 45 s → ``closed``(0)).

Fix: the pause guard only fires when the event itself shows progress toward
the target (``new_distance < old_distance``) — the signature both canonical
#186 scenarios (46→51 toward 100; 50→40 toward 20) share. No-progress and
moved-away events fall through to the existing direction/progress branches,
which clear ``wait_for_target`` so the manual-override detector runs on the
same event.
"""

from __future__ import annotations

import pytest

from tests.test_issue_285_open_state_cover_false_override import (
    _call,
    _make_coordinator,
)

# ===========================================================================
# Issue #1235: reversal during transit must clear wait_for_target
# ===========================================================================


class TestTransitReversalDetected:
    """A user-reversed cover must not be mistaken for a step-motor pause."""

    @pytest.mark.asyncio
    async def test_no_progress_reversal_clears_wait_for_target(self) -> None:
        """Velux/KLF-200 signature: opening(0) → closed(0), target 100.

        The cover publishes no intermediate positions. ACP commands open
        (target 100), the cover acks "opening" at position 0, the user presses
        the physical close button mid-transit, and 45 s later the cover
        reports "closed" still at position 0. Zero progress toward the target
        was ever observed — this is not a motor-pulse pause, and the event
        must fall through so the manual-override detector runs on it.
        """
        entity_id = "cover.roof_window"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=0,
            old_position=0,
            new_state_str="closed",
            sent_seconds_ago=47.0,
            transit_timeout_seconds=150,
        )
        coord.state_change_data.old_state.state = "opening"
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is False, (
            "opening→closed with zero progress toward target must clear "
            "wait_for_target so manual-override detection can run"
        )

    @pytest.mark.asyncio
    async def test_no_progress_reversal_does_not_restart_grace(self) -> None:
        """The reversal event must not restart the command grace period.

        A restarted grace period swallows the follow-up state report of the
        same reversal (observed live: "open" and "closed" arriving 4 ms apart,
        the second rejected because the first restarted grace).
        """
        entity_id = "cover.roof_window"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=0,
            old_position=0,
            new_state_str="closed",
            sent_seconds_ago=47.0,
            transit_timeout_seconds=150,
        )
        coord.state_change_data.old_state.state = "opening"
        _call(coord)
        assert not coord._grace_mgr.is_in_command_grace_period(
            entity_id
        ), "a no-progress reversal must not restart the grace period"
        coord._grace_mgr.cancel_all()

    @pytest.mark.asyncio
    async def test_moved_away_reversal_clears_wait_for_target(self) -> None:
        """opening(7) → closed(0), target 100: moved away from target.

        The cover had opened slightly (7 %) before the user reversed it back
        to fully closed. Distance to target grew (93 → 100) — unambiguously
        not ACP's own transit.
        """
        entity_id = "cover.roof_window"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=0,
            old_position=7,
            new_state_str="closed",
            sent_seconds_ago=47.0,
            transit_timeout_seconds=150,
        )
        coord.state_change_data.old_state.state = "opening"
        _call(coord)
        assert (
            coord._cmd_svc.is_waiting_for_target(entity_id) is False
        ), "opening→closed moving away from target must clear wait_for_target"

    @pytest.mark.asyncio
    async def test_closing_command_reversed_to_open_clears_wait_for_target(
        self,
    ) -> None:
        """Mirror case: ACP commands close (target 0), user reverses to open."""
        entity_id = "cover.roof_window"
        coord = _make_coordinator(
            entity_id,
            target_position=0,
            current_position=100,
            old_position=100,
            new_state_str="open",
            sent_seconds_ago=47.0,
            transit_timeout_seconds=150,
        )
        coord.state_change_data.old_state.state = "closing"
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is False, (
            "closing→open with zero progress toward target must clear "
            "wait_for_target"
        )


# ===========================================================================
# Regression guards — the #186 pause guard must keep protecting real pauses
# ===========================================================================


class TestStepMotorPauseStillProtected:
    """Canonical #186 scenarios (pause WITH progress) must keep restarting grace."""

    @pytest.mark.asyncio
    async def test_pause_with_progress_still_restarts_grace(self) -> None:
        """opening(46) → open(51), target 100: genuine motor-pulse pause."""
        entity_id = "cover.step_motor_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=51,
            old_position=46,
            new_state_str="open",
        )
        coord.state_change_data.old_state.state = "opening"
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is True
        assert coord._grace_mgr.is_in_command_grace_period(entity_id)
        coord._grace_mgr.cancel_all()

    @pytest.mark.asyncio
    async def test_pause_with_unknown_old_position_still_restarts_grace(self) -> None:
        """Old position unreadable → cannot judge direction → keep today's behaviour."""
        entity_id = "cover.step_motor_shade"
        coord = _make_coordinator(
            entity_id,
            target_position=100,
            current_position=51,
            old_position=None,
            new_state_str="open",
        )
        coord.state_change_data.old_state.state = "opening"
        _call(coord)
        assert coord._cmd_svc.is_waiting_for_target(entity_id) is True
        assert coord._grace_mgr.is_in_command_grace_period(entity_id)
        coord._grace_mgr.cancel_all()
