"""Tests for held_position on PipelineResult and ManualOverrideHandler.

Covers the display-contract fix: while manual override is active, the
user-facing "Target Position" sensor must show the cover's actual physical
position, not the solar-handler value the override is shadowing.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


from custom_components.adaptive_cover_pro.diagnostics.builder import (
    DiagnosticContext,
    DiagnosticsBuilder,
)
from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers import ManualOverrideHandler
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult
from custom_components.adaptive_cover_pro.sensor import _cover_position_value

from tests.test_pipeline.conftest import make_snapshot


# ---------------------------------------------------------------------------
# 1. PipelineResult.held_position defaults to None
# ---------------------------------------------------------------------------


def test_pipeline_result_held_position_defaults_to_none() -> None:
    """Construct PipelineResult without held_position; assert .held_position is None."""
    r = PipelineResult(
        position=42,
        control_method=ControlMethod.DEFAULT,
        reason="x",
    )
    assert r.held_position is None


# ---------------------------------------------------------------------------
# 2. ManualOverrideHandler — sun outside FOV branch
# ---------------------------------------------------------------------------


def test_handler_sets_held_position_to_current_when_sun_outside_fov() -> None:
    """Snapshot with override active, sun outside FOV, current_position=100.

    Asserts result.held_position == 100.
    """
    handler = ManualOverrideHandler()
    snap = make_snapshot(
        manual_override_active=True,
        direct_sun_valid=False,
        current_cover_position=100,
    )
    result = handler.evaluate(snap)
    assert result is not None
    assert result.held_position == 100


# ---------------------------------------------------------------------------
# 3. ManualOverrideHandler — sun inside FOV branch
# ---------------------------------------------------------------------------


def test_handler_sets_held_position_to_current_when_sun_inside_fov() -> None:
    """Snapshot with override active, sun in FOV, cover at 50%, solar calc = 20%.

    Asserts result.held_position == 50 (physical position, NOT solar value).
    """
    handler = ManualOverrideHandler()
    snap = make_snapshot(
        manual_override_active=True,
        direct_sun_valid=True,
        calculate_percentage_return=20.0,
        current_cover_position=50,
    )
    result = handler.evaluate(snap)
    assert result is not None
    assert result.held_position == 50


# ---------------------------------------------------------------------------
# 4. ManualOverrideHandler — None current_position (unknown cover state)
# ---------------------------------------------------------------------------


def test_handler_handles_unknown_current_position_gracefully() -> None:
    """Override active, sun outside FOV, current_position=None → held_position is None."""
    handler = ManualOverrideHandler()
    snap = make_snapshot(
        manual_override_active=True,
        direct_sun_valid=False,
        current_cover_position=None,
    )
    result = handler.evaluate(snap)
    assert result is not None
    assert result.held_position is None


# ---------------------------------------------------------------------------
# 5. ManualOverrideHandler — override inactive regression guard
# ---------------------------------------------------------------------------


def test_handler_returns_none_when_override_inactive() -> None:
    """When override is inactive, evaluate() returns None (regression guard)."""
    handler = ManualOverrideHandler()
    snap = make_snapshot(manual_override_active=False)
    assert handler.evaluate(snap) is None


# ---------------------------------------------------------------------------
# 6-9. Sensor helper — _cover_position_value
# ---------------------------------------------------------------------------


def _make_sensor_stub(states: dict) -> MagicMock:
    """Build a minimal mock of _ACPSensor with the given states dict."""
    s = MagicMock()
    s.data.states = states
    return s


def test_sensor_cover_position_value_prefers_held_position() -> None:
    """When held_position is set, _cover_position_value returns it instead of state."""
    s = _make_sensor_stub({"state": 20, "held_position": 100})
    assert _cover_position_value(s) == 100


def test_sensor_cover_position_value_falls_back_when_held_position_absent() -> None:
    """When held_position key is absent, _cover_position_value returns state."""
    s = _make_sensor_stub({"state": 42})
    assert _cover_position_value(s) == 42


def test_sensor_cover_position_value_falls_back_when_held_position_none() -> None:
    """When held_position is None, _cover_position_value falls back to state."""
    s = _make_sensor_stub({"state": 42, "held_position": None})
    assert _cover_position_value(s) == 42


def test_sensor_cover_position_value_handles_held_position_zero() -> None:
    """held_position=0 must be returned (0 is not None — explicit is-not-None check)."""
    s = _make_sensor_stub({"state": 75, "held_position": 0})
    assert _cover_position_value(s) == 0


# ---------------------------------------------------------------------------
# 10. DiagnosticsBuilder — position explanation for manual override divergence
# ---------------------------------------------------------------------------


def _make_pr_manual(
    *,
    position: int = 100,
    held_position: int | None = 100,
    raw_calculated_position: int = 20,
) -> PipelineResult:
    """Build a PipelineResult as if ManualOverrideHandler produced it."""
    return PipelineResult(
        position=position,
        control_method=ControlMethod.MANUAL,
        reason=f"manual override active — holding cover at {position}%",
        raw_calculated_position=raw_calculated_position,
        held_position=held_position,
    )


def _base_ctx(**overrides) -> DiagnosticContext:
    """Return a DiagnosticContext with sensible defaults."""
    defaults = {
        "pos_sun": [180.0, 45.0],
        "cover": SimpleNamespace(
            gamma=10.0,
            valid=True,
            valid_elevation=True,
            is_sun_in_blind_spot=False,
            direct_sun_valid=True,
            sunset_valid=False,
            control_state_reason="Manual Override",
        ),
        "pipeline_result": _make_pr_manual(),
        "climate_mode": False,
        "check_adaptive_time": True,
        "after_start_time": True,
        "before_end_time": True,
        "start_time": None,
        "end_time": None,
        "automatic_control": True,
        "last_cover_action": {},
        "last_skipped_action": {},
        "min_change": 1,
        "time_threshold": 2,
        "switch_mode": False,
        "inverse_state": False,
        "use_interpolation": False,
        "final_state": 100,
        "config_options": {},
        "motion_detected": True,
        "motion_timeout_active": False,
    }
    defaults.update(overrides)
    return DiagnosticContext(**defaults)


def test_diagnostics_explanation_shows_held_vs_solar_divergence() -> None:
    """When override holds at 100% but solar calc is 20%, explanation surfaces both.

    The explanation string must contain:
    - "100" (the held physical position)
    - "20"  (what solar would compute)
    - "manual override" (so the user knows why)
    """
    ctx = _base_ctx(
        pipeline_result=_make_pr_manual(
            position=100,
            held_position=100,
            raw_calculated_position=20,
        )
    )
    explanation = DiagnosticsBuilder._build_position_explanation(ctx)
    assert "100" in explanation
    assert "20" in explanation
    assert "manual override" in explanation.lower()
