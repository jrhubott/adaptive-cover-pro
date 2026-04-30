"""Tests for WeatherManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.managers.weather import WeatherManager


@pytest.fixture
def mock_hass():
    """Return a mock Home Assistant instance."""
    return MagicMock()


@pytest.fixture
def logger():
    """Return a mock logger."""
    return MagicMock()


@pytest.fixture
def mgr(mock_hass, logger):
    """Return a WeatherManager with no sensors configured."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor=None,
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    return m


def _make_state(value: str) -> MagicMock:
    """Return a mock HA state with the given state string."""
    s = MagicMock()
    s.state = value
    return s


# --- configured_sensors ---


def test_configured_sensors_empty_when_none(mgr):
    """Returns empty list when no sensors configured."""
    assert mgr.configured_sensors == []


def test_configured_sensors_includes_all(mock_hass, logger):
    """Returns all configured entity IDs."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor="sensor.wind",
        wind_direction_sensor="sensor.wind_dir",
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor="sensor.rain",
        rain_threshold=1.0,
        is_raining_sensor="binary_sensor.raining",
        is_windy_sensor="binary_sensor.windy",
        severe_sensors=["binary_sensor.hail", "binary_sensor.frost"],
        timeout_seconds=300,
    )
    sensors = m.configured_sensors
    assert "sensor.wind" in sensors
    assert "sensor.wind_dir" in sensors
    assert "sensor.rain" in sensors
    assert "binary_sensor.raining" in sensors
    assert "binary_sensor.windy" in sensors
    assert "binary_sensor.hail" in sensors
    assert "binary_sensor.frost" in sensors
    assert len(sensors) == 7


# --- is_weather_override_active ---


def test_not_active_when_no_sensors(mgr):
    """Feature disabled when no sensors configured."""
    mgr._override_active = True  # Even if flag set, no sensors → False
    assert mgr.is_weather_override_active is False


def test_active_when_flag_set_and_sensors_configured(mock_hass, logger):
    """Returns True when _override_active is True and sensors configured."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor="sensor.wind",
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    m._override_active = True
    assert m.is_weather_override_active is True


def test_not_active_when_flag_false(mock_hass, logger):
    """Returns False when sensors configured but flag not set."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor="sensor.wind",
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    assert m.is_weather_override_active is False


# --- is_any_condition_active: wind speed ---


def test_wind_speed_above_threshold(mock_hass, logger):
    """Returns True when wind speed exceeds threshold."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor="sensor.wind",
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    mock_hass.states.get.return_value = _make_state("75.0")
    assert m.is_any_condition_active is True


def test_wind_speed_below_threshold(mock_hass, logger):
    """Returns False when wind speed is below threshold."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor="sensor.wind",
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    mock_hass.states.get.return_value = _make_state("20.0")
    assert m.is_any_condition_active is False


def test_wind_speed_unavailable_treated_as_inactive(mock_hass, logger):
    """Unavailable wind sensor does not trigger override."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor="sensor.wind",
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    mock_hass.states.get.return_value = _make_state("unavailable")
    assert m.is_any_condition_active is False


def test_wind_speed_unknown_treated_as_inactive(mock_hass, logger):
    """Unknown wind sensor state does not trigger override."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor="sensor.wind",
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    mock_hass.states.get.return_value = _make_state("unknown")
    assert m.is_any_condition_active is False


def test_wind_speed_none_state_treated_as_inactive(mock_hass, logger):
    """Missing wind sensor entity does not trigger override."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor="sensor.wind",
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    mock_hass.states.get.return_value = None
    assert m.is_any_condition_active is False


# --- is_any_condition_active: wind direction ---


