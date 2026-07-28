"""Unit tests for ``TimeoutController``.

Pins the four contract guarantees that the three timeout-owning managers
rely on:

  1. ``start`` cancels an existing timer before spawning a new one.
  2. ``cancel`` before expiry suppresses the callback.
  3. The handle nulls out after expiry so the controller is reusable.
  4. ``on_expire`` raising does not leave a dangling task handle.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from custom_components.adaptive_cover_pro.managers.common import TimeoutController

pytestmark = pytest.mark.unit


@pytest.fixture
def logger():
    return logging.getLogger("test.timeout_controller")


def _zero_sleep_controller(logger) -> TimeoutController:
    """Build a controller with a label that keeps assertions readable."""
    return TimeoutController(logger, label="test timer")


class TestLifecycle:
    """Idle → running → expired transitions."""

    async def test_idle_at_construction(self, logger) -> None:
        ctrl = _zero_sleep_controller(logger)
        assert ctrl.is_running is False

    async def test_start_marks_running(self, logger) -> None:
        ctrl = _zero_sleep_controller(logger)

        async def _noop() -> None:
            await asyncio.sleep(0)

        # A long sleep so the timer stays in flight long enough to inspect.
        ctrl.start(seconds=100, on_expire=_noop)
        assert ctrl.is_running is True
        ctrl.cancel()

    async def test_callback_runs_after_sleep(self, logger) -> None:
        ctrl = _zero_sleep_controller(logger)
        fired = asyncio.Event()

        async def _on_expire() -> None:
            fired.set()

        ctrl.start(seconds=0, on_expire=_on_expire)
        # Yield twice — first to let the create_task task run, second for sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert fired.is_set()

    async def test_handle_nulled_after_expiry(self, logger) -> None:
        ctrl = _zero_sleep_controller(logger)

        async def _on_expire() -> None:
            return

        ctrl.start(seconds=0, on_expire=_on_expire)
        # Drain the event loop until the controller is idle again.
        for _ in range(5):
            if not ctrl.is_running:
                break
            await asyncio.sleep(0)
        assert ctrl.is_running is False


class TestCancel:
    """Cancellation semantics."""

    async def test_cancel_before_expiry_suppresses_callback(self, logger) -> None:
        ctrl = _zero_sleep_controller(logger)
        fired = asyncio.Event()

        async def _on_expire() -> None:
            fired.set()

        ctrl.start(seconds=100, on_expire=_on_expire)
        ctrl.cancel()
        # Give the cancelled task a chance to settle.
        await asyncio.sleep(0)
        assert ctrl.is_running is False
        assert fired.is_set() is False

    async def test_cancel_when_idle_is_noop(self, logger) -> None:
        ctrl = _zero_sleep_controller(logger)
        ctrl.cancel()  # must not raise
        assert ctrl.is_running is False

    async def test_double_cancel_is_safe(self, logger) -> None:
        ctrl = _zero_sleep_controller(logger)

        async def _on_expire() -> None:
            return

        ctrl.start(seconds=100, on_expire=_on_expire)
        ctrl.cancel()
        ctrl.cancel()  # second cancel must not raise


class TestStartReplaces:
    """Calling ``start`` twice cancels the first timer."""

    async def test_second_start_cancels_first(self, logger) -> None:
        ctrl = _zero_sleep_controller(logger)
        first_fired = asyncio.Event()
        second_fired = asyncio.Event()

        async def _first() -> None:
            first_fired.set()

        async def _second() -> None:
            second_fired.set()

        ctrl.start(seconds=100, on_expire=_first)
        ctrl.start(seconds=0, on_expire=_second)
        # Drain
        for _ in range(5):
            if second_fired.is_set():
                break
            await asyncio.sleep(0)
        assert first_fired.is_set() is False
        assert second_fired.is_set() is True


class TestCallbackErrors:
    """A callback that raises must not leave a stale handle behind."""

    async def test_handle_cleared_when_callback_raises(self, logger) -> None:
        ctrl = _zero_sleep_controller(logger)

        async def _explode() -> None:
            raise RuntimeError("boom")

        ctrl.start(seconds=0, on_expire=_explode)
        # Drain. The task will end with an exception; asyncio will log it,
        # but we only care that the controller is idle again.
        for _ in range(5):
            if not ctrl.is_running:
                break
            await asyncio.sleep(0)
        assert ctrl.is_running is False


class TestCallbackMayRestart:
    """``on_expire`` is free to start a new timer on the same controller."""

    async def test_callback_starting_new_timer_is_not_clobbered(self, logger) -> None:
        ctrl = _zero_sleep_controller(logger)
        first_fired = asyncio.Event()
        second_fired = asyncio.Event()

        async def _second() -> None:
            second_fired.set()

        async def _first() -> None:
            first_fired.set()
            ctrl.start(seconds=0, on_expire=_second)

        ctrl.start(seconds=0, on_expire=_first)
        # Drain enough turns for both timers to fire.
        for _ in range(8):
            if second_fired.is_set():
                break
            await asyncio.sleep(0)
        assert first_fired.is_set()
        assert second_fired.is_set()
        # Final state must be idle, not stuck with the first task's handle.
        assert ctrl.is_running is False
