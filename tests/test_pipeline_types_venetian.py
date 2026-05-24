"""DecisionStep + PipelineResult dual-axis fields for venetian.

Issue #33 piped tilt through the pipeline output types so the venetian engine
can record its synthesized terminal step in the decision trace, and the
diagnostics builder can surface ``tilt_target`` alongside position.
"""

from __future__ import annotations

from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.types import (
    DecisionStep,
    PipelineResult,
)


def test_decision_step_default_tilt_is_none() -> None:
    """Existing handlers don't emit tilt — they get None by default."""
    step = DecisionStep(handler="solar", matched=True, reason="x", position=42)
    assert step.tilt is None


def test_decision_step_accepts_tilt() -> None:
    """Venetian terminal step records both axes."""
    step = DecisionStep(
        handler="venetian_engine",
        matched=True,
        reason="slat angle for position 60% — tilt 80%",
        position=60,
        tilt=80,
    )
    assert step.tilt == 80


def test_pipeline_result_default_tilt_is_none() -> None:
    """Non-venetian results carry tilt=None — diagnostics suppresses the key."""
    result = PipelineResult(
        position=50,
        control_method=ControlMethod.SOLAR,
        reason="sun in FOV",
    )
    assert result.tilt is None


def test_pipeline_result_carries_tilt_after_replace() -> None:
    """Coordinator's post-pipeline tilt fill via dataclasses.replace propagates tilt."""
    import dataclasses

    base = PipelineResult(
        position=60, control_method=ControlMethod.SOLAR, reason="sun in FOV"
    )
    enriched = dataclasses.replace(base, tilt=80)
    assert enriched.tilt == 80
    assert enriched.position == 60
    assert enriched.reason == "sun in FOV"