def _make_wind_mgr(mock_hass, logger, *, win_azi=180, tolerance=45):
    """Build WeatherManager with wind speed + direction configured."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor="sensor.wind_speed",
        wind_direction_sensor="sensor.wind_dir",
        wind_speed_threshold=50.0,
        wind_direction_tolerance=tolerance,
        win_azi=win_azi,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    return m


def _dir_states(mock_hass, speed: float, direction: float):
    """Configure mock_hass.states.get to return speed+direction for respective sensors."""

    def get_state(entity_id):
        if entity_id == "sensor.wind_speed":
            return _make_state(str(speed))
        if entity_id == "sensor.wind_dir":
            return _make_state(str(direction))
        return None

    mock_hass.states.get.side_effect = get_state


def test_wind_direction_within_tolerance_triggers(mock_hass, logger):
    """Wind from same direction as window azimuth (within tolerance) triggers."""
    m = _make_wind_mgr(mock_hass, logger, win_azi=180, tolerance=45)
    _dir_states(mock_hass, speed=75.0, direction=190.0)  # 10° from 180 — within 45°
    assert m.is_any_condition_active is True


def test_wind_direction_outside_tolerance_no_trigger(mock_hass, logger):
    """Wind from different direction (outside tolerance) does not trigger."""
    m = _make_wind_mgr(mock_hass, logger, win_azi=180, tolerance=45)
    _dir_states(mock_hass, speed=75.0, direction=280.0)  # 100° from 180 — outside 45°
    assert m.is_any_condition_active is False


def test_wind_direction_wraparound(mock_hass, logger):
    """Angular distance handles 0°/360° wraparound correctly."""
    # Window faces north (0°). Wind from 350° is only 10° away.
    m = _make_wind_mgr(mock_hass, logger, win_azi=0, tolerance=45)
    _dir_states(mock_hass, speed=75.0, direction=350.0)  # 10° from 0° via wraparound
    assert m.is_any_condition_active is True


def test_wind_direction_wraparound_outside(mock_hass, logger):
    """Wraparound check correctly excludes wind from opposite direction."""
    # Window faces north (0°). Wind from 180° is 180° away.
    m = _make_wind_mgr(mock_hass, logger, win_azi=0, tolerance=45)
    _dir_states(mock_hass, speed=75.0, direction=180.0)
    assert m.is_any_condition_active is False


def test_wind_direction_unavailable_assumes_exposed(mock_hass, logger):
    """Direction sensor unavailable → assume wind hits window (safe default)."""
    m = _make_wind_mgr(mock_hass, logger, win_azi=180, tolerance=45)

    def get_state(entity_id):
        if entity_id == "sensor.wind_speed":
            return _make_state("75.0")
        return _make_state("unavailable")

    mock_hass.states.get.side_effect = get_state
    assert m.is_any_condition_active is True


# --- is_any_condition_active: rain ---


def test_rain_above_threshold_triggers(mock_hass, logger):
    """Returns True when rain rate exceeds threshold."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor=None,
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor="sensor.rain",
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    mock_hass.states.get.return_value = _make_state("5.0")
    assert m.is_any_condition_active is True


def test_rain_below_threshold_no_trigger(mock_hass, logger):
    """Returns False when rain rate is below threshold."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor=None,
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor="sensor.rain",
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    mock_hass.states.get.return_value = _make_state("0.2")
    assert m.is_any_condition_active is False


# --- is_any_condition_active: binary sensors ---


def test_is_raining_binary_on_triggers(mock_hass, logger):
    """IsRaining binary 'on' triggers override."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor=None,
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor="binary_sensor.raining",
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    mock_hass.states.get.return_value = _make_state("on")
    assert m.is_any_condition_active is True


def test_is_raining_binary_off_no_trigger(mock_hass, logger):
    """IsRaining binary 'off' does not trigger override."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor=None,
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor="binary_sensor.raining",
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    mock_hass.states.get.return_value = _make_state("off")
    assert m.is_any_condition_active is False


def test_is_windy_binary_on_triggers(mock_hass, logger):
    """IsWindy binary 'on' triggers override."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor=None,
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor="binary_sensor.windy",
        severe_sensors=[],
        timeout_seconds=300,
    )
    mock_hass.states.get.return_value = _make_state("on")
    assert m.is_any_condition_active is True


