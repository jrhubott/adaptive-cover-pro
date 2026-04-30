"""Motion sensor timeout management for Adaptive Cover Pro."""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import TYPE_CHECKING
from collections.abc import Callable

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class MotionManager:
    """Manage motion sensor state and timeout tracking for cover control.

    Tracks occupancy from one or more binary motion/occupancy sensors and
    manages a debounce timeout so covers return to a default position only
    after sustained no-motion rather than on brief sensor off-flickers.

    Behavior:
    - Motion detected (any sensor on)  → immediate response, cancel timeout
    - Motion stopped (all sensors off) → start timeout; set active after expiry
    - No sensors configured            → feature disabled (always-present assumed)

    """

    def __init__(self, hass: HomeAssistant, logger, *, event_buffer=None) -> None:
        """Initialize the MotionManager.

        Args:
            hass: Home Assistant instance used to read sensor states
            logger: Logger instance for debug/info output
            event_buffer: Shared diagnostic ring buffer (optional, reserved for future events).

        """
        self._hass = hass
        self._logger = logger
        self._event_buffer = event_buffer

        self._sensors: list[str] = []
        self._timeout_seconds: int = 300

        self._motion_timeout_task: asyncio.Task | None = None
        self._last_motion_time: float | None = None
        self._motion_timeout_active: bool = False

    # --- Configuration ---

    def update_config(self, sensors: list[str], timeout_seconds: int) -> None:
        """Update sensor list and timeout duration.

        Called whenever config options change so the manager stays in sync
        without recreating it.

        Args:
            sensors: Entity IDs of binary motion/occupancy sensors to track
            timeout_seconds: Seconds to wait after last motion before setting active

        """
        self._sensors = list(sensors)
        self._timeout_seconds = timeout_seconds

    # --- Properties ---

    @property
    def is_motion_detected(self) -> bool:
        """Check whether any configured motion sensor currently detects motion.

        Returns:
            True if no sensors configured (feature disabled → assume presence),
            or if any sensor reports state "on".

        """
        if not self._sensors:
            return True  # Feature disabled — assume presence

        for sensor_id in self._sensors:
            state = self._hass.states.get(sensor_id)
            if state and state.state == "on":
                return True

        return False

    @property
    def is_motion_timeout_active(self) -> bool:
        """Check whether the no-motion timeout has expired.

        Returns:
            False when no sensors configured (feature disabled).
            True only after a completed timeout cycle.

        """
        if not self._sensors:
            return False  # Feature disabled

        return self._motion_timeout_active

    @property
    def last_motion_time(self) -> float | None:
        """Return UNIX timestamp of the most recent motion detection, or None."""
        return self._last_motion_time

    # --- Motion event handling ---

    def set_no_motion(self) -> None:
        """Immediately activate the no-motion state without waiting for a timeout.

        Used at startup when all sensors are already off so the feature does not
        stay stuck in ``waiting_for_data`` until a sensor first turns on then off.
        """
        self.cancel_motion_timeout()
        self._motion_timeout_active = True

    def record_motion_detected(self) -> bool:
        """Record that motion was detected right now.

        Updates last_motion_time, cancels any running timeout task, and
        clears the active flag so covers resume automatic sun positioning.

        Returns:
            True if a timeout was active (expired) or pending (task still
            running), indicating the coordinator should call async_refresh
            to resume automatic sun positioning.  False when motion was
            already the current state (no refresh needed).

        """
        had_pending = (
            self._motion_timeout_task is not None
            and not self._motion_timeout_task.done()
        )
        had_active = self._motion_timeout_active
        self.cancel_motion_timeout()
        self._last_motion_time = dt.datetime.now().timestamp()
        self._motion_timeout_active = False
        return had_active or had_pending

    # --- Timeout management ---

    def start_motion_timeout(self, refresh_callback: Callable) -> None:
        """Start the no-motion timeout task.

        Cancels any existing timeout before creating a new one so only one
        timer runs at a time.

        Args:
            refresh_callback: Async callable invoked when timeout expires and
                covers should switch to the default position.  Typically
                ``coordinator.async_refresh``.

        """
        self.cancel_motion_timeout()

        self._logger.info(
            "No motion detected - starting %s second timeout before using default position",
            self._timeout_seconds,
        )
        if self._event_buffer is not None:
            self._event_buffer.record(
                {
                    "ts": dt.datetime.now(dt.UTC).isoformat(),
                    "event": "motion_timeout_started",
                    "timeout_seconds": self._timeout_seconds,
                }
            )

        task = asyncio.create_task(
            self._motion_timeout_handler(self._timeout_seconds, refresh_callback)
        )
        self._motion_timeout_task = task

    async def _motion_timeout_handler(
        self, timeout_seconds: int, refresh_callback: Callable
    ) -> None:
        """Handle timeout expiration after no motion is detected.

        Args:
            timeout_seconds: How long to wait before activating default position
            refresh_callback: Called after timeout expires if motion is still absent

        """
        try:
            await asyncio.sleep(timeout_seconds)
        except asyncio.CancelledError:
            return

        # Double-check: motion may have re-appeared during the sleep
        if self.is_motion_detected:
            self._logger.debug(
                "Motion detected during timeout - canceling default position"
            )
            if self._event_buffer is not None:
                self._event_buffer.record(
                    {
                        "ts": dt.datetime.now(dt.UTC).isoformat(),
                        "event": "motion_detected_during_timeout",
                    }
                )
            return

        self._motion_timeout_active = True
        self._logger.info(
            "Motion timeout expired (%s seconds) - using default position",
            timeout_seconds,
        )
        if self._event_buffer is not None:
            self._event_buffer.record(
                {
                    "ts": dt.datetime.now(dt.UTC).isoformat(),
                    "event": "motion_timeout_expired",
                    "timeout_seconds": timeout_seconds,
                }
            )

        await refresh_callback()

    def cancel_motion_timeout(self) -> None:
        """Cancel the running timeout task, if any."""
        if self._motion_timeout_task and not self._motion_timeout_task.done():
            self._logger.debug("Motion timeout canceled")
            self._motion_timeout_task.cancel()
            if self._event_buffer is not None:
                self._event_buffer.record(
                    {
                        "ts": dt.datetime.now(dt.UTC).isoformat(),
                        "event": "motion_timeout_canceled",
                    }
                )
        self._motion_timeout_task = None
