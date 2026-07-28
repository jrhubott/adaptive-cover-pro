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

    from custom_components.adaptive_cover_pro.const import CONF_INVERSE_STATE

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._inverse_state = False
    # The real coordinator always has a config entry; ``position_axis_inverted``
    # derives from its options, so give the fixture the same input rather than
    # stubbing the property out.
    coord.config_entry = MagicMock()
    coord.config_entry.options = {CONF_INVERSE_STATE: False}

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


# ---------------------------------------------------------------------------
# The published hold extra must not change frame with the winning handler
# (issue #1028)
# ---------------------------------------------------------------------------


def _coordinator_for_hold(result: PipelineResult, *, inverse: bool):
    """Coordinator whose real ``position_axis_inverted`` derives from options."""
    from custom_components.adaptive_cover_pro.const import CONF_INVERSE_STATE
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._inverse_state = inverse
    coord.config_entry = MagicMock()
    coord.config_entry.options = {CONF_INVERSE_STATE: inverse}
    coord._policy = get_policy("cover_blind")
    coord._pipeline_result = result
    cmd_svc = MagicMock()
    cmd_svc.apply_position = AsyncMock(return_value=("sent", None))
    cmd_svc.record_skipped_action = MagicMock()
    coord._cmd_svc = cmd_svc
    return coord


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "result"),
    [
        (
            "motion_hold",
            # A motion hold carries the physical place in ``position``, in the
            # LOGICAL frame — a cover sitting at cover-frame 30 reads 70 here.
            PipelineResult(
                position=70,
                control_method=ControlMethod.MOTION,
                reason="occupancy timeout — holding position",
                skip_command=True,
            ),
        ),
        (
            "manual_override_hold",
            # A manual hold puts the raw cover-frame read in ``held_position``
            # and keeps ``position`` as the default it merely shadows.
            PipelineResult(
                position=55,
                control_method=ControlMethod.MANUAL,
                reason="manual override hold",
                skip_command=True,
                held_position=30,
            ),
        ),
    ],
)
async def test_skipped_hold_publishes_held_position_in_one_frame(label, result):
    """``held_position`` is the physical cover-frame reading for EVERY hold type.

    It ships beside ``would_be_position`` (the coordinator's cover-frame
    ``state``) and an ``inverse_state_applied`` label, so the cover frame is
    the frame this surface speaks. What must never happen is what #1028 exists
    to remove: the same published field meaning cover-frame when a manual hold
    wins and logical when a motion hold wins.
    """
    coord = _coordinator_for_hold(result, inverse=True)

    await coord._dispatch_to_cover("cover.test", 30, "solar", MagicMock())

    args, kwargs = coord._cmd_svc.record_skipped_action.call_args
    assert args[1] == label
    assert kwargs["extras"]["held_position"] == 30


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skipped_motion_hold_held_position_untouched_without_inversion():
    """Without inversion the logical and cover frames coincide — no conversion."""
    result = PipelineResult(
        position=30,
        control_method=ControlMethod.MOTION,
        reason="occupancy timeout — holding position",
        skip_command=True,
    )
    coord = _coordinator_for_hold(result, inverse=False)

    await coord._dispatch_to_cover("cover.test", 30, "solar", MagicMock())

    _args, kwargs = coord._cmd_svc.record_skipped_action.call_args
    assert kwargs["extras"]["held_position"] == 30


@pytest.mark.unit
def test_hold_position_trace_step_stays_on_the_linear_position_frame():
    """The motion-hold decision-trace step is LOGICAL, like every other step.

    Deliberate call (#1028): ``DecisionStep.position`` is each handler's
    proposed position, and every other handler publishes that in the logical
    frame. Pinning the motion hold to the same frame keeps the trace step equal
    to ``linear_position`` — the field the companion card reads — instead of
    reintroducing "frame depends on which handler won" inside the trace. The
    cover-frame view of the same place stays reachable as ``would_be_position``
    and ``held_position`` on ``last_skipped_action``.
    """
    from types import SimpleNamespace

    from custom_components.adaptive_cover_pro.diagnostics.builder import (
        DiagnosticsBuilder,
    )
    from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
    from tests.test_pipeline.conftest import make_snapshot

    snap = make_snapshot(
        motion_control_enabled=True,
        motion_timeout_active=True,
        motion_timeout_mode="hold_position",
        in_time_window=True,
        direct_sun_valid=True,
        current_cover_position=30,
        position_axis_inverted=True,
    )
    result = PipelineRegistry([MotionTimeoutHandler()]).evaluate(snap)

    step = next(s for s in result.decision_trace if s.matched)
    assert step.position == result.position == 70
    assert (
        DiagnosticsBuilder._logical_position(
            SimpleNamespace(pipeline_result=result, position_axis_inverted=True)
        )
        == step.position
    )
