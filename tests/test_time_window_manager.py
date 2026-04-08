"""Unit tests for TimeWindowManager covering previously uncovered branches."""

from __future__ import annotations

import datetime as dt
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

    with patch(
        "custom_components.adaptive_cover_pro.managers.time_window.get_safe_state",
        return_value="2024-01-01T07:00:00",
    ), patch(
        "custom_components.adaptive_cover_pro.managers.time_window.get_datetime_from_str",
        return_value=past_time,
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

    with patch(
        "custom_components.adaptive_cover_pro.managers.time_window.get_safe_state",
        return_value="2099-01-01T22:00:00",
    ), patch(
        "custom_components.adaptive_cover_pro.managers.time_window.get_datetime_from_str",
        return_value=future_time,
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

    with patch(
        "custom_components.adaptive_cover_pro.managers.time_window.get_safe_state",
        return_value="2024-06-21T20:00:00",
    ), patch(
        "custom_components.adaptive_cover_pro.managers.time_window.get_datetime_from_str",
        return_value=expected,
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
    from custom_components.adaptive_cover_pro.managers.time_window import TimeWindowManager

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

    with patch.object(TimeWindowManager, "end_time", new_callable=PropertyMock, return_value=past_end), \
         patch.object(TimeWindowManager, "before_end_time", new_callable=PropertyMock, return_value=True), \
         patch.object(TimeWindowManager, "after_start_time", new_callable=PropertyMock, return_value=True):
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
    with patch.object(type(mgr), "is_active", new_callable=lambda: property(lambda self: False)):
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

    with patch.object(type(mgr), "is_active", new_callable=lambda: property(lambda self: False)):
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

    with patch.object(type(mgr), "is_active", new_callable=lambda: property(lambda self: True)):
        await mgr.check_transition(track_end_time=True, refresh_callback=callback)

    callback.assert_not_called()
