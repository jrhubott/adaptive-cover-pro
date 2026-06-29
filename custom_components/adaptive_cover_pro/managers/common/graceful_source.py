"""Grace-period state machine for an intermittently indeterminate source.

A small, reusable kernel for the pattern "a source usually has a verdict, but
sometimes goes indeterminate (sensor unavailable, template unrenderable); don't
react to a transient blip — hold the last-known verdict for a grace window, and
only after the window expires fall back to whatever the caller decides".

The kernel owns ONLY the state machine. It is deliberately HA-free and
asyncio-free: no ``hass``, no entities, no tasks, no knowledge of what
"fall back" means (astronomical sunset, a safe position, "assume present", …).
The caller feeds it a tri-state verdict each cycle and maps the returned
:class:`SourceResolution` onto its own policy. This is what lets opposite-
direction consumers (fail-open gate vs. fail-closed safety) reuse it later.

Time is injected as a ``clock`` callable (defaults to :func:`time.monotonic`) so
tests drive the grace window deterministically. Evaluation is stateless re-eval:
:meth:`observe` is idempotent within a cycle — the grace anchor is fixed at the
first indeterminate sighting and never advanced by repeat calls at the same
clock value. Managers compose this; they do not inherit from it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto


class SourceResolution(Enum):
    """How a source's current verdict should be applied this cycle."""

    DETERMINATE = auto()  # source produced a real verdict this cycle
    HOLDING = auto()  # indeterminate, within grace → use last_known
    FELL_BACK = auto()  # indeterminate past grace (or never seen) → caller's fallback


@dataclass(frozen=True, slots=True)
class Resolution:
    """The outcome of one :meth:`GracefulSource.observe` call.

    ``value`` carries the verdict to use: the live verdict for DETERMINATE, the
    held last-known verdict for HOLDING, and ``None`` for FELL_BACK (or a source
    that has never produced a verdict).
    """

    state: SourceResolution
    value: bool | None


class GracefulSource:
    """Track a tri-state verdict source through a grace window.

    Feed :meth:`observe` the source's verdict each cycle (``True``/``False`` for
    a real verdict, ``None`` when the source is indeterminate). The returned
    :class:`Resolution` tells the caller whether to use the live verdict, hold
    the last-known one, or apply its own fallback.
    """

    def __init__(
        self,
        grace_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize with the grace window length and an injectable clock.

        Args:
            grace_seconds: How long to hold the last-known verdict after the
                source first goes indeterminate, before reporting FELL_BACK.
            clock: Monotonic time source returning seconds. Injected so tests
                can advance time deterministically.

        """
        self._grace_seconds = grace_seconds
        self._clock = clock
        self._last_known: bool | None = None
        self._indeterminate_since: float | None = None

    def observe(self, verdict: bool | None, *, now: float | None = None) -> Resolution:
        """Record this cycle's verdict and resolve how it should be applied.

        Args:
            verdict: ``True``/``False`` for a real verdict; ``None`` when the
                source is indeterminate this cycle.
            now: Optional clock override (seconds). Defaults to the injected
                clock — supplied mainly for callers that already sampled time.

        Returns:
            A :class:`Resolution`. A real verdict records last-known, clears the
            grace anchor, and returns DETERMINATE. ``None`` with no last-known
            ever returns FELL_BACK without arming the timer. ``None`` with a
            last-known fixes the grace anchor on first sight and returns HOLDING
            until ``grace_seconds`` elapse, then FELL_BACK.

        """
        if verdict is not None:
            self._last_known = verdict
            self._indeterminate_since = None
            return Resolution(SourceResolution.DETERMINATE, verdict)

        # Indeterminate this cycle.
        if self._last_known is None:
            # Never observed a real verdict → nothing to hold. Never arm the
            # timer; the caller falls back immediately.
            return Resolution(SourceResolution.FELL_BACK, None)

        current = self._clock() if now is None else now
        if self._indeterminate_since is None:
            # Fix the anchor on first sight; idempotent for repeats this cycle.
            self._indeterminate_since = current
        if current - self._indeterminate_since >= self._grace_seconds:
            return Resolution(SourceResolution.FELL_BACK, None)
        return Resolution(SourceResolution.HOLDING, self._last_known)

    def remaining(self, *, now: float | None = None) -> float | None:
        """Return seconds until a HOLDING source flips to FELL_BACK.

        Returns ``None`` when there is no pending flip: the source is
        determinate, has never produced a verdict, or has already fallen back.
        Used by the caller to schedule a single prompt wake-up at expiry.
        """
        if self._indeterminate_since is None or self._last_known is None:
            return None
        current = self._clock() if now is None else now
        remaining = self._grace_seconds - (current - self._indeterminate_since)
        return remaining if remaining > 0 else None

    @property
    def last_known(self) -> bool | None:
        """The most recent real verdict observed, or ``None`` if never seen."""
        return self._last_known

    def reset(self) -> None:
        """Forget the last-known verdict and any in-flight grace window."""
        self._last_known = None
        self._indeterminate_since = None
