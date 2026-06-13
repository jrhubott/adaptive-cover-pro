"""Tests for ``Coordinator.async_apply_user_position`` shared helper.

This helper is the single delegation point for any user-initiated cover
position command (the ``set_position`` integration service, the opt-in
proxy cover entity, future external triggers). It owns the min-mode floor
clamp, the pipeline preemption check, manual-override engagement, and the
``apply_position`` dispatch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import DEFAULT_CUSTOM_POSITION_PRIORITY
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.manual_override import (
    ManualOverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
    DecisionStep,
    PipelineResult,
)


def _slot(pos: int, *, is_on: bool, min_mode: bool) -> CustomPositionSensorState:
    return CustomPositionSensorState(
        entity_ids=(f"binary_sensor.slot_{pos}",),
        is_on=is_on,
        position=pos,
        priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
        min_mode=min_mode,
        use_my=False,
    )


def _pipeline_result_with_winner(
    handler_name: str, priority: int, position: int = 50
) -> PipelineResult:
    """Build a synthetic ``PipelineResult`` whose decision_trace flags ``handler_name`` as the matched winner."""
    from custom_components.adaptive_cover_pro.const import ControlMethod

    return PipelineResult(
        position=position,
        control_method=ControlMethod.SOLAR,
        reason="test",
        decision_trace=[
            DecisionStep(
                handler=handler_name, matched=True, reason="test", position=position
            )
        ],
    )


def _make_coord(
    custom_states,
    *,
    default_options=None,
    winner_name: str = "solar",
    winner_priority: int = 40,
    winner_handler_instance=None,
    weather_override_active: bool = False,
    weather_override_position: int = 0,
    weather_override_min_mode: bool = False,
):
    """Build a coordinator-shaped mock that exposes async_apply_user_position.

    We import the *real* method off the Coordinator class and bind it onto a
    MagicMock so we can drive it without a full HA setup. By default the
    pipeline mock returns ``solar`` (priority 40) as the winner — strictly
    less than ``ManualOverrideHandler.priority`` (80), so the preemption
    check passes through to dispatch.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from tests.test_pipeline.conftest import make_snapshot

    coord = MagicMock(spec=AdaptiveDataUpdateCoordinator)
    coord.config_entry = MagicMock()
    coord.config_entry.options = default_options if default_options is not None else {}
    # PipelineSnapshotBuilder mock — Phase D moved HA reads + snapshot
    # assembly onto this collaborator.  Both call-sites of
    # async_apply_user_position route through it.
    coord._snapshot_builder = MagicMock()
    coord._snapshot_builder.read_custom_position_sensors.return_value = custom_states
    snapshot = make_snapshot(
        custom_position_sensors=custom_states or [],
        weather_override_active=weather_override_active,
        weather_override_position=weather_override_position,
        weather_override_min_mode=weather_override_min_mode,
    )
    coord._snapshot_builder.build = MagicMock(return_value=snapshot)
    # Coordinator state that the builder call needs as keyword args.  These
    # are instance attributes (set in __init__) so they aren't on the spec'd
    # MagicMock by default — preset them explicitly.
    coord._cover_data = MagicMock(name="cover_data")
    coord._cover_type = "cover_blind"
    coord._weather_readings = None
    ctx = MagicMock(name="position_context")
    coord._build_position_context.return_value = ctx
    coord._cmd_svc = MagicMock()
    coord._cmd_svc.apply_position = AsyncMock(
        return_value=("sent", "set_cover_position")
    )
    coord._cmd_svc.record_preempted_skip = MagicMock()

    # Pipeline mock: returns a synthetic result with the named handler as winner.
    coord._pipeline = MagicMock()
    coord._pipeline.evaluate.return_value = _pipeline_result_with_winner(
        winner_name, winner_priority
    )

    # Handler lookup so the helper can resolve priority from the winner step.
    if winner_handler_instance is not None:
        coord._handler_by_name = {winner_name: winner_handler_instance}
    else:
        handler = MagicMock()
        handler.priority = winner_priority
        coord._handler_by_name = {winner_name: handler}

    # Manager for manual-override engagement.
    coord.manager = MagicMock()
    coord.manager.mark_user_command = MagicMock()

    # Bind the real method
    coord.async_apply_user_position = (
        AdaptiveDataUpdateCoordinator.async_apply_user_position.__get__(coord)
    )
    return coord, ctx


