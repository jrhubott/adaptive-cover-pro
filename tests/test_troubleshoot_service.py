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


def _make_unresolvable_entry(entry_id, title, state, runtime_data=None):
    """Build a MagicMock ``ConfigEntry`` for an id that must NOT resolve to a
    cover coordinator — used by the three degrade-not-raise cases below.
    """
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.domain = DOMAIN
    entry.title = title
    entry.state = state
    entry.runtime_data = runtime_data
    return entry


def _make_multi_entry_hass(good_coord, *unresolvable_entries):
    """Build a hass whose ``config_entries`` roster mixes one real cover
    coordinator with one or more unresolvable entries (issue #1059 audit
    round 3, defect #1: multiple entries in one explicit ``config_entry_id``
    call — a single unresolvable one must not sink the others).
    """
    good_entry = MagicMock()
    good_entry.entry_id = good_coord.config_entry.entry_id
    good_entry.domain = DOMAIN
    good_entry.title = good_coord.config_entry.data.get("name")
    good_entry.runtime_data = good_coord
    good_entry.state = ConfigEntryState.LOADED

    all_entries = [good_entry, *unresolvable_entries]
    by_id = {entry.entry_id: entry for entry in all_entries}

    hass = MagicMock()
    hass.config.language = "en"
    hass.config_entries.async_entries = MagicMock(return_value=all_entries)
    hass.config_entries.async_get_entry.side_effect = lambda eid: by_id.get(eid)

    async def _run_executor(func, *args):
        return func(*args)

    hass.async_add_executor_job = _run_executor
    return hass


@pytest.mark.asyncio
async def test_group_config_entry_id_degrades_instead_of_raising():
    """Explicitly targeting a cover group's config_entry_id degrades to a
    per-entry error entry instead of raising ``ServiceValidationError`` for
    the WHOLE call — and the other explicitly-targeted, resolvable entry in
    the same call still comes back (issue #1059 audit round 3, defect #1: an
    ``else: raise`` here previously made an explicit multi-entry call
    all-or-nothing). ``_resolve_by_config_entry`` must resolve through
    ``cover_coordinators`` (which filters out groups/building profiles) rather
    than ``loaded_coordinators`` (which does not) — a ``GroupCoordinator``'s
    ``.data`` is ``GroupAggregates``, which has no ``diagnostics`` attribute.
    """
    from custom_components.adaptive_cover_pro.group_coordinator import (
        GroupCoordinator,
    )

    good = make_coordinator(entry_id="good-1", name="Good Cover")
    group_coord = MagicMock()
    group_coord.__class__ = GroupCoordinator  # isinstance(..., GroupCoordinator) → True
    group_coord.config_entry.entry_id = "group-1"
    group_entry = _make_unresolvable_entry(
        "group-1", "Group Cover", ConfigEntryState.LOADED, runtime_data=group_coord
    )
    hass = _make_multi_entry_hass(good, group_entry)
    call = make_call(hass, data={"config_entry_id": ["good-1", "group-1"]})

    result = await async_handle_get_troubleshooting(call)

    assert result["count"] == 2
    assert result["entries"]["good-1"]["error"] is None
    group_result = result["entries"]["group-1"]
    assert group_result["config_entry_id"] == "group-1"
    assert group_result["name"] == "Group Cover"
    assert group_result["source"] == "error"
    assert group_result["findings"] == []
    assert "cover group" in group_result["error"].lower()
    assert "cover group" in group_result["report"].lower()


