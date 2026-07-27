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
    CONF_ENDPOINT_USE_OPEN_CLOSE,
    CONF_OUTSIDETEMP_ENTITY,
    POSITION_OPEN,
    ControlMethod,
    TriageCode,
)
from custom_components.adaptive_cover_pro.diagnostics.builder import DiagnosticsBuilder
from custom_components.adaptive_cover_pro.diagnostics.triage import run_triage
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
    ClimateCoverData,
)
from custom_components.adaptive_cover_pro.pipeline.types import DecisionStep

# Reuse the builder test's context/pipeline factories — the same shapes the real
# coordinator feeds the builder — rather than duplicating them.
from tests.test_diagnostics.test_builder import _base_ctx, _make_cover, _make_pr


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


# ---------------------------------------------------------------------------
# (iv) rule 17 DRY_RUN_LEFT_ON fires on a real dry-run debug_config
# ---------------------------------------------------------------------------


def test_dry_run_left_on_fires_on_real_builder_output():
    """A ``debug_config.dry_run`` True → DRY_RUN_LEFT_ON.

    Proves rule 17's ``debug_config.dry_run`` key path matches the builder
    (builder.py:736).
    """
    diag, _ = DiagnosticsBuilder().build(_base_ctx(debug_config={"dry_run": True}))
    assert diag["debug_config"]["dry_run"] is True
    findings = run_triage({"options": {}, **diag})
    assert TriageCode.DRY_RUN_LEFT_ON in _codes(findings)


def test_dry_run_left_on_does_not_fire_when_off():
    diag, _ = DiagnosticsBuilder().build(_base_ctx(debug_config={"dry_run": False}))
    findings = run_triage({"options": {}, **diag})
    assert TriageCode.DRY_RUN_LEFT_ON not in _codes(findings)


# ---------------------------------------------------------------------------
# (v) rule 21 OVERRIDE_BLOCKED_AUTO_OFF fires when automatic control is off
# ---------------------------------------------------------------------------


def test_override_blocked_auto_off_fires_on_real_builder_output():
    """``automatic_control`` False → control_status ``automatic_control_off``.

    Proves rule 21's ``control_status`` key path and value match the builder
    (builder.py:416-419).
    """
    diag, _ = DiagnosticsBuilder().build(_base_ctx(automatic_control=False))
    assert diag["control_status"] == "automatic_control_off"
    findings = run_triage({"options": {}, **diag})
    assert TriageCode.OVERRIDE_BLOCKED_AUTO_OFF in _codes(findings)


def test_override_blocked_auto_off_does_not_fire_when_active():
    diag, _ = DiagnosticsBuilder().build(_base_ctx(automatic_control=True))
    findings = run_triage({"options": {}, **diag})
    assert TriageCode.OVERRIDE_BLOCKED_AUTO_OFF not in _codes(findings)


# ---------------------------------------------------------------------------
# (vi) rule 11 AZIMUTH_FOV_MISMATCH fires on real sun_validity + default winner
# ---------------------------------------------------------------------------


def test_azimuth_fov_mismatch_fires_on_real_builder_output():
    """Sun above the horizon, outside the FOV, default handler won → mismatch.

    Proves rule 11's ``sun_validity.in_fov`` / ``valid_elevation`` and the
    matched-``default`` decision-trace key paths match the builder
    (builder.py:629-638, 792-808).
    """
    cover = _make_cover(in_fov=False, valid_elevation=True, direct_sun_valid=False)
    pr = _make_pr()
    object.__setattr__(
        pr,
        "decision_trace",
        [DecisionStep(handler="default", matched=True, position=0)],
    )
    diag, _ = DiagnosticsBuilder().build(_base_ctx(cover=cover, pipeline_result=pr))

    # Sanity: the builder emitted the exact keys/values the rule reads.
    assert diag["sun_validity"]["in_fov"] is False
    assert diag["sun_validity"]["valid_elevation"] is True
    assert diag["decision_trace"][0]["handler"] == "default"
    assert diag["decision_trace"][0]["matched"] is True

    findings = run_triage({"options": {}, **diag})
    assert TriageCode.AZIMUTH_FOV_MISMATCH in _codes(findings)


def test_azimuth_fov_mismatch_does_not_fire_when_in_fov():
    cover = _make_cover(in_fov=True, valid_elevation=True)
    pr = _make_pr()
    object.__setattr__(
        pr,
        "decision_trace",
        [DecisionStep(handler="default", matched=True, position=0)],
    )
    diag, _ = DiagnosticsBuilder().build(_base_ctx(cover=cover, pipeline_result=pr))
    findings = run_triage({"options": {}, **diag})
    assert TriageCode.AZIMUTH_FOV_MISMATCH not in _codes(findings)


# ---------------------------------------------------------------------------
# (vii) rule 20 skip trio fires on a real recorded skipped action
# ---------------------------------------------------------------------------


def test_skip_service_call_failed_fires_on_real_builder_output():
    """A recorded ``last_skipped_action`` with a fault reason → skip finding.

    Proves rule 20's ``last_skipped_action.reason``/``entity_id``/``timestamp``
    and ``data_window.captured_at`` key paths match the builder
    (builder.py:704-712, 714-728).
    """
    skipped = {
        "entity_id": "cover.a",
        "reason": "service_call_failed",
        "timestamp": "2026-07-22T10:00:00+00:00",
    }
    diag, _ = DiagnosticsBuilder().build(_base_ctx(last_skipped_action=skipped))

    # Sanity: the builder emitted the section + the data window the rule reads.
    assert diag["last_skipped_action"]["reason"] == "service_call_failed"
    assert "captured_at" in diag["data_window"]

    findings = run_triage({"options": {}, **diag})
    assert TriageCode.SKIP_SERVICE_CALL_FAILED in _codes(findings)