@pytest.mark.asyncio
async def test_async_apply_user_position_clamps_to_min_mode_floor() -> None:
    """Requested < highest active min-mode floor → clamped up to floor."""
    coord, ctx = _make_coord([_slot(40, is_on=True, min_mode=True)])

    await coord.async_apply_user_position("cover.test", 10, trigger="set_position")

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 40, "set_position", ctx
    )


@pytest.mark.asyncio
async def test_async_apply_user_position_passes_above_floor_unchanged() -> None:
    """Requested > floor → passes through unchanged."""
    coord, ctx = _make_coord([_slot(40, is_on=True, min_mode=True)])

    await coord.async_apply_user_position("cover.test", 80, trigger="proxy_managed")

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 80, "proxy_managed", ctx
    )


@pytest.mark.asyncio
async def test_async_apply_user_position_no_floors_uses_requested() -> None:
    """No active min-mode slots → requested value passes through."""
    coord, ctx = _make_coord(
        [_slot(80, is_on=True, min_mode=False), _slot(20, is_on=False, min_mode=True)]
    )

    await coord.async_apply_user_position("cover.test", 5, trigger="set_position")

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 5, "set_position", ctx
    )


@pytest.mark.asyncio
async def test_async_apply_user_position_uses_force_context() -> None:
    """_build_position_context must be called with force=True."""
    coord, _ctx = _make_coord([])
    await coord.async_apply_user_position("cover.test", 50, trigger="proxy_open")

    coord._build_position_context.assert_called_once()
    _, kwargs = coord._build_position_context.call_args
    assert kwargs.get("force") is True


@pytest.mark.asyncio
async def test_async_apply_user_position_accepts_trigger_label() -> None:
    """The trigger label is forwarded verbatim to ``apply_position``."""
    coord, ctx = _make_coord([])
    await coord.async_apply_user_position("cover.test", 33, trigger="proxy_tilt")
    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 33, "proxy_tilt", ctx
    )


@pytest.mark.asyncio
async def test_async_apply_user_position_uses_passed_options_when_provided() -> None:
    """When ``options`` is passed, it overrides ``self.config_entry.options``."""
    entry_options = {"from": "entry"}
    coord, _ctx = _make_coord(
        [_slot(70, is_on=True, min_mode=True)], default_options=entry_options
    )
    custom_options = {"from": "override"}

    await coord.async_apply_user_position(
        "cover.test", 10, trigger="set_position", options=custom_options
    )

    coord._snapshot_builder.build.assert_called_once()
    args, kwargs = coord._snapshot_builder.build.call_args
    # First positional arg of build() is the options dict.
    assert args[0] is custom_options or kwargs.get("opts") is custom_options
    # And the override flowed into _build_position_context too
    args, kwargs = coord._build_position_context.call_args
    # signature: (entity, options, *, force=...)
    assert args[1] is custom_options or kwargs.get("options") is custom_options


# ---------------------------------------------------------------------------
# New contract: pipeline preemption check + manual-override engagement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_engages_manual_override_when_no_preemption() -> None:
    """Default path: solar winner (40) < ManualOverride priority → command dispatched and manual override engaged."""
    coord, ctx = _make_coord([], winner_name="solar", winner_priority=40)

    outcome = await coord.async_apply_user_position(
        "cover.test", 50, trigger="proxy_managed"
    )

    coord.manager.mark_user_command.assert_called_once_with(
        "cover.test", reason="proxy_managed"
    )
    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 50, "proxy_managed", ctx
    )
    assert outcome == ("sent", "set_cover_position")


@pytest.mark.asyncio
async def test_default_skipped_when_safety_custom_position_active() -> None:
    """Safety custom position (priority 100) wins → command dropped, manual override not engaged."""
    coord, _ctx = _make_coord([], winner_name="custom_position_5", winner_priority=100)

    outcome = await coord.async_apply_user_position(
        "cover.test", 50, trigger="proxy_managed"
    )

    assert outcome == ("skipped", "preempted_by_custom_position_5")
    coord._cmd_svc.apply_position.assert_not_awaited()
    coord.manager.mark_user_command.assert_not_called()
    coord._cmd_svc.record_preempted_skip.assert_called_once_with(
        "cover.test", 50, trigger="proxy_managed", winner_name="custom_position_5"
    )


