"""Distance-attribute computation on the Target Position sensor.

Verifies the ``_compute_distance_attrs`` helper that translates a 0-100 %
target position into a physical distance for cover_blind / cover_awning /
cover_venetian. The helper is exercised directly so the test stays focused
on the unit-system + policy-hook contract without spinning up a full
coordinator.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.sensor import _compute_distance_attrs


def _make_coordinator(
    *,
    cover_type: str,
    h_win: float = 2.0,
    awn_length: float = 1.6,
    imperial: bool = False,
    position_axis_inverted: bool = False,
):
    """Build the minimum coordinator shape ``_compute_distance_attrs`` needs."""
    config_service = MagicMock()
    config_service.get_vertical_data.return_value = SimpleNamespace(h_win=h_win)
    config_service.get_horizontal_data.return_value = SimpleNamespace(
        awn_length=awn_length
    )
    hass = MagicMock()
    hass.config.units = US_CUSTOMARY_SYSTEM if imperial else MagicMock()
    return SimpleNamespace(
        _policy=get_policy(cover_type),
        _config_service=config_service,
        config_entry=SimpleNamespace(options={}),
        hass=hass,
        position_axis_inverted=position_axis_inverted,
    )


def _snapshot(positions: dict[str, int | None] | None):
    return SimpleNamespace(cover_positions=positions) if positions is not None else None


@pytest.mark.unit
def test_blind_metric_target_distance() -> None:
    coord = _make_coordinator(cover_type="cover_blind", h_win=2.0)
    attrs = _compute_distance_attrs(coord, _snapshot({}), target_position=50)
    assert attrs == {"target_distance": 1.0, "distance_unit": "m"}


@pytest.mark.unit
def test_blind_imperial_target_distance() -> None:
    coord = _make_coordinator(cover_type="cover_blind", h_win=2.0, imperial=True)
    attrs = _compute_distance_attrs(coord, _snapshot({}), target_position=50)
    assert attrs is not None
    # 1.0 m → 39.3700787... in → rounded to 2 dp = 39.37
    assert attrs["target_distance"] == pytest.approx(39.37)
    assert attrs["distance_unit"] == "in"


@pytest.mark.unit
def test_blind_inverse_rebases_distance_on_logical_frame() -> None:
    """A distance is physical, so it must be derived from the logical frame.

    The percentages this helper receives are cover-frame values on an inverted
    install. Doing literal arithmetic on them reports the physical opposite of
    the truth, so the helper converts first: cover-frame 30 is logical 70, and
    70 % of a 2 m window is 1.4 m (issue #1028).
    """
    coord = _make_coordinator(
        cover_type="cover_blind", h_win=2.0, position_axis_inverted=True
    )
    attrs = _compute_distance_attrs(coord, _snapshot({}), target_position=30)
    assert attrs == {"target_distance": 1.4, "distance_unit": "m"}


@pytest.mark.unit
def test_awning_inverse_full_extension_reports_full_length() -> None:
    """A fully-extended 3 m awning must not report 0.00 m of extension.

    On an inverted install the awning is fully out at cover-frame 0; the
    reported distance is the full configured length (issue #1028).
    """
    coord = _make_coordinator(
        cover_type="cover_awning", awn_length=3.0, position_axis_inverted=True
    )
    attrs = _compute_distance_attrs(
        coord, _snapshot({"cover.awning": 0}), target_position=0
    )
    assert attrs is not None
    assert attrs["target_distance"] == 3.0
    assert attrs["actual_distances"] == {"cover.awning": 3.0}


@pytest.mark.unit
def test_awning_uses_awn_length() -> None:
    coord = _make_coordinator(cover_type="cover_awning", awn_length=2.0)
    attrs = _compute_distance_attrs(coord, _snapshot({}), target_position=75)
    assert attrs == {"target_distance": 1.5, "distance_unit": "m"}


@pytest.mark.unit
def test_venetian_uses_window_height_not_awn_length() -> None:
    coord = _make_coordinator(cover_type="cover_venetian", h_win=1.4, awn_length=99.0)
    attrs = _compute_distance_attrs(coord, _snapshot({}), target_position=50)
    assert attrs == {"target_distance": 0.7, "distance_unit": "m"}


@pytest.mark.unit
def test_tilt_emits_no_distance_attrs() -> None:
    coord = _make_coordinator(cover_type="cover_tilt")
    assert _compute_distance_attrs(coord, _snapshot({}), target_position=50) is None


@pytest.mark.unit
def test_actual_distances_per_cover() -> None:
    coord = _make_coordinator(cover_type="cover_blind", h_win=2.0)
    snap = _snapshot({"cover.a": 25, "cover.b": 100, "cover.c": None})
    attrs = _compute_distance_attrs(coord, snap, target_position=50)
    assert attrs is not None
    assert attrs["actual_distances"] == {
        "cover.a": 0.5,
        "cover.b": 2.0,
        "cover.c": None,
    }


@pytest.mark.unit
def test_target_position_none_skips_all_attrs() -> None:
    coord = _make_coordinator(cover_type="cover_blind", h_win=2.0)
    assert _compute_distance_attrs(coord, _snapshot({}), target_position=None) is None


@pytest.mark.unit
def test_zero_dimension_skips_all_attrs() -> None:
    coord = _make_coordinator(cover_type="cover_blind", h_win=0.0)
    assert _compute_distance_attrs(coord, _snapshot({}), target_position=50) is None


@pytest.mark.unit
def test_no_snapshot_still_emits_target_distance() -> None:
    coord = _make_coordinator(cover_type="cover_blind", h_win=2.0)
    attrs = _compute_distance_attrs(coord, None, target_position=50)
    assert attrs == {"target_distance": 1.0, "distance_unit": "m"}


@pytest.mark.unit
def test_empty_snapshot_omits_actual_distances() -> None:
    coord = _make_coordinator(cover_type="cover_blind", h_win=2.0)
    attrs = _compute_distance_attrs(coord, _snapshot({}), target_position=50)
    assert attrs is not None
    assert "actual_distances" not in attrs
