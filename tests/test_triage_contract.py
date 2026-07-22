"""Contract tests: triage rules vs. the REAL DiagnosticsBuilder output.

The seed rule checks in ``diagnostics/triage.py`` were written partly from the
epic's spec. A hand-rolled fixture would be self-referential — it would agree
with the rule by construction and rot silently against ``builder.py``. These
tests instead drive the *actual* ``DiagnosticsBuilder`` and assert the rules
fire on its output, so any key-path drift between a rule's check and the real
payload surfaces here. No stored JSON fixture.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.adaptive_cover_pro.const import (
    CONF_OUTSIDETEMP_ENTITY,
    ControlMethod,
    TriageCode,
)
from custom_components.adaptive_cover_pro.diagnostics.builder import DiagnosticsBuilder
from custom_components.adaptive_cover_pro.diagnostics.triage import run_triage
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
    ClimateCoverData,
)

# Reuse the builder test's context/pipeline factories — the same shapes the real
# coordinator feeds the builder — rather than duplicating them.
from tests.test_diagnostics.test_builder import _base_ctx, _make_pr


def _codes(findings):
    return {f.reason.code for f in findings}


def _climate_data(*, inside_temperature, outside_temperature=None):
    return ClimateCoverData(
        temp_low=20.0,
        temp_high=25.0,
        temp_switch=False,
        policy=get_policy("cover_blind"),
        transparent_blind=False,
        temp_summer_outside=22.5,
        outside_temperature=outside_temperature,
        inside_temperature=inside_temperature,
        is_presence=True,
        is_sunny=True,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        winter_close_insulation=False,
    )


# ---------------------------------------------------------------------------
# (i) run_triage tolerates a full real payload
# ---------------------------------------------------------------------------


def test_run_triage_never_raises_on_full_real_payload():
    """A complete real builder payload folded with options runs cleanly."""
    diag, _ = DiagnosticsBuilder().build(_base_ctx())
    findings = run_triage({"options": {}, **diag})
    assert isinstance(findings, list)


# ---------------------------------------------------------------------------
# (ii) rule 4 CLIMATE_TEMP_NONE fires on real climate-mode output
# ---------------------------------------------------------------------------


def test_climate_temp_none_fires_on_real_builder_output():
    """inside_temperature None under climate mode → CLIMATE_TEMP_NONE.

    Proves rule 4's ``temperature_details.inside_temperature`` key path matches
    what the builder actually emits (builder.py:681).
    """
    cd = _climate_data(inside_temperature=None)
    pr = _make_pr(control_method=ControlMethod.WINTER, climate_data=cd)
    diag, _ = DiagnosticsBuilder().build(
        _base_ctx(climate_mode=True, pipeline_result=pr)
    )

    # Sanity: the builder really did emit the section the rule reads.
    assert diag["temperature_details"]["inside_temperature"] is None

    findings = run_triage({"options": {}, **diag})
    assert TriageCode.CLIMATE_TEMP_NONE in _codes(findings)


def test_climate_temp_none_does_not_fire_when_temp_present():
    """A present inside temperature must NOT fire rule 4 (guards a false positive)."""
    cd = _climate_data(inside_temperature="21.5")
    pr = _make_pr(control_method=ControlMethod.WINTER, climate_data=cd)
    diag, _ = DiagnosticsBuilder().build(
        _base_ctx(climate_mode=True, pipeline_result=pr)
    )
    findings = run_triage({"options": {}, **diag})
    assert TriageCode.CLIMATE_TEMP_NONE not in _codes(findings)


# ---------------------------------------------------------------------------
# (iii) rule 8b ENTITY_UNAVAILABLE fires on a real unavailable sensor
# ---------------------------------------------------------------------------


def test_entity_unavailable_fires_on_real_builder_output():
    """A shared sensor resolving to 'unavailable' → ENTITY_UNAVAILABLE.

    Proves rule 8b's ``local_sensors[].state``/``entity_id`` key paths match the
    builder's SensorSource descriptors (builder.py:1030-1037).
    """
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda eid: SimpleNamespace(state="unavailable", attributes={})
        ),
    )
    diag, _ = DiagnosticsBuilder().build(
        _base_ctx(
            hass=hass,
            config_options={CONF_OUTSIDETEMP_ENTITY: "sensor.outdoor_temp"},
        )
    )

    # Sanity: the builder emitted an unavailable descriptor with the keys the
    # rule reads.
    descriptors = diag["local_sensors"]
    assert any(
        d.get("state") == "unavailable" and d.get("entity_id") == "sensor.outdoor_temp"
        for d in descriptors
    )

    findings = run_triage({"options": {}, **diag})
    unavailable = [
        f for f in findings if f.reason.code == TriageCode.ENTITY_UNAVAILABLE
    ]
    assert unavailable
    assert any(f.reason.params.get("eid") == "sensor.outdoor_temp" for f in unavailable)
