"""Unit tests for TimeWindowManager covering previously uncovered branches."""

from __future__ import annotations

import datetime as dt
import zoneinfo
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from freezegun import freeze_time

from custom_components.adaptive_cover_pro.const import BLANK_TIME
from custom_components.adaptive_cover_pro.managers.time_window import TimeWindowManager


def _make_manager(mock_hass=None, sunrise_provider=None):
    """Build a TimeWindowManager with a MagicMock hass and logger."""
    hass = mock_hass or MagicMock()
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.error = MagicMock()
    return TimeWindowManager(
        hass=hass, logger=logger, sunrise_provider=sunrise_provider
    )


_TIME_WINDOW = "custom_components.adaptive_cover_pro.managers.time_window"

# The fixed HA-local "now" every date-sensitive fixture in this module is built
# from. Production anchors on ``local_now_naive()`` (HA's configured zone), so a
# fixture built from ``dt.datetime.now()`` / ``dt.date.today()`` sits in the HOST
# frame instead and the two dates diverge by the zone offset for part of every
# day. While they diverge, a "tomorrow" fixture is really HA-today: normalization
# never runs and the test quietly stops guarding rather than failing (#1161).
_HA_NOW = dt.datetime(2090, 6, 15, 12, 0, 0)


@contextmanager
def _patch_time_window(parsed, *, now=_HA_NOW, safe_state="irrelevant-raw-state"):
    """Patch time_window's entity reads and its clock to a fixed HA-local ``now``.

    Args:
        parsed: What ``get_datetime_from_str`` yields — a single datetime, or an
            iterable of datetimes handed out in call order.
        now: The value ``local_now_naive`` returns (defaults to ``_HA_NOW``).
        safe_state: The raw entity state ``get_safe_state`` returns.

    """
    if isinstance(parsed, dt.datetime):
        parsed_patch = patch(
            f"{_TIME_WINDOW}.get_datetime_from_str", return_value=parsed
        )
    else:
        values = iter(parsed)
        parsed_patch = patch(
            f"{_TIME_WINDOW}.get_datetime_from_str", side_effect=lambda _: next(values)
        )
    with (
        patch(f"{_TIME_WINDOW}.get_safe_state", return_value=safe_state),
        parsed_patch,
        patch(f"{_TIME_WINDOW}.local_now_naive", return_value=now),
    ):
        yield


# ---------------------------------------------------------------------------
# after_start_time: entity-based branch
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_after_start_time_entity_returns_true_when_state_is_none():
    """Entity returns None state → treats as start passed (returns True)."""
    mgr = _make_manager()
    mgr.update_config(
        start_time=None,
        start_time_entity="input_datetime.start",
        end_time=None,
        end_time_entity=None,
    )

    with patch(
        "custom_components.adaptive_cover_pro.managers.time_window.get_safe_state",
        return_value=None,
    ):
        result = mgr.after_start_time

    assert result is True


@pytest.mark.unit
def test_after_start_time_entity_evaluates_correctly():
    """Entity provides a valid time → evaluates now >= time."""
    mgr = _make_manager()
    mgr.update_config(
        start_time=None,
        start_time_entity="input_datetime.start",
        end_time=None,
        end_time_entity=None,
    )

    # "now" is pinned to the fixed HA-local sentinel and the fixture is built
    # from it, so both sides of the comparison sit in the same frame on any host
    # zone (#1161) — a host-clock fixture drifts out of frame by the zone offset.
    # An hour before that sentinel, so now >= time is True.
    past_time = _HA_NOW - dt.timedelta(hours=1)

    with _patch_time_window(past_time, safe_state="2024-01-01T07:00:00"):
        result = mgr.after_start_time

    assert result is True
    assert mgr._cached_start_time == past_time


@pytest.mark.unit
def test_after_start_time_entity_returns_false_when_future():
    """Entity provides a future time → evaluates now >= time as False."""
    mgr = _make_manager()
    mgr.update_config(
        start_time=None,
        start_time_entity="input_datetime.start",
        end_time=None,
        end_time_entity=None,
    )

    now = dt.datetime(2024, 6, 15, 10, 0, 0)
    future_time = dt.datetime(2024, 6, 15, 12, 0, 0)

    with (
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_safe_state",
            return_value="2024-06-15T12:00:00",
        ),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_datetime_from_str",
            return_value=future_time,
        ),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.local_now_naive",
            return_value=now,
        ),
    ):
        result = mgr.after_start_time

    assert result is False


# ---------------------------------------------------------------------------
# after_start_time: static config parse failure
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_after_start_time_static_parse_failure_treats_as_passed():
    """Unparseable static start time → returns True (treat start as passed)."""
    mgr = _make_manager()
    mgr.update_config(
        start_time="not-a-time",
        start_time_entity=None,
        end_time=None,
        end_time_entity=None,
    )

    with patch(
        "custom_components.adaptive_cover_pro.managers.time_window.get_datetime_from_str",
        return_value=None,
    ):
        result = mgr.after_start_time

    assert result is True


# ---------------------------------------------------------------------------
# after_start_time: blank start anchors to sunrise once an end bound is
# configured (issue #1256) — otherwise the window re-opens at local midnight
# ---------------------------------------------------------------------------


def _sunrise_provider(hour: int, minute: int, day: dt.date = dt.date(2026, 8, 12)):
    """Build a deterministic ``sunrise_provider`` returning a fixed naive-local time."""
    sunrise = dt.datetime.combine(day, dt.time(hour, minute))
    return lambda: sunrise


