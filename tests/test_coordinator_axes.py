"""Tests for the coordinator's axis surface (issue #725).

Two halves of the same feature:

* ``async_apply_user_axis`` — the collapse point every user axis command routes
  through. The generalized ``set_axes`` service (and the refactored
  ``set_position`` / ``set_tilt`` wrappers) key on the ``AXIS_NAME_*``
  constants; it must delegate to the existing per-axis setters unchanged and
  reject an unknown axis name.
* ``build_axis_discovery`` — rolls up per-axis capability support across every
  managed cover entity (an axis is supported if ANY member exposes it) and
  delegates the per-axis metadata to the policy's ``describe`` — never
  re-reading HA features or branching on the cover-type string.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import CONF_INTERP, CONF_INVERSE_STATE
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.base import (
    AXIS_NAME_POSITION,
    AXIS_NAME_TILT,
)
from custom_components.adaptive_cover_pro.state.snapshot import CoverCapabilities

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# async_apply_user_axis — the single user-command dispatch point
# ---------------------------------------------------------------------------


def _dispatch_coord() -> MagicMock:
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
    coord = _dispatch_coord()
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
    coord = _dispatch_coord()
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
    coord = _dispatch_coord()
    with pytest.raises(ValueError, match="Unknown axis"):
        await coord.async_apply_user_axis("cover.x", "diagonal", 50, trigger="set_axes")
    coord.async_apply_user_position.assert_not_awaited()
    coord.async_apply_user_tilt.assert_not_awaited()


# ---------------------------------------------------------------------------
# build_axis_discovery — capability roll-up across managed entities
# ---------------------------------------------------------------------------


def _caps(*, position: bool, tilt: bool) -> CoverCapabilities:
    return CoverCapabilities(
        has_set_position=position,
        has_set_tilt_position=tilt,
        has_open=True,
        has_close=True,
    )


def _discovery_coord(
    cover_type: str, caps_map: dict[str, CoverCapabilities]
) -> MagicMock:
    coord = MagicMock()
    coord.entities = list(caps_map)
    coord._policy = get_policy(cover_type)
    coord._cover_provider = MagicMock()
    coord._cover_provider.read_all_capabilities.return_value = caps_map
    coord.build_axis_discovery = (
        AdaptiveDataUpdateCoordinator.build_axis_discovery.__get__(coord)
    )
    return coord


def test_blind_discovery_single_position_axis() -> None:
    coord = _discovery_coord(
        "cover_blind", {"cover.blind": _caps(position=True, tilt=False)}
    )
    desc = coord.build_axis_discovery()
    assert desc.cover_type == "cover_blind"
    assert [a.id for a in desc.axes] == ["position"]
    assert desc.axes[0].supported is True


def test_venetian_discovery_rolls_up_supported_across_members() -> None:
    """A position-only member + a dual-axis member → both axes supported."""
    coord = _discovery_coord(
        "cover_venetian",
        {
            "cover.a": _caps(position=True, tilt=False),
            "cover.b": _caps(position=True, tilt=True),
        },
    )
    desc = coord.build_axis_discovery()
    by_id = {a.id: a for a in desc.axes}
    assert set(by_id) == {"position", "tilt"}
    assert by_id["position"].supported is True
    assert by_id["tilt"].supported is True


def test_venetian_discovery_tilt_unsupported_when_no_member_has_it() -> None:
    coord = _discovery_coord(
        "cover_venetian",
        {
            "cover.a": _caps(position=True, tilt=False),
            "cover.b": _caps(position=True, tilt=False),
        },
    )
    desc = coord.build_axis_discovery()
    by_id = {a.id: a for a in desc.axes}
    assert by_id["position"].supported is True
    assert by_id["tilt"].supported is False


def test_build_axis_discovery_passes_entry_options() -> None:
    """The descriptor's per-axis ``inverted`` reflects this entry's options (#1028)."""
    coord = _discovery_coord(
        "cover_blind", {"cover.blind": _caps(position=True, tilt=False)}
    )

    coord.config_entry = SimpleNamespace(options={CONF_INVERSE_STATE: True})
    assert coord.build_axis_discovery().axes[0].inverted is True

    # Interpolation suppresses position inversion — same rule as coordinator.state.
    coord.config_entry = SimpleNamespace(
        options={CONF_INVERSE_STATE: True, CONF_INTERP: True}
    )
    assert coord.build_axis_discovery().axes[0].inverted is False
