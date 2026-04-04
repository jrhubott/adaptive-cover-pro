"""Grace period management for Adaptive Cover Pro."""

from __future__ import annotations

import asyncio
import datetime as dt


class GracePeriodManager:
    """Manage command and startup grace periods for cover control.

    Tracks per-entity command grace periods to prevent false manual override
    detection after the integration sends a command. Also manages a global
    startup grace period to suppress override detection during HA restart.

    """

    def __init__(
        self,
        logger,
        command_grace_seconds: float = 5.0,
        startup_grace_seconds: float = 30.0,
    ) -> None:
        """Initialize the GracePeriodManager.

        Args:
            logger: Logger instance for debug output
            command_grace_seconds: Duration of per-command grace period
            startup_grace_seconds: Duration of startup grace period

        """
        self._logger = logger
        self._command_grace_seconds = command_grace_seconds
        self._startup_grace_seconds = startup_grace_seconds

        self._command_timestamps: dict[str, float] = {}
        self._grace_period_tasks: dict[str, asyncio.Task] = {}
        self._startup_timestamp: float | None = None
        self._startup_grace_period_task: asyncio.Task | None = None

    # --- Command grace period ---

    def is_in_command_grace_period(self, entity_id: str) -> bool:
        """Check if entity is in command grace period.

        Args:
            entity_id: Entity to check

        Returns:
            True if in grace period, False otherwise

        """
        timestamp = self._command_timestamps.get(entity_id)
        if timestamp is None:
            return False

        elapsed = dt.datetime.now().timestamp() - timestamp
        return elapsed < self._command_grace_seconds

    def start_command_grace_period(self, entity_id: str) -> None:
        """Start grace period for entity.

        Cancels any existing grace period, records timestamp, and schedules
        automatic expiration.

        Args:
            entity_id: Entity to start grace period for

        """
        self.cancel_command_grace_period(entity_id)

        self._command_timestamps[entity_id] = dt.datetime.now().timestamp()

        task = asyncio.create_task(self._command_grace_period_timeout(entity_id))
        self._grace_period_tasks[entity_id] = task

        self._logger.debug(
            "Started %s second grace period for %s",
            self._command_grace_seconds,
            entity_id,
        )

    async def _command_grace_period_timeout(self, entity_id: str) -> None:
        """Clear command grace period after timeout.

        Args:
            entity_id: Entity whose grace period expired

        """
        try:
            await asyncio.sleep(self._command_grace_seconds)
        except asyncio.CancelledError:
            return

        self._command_timestamps.pop(entity_id, None)
        self._grace_period_tasks.pop(entity_id, None)

        self._logger.debug("Grace period expired for %s", entity_id)

    def cancel_command_grace_period(self, entity_id: str) -> None:
        """Cancel grace period task for entity.

        Args:
            entity_id: Entity whose grace period to cancel

        """
        task = self._grace_period_tasks.get(entity_id)
        if task and not task.done():
            task.cancel()

        self._grace_period_tasks.pop(entity_id, None)
        self._command_timestamps.pop(entity_id, None)

    # --- Startup grace period ---

    def is_in_startup_grace_period(self) -> bool:
        """Check if integration is in startup grace period.

        Returns:
            True if in startup grace period, False otherwise

        """
        if self._startup_timestamp is None:
            return False

        elapsed = dt.datetime.now().timestamp() - self._startup_timestamp
        return elapsed < self._startup_grace_seconds

    def start_startup_grace_period(self) -> None:
        """Start startup grace period after first refresh.

        Sets timestamp and schedules automatic clearing after grace period.
        Prevents manual override detection during HA restart when covers may
        respond slowly due to system initialization.

        """
        if (
            self._startup_grace_period_task
            and not self._startup_grace_period_task.done()
        ):
            self._startup_grace_period_task.cancel()

        self._startup_timestamp = dt.datetime.now().timestamp()

        self._startup_grace_period_task = asyncio.create_task(
            self._startup_grace_period_timeout()
        )

        self._logger.info(
            "Started %s second startup grace period (manual override detection disabled)",
            self._startup_grace_seconds,
        )

    async def _startup_grace_period_timeout(self) -> None:
        """Clear startup grace period after timeout."""
        try:
            await asyncio.sleep(self._startup_grace_seconds)
        except asyncio.CancelledError:
            return

        self._startup_timestamp = None
        self._startup_grace_period_task = None

        self._logger.debug("Startup grace period expired")

    # --- Cleanup ---

    def cancel_all(self) -> None:
        """Cancel all command and startup grace period tasks.

        Called during coordinator shutdown to clean up lingering tasks.

        """
        for entity_id in list(self._grace_period_tasks.keys()):
            self.cancel_command_grace_period(entity_id)

        if (
            self._startup_grace_period_task
            and not self._startup_grace_period_task.done()
        ):
            self._startup_grace_period_task.cancel()
            self._startup_grace_period_task = None
            self._startup_timestamp = None
