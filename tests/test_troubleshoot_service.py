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
async def test_group_config_entry_id_raises_service_validation_error():
    """Explicitly targeting a cover group's config_entry_id raises a clear
    ServiceValidationError instead of an AttributeError from ``coord.data.diagnostics``
    (issue #1059, finding #1). ``_resolve_by_config_entry`` must resolve through
    ``cover_coordinators`` (which filters out groups/building profiles) rather
    than ``loaded_coordinators`` (which does not) — a ``GroupCoordinator``'s
    ``.data`` is ``GroupAggregates``, which has no ``diagnostics`` attribute.
    """
    from homeassistant.exceptions import ServiceValidationError

    from custom_components.adaptive_cover_pro.group_coordinator import (
        GroupCoordinator,
    )

    group_coord = MagicMock()
    group_coord.__class__ = GroupCoordinator  # isinstance(..., GroupCoordinator) → True
    group_coord.config_entry.entry_id = "group-1"

    entry = MagicMock()
    entry.entry_id = "group-1"
    entry.runtime_data = group_coord
    entry.state = ConfigEntryState.LOADED

    hass = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[entry])
    hass.config_entries.async_get_entry.return_value = MagicMock(
        domain=DOMAIN, entry_id="group-1"
    )
    call = make_call(hass, data={"config_entry_id": ["group-1"]})

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


@pytest.mark.asyncio
async def test_one_bad_entry_does_not_blank_the_others():
    """One coordinator has a corrupt ``sensor_type`` that ``get_policy`` can't
    resolve (raises ``ValueError`` inside ``build_troubleshoot_result``) — the
    other targeted coordinator still comes back, and the failing one degrades
    to an error entry rather than losing the whole response (issue #1059,
    finding #3).
    """
    good = make_coordinator(entry_id="good-1", name="Good Cover")
    bad = make_coordinator(
        entry_id="bad-1", name="Bad Cover", cover_type="not_a_real_cover_type"
    )
    hass = make_hass(good, bad)
    call = make_call(hass)

    result = await async_handle_get_troubleshooting(call)

    assert result["count"] == 2
    good_entry = result["entries"]["good-1"]
    assert "findings" in good_entry
    assert "error" not in good_entry
    bad_entry = result["entries"]["bad-1"]
    assert bad_entry["config_entry_id"] == "bad-1"
    assert bad_entry["name"] == "Bad Cover"
    assert "error" in bad_entry
    assert "findings" not in bad_entry


@pytest.mark.asyncio
async def test_missing_sensor_type_falls_back_to_blind_without_raising():
    """A falsy ``sensor_type`` (group/orchestrator or not-yet-configured cover)
    falls back to ``CoverType.BLIND`` — the same default the options flow uses
    — instead of raising when ``get_policy(None)`` is called (issue #1059,
    finding #3).
    """
    coord = make_coordinator(entry_id="e1", cover_type=None)
    hass = make_hass(coord)
    call = make_call(hass)

    result = await async_handle_get_troubleshooting(call)

    entry = result["entries"]["e1"]
    assert "error" not in entry
    assert entry["findings"] == []


@pytest.mark.asyncio
async def test_read_error_is_surfaced_not_discarded():
    """When ``read_from_coordinator`` reports an error (``coord.data`` is None
    and a rebuild raises), the entry still gets a normal troubleshoot result
    (CONFIG rules can still run against the empty payload) AND carries the raw
    error string — mirroring ``get_diagnostics``'s existing ``read.error``
    surfacing rather than silently discarding it (issue #1059, finding #3).
    """
    coord = make_coordinator(entry_id="e1")
    coord.data = None
    coord.build_diagnostic_data.side_effect = RuntimeError("update in progress")
    hass = make_hass(coord)
    call = make_call(hass)

    result = await async_handle_get_troubleshooting(call)

    entry = result["entries"]["e1"]
    assert entry["source"] == "unavailable"
    assert "error" in entry
    assert "diagnostics_unavailable" in entry["error"]
    assert "Diagnostics aren't available" in entry["report"]


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
