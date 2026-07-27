"""Tests for the get_diagnostics service."""

from __future__ import annotations

import datetime as dt
import enum
import json
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState

from custom_components.adaptive_cover_pro.services.diagnostics_service import (
    async_handle_get_diagnostics,
)
from custom_components.adaptive_cover_pro.const import DOMAIN

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_coordinator(entry_id="entry-1", name="Test Cover", cover_type="cover_blind"):
    coord = MagicMock()
    coord.config_entry.entry_id = entry_id
    coord.config_entry.data = {"name": name}
    coord.config_entry.domain = DOMAIN
    coord._cover_type = cover_type  # noqa: SLF001
    coord.last_update_success = True
    coord._last_update_success_time = dt.datetime(
        2026, 4, 28, 12, 0, 0, tzinfo=dt.UTC
    )  # noqa: SLF001
    coord.entities = [f"cover.{name.lower().replace(' ', '_')}"]
    coord.data = MagicMock()
    coord.data.diagnostics = {
        "pipeline": {"handler": "solar"},
        "sun": {"elevation": 25.5},
    }
    return coord


def make_hass(*coordinators):
    hass = MagicMock()
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

    result = await async_handle_get_diagnostics(call)

    assert result["version"] == 1
    assert "generated_at" in result
    assert "count" in result
    assert "entries" in result


@pytest.mark.asyncio
async def test_entry_keyed_by_config_entry_id():
    """Single coordinator → one entry keyed by its config entry ID."""
    coord = make_coordinator(entry_id="abc-123")
    hass = make_hass(coord)
    call = make_call(hass)

    result = await async_handle_get_diagnostics(call)

    assert result["count"] == 1
    assert "abc-123" in result["entries"]
    assert result["entries"]["abc-123"]["config_entry_id"] == "abc-123"
    assert result["entries"]["abc-123"]["name"] == "Test Cover"
    assert result["entries"]["abc-123"]["cover_type"] == "cover_blind"


@pytest.mark.asyncio
async def test_no_coordinators_returns_empty_envelope():
    """No ACP instances → count 0, empty entries, no exception."""
    hass = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[])
    call = make_call(hass)

    result = await async_handle_get_diagnostics(call)

    assert result["count"] == 0
    assert result["entries"] == {}


@pytest.mark.asyncio
async def test_unknown_explicit_entry_raises():
    """Explicit config_entry_id that doesn't exist raises ServiceValidationError."""
    from homeassistant.exceptions import ServiceValidationError

    hass = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[])
    hass.config_entries.async_get_entry.return_value = None
    call = make_call(hass, data={"config_entry_id": ["nonexistent-id"]})

    with pytest.raises(ServiceValidationError):
        await async_handle_get_diagnostics(call)


def _make_unresolvable_entry(entry_id, state, runtime_data=None):
    """Build a MagicMock ``ConfigEntry`` for an id that must NOT resolve to a
    cover coordinator — used by the three degrade-not-raise cases below.
    """
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.domain = DOMAIN
    entry.title = entry_id
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
    hass.config_entries.async_entries = MagicMock(return_value=all_entries)
    hass.config_entries.async_get_entry.side_effect = lambda eid: by_id.get(eid)
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
        "group-1", ConfigEntryState.LOADED, runtime_data=group_coord
    )
    hass = _make_multi_entry_hass(good, group_entry)
    call = make_call(hass, data={"config_entry_id": ["good-1", "group-1"]})

    result = await async_handle_get_diagnostics(call)

    assert result["count"] == 2
    assert "error" not in result["entries"]["good-1"]["diagnostics"]
    group_result = result["entries"]["group-1"]
    assert group_result["config_entry_id"] == "group-1"
    assert group_result["diagnostics"]["error"]
    assert "cover group" in group_result["diagnostics"]["error"].lower()


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
        "profile-1", ConfigEntryState.LOADED, runtime_data=None
    )
    hass = _make_multi_entry_hass(good, profile_entry)
    call = make_call(hass, data={"config_entry_id": ["good-1", "profile-1"]})

    result = await async_handle_get_diagnostics(call)

    assert result["count"] == 2
    assert "error" not in result["entries"]["good-1"]["diagnostics"]
    profile_result = result["entries"]["profile-1"]
    assert profile_result["config_entry_id"] == "profile-1"
    assert profile_result["diagnostics"]["error"]
    assert "building profile" in profile_result["diagnostics"]["error"].lower()


