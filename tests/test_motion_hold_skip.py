"""Tests for coordinator skip path when pipeline result has skip_command=True."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.enums import ControlMethod
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
        reason="motion timeout — holding position 42% (sun in FOV)",
        skip_command=skip_command,
    )

    cmd_svc = MagicMock()
    cmd_svc.apply_position = AsyncMock(return_value=("sent", None))
    cmd_svc.record_skipped_action = MagicMock()
    coord._cmd_svc = cmd_svc

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
