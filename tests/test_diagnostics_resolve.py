"""Tests for the read-only diagnostics resolver (issue #970, Phase 1).

The safety invariant under test: no resolve path may ever call
``coordinator.async_refresh`` — that runs the full update cycle and can move a
blind. Every test that touches a coordinator asserts ``async_refresh`` was NOT
called.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pytest

import custom_components.adaptive_cover_pro.services as services
from custom_components.adaptive_cover_pro.const import DIAG_CACHE_KEY
from custom_components.adaptive_cover_pro.diagnostics.resolve import (
    DiagnosticsRead,
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
