"""Tests for the engage_manual_override service handler (issue #793).

The handler does lenient TYPE coercion + tz-normalization only; semantic
validation against ``now`` lives in the manager. A bad ``end_time`` /
``duration`` coerces to ``None`` (never raises) and the manager falls back.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.services.engage_manual_override_service import (
    ENGAGE_MANUAL_OVERRIDE_SCHEMA,
    _coerce_duration,
    _coerce_end_time,
    async_handle_engage_manual_override,
)

pytestmark = pytest.mark.unit

_PATCH_TARGET = (
    "custom_components.adaptive_cover_pro.services."
    "engage_manual_override_service._resolve_targets"
)


def _make_coord() -> MagicMock:
    coord = MagicMock()
    coord.entities = ["cover.living_room"]
    coord.async_engage_manual_override = AsyncMock()
    return coord


async def _run(data: dict) -> MagicMock:
    coord = _make_coord()
    call = MagicMock()
    call.hass = MagicMock()
    call.data = data
    with patch(_PATCH_TARGET, return_value={coord: None}):
        await async_handle_engage_manual_override(call)
    return coord


@pytest.mark.asyncio
async def test_iso_end_time_parsed_and_passed_through() -> None:
    coord = await _run({"end_time": "2026-07-02T15:00:00+00:00"})
    coord.async_engage_manual_override.assert_awaited_once()
    args, kwargs = coord.async_engage_manual_override.call_args
    assert args[0] == ["cover.living_room"]
    assert kwargs["end_time"] == dt.datetime(2026, 7, 2, 15, 0, tzinfo=dt.UTC)
    assert kwargs["duration"] is None


@pytest.mark.asyncio
async def test_naive_iso_end_time_normalized_to_utc() -> None:
    coord = await _run({"end_time": "2026-07-02T15:00:00"})
    _, kwargs = coord.async_engage_manual_override.call_args
    assert kwargs["end_time"] == dt.datetime(2026, 7, 2, 15, 0, tzinfo=dt.UTC)


@pytest.mark.asyncio
async def test_datetime_end_time_passed_through() -> None:
    end = dt.datetime(2026, 7, 2, 18, 0, tzinfo=dt.UTC)
    coord = await _run({"end_time": end})
    _, kwargs = coord.async_engage_manual_override.call_args
    assert kwargs["end_time"] == end


@pytest.mark.asyncio
async def test_garbage_end_time_coerces_to_none() -> None:
    coord = await _run({"end_time": "not-a-date"})
    _, kwargs = coord.async_engage_manual_override.call_args
    assert kwargs["end_time"] is None
    assert kwargs["duration"] is None


@pytest.mark.asyncio
async def test_no_data_passes_none_none() -> None:
    coord = await _run({})
    coord.async_engage_manual_override.assert_awaited_once()
    _, kwargs = coord.async_engage_manual_override.call_args
    assert kwargs["end_time"] is None
    assert kwargs["duration"] is None


@pytest.mark.asyncio
async def test_duration_dict_coerced_to_timedelta() -> None:
    coord = await _run({"duration": {"hours": 1, "minutes": 30}})
    _, kwargs = coord.async_engage_manual_override.call_args
    assert kwargs["duration"] == dt.timedelta(hours=1, minutes=30)
    assert kwargs["end_time"] is None


@pytest.mark.asyncio
async def test_zero_duration_coerces_to_none() -> None:
    coord = await _run({"duration": {"hours": 0, "minutes": 0, "seconds": 0}})
    _, kwargs = coord.async_engage_manual_override.call_args
    assert kwargs["duration"] is None


@pytest.mark.asyncio
async def test_timedelta_duration_passed_through() -> None:
    coord = await _run({"duration": dt.timedelta(minutes=45)})
    _, kwargs = coord.async_engage_manual_override.call_args
    assert kwargs["duration"] == dt.timedelta(minutes=45)


@pytest.mark.asyncio
async def test_entity_filter_expands_to_all_coordinator_entities() -> None:
    """A None filter (whole-coordinator) engages every cover the coordinator owns."""
    coord = _make_coord()
    coord.entities = ["cover.a", "cover.b"]
    call = MagicMock()
    call.hass = MagicMock()
    call.data = {}
    with patch(_PATCH_TARGET, return_value={coord: None}):
        await async_handle_engage_manual_override(call)
    args, _ = coord.async_engage_manual_override.call_args
    assert args[0] == ["cover.a", "cover.b"]


@pytest.mark.asyncio
async def test_explicit_entity_filter_passed_through() -> None:
    coord = _make_coord()
    coord.entities = ["cover.a", "cover.b"]
    call = MagicMock()
    call.hass = MagicMock()
    call.data = {}
    with patch(_PATCH_TARGET, return_value={coord: {"cover.a"}}):
        await async_handle_engage_manual_override(call)
    args, _ = coord.async_engage_manual_override.call_args
    assert args[0] == ["cover.a"]


# ---------------------------------------------------------------------------
# Direct coercion-branch coverage
# ---------------------------------------------------------------------------


def test_coerce_end_time_rejects_non_datetime_non_string() -> None:
    assert _coerce_end_time(12345) is None
    assert _coerce_end_time(None) is None


def test_coerce_duration_rejects_wrong_type_and_bad_dict() -> None:
    assert _coerce_duration("PT1H") is None
    assert _coerce_duration(None) is None
    # Non-numeric dict value → TypeError/ValueError → None (never raises)
    assert _coerce_duration({"hours": "abc"}) is None


def test_coerce_duration_negative_is_none() -> None:
    assert _coerce_duration(dt.timedelta(seconds=-5)) is None


def test_schema_passes_end_time_and_duration_through_untouched() -> None:
    validated = ENGAGE_MANUAL_OVERRIDE_SCHEMA(
        {
            "entity_id": "cover.a",
            "end_time": "not-a-date",
            "duration": {"hours": 1},
        }
    )
    assert validated["end_time"] == "not-a-date"
    assert validated["duration"] == {"hours": 1}
