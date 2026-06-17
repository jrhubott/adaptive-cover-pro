"""Unit tests for the stop_service module.

Tests the thin target-resolution layer over
``Coordinator.async_apply_user_stop``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coord(*, entities: list[str] | None = None, apply_user_stop_result=None):
    """Build a minimal mock coordinator."""
    coord = MagicMock()
    coord.entities = entities or ["cover.test_blind"]
    coord.async_apply_user_stop = AsyncMock(
        return_value=apply_user_stop_result or ("sent", "stop_cover")
    )
    return coord


# ---------------------------------------------------------------------------
# Step 2: _resolve_targets wrapper delegates to services._resolve_targets
# ---------------------------------------------------------------------------


def test_resolve_targets_wrapper_delegates_to_services_module() -> None:
    """The local _resolve_targets wrapper forwards args to services._resolve_targets."""
    from custom_components.adaptive_cover_pro.services.stop_service import (
        _resolve_targets as wrapper,
    )

    sentinel_hass = MagicMock(name="hass")
    sentinel_call = MagicMock(name="call")
    expected = {"coord_x": None}

    with patch(
        "custom_components.adaptive_cover_pro.services._resolve_targets",
        return_value=expected,
    ) as real:
        result = wrapper(sentinel_hass, sentinel_call)

    real.assert_called_once_with(sentinel_hass, sentinel_call)
    assert result is expected


# ---------------------------------------------------------------------------
# Step 3: mark_user_command is called via async_apply_user_stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_handle_stop_calls_apply_user_stop() -> None:
    """async_handle_stop calls coord.async_apply_user_stop for each entity."""
    from custom_components.adaptive_cover_pro.services.stop_service import (
        async_handle_stop,
    )

    coord = _make_coord()
    call = MagicMock()

    with patch(
        "custom_components.adaptive_cover_pro.services.stop_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_stop(call)

    coord.async_apply_user_stop.assert_awaited_once_with(
        "cover.test_blind", trigger="stop"
    )


# ---------------------------------------------------------------------------
# Step 6: Entity filter limits which entities are commanded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entity_filter_limits_commands() -> None:
    """When entity_filter is a set, only those entities get commanded."""
    from custom_components.adaptive_cover_pro.services.stop_service import (
        async_handle_stop,
    )

    coord = _make_coord(entities=["cover.blind_a", "cover.blind_b"])
    call = MagicMock()

    with patch(
        "custom_components.adaptive_cover_pro.services.stop_service._resolve_targets",
        return_value={coord: {"cover.blind_a"}},
    ):
        await async_handle_stop(call)

    calls = coord.async_apply_user_stop.await_args_list
    entity_ids_commanded = [c.args[0] for c in calls]
    assert entity_ids_commanded == ["cover.blind_a"]


# ---------------------------------------------------------------------------
# Step 7: No target → all coordinators
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_filter_commands_all_entities() -> None:
    """When entity_filter is None, all coordinator entities are commanded."""
    from custom_components.adaptive_cover_pro.services.stop_service import (
        async_handle_stop,
    )

    coord = _make_coord(entities=["cover.blind_a", "cover.blind_b"])
    call = MagicMock()

    with patch(
        "custom_components.adaptive_cover_pro.services.stop_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_stop(call)

    calls = coord.async_apply_user_stop.await_args_list
    entity_ids_commanded = sorted(c.args[0] for c in calls)
    assert entity_ids_commanded == ["cover.blind_a", "cover.blind_b"]


# ---------------------------------------------------------------------------
# Step 8: Unknown entity → silently skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_entity_silently_skipped() -> None:
    """Empty resolve → no coordinator → no async_apply_user_stop call."""
    from custom_components.adaptive_cover_pro.services.stop_service import (
        async_handle_stop,
    )

    call = MagicMock()

    with patch(
        "custom_components.adaptive_cover_pro.services.stop_service._resolve_targets",
        return_value={},
    ):
        await async_handle_stop(call)
    # No assertion needed — just must not raise