# The reporter's end entity (sensor.sun_next_setting) reads tomorrow's sunset
# all evening — the parsed value never changes; only _normalize_to_today's
# comparison against "today" does, as the clock crosses midnight.
_END_ENTITY_RAW = "2026-08-12T20:07:39"
_END_ENTITY_PARSED = dt.datetime(2026, 8, 12, 20, 7, 39)


@pytest.mark.unit
def test_blank_start_with_configured_end_stays_closed_before_sunrise():
    """A blank start + a configured end must NOT open the window at midnight.

    Issue #1256: mapping "no start configured" straight to True puts the
    window's lower bound at midnight. With a sunrise provider wired and an
    end bound configured, the lower bound must be sunrise instead.
    """
    mgr = _make_manager(sunrise_provider=_sunrise_provider(6, 0))
    mgr.update_config(
        start_time=None,
        start_time_entity=None,
        end_time=None,
        end_time_entity="sensor.sun_next_setting",
    )

    with (
        patch(f"{_TIME_WINDOW}.get_safe_state", return_value=_END_ENTITY_RAW),
        patch(f"{_TIME_WINDOW}.get_datetime_from_str", return_value=_END_ENTITY_PARSED),
        patch(
            f"{_TIME_WINDOW}.local_now_naive",
            return_value=dt.datetime(2026, 8, 12, 0, 5, 0),
        ),
    ):
        assert mgr.after_start_time is False
        assert mgr.is_active is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_blank_start_with_configured_end_no_midnight_reopen_transition():
    """No ``on_window_open`` / ``time_window_changed`` event fires at local midnight.

    Reproduces the reporter's timeline end-to-end: window closes at sunset
    (end time passed), stays closed through midnight, and check_transition
    never sees inactive→active until sunrise.
    """
    from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
        EventBuffer,
    )

    event_buffer = EventBuffer(maxlen=50)
    mgr = TimeWindowManager(
        hass=MagicMock(),
        logger=MagicMock(),
        event_buffer=event_buffer,
        sunrise_provider=_sunrise_provider(6, 0),
    )
    mgr.update_config(
        start_time=None,
        start_time_entity=None,
        end_time=None,
        end_time_entity="sensor.sun_next_setting",
    )
    on_window_open = AsyncMock()
    refresh_callback = AsyncMock()

    # Prime at 23:55 Aug 11 local — window CLOSED (end time already passed
    # today, per _normalize_to_today pinning the entity's Aug-12 reading
    # back to Aug-11).
    with (
        patch(f"{_TIME_WINDOW}.get_safe_state", return_value=_END_ENTITY_RAW),
        patch(f"{_TIME_WINDOW}.get_datetime_from_str", return_value=_END_ENTITY_PARSED),
        patch(
            f"{_TIME_WINDOW}.local_now_naive",
            return_value=dt.datetime(2026, 8, 11, 23, 55, 0),
        ),
    ):
        await mgr.check_transition(
            track_end_time=True,
            refresh_callback=refresh_callback,
            on_window_open=on_window_open,
        )
    assert mgr._last_time_window_state is False

    # Advance to 00:05 Aug 12 local — the date rollover that (pre-fix)
    # re-opens the window at midnight.
    with (
        patch(f"{_TIME_WINDOW}.get_safe_state", return_value=_END_ENTITY_RAW),
        patch(f"{_TIME_WINDOW}.get_datetime_from_str", return_value=_END_ENTITY_PARSED),
        patch(
            f"{_TIME_WINDOW}.local_now_naive",
            return_value=dt.datetime(2026, 8, 12, 0, 5, 0),
        ),
    ):
        await mgr.check_transition(
            track_end_time=True,
            refresh_callback=refresh_callback,
            on_window_open=on_window_open,
        )

    on_window_open.assert_not_awaited()
    assert all(
        event.get("event") != "time_window_changed" for event in event_buffer.snapshot()
    )


@pytest.mark.unit
def test_window_explicitly_started_stays_false_across_midnight_and_noon():
    """window_explicitly_started (#492/#493) must not move.

    Locked separately from after_start_time so the sunrise anchor cannot
    resurrect the bug #493 fixed: a blank start must never register as an
    explicit start, at midnight or at any other hour.
    """
    mgr = _make_manager(sunrise_provider=_sunrise_provider(6, 0))
    mgr.update_config(
        start_time=None,
        start_time_entity=None,
        end_time=None,
        end_time_entity="sensor.sun_next_setting",
    )

    for now in (
        dt.datetime(2026, 8, 12, 0, 5, 0),
        dt.datetime(2026, 8, 12, 12, 0, 0),
    ):
        with patch(f"{_TIME_WINDOW}.local_now_naive", return_value=now):
            assert mgr.window_explicitly_started is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_blank_start_opens_window_at_sunrise_not_before():
    """Sunrise, not midnight, is where the blank-start window actually opens.

    check_transition must fire on_window_open exactly once, at the 05:55 →
    06:05 sunrise crossing.
    """
    from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
        EventBuffer,
    )

    event_buffer = EventBuffer(maxlen=50)
    mgr = TimeWindowManager(
        hass=MagicMock(),
        logger=MagicMock(),
        event_buffer=event_buffer,
        sunrise_provider=_sunrise_provider(6, 0),
    )
    mgr.update_config(
        start_time=None,
        start_time_entity=None,
        end_time=None,
        end_time_entity="sensor.sun_next_setting",
    )
    on_window_open = AsyncMock()
    refresh_callback = AsyncMock()

    with (
        patch(f"{_TIME_WINDOW}.get_safe_state", return_value=_END_ENTITY_RAW),
        patch(f"{_TIME_WINDOW}.get_datetime_from_str", return_value=_END_ENTITY_PARSED),
        patch(
            f"{_TIME_WINDOW}.local_now_naive",
            return_value=dt.datetime(2026, 8, 12, 5, 55, 0),
        ),
    ):
        assert mgr.after_start_time is False
        await mgr.check_transition(
            track_end_time=True,
            refresh_callback=refresh_callback,
            on_window_open=on_window_open,
        )

    with (
        patch(f"{_TIME_WINDOW}.get_safe_state", return_value=_END_ENTITY_RAW),
        patch(f"{_TIME_WINDOW}.get_datetime_from_str", return_value=_END_ENTITY_PARSED),
        patch(
            f"{_TIME_WINDOW}.local_now_naive",
            return_value=dt.datetime(2026, 8, 12, 6, 5, 0),
        ),
    ):
        assert mgr.after_start_time is True
        assert mgr.is_active is True
        await mgr.check_transition(
            track_end_time=True,
            refresh_callback=refresh_callback,
            on_window_open=on_window_open,
        )

    on_window_open.assert_awaited_once()