def test_severe_any_on_triggers(mock_hass, logger):
    """Any severe weather binary 'on' triggers override."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor=None,
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=["binary_sensor.hail", "binary_sensor.frost"],
        timeout_seconds=300,
    )

    def get_state(entity_id):
        s = MagicMock()
        s.state = "on" if entity_id == "binary_sensor.hail" else "off"
        return s

    mock_hass.states.get.side_effect = get_state
    assert m.is_any_condition_active is True


def test_severe_all_off_no_trigger(mock_hass, logger):
    """Severe sensors all off does not trigger."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor=None,
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=["binary_sensor.hail", "binary_sensor.frost"],
        timeout_seconds=300,
    )
    mock_hass.states.get.return_value = _make_state("off")
    assert m.is_any_condition_active is False


# --- OR logic ---


def test_or_logic_single_condition_sufficient(mock_hass, logger):
    """Only one condition active is sufficient to trigger override."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor="sensor.wind",
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor="sensor.rain",
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )

    def get_state(entity_id):
        if entity_id == "sensor.wind":
            return _make_state("10.0")  # below threshold
        if entity_id == "sensor.rain":
            return _make_state("5.0")  # above threshold
        return None

    mock_hass.states.get.side_effect = get_state
    assert m.is_any_condition_active is True


# --- record_conditions_active ---


def test_record_conditions_active_sets_flag(mock_hass, logger):
    """record_conditions_active sets _override_active to True."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor="sensor.wind",
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    m.record_conditions_active()
    assert m._override_active is True


def test_record_conditions_active_cancels_timeout(mock_hass, logger):
    """record_conditions_active cancels a running clear-delay timeout."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor="sensor.wind",
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    task = MagicMock()
    task.done.return_value = False
    m._timeout_task = task

    m.record_conditions_active()

    task.cancel.assert_called_once()
    assert m._timeout_task is None


# --- cancel_weather_timeout ---


def test_cancel_timeout_cancels_task(mock_hass, logger):
    """cancel_weather_timeout cancels running task and clears reference."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    task = MagicMock()
    task.done.return_value = False
    m._timeout_task = task

    m.cancel_weather_timeout()

    task.cancel.assert_called_once()
    assert m._timeout_task is None


def test_cancel_timeout_no_task_safe(mgr):
    """cancel_weather_timeout does not raise when no task."""
    mgr.cancel_weather_timeout()
    assert mgr._timeout_task is None


def test_cancel_timeout_already_done(mock_hass, logger):
    """Does not call cancel on an already-done task."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    task = MagicMock()
    task.done.return_value = True
    m._timeout_task = task

    m.cancel_weather_timeout()

    task.cancel.assert_not_called()
    assert m._timeout_task is None


# --- _weather_timeout_handler ---


@pytest.mark.asyncio
async def test_timeout_handler_deactivates_when_clear(mock_hass, logger):
    """Timeout handler deactivates override and calls refresh when conditions clear."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor="sensor.wind",
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    m._override_active = True

    # Patch is_any_condition_active to return False (conditions cleared)
    original_prop = WeatherManager.is_any_condition_active
    WeatherManager.is_any_condition_active = property(lambda self: False)
    try:
        callback = AsyncMock()
        await m._weather_timeout_handler(0.01, callback)

        assert m._override_active is False
        callback.assert_called_once()
    finally:
        WeatherManager.is_any_condition_active = original_prop


