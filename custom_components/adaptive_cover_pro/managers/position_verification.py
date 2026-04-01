"""Position verification management for Adaptive Cover Pro."""

from __future__ import annotations

import datetime as dt


class PositionVerificationManager:
    """Manage periodic position verification and retry logic for cover control.

    Tracks per-entity retry counts, last verification timestamps, and entities
    that have never received a command. Used by the coordinator to detect and
    correct position mismatches after cover commands are sent.

    """

    def __init__(
        self,
        logger,
        check_interval_minutes: int,
        position_tolerance: int,
        max_retries: int,
    ) -> None:
        """Initialize the PositionVerificationManager.

        Args:
            logger: Logger instance for debug output
            check_interval_minutes: How often to run position verification (minutes)
            position_tolerance: Allowed deviation between target and actual position (%)
            max_retries: Maximum number of repositioning attempts before giving up

        """
        self.logger = logger
        self.check_interval_minutes = check_interval_minutes
        self.position_tolerance = position_tolerance
        self.max_retries = max_retries

        self._retry_counts: dict[str, int] = {}
        self._last_verification: dict[str, dt.datetime] = {}
        self._never_commanded: set[str] = set()
        self._position_check_interval = None  # async_track_time_interval listener

    def is_position_matched(self, actual: float, target: float) -> bool:
        """Check if actual position matches target within tolerance.

        Args:
            actual: The cover's current actual position (0-100)
            target: The target position we commanded (0-100)

        Returns:
            True if within tolerance, False if mismatch

        """
        return abs(actual - target) <= self.position_tolerance

    def should_retry(self, entity_id: str) -> bool:
        """Check if a retry is allowed, and increment count if so.

        If the current retry count is below max_retries, increments the count
        and returns True. Returns False when max retries reached.

        Args:
            entity_id: Cover entity ID to check

        Returns:
            True if retry is allowed (count incremented), False if at max

        """
        count = self._retry_counts.get(entity_id, 0)
        if count >= self.max_retries:
            return False
        self._retry_counts[entity_id] = count + 1
        return True

    def get_retry_count(self, entity_id: str) -> int:
        """Return current retry count for entity.

        Args:
            entity_id: Cover entity ID

        Returns:
            Current retry count, 0 if never retried

        """
        return self._retry_counts.get(entity_id, 0)

    def reset_retry_count(self, entity_id: str) -> None:
        """Reset retry count for entity.

        Called when cover reaches target position or when manual override is
        active. Clears retry tracking to start fresh on next position command.

        Args:
            entity_id: Cover entity ID to reset

        """
        self._retry_counts.pop(entity_id, None)

    def record_verification(self, entity_id: str, now: dt.datetime) -> None:
        """Record verification timestamp for entity.

        Args:
            entity_id: Cover entity ID
            now: Current datetime (or fallback to dt.datetime.now())

        """
        check_time = now if isinstance(now, dt.datetime) else dt.datetime.now()
        self._last_verification[entity_id] = check_time

    def mark_never_commanded(self, entity_id: str) -> bool:
        """Mark entity as never having received a command (first-time logging).

        Returns True the first time called for an entity (allowing caller to log),
        False on subsequent calls (suppresses repeat log spam).

        Args:
            entity_id: Cover entity ID

        Returns:
            True if this is the first time entity is marked, False if already marked

        """
        if entity_id in self._never_commanded:
            return False
        self._never_commanded.add(entity_id)
        return True

    def mark_commanded(self, entity_id: str) -> None:
        """Remove entity from never-commanded tracking.

        Called when a command is successfully sent to the cover.

        Args:
            entity_id: Cover entity ID that received a command

        """
        self._never_commanded.discard(entity_id)