# ---------------------------------------------------------------------------
# end_time: entity-based and midnight branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_end_time_from_entity():
    """end_time resolves from entity state."""
    mgr = _make_manager()
    mgr.update_config(
        start_time=None,
        start_time_entity=None,
        end_time=None,
        end_time_entity="input_datetime.end",
    )

    expected = dt.datetime(2024, 6, 21, 20, 0, 0)

    with (
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_safe_state",
            return_value="2024-06-21T20:00:00",
        ),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_datetime_from_str",
            return_value=expected,
        ),
    ):
        result = mgr.end_time

    assert result == expected


@pytest.mark.unit
def test_end_time_midnight_adds_one_day():
    """Static end time of 00:00 is adjusted to +1 day to avoid immediate expiry."""
    mgr = _make_manager()
    mgr.update_config(
        start_time=None,
        start_time_entity=None,
        end_time="00:00:00",
        end_time_entity=None,
    )

    midnight = dt.datetime(2024, 6, 21, 0, 0, 0)

    with patch(
        "custom_components.adaptive_cover_pro.managers.time_window.get_datetime_from_str",
        return_value=midnight,
    ):
        result = mgr.end_time

    assert result == midnight + dt.timedelta(days=1)


# ---------------------------------------------------------------------------
# is_active: start > end logs error
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_is_active_logs_error_when_start_after_end():
    """is_active logs error when cached start time is after end time."""
    from unittest.mock import PropertyMock
    from custom_components.adaptive_cover_pro.managers.time_window import (
        TimeWindowManager,
    )

    mgr = _make_manager()
    mgr.update_config(
        start_time=None,
        start_time_entity=None,
        end_time="08:00:00",
        end_time_entity=None,
    )

    # Set cached_start to a late time so cached_start > end_time triggers the error
    past_end = dt.datetime.now() - dt.timedelta(hours=1)
    future_start = dt.datetime.now() + dt.timedelta(hours=2)
    mgr._cached_start_time = future_start

    with (
        patch.object(
            TimeWindowManager,
            "end_time",
            new_callable=PropertyMock,
            return_value=past_end,
        ),
        patch.object(
            TimeWindowManager,
            "before_end_time",
            new_callable=PropertyMock,
            return_value=True,
        ),
        patch.object(
            TimeWindowManager,
            "after_start_time",
            new_callable=PropertyMock,
            return_value=True,
        ),
    ):
        mgr.is_active

    mgr.logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# clock_window_open: the gate-free clock predicate (issue #656)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_clock_window_open_true_when_gate_dark_but_clock_open():
    """clock_window_open is True when the clock is open even if the gate reads dark.

    Issue #656: is_active folds the daytime gate into the clock window, so a
    gate-dark night reads is_active=False even when the user's start/end clock
    is still open. clock_window_open must stay True in that case so suppression
    sites that only care about the clock still dispatch the night position.
    """
    from unittest.mock import PropertyMock
    from custom_components.adaptive_cover_pro.managers.time_window import (
        TimeWindowManager,
    )

    mgr = _make_manager()

    with (
        patch.object(
            TimeWindowManager,
            "before_end_time",
            new_callable=PropertyMock,
            return_value=True,
        ),
        patch.object(
            TimeWindowManager,
            "after_start_time",
            new_callable=PropertyMock,
            return_value=True,
        ),
        patch.object(
            TimeWindowManager,
            "gate_is_daytime",
            new_callable=PropertyMock,
            return_value=False,  # gate reads dark
        ),
    ):
        assert mgr.clock_window_open is True
        # is_active simultaneously False because the gate is dark
        assert mgr.is_active is False


@pytest.mark.unit
def test_clock_window_open_false_when_clock_closed():
    """clock_window_open is False when the user's start/end clock is genuinely closed."""
    from unittest.mock import PropertyMock
    from custom_components.adaptive_cover_pro.managers.time_window import (
        TimeWindowManager,
    )

    mgr = _make_manager()

    with (
        patch.object(
            TimeWindowManager,
            "before_end_time",
            new_callable=PropertyMock,
            return_value=False,  # clock closed (after end time)
        ),
        patch.object(
            TimeWindowManager,
            "after_start_time",
            new_callable=PropertyMock,
            return_value=True,
        ),
    ):
        assert mgr.clock_window_open is False


