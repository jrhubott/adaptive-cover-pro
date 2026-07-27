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
direction consumers (fail-open gate vs. fail-closed safety) reuse it later —
and, since issue #1012, differently-*shaped* payloads too: the verdict is a
generic ``T`` (``bool`` for the daytime gate; ``bool`` or ``tuple[bool,
tuple[str, ...]]`` for a custom-position slot's sensor/template fold), with
``None`` reserved as the indeterminate sentinel. This works even when ``T``
itself can be "falsy" (an empty tuple, ``False``) because the sentinel check is
always ``is not None``, never truthiness — a valid-but-falsy payload is never
confused with "no opinion this cycle".

Time is injected as a ``clock`` callable (defaults to :func:`time.monotonic`) so
tests drive the grace window deterministically. Evaluation is stateless re-eval:
:meth:`observe` is idempotent within a cycle — the grace anchor is fixed once
per indeterminate run and never advanced by repeat calls at the same clock
value. Managers compose this; they do not inherit from it.

**Anchor point** (issue #1012 audit): the grace window can be measured from
either of two moments, and the default preserves the daytime gate's original,
test-locked behaviour:

* ``anchor_at_last_known=False`` (default) — anchor at the *first
  indeterminate sighting*. This is what :class:`~managers.time_window.
  TimeWindowManager` has always used for the daytime gate (issue #742) —
  changing it would change production fallback timing for an existing,
  shipped consumer, so it stays the default. ``tests/test_graceful_source.py``
  and ``tests/test_time_window_manager.py`` (``test_seconds_until_gate_fallback_
  phases``, which asserts a *full* grace window remains at the first
  indeterminate reading) lock this in.
* ``anchor_at_last_known=True`` — anchor at the source's *last known-good
  observation*. The custom-position per-input hold (issue #1012) needs this:
  its regression suite was written and locked against "the window is measured
  from when we last knew the truth", not "from when we noticed we didn't".
  Both anchors keep the same ``>=`` boundary convention (expiry lands exactly
  *at* ``grace_seconds`` elapsed, not after) — no existing test in either
  consumer's suite asserts the single-instant tie-breaker, so one boundary
  rule serves both without special-casing.
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
class Resolution[T]:
    """The outcome of one :meth:`GracefulSource.observe` call.

    ``value`` carries the verdict to use: the live verdict for DETERMINATE, the
    held last-known verdict for HOLDING, and ``None`` for FELL_BACK (or a source
    that has never produced a verdict).
    """

    state: SourceResolution
    value: T | None


class GracefulSource[T]:
    """Track a tri-state verdict source through a grace window.

    Feed :meth:`observe` the source's verdict each cycle (a real ``T`` value
    for a real verdict, ``None`` when the source is indeterminate). The
    returned :class:`Resolution` tells the caller whether to use the live
    verdict, hold the last-known one, or apply its own fallback.
    """

    def __init__(
        self,
        grace_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        anchor_at_last_known: bool = False,
    ) -> None:
        """Initialize with the grace window length and an injectable clock.

        Args:
            grace_seconds: How long to hold the last-known verdict before
                reporting FELL_BACK — measured from whichever moment
                ``anchor_at_last_known`` selects.
            clock: Monotonic time source returning seconds. Injected so tests
                can advance time deterministically.
            anchor_at_last_known: When ``False`` (default), the grace window
                is measured from the first indeterminate sighting — the
                daytime gate's original, test-locked behaviour (issue #742).
                When ``True``, it is measured from the last known-good
                observation instead — what the custom-position per-input hold
                needs (issue #1012). See the module docstring's "Anchor
                point" section for why both exist rather than picking one.

        """
        self._grace_seconds = grace_seconds
        self._clock = clock
        self._anchor_at_last_known = anchor_at_last_known
        self._last_known: T | None = None
        self._last_known_at: float | None = None
        self._indeterminate_since: float | None = None

    def observe(self, verdict: T | None, *, now: float | None = None) -> Resolution[T]:
        """Record this cycle's verdict and resolve how it should be applied.

        Args:
            verdict: A real ``T`` value for a real verdict; ``None`` when the
                source is indeterminate this cycle. ``None`` is the sentinel —
                a falsy-but-real payload (``False``, an empty tuple) is never
                mistaken for indeterminate.
            now: Optional clock override (seconds). Defaults to the injected
                clock — supplied mainly for callers that already sampled time
                once for the whole cycle, so every source they resolve agrees
                on "now" to the same instant.

        Returns:
            A :class:`Resolution`. A real verdict records last-known (and when
            it was observed), clears the indeterminate anchor, and returns
            DETERMINATE. ``None`` with no last-known ever returns FELL_BACK
            without arming the timer. ``None`` with a last-known fixes the
            grace anchor (per ``anchor_at_last_known``) on first sight and
            returns HOLDING until ``grace_seconds`` elapse, then FELL_BACK.

        """
        if verdict is not None:
            current = self._clock() if now is None else now
            self._last_known = verdict
            self._last_known_at = current
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
        anchor = (
            self._last_known_at
            if self._anchor_at_last_known
            else self._indeterminate_since
        )
        if current - anchor >= self._grace_seconds:
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
        anchor = (
            self._last_known_at
            if self._anchor_at_last_known
            else self._indeterminate_since
        )
        remaining = self._grace_seconds - (current - anchor)
        return remaining if remaining > 0 else None

    @property
    def last_known(self) -> T | None:
        """The most recent real verdict observed, or ``None`` if never seen."""
        return self._last_known

    def reset(self) -> None:
        """Forget the last-known verdict and any in-flight grace window."""
        self._last_known = None
        self._last_known_at = None
        self._indeterminate_since = None