@pytest.mark.asyncio
async def test_default_skipped_when_weather_active() -> None:
    """Weather (90) wins → command dropped, manual override not engaged."""
    coord, _ctx = _make_coord([], winner_name="weather", winner_priority=90)

    outcome = await coord.async_apply_user_position(
        "cover.test", 30, trigger="set_position"
    )

    assert outcome == ("skipped", "preempted_by_weather")
    coord._cmd_svc.apply_position.assert_not_awaited()
    coord.manager.mark_user_command.assert_not_called()
    coord._cmd_svc.record_preempted_skip.assert_called_once_with(
        "cover.test", 30, trigger="set_position", winner_name="weather"
    )


@pytest.mark.asyncio
async def test_default_not_blocked_by_cloud_suppression() -> None:
    """cloud_suppression (60) does NOT preempt — command dispatched + manual override engaged."""
    coord, ctx = _make_coord([], winner_name="cloud_suppression", winner_priority=60)

    await coord.async_apply_user_position("cover.test", 50, trigger="proxy_managed")

    coord.manager.mark_user_command.assert_called_once_with(
        "cover.test", reason="proxy_managed"
    )
    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 50, "proxy_managed", ctx
    )


@pytest.mark.asyncio
async def test_default_not_blocked_by_solar() -> None:
    """Solar (40) does NOT preempt — command dispatched + manual override engaged."""
    coord, ctx = _make_coord([], winner_name="solar", winner_priority=40)

    await coord.async_apply_user_position("cover.test", 50, trigger="proxy_managed")

    coord.manager.mark_user_command.assert_called_once_with(
        "cover.test", reason="proxy_managed"
    )
    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 50, "proxy_managed", ctx
    )


@pytest.mark.asyncio
async def test_force_true_bypasses_pipeline_check() -> None:
    """force=True with safety custom-position winner → command still dispatches."""
    coord, ctx = _make_coord([], winner_name="custom_position_5", winner_priority=100)

    outcome = await coord.async_apply_user_position(
        "cover.test", 50, trigger="set_position", force=True
    )

    assert outcome == ("sent", "set_cover_position")
    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 50, "set_position", ctx
    )
    # Pipeline must NOT have been consulted on the force=True path
    coord._pipeline.evaluate.assert_not_called()
    coord._cmd_svc.record_preempted_skip.assert_not_called()


@pytest.mark.asyncio
async def test_force_true_does_not_engage_manual_override() -> None:
    """force=True must NOT call mark_user_command, regardless of pipeline winner."""
    coord, _ctx = _make_coord([], winner_name="solar", winner_priority=40)

    await coord.async_apply_user_position(
        "cover.test", 50, trigger="set_position", force=True
    )

    coord.manager.mark_user_command.assert_not_called()


@pytest.mark.asyncio
async def test_snapshot_passed_to_pipeline_has_manual_override_false() -> None:
    """Meta-bug guard: preemption snapshot must force manual_override_active=False.

    Otherwise a stale manual_control flag would self-claim priority 80 and
    let the cover move anyway.
    """
    coord, _ctx = _make_coord([], winner_name="solar", winner_priority=40)
    coord.manager.binary_cover_manual = True  # stale flag set

    await coord.async_apply_user_position("cover.test", 50, trigger="proxy_managed")

    coord._snapshot_builder.build.assert_called_once()
    _, kwargs = coord._snapshot_builder.build.call_args
    assert kwargs.get("manual_override_active") is False


@pytest.mark.asyncio
async def test_preempted_skip_recorded_in_last_skipped_action() -> None:
    """When preempted, record_preempted_skip is called with the winner name."""
    coord, _ctx = _make_coord([], winner_name="custom_position_5", winner_priority=100)

    await coord.async_apply_user_position("cover.test", 42, trigger="proxy_managed")

    coord._cmd_svc.record_preempted_skip.assert_called_once_with(
        "cover.test", 42, trigger="proxy_managed", winner_name="custom_position_5"
    )


