"""Tests for CloudSuppressionManager (issue #864).

The manager owns the cross-cycle state the pure handler and the frozen
``ClimateReadings`` cannot: per-trigger Schmitt latches (hysteresis) and an
aggregate hold-timer (debounce). It consumes provider booleans — it never reads
HA — so it stays cover-type-agnostic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    DEFAULT_CLOUD_SUPPRESSION_HOLD_TIME,
)
from custom_components.adaptive_cover_pro.managers.cloud_suppression import (
    CloudSuppressionManager,
)
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings


@pytest.fixture
def logger():
    """Return a mock logger."""
    return MagicMock()


@pytest.fixture
def mgr(logger):
    """Return an enabled CloudSuppressionManager with hold-time 0."""
    m = CloudSuppressionManager(logger=logger)
    m.update_config(enabled=True, hold_time_seconds=0)
    return m


def _readings(
    *,
    is_sunny: bool = True,
    lux_below_threshold: bool = False,
    lux_release_cleared: bool | None = None,
    irradiance_below_threshold: bool = False,
    irradiance_release_cleared: bool | None = None,
    cloud_coverage_above_threshold: bool = False,
    cloud_coverage_release_cleared: bool | None = None,
) -> ClimateReadings:
    """Build ClimateReadings; blank-release defaults mirror ``not activate``."""
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=None,
        is_presence=True,
        is_sunny=is_sunny,
        lux_below_threshold=lux_below_threshold,
        irradiance_below_threshold=irradiance_below_threshold,
        cloud_coverage_above_threshold=cloud_coverage_above_threshold,
        lux_release_cleared=(
            (not lux_below_threshold)
            if lux_release_cleared is None
            else lux_release_cleared
        ),
        irradiance_release_cleared=(
            (not irradiance_below_threshold)
            if irradiance_release_cleared is None
            else irradiance_release_cleared
        ),
        cloud_coverage_release_cleared=(
            (not cloud_coverage_above_threshold)
            if cloud_coverage_release_cleared is None
            else cloud_coverage_release_cleared
        ),
    )


# ---------------------------------------------------------------------------
# (a) Back-compat: hold=0 + blank release ⇒ resolved bool == raw OR
# ---------------------------------------------------------------------------


class TestBackCompat:
    """With hold-time 0 and blank release, the manager is instantaneous."""

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({}, False),  # all sunny
            ({"is_sunny": False}, True),
            ({"lux_below_threshold": True}, True),
            ({"irradiance_below_threshold": True}, True),
            ({"cloud_coverage_above_threshold": True}, True),
            (
                {"is_sunny": True, "lux_below_threshold": False},
                False,
            ),
        ],
    )
    def test_resolved_equals_raw_or(self, mgr, kwargs, expected):
        """is_suppression_active equals the raw OR of the four triggers."""
        r = _readings(**kwargs)
        mgr.evaluate(r)
        assert mgr.is_suppression_active is expected

    def test_hold_zero_flips_immediately_and_returns_none(self, mgr):
        """A transition with hold=0 commits in the same evaluate (no timer)."""
        signal = mgr.evaluate(_readings(is_sunny=False))
        assert signal is None
        assert mgr.is_suppression_active is True
        assert mgr.is_timeout_running is False


# ---------------------------------------------------------------------------
# (b) Hysteresis: latch holds in band, releases on clear edge
# ---------------------------------------------------------------------------


class TestHysteresis:
    """A per-trigger Schmitt latch holds suppression across the release band."""

    def test_latch_holds_in_band_then_releases(self, mgr):
        """Activate → hold in band → drop only on the release edge."""
        # Cycle 1: lux below activate → latch engages.
        mgr.evaluate(_readings(lux_below_threshold=True))
        assert mgr.is_suppression_active is True

        # Cycle 2: value inside the band — neither activate nor cleared.
        mgr.evaluate(_readings(lux_below_threshold=False, lux_release_cleared=False))
        assert mgr.is_suppression_active is True  # latch held

        # Cycle 3: value clears the release edge → latch drops.
        mgr.evaluate(_readings(lux_below_threshold=False, lux_release_cleared=True))
        assert mgr.is_suppression_active is False


# ---------------------------------------------------------------------------
# (c) Hold-time debounce: pending transition + revert cancels
# ---------------------------------------------------------------------------


class TestHoldTimeDebounce:
    """A hold-time delays the flip; reverting before expiry cancels it."""

    def test_pending_transition_does_not_flip_and_signals_timer(self, logger):
        """Instantaneous flip with hold>0 keeps state and signals start-timer."""
        m = CloudSuppressionManager(logger=logger)
        m.update_config(enabled=True, hold_time_seconds=120)

        signal = m.evaluate(_readings(is_sunny=False))
        assert signal == "should_start_timeout"
        assert m.is_suppression_active is False  # unchanged pending expiry

    @pytest.mark.asyncio
    async def test_revert_before_expiry_cancels_timer(self, logger):
        """If instantaneous reverts before the timer fires, it is cancelled."""
        m = CloudSuppressionManager(logger=logger)
        m.update_config(enabled=True, hold_time_seconds=120)

        assert m.evaluate(_readings(is_sunny=False)) == "should_start_timeout"
        m.start_hold_timeout(AsyncMock())
        assert m.is_timeout_running is True

        # Revert: sun back → instantaneous matches the (still False) latch.
        signal = m.evaluate(_readings(is_sunny=True))
        assert signal is None
        assert m.is_timeout_running is False  # cancelled (true debounce)
        assert m.is_suppression_active is False  # never flipped

    @pytest.mark.asyncio
    async def test_second_evaluate_while_timer_running_does_not_resignal(self, logger):
        """A still-pending transition does not ask to start a second timer."""
        m = CloudSuppressionManager(logger=logger)
        m.update_config(enabled=True, hold_time_seconds=120)
        assert m.evaluate(_readings(is_sunny=False)) == "should_start_timeout"
        m.start_hold_timeout(AsyncMock())
        # Same instantaneous, timer already running → no new signal.
        assert m.evaluate(_readings(is_sunny=False)) is None
        assert m.is_suppression_active is False
        m.cancel_hold_timeout()


# ---------------------------------------------------------------------------
# (d) Timer expiry commits the pending transition
# ---------------------------------------------------------------------------


class TestTimerExpiry:
    """When the hold-timer fires, the pending transition commits + refreshes."""

    @pytest.mark.asyncio
    async def test_expiry_commits_and_calls_refresh(self, logger):
        """Expiry flips the resolved bool and invokes the refresh callback."""
        m = CloudSuppressionManager(logger=logger)
        m.update_config(enabled=True, hold_time_seconds=120)
        m.evaluate(_readings(is_sunny=False))
        assert m.is_suppression_active is False

        callback = AsyncMock()
        await m._on_hold_timeout_expired(callback)

        assert m.is_suppression_active is True
        callback.assert_awaited_once()


# ---------------------------------------------------------------------------
# (e) update_config default references the DEFAULT constant
# ---------------------------------------------------------------------------


def test_update_config_hold_time_defaults_to_constant(logger):
    """Omitting hold_time_seconds falls back to DEFAULT_CLOUD_SUPPRESSION_HOLD_TIME."""
    m = CloudSuppressionManager(logger=logger)
    m.update_config(enabled=True)
    # Default is instantaneous → a transition flips in the same evaluate.
    assert DEFAULT_CLOUD_SUPPRESSION_HOLD_TIME == 0
    assert m.evaluate(_readings(is_sunny=False)) is None
    assert m.is_suppression_active is True


# ---------------------------------------------------------------------------
# (f) Disabled resets latches and reports inactive
# ---------------------------------------------------------------------------


class TestDisabled:
    """A disabled manager holds no state and always reports inactive."""

    def test_disabled_returns_false_and_resets(self, logger):
        """Disabling clears any held latch and short-circuits to False."""
        m = CloudSuppressionManager(logger=logger)
        m.update_config(enabled=True, hold_time_seconds=0)
        m.evaluate(_readings(lux_below_threshold=True))
        assert m.is_suppression_active is True

        m.update_config(enabled=False, hold_time_seconds=0)
        signal = m.evaluate(_readings(lux_below_threshold=True))
        assert signal is None
        assert m.is_suppression_active is False

        # Re-enable: no stale latch survived — a sunny reading stays inactive.
        m.update_config(enabled=True, hold_time_seconds=0)
        m.evaluate(_readings(is_sunny=True))
        assert m.is_suppression_active is False

    def test_none_readings_inactive(self, mgr):
        """No readings → inactive."""
        assert mgr.evaluate(None) is None
        assert mgr.is_suppression_active is False
