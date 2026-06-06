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


# --- media players (occupancy via playback) ---


@pytest.mark.parametrize(
    ("player_state", "expected"),
    [
        ("playing", True),
        ("paused", True),
        ("idle", True),
        ("buffering", True),
        ("standby", True),
        ("on", True),
        ("off", False),
        ("unavailable", False),
        ("unknown", False),
    ],
)
def test_is_motion_detected_media_player_states(
    mock_hass, logger, player_state, expected
):
    """Any non-off/-unavailable/-unknown media_player state counts as occupancy."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(
        sensors=[], timeout_seconds=300, media_players=["media_player.tv"]
    )

    state = MagicMock()
    state.state = player_state
    mock_hass.states.get.return_value = state

    assert mgr.is_motion_detected is expected


def test_is_motion_detected_media_player_missing(mock_hass, logger):
    """A missing media player is not occupancy (fail-closed)."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(
        sensors=[], timeout_seconds=300, media_players=["media_player.tv"]
    )

    mock_hass.states.get.return_value = None

    assert mgr.is_motion_detected is False


def test_is_motion_detected_sensor_off_media_player_playing(mock_hass, logger):
    """Media players OR with motion sensors: playing player wins over an off sensor."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(
        sensors=["binary_sensor.motion_room"],
        timeout_seconds=300,
        media_players=["media_player.tv"],
    )

    def get_state(entity_id):
        s = MagicMock()
        s.state = "playing" if entity_id == "media_player.tv" else "off"
        return s

    mock_hass.states.get.side_effect = get_state

    assert mgr.is_motion_detected is True


# --- is_motion_timeout_active ---


def test_is_motion_timeout_active_no_sensors(mgr):
    """Returns False when no sensors configured (feature disabled)."""
    assert mgr.is_motion_timeout_active is False


def test_is_motion_timeout_active_when_set(mock_hass, logger):
    """Returns True after a no-motion event has activated the fallback state."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)
    mgr.set_no_motion()  # public path that flips the active flag without waiting

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
    """record_motion_detected() returns the manager to the "motion present" state."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)
    mgr.set_no_motion()
    assert mgr.is_motion_timeout_active is True

    mgr.record_motion_detected()

    assert mgr.is_motion_timeout_active is False


@pytest.mark.asyncio
async def test_record_motion_detected_cancels_pending_timer(mock_hass, logger):
    """A pending no-motion timer is cancelled when motion returns."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)
    mgr.start_motion_timeout(AsyncMock())
    assert mgr.has_pending_timeout is True

    refreshed_needed = mgr.record_motion_detected()

    assert mgr.has_pending_timeout is False
    assert refreshed_needed is True  # caller should refresh — timer was in flight


# --- cancel_motion_timeout ---


@pytest.mark.asyncio
async def test_cancel_motion_timeout_clears_pending(mock_hass, logger):
    """cancel_motion_timeout idempotently clears any pending timer."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)
    mgr.start_motion_timeout(AsyncMock())
    assert mgr.has_pending_timeout is True

    mgr.cancel_motion_timeout()

    assert mgr.has_pending_timeout is False


def test_cancel_motion_timeout_when_idle_is_noop(mgr):
    """Cancel is safe to call when no timer is pending."""
    mgr.cancel_motion_timeout()  # must not raise
    assert mgr.has_pending_timeout is False


# --- timeout expiry body ---


@pytest.mark.asyncio
async def test_timeout_expiry_sets_active_and_refreshes_when_motion_absent(
    mock_hass, logger
):
    """When the timer expires with motion still absent, fallback state activates."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)

    original_prop = MotionManager.is_motion_detected
    MotionManager.is_motion_detected = property(lambda self: False)
    try:
        callback = AsyncMock()
        # Drive the on-expire body directly with a known elapsed value; the
        # body is the unit under test, not the asyncio plumbing (covered by
        # TimeoutController tests).
        await mgr._on_motion_timeout_expired(0, callback)

        assert mgr.is_motion_timeout_active is True
        callback.assert_called_once()
    finally:
        MotionManager.is_motion_detected = original_prop


@pytest.mark.asyncio
async def test_timeout_expiry_skips_when_motion_returned(mock_hass, logger):
    """If motion returned during the sleep, fallback must not activate."""
    mgr = MotionManager(hass=mock_hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)

    original_prop = MotionManager.is_motion_detected
    MotionManager.is_motion_detected = property(lambda self: True)
    try:
        callback = AsyncMock()
        await mgr._on_motion_timeout_expired(0, callback)

        assert mgr.is_motion_timeout_active is False
        callback.assert_not_called()
    finally:
        MotionManager.is_motion_detected = original_prop
