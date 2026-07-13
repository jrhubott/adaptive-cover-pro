"""Tests for ClimateSmoothingManager (issue #917).

Structural mirror of ``test_cloud_suppression`` — but the resolved value is a
four-field ``ClimateTempFlags`` (a multi-way season classifier), not a single
OR-bool. The manager holds four Schmitt latches (hysteresis) and one aggregate
hold-time debounce over the flags tuple. It consumes provider booleans only —
never HA, never a cover type.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    DEFAULT_CLIMATE_TEMP_HOLD_TIME,
)
from custom_components.adaptive_cover_pro.managers.climate_smoothing import (
    ClimateSmoothingManager,
)
from custom_components.adaptive_cover_pro.pipeline.types import ClimateTempFlags
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings


@pytest.fixture
def logger():
    """Return a mock logger."""
    return MagicMock()


@pytest.fixture
def mgr(logger):
    """Return an enabled manager with hold-time 0."""
    m = ClimateSmoothingManager(logger=logger)
    m.update_config(enabled=True, hold_time_seconds=0)
    return m


def _readings(
    *,
    winter: bool = False,
    winter_cleared: bool | None = None,
    summer_warm: bool = False,
    summer_warm_cleared: bool | None = None,
    outside_high: bool = False,
    outside_cleared: bool | None = None,
    extreme: bool = False,
    extreme_cleared: bool | None = None,
) -> ClimateReadings:
    """Build ClimateReadings; blank-release defaults mirror ``not activate``."""
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=None,
        is_presence=True,
        is_sunny=True,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        cloud_coverage_above_threshold=False,
        temp_below_low_threshold=winter,
        temp_low_release_cleared=(
            (not winter) if winter_cleared is None else winter_cleared
        ),
        temp_above_high_threshold=summer_warm,
        temp_high_release_cleared=(
            (not summer_warm) if summer_warm_cleared is None else summer_warm_cleared
        ),
        outside_above_threshold=outside_high,
        outside_release_cleared=(
            (not outside_high) if outside_cleared is None else outside_cleared
        ),
        outside_above_extreme_heat=extreme,
        extreme_heat_release_cleared=(
            (not extreme) if extreme_cleared is None else extreme_cleared
        ),
    )


# ---------------------------------------------------------------------------
# (a) Back-compat: hold=0 + blank release ⇒ resolved flags == raw activate bits
# ---------------------------------------------------------------------------


class TestBackCompat:
    """hold=0 + blank release ⇒ resolved flags equal the raw activate bits."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {},
            {"winter": True},
            {"summer_warm": True},
            {"outside_high": True},
            {"extreme": True},
            {"summer_warm": True, "outside_high": True},
        ],
    )
    def test_resolved_equals_raw_activate_bits(self, mgr, kwargs):
        r = _readings(**kwargs)
        mgr.evaluate(r)
        assert mgr.resolved_flags == ClimateTempFlags(
            winter=r.temp_below_low_threshold,
            summer_warm=r.temp_above_high_threshold,
            outside_high=r.outside_above_threshold,
            extreme_heat=r.outside_above_extreme_heat,
        )

    def test_hold_zero_flips_immediately_and_returns_none(self, mgr):
        signal = mgr.evaluate(_readings(outside_high=True))
        assert signal is None
        assert mgr.resolved_flags.outside_high is True
        assert mgr.is_timeout_running is False


# ---------------------------------------------------------------------------
# (b) Hysteresis: the outside-high latch drives the reporter's scenario
# ---------------------------------------------------------------------------


class TestHysteresis:
    """The outside-high Schmitt latch drives the reporter's scenario."""

    def test_outside_high_latch_holds_in_band_then_releases(self, mgr):
        # Cycle 1: outside above threshold → latch engages.
        mgr.evaluate(_readings(outside_high=True))
        assert mgr.resolved_flags.outside_high is True

        # Cycle 2: value inside the [release, threshold] band — holds.
        mgr.evaluate(_readings(outside_high=False, outside_cleared=False))
        assert mgr.resolved_flags.outside_high is True

        # Cycle 3: value clears the release edge → drops.
        mgr.evaluate(_readings(outside_high=False, outside_cleared=True))
        assert mgr.resolved_flags.outside_high is False


# ---------------------------------------------------------------------------
# (c) Hold-time debounce
# ---------------------------------------------------------------------------