def test_benign_skip_reason_produces_no_skip_finding_on_real_output():
    skipped = {
        "entity_id": "cover.a",
        "reason": "delta_too_small",
        "timestamp": "2026-07-22T10:00:00+00:00",
    }
    diag, _ = DiagnosticsBuilder().build(_base_ctx(last_skipped_action=skipped))
    findings = run_triage({"options": {}, **diag})
    assert not any(f.reason.code.startswith("triage.skip_") for f in findings)


# ---------------------------------------------------------------------------
# (viii) rule 14 MIXED_TEMP_UNITS fires on real inside/outside unit descriptors
# ---------------------------------------------------------------------------


def _units_hass(units):
    """Return a hass stand-in whose states report the given per-entity units."""

    def get(eid):
        if eid in units:
            return SimpleNamespace(
                state="21.0", attributes={"unit_of_measurement": units[eid]}
            )
        return None

    return SimpleNamespace(states=SimpleNamespace(get=get))


def test_mixed_temp_units_fires_on_real_builder_output():
    """Different inside (°F) / outside (°C) units → MIXED_TEMP_UNITS.

    Proves rule 14's key paths — ``temp_sensor.unit_of_measurement`` for the
    inside sensor (builder.py:658) and the ``outside_temp`` SensorSource
    descriptor's ``unit_of_measurement`` in ``local_sensors`` (builder.py:1035)
    — match what the builder actually emits.
    """
    hass = _units_hass({"sensor.inside_temp": "°F", "sensor.outdoor_temp": "°C"})
    diag, _ = DiagnosticsBuilder().build(
        _base_ctx(
            hass=hass,
            temp_sensor_entity_id="sensor.inside_temp",
            temp_sensor_source="area",
            temp_sensor_area_id="area_x",
            config_options={CONF_OUTSIDETEMP_ENTITY: "sensor.outdoor_temp"},
        )
    )

    # Sanity: the builder emitted the exact keys/values the rule reads.
    assert diag["temp_sensor"]["unit_of_measurement"] == "°F"
    assert any(
        d.get("key") == "outside_temp" and d.get("unit_of_measurement") == "°C"
        for d in diag["local_sensors"]
    )

    findings = run_triage({"options": {}, **diag})
    assert TriageCode.MIXED_TEMP_UNITS in _codes(findings)


def test_mixed_temp_units_does_not_fire_when_units_match():
    hass = _units_hass({"sensor.inside_temp": "°C", "sensor.outdoor_temp": "°C"})
    diag, _ = DiagnosticsBuilder().build(
        _base_ctx(
            hass=hass,
            temp_sensor_entity_id="sensor.inside_temp",
            temp_sensor_source="area",
            temp_sensor_area_id="area_x",
            config_options={CONF_OUTSIDETEMP_ENTITY: "sensor.outdoor_temp"},
        )
    )
    findings = run_triage({"options": {}, **diag})
    assert TriageCode.MIXED_TEMP_UNITS not in _codes(findings)


# ---------------------------------------------------------------------------
# (ix) rule 25 ENDPOINT_POSITION_NOT_TRACKING fires on real covers/cover_commands
# ---------------------------------------------------------------------------


def _endpoint_covers(current_position: int) -> dict:
    return {
        "cover.a": {
            "current_position": current_position,
            "available": True,
            "transit_state": None,
            "ha_state": "open",
            "capabilities": {
                "has_set_position": True,
                "has_set_tilt_position": False,
                "has_open": True,
                "has_close": True,
            },
        }
    }


def _endpoint_command_state() -> dict:
    return {
        "cover.a": {
            "target_call": POSITION_OPEN,
            "wait_for_target": True,
            "retry_count": 0,
            "gave_up": False,
        }
    }


def test_endpoint_position_not_tracking_fires_on_real_builder_output():
    """A stuck-at-0 cover claiming "open" under endpoint routing → the new rule.

    Proves rule 25's RUNTIME key path — ``covers[eid]["ha_state"]`` (the field
    added to ``coordinator.build_diagnostic_data()`` for issue #1026) — survives
    the real ``DiagnosticsBuilder`` passthrough, not just a hand-built dict.
    """
    diag, _ = DiagnosticsBuilder().build(
        _base_ctx(
            covers=_endpoint_covers(current_position=0),
            cover_command_state=_endpoint_command_state(),
        )
    )

    # Sanity: the builder really did emit the keys the rule reads.
    assert diag["covers"]["cover.a"]["ha_state"] == "open"
    assert diag["cover_commands"]["cover.a"]["target_call"] == POSITION_OPEN

    findings = run_triage({"options": {CONF_ENDPOINT_USE_OPEN_CLOSE: True}, **diag})
    assert TriageCode.ENDPOINT_POSITION_NOT_TRACKING in _codes(findings)


def test_endpoint_position_not_tracking_does_not_fire_when_position_tracks():
    diag, _ = DiagnosticsBuilder().build(
        _base_ctx(
            covers=_endpoint_covers(current_position=100),
            cover_command_state=_endpoint_command_state(),
        )
    )
    findings = run_triage({"options": {CONF_ENDPOINT_USE_OPEN_CLOSE: True}, **diag})
    assert TriageCode.ENDPOINT_POSITION_NOT_TRACKING not in _codes(findings)