@pytest.mark.asyncio
async def test_building_profile_config_entry_id_degrades_instead_of_raising():
    """Explicitly targeting a Building Profile's config_entry_id degrades to a
    per-entry error entry instead of raising (issue #1059 audit round 3,
    defect #1) — and the other explicitly-targeted, resolvable entry in the
    same call still comes back.

    A Building Profile is a virtual config entry: ``async_setup_entry``
    returns early for it (``not policy.controls_cover``) without ever setting
    ``entry.runtime_data``, so it is absent from BOTH ``cover_coordinators``
    AND ``loaded_coordinators`` — unlike a cover group, which sets
    ``runtime_data`` to a ``GroupCoordinator`` and is only filtered out of the
    former.
    """
    good = make_coordinator(entry_id="good-1", name="Good Cover")
    profile_entry = _make_unresolvable_entry(
        "profile-1", "Profile Cover", ConfigEntryState.LOADED, runtime_data=None
    )
    hass = _make_multi_entry_hass(good, profile_entry)
    call = make_call(hass, data={"config_entry_id": ["good-1", "profile-1"]})

    result = await async_handle_get_troubleshooting(call)

    assert result["count"] == 2
    assert result["entries"]["good-1"]["error"] is None
    profile_result = result["entries"]["profile-1"]
    assert profile_result["config_entry_id"] == "profile-1"
    assert profile_result["name"] == "Profile Cover"
    assert profile_result["source"] == "error"
    assert profile_result["findings"] == []
    assert "building profile" in profile_result["error"].lower()
    assert "building profile" in profile_result["report"].lower()


@pytest.mark.asyncio
async def test_not_loaded_config_entry_id_degrades_instead_of_raising():
    """Explicitly targeting a config entry that is mid-reload / ``SETUP_RETRY``
    / disabled (state is not ``LOADED``) degrades to a per-entry error entry
    instead of raising — and the other explicitly-targeted, resolvable entry
    in the same call still comes back (issue #1059 audit round 3, defect #1:
    the widened ``else: raise`` regressed exactly this case, which shipped
    working on ``develop`` — a single mid-reload instance used to zero the
    whole multi-entry response).
    """
    good = make_coordinator(entry_id="good-1", name="Good Cover")
    retry_entry = _make_unresolvable_entry(
        "retry-1", "Retry Cover", ConfigEntryState.SETUP_RETRY, runtime_data=None
    )
    hass = _make_multi_entry_hass(good, retry_entry)
    call = make_call(hass, data={"config_entry_id": ["good-1", "retry-1"]})

    result = await async_handle_get_troubleshooting(call)

    assert result["count"] == 2
    assert result["entries"]["good-1"]["error"] is None
    retry_result = result["entries"]["retry-1"]
    assert retry_result["config_entry_id"] == "retry-1"
    assert retry_result["name"] == "Retry Cover"
    assert retry_result["source"] == "error"
    assert retry_result["findings"] == []
    assert "not currently loaded" in retry_result["error"].lower()
    assert "setup_retry" in retry_result["error"].lower()
    assert "not currently loaded" in retry_result["report"].lower()


@pytest.mark.asyncio
async def test_unresolvable_config_entry_reasons_are_distinguishable_per_case():
    """The three ways an explicit ``config_entry_id`` can fail to resolve to a
    cover coordinator — cover group, Building Profile, not currently loaded —
    must each carry their own accurate, distinguishable reason text, not one
    generic message (issue #1059 audit round 3, defect #1 design requirement).
    """
    from custom_components.adaptive_cover_pro.group_coordinator import (
        GroupCoordinator,
    )

    good = make_coordinator(entry_id="good-1", name="Good Cover")

    group_coord = MagicMock()
    group_coord.__class__ = GroupCoordinator
    group_coord.config_entry.entry_id = "group-1"
    group_entry = _make_unresolvable_entry(
        "group-1", "Group Cover", ConfigEntryState.LOADED, runtime_data=group_coord
    )
    profile_entry = _make_unresolvable_entry(
        "profile-1", "Profile Cover", ConfigEntryState.LOADED, runtime_data=None
    )
    retry_entry = _make_unresolvable_entry(
        "retry-1", "Retry Cover", ConfigEntryState.SETUP_RETRY, runtime_data=None
    )
    hass = _make_multi_entry_hass(good, group_entry, profile_entry, retry_entry)
    call = make_call(
        hass, data={"config_entry_id": ["good-1", "group-1", "profile-1", "retry-1"]}
    )

    result = await async_handle_get_troubleshooting(call)

    assert result["count"] == 4
    reasons = {
        entry_id: result["entries"][entry_id]["error"]
        for entry_id in ("group-1", "profile-1", "retry-1")
    }
    # All three reasons are distinct strings...
    assert len(set(reasons.values())) == 3
    # ...and each names its own specific case accurately.
    assert "cover group" in reasons["group-1"].lower()
    assert "building profile" in reasons["profile-1"].lower()
    assert "not currently loaded" in reasons["retry-1"].lower()