@pytest.mark.unit
@pytest.mark.parametrize("before_end", [True, False])
@pytest.mark.parametrize("after_start", [True, False])
@pytest.mark.parametrize("gate_daytime", [True, False])
def test_is_active_is_clock_window_open_and_gate(before_end, after_start, gate_daytime):
    """Lock the slice: is_active == (clock_window_open and gate_is_daytime).

    is_active must remain exactly the clock predicate ANDed with the daytime
    gate — clock_window_open is is_active with the gate factor removed.
    """
    from unittest.mock import PropertyMock
    from custom_components.adaptive_cover_pro.managers.time_window import (
        TimeWindowManager,
    )

    mgr = _make_manager()

    with (
        patch.object(
            TimeWindowManager,
            "before_end_time",
            new_callable=PropertyMock,
            return_value=before_end,
        ),
        patch.object(
            TimeWindowManager,
            "after_start_time",
            new_callable=PropertyMock,
            return_value=after_start,
        ),
        patch.object(
            TimeWindowManager,
            "gate_is_daytime",
            new_callable=PropertyMock,
            return_value=gate_daytime,
        ),
    ):
        assert mgr.is_active == (mgr.clock_window_open and gate_daytime)


# ---------------------------------------------------------------------------
# check_transition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_check_transition_initializes_on_first_call():
    """First check_transition call initializes state, does not invoke callback."""
    mgr = _make_manager()
    mgr.update_config(None, None, None, None)
    callback = AsyncMock()

    await mgr.check_transition(track_end_time=True, refresh_callback=callback)

    # First call: state initialized, no callback
    assert mgr._last_time_window_state is not None
    callback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_check_transition_no_callback_when_state_unchanged():
    """No callback when window state hasn't changed."""
    mgr = _make_manager()
    mgr.update_config(None, None, None, None)
    callback = AsyncMock()

    # Initialize state
    await mgr.check_transition(track_end_time=True, refresh_callback=callback)
    # Second call with same state
    await mgr.check_transition(track_end_time=True, refresh_callback=callback)

    callback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_check_transition_calls_callback_on_window_close():
    """Callback is invoked when window transitions active→inactive with track_end_time=True."""
    mgr = _make_manager()
    mgr.update_config(None, None, None, None)
    callback = AsyncMock()

    # Force-set a prior state of "active"
    mgr._last_time_window_state = True

    # Now make is_active return False (window just closed)
    with patch.object(
        type(mgr), "is_active", new_callable=lambda: property(lambda self: False)
    ):
        await mgr.check_transition(track_end_time=True, refresh_callback=callback)

    callback.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_check_transition_no_callback_when_track_end_time_false():
    """Callback NOT invoked when track_end_time=False even if window closed."""
    mgr = _make_manager()
    mgr.update_config(None, None, None, None)
    callback = AsyncMock()

    mgr._last_time_window_state = True

    with patch.object(
        type(mgr), "is_active", new_callable=lambda: property(lambda self: False)
    ):
        await mgr.check_transition(track_end_time=False, refresh_callback=callback)

    callback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_check_transition_no_callback_on_window_open():
    """No callback when window transitions inactive→active."""
    mgr = _make_manager()
    mgr.update_config(None, None, None, None)
    callback = AsyncMock()

    mgr._last_time_window_state = False

    with patch.object(
        type(mgr), "is_active", new_callable=lambda: property(lambda self: True)
    ):
        await mgr.check_transition(track_end_time=True, refresh_callback=callback)

    callback.assert_not_called()


# ---------------------------------------------------------------------------
# HA-configured tz vs. host clock (issue #1161)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_start_has_passed_uses_ha_local_now_not_host_clock():
    """_start_has_passed must read ``local_now_naive``, not the naive host clock.

    Issue #1161: a bare ``dt.datetime.now()`` silently reads the host OS
    timezone instead of HA's configured ``hass.config.time_zone``. Freezing
    ``local_now_naive`` (which wraps ``dt_util.now()``) to a sentinel far from
    the real host clock must move the result — proving the code reads HA's
    zone, not the host clock.
    """
    mgr = _make_manager()
    mgr.update_config(
        start_time="06:00:00",
        start_time_entity=None,
        end_time=None,
        end_time_entity=None,
    )

    # Resolved start is far in the future relative to the real host clock, so
    # a host-clock read would find "now" is still before it (not yet passed).
    resolved = dt.datetime(2090, 1, 1, 0, 0, 0)
    # The HA-local sentinel is AFTER the resolved start — only a fix that reads
    # HA's zone can make after_start_time True here.
    sentinel = dt.datetime(2095, 1, 1, 0, 0, 0)

    with (
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_datetime_from_str",
            return_value=resolved,
        ),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.local_now_naive",
            return_value=sentinel,
        ),
    ):
        result = mgr.after_start_time

    assert result is True


@pytest.mark.unit
def test_before_end_time_uses_ha_local_now_not_host_clock():
    """before_end_time must read ``local_now_naive``, not the host clock (#1161)."""
    mgr = _make_manager()
    mgr.update_config(
        start_time=None,
        start_time_entity=None,
        end_time="05:00:00",
        end_time_entity=None,
    )

    # end resolves far in the future relative to the real host clock.
    end = dt.datetime(2090, 1, 1, 5, 0, 0)
    # The HA-local sentinel is AFTER end — only a fix that reads HA's zone can
    # make before_end_time False here (a host-clock read would still be well
    # before `end` and stay True).
    sentinel = dt.datetime(2095, 1, 1, 0, 0, 0)

    with (
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_datetime_from_str",
            return_value=end,
        ),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.local_now_naive",
            return_value=sentinel,
        ),
    ):
        result = mgr.before_end_time

    assert result is False


