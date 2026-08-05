"""Acceptance test for issue #917 — end-to-end climate temperature smoothing.

Drives the real chain: ``ClimateProvider.read()`` computes the crossings from raw
temps + thresholds → ``ClimateSmoothingManager`` applies the Schmitt latch +
hold-time debounce → ``ClimateCoverData`` consumes the resolved flags. Reproduces
the reporter's scenario (a boiler sensor swinging 31.8 ↔ 32.4 °C around a 32 °C
outside threshold) and proves the season stops flapping.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.managers.climate_smoothing import (
    ClimateSmoothingManager,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
    ClimateCoverData,
)
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateProvider


def _make_state(value: str):
    s = MagicMock()
    s.state = value
    s.attributes = {}
    return s


class _ProviderHarness:
    """Feed a sequence of outside temperatures through the real provider."""

    def __init__(self, *, outside_threshold, outside_release=None):
        self._hass = MagicMock()
        self._provider = ClimateProvider(hass=self._hass, logger=MagicMock())
        self._outside_threshold = outside_threshold
        self._outside_release = outside_release

    def read(self, outside_value: str):
        self._hass.states.get.side_effect = lambda eid: (
            _make_state(outside_value) if eid == "sensor.out" else None
        )
        return self._provider.read(
            outside_entity="sensor.out",
            temp_switch=True,
            outside_threshold=self._outside_threshold,
            outside_threshold_release=self._outside_release,
        )


def _cover_data(flags):
    return ClimateCoverData(
        temp_low=18.0,
        temp_high=25.0,
        temp_switch=True,
        policy=get_policy("cover_blind"),
        transparent_blind=False,
        temp_summer_outside=32.0,
        outside_temperature="31.8",
        inside_temperature=None,
        is_presence=True,
        is_sunny=True,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        winter_close_insulation=False,
        outside_high_active=flags.outside_high,
        winter_active=flags.winter,
        summer_warm_active=flags.summer_warm,
        extreme_heat_active=flags.extreme_heat,
    )


class TestReporterScenario:
    """Hysteresis latch stops the outside-high season from flapping (#917)."""

    def test_release_band_latches_outside_high_across_dither(self):
        harness = _ProviderHarness(outside_threshold=32.0, outside_release=30.0)
        mgr = ClimateSmoothingManager(logger=MagicMock())
        mgr.update_config(enabled=True, hold_time_seconds=0)

        # Sensor climbs above 32 → outside-high engages.
        mgr.evaluate(harness.read("32.4"))
        assert mgr.resolved_flags.outside_high is True

        # Now dither around the threshold, staying inside the [30, 32] band —
        # the latch HOLDS (no flapping), which is the whole fix.
        for value in ("31.8", "32.4", "31.8", "32.4", "31.8"):
            mgr.evaluate(harness.read(value))
            assert mgr.resolved_flags.outside_high is True, f"flapped at {value}"

        # The handler sees a stable outside-high the whole time.
        assert _cover_data(mgr.resolved_flags).outside_high is True

        # Only once it clears the release edge (≤30) does the latch drop.
        mgr.evaluate(harness.read("29.0"))
        assert mgr.resolved_flags.outside_high is False

    @pytest.mark.asyncio
    async def test_hold_time_debounces_a_brief_spike(self):
        harness = _ProviderHarness(outside_threshold=32.0)  # blank release
        mgr = ClimateSmoothingManager(logger=MagicMock())
        mgr.update_config(enabled=True, hold_time_seconds=600)

        # A brief spike above threshold → transition pending, timer requested.
        signal = mgr.evaluate(harness.read("33.0"))
        assert signal == "should_start_timeout"
        mgr.start_hold_timeout(AsyncMock())
        assert mgr.is_timeout_running is True
        assert mgr.resolved_flags.outside_high is False  # not committed yet

        # The spike passes (back below threshold) before the hold elapses →
        # the pending transition is cancelled, so the cover never moved.
        assert mgr.evaluate(harness.read("31.0")) is None
        assert mgr.is_timeout_running is False
        assert mgr.resolved_flags.outside_high is False


class TestBackCompatSweep:
    """hold=0 + no release edges ⇒ smoothed flags equal the raw crossing."""

    @pytest.mark.parametrize("value", ["25.0", "31.9", "32.0", "32.1", "40.0"])
    def test_smoothed_equals_raw_at_every_sample(self, value):
        harness = _ProviderHarness(outside_threshold=32.0)  # blank release, hold 0
        mgr = ClimateSmoothingManager(logger=MagicMock())
        mgr.update_config(enabled=True, hold_time_seconds=0)

        readings = harness.read(value)
        mgr.evaluate(readings)
        # Raw crossing bit the provider emitted == smoothed resolved flag.
        assert mgr.resolved_flags.outside_high is readings.outside_above_threshold
