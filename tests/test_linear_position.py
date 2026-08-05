"""Cover_Position sensor surfaces `linear_position` (issue #911).

`linear_position` mirrors the diagnostics field of the same name onto the
Cover_Position sensor so the companion card can show the pre-interpolation
logical target instead of the interpolated motor value.
"""

from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.sensor import _cover_position_attrs


def _make_sensor(diagnostics: dict) -> MagicMock:
    """Minimal MagicMock sensor exercising only the diagnostics branch."""
    s = MagicMock()
    s.data.attributes = {}
    s.data.states = {"control": "solar", "state": 31, "held_position": None}
    s.coordinator._pipeline_result = None
    s.coordinator.data.diagnostics = diagnostics
    s.coordinator._cmd_svc.transit_states.return_value = {}
    s.coordinator._snapshot = None
    # lift_travel_metres None -> _compute_distance_attrs returns None (skip).
    s.coordinator._policy.lift_travel_metres.return_value = None
    return s


def test_linear_position_mirrored_from_diagnostics():
    """The sensor copies diagnostics['linear_position'] onto its attributes."""
    s = _make_sensor({"linear_position": 10, "calculated_position": 40})
    attrs = _cover_position_attrs(s)
    assert attrs["linear_position"] == 10


def test_linear_position_distinct_from_state():
    """linear_position (10) is exposed alongside the interpolated state (31)."""
    s = _make_sensor({"linear_position": 10, "calculated_position": 40})
    attrs = _cover_position_attrs(s)
    assert attrs["linear_position"] == 10
    assert s.data.states["state"] == 31


def test_linear_position_none_when_absent():
    """Missing diagnostics key -> attribute present as None (backwards safe)."""
    s = _make_sensor({"calculated_position": 40})
    attrs = _cover_position_attrs(s)
    assert attrs["linear_position"] is None
