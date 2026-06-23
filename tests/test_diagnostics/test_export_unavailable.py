"""Tests for the diagnostics export null-case marker (issue #656).

When a user downloads diagnostics before the coordinator has completed an
update cycle, ``coordinator.data`` is None. The export must surface an explicit
"unavailable" marker (with the sanitized event buffer when present) instead of a
bare ``None`` that gives no diagnostic clue about why the snapshot is empty.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.adaptive_cover_pro.const import DOMAIN
from custom_components.adaptive_cover_pro.diagnostics import (
    async_get_config_entry_diagnostics,
)


def _make_entry(entry_id: str = "entry-1") -> MagicMock:
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = entry_id
    entry.data = {"name": "Test"}
    entry.options = {"opt": 1}
    return entry


def _make_hass(coordinator, entry_id: str = "entry-1") -> MagicMock:
    hass = MagicMock(spec=HomeAssistant)
    data = (
        {DOMAIN: {entry_id: coordinator}} if coordinator is not None else {DOMAIN: {}}
    )
    hass.data = data
    return hass


@pytest.mark.asyncio
async def test_data_none_emits_marker_with_event_timeline():
    """coordinator.data is None → status unavailable + sanitized event buffer."""
    entry = _make_entry()
    events = [
        {"ts": "2026-06-22T20:00:00+00:00", "event": "manual_override_armed"},
        {"ts": "2026-06-22T20:05:00+00:00", "event": "manual_override_cleared"},
    ]
    coordinator = MagicMock()
    coordinator.data = None
    coordinator._event_buffer.snapshot.return_value = events

    hass = _make_hass(coordinator)
    result = await async_get_config_entry_diagnostics(hass, entry)

    diag = result["diagnostics"]
    assert diag is not None, "marker must not be a bare None"
    assert diag["status"] == "unavailable"
    assert "no completed update cycle" in diag["reason"]
    assert diag["event_timeline"] == events


@pytest.mark.asyncio
async def test_coordinator_missing_emits_marker_without_timeline():
    """No coordinator at all → status unavailable, reason mentions missing, no timeline."""
    entry = _make_entry()
    hass = _make_hass(None)

    result = await async_get_config_entry_diagnostics(hass, entry)

    diag = result["diagnostics"]
    assert diag is not None
    assert diag["status"] == "unavailable"
    assert "coordinator missing" in diag["reason"]
    assert "event_timeline" not in diag


@pytest.mark.asyncio
async def test_data_none_without_event_buffer_falls_back_to_reason_only():
    """A coordinator stub lacking _event_buffer → marker without timeline, no raise."""
    entry = _make_entry()
    coordinator = MagicMock(spec=["data"])
    coordinator.data = None

    hass = _make_hass(coordinator)
    result = await async_get_config_entry_diagnostics(hass, entry)

    diag = result["diagnostics"]
    assert diag is not None
    assert diag["status"] == "unavailable"
    assert "no completed update cycle" in diag["reason"]
    assert "event_timeline" not in diag


@pytest.mark.asyncio
async def test_data_present_returns_sanitized_passthrough_not_marker():
    """coordinator.data.diagnostics present → sanitized pass-through, NOT the marker."""
    entry = _make_entry()
    coordinator = MagicMock()
    coordinator.data.diagnostics = {"control_status": "sun_tracking", "position": 42}

    hass = _make_hass(coordinator)
    result = await async_get_config_entry_diagnostics(hass, entry)

    diag = result["diagnostics"]
    assert diag["control_status"] == "sun_tracking"
    assert diag["position"] == 42
    assert "status" not in diag or diag.get("status") != "unavailable"
