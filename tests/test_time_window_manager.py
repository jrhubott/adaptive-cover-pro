"""Unit tests for TimeWindowManager covering previously uncovered branches."""

from __future__ import annotations

import datetime as dt
import zoneinfo
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.managers.time_window import TimeWindowManager


def _make_manager(mock_hass=None):
    """Build a TimeWindowManager with a MagicMock hass and logger."""
    hass = mock_hass or MagicMock()
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.error = MagicMock()
    return TimeWindowManager(hass=hass, logger=logger)


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

    # Use a time far in the past so now >= time is True
    past_time = dt.datetime.now() - dt.timedelta(hours=1)

    with (
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_safe_state",
            return_value="2024-01-01T07:00:00",
        ),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_datetime_from_str",
            return_value=past_time,
        ),
    ):
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

    future_time = dt.datetime.now() + dt.timedelta(hours=2)

    with (
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_safe_state",
            return_value="2099-01-01T22:00:00",
        ),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_datetime_from_str",
            return_value=future_time,
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

    today = dt.date(2024, 6, 15)
    tomorrow = today + dt.timedelta(days=1)
    now = dt.datetime(2024, 6, 15, 12, 0, 0)
    # Simulate the sensor reporting tomorrow's sunrise (06:30 tomorrow)
    tomorrow_sunrise = dt.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 6, 30)

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
            "custom_components.adaptive_cover_pro.managers.time_window.dt"
        ) as mock_dt,
    ):
        mock_dt.date.today.return_value = today
        mock_dt.datetime.now.return_value = now
        mock_dt.timedelta = dt.timedelta
        result = mgr.after_start_time

    # Normalized to 2024-06-15 06:30 — which is before noon (now), so True
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

    today = dt.date(2024, 6, 15)
    now = dt.datetime(2024, 6, 15, 12, 0, 0)
    # Sunrise already passed today — entity still shows today's date
    past_today = dt.datetime(today.year, today.month, today.day, 6, 30)

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
            "custom_components.adaptive_cover_pro.managers.time_window.dt"
        ) as mock_dt,
    ):
        mock_dt.date.today.return_value = today
        mock_dt.datetime.now.return_value = now
        mock_dt.timedelta = dt.timedelta
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

    # Entity reports today's date but 1 hour in the future — small enough to never cross midnight
    future_today = dt.datetime.now() + dt.timedelta(hours=1)

    with (
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_safe_state",
            return_value="irrelevant-raw-state",
        ),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_datetime_from_str",
            return_value=future_today,
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

    today = dt.date.today()
    tomorrow = today + dt.timedelta(days=1)
    tomorrow_sunset = dt.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 20, 30)

    with (
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_safe_state",
            return_value="irrelevant-raw-state",
        ),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_datetime_from_str",
            return_value=tomorrow_sunset,
        ),
    ):
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

    today = dt.date.today()
    today_sunset = dt.datetime(today.year, today.month, today.day, 20, 30)

    with (
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_safe_state",
            return_value="irrelevant-raw-state",
        ),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_datetime_from_str",
            return_value=today_sunset,
        ),
    ):
        result = mgr.end_time

    assert result == today_sunset


@pytest.mark.unit
def test_is_active_with_sun_entities_after_rollover():
    """Both sun entities rolled to tomorrow — is_active returns True, no error logged.

    Uses sunrise=00:01 and sunset=23:59 so the window is always active regardless
    of when the test runs. Verifies normalization integrates correctly end-to-end.
    """
    mgr = _make_manager()
    mgr.update_config(
        start_time=None,
        start_time_entity="sensor.sun_next_rising",
        end_time=None,
        end_time_entity="sensor.sun_next_setting",
    )

    today = dt.date.today()
    tomorrow = today + dt.timedelta(days=1)
    # Use extreme times so the window is active regardless of when the test runs
    tomorrow_sunrise = dt.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 1)
    tomorrow_sunset = dt.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 23, 59)

    # is_active evaluates before_end_time first (calls end_time → get_datetime_from_str),
    # then after_start_time (calls get_datetime_from_str again). Iterator must match that order.
    parsed_values = iter([tomorrow_sunset, tomorrow_sunrise])

    with (
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_safe_state",
            return_value="irrelevant-raw-state",
        ),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_datetime_from_str",
            side_effect=lambda _: next(parsed_values),
        ),
    ):
        result = mgr.is_active

    assert result is True
    mgr.logger.error.assert_not_called()


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


@pytest.mark.unit
def test_after_start_time_with_utc_iso_sun_sensor_string():
    """Entity state is a real UTC ISO string — is converted through to local wall-clock.

    Regression: before the fix, "04:46 UTC" was compared as naive 04:46 in a
    non-UTC zone, causing the window to activate hours early.

    Setup: local timezone America/New_York (UTC-4 DST). Sunrise UTC is 04:46,
    which is 00:46 local. Freeze "now" at 01:00 local (after 00:46 local sunrise)
    so after_start_time should be True.
    """

    mgr = _make_manager()
    mgr.update_config(
        start_time=None,
        start_time_entity="sensor.sun_next_rising",
        end_time=None,
        end_time_entity=None,
    )

    ny = zoneinfo.ZoneInfo("America/New_York")
    # "now" is 01:00 local NY — after 00:46 local sunrise
    frozen_now = dt.datetime(2026, 4, 18, 1, 0, 0)

    with (
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.get_safe_state",
            return_value="2026-04-18T04:46:00+00:00",
        ),
        patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", ny),
        patch(
            "custom_components.adaptive_cover_pro.managers.time_window.dt"
        ) as mock_dt,
    ):
        # "now" is 01:00 local NY — after 00:46 local sunrise (04:46 UTC converted)
        mock_dt.datetime.now.return_value = frozen_now
        mock_dt.date.today.return_value = dt.date(2026, 4, 18)
        mock_dt.timedelta = dt.timedelta
        result = mgr.after_start_time

    assert result is True
