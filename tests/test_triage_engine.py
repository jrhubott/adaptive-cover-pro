"""Tests for the pure diagnostics-triage engine (issue #970, Phase 1).

Covers the engine shapes (``Severity``/``RuleInput``/``Finding``/``TriageRule``),
the ``run_triage`` degradation contract, the ``only=`` input filter, and the
dotted ``_get`` accessor. No seed rule is exercised here — those live in
``test_triage_rules.py``.
"""

from __future__ import annotations

from enum import Flag

import pytest

from custom_components.adaptive_cover_pro.diagnostics.triage import (
    Finding,
    RuleInput,
    Severity,
    TriageRule,
    _get,
    render_report,
    run_triage,
)
from custom_components.adaptive_cover_pro.reason_i18n import Reason

pytestmark = pytest.mark.unit


def _rule(code, inputs, *, severity=Severity.WARNING, fix_step="position", check):
    return TriageRule(
        code=code,
        severity=severity,
        inputs=inputs,
        fix_step=fix_step,
        wiki="Troubleshooting#x",
        issues=(970,),
        check=check,
    )


# ---------------------------------------------------------------------------
# Degradation contract
# ---------------------------------------------------------------------------


def test_run_triage_empty_dict_returns_empty_list() -> None:
    assert run_triage({}) == []


def test_run_triage_all_none_values_returns_empty_list() -> None:
    view = {"options": None, "capabilities": None, "climate_conditions": None}
    assert run_triage(view) == []


def test_run_triage_never_raises_on_garbage() -> None:
    for view in ({}, {"options": None}, {"a": {"b": {"c": None}}}):
        assert run_triage(view) == []


# ---------------------------------------------------------------------------
# check yields 0 / 1 / N param dicts -> 0 / 1 / N findings
# ---------------------------------------------------------------------------


def test_stub_rule_yields_zero_findings() -> None:
    rule = _rule("triage.custom_safety_bypass", RuleInput.CONFIG, check=lambda _d: [])
    assert run_triage({}, rules=[rule]) == []


def test_stub_rule_yields_one_finding_with_reason_severity_fix_step() -> None:
    rule = _rule(
        "triage.custom_safety_bypass",
        RuleInput.CONFIG,
        severity=Severity.INFO,
        fix_step="custom_position",
        check=lambda _d: [{"slot": 5, "safety": 100}],
    )
    findings = run_triage({}, rules=[rule])
    assert len(findings) == 1
    (finding,) = findings
    assert isinstance(finding, Finding)
    assert finding.severity is Severity.INFO
    assert finding.fix_step == "custom_position"
    assert isinstance(finding.reason, Reason)
    assert finding.reason.code == "triage.custom_safety_bypass"
    assert dict(finding.reason.params) == {"slot": 5, "safety": 100}


def test_stub_rule_yields_n_findings_one_per_param_dict() -> None:
    params = [{"eid": "cover.a"}, {"eid": "cover.b"}, {"eid": "cover.c"}]
    rule = _rule(
        "triage.cover_not_ready", RuleInput.CONFIG, check=lambda _d: iter(params)
    )
    findings = run_triage({}, rules=[rule])
    assert [dict(f.reason.params) for f in findings] == params
    assert all(f.reason.code == "triage.cover_not_ready" for f in findings)


# ---------------------------------------------------------------------------
# only= input filter (subset semantics)
# ---------------------------------------------------------------------------


def test_only_config_excludes_runtime_and_mixed_rows() -> None:
    fire = lambda _d: [{}]  # noqa: E731
    config_rule = _rule("triage.custom_safety_bypass", RuleInput.CONFIG, check=fire)
    runtime_rule = _rule("triage.climate_temp_none", RuleInput.RUNTIME, check=fire)
    mixed_rule = _rule(
        "triage.summer_wont_close",
        RuleInput.CONFIG | RuleInput.RUNTIME,
        check=fire,
    )
    rules = [config_rule, runtime_rule, mixed_rule]

    only_config = run_triage({}, only=RuleInput.CONFIG, rules=rules)
    assert [f.reason.code for f in only_config] == ["triage.custom_safety_bypass"]

    only_runtime = run_triage({}, only=RuleInput.RUNTIME, rules=rules)
    assert [f.reason.code for f in only_runtime] == ["triage.climate_temp_none"]

    both = run_triage({}, only=RuleInput.CONFIG | RuleInput.RUNTIME, rules=rules)
    assert {f.reason.code for f in both} == {
        "triage.custom_safety_bypass",
        "triage.climate_temp_none",
        "triage.summer_wont_close",
    }

    unfiltered = run_triage({}, rules=rules)
    assert len(unfiltered) == 3


