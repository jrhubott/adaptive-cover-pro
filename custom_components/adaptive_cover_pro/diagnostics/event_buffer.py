"""Bounded ring buffer for cross-component diagnostic events."""

from __future__ import annotations

from collections import deque


class EventBuffer:
    """Shared ring buffer owned by the coordinator and injected into all writers.

    Writers call record(); DiagnosticsBuilder is the only reader that formats
    the raw dicts for output.
    """

    def __init__(self, maxlen: int) -> None:
        """Initialize the ring buffer.

        Args:
            maxlen: Maximum number of events to retain (oldest are evicted).
                Coerced to int — HA's NumberSelector(SLIDER) returns floats
                (e.g. 50.0) and ``deque(maxlen=<float>)`` raises TypeError.

        """
        self._buf: deque[dict] = deque(maxlen=int(maxlen))

    def record(self, event: dict) -> None:
        """Append an event dict to the buffer, evicting the oldest if full."""
        self._buf.append(event)

    def snapshot(self) -> list[dict]:
        """Return a copy of current buffer contents in insertion order."""
        return list(self._buf)

    def resize(self, maxlen: int) -> None:
        """Resize the buffer, preserving the most-recent events."""
        self._buf = deque(self._buf, maxlen=int(maxlen))

    @property
    def maxlen(self) -> int | None:
        """Maximum capacity of the ring buffer."""
        return self._buf.maxlen

    def __len__(self) -> int:
        """Return the current number of events in the buffer."""
        return len(self._buf)
