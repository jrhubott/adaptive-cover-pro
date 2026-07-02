"""Tests for the engage/extend manual-override service path (issue #793).

A new ``adaptive_cover_pro.engage_manual_override`` service engages (or extends)
manual override on targeted covers WITHOUT sending any cover command. These
tests cover the manager engage method, the SSOT expiry↔start inverse helpers,
and the coordinator wrapper.
"""

from __future__ import annotations

import datetime as dt
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.managers.manual_override import (
    AdaptiveCoverManager,
)
from custom_components.adaptive_cover_pro.managers.manual_override.expiry import (
    expiry_for_started_at,
    started_at_for_expiry,
)

pytestmark = pytest.mark.unit


def _make_manager(
    covers: list[str], *, reset_duration: dict[str, int] | None = None
) -> AdaptiveCoverManager:
    manager = AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration=reset_duration or {"hours": 2},
        logger=MagicMock(),
    )
    manager.add_covers(covers)
    return manager


# ---------------------------------------------------------------------------
# SSOT expiry helpers (inverse-helper guard)
# ---------------------------------------------------------------------------


def test_expiry_helpers_are_inverses() -> None:
    start = dt.datetime(2026, 7, 2, 12, 0, tzinfo=dt.UTC)
    dur = dt.timedelta(hours=2, minutes=15)
    expiry = expiry_for_started_at(start, dur)
    assert expiry == start + dur
    assert started_at_for_expiry(expiry, dur) == start
    # Round trip both directions
    assert expiry_for_started_at(started_at_for_expiry(expiry, dur), dur) == expiry


# ---------------------------------------------------------------------------
# Manager: engage_override with an absolute end_time (no cover command)
# ---------------------------------------------------------------------------


def test_engage_override_absolute_end_time_sets_expiry_no_command() -> None:
    cover = "cover.x"
    manager = _make_manager([cover], reset_duration={"hours": 2})
    on_engaged = MagicMock()
    manager.set_transition_callbacks(on_engaged=on_engaged)

    now = dt.datetime.now(dt.UTC)
    end = now + dt.timedelta(hours=1)
    manager.engage_override(cover, end_time=end, duration=None, reason="service")

    assert manager.is_cover_manual(cover) is True
    # stored start = end - reset_duration (the SSOT inverse the sensor uses)
    expected_start = end - manager.reset_duration
    assert (
        abs((manager.manual_control_time[cover] - expected_start).total_seconds()) < 1
    )
    on_engaged.assert_called_once_with(cover)


def test_engage_override_none_falls_back_to_now() -> None:
    cover = "cover.x"
    manager = _make_manager([cover], reset_duration={"hours": 2})

    before = dt.datetime.now(dt.UTC)
    manager.engage_override(cover, end_time=None, duration=None, reason="service")
    after = dt.datetime.now(dt.UTC)

    assert manager.is_cover_manual(cover) is True
    ts = manager.manual_control_time[cover]
    assert before <= ts <= after


def test_engage_override_past_end_time_falls_back_to_now() -> None:
    cover = "cover.x"
    manager = _make_manager([cover], reset_duration={"hours": 2})

    before = dt.datetime.now(dt.UTC)
    past = before - dt.timedelta(hours=1)
    manager.engage_override(cover, end_time=past, duration=None, reason="service")
    after = dt.datetime.now(dt.UTC)

    ts = manager.manual_control_time[cover]
    assert before <= ts <= after


def test_engage_override_naive_datetime_normalized_utc() -> None:
    cover = "cover.x"
    manager = _make_manager([cover], reset_duration={"hours": 2})

    # naive future datetime — must not raise, treated as UTC
    end_naive = (dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).replace(tzinfo=None)
    manager.engage_override(cover, end_time=end_naive, duration=None, reason="service")

    assert manager.is_cover_manual(cover) is True
    expected_start = end_naive.replace(tzinfo=dt.UTC) - manager.reset_duration
    assert (
        abs((manager.manual_control_time[cover] - expected_start).total_seconds()) < 1
    )


