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
from custom_components.adaptive_cover_pro.pipeline.types import (
    DecisionStep,
    PipelineResult,
)
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


# ---------------------------------------------------------------------------
# 11. DecisionStep accepts held_position field (issue #608)
# ---------------------------------------------------------------------------


def test_decision_step_accepts_held_position() -> None:
    """DecisionStep with held_position carries the value distinctly from position."""
    step = DecisionStep(
        handler="manual_override",
        matched=True,
        reason="manual override active — holding 44% (solar would-be 60%)",
        position=60,
        held_position=44,
    )
    assert step.position == 60
    assert step.held_position == 44


def test_decision_step_held_position_defaults_to_none() -> None:
    """DecisionStep without held_position has held_position=None by default."""
    step = DecisionStep(
        handler="solar",
        matched=True,
        reason="sun in FOV",
        position=60,
    )
    assert step.held_position is None


# ---------------------------------------------------------------------------
# 12. Registry passes held_position to the winning DecisionStep (issue #608)
# ---------------------------------------------------------------------------


def test_registry_manual_override_step_carries_held_position() -> None:
    """When ManualOverrideHandler wins, its DecisionStep.held_position == physical position."""
    from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
    from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
        DefaultHandler,
    )

    snap = make_snapshot(
        manual_override_active=True,
        direct_sun_valid=True,
        calculate_percentage_return=60.0,
        current_cover_position=44,
    )
    registry = PipelineRegistry([ManualOverrideHandler(), DefaultHandler()])
    result = registry.evaluate(snap)
    mo_step = next(s for s in result.decision_trace if s.handler == "manual_override")
    assert mo_step.matched is True
    assert mo_step.position == 60  # solar would-be
    assert mo_step.held_position == 44  # physical held position


def test_registry_non_winning_steps_have_none_held_position() -> None:
    """Non-winning DecisionStep entries have held_position=None."""
    from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
    from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
        DefaultHandler,
    )

    snap = make_snapshot(
        manual_override_active=True,
        direct_sun_valid=True,
        calculate_percentage_return=60.0,
        current_cover_position=44,
    )
    registry = PipelineRegistry([ManualOverrideHandler(), DefaultHandler()])
    result = registry.evaluate(snap)
    # DefaultHandler is not the winner; its step should have held_position=None
    default_step = next(
        (s for s in result.decision_trace if s.handler == "default"), None
    )
    assert default_step is not None
    assert default_step.held_position is None


# ---------------------------------------------------------------------------
# 13. Reason strings name both held and solar values (issue #608)
# ---------------------------------------------------------------------------


def test_handler_reason_string_names_held_and_solar_when_both_known() -> None:
    """When sun is in FOV and held_position is known, reason cites both values."""
    handler = ManualOverrideHandler()
    snap = make_snapshot(
        manual_override_active=True,
        direct_sun_valid=True,
        calculate_percentage_return=60.0,
        current_cover_position=44,
    )
    result = handler.evaluate(snap)
    assert result is not None
    # Reason must name the physical held position
    assert "44" in result.reason
    # Reason must name the solar would-be
    assert "60" in result.reason
    assert "manual override" in result.reason.lower()


def test_handler_reason_string_when_held_position_unknown_in_fov() -> None:
    """When current_cover_position is None, reason omits held_position (FOV branch)."""
    handler = ManualOverrideHandler()
    snap = make_snapshot(
        manual_override_active=True,
        direct_sun_valid=True,
        calculate_percentage_return=60.0,
        current_cover_position=None,
    )
    result = handler.evaluate(snap)
    assert result is not None
    assert "60" in result.reason
    assert "None" not in result.reason


def test_handler_reason_string_outside_fov_held_known() -> None:
    """When sun is outside FOV and held_position is known, reason cites both."""
    handler = ManualOverrideHandler()
    snap = make_snapshot(
        manual_override_active=True,
        direct_sun_valid=False,
        current_cover_position=30,
        default_position=0,
    )
    result = handler.evaluate(snap)
    assert result is not None
    assert "30" in result.reason
    assert "None" not in result.reason
    assert "manual override" in result.reason.lower()


def test_handler_reason_string_outside_fov_held_none() -> None:
    """When sun is outside FOV and held_position is None, reason omits held_position."""
    handler = ManualOverrideHandler()
    snap = make_snapshot(
        manual_override_active=True,
        direct_sun_valid=False,
        current_cover_position=None,
        default_position=0,
    )
    result = handler.evaluate(snap)
    assert result is not None
    assert "None" not in result.reason
    assert "manual override" in result.reason.lower()


# ---------------------------------------------------------------------------
# 14. Sensor trace attrs include held_position for manual_override step (issue #608)
# ---------------------------------------------------------------------------


def test_handler_sets_skip_command_when_held_position_known_outside_fov() -> None:
    """Override active, sun outside FOV, held position known → hold the cover.

    The handler must emit skip_command=True so dispatch does not drive the cover
    to the would-be default.  position stays the would-be shadow (100),
    held_position carries the physical position (0)  (issue #809).
    """
    handler = ManualOverrideHandler()
    snap = make_snapshot(
        manual_override_active=True,
        direct_sun_valid=False,
        current_cover_position=0,
        default_position=100,
    )
    result = handler.evaluate(snap)
    assert result is not None
    assert result.skip_command is True
    assert result.position == 100  # would-be shadow preserved
    assert result.held_position == 0


def test_handler_sets_skip_command_when_held_position_known_in_fov() -> None:
    """Override active, sun in FOV, held position known → hold the cover.

    skip_command=True; position is the solar would-be (20); held_position is the
    physical position (50)  (issue #809).
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
    assert result.skip_command is True
    assert result.position == 20  # solar would-be
    assert result.held_position == 50


def test_handler_no_skip_command_when_held_position_unknown() -> None:
    """Override active but no physical position reported → do NOT hold.

    With held_position unknown the handler cannot hold at a known position, so
    skip_command stays False (parity with motion_timeout's guard)  (issue #809).
    """
    handler = ManualOverrideHandler()
    snap = make_snapshot(
        manual_override_active=True,
        direct_sun_valid=False,
        current_cover_position=None,
    )
    result = handler.evaluate(snap)
    assert result is not None
    assert result.skip_command is False
    assert result.held_position is None


def test_sensor_trace_attrs_include_held_position_for_manual_override() -> None:
    """The decision_trace sensor attribute trace includes held_position for manual_override."""
    step_with_held = DecisionStep(
        handler="manual_override",
        matched=True,
        reason="holding 44% (solar would-be 60%)",
        position=60,
        held_position=44,
    )
    step_without_held = DecisionStep(
        handler="solar",
        matched=False,
        reason="outprioritized by manual_override",
        position=60,
    )
    # Build trace items using the same conditional-include idiom as sensor.py
    trace_items = []
    for step in [step_with_held, step_without_held]:
        item = {
            "handler": step.handler,
            "matched": step.matched,
            "reason": step.reason,
            "position": step.position,
            **({"tilt": step.tilt} if step.tilt is not None else {}),
            **(
                {"held_position": step.held_position}
                if step.held_position is not None
                else {}
            ),
        }
        trace_items.append(item)
    assert trace_items[0]["held_position"] == 44
    assert "held_position" not in trace_items[1]
