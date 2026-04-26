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
    coord.target_call = {"cover.a": 100}
    coord.wait_for_target = {"cover.a": True}  # latched by prior unwanted command
    coord._cover_type = "cover_awning"
    coord.manual_reset = False
    coord.manual_threshold = 5
    coord.logger = MagicMock()
    coord.cover_state_change = True
    coord._is_in_startup_grace_period = MagicMock(return_value=False)
    coord._target_just_reached = set()
    coord._cmd_svc = MagicMock()
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
    and reconciliation could resurrect it.
    """
    coord = _make_coord_auto_off()
    coord.manager = MagicMock()
    # is_cover_manual returns False (was_manual) before handle_state_change,
    # then True (became manual) after — simulate via side_effect.
    coord.manager.is_cover_manual.side_effect = [False, True]
    coord._pending_cover_events = [_event("cover.a", 30)]

    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coord, 50)

    coord._cmd_svc.discard_target.assert_called_once_with("cover.a")


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
