"""Issue #293 (Defect B) — state changes must register manual overrides
even when auto_control is OFF.

Today, async_handle_cover_state_change early-returns when auto_control=False.
The user's manual response to an unwanted move (e.g. issue #293's awning
closing) cannot register, leaving wait_for_target=True latched and the
diagnostics file blind to the user's intent.

The fix: gate the early-return on manual_toggle ONLY. Observation of state
changes is not the same as taking action — recording manual overrides when
auto_control is off lets reconciliation back off via the existing
_manual_override_entities check at cover_command.py:1033 and surfaces the
user's intent in diagnostics.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)


def _event(entity_id, position):
    e = MagicMock()
    e.entity_id = entity_id
    e.new_state = MagicMock()
    e.new_state.attributes = {"current_position": position}
    return e


def _make_coord_auto_off():
    """Build a minimal coordinator stub with auto_control=False, manual_toggle=True."""
    coord = MagicMock()
    coord.manual_toggle = True
    coord.automatic_control = False  # ← key: auto control is OFF
    coord.manual_ignore_external = False
    coord._cover_type = "cover_awning"
    coord.manual_reset = False
    coord.manual_threshold = 5
    coord._position_tolerance = 3
    coord.logger = MagicMock()
    coord.cover_state_change = True
    coord._is_in_startup_grace_period = MagicMock(return_value=False)
    coord._manual_gate_closed_log = MagicMock()
    coord._target_just_reached = set()
    coord._cmd_svc = MagicMock()
    coord._cmd_svc.get_target = MagicMock(return_value=100)  # latched
    coord._cmd_svc.is_waiting_for_target = MagicMock(return_value=True)
    coord._cmd_svc.enable_position_matching = True
    return coord


@pytest.mark.asyncio
async def test_state_change_observed_when_auto_control_off():
    """Manual override observation must run even when automatic_control=False."""
    coord = _make_coord_auto_off()
    coord.manager = MagicMock()
    coord.manager.is_cover_manual.return_value = False
    coord._pending_cover_events = [_event("cover.a", 30)]  # user moved cover

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coord, 50)

    # The user's manual move MUST be observed.
    assert coord.manager.handle_state_change.call_count == 1
    assert coord.manager.handle_state_change.call_args.args[0].entity_id == "cover.a"


@pytest.mark.asyncio
async def test_discard_target_called_when_observation_flips_to_manual():
    """When observation registers a manual override, latched target must be cleared.

    Without this, the unwanted force=True command's target_call would persist
    and reconciliation could resurrect it. The discard now fires from the
    manager's ``on_engaged`` edge callback (wired to ``cmd_svc.discard_target``),
    so this drives a real engine and asserts the relocated seam.
    """
    from custom_components.adaptive_cover_pro.managers.manual_override import (
        AdaptiveCoverManager,
    )

    cmd_svc = MagicMock()
    entity_id = "cover.a"
    manager = AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration={"hours": 2},
        logger=MagicMock(),
        on_engaged=cmd_svc.discard_target,
    )
    manager.add_covers([entity_id])

    # User moved the cover to 30 against the latched target of 100.
    policy = MagicMock()
    policy.read_axis_value.return_value = 30
    policy.primary_axis_suppression.return_value = False

    event = _event(entity_id, 30)
    event.old_state = MagicMock()
    event.new_state.state = "open"
    event.new_state.context = None
    event.new_state.last_updated = "2026-05-10T20:42:00+00:00"

    manager.handle_state_change(
        event,
        100,  # latched target
        policy,
        False,  # allow_reset
        lambda _e: False,  # is_waiting
        5,  # manual_threshold
        is_in_command_grace=lambda _e: False,
        is_in_transit=lambda _e: False,
    )

    assert manager.is_cover_manual(entity_id)
    cmd_svc.discard_target.assert_called_once_with(entity_id)


@pytest.mark.asyncio
async def test_manual_toggle_off_still_short_circuits():
    """Regression: when manual_toggle=False, early-return still short-circuits."""
    coord = _make_coord_auto_off()
    coord.manual_toggle = False  # globally disable manual override detection
    coord.manager = MagicMock()
    coord._pending_cover_events = [_event("cover.a", 30)]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coord, 50)

    # When manual_toggle is off, observation does not run.
    coord.manager.handle_state_change.assert_not_called()
