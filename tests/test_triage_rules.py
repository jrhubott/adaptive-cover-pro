"""Per-rule + meta tests for the seed triage rule table (issue #970, Phase 1).

Each seed rule (rows 1,2,3,4,5,6,7,8a,8b,9,10) gets a firing view, a near-miss
view, and — for per-entity rules — an N-entity view. The meta section runs over
``TRIAGE_RULES`` and enforces table invariants plus the CONFIG-determinism lock.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from custom_components.adaptive_cover_pro.const import (
    CUSTOM_POSITION_SAFETY_PRIORITY,
    TriageCode,
)
from custom_components.adaptive_cover_pro.diagnostics.triage import (
    TRIAGE_RULES,
    RuleInput,
    Severity,
    run_triage,
    wiki_anchor_for,
)
from custom_components.adaptive_cover_pro.reason_i18n import Reason, render
from custom_components.adaptive_cover_pro.troubleshoot_i18n import (
    _TRIAGE_TEMPLATES_EN,
    load_troubleshoot_labels,
)
from tests._helpers import i18n_parity

pytestmark = pytest.mark.unit

_RULES_BY_CODE = {rule.code: rule for rule in TRIAGE_RULES}

_TROUBLESHOOT_I18N_DIR = (
    Path(__file__).parent.parent
    / "custom_components"
    / "adaptive_cover_pro"
    / "troubleshoot_i18n"
)


def _fire(code: str, view: dict) -> list:
    """Run only the rule identified by ``code`` against ``view``."""
    return run_triage(view, rules=[_RULES_BY_CODE[code]])


def _params(findings: list) -> list[dict]:
    return [dict(f.reason.params) for f in findings]


# ---------------------------------------------------------------------------
# Rule 1 — CUSTOM_SAFETY_BYPASS
# ---------------------------------------------------------------------------


def test_rule1_fires_on_configured_safety_slot() -> None:
    view = {
        "options": {
            "custom_position_sensor_1": "binary_sensor.trigger",
            "custom_position_1": 50,
            "custom_position_priority_1": CUSTOM_POSITION_SAFETY_PRIORITY,
        }
    }
    findings = _fire(TriageCode.CUSTOM_SAFETY_BYPASS, view)
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].fix_step == "custom_position"
    assert dict(findings[0].reason.params) == {
        "slot": 1,
        "safety": CUSTOM_POSITION_SAFETY_PRIORITY,
    }


def test_rule1_near_miss_priority_below_safety() -> None:
    view = {
        "options": {
            "custom_position_sensor_1": "binary_sensor.trigger",
            "custom_position_1": 50,
            "custom_position_priority_1": 77,
        }
    }
    assert _fire(TriageCode.CUSTOM_SAFETY_BYPASS, view) == []


def test_rule1_near_miss_trigger_without_claim_is_unconfigured() -> None:
    # A safety-priority slot with a trigger but no axis claim is not configured,
    # so it does not participate — matches the summary's slot-skip guard.
    view = {
        "options": {
            "custom_position_sensor_1": "binary_sensor.trigger",
            "custom_position_priority_1": CUSTOM_POSITION_SAFETY_PRIORITY,
        }
    }
    assert _fire(TriageCode.CUSTOM_SAFETY_BYPASS, view) == []


def test_rule1_template_via_sensors_list_key() -> None:
    view = {
        "options": {
            "custom_position_sensors_2": ["binary_sensor.a", "binary_sensor.b"],
            "custom_position_2": 30,
            "custom_position_priority_2": CUSTOM_POSITION_SAFETY_PRIORITY,
        }
    }
    assert _params(_fire(TriageCode.CUSTOM_SAFETY_BYPASS, view)) == [
        {"slot": 2, "safety": CUSTOM_POSITION_SAFETY_PRIORITY}
    ]


# ---------------------------------------------------------------------------
# Rule 2 — HIGHER_PRIORITY_WON
# ---------------------------------------------------------------------------


def test_rule2_fires_when_climate_outranks_solar() -> None:
    view = {
        "decision_trace": [
            {"handler": "climate", "matched": True, "priority": 50},
            {"handler": "solar", "matched": False, "priority": 40},
        ],
        "handler_priorities": {
            "climate": {"priority": 50},
            "solar": {"priority": 40},
        },
    }
    findings = _fire(TriageCode.HIGHER_PRIORITY_WON, view)
    assert len(findings) == 1
    assert findings[0].severity is Severity.INFO
    params = dict(findings[0].reason.params)
    assert params["winner"] == "climate"
    assert params["skipped"] == "solar"


def test_rule2_near_miss_solar_is_winner() -> None:
    view = {
        "decision_trace": [{"handler": "solar", "matched": True, "priority": 40}],
        "handler_priorities": {"solar": {"priority": 40}},
    }
    assert _fire(TriageCode.HIGHER_PRIORITY_WON, view) == []


def test_rule2_near_miss_no_matched_step() -> None:
    view = {
        "decision_trace": [{"handler": "climate", "matched": False}],
        "handler_priorities": {"climate": {"priority": 50}, "solar": {"priority": 40}},
    }
    assert _fire(TriageCode.HIGHER_PRIORITY_WON, view) == []


def test_rule2_near_miss_winner_priority_not_above_solar() -> None:
    # A non-solar winner that does not actually outrank solar (e.g. a floor
    # clamp) must not fire.
    view = {
        "decision_trace": [{"handler": "glare_zone", "matched": True}],
        "handler_priorities": {
            "glare_zone": {"priority": 45},
            "solar": {"priority": 45},
        },
    }
    assert _fire(TriageCode.HIGHER_PRIORITY_WON, view) == []


def test_rule2_near_miss_trace_absent() -> None:
    assert _fire(TriageCode.HIGHER_PRIORITY_WON, {}) == []


# ---------------------------------------------------------------------------
# Rule 3 — TIME_WINDOW_SUSPECT
# ---------------------------------------------------------------------------


def test_rule3_near_miss_blank_time_sentinel_start() -> None:
    # "00:00:00" is BLANK_TIME (UNSET / all-day), not a suspect start — a legacy
    # entry carrying it tracks all day legitimately (issue #970, MINOR 6).
    view = {"options": {"start_time": "00:00:00", "end_time": "20:00:00"}}
    assert _fire(TriageCode.TIME_WINDOW_SUSPECT, view) == []


def test_rule3_near_miss_blank_time_end_not_inverted() -> None:
    # A real start with a BLANK_TIME (unset) end must NOT read as start > end.
    view = {"options": {"start_time": "07:00:00", "end_time": "00:00:00"}}
    assert _fire(TriageCode.TIME_WINDOW_SUSPECT, view) == []


def test_rule3_fires_on_sun_sensor_start_entity() -> None:
    view = {"options": {"start_entity": "sensor.sun_next_dawn"}}
    findings = _fire(TriageCode.TIME_WINDOW_SUSPECT, view)
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].fix_step == "automation"


def test_rule3_sun_sensor_start_with_no_end_does_not_render_none() -> None:
    # MINOR 8: a missing end must not surface as "end None" — the sentinel is used.
    view = {"options": {"start_entity": "sensor.sun_next_dawn"}}
    params = dict(_fire(TriageCode.TIME_WINDOW_SUSPECT, view)[0].reason.params)
    assert params["end"] is not None
    rendered = render(
        _fire(TriageCode.TIME_WINDOW_SUSPECT, view)[0].reason,
        load_troubleshoot_labels("en"),
    )
    assert "None" not in rendered


def test_rule3_fires_when_start_after_end() -> None:
    view = {"options": {"start_time": "21:00:00", "end_time": "08:00:00"}}
    assert len(_fire(TriageCode.TIME_WINDOW_SUSPECT, view)) == 1


def test_rule3_near_miss_sane_window() -> None:
    view = {"options": {"start_time": "07:00:00", "end_time": "20:00:00"}}
    assert _fire(TriageCode.TIME_WINDOW_SUSPECT, view) == []


# ---------------------------------------------------------------------------
# Rule 4 — CLIMATE_TEMP_NONE
# ---------------------------------------------------------------------------


def test_rule4_fires_when_inside_temp_none() -> None:
    view = {"temperature_details": {"inside_temperature": None}}
    findings = _fire(TriageCode.CLIMATE_TEMP_NONE, view)
    assert len(findings) == 1
    assert findings[0].fix_step == "temperature_climate"


def test_rule4_near_miss_temp_present() -> None:
    view = {"temperature_details": {"inside_temperature": 21.0}}
    assert _fire(TriageCode.CLIMATE_TEMP_NONE, view) == []


def test_rule4_near_miss_section_absent() -> None:
    assert _fire(TriageCode.CLIMATE_TEMP_NONE, {}) == []


# ---------------------------------------------------------------------------
# Rule 5 — SUMMER_WONT_CLOSE
# ---------------------------------------------------------------------------


def test_rule5_fires_summer_presence_climate_unmatched() -> None:
    view = {
        "options": {},
        "climate_conditions": {"is_summer": True, "is_presence": True},
        "decision_trace": [{"handler": "climate", "matched": False}],
    }
    findings = _fire(TriageCode.SUMMER_WONT_CLOSE, view)
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING


def test_rule5_near_miss_transparent_blind() -> None:
    view = {
        "options": {"transparent_blind": True},
        "climate_conditions": {"is_summer": True, "is_presence": True},
        "decision_trace": [{"handler": "climate", "matched": False}],
    }
    assert _fire(TriageCode.SUMMER_WONT_CLOSE, view) == []


def test_rule5_near_miss_climate_matched() -> None:
    view = {
        "options": {},
        "climate_conditions": {"is_summer": True, "is_presence": True},
        "decision_trace": [{"handler": "climate", "matched": True}],
    }
    assert _fire(TriageCode.SUMMER_WONT_CLOSE, view) == []


def test_rule5_near_miss_not_summer() -> None:
    view = {
        "options": {},
        "climate_conditions": {"is_summer": False, "is_presence": True},
        "decision_trace": [{"handler": "climate", "matched": False}],
    }
    assert _fire(TriageCode.SUMMER_WONT_CLOSE, view) == []


def test_rule5_near_miss_climate_step_absent() -> None:
    view = {
        "options": {},
        "climate_conditions": {"is_summer": True, "is_presence": True},
        "decision_trace": [{"handler": "solar", "matched": True}],
    }
    assert _fire(TriageCode.SUMMER_WONT_CLOSE, view) == []


def test_rule5_near_miss_decision_trace_absent() -> None:
    view = {
        "options": {},
        "climate_conditions": {"is_summer": True, "is_presence": True},
    }
    assert _fire(TriageCode.SUMMER_WONT_CLOSE, view) == []


# ---------------------------------------------------------------------------
# Rule 6 — PRESENCE_DEFAULTS_TRUE
# ---------------------------------------------------------------------------


def test_rule6_fires_climate_on_no_presence() -> None:
    view = {"options": {"climate_mode": True}}
    findings = _fire(TriageCode.PRESENCE_DEFAULTS_TRUE, view)
    assert len(findings) == 1
    assert findings[0].severity is Severity.INFO


def test_rule6_near_miss_presence_entity_set() -> None:
    view = {"options": {"climate_mode": True, "presence_entity": "binary_sensor.p"}}
    assert _fire(TriageCode.PRESENCE_DEFAULTS_TRUE, view) == []


def test_rule6_near_miss_climate_off() -> None:
    view = {"options": {"climate_mode": False}}
    assert _fire(TriageCode.PRESENCE_DEFAULTS_TRUE, view) == []


# ---------------------------------------------------------------------------
# Rule 7 — CLOUD_OR_SEMANTICS
# ---------------------------------------------------------------------------


def test_rule7_fires_with_two_inputs() -> None:
    view = {
        "options": {
            "lux_entity": "sensor.lux",
            "irradiance_entity": "sensor.irr",
        },
        "climate_conditions": {"lux_below_threshold": True},
    }
    findings = _fire(TriageCode.CLOUD_OR_SEMANTICS, view)
    assert len(findings) == 1
    params = dict(findings[0].reason.params)
    assert params["inputs"] == "lux, irradiance"
    assert params["tripped"] == "lux below threshold"


def test_rule7_near_miss_single_input() -> None:
    view = {"options": {"lux_entity": "sensor.lux"}}
    assert _fire(TriageCode.CLOUD_OR_SEMANTICS, view) == []


# ---------------------------------------------------------------------------
# Rule 8a — COVER_NOT_READY (per-entity)
# ---------------------------------------------------------------------------


def test_rule8a_fires_per_unavailable_cover() -> None:
    view = {
        "capabilities": {
            "cover.a": None,
            "cover.b": {"has_set_position": True},
            "cover.c": None,
        }
    }
    findings = _fire(TriageCode.COVER_NOT_READY, view)
    assert _params(findings) == [{"eid": "cover.a"}, {"eid": "cover.c"}]
    assert all(f.fix_step == "cover_entities" for f in findings)


def test_rule8a_near_miss_all_ready() -> None:
    view = {"capabilities": {"cover.a": {"has_set_position": True}}}
    assert _fire(TriageCode.COVER_NOT_READY, view) == []


def test_rule8a_ignores_non_entity_capability_key() -> None:
    """A bogus non-entity capabilities key (e.g. a stray combine-mode scalar)
    must not be reported as an unready cover (#1017 defense-in-depth).
    """
    view = {"capabilities": {"or": None, "cover.a": None}}
    assert _params(_fire(TriageCode.COVER_NOT_READY, view)) == [{"eid": "cover.a"}]


# ---------------------------------------------------------------------------
# Rule 8b — ENTITY_UNAVAILABLE (per-entity)
# ---------------------------------------------------------------------------


def test_rule8b_fires_per_unavailable_sensor_and_cover() -> None:
    view = {
        "local_sensors": [
            {"entity_id": "sensor.x", "state": "unavailable"},
            {"entity_id": "sensor.y", "state": "available"},
        ],
        "building_profile_sensors": [
            {"entity_id": "sensor.z", "state": "unavailable"},
        ],
        "covers": {
            "cover.a": {"available": False},
            "cover.b": {"available": True},
        },
    }
    findings = _fire(TriageCode.ENTITY_UNAVAILABLE, view)
    assert _params(findings) == [
        {"eid": "sensor.x"},
        {"eid": "sensor.z"},
        {"eid": "cover.a"},
    ]
    # No fix route: an unavailable entity is an HA-side condition, not something
    # an ACP options step fixes (rule 8b fires for heterogeneous entities).
    assert all(f.fix_step is None for f in findings)


def test_rule8b_near_miss_all_available() -> None:
    view = {
        "local_sensors": [{"entity_id": "sensor.y", "state": "available"}],
        "covers": {"cover.b": {"available": True}},
    }
    assert _fire(TriageCode.ENTITY_UNAVAILABLE, view) == []


def test_rule8b_ignores_non_entity_sensor_descriptor() -> None:
    """A ``*_template_mode`` scalar (e.g. "or") is not an entity_id (#1017).

    Defense-in-depth: even if a non-entity value slips into a sensor-source
    descriptor, rule 8b must not treat it as an unavailable entity — but a
    genuine unavailable entity on the same view still fires.
    """
    view = {
        "local_sensors": [
            {"entity_id": "or", "state": "unavailable"},
            {"entity_id": "sensor.x", "state": "unavailable"},
        ],
    }
    assert _params(_fire(TriageCode.ENTITY_UNAVAILABLE, view)) == [{"eid": "sensor.x"}]


def test_rule8b_ignores_non_entity_cover_key() -> None:
    """The covers-path key must also look like an entity_id before firing."""
    view = {
        "covers": {
            "or": {"available": False},
            "cover.a": {"available": False},
        },
    }
    assert _params(_fire(TriageCode.ENTITY_UNAVAILABLE, view)) == [{"eid": "cover.a"}]


def test_rule8b_list_valued_entity_id_yields_one_finding_per_member() -> None:
    """A list-valued ``entity_id`` (e.g. ``weather_severe_sensors`` /
    ``daytime_gate_sensors``) with ``state: "unavailable"`` yields one finding
    per member entity id, not zero.

    ``_classify_sensor_state`` only reports ``"unavailable"`` for a list
    descriptor when EVERY member is down, so each member genuinely is
    unavailable and deserves its own finding — closing the audit gap where
    ``_is_entity_id`` (scalar-only) rejected the list outright and silently
    dropped multi-sensor descriptors after the #1017 fix.
    """
    view = {
        "local_sensors": [
            {
                "entity_id": ["sensor.severe_a", "sensor.severe_b"],
                "state": "unavailable",
            },
        ],
    }
    assert _params(_fire(TriageCode.ENTITY_UNAVAILABLE, view)) == [
        {"eid": "sensor.severe_a"},
        {"eid": "sensor.severe_b"},
    ]


def test_rule8b_list_valued_entity_id_skips_non_entity_members() -> None:
    """Within a list-valued descriptor, only entity-id-shaped members fire."""
    view = {
        "local_sensors": [
            {
                "entity_id": ["sensor.ok", "or"],
                "state": "unavailable",
            },
        ],
    }
    assert _params(_fire(TriageCode.ENTITY_UNAVAILABLE, view)) == [{"eid": "sensor.ok"}]


# ---------------------------------------------------------------------------
# Rule 9 — MIN_FLOOR_BYPASSED
# ---------------------------------------------------------------------------


def test_rule9_fires_on_sunset_below_floor() -> None:
    view = {"options": {"min_position": 40, "sunset_position": 10}}
    findings = _fire(TriageCode.MIN_FLOOR_BYPASSED, view)
    assert len(findings) == 1
    params = dict(findings[0].reason.params)
    assert params["min"] == 40
    assert params["offenders"] == "sunset position"


def test_rule9_fires_on_custom_position_below_floor() -> None:
    view = {
        "options": {
            "min_position": 40,
            "custom_position_sensor_1": "binary_sensor.t",
            "custom_position_1": 5,
            "custom_position_priority_1": 77,
        }
    }
    params = dict(_fire(TriageCode.MIN_FLOOR_BYPASSED, view)[0].reason.params)
    assert params["offenders"] == "Custom #1"


def test_rule9_near_miss_sunset_above_floor() -> None:
    view = {"options": {"min_position": 40, "sunset_position": 50}}
    assert _fire(TriageCode.MIN_FLOOR_BYPASSED, view) == []


def test_rule9_near_miss_no_floor() -> None:
    view = {"options": {"min_position": 0, "sunset_position": 5}}
    assert _fire(TriageCode.MIN_FLOOR_BYPASSED, view) == []


# ---------------------------------------------------------------------------
# Rule 10 — ENABLE_MIN_BACKWARDS
# ---------------------------------------------------------------------------


def test_rule10_fires_when_enable_min_false_with_floor() -> None:
    view = {"options": {"enable_min_position": False, "min_position": 40}}
    findings = _fire(TriageCode.ENABLE_MIN_BACKWARDS, view)
    assert len(findings) == 1
    assert findings[0].severity is Severity.INFO
    assert dict(findings[0].reason.params) == {"min": 40}


def test_rule10_near_miss_enable_min_true() -> None:
    view = {"options": {"enable_min_position": True, "min_position": 40}}
    assert _fire(TriageCode.ENABLE_MIN_BACKWARDS, view) == []


def test_rule10_near_miss_no_floor() -> None:
    view = {"options": {"enable_min_position": False, "min_position": 0}}
    assert _fire(TriageCode.ENABLE_MIN_BACKWARDS, view) == []


# ---------------------------------------------------------------------------
# Rule 15 — TRACKING_WINDOW_TRUNCATED (step 5)
# ---------------------------------------------------------------------------


def test_rule15_fires_when_max_elevation_truncates_window() -> None:
    view = {"options": {"max_elevation": 25}}
    findings = _fire(TriageCode.TRACKING_WINDOW_TRUNCATED, view)
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].fix_step == "sun_tracking"
    assert dict(findings[0].reason.params) == {"max_elevation": 25}


def test_rule15_near_miss_max_elevation_above_threshold() -> None:
    view = {"options": {"max_elevation": 26}}
    assert _fire(TriageCode.TRACKING_WINDOW_TRUNCATED, view) == []


def test_rule15_near_miss_key_absent() -> None:
    assert _fire(TriageCode.TRACKING_WINDOW_TRUNCATED, {"options": {}}) == []


# ---------------------------------------------------------------------------
# Rule 16 — GEOMETRY_NEAR_BINARY (step 6)
# ---------------------------------------------------------------------------


def test_rule16_fires_when_distance_near_zero() -> None:
    view = {"options": {"distance_shaded_area": 0.5}}
    findings = _fire(TriageCode.GEOMETRY_NEAR_BINARY, view)
    assert len(findings) == 1
    assert findings[0].severity is Severity.INFO
    assert findings[0].fix_step == "geometry"
    assert dict(findings[0].reason.params) == {"distance": 0.5}


def test_rule16_near_miss_distance_at_threshold() -> None:
    view = {"options": {"distance_shaded_area": 0.75}}
    assert _fire(TriageCode.GEOMETRY_NEAR_BINARY, view) == []


def test_rule16_near_miss_key_absent() -> None:
    assert _fire(TriageCode.GEOMETRY_NEAR_BINARY, {"options": {}}) == []


# ---------------------------------------------------------------------------
# Rule 19 — SPECIAL_POSITION_DELTA_BYPASS (step 7)
# ---------------------------------------------------------------------------


def test_rule19_fires_special_default_with_wide_delta() -> None:
    view = {"options": {"default_percentage": 0, "delta_position": 10}}
    findings = _fire(TriageCode.SPECIAL_POSITION_DELTA_BYPASS, view)
    assert len(findings) == 1
    assert findings[0].severity is Severity.INFO
    assert findings[0].fix_step == "behavior"
    assert dict(findings[0].reason.params) == {"default": 0, "delta": 10}


def test_rule19_fires_special_default_100() -> None:
    view = {"options": {"default_percentage": 100, "delta_position": 6}}
    assert len(_fire(TriageCode.SPECIAL_POSITION_DELTA_BYPASS, view)) == 1


def test_rule19_near_miss_default_not_special() -> None:
    view = {"options": {"default_percentage": 50, "delta_position": 10}}
    assert _fire(TriageCode.SPECIAL_POSITION_DELTA_BYPASS, view) == []


def test_rule19_near_miss_delta_narrow() -> None:
    view = {"options": {"default_percentage": 0, "delta_position": 5}}
    assert _fire(TriageCode.SPECIAL_POSITION_DELTA_BYPASS, view) == []


# ---------------------------------------------------------------------------
# Rule 22 — CUSTOM_ABOVE_MANUAL (step 8, per-slot)
# ---------------------------------------------------------------------------


def test_rule22_fires_when_slot_priority_above_manual() -> None:
    view = {
        "options": {
            "custom_position_sensor_1": "binary_sensor.t",
            "custom_position_1": 40,
            "custom_position_priority_1": 90,
        }
    }
    findings = _fire(TriageCode.CUSTOM_ABOVE_MANUAL, view)
    assert len(findings) == 1
    assert findings[0].severity is Severity.INFO
    assert findings[0].fix_step == "custom_position"
    assert dict(findings[0].reason.params) == {"slot": 1, "priority": 90, "manual": 80}


def test_rule22_near_miss_priority_equals_manual() -> None:
    view = {
        "options": {
            "custom_position_sensor_1": "binary_sensor.t",
            "custom_position_1": 40,
            "custom_position_priority_1": 80,
        }
    }
    assert _fire(TriageCode.CUSTOM_ABOVE_MANUAL, view) == []


def test_rule22_near_miss_priority_is_safety() -> None:
    view = {
        "options": {
            "custom_position_sensor_1": "binary_sensor.t",
            "custom_position_1": 40,
            "custom_position_priority_1": CUSTOM_POSITION_SAFETY_PRIORITY,
        }
    }
    assert _fire(TriageCode.CUSTOM_ABOVE_MANUAL, view) == []


def test_rule22_near_miss_unconfigured_slot() -> None:
    view = {"options": {"custom_position_priority_1": 90}}
    assert _fire(TriageCode.CUSTOM_ABOVE_MANUAL, view) == []


def test_rule22_uses_configured_manual_priority_high() -> None:
    # A user who raised manual override to 95 means a slot at 90 no longer wins
    # over it — the rule must read the configured priority, not the hardcoded 80.
    view = {
        "options": {
            "custom_position_sensor_1": "binary_sensor.t",
            "custom_position_1": 40,
            "custom_position_priority_1": 90,
            "manual_override_priority": 95,
        }
    }
    assert _fire(TriageCode.CUSTOM_ABOVE_MANUAL, view) == []


def test_rule22_uses_configured_manual_priority_low() -> None:
    # A user who lowered manual override to 60 means a slot at 70 now outranks it
    # — the rule fires and reports the configured manual priority as {manual}.
    view = {
        "options": {
            "custom_position_sensor_1": "binary_sensor.t",
            "custom_position_1": 40,
            "custom_position_priority_1": 70,
            "manual_override_priority": 60,
        }
    }
    findings = _fire(TriageCode.CUSTOM_ABOVE_MANUAL, view)
    assert len(findings) == 1
    assert dict(findings[0].reason.params) == {"slot": 1, "priority": 70, "manual": 60}


def test_rule22_float_manual_priority_high_does_not_fire() -> None:
    # HA's NumberSelector stores manual_override_priority as a float (95.0). A
    # slot at 90 does NOT outrank a real manual priority of 95, so nothing fires
    # — the read must be float-tolerant, not fall back to the hardcoded 80.
    view = {
        "options": {
            "custom_position_sensor_1": "binary_sensor.t",
            "custom_position_1": 40,
            "custom_position_priority_1": 90.0,
            "manual_override_priority": 95.0,
        }
    }
    assert _fire(TriageCode.CUSTOM_ABOVE_MANUAL, view) == []


def test_rule22_float_manual_priority_low_fires_with_int_manual() -> None:
    # A float-stored manual priority of 60.0 is outranked by a slot at 70.0; the
    # rule fires and reports {manual} as the int 60 (not the hardcoded 80).
    view = {
        "options": {
            "custom_position_sensor_1": "binary_sensor.t",
            "custom_position_1": 40,
            "custom_position_priority_1": 70.0,
            "manual_override_priority": 60.0,
        }
    }
    findings = _fire(TriageCode.CUSTOM_ABOVE_MANUAL, view)
    assert len(findings) == 1
    assert dict(findings[0].reason.params) == {"slot": 1, "priority": 70, "manual": 60}


# ---------------------------------------------------------------------------
# Rule 12 — GLARE_ZONE_NEVER_FIRES (step 9, per-zone)
# ---------------------------------------------------------------------------


def test_rule12_fires_when_zone_reach_beyond_shaded_distance() -> None:
    view = {
        "options": {
            "enable_glare_zones": True,
            "glare_zone_1_name": "Desk",
            "glare_zone_1_y": 3.0,
            "glare_zone_1_radius": 0.3,
            "distance_shaded_area": 1.0,
        }
    }
    findings = _fire(TriageCode.GLARE_ZONE_NEVER_FIRES, view)
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].fix_step == "glare_zones"
    params = dict(findings[0].reason.params)
    assert params["zone"] == "Desk"
    assert params["reach"] == 2.7
    assert params["distance"] == 1.0


def test_rule12_near_miss_zone_within_reach() -> None:
    view = {
        "options": {
            "enable_glare_zones": True,
            "glare_zone_1_name": "Desk",
            "glare_zone_1_y": 1.0,
            "glare_zone_1_radius": 0.3,
            "distance_shaded_area": 1.0,
        }
    }
    assert _fire(TriageCode.GLARE_ZONE_NEVER_FIRES, view) == []


def test_rule12_near_miss_glare_zones_disabled() -> None:
    view = {
        "options": {
            "enable_glare_zones": False,
            "glare_zone_1_name": "Desk",
            "glare_zone_1_y": 3.0,
            "glare_zone_1_radius": 0.3,
            "distance_shaded_area": 1.0,
        }
    }
    assert _fire(TriageCode.GLARE_ZONE_NEVER_FIRES, view) == []


def test_rule12_near_miss_unnamed_zone() -> None:
    view = {
        "options": {
            "enable_glare_zones": True,
            "glare_zone_1_y": 3.0,
            "glare_zone_1_radius": 0.3,
            "distance_shaded_area": 1.0,
        }
    }
    assert _fire(TriageCode.GLARE_ZONE_NEVER_FIRES, view) == []


def test_rule12_near_miss_sun_tracking_disabled() -> None:
    # The glare handler's beyond-distance short-circuit only runs while sun
    # tracking is enabled; with tracking off the zone can still act against the
    # default position, so an unreachable zone is not a fault (finding 5).
    view = {
        "options": {
            "enable_sun_tracking": False,
            "enable_glare_zones": True,
            "glare_zone_1_name": "Desk",
            "glare_zone_1_y": 3.0,
            "glare_zone_1_radius": 0.3,
            "distance_shaded_area": 1.0,
        }
    }
    assert _fire(TriageCode.GLARE_ZONE_NEVER_FIRES, view) == []


def test_rule12_fires_with_sun_tracking_enabled() -> None:
    view = {
        "options": {
            "enable_sun_tracking": True,
            "enable_glare_zones": True,
            "glare_zone_1_name": "Desk",
            "glare_zone_1_y": 3.0,
            "glare_zone_1_radius": 0.3,
            "distance_shaded_area": 1.0,
        }
    }
    assert len(_fire(TriageCode.GLARE_ZONE_NEVER_FIRES, view)) == 1


def test_rule12_reach_is_rounded_for_display() -> None:
    # Float subtraction can yield 0.9000000000000004; the rendered reach must be
    # a clean 0.9, not the full binary-noise expansion (finding 9).
    view = {
        "options": {
            "enable_glare_zones": True,
            "glare_zone_1_name": "Desk",
            "glare_zone_1_y": 3.0,
            "glare_zone_1_radius": 2.1,
            "distance_shaded_area": 0.5,
        }
    }
    params = dict(_fire(TriageCode.GLARE_ZONE_NEVER_FIRES, view)[0].reason.params)
    assert params["reach"] == 0.9


# ---------------------------------------------------------------------------
# Rule 23 — POSITION_MATCHING_OFF (step 10, mixed CONFIG|RUNTIME)
# ---------------------------------------------------------------------------


def test_rule23_fires_manual_override_with_matching_off() -> None:
    view = {
        "options": {"enable_position_matching": False},
        "control_status": "manual_override",
    }
    findings = _fire(TriageCode.POSITION_MATCHING_OFF, view)
    assert len(findings) == 1
    assert findings[0].severity is Severity.INFO
    assert findings[0].fix_step == "position"


def test_rule23_near_miss_matching_on() -> None:
    view = {
        "options": {"enable_position_matching": True},
        "control_status": "manual_override",
    }
    assert _fire(TriageCode.POSITION_MATCHING_OFF, view) == []


def test_rule23_near_miss_not_manual_override() -> None:
    view = {
        "options": {"enable_position_matching": False},
        "control_status": "active",
    }
    assert _fire(TriageCode.POSITION_MATCHING_OFF, view) == []


def test_rule23_is_dropped_by_config_only_filter() -> None:
    # A mixed CONFIG|RUNTIME row must be filtered out under only=CONFIG so the
    # config-only surfaces (summary/wizard) never surface a runtime-dependent
    # finding.
    view = {
        "options": {"enable_position_matching": False},
        "control_status": "manual_override",
    }
    assert run_triage(view, only=RuleInput.CONFIG) == []


# ---------------------------------------------------------------------------
# Rule 17 — DRY_RUN_LEFT_ON (step 12, RUNTIME)
# ---------------------------------------------------------------------------


def test_rule17_fires_when_dry_run_on() -> None:
    view = {"debug_config": {"dry_run": True}}
    findings = _fire(TriageCode.DRY_RUN_LEFT_ON, view)
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].fix_step == "debug"


def test_rule17_near_miss_dry_run_off() -> None:
    assert _fire(TriageCode.DRY_RUN_LEFT_ON, {"debug_config": {"dry_run": False}}) == []


def test_rule17_near_miss_section_absent() -> None:
    assert _fire(TriageCode.DRY_RUN_LEFT_ON, {}) == []


# ---------------------------------------------------------------------------
# Rule 21 — OVERRIDE_BLOCKED_AUTO_OFF (step 13, RUNTIME)
# ---------------------------------------------------------------------------


def test_rule21_fires_when_auto_control_off() -> None:
    view = {"control_status": "automatic_control_off"}
    findings = _fire(TriageCode.OVERRIDE_BLOCKED_AUTO_OFF, view)
    assert len(findings) == 1
    assert findings[0].severity is Severity.INFO
    assert findings[0].fix_step is None


def test_rule21_near_miss_active() -> None:
    assert (
        _fire(TriageCode.OVERRIDE_BLOCKED_AUTO_OFF, {"control_status": "active"}) == []
    )


# ---------------------------------------------------------------------------
# Rule 11 — AZIMUTH_FOV_MISMATCH (step 14, RUNTIME)
# ---------------------------------------------------------------------------


def test_rule11_fires_when_sun_up_but_outside_fov_and_default_won() -> None:
    view = {
        "sun_validity": {"in_fov": False, "valid_elevation": True},
        "decision_trace": [{"handler": "default", "matched": True}],
    }
    findings = _fire(TriageCode.AZIMUTH_FOV_MISMATCH, view)
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].fix_step == "geometry"


def test_rule11_near_miss_in_fov() -> None:
    view = {
        "sun_validity": {"in_fov": True, "valid_elevation": True},
        "decision_trace": [{"handler": "default", "matched": True}],
    }
    assert _fire(TriageCode.AZIMUTH_FOV_MISMATCH, view) == []


def test_rule11_near_miss_solar_won() -> None:
    view = {
        "sun_validity": {"in_fov": False, "valid_elevation": True},
        "decision_trace": [{"handler": "solar", "matched": True}],
    }
    assert _fire(TriageCode.AZIMUTH_FOV_MISMATCH, view) == []


def test_rule11_near_miss_elevation_invalid() -> None:
    view = {
        "sun_validity": {"in_fov": False, "valid_elevation": False},
        "decision_trace": [{"handler": "default", "matched": True}],
    }
    assert _fire(TriageCode.AZIMUTH_FOV_MISMATCH, view) == []


def test_rule11_near_miss_trace_absent() -> None:
    view = {"sun_validity": {"in_fov": False, "valid_elevation": True}}
    assert _fire(TriageCode.AZIMUTH_FOV_MISMATCH, view) == []


# ---------------------------------------------------------------------------
# Rule 18 — ENDPOINT_CHASE (step 15, RUNTIME, per-entity)
# ---------------------------------------------------------------------------


def test_rule18_fires_per_gave_up_entity() -> None:
    view = {
        "cover_commands": {
            "cover.a": {"target_call": 30, "retry_count": 4, "gave_up": True},
            "cover.b": {"target_call": 50, "retry_count": 1, "gave_up": False},
            "cover.c": {"target_call": 0, "retry_count": 4, "gave_up": True},
        }
    }
    findings = _fire(TriageCode.ENDPOINT_CHASE, view)
    assert _params(findings) == [
        {"entity": "cover.a", "retry_count": 4, "target": 30},
        {"entity": "cover.c", "retry_count": 4, "target": 0},
    ]
    assert all(f.severity is Severity.WARNING for f in findings)
    assert all(f.fix_step == "position" for f in findings)


def test_rule18_near_miss_not_gave_up() -> None:
    view = {"cover_commands": {"cover.a": {"retry_count": 2, "gave_up": False}}}
    assert _fire(TriageCode.ENDPOINT_CHASE, view) == []


def test_rule18_near_miss_empty() -> None:
    assert _fire(TriageCode.ENDPOINT_CHASE, {"cover_commands": {}}) == []


# ---------------------------------------------------------------------------
# Rule 13 — COVER_FEATURE_MISMATCH (step 11, per entity/axis)
# ---------------------------------------------------------------------------


def test_rule13_fires_when_entity_lacks_required_tilt() -> None:
    view = {
        "axis_requirements": (
            {"axis": "tilt", "capability": "has_set_tilt_position", "fallbacks": ()},
        ),
        "capabilities": {
            "cover.a": {"has_set_position": True, "has_set_tilt_position": False},
        },
    }
    findings = _fire(TriageCode.COVER_FEATURE_MISMATCH, view)
    assert len(findings) == 1
    assert findings[0].severity is Severity.CRITICAL
    assert findings[0].fix_step == "cover_entities"
    assert dict(findings[0].reason.params) == {"entity": "cover.a", "axis": "tilt"}


def test_rule13_near_miss_position_axis_satisfied_by_open_close_fallback() -> None:
    view = {
        "axis_requirements": (
            {
                "axis": "position",
                "capability": "has_set_position",
                "fallbacks": (("has_open", "has_close"),),
            },
        ),
        "capabilities": {
            "cover.a": {
                "has_set_position": False,
                "has_open": True,
                "has_close": True,
            },
        },
    }
    assert _fire(TriageCode.COVER_FEATURE_MISMATCH, view) == []


def test_rule13_near_miss_caps_none_is_left_to_rule_8a() -> None:
    view = {
        "axis_requirements": (
            {"axis": "tilt", "capability": "has_set_tilt_position", "fallbacks": ()},
        ),
        "capabilities": {"cover.a": None},
    }
    assert _fire(TriageCode.COVER_FEATURE_MISMATCH, view) == []


def test_rule13_near_miss_axis_requirements_absent() -> None:
    view = {"capabilities": {"cover.a": {"has_set_tilt_position": False}}}
    assert _fire(TriageCode.COVER_FEATURE_MISMATCH, view) == []


def test_rule13_fires_per_entity() -> None:
    view = {
        "axis_requirements": (
            {"axis": "tilt", "capability": "has_set_tilt_position", "fallbacks": ()},
        ),
        "capabilities": {
            "cover.a": {"has_set_tilt_position": False},
            "cover.b": {"has_set_tilt_position": True},
            "cover.c": {"has_set_tilt_position": False},
        },
    }
    findings = _fire(TriageCode.COVER_FEATURE_MISMATCH, view)
    assert _params(findings) == [
        {"entity": "cover.a", "axis": "tilt"},
        {"entity": "cover.c", "axis": "tilt"},
    ]


# ---------------------------------------------------------------------------
# Rule 20 — skip trio (step 16)
# ---------------------------------------------------------------------------


def _skip_view(reason: str) -> dict:
    return {
        "last_skipped_action": {
            "entity_id": "cover.a",
            "reason": reason,
            "timestamp": "2026-07-22T10:00:00+00:00",
        },
        "data_window": {"captured_at": "2026-07-22T10:30:00+00:00"},
    }


def test_rule20_service_call_failed_fires_critical_with_age() -> None:
    findings = _fire(
        TriageCode.SKIP_SERVICE_CALL_FAILED, _skip_view("service_call_failed")
    )
    assert len(findings) == 1
    assert findings[0].severity is Severity.CRITICAL
    assert findings[0].fix_step == "cover_entities"
    params = dict(findings[0].reason.params)
    assert params["entity"] == "cover.a"
    assert params["age_minutes"] == 30.0


def test_rule20_no_capable_service_fires_critical() -> None:
    findings = _fire(
        TriageCode.SKIP_NO_CAPABLE_SERVICE, _skip_view("no_capable_service")
    )
    assert len(findings) == 1
    assert findings[0].severity is Severity.CRITICAL


def test_rule20_cover_unavailable_fires_warning() -> None:
    findings = _fire(TriageCode.SKIP_COVER_UNAVAILABLE, _skip_view("cover_unavailable"))
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING


def test_rule20_service_call_failed_near_miss_other_reason() -> None:
    assert (
        _fire(TriageCode.SKIP_SERVICE_CALL_FAILED, _skip_view("cover_unavailable"))
        == []
    )


def test_rule20_benign_reasons_produce_no_finding() -> None:
    # delta_too_small and dry_run are expected steady-state skips, not faults —
    # none of the three skip rules should fire on them.
    for reason in ("delta_too_small", "dry_run"):
        view = _skip_view(reason)
        assert run_triage(view) == [] or all(
            not f.reason.code.startswith("triage.skip_") for f in run_triage(view)
        )


def test_rule20_age_none_when_timestamps_missing() -> None:
    # When no skip timestamp is known the finding must render WITHOUT an age
    # clause — never "None minutes ago" and never a literal "{age_minutes}"
    # placeholder (finding 3). The check drops the age key and splices an empty
    # {age} clause instead.
    view = {
        "last_skipped_action": {"entity_id": "cover.a", "reason": "service_call_failed"}
    }
    findings = _fire(TriageCode.SKIP_SERVICE_CALL_FAILED, view)
    assert len(findings) == 1
    params = dict(findings[0].reason.params)
    assert "age_minutes" not in params
    rendered = render(findings[0].reason, load_troubleshoot_labels("en"))
    assert "None" not in rendered
    assert "{age" not in rendered
    assert rendered == (
        "🛑 The last command to cover.a was skipped because the service call "
        "failed. Check the cover integration and the HA log for the underlying "
        "error."
    )


def test_rule20_age_none_on_mixed_aware_naive_timestamps() -> None:
    # An aware skip timestamp with a naive captured_at (or vice versa) makes the
    # subtraction raise TypeError; the finding must still emit with no age clause
    # rather than vanishing entirely (finding 4 — _age_minutes never raises).
    view = {
        "last_skipped_action": {
            "entity_id": "cover.a",
            "reason": "service_call_failed",
            "timestamp": "2026-07-22T10:00:00+00:00",
        },
        "data_window": {"captured_at": "2026-07-22T10:30:00"},
    }
    findings = _fire(TriageCode.SKIP_SERVICE_CALL_FAILED, view)
    assert len(findings) == 1
    params = dict(findings[0].reason.params)
    assert "age_minutes" not in params
    rendered = render(findings[0].reason, load_troubleshoot_labels("en"))
    assert "None" not in rendered


def test_rule20_age_rendered_inline_when_known() -> None:
    findings = _fire(
        TriageCode.SKIP_SERVICE_CALL_FAILED, _skip_view("service_call_failed")
    )
    rendered = render(findings[0].reason, load_troubleshoot_labels("en"))
    assert "30.0 minutes ago" in rendered
    assert "None" not in rendered


# ---------------------------------------------------------------------------
# Rule 24 — STALE_VERSION (step 17)
# ---------------------------------------------------------------------------


def test_rule24_fires_when_newer_release_available() -> None:
    view = {
        "latest_version": "2026.8.0",
        "meta": {"integration_version": "2026.7.0"},
    }
    findings = _fire(TriageCode.STALE_VERSION, view)
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].fix_step is None
    assert dict(findings[0].reason.params) == {
        "latest": "2026.8.0",
        "current": "2026.7.0",
    }


def test_rule24_fires_on_patch_bump() -> None:
    view = {"latest_version": "2026.7.2", "meta": {"integration_version": "2026.7.0"}}
    assert len(_fire(TriageCode.STALE_VERSION, view)) == 1


def test_rule24_near_miss_key_absent() -> None:
    assert (
        _fire(TriageCode.STALE_VERSION, {"meta": {"integration_version": "2026.7.0"}})
        == []
    )


def test_rule24_near_miss_equal_version() -> None:
    view = {"latest_version": "2026.7.0", "meta": {"integration_version": "2026.7.0"}}
    assert _fire(TriageCode.STALE_VERSION, view) == []


def test_rule24_near_miss_latest_older() -> None:
    view = {"latest_version": "2026.6.0", "meta": {"integration_version": "2026.7.0"}}
    assert _fire(TriageCode.STALE_VERSION, view) == []


def test_rule24_unparseable_never_raises_no_finding() -> None:
    view = {"latest_version": "garbage", "meta": {"integration_version": "2026.7.0"}}
    assert _fire(TriageCode.STALE_VERSION, view) == []


def test_rule24_near_miss_unequal_length_equal_version() -> None:
    # "2026.8" and "2026.8.0" are the same release — a missing patch segment must
    # normalize to 0, not read as "older" and fire a spurious update nag
    # (finding 7).
    view = {"latest_version": "2026.8", "meta": {"integration_version": "2026.8.0"}}
    assert _fire(TriageCode.STALE_VERSION, view) == []
    view = {"latest_version": "2026.8.0", "meta": {"integration_version": "2026.8"}}
    assert _fire(TriageCode.STALE_VERSION, view) == []


def test_rule24_fires_across_unequal_length_when_newer() -> None:
    view = {"latest_version": "2026.9", "meta": {"integration_version": "2026.8.0"}}
    assert len(_fire(TriageCode.STALE_VERSION, view)) == 1


# ---------------------------------------------------------------------------
# Rule 14 — MIXED_TEMP_UNITS (RUNTIME)
# ---------------------------------------------------------------------------


def _mixed_units_view(inside_unit, outside_unit) -> dict:
    return {
        "temp_sensor": {
            "entity_id": "sensor.inside",
            "unit_of_measurement": inside_unit,
        },
        "local_sensors": [
            {
                "key": "outside_temp",
                "entity_id": "sensor.outside",
                "unit_of_measurement": outside_unit,
            }
        ],
    }


def test_rule14_fires_when_inside_and_outside_units_differ() -> None:
    findings = _fire(TriageCode.MIXED_TEMP_UNITS, _mixed_units_view("°F", "°C"))
    assert len(findings) == 1
    assert findings[0].severity is Severity.CRITICAL
    assert findings[0].fix_step == "temperature_climate"
    assert dict(findings[0].reason.params) == {
        "inside_unit": "°F",
        "outside_unit": "°C",
    }


def test_rule14_near_miss_matching_units() -> None:
    assert _fire(TriageCode.MIXED_TEMP_UNITS, _mixed_units_view("°C", "°C")) == []


def test_rule14_near_miss_only_inside_unit_present() -> None:
    view = {"temp_sensor": {"unit_of_measurement": "°C"}}
    assert _fire(TriageCode.MIXED_TEMP_UNITS, view) == []


def test_rule14_near_miss_outside_unit_missing() -> None:
    view = {
        "temp_sensor": {"unit_of_measurement": "°C"},
        "local_sensors": [{"key": "outside_temp", "entity_id": "sensor.outside"}],
    }
    assert _fire(TriageCode.MIXED_TEMP_UNITS, view) == []


def test_rule14_near_miss_keys_absent() -> None:
    assert _fire(TriageCode.MIXED_TEMP_UNITS, {}) == []


def test_rule14_reads_outside_unit_from_building_profile_sensors() -> None:
    view = {
        "temp_sensor": {"unit_of_measurement": "°F"},
        "building_profile_sensors": [
            {"key": "outside_temp", "unit_of_measurement": "°C"}
        ],
    }
    findings = _fire(TriageCode.MIXED_TEMP_UNITS, view)
    assert len(findings) == 1
    assert dict(findings[0].reason.params) == {
        "inside_unit": "°F",
        "outside_unit": "°C",
    }


def test_rule14_ignores_non_temperature_local_sensors() -> None:
    # A lux sensor reporting "lx" is not a temperature-unit mismatch — only the
    # outside_temp descriptor is compared against the inside temperature unit.
    view = {
        "temp_sensor": {"unit_of_measurement": "°C"},
        "local_sensors": [{"key": "lux", "unit_of_measurement": "lx"}],
    }
    assert _fire(TriageCode.MIXED_TEMP_UNITS, view) == []


# ---------------------------------------------------------------------------
# Step 4 — meta-test over the whole table
# ---------------------------------------------------------------------------


def _real_options_steps() -> frozenset[str]:
    """Return the real ``async_step_*`` ids on ``OptionsFlowHandler``.

    Imported inside the helper so the HA-dependent ``config_flow`` module is not
    pulled in at collection time (mirrors how other tests defer that import).
    """
    from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler

    return frozenset(
        name.removeprefix("async_step_")
        for name in dir(OptionsFlowHandler)
        if name.startswith("async_step_")
    )


# Steps reachable from the COVER options menu (``async_step_init`` cover branch,
# config_flow.py ~:4454-4501 — the ``keys`` list plus its conditional appends).
# A rule's ``fix_step`` must be one of these or ``None``: a profile-only step
# such as ``profile_sensors`` (which calls ``async_create_entry`` and closes the
# flow) is NOT reachable here and is not a valid fix route for a cover finding.
_COVER_MENU_STEPS: frozenset[str] = frozenset(
    {
        "cover_entities",
        "geometry",
        "sun_tracking",
        "building_profile",
        "blind_spot",
        "position",
        "behavior",
        "interp",
        "weather_override",
        "manual_override",
        "custom_position",
        "motion_override",
        "light_cloud",
        "temperature_climate",
        "glare_zones",
        "pipeline_priorities",
        "automation",
        "sync",
        "summary",
        "troubleshoot",
        "debug",
        "done",
    }
)


_WIKI_RE = re.compile(r"^[A-Za-z0-9-]+#[a-z0-9-]+$")


def test_rule_codes_are_unique() -> None:
    codes = [rule.code for rule in TRIAGE_RULES]
    assert len(codes) == len(set(codes))


def test_rule_table_covers_every_triage_code() -> None:
    # Every TriageCode maps to exactly one rule EXCEPT the fragment codes, which
    # are nested render-only clauses (e.g. the skip "N minutes ago" suffix) with
    # a template but no rule row — mirrors ReasonCode's FRAGMENT_* members.
    from custom_components.adaptive_cover_pro.diagnostics.triage import (
        _TRIAGE_FRAGMENT_CODES,
    )

    assert {rule.code for rule in TRIAGE_RULES} == {c.value for c in TriageCode} - {
        str(c) for c in _TRIAGE_FRAGMENT_CODES
    }


@pytest.mark.parametrize("rule", TRIAGE_RULES, ids=lambda r: r.code)
def test_rule_template_exists_in_code_defaults_and_en_json(rule) -> None:
    assert rule.code in _TRIAGE_TEMPLATES_EN
    en_flat = i18n_parity.flatten(
        i18n_parity.load_bundle(_TROUBLESHOOT_I18N_DIR, "en.json")
    )
    assert rule.code in en_flat


def test_cover_menu_steps_are_all_real_options_steps() -> None:
    # Drift guard: every step in the curated cover-menu set must be a real
    # ``async_step_*`` handler, so a renamed cover step breaks this test rather
    # than silently letting a stale name into the reachable allowlist.
    assert _COVER_MENU_STEPS.issubset(_real_options_steps())


@pytest.mark.parametrize("rule", TRIAGE_RULES, ids=lambda r: r.code)
def test_rule_fix_step_is_reachable_from_cover_menu(rule) -> None:
    # A finding's fix_step must be None or a step the user can actually reach
    # from the cover options menu — NOT merely any ``async_step_*`` (which would
    # wrongly accept profile-only steps like ``profile_sensors``).
    assert rule.fix_step is None or rule.fix_step in _COVER_MENU_STEPS


@pytest.mark.parametrize("rule", TRIAGE_RULES, ids=lambda r: r.code)
def test_rule_wiki_anchor_format(rule) -> None:
    assert _WIKI_RE.match(rule.wiki), rule.wiki


def _canonical_anchor(code: str) -> str:
    """Return the canonical findings anchor for a triage ``code``.

    The scheme: drop the ``triage.`` namespace and hyphenate the remainder, so
    ``triage.custom_safety_bypass`` → ``custom-safety-bypass``. Every rule points
    at one canonical page — ``Troubleshooting-Findings`` — with a per-code anchor.
    """
    return code.split(".", 1)[1].replace("_", "-")


@pytest.mark.parametrize("rule", TRIAGE_RULES, ids=lambda r: r.code)
def test_rule_wiki_points_at_canonical_findings_page(rule) -> None:
    # Every rule deep-links into the single canonical findings page at a stable
    # per-code anchor (the deliverable-D contract), not a scattering of config
    # pages with dangling anchors.
    assert rule.wiki == f"Troubleshooting-Findings#{_canonical_anchor(rule.code)}"


@pytest.mark.parametrize("rule", TRIAGE_RULES, ids=lambda r: r.code)
def test_wiki_anchor_for_returns_rule_wiki(rule) -> None:
    # The single-source-of-truth accessor (issue #1059) must agree with the
    # rule table itself for every code — the same map ``scripts/triage_json.py``
    # and the ``get_troubleshooting`` service both point at.
    assert wiki_anchor_for(rule.code) == rule.wiki


def test_wiki_anchor_for_unknown_code_returns_none() -> None:
    assert wiki_anchor_for("triage.not_a_real_code") is None


def _find_wiki_checkout() -> Path | None:
    """Locate a sibling ``adaptive-cover-pro.wiki`` checkout, if one is present.

    Walks the ancestors of this test file (which sit inside the integration
    repo — possibly a worktree) looking for a sibling wiki clone. Returns None
    when none is found so the anchor-resolves test skips instead of failing on a
    machine without the wiki checked out.
    """
    return next(
        (
            p / "adaptive-cover-pro.wiki"
            for p in Path(__file__).resolve().parents
            if (p / "adaptive-cover-pro.wiki").is_dir()
        ),
        None,
    )


def _github_slugify(heading: str) -> str:
    """Slugify a Markdown heading the way GitHub's wiki anchors do."""
    slug = heading.strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    return re.sub(r"[\s]+", "-", slug)


_WIKI_CHECKOUT = _find_wiki_checkout()


@pytest.mark.skipif(
    _WIKI_CHECKOUT is None, reason="no sibling adaptive-cover-pro.wiki checkout"
)
@pytest.mark.parametrize("rule", TRIAGE_RULES, ids=lambda r: r.code)
def test_rule_wiki_anchor_resolves_on_findings_page(rule) -> None:
    # Upgrade from format-only to resolvability: parse the canonical findings
    # page's ### headings, GitHub-slugify them, and assert every rule's wiki
    # anchor actually points at a real section. A rule added without its section
    # (or a typo'd anchor) fails here rather than shipping a dead deep-link.
    page = _WIKI_CHECKOUT / "Troubleshooting-Findings.md"
    assert page.is_file(), page
    slugs = {
        _github_slugify(line[4:])
        for line in page.read_text(encoding="utf-8").splitlines()
        if line.startswith("### ")
    }
    _, _, anchor = rule.wiki.partition("#")
    assert anchor in slugs, f"{rule.code} → #{anchor} not a heading on {page.name}"


@pytest.mark.parametrize("rule", TRIAGE_RULES, ids=lambda r: r.code)
def test_rule_issues_non_empty_int_tuple(rule) -> None:
    assert isinstance(rule.issues, tuple)
    assert rule.issues
    assert all(isinstance(i, int) for i in rule.issues)


# ---------------------------------------------------------------------------
# Slot-gate parity lock — the triage module's slot helpers are a forced
# hand-copy of the ``helpers`` / ``templates`` originals (those import Home
# Assistant; triage must stay HA-free). If the copies drift, the config summary
# can index a slot with no finding and raise KeyError. Lock the copy at test
# time so drift fails here, not in production. (HA-dependent helpers are
# imported inside the test to keep collection HA-free, mirroring the pattern
# above.)
# ---------------------------------------------------------------------------


def test_triage_claim_keys_match_helpers_constant() -> None:
    from custom_components.adaptive_cover_pro.diagnostics.triage import _CLAIM_KEYS
    from custom_components.adaptive_cover_pro.helpers import (
        CUSTOM_POSITION_CLAIM_KEYS,
    )

    assert _CLAIM_KEYS == CUSTOM_POSITION_CLAIM_KEYS


def test_triage_is_template_agrees_with_templates() -> None:
    from custom_components.adaptive_cover_pro.diagnostics.triage import _is_template
    from custom_components.adaptive_cover_pro.templates import is_template_string

    for value in ("{{ x }}", "{% if x %}", "plain", "1000", "", None, 5, True):
        assert _is_template(value) == is_template_string(value), value


def test_triage_slot_configured_agrees_with_helpers() -> None:
    from custom_components.adaptive_cover_pro.const import CUSTOM_POSITION_SLOTS
    from custom_components.adaptive_cover_pro.diagnostics.triage import (
        _slot_configured,
    )
    from custom_components.adaptive_cover_pro.helpers import (
        custom_position_slot_configured,
    )

    keys = CUSTOM_POSITION_SLOTS[1]
    configs = [
        {},  # unconfigured
        {keys["sensor"]: "binary_sensor.t", keys["position"]: 50},  # configured
        {keys["template"]: "{{ true }}", keys["tilt_min"]: 20},  # template + claim
        {keys["sensor"]: "binary_sensor.t"},  # trigger only, no claim
        {keys["position"]: 50},  # claim only, no trigger
        {keys["sensors"]: ["binary_sensor.a"], keys["position_max"]: 80},  # list+claim
        {keys["sensors"]: [], keys["position"]: 50},  # empty list = no trigger
        {keys["template"]: "not-a-template", keys["position"]: 50},  # plain != tmpl
    ]
    for cfg in configs:
        assert _slot_configured(cfg, keys) == custom_position_slot_configured(
            cfg, keys
        ), cfg


# ---------------------------------------------------------------------------
# CONFIG-determinism lock — a CONFIG rule's output must not depend on runtime.
# ---------------------------------------------------------------------------


def _full_view() -> dict:
    return {
        "options": {
            "custom_position_sensor_1": "binary_sensor.t",
            "custom_position_1": 5,
            "custom_position_priority_1": CUSTOM_POSITION_SAFETY_PRIORITY,
            "climate_mode": True,
            "start_time": "00:00:00",
            "min_position": 40,
            "enable_min_position": False,
            "sunset_position": 10,
        },
        "capabilities": {"cover.a": None, "cover.b": {"has_set_position": True}},
        # runtime sections that CONFIG rules must ignore
        "decision_trace": [{"handler": "climate", "matched": True, "priority": 50}],
        "handler_priorities": {"climate": {"priority": 50}, "solar": {"priority": 40}},
        "climate_conditions": {"is_summer": True, "is_presence": True},
        "temperature_details": {"inside_temperature": None},
        "local_sensors": [{"entity_id": "sensor.x", "state": "unavailable"}],
        "covers": {"cover.a": {"available": False}},
    }


def test_config_rules_are_deterministic_without_runtime() -> None:
    full = _full_view()
    config_only = {"options": full["options"], "capabilities": full["capabilities"]}
    config_rules = [r for r in TRIAGE_RULES if r.inputs == RuleInput.CONFIG]
    assert run_triage(full, only=RuleInput.CONFIG, rules=config_rules) == run_triage(
        config_only, only=RuleInput.CONFIG, rules=config_rules
    )
    # And the same via the full table (only= filters mixed/runtime rows out).
    assert run_triage(full, only=RuleInput.CONFIG) == run_triage(
        config_only, only=RuleInput.CONFIG
    )


# ---------------------------------------------------------------------------
# Summary-migration byte-identity lock (issue #970, Step 8). The config summary
# renders these two CONFIG findings via reason_i18n.render (NOT render_report),
# so the rendered English MUST byte-match the legacy hand-written strings the
# migration replaces — guarded end-to-end by tests/test_config_flow_summary.py.
# ---------------------------------------------------------------------------


def test_custom_safety_bypass_renders_byte_identical_to_legacy_summary() -> None:
    reason = Reason(
        TriageCode.CUSTOM_SAFETY_BYPASS,
        {"slot": 5, "safety": CUSTOM_POSITION_SAFETY_PRIORITY},
    )
    assert render(reason, load_troubleshoot_labels("en")) == (
        "⚠️ Custom #5 is at safety priority (100) — it bypasses the "
        "automatic-control toggle, manual override, and the start/end time "
        "window, so it can move the cover even when automatic control is OFF "
        "and outside your schedule. Lower its priority below 100 to make it "
        "respect those gates."
    )


def test_cover_not_ready_renders_byte_identical_to_legacy_summary() -> None:
    reason = Reason(TriageCode.COVER_NOT_READY, {"eid": "cover.x"})
    assert (
        render(reason, load_troubleshoot_labels("en"))
        == "⚠️ cover.x: not ready (unavailable)"
    )
