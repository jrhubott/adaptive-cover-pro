"""Shared smoothing primitives — Schmitt latch + hold-time debounce (issue #917).

Both :class:`~..cloud_suppression.CloudSuppressionManager` (light/cloud
thresholds, issue #864) and :class:`~..climate_smoothing.ClimateSmoothingManager`
(temperature-season thresholds) need the identical two mechanisms:

* a per-trigger **Schmitt latch** — engage on the activate edge, drop only once
  the value clears the release edge, hold in between; and
* an aggregate **hold-time debounce** — a resolved value flips only after a
  pending change has persisted for the configured hold-time; reverting before
  expiry cancels the pending transition (true debounce). ``hold_time == 0``
  flips immediately.

CODING_GUIDELINES "No Code Duplication — Unify First": extract once, both
managers delegate. Composed, not inherited — managers hold instances, the same
philosophy as :class:`.timeout_controller.TimeoutController`.

The debouncer's resolved value is generic: a plain ``bool`` for cloud
suppression, or a frozen multi-field flags dataclass for climate. Equality
comparison (``==``) decides "no change", so any hashable value with meaningful
equality works — including a target that changes to a *third* value mid-hold,
which keeps the running timer and simply updates the pending target.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .timeout_controller import TimeoutController


def advance_schmitt_latch(
    prev: bool, activate_met: bool, release_cleared: bool
) -> bool:
    """Return the next latch state: activate wins, then release, else hold.

    With a blank release threshold the provider sets
    ``release_cleared = not activate_met``, so this collapses to
    ``latched == activate_met`` — exact instantaneous back-compat.
    """
    if activate_met:
        return True
    if release_cleared:
        return False
    return prev


class HoldDebouncer:
    """Delay a resolved value's flip until a pending change persists.

    Owns a single :class:`TimeoutController` and the pending-target bookkeeping.
    ``resolved`` is the committed value; :meth:`evaluate` folds this cycle's
    instantaneous value in and reports whether the coordinator should start the
    hold-timer (it owns the refresh callback, not this helper).
    """

    def __init__(
        self,
        logger,
        *,
        label: str,
        on_commit: Callable[[Any, Any], None] | None = None,
    ) -> None:
        """Bind a logger, a timer label, and an optional commit callback.

        Args:
            logger: Used by the wrapped :class:`TimeoutController`.
            label: Short identifier for the timer (debug logs only).
            on_commit: Called ``on_commit(previous, target)`` whenever the
                resolved value actually changes — managers use it to record a
                diagnostic event.

        """
        self._logger = logger
        self._on_commit = on_commit
        self.resolved: Any = None
        self._pending_target: Any = None
        self._timer = TimeoutController(logger, label=label)

    @property
    def is_timeout_running(self) -> bool:
        """Return True when a hold-time debounce timer is pending."""
        return self._timer.is_running

    def evaluate(self, instantaneous: Any, hold_time: int) -> str | None:
        """Fold this cycle's instantaneous value into the debounce.

        Returns ``"should_start_timeout"`` when a transition is pending, the
        hold-time is non-zero, and no timer is already counting. Returns
        ``None`` otherwise (including the ``hold_time == 0`` case, which commits
        in-line). A pending target that changes to a further value while a timer
        runs keeps the timer and updates the pending target.
        """
        if instantaneous == self.resolved:
            # No transition needed. A timer counting toward a now-abandoned
            # transition is cancelled (the condition reverted → true debounce).
            if self.is_timeout_running:
                self.cancel()
            self._pending_target = None
            return None

        # A transition is pending.
        if hold_time <= 0:
            self._commit(instantaneous)
            return None

        self._pending_target = instantaneous
        if self.is_timeout_running:
            return None
        return "should_start_timeout"

    def start_hold_timeout(
        self, hold_time: int, refresh_callback: Callable[[], Awaitable[None]]
    ) -> None:
        """Start the hold-time debounce timer.

        When it expires (and the transition has not been reverted), the resolved
        value commits and ``refresh_callback`` runs.
        """

        async def _on_expire() -> None:
            await self._on_hold_timeout_expired(refresh_callback)

        self._timer.start(hold_time, _on_expire)

    async def _on_hold_timeout_expired(
        self, refresh_callback: Callable[[], Awaitable[None]]
    ) -> None:
        """Commit the pending transition once the hold-time has elapsed."""
        if self._pending_target is None:
            return
        self._commit(self._pending_target)
        await refresh_callback()

    def cancel(self) -> None:
        """Cancel the running hold-time timer, if any."""
        self._timer.cancel()

    def reset(self, initial: Any) -> None:
        """Restore the resolved value to ``initial`` and drop pending state."""
        self.resolved = initial
        self._pending_target = None
        self.cancel()

    def _commit(self, target: Any) -> None:
        """Flip the resolved value, fire on_commit, clear pending."""
        previous = self.resolved
        self.resolved = target
        self._pending_target = None
        if previous != target and self._on_commit is not None:
            self._on_commit(previous, target)