@pytest.mark.unit
def test_normalize_to_today_uses_ha_local_date_not_host_clock():
    """_normalize_to_today must anchor "today" on HA's local date, not the host clock.

    Exercised via an entity-sourced start time (sun-entity date rollover,
    #226's mechanism) so the assertion is on the resolved/cached start time —
    which reveals which "today" was used to normalize — rather than on the
    True/False comparison, keeping this test independent of wall-clock time-
    of-day (#1161).
    """
    mgr = _make_manager()
    mgr.update_config(
        start_time=None,
        start_time_entity="sensor.sun_next_rising",
        end_time=None,
        end_time_entity=None,
    )

    # The HA-local sentinel's date is 2090-06-15; the entity reports "tomorrow"
    # relative to it.
    tomorrow_relative_to_sentinel = dt.datetime.combine(
        _HA_NOW.date() + dt.timedelta(days=1), dt.time(6, 30)
    )

    with _patch_time_window(tomorrow_relative_to_sentinel):
        mgr.after_start_time

    # Normalized onto the HA-local sentinel's date (2090-06-15), not whatever
    # the real host clock's date happens to be.
    assert mgr._cached_start_time == dt.datetime(2090, 6, 15, 6, 30, 0)


@pytest.mark.unit
def test_blank_end_time_stays_open_when_ha_zone_is_ahead_of_host():
    """A BLANK_TIME end must stay open all HA-local day, in HA's zone (#1161).

    Under ``freeze_time`` the host's naive clock reads the frozen UTC instant;
    HA's zone is Pacific/Auckland (UTC+12). At 2026-08-02 14:06 UTC it is
    already 2026-08-03 02:06 in HA's zone while the host is still on 08-02.

    ``end_time`` resolves ``BLANK_TIME`` through ``dateutil.parser.parse``,
    which fills the missing *date* from its own ``datetime.now()`` — the HOST
    clock. Anchored on the host's 08-02 the sentinel rolls to 08-03 00:00,
    which HA-local "now" (02:06) is already past, so the window reports CLOSED
    for the first 12 hours of every HA-local day and ``check_transition``
    fires "End time reached" and reverts to the default position. Both sides
    of the comparison must be framed on HA's local date.
    """
    mgr = _make_manager()
    mgr.update_config(
        start_time=None,
        start_time_entity=None,
        end_time=BLANK_TIME,
        end_time_entity=None,
    )

    auckland = zoneinfo.ZoneInfo("Pacific/Auckland")
    with (
        freeze_time("2026-08-02 14:06:00"),
        patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", auckland),
    ):
        # Midnight anchored on HA's local date (08-03), rolled to tomorrow.
        assert mgr.end_time == dt.datetime(2026, 8, 4, 0, 0)
        assert mgr.before_end_time is True


@pytest.mark.unit
def test_static_window_uses_ha_local_date_when_host_date_is_ahead():
    """A static start/end window must key on HA's local date, not the host's (#1161).

    The canonical #1161 setup: a UTC host with HA configured for
    America/Los_Angeles (UTC-7 in August). Under ``freeze_time`` the host's
    naive clock reads the frozen UTC instant, so at 2026-08-03 02:06 UTC the
    host has already rolled over to 08-03 while HA-local is still 2026-08-02
    19:06 — inside an 08:00-20:00 window with an hour to spare.

    ``dateutil.parser.parse`` fills the missing date of ``"08:00:00"`` from
    its own ``datetime.now()``, i.e. the HOST clock, producing 2026-08-03
    08:00 — an instant HA-local "now" has not reached. The window then reports
    CLOSED for the last ``|tz-offset|`` hours of every HA-local day, which is
    the reported "closes ~3h early every evening".
    """
    mgr = _make_manager()
    mgr.update_config(
        start_time="08:00:00",
        start_time_entity=None,
        end_time="20:00:00",
        end_time_entity=None,
    )

    los_angeles = zoneinfo.ZoneInfo("America/Los_Angeles")
    with (
        freeze_time("2026-08-03 02:06:00"),
        patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", los_angeles),
    ):
        assert mgr.resolved_start_time == dt.datetime(2026, 8, 2, 8, 0)
        assert mgr.end_time == dt.datetime(2026, 8, 2, 20, 0)
        assert mgr.after_start_time is True
        assert mgr.before_end_time is True
        assert mgr.clock_window_open is True


# ---------------------------------------------------------------------------
# Sun entity rollover normalization (#226)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_after_start_time_entity_normalizes_tomorrow_to_today():
    """Sun entity rolled to tomorrow's date is normalized back to today.

    After today's sunrise passes, sensor.sun_next_rising flips to tomorrow's
    datetime. after_start_time must still return True for the rest of today.
    """
    mgr = _make_manager()
    mgr.update_config(
        start_time=None,
        start_time_entity="sensor.sun_next_rising",
        end_time=None,
        end_time_entity=None,
    )

    # Far-future year + a near-midnight time-of-day: this deliberately makes a
    # host-clock evaluation land on the opposite side (real "now" is virtually
    # never past 23:59:00 on whatever day it actually is), so a regression to
    # the host clock is caught here rather than passing by date-order coincidence.
    today = dt.date(2090, 6, 15)
    tomorrow = today + dt.timedelta(days=1)
    now = dt.datetime(2090, 6, 15, 23, 59, 0)
    # Simulate the sensor reporting tomorrow's sunrise (23:59 tomorrow)
    tomorrow_sunrise = dt.datetime(
        tomorrow.year, tomorrow.month, tomorrow.day, 23, 59, 0
    )

    with (
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_safe_state",
            return_value="irrelevant-raw-state",
        ),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_datetime_from_str",
            return_value=tomorrow_sunrise,
        ),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.local_now_naive",
            return_value=now,
        ),
    ):
        result = mgr.after_start_time

    # Normalized to 2090-06-15 23:59 — equal to "now", so True
    assert result is True


