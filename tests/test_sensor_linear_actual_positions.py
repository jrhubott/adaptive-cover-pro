"""Cover_Position sensor publishes `linear_actual_positions` (issue #1028).

`actual_positions` is a raw HA attribute read and must stay in the cover's own
frame — the delta gates, assumed-position surface (#888) and command-target
restore (#1022) all depend on that. `linear_actual_positions` is the additive
sibling that puts the same numbers on the logical (HA-convention) frame, so a
consumer can compare an actual against `linear_position` without knowing
whether the install is inverted.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import CONF_INTERP, CONF_INVERSE_STATE
from custom_components.adaptive_cover_pro.sensor import _cover_position_attrs

from tests._helpers.cover_position_sensor import make_cover_position_sensor

pytestmark = pytest.mark.unit


def _make_sensor(*, options: dict, positions: dict | None) -> MagicMock:
    """Shared stub, pinned to this module's snapshot.cover_positions branch."""
    return make_cover_position_sensor(options=options, positions=positions)


def test_linear_actual_positions_inverted() -> None:
    """With inverse on, each raw cover read is republished as 100 - read."""
    s = _make_sensor(
        options={CONF_INVERSE_STATE: True},
        positions={"cover.a": 0, "cover.b": 30, "cover.c": None},
    )
    attrs = _cover_position_attrs(s)

    assert attrs["linear_actual_positions"] == {
        "cover.a": 100,
        "cover.b": 70,
        "cover.c": None,
    }
    # The raw sibling is untouched — gates and #888/#1022 depend on it.
    assert attrs["actual_positions"] == {"cover.a": 0, "cover.b": 30, "cover.c": None}


def test_linear_actual_positions_identity_without_inverse() -> None:
    """No inversion configured -> the logical view equals the raw view."""
    s = _make_sensor(options={}, positions={"cover.a": 0, "cover.b": 30})
    attrs = _cover_position_attrs(s)

    assert attrs["linear_actual_positions"] == attrs["actual_positions"]


def test_interpolation_suppresses_linear_actual_inversion() -> None:
    """Under interpolation the read-back is a motor value — leave it alone.

    Un-interpolating a motor read into the linear frame is #925's scope; the
    coordinator already declares inverse-state unsupported with interpolation,
    so this attribute must not pretend otherwise.
    """
    s = _make_sensor(
        options={CONF_INVERSE_STATE: True, CONF_INTERP: True},
        positions={"cover.a": 0, "cover.b": 30},
    )
    attrs = _cover_position_attrs(s)

    assert attrs["linear_actual_positions"] == attrs["actual_positions"]


def test_linear_actual_positions_absent_without_snapshot_positions() -> None:
    """Mirrors ``actual_positions`` — no snapshot positions, no attribute."""
    s = _make_sensor(options={CONF_INVERSE_STATE: True}, positions=None)
    attrs = _cover_position_attrs(s)

    assert "actual_positions" not in attrs
    assert "linear_actual_positions" not in attrs