# ---------------------------------------------------------------------------
# a raising check is logged and skipped, never propagated
# ---------------------------------------------------------------------------


def test_raising_check_is_skipped_not_propagated() -> None:
    def boom(_data):
        raise ValueError("kaboom")

    bad = _rule("triage.custom_safety_bypass", RuleInput.CONFIG, check=boom)
    good = _rule(
        "triage.cover_not_ready",
        RuleInput.CONFIG,
        check=lambda _d: [{"eid": "cover.a"}],
    )
    findings = run_triage({}, rules=[bad, good])
    assert [f.reason.code for f in findings] == ["triage.cover_not_ready"]


# ---------------------------------------------------------------------------
# _get dotted accessor
# ---------------------------------------------------------------------------


def test_get_nested_hit() -> None:
    assert _get({"a": {"b": 1}}, "a.b") == 1


def test_get_missing_returns_none() -> None:
    assert _get({}, "x.y") is None
    assert _get({"a": {}}, "a.b") is None
    assert _get({"a": {"b": 1}}, "a.c") is None


def test_get_non_mapping_segment_returns_none() -> None:
    assert _get({"a": 5}, "a.b") is None
    assert _get({"a": "str"}, "a.b") is None


def test_get_falsy_leaf_values_pass_through() -> None:
    assert _get({"a": {"b": 0}}, "a.b") == 0
    assert _get({"a": {"b": False}}, "a.b") is False
    assert _get({"a": {"b": ""}}, "a.b") == ""


def test_get_single_segment() -> None:
    assert _get({"a": 1}, "a") == 1
    assert _get({}, "a") is None


# ---------------------------------------------------------------------------
# enum / dataclass shapes
# ---------------------------------------------------------------------------


def test_severity_members() -> None:
    assert {s.name for s in Severity} == {"CRITICAL", "WARNING", "INFO"}


def test_ruleinput_is_flag() -> None:
    assert issubclass(RuleInput, Flag)
    assert RuleInput.CONFIG != RuleInput.RUNTIME
    assert (RuleInput.CONFIG | RuleInput.RUNTIME) & RuleInput.CONFIG == RuleInput.CONFIG


def test_finding_is_frozen() -> None:
    finding = Finding(Reason("triage.x"), Severity.INFO, None)
    with pytest.raises(Exception):  # noqa: B017, PT011 - FrozenInstanceError
        finding.severity = Severity.WARNING  # type: ignore[misc]


def test_triage_rule_is_frozen() -> None:
    rule = _rule("triage.x", RuleInput.CONFIG, check=lambda _d: [])
    with pytest.raises(Exception):  # noqa: B017, PT011 - FrozenInstanceError
        rule.code = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# render_report — reuses reason_i18n.render, one bullet per finding
# ---------------------------------------------------------------------------


def test_render_report_empty_is_empty_string() -> None:
    assert render_report([]) == ""


def test_render_report_one_bullet_per_finding() -> None:
    # An unknown code renders (via reason_i18n) to the code string itself. The
    # report uses a plain bullet marker and never prepends a severity icon — the
    # template text carries its own leading emoji (see the double-icon test).
    findings = [
        Finding(Reason("triage.aaa"), Severity.WARNING, None),
        Finding(Reason("triage.bbb"), Severity.INFO, None),
    ]
    report = render_report(findings)
    lines = report.split("\n")
    assert len(lines) == 2
    assert lines[0] == "- triage.aaa"
    assert lines[1] == "- triage.bbb"


def test_render_report_uses_supplied_labels() -> None:
    labels = {"triage.aaa": "hello {who}"}
    findings = [Finding(Reason("triage.aaa", {"who": "world"}), Severity.INFO, None)]
    assert render_report(findings, labels) == "- hello world"


def test_render_report_does_not_double_severity_icon() -> None:
    # A real triage template already leads with its own severity emoji;
    # render_report must NOT prepend a second one (the Chunk 3 double-icon bug).
    labels = {"triage.warn": "⚠️ something is wrong"}
    findings = [Finding(Reason("triage.warn"), Severity.WARNING, None)]
    report = render_report(findings, labels)
    assert report == "- ⚠️ something is wrong"
    assert "⚠️ ⚠️" not in report
