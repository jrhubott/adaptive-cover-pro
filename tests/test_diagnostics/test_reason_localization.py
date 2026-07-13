"""Localization of the diagnostics builder's reason strings (issue #882, step 9b).

The builder renders ``position_explanation`` and ``control_state_reason`` through
the injected ``DiagnosticContext.reason_labels`` overlay (falling back to English
per key). With ``reason_labels=None`` the output stays byte-identical to the
legacy English literals; with a translated overlay it renders in that language.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.adaptive_cover_pro.const import (
    ClimateStrategy,
    ControlMethod,
    ReasonCode,
)
from custom_components.adaptive_cover_pro.diagnostics.builder import (
    DiagnosticContext,
    DiagnosticsBuilder,
)
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult
from custom_components.adaptive_cover_pro.reason_i18n import Reason

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cover(*, control_state_reason: str = "Sun in FOV", **code):
    """Minimal cover mock; optionally carries a ``control_state_reason_code``."""
    ns = SimpleNamespace(control_state_reason=control_state_reason)
    if "control_state_reason_code" in code:
        ns.control_state_reason_code = code["control_state_reason_code"]
    return ns


def _ctx(**overrides) -> DiagnosticContext:
    defaults = {  # noqa: C408
        "pos_sun": [180.0, 45.0],
        "cover": _make_cover(),
        "pipeline_result": None,
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
        "final_state": 50,
        "config_options": {},
        "motion_detected": True,
        "motion_timeout_active": False,
    }
    defaults.update(overrides)
    return DiagnosticContext(**defaults)


# A small hand-written German overlay (only the keys these tests exercise).
_FAKE_DE = {
    ReasonCode.SOLAR_TRACKING: "Sonne im Sichtfeld — Position {position}%{suffix}",
    ReasonCode.BUILDER_INTERPOLATED: "interpoliert → {final}%",
    ReasonCode.BUILDER_INVERSED: "invertiert → {final}%",
    ReasonCode.BUILDER_OUTSIDE_WINDOW: (
        "Außerhalb des Zeitfensters → {pos_label} {pos}% (Befehle pausiert)"
    ),
    ReasonCode.FRAGMENT_SUNSET_POSITION: "Sonnenuntergangsposition",
    ReasonCode.FRAGMENT_DEFAULT_POSITION: "Standardposition",
    ReasonCode.BUILDER_CONTROL_MANUAL_OVERRIDE: "Manuelle Übersteuerung",
    ReasonCode.BUILDER_CONTROL_OCCUPANCY_TIMEOUT: "Anwesenheits-Timeout",
    ReasonCode.BUILDER_CONTROL_TRACKING_OFF_SEASON: (
        "Standard: Nachführung in dieser Jahreszeit aus"
    ),
    ReasonCode.BUILDER_UNKNOWN: "Unbekannt",
    ReasonCode.BUILDER_CONTROL_TILT_FIXED: "{reason} — Neigung fest durch Custom #{slot}",
    ReasonCode.BUILDER_MANUAL_DIVERGENCE: (
        "manuelle Übersteuerung aktiv — halte {held}% (Solar wäre {raw}%)"
    ),
    ReasonCode.BUILDER_TILT_FIXED: "Neigung fest bei {tilt}% durch Custom #{slot}",
    ReasonCode.ENGINE_DIRECT_SUN: "Direkte Sonne",
}


# ---------------------------------------------------------------------------
# position_explanation — DE
# ---------------------------------------------------------------------------


def test_position_explanation_base_renders_from_payload_in_de() -> None:
    pr = PipelineResult(
        position=72,
        control_method=ControlMethod.SOLAR,
        reason_payload=Reason(
            ReasonCode.SOLAR_TRACKING, {"position": 72, "suffix": ""}
        ),
        raw_calculated_position=72,
    )
    ctx = _ctx(
        pipeline_result=pr,
        reason_labels=_FAKE_DE,
        use_interpolation=True,
        final_state=65,
    )
    result = DiagnosticsBuilder._build_position_explanation(ctx)
    assert result == "Sonne im Sichtfeld — Position 72% → interpoliert → 65%"


def test_position_explanation_outside_window_renders_de_with_fragment() -> None:
    pr = PipelineResult(
        position=30,
        control_method=ControlMethod.DEFAULT,
        default_position=30,
        is_sunset_active=True,
    )
    ctx = _ctx(pipeline_result=pr, check_adaptive_time=False, reason_labels=_FAKE_DE)
    result = DiagnosticsBuilder._build_position_explanation(ctx)
    assert result == (
        "Außerhalb des Zeitfensters → Sonnenuntergangsposition 30% (Befehle pausiert)"
    )


def test_position_explanation_unknown_de() -> None:
    """A None pipeline result localizes the ``Unknown`` fallback (issue #882)."""
    ctx = _ctx(pipeline_result=None, reason_labels=_FAKE_DE)
    assert DiagnosticsBuilder._build_position_explanation(ctx) == "Unbekannt"


def test_position_explanation_manual_divergence_and_inverse_de() -> None:
    pr = PipelineResult(
        position=72,
        control_method=ControlMethod.MANUAL,
        reason_payload=Reason(
            ReasonCode.SOLAR_TRACKING, {"position": 72, "suffix": ""}
        ),
        raw_calculated_position=72,
        held_position=40,
    )
    ctx = _ctx(
        pipeline_result=pr,
        reason_labels=_FAKE_DE,
        inverse_state=True,
        final_state=28,
    )
    result = DiagnosticsBuilder._build_position_explanation(ctx)
    assert result == (
        "Sonne im Sichtfeld — Position 72% → "
        "manuelle Übersteuerung aktiv — halte 40% (Solar wäre 72%) → "
        "invertiert → 28%"
    )


# ---------------------------------------------------------------------------
# control_state_reason — DE
# ---------------------------------------------------------------------------


def test_control_state_reason_manual_override_de() -> None:
    pr = PipelineResult(position=50, control_method=ControlMethod.MANUAL)
    ctx = _ctx(pipeline_result=pr, reason_labels=_FAKE_DE)
    assert DiagnosticsBuilder._get_control_state_reason(ctx) == "Manuelle Übersteuerung"


def test_control_state_reason_tracking_off_season_de() -> None:
    pr = PipelineResult(
        position=50,
        control_method=ControlMethod.DEFAULT,
        climate_strategy=ClimateStrategy.TRACKING_SEASON_GATE,
    )
    ctx = _ctx(pipeline_result=pr, reason_labels=_FAKE_DE)
    assert DiagnosticsBuilder._get_control_state_reason(ctx) == (
        "Standard: Nachführung in dieser Jahreszeit aus"
    )


def test_control_state_reason_engine_code_de() -> None:
    """When the cover exposes a code, the engine control state localizes too."""
    pr = PipelineResult(position=50, control_method=ControlMethod.SOLAR)
    cover = _make_cover(control_state_reason_code=ReasonCode.ENGINE_DIRECT_SUN)
    ctx = _ctx(pipeline_result=pr, cover=cover, reason_labels=_FAKE_DE)
    assert DiagnosticsBuilder._get_control_state_reason(ctx) == "Direkte Sonne"


def test_control_state_reason_tilt_suffix_de() -> None:
    pr = PipelineResult(
        position=50, control_method=ControlMethod.MANUAL, tilt_only_slot=2
    )
    ctx = _ctx(pipeline_result=pr, reason_labels=_FAKE_DE)
    assert DiagnosticsBuilder._get_control_state_reason(ctx) == (
        "Manuelle Übersteuerung — Neigung fest durch Custom #2"
    )


def test_control_state_reason_unknown_de() -> None:
    ctx = _ctx(pipeline_result=None, cover=None, reason_labels=_FAKE_DE)
    assert DiagnosticsBuilder._get_control_state_reason(ctx) == "Unbekannt"


# ---------------------------------------------------------------------------
# EN byte-identical (reason_labels=None → English)
# ---------------------------------------------------------------------------


def test_control_state_reason_manual_override_en_byte_identical() -> None:
    pr = PipelineResult(position=50, control_method=ControlMethod.MANUAL)
    assert DiagnosticsBuilder._get_control_state_reason(_ctx(pipeline_result=pr)) == (
        "Manual Override"
    )


def test_control_state_reason_occupancy_en_byte_identical() -> None:
    pr = PipelineResult(position=50, control_method=ControlMethod.MOTION)
    assert DiagnosticsBuilder._get_control_state_reason(_ctx(pipeline_result=pr)) == (
        "Occupancy Timeout"
    )


def test_control_state_reason_unknown_en_byte_identical() -> None:
    assert (
        DiagnosticsBuilder._get_control_state_reason(
            _ctx(pipeline_result=None, cover=None)
        )
        == "Unknown"
    )


def test_control_state_reason_cover_prose_passthrough_en() -> None:
    """A cover without a code attr falls back to its prose (legacy mocks)."""
    pr = PipelineResult(position=50, control_method=ControlMethod.SOLAR)
    ctx = _ctx(pipeline_result=pr, cover=_make_cover(control_state_reason="Sun in FOV"))
    assert DiagnosticsBuilder._get_control_state_reason(ctx) == "Sun in FOV"


def test_control_state_reason_tilt_suffix_en_byte_identical() -> None:
    pr = PipelineResult(
        position=50, control_method=ControlMethod.MANUAL, tilt_only_slot=2
    )
    assert DiagnosticsBuilder._get_control_state_reason(_ctx(pipeline_result=pr)) == (
        "Manual Override — tilt fixed by Custom #2"
    )


def test_position_explanation_outside_window_en_byte_identical() -> None:
    pr = PipelineResult(
        position=30,
        control_method=ControlMethod.DEFAULT,
        default_position=30,
        is_sunset_active=True,
    )
    ctx = _ctx(pipeline_result=pr, check_adaptive_time=False)
    assert DiagnosticsBuilder._build_position_explanation(ctx) == (
        "Outside time window → sunset position 30% (commands paused)"
    )


def test_position_explanation_unknown_en_byte_identical() -> None:
    """The None-result fallback stays exactly ``Unknown`` with EN labels."""
    ctx = _ctx(pipeline_result=None)
    assert DiagnosticsBuilder._build_position_explanation(ctx) == "Unknown"


def test_position_explanation_base_payload_en_byte_identical() -> None:
    pr = PipelineResult(
        position=72,
        control_method=ControlMethod.SOLAR,
        reason_payload=Reason(
            ReasonCode.SOLAR_TRACKING, {"position": 72, "suffix": ""}
        ),
        raw_calculated_position=72,
    )
    ctx = _ctx(pipeline_result=pr, use_interpolation=True, final_state=65)
    assert DiagnosticsBuilder._build_position_explanation(ctx) == (
        "sun within acceptance angle — position 72% → interpolated → 65%"
    )


def test_position_explanation_legacy_reason_without_payload_en() -> None:
    """A result carrying only a legacy ``reason`` string still renders it verbatim."""
    pr = PipelineResult(
        position=65,
        control_method=ControlMethod.SOLAR,
        reason="sun in FOV — position 65%",
        raw_calculated_position=65,
    )
    ctx = _ctx(pipeline_result=pr)
    assert (
        DiagnosticsBuilder._build_position_explanation(ctx)
        == "sun in FOV — position 65%"
    )
