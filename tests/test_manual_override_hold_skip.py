"""Coordinator dispatch holds and labels a manual-override hold (issue #809).

Mirrors ``tests/test_motion_hold_skip.py`` but for a manual-override hold: when
the pipeline result is a MANUAL hold (skip_command=True, position=would-be,
held_position=physical), ``_dispatch_to_cover`` must suppress the command and
record the skip with a ``manual_override_hold`` label — not ``motion_hold``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult


def _make_coordinator_with_manual_hold(*, position: int = 100, held: int = 0):
    """Build a minimal coordinator whose _pipeline_result is a MANUAL hold."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._inverse_state = False

    coord._pipeline_result = PipelineResult(
        position=position,
        control_method=ControlMethod.MANUAL,
        reason=f"manual override active — holding {held}% "
        f"(default position would be {position}%)",
        skip_command=True,
        held_position=held,
    )

    cmd_svc = MagicMock()
    cmd_svc.apply_position = AsyncMock(return_value=("sent", None))
    cmd_svc.record_skipped_action = MagicMock()
    coord._cmd_svc = cmd_svc

    return coord


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_records_manual_override_hold_and_skips_apply():
    """A MANUAL hold does not move the cover and records manual_override_hold."""
    coord = _make_coordinator_with_manual_hold(position=100, held=0)
    ctx = MagicMock()

    await coord._dispatch_to_cover("cover.bedroom", 100, "manual_override", ctx)

    coord._cmd_svc.apply_position.assert_not_called()
    coord._cmd_svc.record_skipped_action.assert_called_once()
    args, kwargs = coord._cmd_svc.record_skipped_action.call_args
    assert args[1] == "manual_override_hold"
    extras = kwargs.get("extras", {})
    assert extras["held_position"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_manual_override_hold_does_not_mislabel_as_motion():
    """The manual-override hold must never be recorded as a motion_hold skip."""
    coord = _make_coordinator_with_manual_hold(position=100, held=0)
    ctx = MagicMock()

    await coord._dispatch_to_cover("cover.bedroom", 100, "manual_override", ctx)

    args, _ = coord._cmd_svc.record_skipped_action.call_args
    assert args[1] != "motion_hold"
