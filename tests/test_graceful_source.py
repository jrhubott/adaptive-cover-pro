"""Unit tests for the GracefulSource grace-period state machine (issue #742).

GracefulSource is a pure, HA-free, asyncio-free kernel: it tracks a tri-state
"verdict" source (bool / bool / None=indeterminate) and decides, on each
``observe``, whether the caller should use the live verdict (DETERMINATE), the
last-known verdict during a grace window (HOLDING), or apply its own fallback
(FELL_BACK). A fake monotonic clock drives time so the grace window is exact.
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.managers.common.graceful_source import (
    GracefulSource,
    Resolution,
    SourceResolution,
)


@pytest.fixture
def clock():
    """Return a mutable fake clock: ``t[0]`` is "now", read via the callable."""
    t = [0.0]
    return t


def _src(clock, grace: float = 120.0) -> GracefulSource:
    return GracefulSource(grace, clock=lambda: clock[0])


def test_determinate_records_last_known(clock):
    src = _src(clock)
    assert src.observe(True) == Resolution(SourceResolution.DETERMINATE, True)
    assert src.last_known is True
    assert src.observe(False) == Resolution(SourceResolution.DETERMINATE, False)
    assert src.last_known is False


def test_holding_returns_last_known_within_grace(clock):
    src = _src(clock)
    src.observe(True)
    clock[0] = 60.0
    assert src.observe(None) == Resolution(SourceResolution.HOLDING, True)
    clock[0] = 119.0
    assert src.observe(None) == Resolution(SourceResolution.HOLDING, True)


def test_fell_back_after_grace(clock):
    src = _src(clock)
    src.observe(True)
    # Indeterminacy begins now: the first observe(None) starts the grace window.
    assert src.observe(None) == Resolution(SourceResolution.HOLDING, True)
    clock[0] = 121.0
    # Still indeterminate past the grace window → fall back.
    assert src.observe(None) == Resolution(SourceResolution.FELL_BACK, None)


def test_no_last_known_falls_back_immediately(clock):
    src = _src(clock)
    assert src.observe(None) == Resolution(SourceResolution.FELL_BACK, None)
    # Never armed the timer → remaining stays None.
    assert src.remaining() is None


def test_recovery_cancels_grace_and_rearms(clock):
    src = _src(clock)
    src.observe(True)
    clock[0] = 60.0
    assert src.observe(None).state is SourceResolution.HOLDING
    # A real verdict clears the grace window.
    assert src.observe(True) == Resolution(SourceResolution.DETERMINATE, True)
    assert src.remaining() is None
    # The machine is re-armed: a later indeterminacy starts a fresh window.
    clock[0] = 200.0
    assert src.observe(None) == Resolution(SourceResolution.HOLDING, True)


def test_idempotent_observe_does_not_advance(clock):
    src = _src(clock)
    src.observe(True)
    clock[0] = 50.0
    assert src.observe(None) == Resolution(SourceResolution.HOLDING, True)
    # Repeated observe(None) at the SAME clock value must not move the anchor.
    assert src.observe(None) == Resolution(SourceResolution.HOLDING, True)
    # The anchor is fixed at first sight (t=50), so at t=170 (120 later) it flips.
    clock[0] = 171.0
    assert src.observe(None) == Resolution(SourceResolution.FELL_BACK, None)


def test_remaining_drives_wake(clock):
    src = _src(clock)
    src.observe(True)
    # Indeterminate at t=0 → full grace remaining.
    assert src.observe(None).state is SourceResolution.HOLDING
    assert src.remaining() == pytest.approx(120.0)
    clock[0] = 119.0
    assert src.remaining() == pytest.approx(1.0)
    clock[0] = 121.0
    assert src.remaining() is None
    # Determinate again → no wake needed.
    src.observe(True)
    assert src.remaining() is None


def test_reset_forgets_state(clock):
    src = _src(clock)
    src.observe(True)
    clock[0] = 30.0
    assert src.observe(None).state is SourceResolution.HOLDING
    src.reset()
    assert src.last_known is None
    # With no last-known, the next indeterminacy falls back immediately.
    assert src.observe(None) == Resolution(SourceResolution.FELL_BACK, None)
