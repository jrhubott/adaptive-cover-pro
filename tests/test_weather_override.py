"""Tests for weather-based override feature (coordinator integration)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.managers.weather import WeatherManager


def _make_coordinator_with_weather_mgr(
    *,
    wind_speed_sensor=None,
    wind_speed_threshold=50.0,
    timeout_seconds=300,
):
    """Create a MagicMock coordinator with a real WeatherManager pre-configured."""
    hass = MagicMock()
    logger = MagicMock()

    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.logger = logger

    mgr = WeatherManager(hass=hass, logger=logger)
    mgr.update_config(
        wind_speed_sensor=wind_speed_sensor,
        wind_direction_sensor=None,
        wind_speed_threshold=wind_speed_threshold,
        wind_direction_tolerance=45,
        win_azi=180,
        rain_sensor=None,
        rain_threshold=1.0,
        is_raining_sensor=None,
        is_windy_sensor=None,
        severe_sensors=[],
        timeout_seconds=timeout_seconds,
    )
    coordinator._weather_mgr = mgr

    return coordinator, hass


# --- is_weather_override_active property ---


def test_is_weather_override_active_no_sensors():
    """Returns False when no weather sensors configured."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_weather_mgr()
    result = AdaptiveDataUpdateCoordinator.is_weather_override_active.fget(coordinator)
    assert result is False


def test_is_weather_override_active_delegates_to_manager():
    """is_weather_override_active delegates to WeatherManager."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_weather_mgr(wind_speed_sensor="sensor.wind")
    coordinator._weather_mgr._override_active = True

    result = AdaptiveDataUpdateCoordinator.is_weather_override_active.fget(coordinator)
    assert result is True


def test_is_weather_override_active_false_when_flag_not_set():
    """Returns False when sensors configured but no conditions active."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_weather_mgr(wind_speed_sensor="sensor.wind")
    coordinator._weather_mgr._override_active = False

    result = AdaptiveDataUpdateCoordinator.is_weather_override_active.fget(coordinator)
    assert result is False


# --- async_check_weather_state_change ---


@pytest.mark.asyncio
async def test_weather_state_change_activates_on_condition_met():
    """State change handler activates override and refreshes when conditions met."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind", wind_speed_threshold=50.0
    )

    # Wind speed above threshold
    hass.states.get.return_value = MagicMock(state="75.0")

    coordinator.async_refresh = AsyncMock()
    coordinator.state_change = False
    coordinator._start_weather_timeout = MagicMock()

    event = MagicMock()
    event.data = {
        "entity_id": "sensor.wind",
        "new_state": MagicMock(state="75.0"),
    }

    await AdaptiveDataUpdateCoordinator.async_check_weather_state_change(
        coordinator, event
    )

    assert coordinator._weather_mgr._override_active is True
    assert coordinator.state_change is True
    coordinator.async_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_weather_state_change_starts_timeout_when_cleared():
    """State change handler starts clear-delay timeout when all conditions clear."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind", wind_speed_threshold=50.0
    )

    # Wind speed dropped below threshold — conditions cleared
    hass.states.get.return_value = MagicMock(state="10.0")
    coordinator._weather_mgr._override_active = True  # Was active

    coordinator.async_refresh = AsyncMock()
    coordinator.state_change = False
    coordinator._start_weather_timeout = MagicMock()
    # Bind real reconcile so _start_weather_timeout is called transitively
    coordinator._reconcile_weather_override = lambda: AdaptiveDataUpdateCoordinator._reconcile_weather_override(coordinator)

    event = MagicMock()
    event.data = {
        "entity_id": "sensor.wind",
        "new_state": MagicMock(state="10.0"),
    }

    await AdaptiveDataUpdateCoordinator.async_check_weather_state_change(
        coordinator, event
    )

    coordinator._start_weather_timeout.assert_called_once()


@pytest.mark.asyncio
async def test_weather_state_change_ignores_none_new_state():
    """State change handler ignores events with no new_state (entity removed)."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_weather_mgr(wind_speed_sensor="sensor.wind")
    coordinator.async_refresh = AsyncMock()
    coordinator._start_weather_timeout = MagicMock()

    event = MagicMock()
    event.data = {
        "entity_id": "sensor.wind",
        "new_state": None,
    }

    await AdaptiveDataUpdateCoordinator.async_check_weather_state_change(
        coordinator, event
    )

    coordinator.async_refresh.assert_not_called()
    coordinator._start_weather_timeout.assert_not_called()


# --- _reconcile_weather_override ---


def test_reconcile_weather_override_starts_timer_when_flag_stuck(hass=None):
    """G1: override active, conditions clear, no timer → starts timeout."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind"
    )
    hass.states.get.return_value = MagicMock(state="10.0")  # below threshold
    coordinator._weather_mgr._override_active = True
    coordinator._start_weather_timeout = MagicMock()

    AdaptiveDataUpdateCoordinator._reconcile_weather_override(coordinator)

    coordinator._start_weather_timeout.assert_called_once()