@pytest.mark.asyncio
async def test_not_loaded_config_entry_id_degrades_instead_of_raising():
    """Explicitly targeting a config entry that is mid-reload / ``SETUP_RETRY``
    / disabled (state is not ``LOADED``) degrades to a per-entry error entry
    instead of raising — and the other explicitly-targeted, resolvable entry
    in the same call still comes back (issue #1059 audit round 3, defect #1:
    the widened ``else: raise`` regressed exactly this case, which shipped
    working on ``develop`` — a single mid-reload instance used to zero the
    whole multi-entry response; ``get_diagnostics`` must be no worse than
    ``develop`` here).
    """
    good = make_coordinator(entry_id="good-1", name="Good Cover")
    retry_entry = _make_unresolvable_entry(
        "retry-1", ConfigEntryState.SETUP_RETRY, runtime_data=None
    )
    hass = _make_multi_entry_hass(good, retry_entry)
    call = make_call(hass, data={"config_entry_id": ["good-1", "retry-1"]})

    result = await async_handle_get_diagnostics(call)

    assert result["count"] == 2
    assert "error" not in result["entries"]["good-1"]["diagnostics"]
    retry_result = result["entries"]["retry-1"]
    assert retry_result["config_entry_id"] == "retry-1"
    assert "not currently loaded" in retry_result["diagnostics"]["error"].lower()
    assert "setup_retry" in retry_result["diagnostics"]["error"].lower()


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
        await async_handle_get_diagnostics(call)


@pytest.mark.asyncio
async def test_sanitizer_handles_numpy_datetime_enum_dataclass():
    """Diagnostics containing numpy scalars, datetimes, enums, and dataclasses are JSON-serializable."""
    try:
        import numpy as np

        numpy_val = np.float64(42.5)
    except ImportError:
        numpy_val = 42.5  # numpy not available in test env, use plain float

    class Colour(enum.Enum):
        RED = "red"

    @dataclass
    class Point:
        x: float
        y: float

    coord = make_coordinator()
    coord.data.diagnostics = {
        "numpy_val": numpy_val,
        "timestamp": dt.datetime(2026, 4, 28, tzinfo=dt.UTC),
        "colour": Colour.RED,
        "point": Point(1.0, 2.0),
        "tags": {"b", "a"},
    }
    hass = make_hass(coord)
    call = make_call(hass)

    result = await async_handle_get_diagnostics(call)

    # Must be fully JSON-serializable
    json.dumps(result)

    diag = result["entries"]["entry-1"]["diagnostics"]
    assert diag["numpy_val"] == 42.5
    assert diag["timestamp"] == "2026-04-28T00:00:00+00:00"
    assert diag["colour"] == "red"
    assert diag["point"] == {"x": 1.0, "y": 2.0}
    assert diag["tags"] == ["a", "b"]


@pytest.mark.asyncio
async def test_coord_data_none_returns_error_payload_without_raising():
    """When coord.data is None and build_diagnostic_data raises, returns error payload."""
    coord = make_coordinator()
    coord.data = None
    coord.build_diagnostic_data.side_effect = RuntimeError("update in progress")
    hass = make_hass(coord)
    call = make_call(hass)

    result = await async_handle_get_diagnostics(call)

    entry = result["entries"]["entry-1"]
    assert "error" in entry["diagnostics"]
    assert "diagnostics_unavailable" in entry["diagnostics"]["error"]


@pytest.mark.asyncio
async def test_multiple_coordinators_returns_one_entry_each():
    """Multiple coordinators each appear as a separate entry."""
    coord1 = make_coordinator(entry_id="e1", name="North")
    coord2 = make_coordinator(entry_id="e2", name="South")
    hass = make_hass(coord1, coord2)
    call = make_call(hass)

    result = await async_handle_get_diagnostics(call)

    assert result["count"] == 2
    assert "e1" in result["entries"]
    assert "e2" in result["entries"]


@pytest.mark.asyncio
async def test_explicit_config_entry_id_targets_single_coordinator():
    """config_entry_id field bypasses entity/device target resolution."""
    coord1 = make_coordinator(entry_id="e1", name="North")
    coord2 = make_coordinator(entry_id="e2", name="South")
    hass = make_hass(coord1, coord2)
    # make async_get_entry return a valid entry for e1 only in this test
    hass.config_entries.async_get_entry.side_effect = lambda eid: (
        MagicMock(domain=DOMAIN, entry_id=eid) if eid == "e1" else None
    )
    call = make_call(hass, data={"config_entry_id": ["e1"]})

    result = await async_handle_get_diagnostics(call)

    assert result["count"] == 1
    assert "e1" in result["entries"]
    assert "e2" not in result["entries"]


def test_translations_contain_get_diagnostics_key():
    """en.json, de.json, and fr.json all contain the services.get_diagnostics key."""
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
        assert "get_diagnostics" in data.get(
            "services", {}
        ), f"{lang}.json missing services.get_diagnostics"
