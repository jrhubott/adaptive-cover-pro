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


def test_seed_table_covers_the_eleven_codes() -> None:
    assert {rule.code for rule in TRIAGE_RULES} == {c.value for c in TriageCode}


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