@pytest.mark.unit
def test_after_start_time_entity_no_normalize_when_today():
    """A past time with today's date is not affected by normalization."""
    mgr = _make_manager()
    mgr.update_config(
        start_time=None,
        start_time_entity="sensor.sun_next_rising",
        end_time=None,
        end_time_entity=None,
    )

    # Far-future year + a near-midnight time-of-day: see
    # test_after_start_time_entity_normalizes_tomorrow_to_today for why.
    today = dt.date(2090, 6, 15)
    now = dt.datetime(2090, 6, 15, 23, 59, 0)
    # Sunrise already passed today — entity still shows today's date
    past_today = dt.datetime(today.year, today.month, today.day, 23, 58, 0)

    with (
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_safe_state",
            return_value="irrelevant-raw-state",
        ),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_datetime_from_str",
            return_value=past_today,
        ),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.local_now_naive",
            return_value=now,
        ),
    ):
        result = mgr.after_start_time

    assert result is True


@pytest.mark.unit
def test_after_start_time_entity_future_today_returns_false():
    """A future time with today's date (before event) is not normalized and returns False."""
    mgr = _make_manager()
    mgr.update_config(
        start_time=None,
        start_time_entity="sensor.sun_next_rising",
        end_time=None,
        end_time_entity=None,
    )

    now = dt.datetime(2024, 6, 15, 10, 0, 0)
    future_today = dt.datetime(2024, 6, 15, 11, 0, 0)

    with (
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_safe_state",
            return_value="irrelevant-raw-state",
        ),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_datetime_from_str",
            return_value=future_today,
        ),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.local_now_naive",
            return_value=now,
        ),
    ):
        result = mgr.after_start_time

    assert result is False


@pytest.mark.unit
def test_end_time_entity_normalizes_tomorrow_to_today():
    """Sun entity rolled to tomorrow's date is normalized in end_time property."""
    mgr = _make_manager()
    mgr.update_config(
        start_time=None,
        start_time_entity=None,
        end_time=None,
        end_time_entity="sensor.sun_next_setting",
    )

    # "Today" is pinned to the fixed HA-local sentinel rather than the host date,
    # so the entity value below is tomorrow *in the frame _normalize_to_today
    # anchors on* and normalization is exercised on every host zone. Deriving
    # it from dt.date.today() lets the two dates diverge, at which point the
    # value stops looking like "tomorrow" and this #226 guard silently stops
    # guarding instead of failing (#1161).
    today = _HA_NOW.date()
    tomorrow = today + dt.timedelta(days=1)
    tomorrow_sunset = dt.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 20, 30)

    with _patch_time_window(tomorrow_sunset):
        result = mgr.end_time

    expected = dt.datetime(today.year, today.month, today.day, 20, 30)
    assert result == expected


@pytest.mark.unit
def test_end_time_entity_no_normalize_when_today():
    """An end time with today's date is returned unchanged."""
    mgr = _make_manager()
    mgr.update_config(
        start_time=None,
        start_time_entity=None,
        end_time=None,
        end_time_entity="sensor.sun_next_setting",
    )

    # "Today" comes from the same fixed HA-local sentinel _normalize_to_today
    # anchors on; a dt.date.today() fixture sits in the host frame and the two
    # dates diverge by the zone offset for part of every day (#1161).
    today = _HA_NOW.date()
    today_sunset = dt.datetime(today.year, today.month, today.day, 20, 30)

    with _patch_time_window(today_sunset):
        result = mgr.end_time

    assert result == today_sunset


@pytest.mark.unit
def test_is_active_with_sun_entities_after_rollover():
    """Both sun entities rolled to tomorrow — is_active returns True, no error logged.

    "Today" is pinned to the fixed HA-local sentinel rather than
    ``dt.date.today()``: production anchors ``_normalize_to_today`` on HA's
    configured zone, so a host-clock fixture is only really "tomorrow" while the
    two dates agree. Wherever HA's date leads the host's, the fixture's tomorrow
    IS HA-today, normalization never runs, and the 00:01/23:59 extremes keep the
    window open anyway — so this #226 guard passed vacuously instead of failing
    (#1161).

    Sunrise=00:01 and sunset=23:59 straddle the pinned midday "now"; the
    assertions on the resolved start/end prove normalization actually fired
    rather than letting the window extremes carry a skipped normalize.
    """
    mgr = _make_manager()
    mgr.update_config(
        start_time=None,
        start_time_entity="sensor.sun_next_rising",
        end_time=None,
        end_time_entity="sensor.sun_next_setting",
    )

    today = _HA_NOW.date()
    tomorrow = today + dt.timedelta(days=1)
    tomorrow_sunrise = dt.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 1)
    tomorrow_sunset = dt.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 23, 59)

    # is_active evaluates before_end_time first (calls end_time → get_datetime_from_str),
    # then after_start_time (calls get_datetime_from_str again); the trailing value
    # serves the explicit end_time re-read below. Iterator must match that order.
    with _patch_time_window([tomorrow_sunset, tomorrow_sunrise, tomorrow_sunset]):
        result = mgr.is_active
        resolved_end = mgr.end_time

    assert result is True
    mgr.logger.error.assert_not_called()
    # Both entity values were pinned back onto the HA-local date. A skipped
    # normalize leaves them on `tomorrow` and fails here loudly, instead of
    # riding the window extremes to a silent pass.
    assert mgr.start_time_value == dt.datetime(today.year, today.month, today.day, 0, 1)
    assert resolved_end == dt.datetime(today.year, today.month, today.day, 23, 59)


