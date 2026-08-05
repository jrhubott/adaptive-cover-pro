"""Sensor-side reason localization + card code/params attrs (issue #882, step 9c).

The decision_trace sensor renders the winner ``reason`` and each trace step's
``reason`` through the coordinator's primed instance-language labels
(``coordinator._reason_labels``). It additionally exposes, on the winner and on
every trace step, JSON-serializable ``reason_code`` + ``reason_params``
attributes so the companion card can localize with its own templates. On an EN
install (``_reason_labels`` None or English) the ``reason`` strings stay
byte-identical.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.const import (
    CONF_SENSOR_TYPE,
    ControlMethod,
    CoverType,
    ReasonCode,
)
from custom_components.adaptive_cover_pro.pipeline.types import (
    DecisionStep,
    PipelineResult,
)
from custom_components.adaptive_cover_pro.reason_i18n import Reason
from custom_components.adaptive_cover_pro.sensor import AdaptiveCoverDecisionTraceSensor


def _make_hass():
    hass = MagicMock()
    hass.config.units.temperature_unit = "°C"
    return hass


def _make_config_entry(options: dict | None = None):
    entry = MagicMock()
    entry.entry_id = "reason_loc_entry"
    entry.data = {"name": "Test", CONF_SENSOR_TYPE: CoverType.BLIND}
    entry.options = options or {}
    return entry


def _make_coordinator(result, reason_labels):
    coord = MagicMock()
    coord._pipeline_result = result
    coord._reason_labels = reason_labels
    coord.logger = MagicMock()
    coord.hass = _make_hass()
    coord.check_adaptive_time = True
    coord.data = None
    coord._cover_data = None
    return coord


def _make_sensor(result, reason_labels):
    return AdaptiveCoverDecisionTraceSensor(
        "reason_loc_entry",
        _make_hass(),
        _make_config_entry(),
        "Test",
        _make_coordinator(result, reason_labels),
    )


def _solar_result():
    payload = Reason(ReasonCode.SOLAR_TRACKING, {"position": 72, "suffix": ""})
    return PipelineResult(
        position=72,
        control_method=ControlMethod.SOLAR,
        reason_payload=payload,
        raw_calculated_position=72,
        decision_trace=[
            DecisionStep(
                handler="solar",
                matched=True,
                reason_payload=payload,
                position=72,
            )
        ],
    )


_FAKE_DE = {ReasonCode.SOLAR_TRACKING: "Sonne — Position {position}%{suffix}"}


# ---------------------------------------------------------------------------
# DE localization
# ---------------------------------------------------------------------------


def test_decision_trace_reason_localizes_de() -> None:
    sensor = _make_sensor(_solar_result(), _FAKE_DE)
    attrs = sensor.extra_state_attributes or {}
    assert attrs["reason"] == "Sonne — Position 72%"
    assert attrs["trace"][0]["reason"] == "Sonne — Position 72%"


def test_decision_trace_exposes_reason_code_and_params() -> None:
    sensor = _make_sensor(_solar_result(), _FAKE_DE)
    attrs = sensor.extra_state_attributes or {}
    step = attrs["trace"][0]
    assert step["reason_code"] == "solar.tracking"
    assert step["reason_params"] == {"position": 72, "suffix": ""}
    # Winner also carries code + params.
    assert attrs["reason_code"] == "solar.tracking"
    assert attrs["reason_params"] == {"position": 72, "suffix": ""}
    # Everything must be JSON serializable for the card.
    json.dumps({"trace": attrs["trace"], "reason_params": attrs["reason_params"]})


# ---------------------------------------------------------------------------
# EN byte-identical
# ---------------------------------------------------------------------------


def test_decision_trace_reason_en_byte_identical() -> None:
    sensor = _make_sensor(_solar_result(), None)
    attrs = sensor.extra_state_attributes or {}
    assert attrs["reason"] == "sun within acceptance angle — position 72%"
    assert attrs["trace"][0]["reason"] == "sun within acceptance angle — position 72%"
    # Card attrs are still present on an EN install (additive).
    assert attrs["trace"][0]["reason_code"] == "solar.tracking"


def test_decision_trace_legacy_step_without_payload_en() -> None:
    """A legacy step carrying only a ``reason`` string keeps it and omits code."""
    result = PipelineResult(
        position=50,
        control_method=ControlMethod.SOLAR,
        reason="sun in FOV — position 50%",
        decision_trace=[
            DecisionStep(
                handler="solar",
                matched=True,
                reason="sun in FOV — position 50%",
                position=50,
            )
        ],
    )
    sensor = _make_sensor(result, None)
    attrs = sensor.extra_state_attributes or {}
    assert attrs["trace"][0]["reason"] == "sun in FOV — position 50%"
    assert "reason_code" not in attrs["trace"][0]
