"""Weather condition override management for Adaptive Cover Pro."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..const import (
    DEFAULT_TEMPLATE_COMBINE_MODE,
    DEFAULT_WEATHER_ENABLED,
    DEFAULT_WEATHER_RAIN_THRESHOLD,
    DEFAULT_WEATHER_TIMEOUT,
    DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE,
    DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
    DEFAULT_WINDOW_AZIMUTH,
    DEGREES_IN_CIRCLE,
)
from ..templates import fold_condition_template, is_template_string
from .common import EventRecorder, TimeoutController

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# Condition label constants — used in active_conditions list
_COND_WIND_SPEED = "wind_speed"
_COND_RAIN_RATE = "rain_rate"
_COND_IS_RAINING = "is_raining"
_COND_IS_WINDY = "is_windy"
_COND_SEVERE = "severe_weather"


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
        self._events = EventRecorder(event_buffer)

        # Config (updated via update_config)
        self._wind_speed_sensor: str | None = None
        self._wind_direction_sensor: str | None = None
        self._wind_speed_threshold: float = DEFAULT_WEATHER_WIND_SPEED_THRESHOLD
        self._wind_direction_tolerance: int = DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE
        self._win_azi: int = DEFAULT_WINDOW_AZIMUTH
        self._rain_sensor: str | None = None
        self._rain_threshold: float = DEFAULT_WEATHER_RAIN_THRESHOLD
        self._is_raining_sensor: str | None = None
        self._is_windy_sensor: str | None = None
        # Optional condition templates + combine modes (issue #639). A
        # template-only override (no companion binary sensor) engages and reacts
        # the instant the template flips — see __init__.py template tracking.
        self._is_raining_template: str | None = None
        self._is_raining_template_mode: str = DEFAULT_TEMPLATE_COMBINE_MODE
        self._is_windy_template: str | None = None
        self._is_windy_template_mode: str = DEFAULT_TEMPLATE_COMBINE_MODE
        self._severe_sensors: list[str] = []
        self._severe_template: str | None = None
        self._severe_template_mode: str = DEFAULT_TEMPLATE_COMBINE_MODE
        self._timeout_seconds: int = DEFAULT_WEATHER_TIMEOUT
        # Master on/off toggle for the whole feature (issue #719). Pre-config
        # state mirrors the new-cover default; the coordinator always supplies
        # the resolved value via update_config.
        self._enabled: bool = DEFAULT_WEATHER_ENABLED

        # Runtime state
        self._timer = TimeoutController(logger, label="weather clear-delay")
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
        is_raining_template: str | None = None,
        is_raining_template_mode: str = DEFAULT_TEMPLATE_COMBINE_MODE,
        is_windy_template: str | None = None,
        is_windy_template_mode: str = DEFAULT_TEMPLATE_COMBINE_MODE,
        severe_template: str | None = None,
        severe_template_mode: str = DEFAULT_TEMPLATE_COMBINE_MODE,
        enabled: bool = True,
    ) -> None:
        """Update all weather override configuration.

        Called from coordinator._update_config_values whenever options change.

        ``enabled`` is the master on/off toggle (issue #719). It defaults to True
        for back-compat: a caller that predates the toggle gets the historical
        always-on behaviour. The new-cover default-OFF semantics live at the
        config layer (``DEFAULT_WEATHER_ENABLED``); the coordinator always passes
        the resolved value here.
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
        self._is_raining_template = is_raining_template
        self._is_raining_template_mode = is_raining_template_mode
        self._is_windy_template = is_windy_template
        self._is_windy_template_mode = is_windy_template_mode
        self._severe_template = severe_template
        self._severe_template_mode = severe_template_mode
        self._severe_sensors = list(severe_sensors)
        self._timeout_seconds = timeout_seconds
        self._enabled = enabled

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
    def condition_templates(self) -> list[str]:
        """Return the configured is-raining / is-windy / severe condition templates.

        Used by __init__.py to register ``async_track_template_result`` so a
        template-only override reacts the instant the template flips (#639/#974).
        """
        return [
            tmpl
            for tmpl in (
                self._is_raining_template,
                self._is_windy_template,
                self._severe_template,
            )
            if is_template_string(tmpl)
        ]

    @property
    def is_feature_configured(self) -> bool:
        """Whether the weather override is enabled AND has any source.

        The single chokepoint feature gate for
        :pyattr:`is_weather_override_active`, ``reconcile``, the priority-90
        override handler, and the min-mode floor. The master toggle
        (``enabled``, issue #719) gates all of them at once; a template-only
        config (no companion binary sensor) still counts as a source (#639).
        """
        return self._enabled and (
            bool(self.configured_sensors) or bool(self.condition_templates)
        )

    @property
    def is_any_condition_active(self) -> bool:
        """Check whether any configured weather condition is currently active.

        Reads live sensor states and renders condition templates from HA. OR
        logic: any single condition being true returns True. Unconfigured
        conditions are ignored. Unavailable/unknown sensors and silent/broken
        templates are treated as inactive (fail-open: don't retract on failure).
        """
        return (
            self._is_wind_active()
            or self._is_rain_active()
            or self._is_raining_active()
            or self._is_windy_active()
            or self._is_any_severe_active()
        )

    @property
    def is_weather_override_active(self) -> bool:
        """Return True when override is active (conditions met or in clear-delay timeout).

        Returns False when no source (sensor or template) is configured.
        """
        if not self.is_feature_configured:
            return False
        return self._override_active

    @property
    def is_timeout_running(self) -> bool:
        """Return True when a clear-delay timeout task is pending."""
        return self._timer.is_running

    @property
    def in_clear_delay(self) -> bool:
        """Return True when override is held active by the clear-delay timer."""
        return self.is_timeout_running

    @property
    def active_conditions(self) -> list[str]:
        """Return labels of currently active weather conditions."""
        result = []
        if self._is_wind_active():
            result.append(_COND_WIND_SPEED)
        if self._is_rain_active():
            result.append(_COND_RAIN_RATE)
        if self._is_raining_active():
            result.append(_COND_IS_RAINING)
        if self._is_windy_active():
            result.append(_COND_IS_WINDY)
        if self._is_any_severe_active():
            result.append(_COND_SEVERE)
        return result

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
                diff = abs(direction - self._win_azi) % DEGREES_IN_CIRCLE
                angular_dist = min(diff, DEGREES_IN_CIRCLE - diff)
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

    def _is_binary_condition_active(
        self, entity_id: str | None, template: str | None, mode: str
    ) -> bool:
        """Fold a binary sensor with its optional condition template (#639).

        Reuses the shared :func:`fold_condition_template`: the sensor's on/off
        and the rendered template combine per ``mode`` (OR default). A silent /
        broken template plus an off/absent sensor → inactive (fail-open).
        """
        result = fold_condition_template(
            self._hass,
            template,
            mode,
            others_truthy=self._is_binary_on(entity_id),
            has_others=bool(entity_id),
        )
        return bool(result)

    def _is_raining_active(self) -> bool:
        """Whether the is-raining sensor and/or template reports rain (#639)."""
        return self._is_binary_condition_active(
            self._is_raining_sensor,
            self._is_raining_template,
            self._is_raining_template_mode,
        )

    def _is_windy_active(self) -> bool:
        """Whether the is-windy sensor and/or template reports wind (#639)."""
        return self._is_binary_condition_active(
            self._is_windy_sensor,
            self._is_windy_template,
            self._is_windy_template_mode,
        )

    def _is_any_severe_active(self) -> bool:
        """Whether any severe sensor and/or the severe template reports severe (#974).

        Folds the severe sensor *list* with its optional condition template via
        the shared :func:`fold_condition_template`: ``others_truthy`` is any
        sensor being on, ``has_others`` gates it so a template-only config
        (no sensors) is decided by the template alone. A broken/empty template
        gives no opinion and the result reduces to the sensor list (fail-open).
        """
        result = fold_condition_template(
            self._hass,
            self._severe_template,
            self._severe_template_mode,
            others_truthy=any(
                self._is_binary_on(entity_id) for entity_id in self._severe_sensors
            ),
            has_others=bool(self._severe_sensors),
        )
        return bool(result)

    # --- State management ---

    def record_conditions_active(self) -> None:
        """Record that weather conditions are currently active.

        Cancels any running clear-delay timeout and sets override active.
        The caller is responsible for triggering a coordinator refresh.
        """
        self.cancel_weather_timeout()
        previous = self._override_active
        self._override_active = True
        if not previous:
            self._events.record(
                "weather_override_changed",
                entity_id="",
                previous=False,
                current=True,
            )

    def reconcile(self) -> str | None:
        """Self-healing check against live sensor state.

        Called every coordinator update tick. Returns "should_start_timeout"
        when the override flag is stuck True but conditions have cleared and
        no clear-delay timer is running. Returns None when no action is needed.
        The caller owns timer creation because that requires a refresh callback
        the manager intentionally doesn't hold.
        """
        if not self.is_feature_configured:
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
        timeout_seconds = self._timeout_seconds
        self._logger.info(
            "Weather conditions cleared — starting %s second delay before resuming normal control",
            timeout_seconds,
        )

        async def _on_expire() -> None:
            await self._on_weather_timeout_expired(timeout_seconds, refresh_callback)

        self._timer.start(timeout_seconds, _on_expire)

    async def _on_weather_timeout_expired(
        self, timeout_seconds: int, refresh_callback: Callable
    ) -> None:
        """Body that runs after the clear-delay sleep completes.

        Re-checks whether weather conditions have returned during the
        sleep — if so, the override is kept active and the refresh
        callback is suppressed.
        """
        if self.is_any_condition_active:
            self._logger.debug(
                "Weather conditions returned during clear-delay — keeping override active"
            )
            self._override_active = True
            return

        self._override_active = False
        self._events.record(
            "weather_override_changed",
            entity_id="",
            previous=True,
            current=False,
            reason=f"clear-delay expired ({timeout_seconds}s)",
        )
        self._logger.info(
            "Weather clear-delay expired (%s seconds) — resuming normal control",
            timeout_seconds,
        )

        await refresh_callback()

    def cancel_weather_timeout(self) -> None:
        """Cancel the running clear-delay timeout task, if any."""
        self._timer.cancel()
