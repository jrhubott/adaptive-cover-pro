"""Tests for the coordinator's axis self-discovery builder (issue #725).

``build_axis_discovery`` rolls up per-axis capability support across every
managed cover entity (an axis is supported if ANY member exposes it) and
delegates the per-axis metadata to the policy's ``describe`` — never re-reading
HA features or branching on the cover-type string.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.state.snapshot import CoverCapabilities

pytestmark = pytest.mark.unit


def _caps(*, position: bool, tilt: bool) -> CoverCapabilities:
    return CoverCapabilities(
        has_set_position=position,
        has_set_tilt_position=tilt,
        has_open=True,
        has_close=True,
    )


def _coord(cover_type: str, caps_map: dict[str, CoverCapabilities]) -> MagicMock:
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
    coord = _coord("cover_blind", {"cover.blind": _caps(position=True, tilt=False)})
    desc = coord.build_axis_discovery()
    assert desc.cover_type == "cover_blind"
    assert [a.id for a in desc.axes] == ["position"]
    assert desc.axes[0].supported is True


def test_venetian_discovery_rolls_up_supported_across_members() -> None:
    """A position-only member + a dual-axis member → both axes supported."""
    coord = _coord(
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
    coord = _coord(
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
