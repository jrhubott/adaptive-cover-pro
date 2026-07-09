"""User-initiated commands must work when automatic control is OFF.

Regression for the report: with automatic control off, the ``set_position``
service and the proxy cover slider silently dropped the command at the
``auto_control_off`` gate, while only the My Position button (which alone
passed ``bypass_auto_control=True``) worked.

The fix makes the bypass intrinsic to ``async_apply_user_position`` — the
single delegation point for every user-facing command — so the service, the
proxy entity, and the button all send when auto control is off. "Automatic
control off" suppresses the integration's own sun tracking, not the user
directly commanding a cover.

The internal ``force=True`` callers (solar update, override-clear) go through
``apply_position`` directly and stay blocked when auto control is off — that
is issue #293 and is verified in test_issue_293_force_true_auto_off.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.managers.cover_command import PositionContext
from custom_components.adaptive_cover_pro.pipeline.types import (
    DecisionStep,
    PipelineResult,
)


def _make_coord():
    """Coordinator-shaped object with auto control OFF and the real gate wired.

    Binds the real ``async_apply_user_position`` + ``_build_position_context``
    and replaces ``apply_position`` with a stand-in that replicates the real
    ``auto_control_off`` gate, so the test asserts "sent" vs "skipped" exactly
    as production would.
    """
    coord = MagicMock(spec=AdaptiveDataUpdateCoordinator)
    coord.config_entry = MagicMock()
    coord.config_entry.options = {}
    # After fix #643, async_apply_user_position falls back to _resolved_options.
    coord._resolved_options = {}

    from tests.test_pipeline.conftest import make_snapshot  # noqa: PLC0415

    coord._snapshot_builder = MagicMock()
    coord._snapshot_builder.read_custom_position_sensors.return_value = []
    coord._snapshot_builder.build = MagicMock(return_value=make_snapshot())
    coord._cover_data = MagicMock()
    coord._cover_type = "cover_blind"
    coord._weather_readings = None

    # Solar winner (priority 40) — below ManualOverrideHandler, so a force=False
    # user move is not preempted.
    coord._pipeline = MagicMock()
    coord._pipeline.evaluate.return_value = PipelineResult(
        position=50,
        control_method=ControlMethod.SOLAR,
        reason="solar",
        decision_trace=[
            DecisionStep(handler="solar", matched=True, reason="solar", position=50)
        ],
    )
    solar_handler = MagicMock()
    solar_handler.priority = 40
    coord._handler_by_name = {"solar": solar_handler}

    # Automatic control OFF — the condition under test.
    coord.automatic_control = False
    coord._pipeline_bypasses_auto_control = False
    coord._pipeline_result = PipelineResult(
        position=50, control_method=ControlMethod.SOLAR, reason="solar"
    )
    coord._inverse_state = False
    coord.min_change = 2
    coord.time_threshold = 0
    coord._policy = MagicMock()
    coord._policy.position_context_overrides.return_value = {}

    coord.manager = MagicMock()
    coord.manager.is_cover_manual.return_value = False

    captured: list[PositionContext] = []

    async def _fake_apply(entity_id, position, trigger, ctx):
        captured.append(ctx)
        # Mirror of the real auto_control gate in CoverCommandService.
        if not ctx.is_safety and not ctx.bypass_auto_control and not ctx.auto_control:
            return ("skipped", "auto_control_off")
        return ("sent", "set_cover_position")

    coord._cmd_svc = MagicMock()
    coord._cmd_svc.apply_position = _fake_apply
    coord._captured_contexts = captured

    coord._build_position_context = (
        AdaptiveDataUpdateCoordinator._build_position_context.__get__(coord)
    )
    coord.async_apply_user_position = (
        AdaptiveDataUpdateCoordinator.async_apply_user_position.__get__(coord)
    )
    # set_position routes through the axis collapse point (issue #725) — bind the
    # real dispatcher so the service still reaches async_apply_user_position.
    coord.async_apply_user_tilt = AsyncMock(return_value=("sent", "tilt"))
    coord.async_apply_user_axis = (
        AdaptiveDataUpdateCoordinator.async_apply_user_axis.__get__(coord)
    )
    return coord


def _assert_user_bypass_context(coord):
    """Assert the last dispatched context bypassed the gate without being safety."""
    ctx = coord._captured_contexts[-1]
    assert ctx.bypass_auto_control is True
    assert ctx.is_safety is False


@pytest.mark.asyncio
async def test_set_position_service_sends_when_auto_control_off():
    """The set_position service sends even with automatic control off."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    coord = _make_coord()
    coord.entities = ["cover.test_blind"]
    call = MagicMock()
    call.data = {"position": 40}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)

    assert coord._captured_contexts, "service must reach apply_position"
    _assert_user_bypass_context(coord)

    # The outcome through the real gate is "sent", not "skipped".
    outcome = await coord.async_apply_user_position(
        "cover.test_blind", 40, trigger="set_position", force=False
    )
    assert outcome == ("sent", "set_cover_position")


@pytest.mark.asyncio
async def test_proxy_cover_sends_when_auto_control_off():
    """The proxy cover slider sends even with automatic control off."""
    from custom_components.adaptive_cover_pro.cover import AdaptiveProxyCover

    coord = _make_coord()
    proxy = AdaptiveProxyCover.__new__(AdaptiveProxyCover)
    proxy.coordinator = coord
    proxy._source_entity_id = "cover.test_blind"
    proxy._source_available = MagicMock(return_value=True)

    await proxy.async_set_cover_position(position=40)

    assert coord._captured_contexts, "proxy slider must reach apply_position"
    _assert_user_bypass_context(coord)


@pytest.mark.asyncio
async def test_force_true_internal_caller_still_blocked_when_auto_off():
    """Guard #293: an internal force=True caller through apply_position stays blocked.

    The bypass is intrinsic to ``async_apply_user_position`` only — not to
    ``apply_position`` itself. An internal force=True (is_safety=False) caller
    with auto control off must still be skipped.
    """
    coord = _make_coord()
    internal_ctx = PositionContext(
        auto_control=False,
        manual_override=False,
        sun_just_appeared=False,
        min_change=1,
        time_threshold=0,
        special_positions=[0, 100],
        force=True,
        is_safety=False,
        bypass_auto_control=False,
    )

    outcome = await coord._cmd_svc.apply_position(
        "cover.test_blind", 100, "force_caller", internal_ctx
    )
    assert outcome == ("skipped", "auto_control_off")
