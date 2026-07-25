"""Tests for coordinator skip path when pipeline result has skip_command=True."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.motion_timeout import (
    MotionTimeoutHandler,
)
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult


def _make_coordinator_with_skip_command(*, skip_command: bool, position: int = 42):
    """Build a minimal coordinator whose _pipeline_result has skip_command set."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._inverse_state = False

    coord._pipeline_result = PipelineResult(
        position=position,
        control_method=ControlMethod.MOTION,
        reason="occupancy timeout — holding position 42% (sun in FOV)",
        skip_command=skip_command,
    )

    cmd_svc = MagicMock()
    cmd_svc.apply_position = AsyncMock(return_value=("sent", None))
    cmd_svc.record_skipped_action = MagicMock()
    coord._cmd_svc = cmd_svc

    # The real coordinator always carries a policy (set in __init__); the
    # per-entity dispatch seam delegates to it. A blind policy is identity.
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    coord._policy = get_policy("cover_blind")

    return coord


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_to_cover_calls_record_skipped_when_skip_command_true():
    """_dispatch_to_cover records motion_hold skip and does not call apply_position."""
    coord = _make_coordinator_with_skip_command(skip_command=True, position=42)
    ctx = MagicMock()

    await coord._dispatch_to_cover("cover.test", 42, "solar", ctx)

    coord._cmd_svc.apply_position.assert_not_called()
    coord._cmd_svc.record_skipped_action.assert_called_once()
    args, kwargs = coord._cmd_svc.record_skipped_action.call_args
    assert args[1] == "motion_hold"
    extras = kwargs.get("extras", {})
    assert "held_position" in extras


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_to_cover_calls_apply_position_when_skip_command_false():
    """_dispatch_to_cover calls apply_position normally when skip_command is False."""
    coord = _make_coordinator_with_skip_command(skip_command=False, position=42)
    ctx = MagicMock()

    await coord._dispatch_to_cover("cover.test", 42, "solar", ctx)

    coord._cmd_svc.apply_position.assert_called_once_with(
        "cover.test", 42, "solar", context=ctx
    )
    coord._cmd_svc.record_skipped_action.assert_not_called()


# ---------------------------------------------------------------------------
# hold_position emits a LOGICAL target (issue #1028)
# ---------------------------------------------------------------------------


def _hold_result(*, current: int, inverted: bool) -> PipelineResult:
    """Run MotionTimeoutHandler in hold_position mode over a held read."""
    from tests.test_pipeline.conftest import make_snapshot  # noqa: PLC0415

    snap = make_snapshot(
        motion_control_enabled=True,
        motion_timeout_active=True,
        motion_timeout_mode="hold_position",
        in_time_window=True,
        direct_sun_valid=True,
        current_cover_position=current,
        position_axis_inverted=inverted,
    )
    result = MotionTimeoutHandler().evaluate(snap)
    assert result is not None
    return result


@pytest.mark.unit
def test_hold_position_converts_cover_read_to_logical_when_inverted():
    """``current_cover_position`` is a raw cover read; the result must be logical.

    ``coordinator.state`` inverts a non-bypass winner's position, so a raw
    cover-frame read placed in ``PipelineResult.position`` would be inverted a
    second time and publish a target that contradicts the cover (#1028).
    """
    result = _hold_result(current=30, inverted=True)
    assert result.position == 70
    # The raw read stays available for the reason payload / diagnostics.
    assert result.reason_payload.params["held"] == 30


@pytest.mark.unit
def test_hold_position_passes_through_when_not_inverted():
    """Without inversion the held read is already logical — no conversion."""
    result = _hold_result(current=30, inverted=False)
    assert result.position == 30
    assert result.reason_payload.params["held"] == 30


@pytest.mark.unit
def test_hold_position_does_not_set_bypass_auto_control():
    """``bypass_auto_control`` also means "apply when auto control is OFF".

    A motion hold must not acquire that semantic just to dodge the inversion —
    the frame conversion is the correct lever (#1028).
    """
    assert _hold_result(current=30, inverted=True).bypass_auto_control is False