@pytest.mark.asyncio
async def test_timeout_handler_stays_active_if_conditions_return(mock_hass, logger):
    """Timeout handler keeps override active if conditions return during sleep."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor="sensor.wind",
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    m._override_active = True

    # Patch is_any_condition_active to return True (conditions returned)
    original_prop = WeatherManager.is_any_condition_active
    WeatherManager.is_any_condition_active = property(lambda self: True)
    try:
        callback = AsyncMock()
        await m._weather_timeout_handler(0.01, callback)

        assert m._override_active is True
        callback.assert_not_called()
    finally:
        WeatherManager.is_any_condition_active = original_prop


# --- update_config ---


def test_update_config_stores_all_values(mock_hass, logger):
    """update_config correctly stores all configuration values."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor="sensor.wind",
        wind_direction_sensor="sensor.dir",
        wind_speed_threshold=30.0,
        wind_direction_tolerance=30,
        win_azi=90,
        rain_sensor="sensor.rain",
        rain_threshold=2.5,
        is_raining_sensor="binary_sensor.rain",
        is_windy_sensor="binary_sensor.wind",
        severe_sensors=["binary_sensor.hail"],
        timeout_seconds=600,
    )
    assert m._wind_speed_sensor == "sensor.wind"
    assert m._wind_direction_sensor == "sensor.dir"
    assert m._wind_speed_threshold == 30.0
    assert m._wind_direction_tolerance == 30
    assert m._win_azi == 90
    assert m._rain_sensor == "sensor.rain"
    assert m._rain_threshold == 2.5
    assert m._is_raining_sensor == "binary_sensor.rain"
    assert m._is_windy_sensor == "binary_sensor.wind"
    assert m._severe_sensors == ["binary_sensor.hail"]


# --- is_timeout_running ---


def test_is_timeout_running_false_when_no_task(mgr):
    assert mgr.is_timeout_running is False


@pytest.mark.asyncio
async def test_is_timeout_running_true_when_task_pending(mgr):
    import asyncio

    async def _long_sleep():
        await asyncio.sleep(9999)

    task = asyncio.create_task(_long_sleep())
    mgr._timeout_task = task
    try:
        assert mgr.is_timeout_running is True
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_is_timeout_running_false_when_task_done(mgr):
    import asyncio

    async def _noop():
        pass

    task = asyncio.create_task(_noop())
    await task
    mgr._timeout_task = task
    assert mgr.is_timeout_running is False


# --- reconcile ---


def _make_simple_wind_mgr(
    mock_hass, logger, *, speed: str = "10.0", threshold: float = 50.0
):
    """Return manager with one wind sensor reporting the given speed."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor="sensor.wind",
        wind_direction_sensor=None,
        wind_speed_threshold=threshold,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    mock_hass.states.get.return_value = MagicMock(state=speed)
    return m


def test_reconcile_signals_start_when_flag_stuck_and_clear(mock_hass, logger):
    """G1: override flag True, conditions clear, no timer → should_start_timeout."""
    m = _make_simple_wind_mgr(mock_hass, logger, speed="10.0")
    m._override_active = True
    assert m.reconcile() == "should_start_timeout"


def test_reconcile_noop_when_conditions_still_active(mock_hass, logger):
    """G2: override flag True, conditions active → None."""
    m = _make_simple_wind_mgr(mock_hass, logger, speed="75.0")
    m._override_active = True
    assert m.reconcile() is None


def test_reconcile_noop_when_flag_not_set(mock_hass, logger):
    """G3: override flag False, conditions clear → None."""
    m = _make_simple_wind_mgr(mock_hass, logger, speed="10.0")
    m._override_active = False
    assert m.reconcile() is None


@pytest.mark.asyncio
async def test_reconcile_noop_when_timer_already_running(mock_hass, logger):
    """G4: override flag True, conditions clear, timer pending → None."""
    import asyncio

    m = _make_simple_wind_mgr(mock_hass, logger, speed="10.0")
    m._override_active = True

    async def _long_sleep():
        await asyncio.sleep(9999)

    task = asyncio.create_task(_long_sleep())
    m._timeout_task = task
    try:
        assert m.reconcile() is None
    finally:
        task.cancel()


def test_reconcile_noop_when_no_sensors_configured(mock_hass, logger):
    """No sensors configured → None (feature disabled)."""
    m = WeatherManager(hass=mock_hass, logger=logger)
    m.update_config(
        wind_speed_sensor=None,
        wind_direction_sensor=None,
        wind_speed_threshold=50.0,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=300,
    )
    m._override_active = True
    assert m.reconcile() is None