# ---------------------------------------------------------------------------
# Manager: duration semantics (engage-for + extend-by + precedence)
# ---------------------------------------------------------------------------


def test_engage_override_duration_engages_fresh_for_now_plus_duration() -> None:
    cover = "cover.x"
    manager = _make_manager([cover], reset_duration={"hours": 2})

    now = dt.datetime.now(dt.UTC)
    manager.engage_override(
        cover, end_time=None, duration=dt.timedelta(hours=1), reason="service"
    )

    assert manager.is_cover_manual(cover) is True
    # end = now + 1h → stored start = now + 1h - 2h = now - 1h
    end = expiry_for_started_at(
        manager.manual_control_time[cover], manager.reset_duration
    )
    assert abs((end - (now + dt.timedelta(hours=1))).total_seconds()) < 1


def test_engage_override_duration_extends_active_override() -> None:
    cover = "cover.x"
    manager = _make_manager([cover], reset_duration={"hours": 2})
    on_engaged = MagicMock()
    manager.set_transition_callbacks(on_engaged=on_engaged)

    # Engage fresh for 1h
    manager.engage_override(
        cover, end_time=None, duration=dt.timedelta(hours=1), reason="service"
    )
    first_end = expiry_for_started_at(
        manager.manual_control_time[cover], manager.reset_duration
    )
    assert on_engaged.call_count == 1

    # Extend by another 1h — end must move to first_end + 1h
    manager.engage_override(
        cover, end_time=None, duration=dt.timedelta(hours=1), reason="service"
    )
    second_end = expiry_for_started_at(
        manager.manual_control_time[cover], manager.reset_duration
    )

    assert abs((second_end - (first_end + dt.timedelta(hours=1))).total_seconds()) < 1
    # Extending an already-manual cover must NOT re-fire the engaged edge
    assert on_engaged.call_count == 1


def test_engage_override_end_time_takes_precedence_over_duration() -> None:
    cover = "cover.x"
    manager = _make_manager([cover], reset_duration={"hours": 2})

    now = dt.datetime.now(dt.UTC)
    end = now + dt.timedelta(hours=3)
    manager.engage_override(
        cover, end_time=end, duration=dt.timedelta(hours=1), reason="service"
    )

    resolved_end = expiry_for_started_at(
        manager.manual_control_time[cover], manager.reset_duration
    )
    assert abs((resolved_end - end).total_seconds()) < 1


def test_engage_override_shares_engine_with_external_path() -> None:
    """The external input-sensor path must still engage all covers + fire edges."""
    covers = ["cover.a", "cover.b"]
    manager = _make_manager(covers)
    on_engaged = MagicMock()
    manager.set_transition_callbacks(on_engaged=on_engaged)

    before = dt.datetime.now(dt.UTC)
    manager.engage_manual_override_from_external(reason="input_sensor")

    for cover in covers:
        assert manager.is_cover_manual(cover) is True
        assert manager.manual_control_time[cover] >= before
    assert on_engaged.call_count == len(covers)

    # Second press re-arms the timer (overwrite) and does not re-fire the edge
    time.sleep(0.01)
    first = manager.manual_control_time["cover.a"]
    manager.engage_manual_override_from_external(reason="input_sensor")
    assert manager.manual_control_time["cover.a"] > first
    assert on_engaged.call_count == len(covers)


# ---------------------------------------------------------------------------
# Coordinator wrapper: engage then refresh once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_engage_manual_override_engages_each_then_refreshes() -> None:
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = MagicMock()
    coord.entities = ["cover.a", "cover.b"]
    coord.manager = MagicMock()
    coord.async_refresh = AsyncMock()
    coord.async_engage_manual_override = (
        AdaptiveDataUpdateCoordinator.async_engage_manual_override.__get__(coord)
    )

    end = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    await coord.async_engage_manual_override(
        ["cover.a"], end_time=end, duration=None, trigger="engage_manual_override"
    )

    coord.manager.engage_override.assert_called_once_with(
        "cover.a", end_time=end, duration=None, reason="engage_manual_override"
    )
    coord.async_refresh.assert_awaited_once()
