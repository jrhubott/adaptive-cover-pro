"""Tests for the shared smoothing primitives (issue #917).

``advance_schmitt_latch`` and ``HoldDebouncer`` are extracted from
``CloudSuppressionManager`` so both it and the new ``ClimateSmoothingManager``
delegate to one implementation (CODING_GUIDELINES "No Code Duplication — Unify
First"). The cloud manager's own suite is the regression guard for the
extraction; these tests pin the primitives directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.managers.common.smoothing import (
    HoldDebouncer,
    advance_schmitt_latch,
)


@pytest.fixture
def logger():
    """Return a mock logger."""
    return MagicMock()


# ---------------------------------------------------------------------------
# advance_schmitt_latch truth table
# ---------------------------------------------------------------------------


class TestAdvanceSchmittLatch:
    """activate wins, then release, else hold the prior state."""

    @pytest.mark.parametrize(
        ("prev", "activate_met", "release_cleared", "expected"),
        [
            # Activate always wins regardless of prior / release.
            (False, True, False, True),
            (False, True, True, True),
            (True, True, False, True),
            # Not activate, release cleared → drop.
            (True, False, True, False),
            (False, False, True, False),
            # Not activate, not cleared → hold prior (the band).
            (True, False, False, True),
            (False, False, False, False),
        ],
    )
    def test_truth_table(self, prev, activate_met, release_cleared, expected):
        assert advance_schmitt_latch(prev, activate_met, release_cleared) is expected

    def test_blank_release_collapses_to_activate(self):
        """With release_cleared == not activate the latch tracks activate exactly."""
        for activate in (True, False):
            # This is what the provider emits for a blank release threshold.
            assert (
                advance_schmitt_latch(not activate, activate, not activate) is activate
            )


# ---------------------------------------------------------------------------
# HoldDebouncer
# ---------------------------------------------------------------------------


class TestHoldDebouncer:
    """The debounce wraps a resolved value behind a hold-time delay."""

    def test_hold_zero_commits_inline_and_returns_none(self, logger):
        d = HoldDebouncer(logger, label="test")
        d.reset(False)
        assert d.evaluate(True, hold_time=0) is None
        assert d.resolved is True
        assert d.is_timeout_running is False

    def test_no_change_returns_none(self, logger):
        d = HoldDebouncer(logger, label="test")
        d.reset(False)
        assert d.evaluate(False, hold_time=120) is None
        assert d.resolved is False

    def test_pending_transition_signals_timer(self, logger):
        d = HoldDebouncer(logger, label="test")
        d.reset(False)
        assert d.evaluate(True, hold_time=120) == "should_start_timeout"
        assert d.resolved is False  # unchanged pending expiry

    @pytest.mark.asyncio
    async def test_revert_before_expiry_cancels(self, logger):
        d = HoldDebouncer(logger, label="test")
        d.reset(False)
        assert d.evaluate(True, hold_time=120) == "should_start_timeout"
        d.start_hold_timeout(120, AsyncMock())
        assert d.is_timeout_running is True

        # Revert to the resolved value → timer cancelled (true debounce).
        assert d.evaluate(False, hold_time=120) is None
        assert d.is_timeout_running is False
        assert d.resolved is False

    @pytest.mark.asyncio
    async def test_second_evaluate_while_running_does_not_resignal(self, logger):
        d = HoldDebouncer(logger, label="test")
        d.reset(False)
        assert d.evaluate(True, hold_time=120) == "should_start_timeout"
        d.start_hold_timeout(120, AsyncMock())
        assert d.evaluate(True, hold_time=120) is None
        d.cancel()

    @pytest.mark.asyncio
    async def test_expiry_commits_and_fires_callbacks(self, logger):
        commits: list[tuple] = []
        d = HoldDebouncer(
            logger,
            label="test",
            on_commit=lambda prev, new: commits.append((prev, new)),
        )
        d.reset(False)
        assert d.evaluate(True, hold_time=120) == "should_start_timeout"

        callback = AsyncMock()
        d.start_hold_timeout(120, callback)
        await d._on_hold_timeout_expired(callback)

        assert d.resolved is True
        callback.assert_awaited()
        assert commits == [(False, True)]

    @pytest.mark.asyncio
    async def test_expiry_with_no_pending_is_noop(self, logger):
        d = HoldDebouncer(logger, label="test")
        d.reset(False)
        callback = AsyncMock()
        await d._on_hold_timeout_expired(callback)
        assert d.resolved is False
        callback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tuple_target_changing_mid_hold_keeps_timer(self, logger):
        """A >2-value target can change mid-hold — keep the timer, update pending."""
        d = HoldDebouncer(logger, label="test")
        d.reset((False, False))

        assert d.evaluate((True, False), hold_time=120) == "should_start_timeout"
        d.start_hold_timeout(120, AsyncMock())
        assert d.is_timeout_running is True

        # A different (still non-resolved) target arrives: timer stays, pending
        # updates. No new signal because a timer is already running.
        assert d.evaluate((True, True), hold_time=120) is None
        assert d.is_timeout_running is True

        # On expiry the LATEST pending target commits.
        await d._on_hold_timeout_expired(AsyncMock())
        assert d.resolved == (True, True)

    def test_reset_restores_initial_and_clears_pending(self, logger):
        d = HoldDebouncer(logger, label="test")
        d.reset(False)
        d.evaluate(True, hold_time=0)
        assert d.resolved is True
        d.reset(False)
        assert d.resolved is False
        assert d.is_timeout_running is False

    def test_on_commit_not_fired_when_value_unchanged(self, logger):
        commits: list[tuple] = []
        d = HoldDebouncer(
            logger, label="test", on_commit=lambda p, n: commits.append((p, n))
        )
        d.reset(False)
        d.evaluate(False, hold_time=0)  # no transition
        assert commits == []
