"""Single-task asyncio timeout helper.

``MotionManager``, ``WeatherManager`` and ``GracePeriodManager`` all
implement the same "spawn task → asyncio.sleep → check / set flag /
record event → null out the task handle" pattern. The pattern is small
but the hand-rolled copies in each manager have drifted (different log
messages, different event names, different cancel-safety) and the
"forgot to null the handle" footgun has bitten the repo before.

This helper owns the lifecycle in one place. It is a concrete class —
no ABC, no Protocol. Managers compose it; they do not inherit from it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class TimeoutController:
    """Manage a single in-flight asyncio task for a fire-once timer.

    Each instance owns at most one task. Calling :meth:`start` while a
    timer is in flight cancels the existing one first, then spawns the
    new one — the helper never has two parallel timers. On expiry, the
    handle is nulled out automatically (even if the on-expire callback
    raises), closing the "stale task handle" bug class.

    The helper makes one explicit promise and one explicit non-promise:

    * **Promised:** if :meth:`cancel` is called *before* the sleep
      completes, ``on_expire`` will not run.
    * **Not promised:** if :meth:`cancel` is called *after* the sleep
      completes (i.e. during the on-expire callback's own awaits), the
      callback is not interrupted at the controller boundary — the
      cancel becomes a no-op there. Cancellation reaching into a
      half-run callback is a footgun, so this helper draws the line at
      "sleep done = callback committed". Callers that need finer
      control should add their own guard inside ``on_expire``.

    The ``label`` argument is a short identifier (e.g. ``"motion
    timeout"``) used in debug logs only — not user-facing.
    """

    def __init__(self, logger, *, label: str = "timeout", hass=None) -> None:
        """Initialize with the manager's logger and a short label.

        Args:
            logger: Used for debug-level cancel/start messages. The same
                logger every consuming manager already uses, so log
                output stays attributable to the owning manager.
            label: Short identifier for log messages. Convention is
                ``"<feature> timeout"`` (e.g. ``"motion timeout"``).
            hass: Optional HomeAssistant instance. When provided, the
                timer task is spawned via ``hass.async_create_background_task``
                so Home Assistant tracks it and cancels it on stop — this is
                what keeps a long debounce (e.g. the 900s health-check timer)
                from leaking as a lingering asyncio task when the owning entry
                is never explicitly shut down (issue #975). When ``None`` the
                helper falls back to a bare ``asyncio.create_task`` — the
                original behaviour, kept for the event-driven managers whose
                own lifecycle already cancels their timers.

        """
        self._logger = logger
        self._label = label
        self._hass = hass
        self._task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        """Return True iff a timer task is in flight (not done, not cancelled)."""
        return self._task is not None and not self._task.done()

    def start(
        self,
        seconds: float,
        on_expire: Callable[[], Awaitable[None]],
    ) -> None:
        """Cancel any in-flight timer and spawn a new one.

        The new task sleeps for ``seconds`` and then awaits
        ``on_expire()``. Exceptions raised by ``on_expire`` propagate
        out of the asyncio task (so they surface in logs) — the
        controller does not swallow them.

        Args:
            seconds: How long to sleep before invoking ``on_expire``.
            on_expire: Async callable run once after the sleep completes.

        """
        self.cancel()
        coro = self._run(seconds, on_expire)
        if self._hass is not None:
            # HA-tracked: cancelled automatically on Home Assistant stop, so a
            # pending long debounce never lingers as an orphan task.
            self._task = self._hass.async_create_background_task(coro, name=self._label)
        else:
            self._task = asyncio.create_task(coro)

    async def _run(
        self,
        seconds: float,
        on_expire: Callable[[], Awaitable[None]],
    ) -> None:
        """Inner coroutine — sleep, then run ``on_expire``.

        The task object running this coroutine is captured at entry and
        only clears ``self._task`` if it still matches at exit. This
        lets ``on_expire`` start a new timer on the same controller
        without the prior task's ``finally`` clobbering the new handle.
        """
        own = asyncio.current_task()
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return

        try:
            await on_expire()
        finally:
            # Identity check protects against on_expire starting a new
            # timer (which would have replaced self._task by now).
            if self._task is own:
                self._task = None

    def cancel(self) -> None:
        """Cancel the in-flight task, if any. Safe no-op when idle."""
        if self.is_running:
            self._logger.debug("%s canceled", self._label)
            self._task.cancel()
        self._task = None
