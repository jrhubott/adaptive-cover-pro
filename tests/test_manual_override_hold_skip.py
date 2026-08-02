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
from custom_components.adaptive_cover_pro.pipeline.types import (
    HoldClampVerdict,
    PipelineResult,
)
from custom_components.adaptive_cover_pro.state.cover_provider import CoverProvider

from tests.test_pipeline.conftest import make_snapshot


def _coordinator_with(result: PipelineResult):
    """Build a minimal coordinator around a ready-made pipeline result."""
    from custom_components.adaptive_cover_pro.const import CONF_INVERSE_STATE
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._inverse_state = False
    coord._use_interpolation = False
    # The real coordinator always has a config entry and a policy;
    # ``position_axis_inverted`` derives from the options and the dispatch seam
    # delegates to the policy, so give the fixture the same inputs rather than
    # stubbing either out. A blind policy's per-entity target is identity.
    coord.config_entry = MagicMock()
    coord.config_entry.options = {CONF_INVERSE_STATE: False}
    coord._policy = get_policy("cover_blind")
    coord._pipeline_result = result

    cmd_svc = MagicMock()
    cmd_svc.apply_position = AsyncMock(return_value=("sent", None))
    cmd_svc.record_skipped_action = MagicMock()
    coord._cmd_svc = cmd_svc

    return coord


def _make_coordinator_with_manual_hold(*, position: int = 100, held: int = 0):
    """Build a minimal coordinator whose _pipeline_result is a MANUAL hold."""
    return _coordinator_with(
        PipelineResult(
            position=position,
            control_method=ControlMethod.MANUAL,
            reason=f"manual override active — holding {held}% "
            f"(default position would be {position}%)",
            skip_command=True,
            held_position=held,
        )
    )


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
# Issue #1174: the hold's release verdict is per cover, not the instance mean
# ---------------------------------------------------------------------------
#
# ``held_position`` is the mean of every bound cover's position, so a group at
# 40/40/0 reports 27. Dispatching off the singular ``skip_command`` sent that
# one verdict to all three: either every cover moved or none did, and every
# skip record claimed the cover was held at 27 % — a number nobody sits at.

_MEAN_HELD = 27
_A_HELD = 40
_C_HELD = 0
#: The bound edge a released cover is sent to in these fixtures.
_CLAMP_TARGET = 40


def _verdicts(*, a_released: bool, c_released: bool):
    """Verdicts shaped the way the registry builds them (#1174).

    A released cover carries the bound edge it was released to; a held one
    carries its own position, which is dispatched only if another axis forces
    the command.
    """
    return {
        "cover.a": HoldClampVerdict(
            _A_HELD,
            released=a_released,
            target=_CLAMP_TARGET if a_released else _A_HELD,
        ),
        "cover.c": HoldClampVerdict(
            _C_HELD,
            released=c_released,
            target=_CLAMP_TARGET if c_released else _C_HELD,
        ),
    }


def _extras_for(coord, cover: str) -> dict:
    """Return the extras of the skip record written for ``cover``."""
    call = next(
        c
        for c in coord._cmd_svc.record_skipped_action.call_args_list
        if c.args[0] == cover
    )
    return call.kwargs["extras"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verdicts_route_dispatch_per_cover():
    """A clamp that released only ``cover.c`` must not also move ``cover.a``.

    The floor outranked the hold, so ``skip_command`` is cleared and the target
    is dispatched — but only to the cover whose own position violated the
    bound. ``cover.a`` was already sitting on 40 and stays held.
    """
    coord = _coordinator_with(
        PipelineResult(
            position=40,
            control_method=ControlMethod.MANUAL,
            skip_command=False,
            position_constraint_applied=True,
            held_position=_MEAN_HELD,
            hold_clamp_verdicts=_verdicts(a_released=False, c_released=True),
        )
    )
    ctx = MagicMock()

    await coord._dispatch_to_cover("cover.a", 40, "custom_position_1", ctx)

    coord._cmd_svc.apply_position.assert_not_called()
    args, _ = coord._cmd_svc.record_skipped_action.call_args
    assert args[0] == "cover.a"
    assert args[1] == "manual_override_hold"
    assert _extras_for(coord, "cover.a")["held_position"] == _A_HELD

    await coord._dispatch_to_cover("cover.c", 40, "custom_position_1", ctx)

    coord._cmd_svc.apply_position.assert_called_once_with(
        "cover.c", 40, "custom_position_1", context=ctx
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unclamped_hold_records_each_covers_own_position():
    """Nothing clamped, so every cover is held — each at its OWN position."""
    coord = _coordinator_with(
        PipelineResult(
            position=100,
            control_method=ControlMethod.MANUAL,
            skip_command=True,
            held_position=_MEAN_HELD,
            hold_clamp_verdicts=_verdicts(a_released=False, c_released=False),
        )
    )
    ctx = MagicMock()

    await coord._dispatch_to_cover("cover.a", 100, "manual_override", ctx)
    await coord._dispatch_to_cover("cover.c", 100, "manual_override", ctx)

    coord._cmd_svc.apply_position.assert_not_called()
    assert _extras_for(coord, "cover.a")["held_position"] == _A_HELD
    assert _extras_for(coord, "cover.c")["held_position"] == _C_HELD
    assert all(
        c.kwargs["extras"]["held_position"] != _MEAN_HELD
        for c in coord._cmd_svc.record_skipped_action.call_args_list
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_verdict_falls_back_to_singular_skip():
    """No verdicts (a motion hold) → the singular ``skip_command`` path, verbatim.

    ``MotionTimeoutHandler`` publishes its hold through ``position`` and leaves
    ``held_position`` unset, so the record still falls back to the logical-frame
    flip of the result position.
    """
    coord = _coordinator_with(
        PipelineResult(
            position=42,
            control_method=ControlMethod.MOTION,
            skip_command=True,
            held_position=None,
            hold_clamp_verdicts=None,
        )
    )
    ctx = MagicMock()

    await coord._dispatch_to_cover("cover.bedroom", 42, "solar", ctx)

    coord._cmd_svc.apply_position.assert_not_called()
    args, kwargs = coord._cmd_svc.record_skipped_action.call_args
    assert args[1] == "motion_hold"
    assert kwargs["extras"]["held_position"] == 42


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