def test_reconcile_weather_override_noop_when_conditions_active():
    """G2: override active, conditions still active → no timer started."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind"
    )
    hass.states.get.return_value = MagicMock(state="75.0")  # above threshold
    coordinator._weather_mgr._override_active = True
    coordinator._start_weather_timeout = MagicMock()

    AdaptiveDataUpdateCoordinator._reconcile_weather_override(coordinator)

    coordinator._start_weather_timeout.assert_not_called()


def test_reconcile_weather_override_noop_when_flag_false():
    """G3: override flag False, conditions clear → no timer started."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind"
    )
    hass.states.get.return_value = MagicMock(state="10.0")
    coordinator._weather_mgr._override_active = False
    coordinator._start_weather_timeout = MagicMock()

    AdaptiveDataUpdateCoordinator._reconcile_weather_override(coordinator)

    coordinator._start_weather_timeout.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_weather_override_noop_when_timer_running():
    """G4: override active, conditions clear, timer already running → no second timer."""
    import asyncio

    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind"
    )
    hass.states.get.return_value = MagicMock(state="10.0")
    coordinator._weather_mgr._override_active = True
    coordinator._start_weather_timeout = MagicMock()

    async def _long_sleep():
        await asyncio.sleep(9999)

    task = asyncio.create_task(_long_sleep())
    coordinator._weather_mgr._timeout_task = task
    try:
        AdaptiveDataUpdateCoordinator._reconcile_weather_override(coordinator)
        coordinator._start_weather_timeout.assert_not_called()
    finally:
        task.cancel()


# --- _recover_weather_override_on_restart ---


def test_recover_on_restart_sets_flag_when_conditions_active():
    """G5: on first refresh, conditions active → restores _override_active=True."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind"
    )
    hass.states.get.return_value = MagicMock(state="75.0")  # above threshold
    coordinator._weather_mgr._override_active = False  # simulates post-restart reset

    AdaptiveDataUpdateCoordinator._recover_weather_override_on_restart(coordinator)

    assert coordinator._weather_mgr._override_active is True


def test_recover_on_restart_noop_when_conditions_clear():
    """G6: on first refresh, conditions clear → flag stays False."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind"
    )
    hass.states.get.return_value = MagicMock(state="10.0")  # below threshold
    coordinator._weather_mgr._override_active = False

    AdaptiveDataUpdateCoordinator._recover_weather_override_on_restart(coordinator)

    assert coordinator._weather_mgr._override_active is False


def test_recover_on_restart_noop_when_no_sensors_configured():
    """No sensors configured → noop, no state reads."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr()  # no wind sensor
    coordinator._weather_mgr._override_active = False

    AdaptiveDataUpdateCoordinator._recover_weather_override_on_restart(coordinator)

    assert coordinator._weather_mgr._override_active is False
    hass.states.get.assert_not_called()


# --- end-to-end: restart race then conditions clear ---


@pytest.mark.asyncio
async def test_restart_race_then_conditions_clear_starts_timer():
    """Regression for #255: after restart recovery, clearing conditions starts timer."""
    from unittest.mock import MagicMock

    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind", wind_speed_threshold=50.0
    )
    # 1. Conditions active on startup (simulates HA restart with wind still up)
    hass.states.get.return_value = MagicMock(state="75.0")
    coordinator._weather_mgr._override_active = False

    AdaptiveDataUpdateCoordinator._recover_weather_override_on_restart(coordinator)
    assert coordinator._weather_mgr._override_active is True  # recovery worked

    # 2. Wind drops; state-change event fires
    hass.states.get.return_value = MagicMock(state="10.0")
    coordinator._start_weather_timeout = MagicMock()
    # Bind real reconcile so _start_weather_timeout is called transitively
    coordinator._reconcile_weather_override = lambda: AdaptiveDataUpdateCoordinator._reconcile_weather_override(coordinator)


    from homeassistant.core import Event

    event = MagicMock(spec=Event)
    event.data = {"entity_id": "sensor.wind", "new_state": MagicMock(state="10.0")}

    await AdaptiveDataUpdateCoordinator.async_check_weather_state_change(
        coordinator, event
    )

    # Without the restart recovery fix, _override_active would be False here
    # and the else-branch would short-circuit without starting the timer.
    coordinator._start_weather_timeout.assert_called_once()


@pytest.mark.asyncio
async def test_weather_state_change_cleared_does_not_restart_running_timer():
    """Regression: a cleared event does not restart a timer that's already running."""
    import asyncio

    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from homeassistant.core import Event

    coordinator, hass = _make_coordinator_with_weather_mgr(
        wind_speed_sensor="sensor.wind"
    )
    hass.states.get.return_value = MagicMock(state="10.0")  # conditions clear
    coordinator._weather_mgr._override_active = True
    coordinator._start_weather_timeout = MagicMock()
    coordinator.async_refresh = AsyncMock()
    coordinator.state_change = False

    async def _long_sleep():
        await asyncio.sleep(9999)

    task = asyncio.create_task(_long_sleep())
    coordinator._weather_mgr._timeout_task = task
    try:
        event = MagicMock(spec=Event)
        event.data = {
            "entity_id": "sensor.wind",
            "new_state": MagicMock(state="10.0"),
        }
        await AdaptiveDataUpdateCoordinator.async_check_weather_state_change(
            coordinator, event
        )
        coordinator._start_weather_timeout.assert_not_called()
    finally:
        task.cancel()
