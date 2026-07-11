"""Coordinator dispatch holds and labels a manual-override hold (issue #809).

Mirrors ``tests/test_motion_hold_skip.py`` but for a manual-override hold: when
the pipeline result is a MANUAL hold (skip_command=True, position=would-be,
held_position=physical), ``_dispatch_to_cover`` must suppress the command and
record the skip with a ``manual_override_hold`` label — not ``motion_hold``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
)
from custom_components.adaptive_cover_pro.pipeline.handlers import (
    ManualOverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult
from custom_components.adaptive_cover_pro.state.cover_provider import CoverProvider

from tests.test_pipeline.conftest import make_snapshot


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


# ---------------------------------------------------------------------------
# Issue #888: the assumed My value feeds the #809 hold on open/close-only covers
# ---------------------------------------------------------------------------


def _cmd_svc_with_assumed(hass) -> CoverCommandService:
    return CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=MagicMock(),
        open_close_threshold=50,
        check_interval_minutes=1,
        position_tolerance=3,
        max_retries=3,
    )


@pytest.mark.unit
def test_hold_at_assumed_my_on_open_close_cover():
    """An open/close-only cover under override holds at its assumed My value.

    The assumed My position flows through ``read_positions`` into the snapshot's
    ``current_cover_position``, so ``ManualOverrideHandler`` holds there
    (skip_command=True) instead of surfacing ``—``. A position-reporting cover
    is unaffected: its real read always wins over any assumed value.
    """
    hass = MagicMock()
    unknown = MagicMock()
    unknown.state = "unknown"
    unknown.attributes = {}
    hass.states.get.return_value = unknown

    cmd_svc = _cmd_svc_with_assumed(hass)
    cmd_svc.record_assumed_position("cover.somfy", 50)
    provider = CoverProvider(hass, MagicMock())
    policy = get_policy("cover_blind")

    open_close_only = {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }
    with patch(
        "custom_components.adaptive_cover_pro.state.cover_provider.check_cover_features",
        return_value=open_close_only,
    ):
        positions = provider.read_positions(
            ["cover.somfy"], policy, assumed=cmd_svc.get_assumed_position
        )
    assert positions["cover.somfy"] == 50

    snap = make_snapshot(
        manual_override_active=True,
        direct_sun_valid=False,
        current_cover_position=positions["cover.somfy"],
        default_position=0,
    )
    result = ManualOverrideHandler().evaluate(snap)
    assert result is not None
    assert result.held_position == 50
    assert result.skip_command is True

    # Position-reporting cover: a real read wins over any assumed value.
    reporting = MagicMock()
    reporting.state = "open"
    reporting.attributes = {"current_position": 30}
    hass.states.get.return_value = reporting
    position_capable = {
        "has_set_position": True,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }
    with patch(
        "custom_components.adaptive_cover_pro.state.cover_provider.check_cover_features",
        return_value=position_capable,
    ):
        positions2 = provider.read_positions(
            ["cover.somfy"], policy, assumed=lambda _e: 50
        )
    assert positions2["cover.somfy"] == 30
