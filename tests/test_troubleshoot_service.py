"""Tests for the get_troubleshooting service (issue #1059)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState

from custom_components.adaptive_cover_pro.const import DOMAIN, CoverType
from custom_components.adaptive_cover_pro.services.troubleshoot_service import (
    async_handle_get_troubleshooting,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_coordinator(
    entry_id="entry-1",
    name="Test Cover",
    cover_type=CoverType.BLIND,
    options=None,
    diagnostics=None,
):
    coord = MagicMock()
    coord.config_entry.entry_id = entry_id
    coord.config_entry.data = {"name": name, "sensor_type": cover_type}
    coord.config_entry.options = options if options is not None else {}
    coord.config_entry.domain = DOMAIN
    coord.entities = [f"cover.{name.lower().replace(' ', '_')}"]
    coord.data = MagicMock()
    coord.data.diagnostics = {} if diagnostics is None else diagnostics
    return coord


def make_hass(*coordinators, language="en"):
    hass = MagicMock()
    hass.config.language = language
    entries = []
    for coord in coordinators:
        entry = MagicMock()
        entry.entry_id = coord.config_entry.entry_id
        entry.runtime_data = coord
        entry.state = ConfigEntryState.LOADED
        entries.append(entry)
    hass.config_entries.async_entries = MagicMock(return_value=entries)
    hass.config_entries.async_get_entry.side_effect = lambda eid: next(
        (
            MagicMock(domain=DOMAIN, entry_id=eid)
            for coord in coordinators
            if coord.config_entry.entry_id == eid
        ),
        None,
    )

    async def _run_executor(func, *args):
        return func(*args)

    hass.async_add_executor_job = _run_executor
    return hass


def make_call(hass, data=None):
    call = MagicMock()
    call.hass = hass
    call.data = data or {}
    return call


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_versioned_envelope():
    """Response always has version, generated_at, count, entries."""
    coord = make_coordinator()
    hass = make_hass(coord)
    call = make_call(hass)

    result = await async_handle_get_troubleshooting(call)

    assert result["version"] == 1
    assert "generated_at" in result
    assert "count" in result
    assert "entries" in result


@pytest.mark.asyncio
async def test_entry_keyed_by_config_entry_id():
    """Single coordinator -> one entry keyed by its config entry ID."""
    coord = make_coordinator(entry_id="abc-123")
    hass = make_hass(coord)
    call = make_call(hass)

    result = await async_handle_get_troubleshooting(call)

    assert result["count"] == 1
    assert "abc-123" in result["entries"]
    assert result["entries"]["abc-123"]["config_entry_id"] == "abc-123"
    assert result["entries"]["abc-123"]["name"] == "Test Cover"


@pytest.mark.asyncio
async def test_findings_include_code_severity_fix_step_wiki_message():
    """A firing rule renders a fully-shaped finding dict."""
    coord = make_coordinator(diagnostics={"debug_config": {"dry_run": True}})
    hass = make_hass(coord)
    call = make_call(hass)

    result = await async_handle_get_troubleshooting(call)

    findings = result["entries"]["entry-1"]["findings"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["code"] == "triage.dry_run_left_on"
    assert isinstance(finding["params"], dict)
    assert finding["severity"] == "warning"
    assert isinstance(finding["severity"], str)
    assert finding["fix_step"] == "debug"
    assert finding["wiki"] == "Troubleshooting-Findings#dry-run-left-on"
    assert "Dry-run mode is on" in finding["message"]


@pytest.mark.asyncio
async def test_no_findings_returns_empty_list_and_no_issues_report():
    """No findings -> empty findings list and the "all good" report text."""
    coord = make_coordinator()
    hass = make_hass(coord)
    call = make_call(hass)

    result = await async_handle_get_troubleshooting(call)

    entry = result["entries"]["entry-1"]
    assert entry["findings"] == []
    assert "No configuration or runtime issues detected" in entry["report"]


@pytest.mark.asyncio
async def test_unknown_explicit_entry_raises():
    """Explicit config_entry_id that doesn't exist raises ServiceValidationError."""
    from homeassistant.exceptions import ServiceValidationError

    hass = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[])
    hass.config_entries.async_get_entry.return_value = None
    call = make_call(hass, data={"config_entry_id": ["nonexistent-id"]})

    with pytest.raises(ServiceValidationError):
        await async_handle_get_troubleshooting(call)


@pytest.mark.asyncio
async def test_explicit_config_entry_id_targets_single_coordinator():
    """config_entry_id field bypasses entity/device target resolution."""
    coord1 = make_coordinator(entry_id="e1", name="North")
    coord2 = make_coordinator(entry_id="e2", name="South")
    hass = make_hass(coord1, coord2)
    hass.config_entries.async_get_entry.side_effect = lambda eid: (
        MagicMock(domain=DOMAIN, entry_id=eid) if eid == "e1" else None
    )
    call = make_call(hass, data={"config_entry_id": ["e1"]})

    result = await async_handle_get_troubleshooting(call)

    assert result["count"] == 1
    assert "e1" in result["entries"]
    assert "e2" not in result["entries"]


def test_translations_contain_get_troubleshooting_key():
    """en.json, de.json, and fr.json all contain the services.get_troubleshooting key."""
    import json
    from pathlib import Path

    translations_dir = (
        Path(__file__).parent.parent
        / "custom_components"
        / "adaptive_cover_pro"
        / "translations"
    )
    for lang in ("en", "de", "fr"):
        data = json.loads((translations_dir / f"{lang}.json").read_text())
        assert "get_troubleshooting" in data.get(
            "services", {}
        ), f"{lang}.json missing services.get_troubleshooting"