def test_manual_override_priority_constant_unchanged() -> None:
    """Locks in ManualOverrideHandler.priority=80 so the preemption cutoff stays correct."""
    assert ManualOverrideHandler.priority == 80


# ---------------------------------------------------------------------------
# Custom-position min-mode floor interaction with async_apply_user_position
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_position_min_mode_does_not_preempt_request_above_floor() -> None:
    """When a min-mode floor is active and the user requests a position at or
    above the floor, the command must NOT be preempted — floors defer to the
    floor-clamp composition pass (#463), they no longer act as priority winners.
    """
    state = CustomPositionSensorState(
        entity_ids=("binary_sensor.cp1",),
        is_on=True,
        position=60,
        priority=95,
        min_mode=True,
        use_my=False,
    )
    coord, ctx = _make_coord(
        [state],
        winner_name="solar",
        winner_priority=40,
    )

    outcome = await coord.async_apply_user_position(
        "cover.test", 90, trigger="set_position"
    )

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 90, "set_position", ctx
    )
    coord._cmd_svc.record_preempted_skip.assert_not_called()
    assert outcome == ("sent", "set_cover_position")


@pytest.mark.asyncio
async def test_custom_position_min_mode_clamps_and_dispatches_request_below_floor() -> (
    None
):
    """When a min-mode floor is active and the user requests below the floor,
    the command is clamped to the floor and dispatched — NOT preempted.
    """
    state = CustomPositionSensorState(
        entity_ids=("binary_sensor.cp1",),
        is_on=True,
        position=60,
        priority=95,
        min_mode=True,
        use_my=False,
    )
    coord, ctx = _make_coord(
        [state],
        winner_name="solar",
        winner_priority=40,
    )

    outcome = await coord.async_apply_user_position(
        "cover.test", 40, trigger="set_position"
    )

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 60, "set_position", ctx
    )
    coord._cmd_svc.record_preempted_skip.assert_not_called()
    assert outcome == ("sent", "set_cover_position")


@pytest.mark.asyncio
async def test_custom_position_exact_mode_still_preempts() -> None:
    """When the winning custom-position handler is NOT in min-mode, it preempts
    as before — the new exception must not fire.
    """
    handler = CustomPositionHandler(slot=1, position=60, priority=95)
    state = CustomPositionSensorState(
        entity_ids=("binary_sensor.cp1",),
        is_on=True,
        position=60,
        priority=95,
        min_mode=False,
        use_my=False,
        slot=1,
    )
    coord, _ctx = _make_coord(
        [state],
        winner_name="custom_position_1",
        winner_priority=95,
        winner_handler_instance=handler,
    )
    coord._pipeline.evaluate.return_value = _pipeline_result_with_winner(
        "custom_position_1", 95, position=60
    )

    outcome = await coord.async_apply_user_position(
        "cover.test", 70, trigger="set_position"
    )

    assert outcome == ("skipped", "preempted_by_custom_position_1")
    coord._cmd_svc.record_preempted_skip.assert_called_once()
    coord._cmd_svc.apply_position.assert_not_awaited()


@pytest.mark.asyncio
async def test_custom_position_min_mode_preempts_when_request_below_floor_and_handler_is_different_slot() -> (
    None
):
    """A real-position custom-position handler (different slot) wins with
    priority 95 > 80 → preempts. The floor clamp on the requested value
    is irrelevant once preemption fires.
    """
    handler = CustomPositionHandler(slot=2, position=60, priority=95)
    # Only slot 1 is the min-mode floor — slot 2 is the priority-95 winner.
    state = CustomPositionSensorState(
        entity_ids=("binary_sensor.cp1",),
        is_on=True,
        position=60,
        priority=95,
        min_mode=True,
        use_my=False,
    )
    coord, _ctx = _make_coord(
        [state],
        winner_name="custom_position_2",
        winner_priority=95,
        winner_handler_instance=handler,
    )
    coord._pipeline.evaluate.return_value = _pipeline_result_with_winner(
        "custom_position_2", 95, position=60
    )

    outcome = await coord.async_apply_user_position(
        "cover.test", 90, trigger="set_position"
    )

    assert outcome == ("skipped", "preempted_by_custom_position_2")
    coord._cmd_svc.record_preempted_skip.assert_called_once()
    coord._cmd_svc.apply_position.assert_not_awaited()
