"""Tests for the read-only diagnostics resolver (issue #970, Phase 1).

The safety invariant under test: no resolve path may ever call
``coordinator.async_refresh`` — that runs the full update cycle and can move a
blind. Every test that touches a coordinator asserts ``async_refresh`` was NOT
called.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock, patch

import pytest

import custom_components.adaptive_cover_pro.services as services
from custom_components.adaptive_cover_pro.const import DIAG_CACHE_KEY, CoverType
from custom_components.adaptive_cover_pro.diagnostics.resolve import (
    RESOLVE_READ_SOURCES,
    DiagnosticsRead,
    build_troubleshoot_result,
    read_diagnostics,
    read_from_coordinator,
)

# ---------------------------------------------------------------------------
# DiagnosticsRead shape
# ---------------------------------------------------------------------------


def test_diagnostics_read_is_frozen():
    """DiagnosticsRead is a frozen dataclass — assignment raises."""
    read = DiagnosticsRead(payload=None, source="unavailable")
    with pytest.raises(dataclasses.FrozenInstanceError):
        read.source = "coordinator"


def test_diagnostics_read_uses_slots():
    """DiagnosticsRead uses __slots__ — no per-instance __dict__."""
    read = DiagnosticsRead(payload=None, source="unavailable")
    assert not hasattr(read, "__dict__")


def test_diagnostics_read_error_defaults_none():
    """Error defaults to None when omitted."""
    read = DiagnosticsRead(payload={"a": 1}, source="coordinator")
    assert read.error is None


def test_resolve_read_sources_enumerates_the_vocabulary():
    """RESOLVE_READ_SOURCES is the one authoritative list of every value
    DiagnosticsRead/TroubleshootResult ``source`` can carry (issue #1059
    audit round 3, nit #4) — locked against a literal so a new source value
    added to one docstring but not the other can't slip through silently.
    """
    assert RESOLVE_READ_SOURCES == ("coordinator", "built", "cache", "unavailable")


# ---------------------------------------------------------------------------
# read_from_coordinator
# ---------------------------------------------------------------------------


def test_prefers_coordinator_data():
    """When coord.data is not None, returns coord.data.diagnostics, source coordinator."""
    coord = MagicMock()
    coord.data.diagnostics = {"a": 1}
    read = read_from_coordinator(coord)
    assert read.payload == {"a": 1}
    assert read.source == "coordinator"
    assert read.error is None
    coord.async_refresh.assert_not_called()
    coord.build_diagnostic_data.assert_not_called()


def test_builds_when_data_none():
    """When coord.data is None, calls build_diagnostic_data, source built."""
    coord = MagicMock()
    coord.data = None
    coord.build_diagnostic_data.return_value = {"b": 2}
    read = read_from_coordinator(coord)
    assert read.payload == {"b": 2}
    assert read.source == "built"
    assert read.error is None
    coord.async_refresh.assert_not_called()


def test_wraps_build_exception_into_error():
    """A raising build is wrapped into a populated error (never propagates)."""
    coord = MagicMock()
    coord.data = None
    coord.build_diagnostic_data.side_effect = RuntimeError("update in progress")
    read = read_from_coordinator(coord)
    assert read.payload is None
    assert read.source == "unavailable"
    assert read.error is not None
    assert "diagnostics_unavailable" in read.error
    assert "update in progress" in read.error
    coord.async_refresh.assert_not_called()


# ---------------------------------------------------------------------------
# read_diagnostics
# ---------------------------------------------------------------------------


def test_read_diagnostics_delegates_to_coordinator(monkeypatch):
    """Resolves the coordinator via cover_coordinators and delegates to it."""
    coord = MagicMock()
    coord.data.diagnostics = {"x": 1}
    monkeypatch.setattr(services, "cover_coordinators", lambda hass: {"e1": coord})
    read = read_diagnostics(MagicMock(), "e1")
    assert read.payload == {"x": 1}
    assert read.source == "coordinator"
    coord.async_refresh.assert_not_called()


def test_read_diagnostics_cache_fallback(monkeypatch):
    """No coordinator → falls back to the DIAG_CACHE_KEY snapshot, source cache."""
    monkeypatch.setattr(services, "cover_coordinators", lambda hass: {})
    hass = MagicMock()
    hass.data = {DIAG_CACHE_KEY: {"e1": {"diagnostics": {"cached": True}}}}
    read = read_diagnostics(hass, "e1")
    assert read.payload == {"cached": True}
    assert read.source == "cache"
    assert read.error is None


def test_read_diagnostics_unavailable_when_nothing(monkeypatch):
    """No coordinator and no cache → source unavailable, payload None."""
    monkeypatch.setattr(services, "cover_coordinators", lambda hass: {})
    hass = MagicMock()
    hass.data = {}
    read = read_diagnostics(hass, "e1")
    assert read.payload is None
    assert read.source == "unavailable"


def test_read_diagnostics_cache_missing_entry(monkeypatch):
    """Cache present but no entry for this entry_id → unavailable."""
    monkeypatch.setattr(services, "cover_coordinators", lambda hass: {})
    hass = MagicMock()
    hass.data = {DIAG_CACHE_KEY: {"other": {"diagnostics": {"x": 1}}}}
    read = read_diagnostics(hass, "e1")
    assert read.payload is None
    assert read.source == "unavailable"


# ---------------------------------------------------------------------------
# Safety invariant — async_refresh is NEVER called on ANY path
# ---------------------------------------------------------------------------


def test_async_refresh_never_called_on_any_path(monkeypatch):
    """The whole point: no resolve path may trigger an update cycle."""
    # coordinator (data present) path
    coord_data = MagicMock()
    coord_data.data.diagnostics = {"a": 1}
    read_from_coordinator(coord_data)
    coord_data.async_refresh.assert_not_called()

    # built (data None) path
    coord_built = MagicMock()
    coord_built.data = None
    coord_built.build_diagnostic_data.return_value = {"b": 2}
    read_from_coordinator(coord_built)
    coord_built.async_refresh.assert_not_called()

    # built-raises path
    coord_err = MagicMock()
    coord_err.data = None
    coord_err.build_diagnostic_data.side_effect = RuntimeError("boom")
    read_from_coordinator(coord_err)
    coord_err.async_refresh.assert_not_called()

    # via read_diagnostics
    spy = MagicMock()
    spy.data.diagnostics = {"z": 9}
    monkeypatch.setattr(services, "cover_coordinators", lambda hass: {"e1": spy})
    read_diagnostics(MagicMock(), "e1")
    spy.async_refresh.assert_not_called()


# ---------------------------------------------------------------------------
# build_troubleshoot_result — the shared troubleshoot seam (issue #1059)
# ---------------------------------------------------------------------------


def test_build_troubleshoot_result_no_findings_uses_no_issues_text():
    """No findings and a live source renders the "all good" envelope text."""
    read = DiagnosticsRead(payload={}, source="coordinator")
    result = build_troubleshoot_result(
        MagicMock(),
        read,
        options={},
        sensor_type=CoverType.BLIND,
        labels=None,
    )
    assert result.findings == []
    assert result.source == "coordinator"
    assert "No configuration or runtime issues detected" in result.report


def test_build_troubleshoot_result_unavailable_source_uses_unavailable_text():
    """An unavailable read with no CONFIG findings renders only the preamble."""
    read = DiagnosticsRead(payload=None, source="unavailable")
    result = build_troubleshoot_result(
        MagicMock(),
        read,
        options={},
        sensor_type=CoverType.BLIND,
        labels=None,
    )
    assert result.findings == []
    assert result.source == "unavailable"
    assert "Diagnostics aren't available" in result.report


def test_build_troubleshoot_result_renders_findings_via_labels():
    """A firing rule (dry-run left on) produces a Finding and shows in the report."""
    read = DiagnosticsRead(
        payload={"debug_config": {"dry_run": True}}, source="coordinator"
    )
    result = build_troubleshoot_result(
        MagicMock(),
        read,
        options={},
        sensor_type=CoverType.BLIND,
        labels=None,
    )
    assert len(result.findings) == 1
    assert result.findings[0].reason.code == "triage.dry_run_left_on"
    assert "Dry-run mode is on" in result.report


def test_build_troubleshoot_result_unavailable_gates_out_mixed_config_runtime_rule():
    """The ``only=RuleInput.CONFIG if unavailable else None`` gate (resolve.py)
    actually suppresses a mixed ``CONFIG | RUNTIME`` rule when the source is
    unavailable — the exact extraction risk the evidence packet's
    REGRESSION_CHECK names, previously unexercised by any test (issue #1059,
    finding #2). CLOUD_OR_SEMANTICS (rule 7) is tagged ``CONFIG | RUNTIME`` and
    fires off options alone (more than one cloud/low-light input configured),
    so it is a rule that COULD run with only options data but must not when
    ``only=RuleInput.CONFIG`` drops any rule that also reads RUNTIME.
    """
    options = {
        "entities": [],
        "lux_entity": "sensor.lux",
        "irradiance_entity": "sensor.irr",
    }

    # Gated: source is unavailable → only=RuleInput.CONFIG drops this mixed rule.
    unavailable_result = build_troubleshoot_result(
        MagicMock(),
        DiagnosticsRead(payload=None, source="unavailable"),
        options=options,
        sensor_type=CoverType.BLIND,
        labels=None,
    )
    unavailable_codes = [f.reason.code for f in unavailable_result.findings]
    assert "triage.cloud_or_semantics" not in unavailable_codes

    # Sibling assertion: the SAME options fire the same rule when the source is
    # NOT unavailable (only=None, every rule runs) — proves the gate itself is
    # what suppressed the finding above, not merely an absence of data.
    available_result = build_troubleshoot_result(
        MagicMock(),
        DiagnosticsRead(payload={}, source="coordinator"),
        options=options,
        sensor_type=CoverType.BLIND,
        labels=None,
    )
    available_codes = [f.reason.code for f in available_result.findings]
    assert "triage.cloud_or_semantics" in available_codes


def test_troubleshoot_view_carries_open_blocks_sun():
    # Rule 26 (WEATHER_OVERRIDE_INVERTED) reads a policy-derived
    # ``open_blocks_sun`` field folded into the view at the HA boundary — NOT a
    # cover-type string in the triage engine. Mirrors
    # test_troubleshoot_view_carries_policy_axis_requirements (rule 13, in
    # test_config_flow_troubleshoot.py) but proves the injection through this
    # module's own seam, build_troubleshoot_result.
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    captured: dict = {}

    def _spy_run_triage(view, **kwargs):
        captured["view"] = view
        return []

    with patch(
        "custom_components.adaptive_cover_pro.diagnostics.triage.run_triage",
        side_effect=_spy_run_triage,
    ):
        build_troubleshoot_result(
            MagicMock(),
            DiagnosticsRead(payload={}, source="coordinator"),
            options={},
            sensor_type=CoverType.BLIND,
            labels=None,
        )

    assert "open_blocks_sun" in captured["view"]
    assert (
        captured["view"]["open_blocks_sun"]
        == get_policy(CoverType.BLIND).axes[0].open_blocks_sun
    )
