"""Regression test for set_position service min-mode floor clamp.

This test locks in the floor-clamping contract BEFORE the helper extraction
refactor (Phase B). The same clamp behaviour must continue to hold after
``async_handle_set_position`` is rewritten to delegate to
``Coordinator.async_apply_user_position``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.const import DEFAULT_CUSTOM_POSITION_PRIORITY
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
)


def _slot(
    position: int,
    *,
    is_on: bool,
    min_mode: bool,
    priority: int = DEFAULT_CUSTOM_POSITION_PRIORITY,
) -> CustomPositionSensorState:
    return CustomPositionSensorState(
        entity_ids=(f"binary_sensor.slot_p{position}",),
        is_on=is_on,
        position=position,
        priority=priority,
        min_mode=min_mode,
        use_my=False,
    )


def _make_coord(custom_states):
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = MagicMock()
    coord.entities = ["cover.living_room"]
    coord.config_entry = MagicMock()
    coord.config_entry.options = {}
    coord._snapshot_builder = MagicMock()
    coord._snapshot_builder.read_custom_position_sensors.return_value = custom_states
    # Floor composition reads custom_position_sensors from a real PipelineSnapshot
    # (issue #463) — return a populated snapshot from build().
    from tests.test_pipeline.conftest import make_snapshot  # noqa: PLC0415

    snapshot = make_snapshot(custom_position_sensors=custom_states or [])
    coord._snapshot_builder.build = MagicMock(return_value=snapshot)
    ctx = MagicMock(name="position_context")
    coord._build_position_context.return_value = ctx
    coord._cmd_svc = MagicMock()
    coord._cmd_svc.apply_position = AsyncMock(
        return_value=("sent", "set_cover_position")
    )
    coord.async_apply_user_position = (
        AdaptiveDataUpdateCoordinator.async_apply_user_position.__get__(coord)
    )
    # set_position routes through the axis collapse point (issue #725) — bind the
    # real dispatcher so the service reaches async_apply_user_position as before.
    coord.async_apply_user_tilt = AsyncMock(return_value=("sent", "tilt"))
    coord.async_apply_user_axis = (
        AdaptiveDataUpdateCoordinator.async_apply_user_axis.__get__(coord)
    )
    return coord, ctx


@pytest.mark.asyncio
async def test_set_position_clamps_requested_below_min_mode_floor() -> None:
    """Two slots: slot 1 (min_mode, on, pos=30); slot 2 (not-min, off, pos=80).

    Verifies the documented contract of the existing service:
      - position=10 (below floor)  → clamped up to 30, force=True
      - position=50 (above floor)  → passes through unchanged, force=True
      - no min-mode active         → passes through unchanged
    """
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    # Re-anchored for #472: a floor must be > ManualOverrideHandler.priority (80)
    # to clamp a user move.
    slot1 = _slot(30, is_on=True, min_mode=True, priority=82)
    slot2 = _slot(80, is_on=False, min_mode=False)

    # Case 1: below floor → clamps to 30
    coord, ctx = _make_coord([slot1, slot2])
    call = MagicMock()
    call.data = {"position": 10}
    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)
    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.living_room", 30, "set_position", ctx
    )
    # force=True passed to _build_position_context
    _, kwargs = coord._build_position_context.call_args
    assert kwargs.get("force") is True

    # Case 2: above floor → unchanged at 50
    coord, ctx = _make_coord([slot1, slot2])
    call = MagicMock()
    call.data = {"position": 50}
    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)
    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.living_room", 50, "set_position", ctx
    )

    # Case 3: no min-mode floor active → passes through
    slot_off = _slot(30, is_on=False, min_mode=True)
    coord, ctx = _make_coord([slot_off])
    call = MagicMock()
    call.data = {"position": 10}
    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)
    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.living_room", 10, "set_position", ctx
    )
