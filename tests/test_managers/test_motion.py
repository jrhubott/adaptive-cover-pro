"""Tests for MotionManager."""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.managers.motion import MotionManager


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
    """Return a MotionManager configured with no sensors."""
    m = MotionManager(hass=mock_hass, logger=logger)
    m.update_config(sensors=[], timeout_seconds=300)
    return m


# --- is_motion_detected ---


def test_is_motion_detected_no_sensors(mgr):
    """Returns True when no sensors configured (feature disabled → assume presence)."""
    assert mgr.is_motion_detected is True


def test_is_motion_detected_sensor_on(mock_hass, logger):
    """Returns True when a configured sensor is on."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)

    state = MagicMock()
    state.state = "on"
    mock_hass.states.get.return_value = state

    assert mgr.is_motion_detected is True


def test_is_motion_detected_sensor_off(mock_hass, logger):
    """Returns False when all configured sensors are off."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)

    state = MagicMock()
    state.state = "off"
    mock_hass.states.get.return_value = state

    assert mgr.is_motion_detected is False


def test_is_motion_detected_sensor_unavailable(mock_hass, logger):
    """Returns True when sensor is missing (fail-open; don't penalize for outages)."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)

    mock_hass.states.get.return_value = None

    assert mgr.is_motion_detected is True


def test_is_motion_detected_or_logic(mock_hass, logger):
    """Returns True when any one of multiple sensors is on (OR logic)."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(
        sensors=[
            "binary_sensor.motion_living",
            "binary_sensor.motion_kitchen",
        ],
        timeout_seconds=300,
    )

    def get_state(entity_id):
        s = MagicMock()
        s.state = "on" if entity_id == "binary_sensor.motion_kitchen" else "off"
        return s

    mock_hass.states.get.side_effect = get_state

    assert mgr.is_motion_detected is True


# --- is_motion_timeout_active ---


def test_is_motion_timeout_active_no_sensors(mgr):
    """Returns False when no sensors configured (feature disabled)."""
    assert mgr.is_motion_timeout_active is False


def test_is_motion_timeout_active_when_set(mock_hass, logger):
    """Returns True when _motion_timeout_active flag is True and sensors configured."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)
    mgr._motion_timeout_active = True

    assert mgr.is_motion_timeout_active is True


def test_is_motion_timeout_active_sensors_but_flag_false(mock_hass, logger):
    """Returns False when sensors configured but flag not set."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)

    assert mgr.is_motion_timeout_active is False


# --- _now ---


def test_now_returns_utc_aware_datetime():
    """_now() returns a UTC-aware datetime so every timestamp source is consistent."""
    now = MotionManager._now()

    assert isinstance(now, dt.datetime)
    assert now.tzinfo is not None
    assert now.utcoffset() == dt.timedelta(0)


# --- last_motion_time ---


def test_last_motion_time_initially_none(mgr):
    """Returns None before any motion is recorded."""
    assert mgr.last_motion_time is None


def test_last_motion_time_tracking(mgr):
    """record_motion_detected() updates last_motion_time to a recent timestamp."""
    assert mgr.last_motion_time is None  # ensure None before

    mgr.record_motion_detected()

    assert mgr.last_motion_time is not None
    assert isinstance(mgr.last_motion_time, float)


# --- record_motion_detected ---


def test_record_motion_detected_clears_active_flag(mock_hass, logger):
    """record_motion_detected() clears _motion_timeout_active."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)
    mgr._motion_timeout_active = True

    mgr.record_motion_detected()

    assert mgr._motion_timeout_active is False


def test_record_motion_detected_cancels_task(mock_hass, logger):
    """record_motion_detected() cancels a running timeout task."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)

    task = MagicMock()
    task.done.return_value = False
    mgr._motion_timeout_task = task

    mgr.record_motion_detected()

    task.cancel.assert_called_once()
    assert mgr._motion_timeout_task is None


# --- cancel_motion_timeout ---


def test_cancel_motion_timeout(mock_hass, logger):
    """Cancels mock task and sets _motion_timeout_task to None."""
    mgr = MotionManager(hass=mock_hass, logger=logger)

    task = MagicMock()
    task.done.return_value = False
    mgr._motion_timeout_task = task

    mgr.cancel_motion_timeout()

    task.cancel.assert_called_once()
    assert mgr._motion_timeout_task is None


def test_cancel_motion_timeout_no_task(mgr):
    """Does not raise when no task exists."""
    mgr.cancel_motion_timeout()
    assert mgr._motion_timeout_task is None


def test_cancel_motion_timeout_already_done(mock_hass, logger):
    """Does not call cancel when task is already done."""
    mgr = MotionManager(hass=mock_hass, logger=logger)

    task = MagicMock()
    task.done.return_value = True
    mgr._motion_timeout_task = task

    mgr.cancel_motion_timeout()

    task.cancel.assert_not_called()
    assert mgr._motion_timeout_task is None


# --- _motion_timeout_handler ---


@pytest.mark.asyncio
async def test_motion_timeout_handler_sets_active(mock_hass, logger):
    """Timeout handler sets active flag and calls refresh when motion absent."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)

    # Patch is_motion_detected to return False (no motion after timeout)
    original_prop = MotionManager.is_motion_detected
    MotionManager.is_motion_detected = property(lambda self: False)
    try:
        callback = AsyncMock()
        await mgr._motion_timeout_handler(0.01, callback)

        assert mgr._motion_timeout_active is True
        callback.assert_called_once()
    finally:
        MotionManager.is_motion_detected = original_prop


@pytest.mark.asyncio
async def test_motion_timeout_handler_skips_if_motion(mock_hass, logger):
    """Timeout handler does not set active flag if motion was re-detected."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)

    # Patch is_motion_detected to return True (motion during timeout)
    original_prop = MotionManager.is_motion_detected
    MotionManager.is_motion_detected = property(lambda self: True)
    try:
        callback = AsyncMock()
        await mgr._motion_timeout_handler(0.01, callback)

        assert mgr._motion_timeout_active is False
        callback.assert_not_called()
    finally:
        MotionManager.is_motion_detected = original_prop