class TestHoldTimeDebounce:
    """A hold-time delays the flip; reverting before expiry cancels it."""

    def test_pending_change_signals_timer_and_does_not_flip(self, logger):
        m = ClimateSmoothingManager(logger=logger)
        m.update_config(enabled=True, hold_time_seconds=120)
        signal = m.evaluate(_readings(winter=True))
        assert signal == "should_start_timeout"
        assert m.resolved_flags.winter is False  # unchanged pending expiry

    @pytest.mark.asyncio
    async def test_revert_before_expiry_cancels(self, logger):
        m = ClimateSmoothingManager(logger=logger)
        m.update_config(enabled=True, hold_time_seconds=120)
        assert m.evaluate(_readings(winter=True)) == "should_start_timeout"
        m.start_hold_timeout(AsyncMock())
        assert m.is_timeout_running is True
        # Revert to the resolved (all-False) flags → timer cancelled.
        assert m.evaluate(_readings()) is None
        assert m.is_timeout_running is False
        assert m.resolved_flags.winter is False

    @pytest.mark.asyncio
    async def test_second_evaluate_while_running_does_not_resignal(self, logger):
        m = ClimateSmoothingManager(logger=logger)
        m.update_config(enabled=True, hold_time_seconds=120)
        assert m.evaluate(_readings(winter=True)) == "should_start_timeout"
        m.start_hold_timeout(AsyncMock())
        assert m.evaluate(_readings(winter=True)) is None
        m.cancel_hold_timeout()

    @pytest.mark.asyncio
    async def test_mixed_pending_uses_single_aggregate_timer(self, logger):
        """A second crossing changing mid-hold keeps ONE timer, updates pending."""
        m = ClimateSmoothingManager(logger=logger)
        m.update_config(enabled=True, hold_time_seconds=120)
        assert m.evaluate(_readings(winter=True)) == "should_start_timeout"
        m.start_hold_timeout(AsyncMock())
        # Now outside_high also flips: same timer, no new signal.
        assert m.evaluate(_readings(winter=True, outside_high=True)) is None
        assert m.is_timeout_running is True
        m.cancel_hold_timeout()


# ---------------------------------------------------------------------------
# (d) Timer expiry commits
# ---------------------------------------------------------------------------


class TestTimerExpiry:
    """When the hold-timer fires, the pending transition commits + refreshes."""

    @pytest.mark.asyncio
    async def test_expiry_commits_and_calls_refresh(self, logger):
        m = ClimateSmoothingManager(logger=logger)
        m.update_config(enabled=True, hold_time_seconds=120)
        m.evaluate(_readings(winter=True))
        assert m.resolved_flags.winter is False

        callback = AsyncMock()
        m.start_hold_timeout(callback)
        await m._on_hold_timeout_expired(callback)

        assert m.resolved_flags.winter is True
        callback.assert_awaited()


# ---------------------------------------------------------------------------
# (e) update_config default references the DEFAULT constant
# ---------------------------------------------------------------------------


def test_update_config_hold_time_defaults_to_constant(logger):
    m = ClimateSmoothingManager(logger=logger)
    m.update_config(enabled=True)
    assert DEFAULT_CLIMATE_TEMP_HOLD_TIME == 0
    assert m.evaluate(_readings(winter=True)) is None
    assert m.resolved_flags.winter is True


# ---------------------------------------------------------------------------
# (f) Disabled resets latches and reports None
# ---------------------------------------------------------------------------


class TestDisabled:
    """A disabled manager holds no state and reports None flags."""

    def test_disabled_returns_none_flags_and_resets(self, logger):
        m = ClimateSmoothingManager(logger=logger)
        m.update_config(enabled=True, hold_time_seconds=0)
        m.evaluate(_readings(winter=True))
        assert m.resolved_flags.winter is True

        m.update_config(enabled=False, hold_time_seconds=0)
        signal = m.evaluate(_readings(winter=True))
        assert signal is None
        assert m.resolved_flags is None

        # Re-enable: no stale latch survived.
        m.update_config(enabled=True, hold_time_seconds=0)
        m.evaluate(_readings())
        assert m.resolved_flags.winter is False

    def test_none_readings_reset_to_all_false(self, mgr):
        # Enabled but no readings this cycle → reset to all-False (mirrors cloud
        # resetting to inactive). None is reserved for the DISABLED case.
        assert mgr.evaluate(None) is None
        assert mgr.resolved_flags == ClimateTempFlags(
            winter=False, summer_warm=False, outside_high=False, extreme_heat=False
        )