@pytest.mark.asyncio
async def test_unknown_explicit_entry_still_raises_alongside_other_entries():
    """An id that is not an ACP config entry at all (typo, wrong integration,
    nonexistent) is a genuine caller error with no instance to attach a
    degraded entry to — it still raises ``ServiceValidationError`` immediately,
    unlike the three degrade-not-raise cases above (issue #1059 audit round 3,
    defect #1 design decision).
    """
    from homeassistant.exceptions import ServiceValidationError

    good = make_coordinator(entry_id="good-1", name="Good Cover")
    hass = _make_multi_entry_hass(good)
    call = make_call(hass, data={"config_entry_id": ["good-1", "nonexistent-id"]})

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

    Both entries share exactly one shape (issue #1059 audit, finding #1): every
    key — ``config_entry_id``, ``name``, ``source``, ``report``, ``findings``,
    ``error`` — is always present. The degraded entry's ``source`` is
    ``"error"`` (nothing ran at all), distinct from ``"unavailable"`` (a
    partial-but-valid CONFIG-only result) — finding #2's structural
    discriminator.
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
    assert good_entry["error"] is None
    bad_entry = result["entries"]["bad-1"]
    assert bad_entry["config_entry_id"] == "bad-1"
    assert bad_entry["name"] == "Bad Cover"
    assert bad_entry["source"] == "error"
    assert bad_entry["findings"] == []
    assert "Troubleshooting could not run" in bad_entry["report"]
    assert "error" in bad_entry
    assert "troubleshoot_unavailable" in bad_entry["error"]
    # Every entry has exactly the documented six-key contract, whichever
    # branch produced it — locked against a literal so dropping a key from
    # BOTH branches in lockstep can't slip through (issue #1059 audit round
    # 3, nit #7: comparing the two branches' key sets against each other only
    # proves they agree, not that the contract itself holds).
    six_key_contract = {
        "config_entry_id",
        "name",
        "source",
        "report",
        "findings",
        "error",
    }
    assert set(good_entry.keys()) == six_key_contract
    assert set(bad_entry.keys()) == six_key_contract


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
    assert entry["error"] is None
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


@pytest.mark.asyncio
async def test_response_is_json_serializable_in_every_shape():
    """The response crosses the HA websocket API, so it must be JSON-safe in
    every shape the service can produce: a fully healthy entry, a
    degraded-by-exception entry (``source == "error"``), and a
    degraded-by-read-error entry (``source == "unavailable"``) — issue #1059
    audit findings #1/#2.

    Uses the stdlib ``json`` module deliberately, NOT
    ``homeassistant.helpers.json.json_dumps`` (issue #1059 audit round 3,
    defect #2): HA's serializer is orjson-backed and serializes a raw
    ``enum.Enum`` natively, so it would pass even if ``_finding_to_dict``
    forgot ``.value`` and put a raw ``Severity`` member into the response —
    exactly the invariant this test exists to protect. Stdlib ``json`` is
    strict and raises ``TypeError`` on a raw ``Enum``, so it actually catches
    that regression.
    """
    import json

    healthy = make_coordinator(
        entry_id="healthy-1", diagnostics={"debug_config": {"dry_run": True}}
    )
    unavailable = make_coordinator(entry_id="unavailable-1")
    unavailable.data = None
    unavailable.build_diagnostic_data.side_effect = RuntimeError("update in progress")
    errored = make_coordinator(entry_id="errored-1", cover_type="not_a_real_cover_type")
    hass = make_hass(healthy, unavailable, errored)
    call = make_call(hass)

    result = await async_handle_get_troubleshooting(call)

    assert result["entries"]["healthy-1"]["error"] is None
    assert result["entries"]["unavailable-1"]["source"] == "unavailable"
    assert result["entries"]["errored-1"]["source"] == "error"

    serialized = json.dumps(result)
    assert isinstance(serialized, str)


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
