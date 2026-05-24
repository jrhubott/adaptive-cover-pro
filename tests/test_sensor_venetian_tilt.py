"""Tests for the Cover_Tilt sensor (venetian-only Target Tilt entity).

Steps 1, 3, 4, 5 from the TDD plan for issue #33 Gap 1.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import CONF_SENSOR_TYPE, CoverType
from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult
from custom_components.adaptive_cover_pro.sensor import _STANDARD_SPECS


def _cover_tilt_spec():
    """Return the Cover_Tilt spec, or raise AssertionError if absent."""
    for spec in _STANDARD_SPECS:
        if spec.suffix == "Cover_Tilt":
            return spec
    raise AssertionError(
        "Cover_Tilt spec not found in _STANDARD_SPECS — has it been added to sensor.py?"
    )


def _make_entry(sensor_type: str):
    entry = MagicMock()
    entry.entry_id = "test_tilt_entry"
    entry.data = {"name": "T", CONF_SENSOR_TYPE: sensor_type}
    entry.options = {}
    return entry


def _make_sensor_stub(pipeline_result):
    """Return a minimal object that value_fn can call."""
    stub = MagicMock()
    stub.coordinator._pipeline_result = pipeline_result
    return stub


@pytest.mark.unit
class TestCoverTiltSpec:
    """Tests for the Cover_Tilt _SensorSpec — existence, gating, and value_fn."""

    def test_spec_exists_in_standard_specs(self):
        """Cover_Tilt must be registered in _STANDARD_SPECS."""
        spec = _cover_tilt_spec()
        assert spec.suffix == "Cover_Tilt"

    def test_enabled_for_venetian(self):
        """enabled_when must return True for cover_venetian config entries."""
        spec = _cover_tilt_spec()
        assert spec.enabled_when(_make_entry(CoverType.VENETIAN)) is True

    def test_not_enabled_for_blind(self):
        """enabled_when must return False for cover_blind."""
        spec = _cover_tilt_spec()
        assert spec.enabled_when(_make_entry(CoverType.BLIND)) is False

    def test_not_enabled_for_awning(self):
        """enabled_when must return False for cover_awning."""
        spec = _cover_tilt_spec()
        assert spec.enabled_when(_make_entry(CoverType.AWNING)) is False

    def test_not_enabled_for_tilt(self):
        """enabled_when must return False for cover_tilt."""
        spec = _cover_tilt_spec()
        assert spec.enabled_when(_make_entry(CoverType.TILT)) is False

    def test_value_fn_returns_tilt_from_pipeline_result(self):
        """value_fn must return the tilt integer from the active pipeline result."""
        spec = _cover_tilt_spec()
        pr = PipelineResult(
            position=50,
            control_method=ControlMethod.SOLAR,
            reason="solar",
            tilt=72,
        )
        sensor_stub = _make_sensor_stub(pr)
        assert spec.value_fn(sensor_stub) == 72

    def test_value_fn_returns_none_when_pipeline_result_is_none(self):
        """value_fn must return None on cold-start (no pipeline result yet)."""
        spec = _cover_tilt_spec()
        sensor_stub = _make_sensor_stub(None)
        assert spec.value_fn(sensor_stub) is None

    def test_value_fn_returns_none_when_tilt_is_none(self):
        """value_fn must return None when PipelineResult.tilt is None."""
        spec = _cover_tilt_spec()
        pr = PipelineResult(
            position=50,
            control_method=ControlMethod.SOLAR,
            reason="solar",
            tilt=None,
        )
        sensor_stub = _make_sensor_stub(pr)
        assert spec.value_fn(sensor_stub) is None

    def test_spec_is_not_diagnostic(self):
        """Cover_Tilt must be a standard (non-diagnostic) sensor."""
        spec = _cover_tilt_spec()
        assert spec.diagnostic is False
