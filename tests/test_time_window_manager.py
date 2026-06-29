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

    today = dt.date(2024, 6, 15)
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
            "custom_components.adaptive_cover_pro.managers.time_window.dt"
        ) as mock_dt,
    ):
        mock_dt.date.today.return_value = today
        mock_dt.datetime.now.return_value = now
        mock_dt.timedelta = dt.timedelta
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

    today = dt.date(2024, 6, 15)
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
            "custom_components.adaptive_cover_pro.managers.time_window.dt"
        ) as mock_dt,
    ):
        mock_dt.date.today.return_value = today
        mock_dt.datetime.now.return_value = now
        mock_dt.timedelta = dt.timedelta
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
