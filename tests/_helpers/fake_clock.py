"""A hand-driven monotonic clock for the grace-window state machines.

``GracefulSource`` and ``ConditionGate`` both take an injectable ``clock``
callable precisely so tests can advance time deterministically instead of
sleeping. This is that callable, shared rather than re-declared per test module
(CODING_GUIDELINES § cross-file test helpers).
"""

from __future__ import annotations


class FakeClock:
    """Callable monotonic clock the test advances explicitly.

    Starts at an arbitrary non-zero value so a test can never accidentally pass
    by comparing against ``0``.
    """

    def __init__(self, start: float = 1000.0) -> None:
        """Start the clock at ``start`` seconds."""
        self.now = start

    def __call__(self) -> float:
        """Return the current time, in seconds."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward by ``seconds``."""
        self.now += seconds
