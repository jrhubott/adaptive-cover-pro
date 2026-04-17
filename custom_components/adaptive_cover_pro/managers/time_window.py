"""Time window management for Adaptive Cover Pro."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..config_context_adapter import ConfigContextAdapter

from ..helpers import get_datetime_from_str, get_safe_state


class TimeWindowManager:
    """Manages operational time window checks.

    Determines whether the current time falls within the configured
    start/end time window for automatic cover control.
    """

    def __init__(self, hass: HomeAssistant, logger: ConfigContextAdapter) -> None:
        """Initialize time window manager.

        Args:
            hass: Home Assistant instance
            logger: Context-aware logger

        """
        self._hass = hass
        self.logger = logger
        self._last_time_window_state: bool | None = None

        # Config values — set via update_config()
        self._start_time: str | None = None
        self._start_time_entity: str | None = None
        self._end_time_config: str | None = None
        self._end_time_entity: str | None = None

        # Cached start time from last evaluation (for diagnostics)
        self._cached_start_time: dt.datetime | None = None

    def update_config(
        self,
        start_time: str | None,
        start_time_entity: str | None,
        end_time: str | None,
        end_time_entity: str | None,
    ) -> None:
        """Update configuration values.

        Args:
            start_time: Static start time string
            start_time_entity: Entity ID providing start time
            end_time: Static end time string
            end_time_entity: Entity ID providing end time

        """
        self._start_time = start_time
        self._start_time_entity = start_time_entity
        self._end_time_config = end_time
        self._end_time_entity = end_time_entity

    @property
    def is_active(self) -> bool:
        """Check if current time is within operational window.

        Returns:
            True if current time is after start time and before end time,
            False otherwise. Returns True if no time restrictions configured.

        """
        if (
            self._cached_start_time
            and self.end_time
            and self._cached_start_time > self.end_time
        ):
            self.logger.error("Start time is after end time")
        return self.before_end_time and self.after_start_time

    def _normalize_to_today(self, time: dt.datetime) -> dt.datetime:
        """Normalize a future-dated entity time to today's date.

        Sun entity sensors (e.g., sensor.sun_next_rising) roll forward to
        tomorrow's datetime once the event passes. This method pins such times
        back to today so time window comparisons work correctly for the
        remainder of the current day.

        Args:
            time: Parsed datetime from an entity sensor.

        Returns:
            The datetime with today's date if the original was a future date,
            otherwise unchanged.

        """
        today = dt.date.today()
        if time.date() > today:
            return time.replace(year=today.year, month=today.month, day=today.day)
        return time

    @property
    def after_start_time(self) -> bool:
        """Check if current time is after start time.

        Returns:
            True if current time is after configured start time (from entity
            or static config), False otherwise. Returns True if no start time
            configured.

        """
        now = dt.datetime.now()
        if self._start_time_entity is not None:
            time = get_datetime_from_str(
                get_safe_state(self._hass, self._start_time_entity)
            )
            if time is None:
                self.logger.debug(
                    "Start time entity %s returned None, treating as start passed",
                    self._start_time_entity,
                )
                return True
            time = self._normalize_to_today(time)
            self.logger.debug(
                "Start time: %s, now: %s, now >= time: %s ", time, now, now >= time
            )
            self._cached_start_time = time
            return now >= time
        if self._start_time is not None:
            time = get_datetime_from_str(self._start_time)
            if time is None:
                self.logger.debug(
                    "Start time config value could not be parsed, treating as start passed"
                )
                return True
            self.logger.debug(
                "Start time: %s, now: %s, now >= time: %s", time, now, now >= time
            )
            self._cached_start_time = time
            return now >= time
        return True

    @property
    def end_time(self) -> dt.datetime | None:
        """Get end time from entity or config.

        Returns:
            End time datetime object from end_time_entity state or end_time
            config value. Handles midnight (00:00) by adding one day. Returns
            None if no end time configured.

        """
        time = None
        if self._end_time_entity is not None:
            time = get_datetime_from_str(
                get_safe_state(self._hass, self._end_time_entity)
            )
            if time is not None:
                time = self._normalize_to_today(time)
        elif self._end_time_config is not None:
            time = get_datetime_from_str(self._end_time_config)
            if time is not None and time.time() == dt.time(0, 0):
                time = time + dt.timedelta(days=1)
        return time

    @property
    def before_end_time(self) -> bool:
        """Check if current time is before end time.

        Returns:
            True if current time is before configured end time (from entity
            or static config), False otherwise. Returns True if no end time
            configured.

        """
        end = self.end_time
        if end is not None:
            now = dt.datetime.now()
            self.logger.debug(
                "End time: %s, now: %s, now < time: %s",
                end,
                now,
                now < end,
            )
            return now < end
        return True

    @property
    def start_time_value(self) -> dt.datetime | None:
        """Get cached start time from last evaluation (for diagnostics)."""
        return self._cached_start_time

    async def check_transition(
        self,
        track_end_time: bool,
        refresh_callback,
        on_window_open=None,
    ) -> None:
        """Check if time window state has changed and trigger refresh if needed.

        Detects when the operational time window changes state
        (e.g., when end time is reached) and triggers appropriate actions.
        Provides <1 minute response time for time window changes.

        Args:
            track_end_time: Whether to track end time transitions
            refresh_callback: Async callback invoked when window closes
            on_window_open: Optional async callback invoked when window opens
                (inactive→active), so covers reposition at the start of the day

        """
        # Initialize tracking on first call
        if self._last_time_window_state is None:
            self._last_time_window_state = self.is_active
            return

        current_state = self.is_active

        # If state changed, trigger appropriate action
        if current_state != self._last_time_window_state:
            self.logger.info(
                "Time window state changed: %s → %s",
                "active" if self._last_time_window_state else "inactive",
                "active" if current_state else "inactive",
            )
            self._last_time_window_state = current_state

            if current_state and on_window_open is not None:
                self.logger.info("Time window opened, repositioning covers")
                await on_window_open()
            elif not current_state and track_end_time:
                self.logger.info(
                    "End time reached, returning covers to default position"
                )
                await refresh_callback()