# ---------------------------------------------------------------------------
# check_transition — window-open callback (#226 gap fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_check_transition_calls_on_window_open_callback():
    """on_window_open callback is invoked when window transitions inactive→active."""
    mgr = _make_manager()
    mgr.update_config(None, None, None, None)
    close_cb = AsyncMock()
    open_cb = AsyncMock()

    mgr._last_time_window_state = False

    with patch.object(
        type(mgr), "is_active", new_callable=lambda: property(lambda self: True)
    ):
        await mgr.check_transition(
            track_end_time=True,
            refresh_callback=close_cb,
            on_window_open=open_cb,
        )

    open_cb.assert_called_once()
    close_cb.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_check_transition_no_on_window_open_no_error():
    """check_transition works without on_window_open (default None) on window open."""
    mgr = _make_manager()
    mgr.update_config(None, None, None, None)
    close_cb = AsyncMock()

    mgr._last_time_window_state = False

    with patch.object(
        type(mgr), "is_active", new_callable=lambda: property(lambda self: True)
    ):
        await mgr.check_transition(track_end_time=True, refresh_callback=close_cb)

    close_cb.assert_not_called()


# ---------------------------------------------------------------------------
# UTC→local timezone conversion for sun entity strings (#226)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Daytime-gate graceful fallback (issue #742)
# ---------------------------------------------------------------------------


class _FakeState:
    def __init__(self, state: str) -> None:
        self.state = state
        self.attributes: dict = {}


class _FakeStates:
    def __init__(self) -> None:
        self._d: dict[str, _FakeState] = {}

    def set(self, entity_id: str, state: str) -> None:
        self._d[entity_id] = _FakeState(state)

    def get(self, entity_id: str):
        return self._d.get(entity_id)


class _FakeHass:
    def __init__(self) -> None:
        self.states = _FakeStates()


def _clock_manager():
    """TimeWindowManager with a fake hass + injectable fake clock (seconds)."""
    hass = _FakeHass()
    clock = [0.0]
    logger = MagicMock()
    mgr = TimeWindowManager(hass=hass, logger=logger, clock=lambda: clock[0])
    return mgr, clock


def _gate(mgr, *, sensors=(), template=None, mode="or"):
    mgr.update_config(
        start_time=None,
        start_time_entity=None,
        end_time=None,
        end_time_entity=None,
        gate_sensors=list(sensors),
        gate_template=template,
        gate_template_mode=mode,
    )


@pytest.mark.unit
def test_gate_determinate_records_and_tracks():
    """A live verdict is tracked directly and recorded for later holding."""
    mgr, _clock = _clock_manager()
    _gate(mgr, sensors=["binary_sensor.gate"])
    mgr._hass.states.set("binary_sensor.gate", "on")
    assert mgr.effective_daytime_gate is True
    assert mgr.is_active is True
    mgr._hass.states.set("binary_sensor.gate", "off")
    assert mgr.effective_daytime_gate is False
    assert mgr.gate_is_dark is True
    assert mgr.is_active is False


@pytest.mark.unit
def test_gate_within_grace_holds_last_known_dark():
    """A dark verdict that goes indeterminate is held dark within the grace window."""
    mgr, clock = _clock_manager()
    _gate(mgr, sensors=["binary_sensor.gate"])
    mgr._hass.states.set("binary_sensor.gate", "off")
    clock[0] = 0.0
    assert mgr.effective_daytime_gate is False  # records dark
    mgr._hass.states.set("binary_sensor.gate", "unknown")
    clock[0] = 30.0
    assert mgr.effective_daytime_gate is False  # HOLDING dark
    assert mgr.gate_is_dark is True
    assert mgr.is_active is False


@pytest.mark.unit
def test_gate_within_grace_no_last_known_falls_to_astral():
    """No verdict ever observed → cannot hold → astral fallback (clock-open)."""
    mgr, _clock = _clock_manager()
    _gate(mgr, sensors=["binary_sensor.gate"])  # never reported
    assert mgr.effective_daytime_gate is None
    assert mgr.gate_is_daytime is True
    assert mgr.is_active is True


@pytest.mark.unit
def test_gate_grace_expired_effective_none_and_clock_open():
    """Past the grace window the gate falls back: effective None, is_active clock-open."""
    mgr, clock = _clock_manager()
    _gate(mgr, sensors=["binary_sensor.gate"])
    mgr._hass.states.set("binary_sensor.gate", "off")
    clock[0] = 0.0
    assert mgr.effective_daytime_gate is False
    mgr._hass.states.set("binary_sensor.gate", "unavailable")
    clock[0] = 1.0
    assert mgr.effective_daytime_gate is False  # HOLDING (anchor=1.0)
    clock[0] = 1.0 + 121.0
    assert mgr.effective_daytime_gate is None  # FELL_BACK
    assert mgr.is_active is True  # clock-open astral fallback


@pytest.mark.unit
def test_gate_one_valid_one_invalid_sensor_is_not_indeterminate():
    """One valid sensor + one dead sensor is a real verdict, not indeterminate."""
    mgr, _clock = _clock_manager()
    _gate(mgr, sensors=["binary_sensor.a", "binary_sensor.b"])
    mgr._hass.states.set("binary_sensor.a", "on")
    mgr._hass.states.set("binary_sensor.b", "unavailable")
    # b is ignored (invalid); a says on → daytime, determinately.
    assert mgr.effective_daytime_gate is True
    assert mgr.seconds_until_gate_fallback() is None


@pytest.mark.unit
def test_gate_valid_template_with_dead_sensor_is_not_indeterminate():
    """A valid template opinion stands even when the only sensor is dead."""
    mgr, _clock = _clock_manager()

    # Render a fixed template result via the module-level render helper.
    with patch(
        "custom_components.adaptive_cover_pro.managers.time_window."
        "render_condition_or_none",
        return_value=False,
    ):
        _gate(
            mgr,
            sensors=["binary_sensor.dead"],
            template="{{ false }}",
        )
        mgr._hass.states.set("binary_sensor.dead", "unavailable")
        # sensor opinion None, template opinion False → dark, determinately.
        assert mgr.effective_daytime_gate is False
        assert mgr.seconds_until_gate_fallback() is None


