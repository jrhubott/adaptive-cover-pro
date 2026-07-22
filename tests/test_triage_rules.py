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
)
from custom_components.adaptive_cover_pro.troubleshoot_i18n import (
    _TRIAGE_TEMPLATES_EN,
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


def test_rule3_fires_on_midnight_start() -> None:
    view = {"options": {"start_time": "00:00:00", "end_time": "20:00:00"}}
    findings = _fire(TriageCode.TIME_WINDOW_SUSPECT, view)
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].fix_step == "automation"


def test_rule3_fires_on_sun_sensor_start_entity() -> None:
    view = {"options": {"start_entity": "sensor.sun_next_dawn"}}
    assert len(_fire(TriageCode.TIME_WINDOW_SUSPECT, view)) == 1


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
    assert all(f.fix_step == "profile_sensors" for f in findings)


def test_rule8b_near_miss_all_available() -> None:
    view = {
        "local_sensors": [{"entity_id": "sensor.y", "state": "available"}],
        "covers": {"cover.b": {"available": True}},
    }
    assert _fire(TriageCode.ENTITY_UNAVAILABLE, view) == []


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
# Step 4 — meta-test over the whole table
# ---------------------------------------------------------------------------

# TODO chunk 4: derive from OptionsFlowHandler reflection (real options steps).
_EXPECTED_FIX_STEPS: frozenset[str] = frozenset(
    {
        "custom_position",
        "pipeline_priorities",
        "automation",
        "temperature_climate",
        "light_cloud",
        "cover_entities",
        "profile_sensors",
        "position",
    }
)

_WIKI_RE = re.compile(r"^[A-Za-z0-9-]+#[a-z0-9-]+$")


def test_rule_codes_are_unique() -> None:
    codes = [rule.code for rule in TRIAGE_RULES]
    assert len(codes) == len(set(codes))


def test_seed_table_covers_the_eleven_codes() -> None:
    assert {rule.code for rule in TRIAGE_RULES} == {c.value for c in TriageCode}


@pytest.mark.parametrize("rule", TRIAGE_RULES, ids=lambda r: r.code)
def test_rule_template_exists_in_code_defaults_and_en_json(rule) -> None:
    assert rule.code in _TRIAGE_TEMPLATES_EN
    en_flat = i18n_parity.flatten(
        i18n_parity.load_bundle(_TROUBLESHOOT_I18N_DIR, "en.json")
    )
    assert rule.code in en_flat


@pytest.mark.parametrize("rule", TRIAGE_RULES, ids=lambda r: r.code)
def test_rule_fix_step_is_real(rule) -> None:
    assert rule.fix_step is None or rule.fix_step in _EXPECTED_FIX_STEPS


@pytest.mark.parametrize("rule", TRIAGE_RULES, ids=lambda r: r.code)
def test_rule_wiki_anchor_format(rule) -> None:
    assert _WIKI_RE.match(rule.wiki), rule.wiki


@pytest.mark.parametrize("rule", TRIAGE_RULES, ids=lambda r: r.code)
def test_rule_issues_non_empty_int_tuple(rule) -> None:
    assert isinstance(rule.issues, tuple)
    assert rule.issues
    assert all(isinstance(i, int) for i in rule.issues)


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
