"""Tests for the coordinator's ``async_apply_user_axis`` collapse point (#725).

The generalized ``set_axes`` service (and the refactored ``set_position`` /
``set_tilt`` wrappers) route every user axis command through this single
dispatch method, keyed on the ``AXIS_NAME_*`` constants. It must delegate to the
existing per-axis setters unchanged and reject an unknown axis name.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.cover_types.base import (
    AXIS_NAME_POSITION,
    AXIS_NAME_TILT,
)

pytestmark = pytest.mark.unit


def _coord() -> MagicMock:
    """Mock coordinator with the real ``async_apply_user_axis`` bound."""
    coord = MagicMock()
    coord.async_apply_user_position = AsyncMock(return_value=("sent", "position"))
    coord.async_apply_user_tilt = AsyncMock(return_value=("sent", "tilt"))
    coord.async_apply_user_axis = (
        AdaptiveDataUpdateCoordinator.async_apply_user_axis.__get__(coord)
    )
    return coord


@pytest.mark.asyncio
async def test_position_axis_routes_to_position_setter() -> None:
    coord = _coord()
    result = await coord.async_apply_user_axis(
        "cover.x", AXIS_NAME_POSITION, 40, trigger="set_axes", force=False
    )
    coord.async_apply_user_position.assert_awaited_once_with(
        "cover.x", 40, trigger="set_axes", force=False
    )
    coord.async_apply_user_tilt.assert_not_awaited()
    assert result == ("sent", "position")


@pytest.mark.asyncio
async def test_tilt_axis_routes_to_tilt_setter() -> None:
    coord = _coord()
    result = await coord.async_apply_user_axis(
        "cover.x", AXIS_NAME_TILT, 30, trigger="set_axes", force=True
    )
    coord.async_apply_user_tilt.assert_awaited_once_with(
        "cover.x", 30, trigger="set_axes", force=True
    )
    coord.async_apply_user_position.assert_not_awaited()
    assert result == ("sent", "tilt")


@pytest.mark.asyncio
async def test_unknown_axis_raises() -> None:
    coord = _coord()
    with pytest.raises(ValueError, match="Unknown axis"):
        await coord.async_apply_user_axis("cover.x", "diagonal", 50, trigger="set_axes")
    coord.async_apply_user_position.assert_not_awaited()
    coord.async_apply_user_tilt.assert_not_awaited()