@pytest.mark.unit
def test_seconds_until_gate_fallback_phases():
    """seconds_until_gate_fallback: None determinate / ~remaining holding / None expired."""
    mgr, clock = _clock_manager()
    _gate(mgr, sensors=["binary_sensor.gate"])
    mgr._hass.states.set("binary_sensor.gate", "on")
    clock[0] = 0.0
    assert mgr.effective_daytime_gate is True
    assert mgr.seconds_until_gate_fallback() is None  # determinate → no wake

    mgr._hass.states.set("binary_sensor.gate", "unavailable")
    clock[0] = 20.0
    # First indeterminate observation anchors the window at t=20 → full grace left.
    assert mgr.effective_daytime_gate is True  # HOLDING
    assert mgr.seconds_until_gate_fallback() == pytest.approx(120.0)
    # 20s into the window → ~100s remain.
    clock[0] = 40.0
    assert mgr.seconds_until_gate_fallback() == pytest.approx(100.0)

    clock[0] = 20.0 + 121.0
    assert mgr.effective_daytime_gate is None  # FELL_BACK
    assert mgr.seconds_until_gate_fallback() is None


@pytest.mark.unit
def test_update_config_no_reset_when_gate_unchanged_preserves_hold():
    """An identical update_config call must NOT forget the held verdict."""
    mgr, clock = _clock_manager()
    _gate(mgr, sensors=["binary_sensor.gate"])
    mgr._hass.states.set("binary_sensor.gate", "on")
    clock[0] = 0.0
    assert mgr.effective_daytime_gate is True  # record last-known
    mgr._hass.states.set("binary_sensor.gate", "unavailable")
    clock[0] = 10.0
    assert mgr.effective_daytime_gate is True  # HOLDING

    # Re-apply the SAME gate config (as the coordinator does every cycle).
    _gate(mgr, sensors=["binary_sensor.gate"])
    clock[0] = 20.0
    assert mgr.effective_daytime_gate is True  # still HOLDING — not reset


@pytest.mark.unit
def test_update_config_resets_when_gate_changed_forgets_last_known():
    """Changing the sensor list forgets the held verdict (fresh grace machine)."""
    mgr, clock = _clock_manager()
    _gate(mgr, sensors=["binary_sensor.gate"])
    mgr._hass.states.set("binary_sensor.gate", "on")
    clock[0] = 0.0
    assert mgr.effective_daytime_gate is True  # record last-known on the old sensor

    # Switch to a different (never-reported) sensor → reset → no last-known.
    _gate(mgr, sensors=["binary_sensor.other"])
    assert mgr.effective_daytime_gate is None  # FELL_BACK (nothing to hold)


@pytest.mark.unit
def test_after_start_time_with_utc_iso_sun_sensor_string():
    """Entity state is a real UTC ISO string — is converted through to local wall-clock.

    Regression: before the fix, "04:46 UTC" was compared as naive 04:46 in a
    non-UTC zone, causing the window to activate hours early.

    Setup: HA's zone is America/New_York (UTC-4 in April). The sensor reports
    04:46 UTC, which is 00:46 local; "now" is 01:00 local, so the start has
    passed by 14 minutes.

    The load-bearing assertion is on the *resolved* start time, not the
    boolean: a naive re-parse would resolve 04:46 and ``_normalize_to_today``
    would happily leave a same-day value alone, so only the resolved value
    distinguishes "converted through HA's zone" from "compared as naive".
    """

    mgr = _make_manager()
    mgr.update_config(
        start_time=None,
        start_time_entity="sensor.sun_next_rising",
        end_time=None,
        end_time_entity=None,
    )

    ny = zoneinfo.ZoneInfo("America/New_York")
    # "now" is 01:00 local NY — after the 00:46 local sunrise.
    frozen_now = dt.datetime(2026, 4, 18, 1, 0, 0)

    with (
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_safe_state",
            return_value="2026-04-18T04:46:00+00:00",
        ),
        patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", ny),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.local_now_naive",
            return_value=frozen_now,
        ),
    ):
        result = mgr.after_start_time

    # 04:46 UTC → 00:46 local NY, not a naive 04:46.
    assert mgr.start_time_value == dt.datetime(2026, 4, 18, 0, 46)
    assert result is True


# ---------------------------------------------------------------------------
# acp self-reference namespace in the daytime-gate template (issue #1159)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_gate_template_resolves_the_acp_namespace(hass):
    """The daytime-gate template can name this instance's own entity (#1159)."""
    from tests._helpers.acp_namespace import (
        acp_variables,
        make_acp_entry,
        seed_acp_row,
    )

    entry = make_acp_entry(hass, "gate_acp_01")
    entity_id = seed_acp_row(
        hass, entry, "switch", "sun_tracking", "dining_room_shade_sun_tracking"
    )
    hass.states.async_set(entity_id, "on")
    await hass.async_block_till_done()

    mgr = TimeWindowManager(
        hass=hass,
        logger=MagicMock(),
        template_variables=acp_variables(hass, entry),
    )
    mgr.update_config(
        start_time=None,
        start_time_entity=None,
        end_time=None,
        end_time_entity=None,
        gate_sensors=[],
        gate_template="{{ is_state(acp.sun_tracking, 'on') }}",
        gate_template_mode="or",
    )
    assert mgr.effective_daytime_gate is True

    hass.states.async_set(entity_id, "off")
    await hass.async_block_till_done()
    assert mgr.effective_daytime_gate is False
