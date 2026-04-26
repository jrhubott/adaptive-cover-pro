"""Weather condition override management for Adaptive Cover Pro."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class WeatherManager:
    """Manage weather-based safety overrides for cover control.

    Evaluates multiple weather conditions (wind speed/direction, rain rate,
    binary weather sensors) and activates a safety override when any condition
    is met. A configurable clear-delay timeout prevents flapping when conditions
    are intermittent (e.g., gusty wind).

    Conditions (OR logic — any configured condition triggers the override):
    - Wind speed sensor >= threshold (optional: filtered by wind direction vs window)
    - Rain rate sensor >= threshold
    - IsRaining binary sensor "on"
    - IsWindy binary sensor "on"
    - Severe weather binary sensors (hail/frost/storm) — any "on"

    Behavior:
    - Any condition active → immediate override (covers retract to configured position)
    - All conditions clear → start clear-delay timeout; deactivate after timeout expires
    - No sensors configured → feature disabled

    Unavailable/unknown sensor states are treated as inactive (fail-open: do not
    retract covers on sensor failure).
    """

    def __init__(self, hass: HomeAssistant, logger, *, event_buffer=None) -> None:
        """Initialize the WeatherManager.

        Args:
            hass: Home Assistant instance used to read sensor states
            logger: Logger instance for debug/info output
            event_buffer: Shared diagnostic ring buffer (optional).

        """
        self._hass = hass
        self._logger = logger
        self._event_buffer = event_buffer

        # Config (updated via update_config)
        self._wind_speed_sensor: str | None = None
        self._wind_direction_sensor: str | None = None
        self._wind_speed_threshold: float = 50.0
        self._wind_direction_tolerance: int = 45
        self._win_azi: int = 180
        self._rain_sensor: str | None = None
        self._rain_threshold: float = 1.0
        self._is_raining_sensor: str | None = None
        self._is_windy_sensor: str | None = None
        self._severe_sensors: list[str] = []
        self._timeout_seconds: int = 300

        # Runtime state
        self._timeout_task: asyncio.Task | None = None
        self._override_active: bool = False

    # --- Configuration ---

    def update_config(
        self,
        *,
        wind_speed_sensor: str | None,
        wind_direction_sensor: str | None,
        wind_speed_threshold: float,
        wind_direction_tolerance: int,
        win_azi: int,
        rain_sensor: str | None,
        rain_threshold: float,
        is_raining_sensor: str | None,
        is_windy_sensor: str | None,
        severe_sensors: list[str],
        timeout_seconds: int,
    ) -> None:
        """Update all weather override configuration.

        Called from coordinator._update_config_values whenever options change.
        """
        self._wind_speed_sensor = wind_speed_sensor
        self._wind_direction_sensor = wind_direction_sensor
        self._wind_speed_threshold = wind_speed_threshold
        self._wind_direction_tolerance = wind_direction_tolerance
        self._win_azi = win_azi
        self._rain_sensor = rain_sensor
        self._rain_threshold = rain_threshold
        self._is_raining_sensor = is_raining_sensor
        self._is_windy_sensor = is_windy_sensor
        self._severe_sensors = list(severe_sensors)
        self._timeout_seconds = timeout_seconds

    # --- Properties ---

    @property
    def configured_sensors(self) -> list[str]:
        """Return list of all configured sensor entity IDs.

        Used by __init__.py to register state change listeners.
        """
        sensors: list[str] = []
        for entity_id in [
            self._wind_speed_sensor,
            self._wind_direction_sensor,
            self._rain_sensor,
            self._is_raining_sensor,
            self._is_windy_sensor,
        ]:
            if entity_id:
                sensors.append(entity_id)
        sensors.extend(self._severe_sensors)
        return sensors

    @property
    def is_any_condition_active(self) -> bool:
        """Check whether any configured weather condition is currently active.

        Reads live sensor states from HA. OR logic: any single condition
        being true returns True. Unconfigured conditions are ignored.
        Unavailable/unknown sensors are treated as inactive.
        """
        return (
            self._is_wind_active()
            or self._is_rain_active()
            or self._is_binary_on(self._is_raining_sensor)
            or self._is_binary_on(self._is_windy_sensor)
            or self._is_any_severe_active()
        )

    @property
    def is_weather_override_active(self) -> bool:
        """Return True when override is active (conditions met or in clear-delay timeout).

        Returns False when no sensors are configured (feature disabled).
        """
        if not self.configured_sensors:
            return False
        return self._override_active

    @property
    def is_timeout_running(self) -> bool:
        """Return True when a clear-delay timeout task is pending."""
        return self._timeout_task is not None and not self._timeout_task.done()

    # --- Condition evaluation helpers ---

    def _is_wind_active(self) -> bool:
        """Check whether wind speed exceeds threshold (with optional direction filter)."""
        if not self._wind_speed_sensor:
            return False
        state = self._hass.states.get(self._wind_speed_sensor)
        if not state or state.state in ("unavailable", "unknown"):
            return False
        try:
            speed = float(state.state)
        except (ValueError, TypeError):
            return False

        if speed < self._wind_speed_threshold:
            return False

        # Speed threshold exceeded — check direction if configured
        if self._wind_direction_sensor:
            dir_state = self._hass.states.get(self._wind_direction_sensor)
            if dir_state and dir_state.state not in ("unavailable", "unknown"):
                try:
                    direction = float(dir_state.state)
                except (ValueError, TypeError):
                    return True  # Can't parse direction — assume exposed
                # Angular distance between wind-from direction and window azimuth.
                # Wind FROM direction D hits a window facing azimuth A when D ≈ A.
                diff = abs(direction - self._win_azi) % 360
                angular_dist = min(diff, 360 - diff)
                if angular_dist > self._wind_direction_tolerance:
                    return False  # Wind not aimed at this window

        return True

    def _is_rain_active(self) -> bool:
        """Check whether rain rate exceeds threshold."""
        if not self._rain_sensor:
            return False
        state = self._hass.states.get(self._rain_sensor)
        if not state or state.state in ("unavailable", "unknown"):
            return False
        try:
            rate = float(state.state)
        except (ValueError, TypeError):
            return False
        return rate >= self._rain_threshold

    def _is_binary_on(self, entity_id: str | None) -> bool:
        """Check whether a binary sensor is 'on'."""
        if not entity_id:
            return False
        state = self._hass.states.get(entity_id)
        return bool(state and state.state == "on")

    def _is_any_severe_active(self) -> bool:
        """Check whether any severe weather binary sensor is 'on'."""
        return any(self._is_binary_on(entity_id) for entity_id in self._severe_sensors)

    # --- State management ---

    def record_conditions_active(self) -> None:
        """Record that weather conditions are currently active.

        Cancels any running clear-delay timeout and sets override active.
        The caller is responsible for triggering a coordinator refresh.
        """
        self.cancel_weather_timeout()
        previous = self._override_active
        self._override_active = True
        if not previous and self._event_buffer is not None:
            import datetime as dt
            self._event_buffer.record({
                "ts": dt.datetime.now(dt.UTC).isoformat(),
                "event": "weather_override_changed",
                "entity_id": "",
                "previous": False,
                "current": True,
            })

    def reconcile(self) -> str | None:
        """Self-healing check against live sensor state.

        Called every coordinator update tick. Returns "should_start_timeout"
        when the override flag is stuck True but conditions have cleared and
        no clear-delay timer is running. Returns None when no action is needed.
        The caller owns timer creation because that requires a refresh callback
        the manager intentionally doesn't hold.
        """
        if not self.configured_sensors:
            return None
        if not self._override_active:
            return None
        if self.is_any_condition_active:
            return None
        if self.is_timeout_running:
            return None
        return "should_start_timeout"

    # --- Timeout management ---

    def start_weather_timeout(self, refresh_callback: Callable) -> None:
        """Start the clear-delay timeout task.

        Called when all weather conditions have cleared. After the timeout
        expires (and conditions are still clear), the override deactivates.
        Cancels any existing timeout before creating a new one.

        Args:
            refresh_callback: Async callable invoked when timeout expires and
                normal control should resume.

        """
        self.cancel_weather_timeout()

        self._logger.info(
            "Weather conditions cleared — starting %s second delay before resuming normal control",
            self._timeout_seconds,
        )

        task = asyncio.create_task(
            self._weather_timeout_handler(self._timeout_seconds, refresh_callback)
        )
        self._timeout_task = task

    async def _weather_timeout_handler(
        self, timeout_seconds: int, refresh_callback: Callable
    ) -> None:
        """Handle timeout expiration after weather conditions clear.

        Args:
            timeout_seconds: How long to wait before deactivating the override
            refresh_callback: Called after timeout expires if conditions still clear

        """
        try:
            await asyncio.sleep(timeout_seconds)
        except asyncio.CancelledError:
            return

        # Double-check: conditions may have returned during the sleep
        if self.is_any_condition_active:
            self._logger.debug(
                "Weather conditions returned during clear-delay — keeping override active"
            )
            self._override_active = True
            return

        self._override_active = False
        if self._event_buffer is not None:
            import datetime as dt
            self._event_buffer.record({
                "ts": dt.datetime.now(dt.UTC).isoformat(),
                "event": "weather_override_changed",
                "entity_id": "",
                "previous": True,
                "current": False,
                "reason": f"clear-delay expired ({timeout_seconds}s)",
            })
        self._logger.info(
            "Weather clear-delay expired (%s seconds) — resuming normal control",
            timeout_seconds,
        )

        await refresh_callback()

    def cancel_weather_timeout(self) -> None:
        """Cancel the running clear-delay timeout task, if any."""
        if self._timeout_task and not self._timeout_task.done():
            self._logger.debug("Weather clear-delay timeout canceled")
            self._timeout_task.cancel()
        self._timeout_task = None
