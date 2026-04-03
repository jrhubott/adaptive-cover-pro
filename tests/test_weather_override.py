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
