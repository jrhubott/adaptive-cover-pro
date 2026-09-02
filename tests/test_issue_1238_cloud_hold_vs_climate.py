"""Issue #1238 — the cloud-suppression hold time must also gate climate LOW_LIGHT.

``cloud_suppression_hold_time`` (issue #864 / PR #873) debounces exactly one
boolean: ``PipelineSnapshot.cloud_suppression_active``, read by
``CloudSuppressionHandler`` at priority 60. ``ClimateHandler`` at priority 50 is
a **second, independent consumer of the very same raw light readings** and was
never brought under the smoothing — its LOW_LIGHT branch moved the cover in the
same update cycle that logged "Cloud-suppression change pending — holding 600
seconds before it takes effect".

Every test here drives a real :class:`CloudSuppressionManager` and threads its
resolved bool into the snapshot, which is exactly what the coordinator does. No
``hass`` is involved: the manager consumes provider booleans and the pipeline is
pure.

The reporter's install (2026.8.1, vertical blind, ``lux_threshold=30000`` with a
blank release threshold, ``cloud_suppression_hold_time=600``, effective default
100 %, ``min_position_sun_tracking=10``) gives the two competing answers used
throughout: **100 %** (the climate LOW_LIGHT default) and **10 %** (sun
tracking).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    ClimateStrategy,
    ControlMethod,
)
from custom_components.adaptive_cover_pro.managers.cloud_suppression import (
    CloudSuppressionManager,
)
from custom_components.adaptive_cover_pro.pipeline.handlers import (
    ClimateHandler,
    DefaultHandler,
    SolarHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.cloud_suppression import (
    CloudSuppressionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import ClimateOptions
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings
from tests.test_pipeline.conftest import make_snapshot

# The reporter's two competing answers.
DEFAULT_POS = 100
SOLAR_POS = 10

HOLD = 600


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _readings(
    *,
    is_sunny: bool = True,
    lux_below_threshold: bool = False,
    lux_release_cleared: bool | None = None,
    cloud_coverage_above_threshold: bool = False,
    inside_temperature: float = 22.0,
) -> ClimateReadings:
    """Build readings; the blank-release default mirrors ``not activate`` (#864)."""
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=inside_temperature,
        is_presence=True,
        is_sunny=is_sunny,
        lux_below_threshold=lux_below_threshold,
        irradiance_below_threshold=False,
        cloud_coverage_above_threshold=cloud_coverage_above_threshold,
        lux_release_cleared=(
            (not lux_below_threshold)
            if lux_release_cleared is None
            else lux_release_cleared
        ),
        cloud_coverage_release_cleared=not cloud_coverage_above_threshold,
    )


def _options(*, cloud_suppression_enabled: bool = True) -> ClimateOptions:
    """Intermediate-temperature climate options with cloud suppression on."""
    return ClimateOptions(
        temp_low=18.0,
        temp_high=26.0,
        temp_switch=False,
        transparent_blind=False,
        temp_summer_outside=None,
        cloud_suppression_enabled=cloud_suppression_enabled,
        winter_close_insulation=False,
    )


def _manager(*, hold_time: int = HOLD) -> CloudSuppressionManager:
    """Build an enabled manager with the reporter's hold time."""
    m = CloudSuppressionManager(logger=MagicMock())
    m.update_config(enabled=True, hold_time_seconds=hold_time)
    return m


def _snapshot(manager: CloudSuppressionManager, readings: ClimateReadings, **kwargs):
    """Snapshot carrying the manager's RESOLVED bool, as the coordinator builds it."""
    kwargs.setdefault("direct_sun_valid", True)
    kwargs.setdefault("calculate_percentage_return", SOLAR_POS)
    kwargs.setdefault("default_position", DEFAULT_POS)
    return make_snapshot(
        climate_mode_enabled=True,
        climate_readings=readings,
        climate_options=_options(),
        cloud_suppression_active=manager.is_suppression_active,
        **kwargs,
    )


def _winner(result) -> str:
    """Name of the handler that matched in the decision trace."""
    return next(step.handler for step in result.decision_trace if step.matched)


def _climate_band() -> PipelineRegistry:
    """Climate → solar → default, WITHOUT ``CloudSuppressionHandler``.

    Priority 60 declines for the whole pending window (that is what "holding"
    means), so on the ENGAGE edge it never masks anything and the registry
    answers identically with or without it. It is left out so the assertions
    speak unambiguously about the priority-50 branch this issue is about — and
    so the RELEASE edge, where priority 60 *is* still commanding the default,
    fails honestly instead of passing for the wrong reason.
    """
    return PipelineRegistry([ClimateHandler(), SolarHandler(), DefaultHandler()])


def _full_pipeline() -> PipelineRegistry:
    """Build the handler chain the reporter's install runs, in priority order."""
    return PipelineRegistry(
        [
            CloudSuppressionHandler(),
            ClimateHandler(),
            SolarHandler(),
            DefaultHandler(),
        ]
    )


# ---------------------------------------------------------------------------
# R1 — the reporter's timeline: a pending hold must not let climate move
# ---------------------------------------------------------------------------


class TestPendingHoldGatesClimate:
    """While the hold is counting down, no handler may act on the crossing."""

    def test_pending_engage_hold_does_not_let_climate_move_the_cover(self) -> None:
        """08:58:25 — lux drops below; the manager arms 600 s, the cover must not move."""
        manager = _manager()
        # Resting state: lux above the threshold, sun tracked at 10 %.
        manager.evaluate(_readings())

        signal = manager.evaluate(_readings(lux_below_threshold=True))
        assert signal == "should_start_timeout"
        assert manager.is_suppression_active is False  # still holding

        result = _climate_band().evaluate(
            _snapshot(manager, _readings(lux_below_threshold=True))
        )
        assert _winner(result) != "climate"
        assert result.climate_strategy is not ClimateStrategy.LOW_LIGHT
        assert result.position != DEFAULT_POS
        assert result.position == SOLAR_POS

    @pytest.mark.asyncio
    async def test_climate_low_light_engages_once_the_hold_expires(self) -> None:
        """The fix DELAYS low light, it does not suppress it — 600 s later it fires."""
        manager = _manager()
        manager.evaluate(_readings())
        manager.evaluate(_readings(lux_below_threshold=True))

        await manager._on_hold_timeout_expired(AsyncMock())
        assert manager.is_suppression_active is True

        result = _climate_band().evaluate(
            _snapshot(manager, _readings(lux_below_threshold=True))
        )
        assert _winner(result) == "climate"
        assert result.climate_strategy is ClimateStrategy.LOW_LIGHT
        assert result.control_method is ControlMethod.DEFAULT
        assert result.position == DEFAULT_POS


# ---------------------------------------------------------------------------
# R2 — the flap property: readings flap, the commanded position must not
# ---------------------------------------------------------------------------


class TestFlapProperty:
    """The reporter's 08:58 → 09:08 sequence through one manager instance."""

    def test_commanded_position_is_constant_across_the_reported_flap(self) -> None:
        """08:58:25 → 08:59:24 → 08:59:42 → 09:08:12 produced 100/10/100/10."""
        manager = _manager()
        registry = _full_pipeline()

        # Resting state before 08:58:25: lux above the threshold.
        manager.evaluate(_readings())

        positions: list[int] = []
        for lux_below in (True, False, True, False):
            readings = _readings(lux_below_threshold=lux_below)
            manager.evaluate(readings)
            positions.append(registry.evaluate(_snapshot(manager, readings)).position)

        assert len(set(positions)) == 1, (
            "A 600 s hold was configured, yet the commanded position flapped "
            f"with the raw lux readings: {positions}"
        )
        assert positions == [SOLAR_POS] * 4


# ---------------------------------------------------------------------------
# R3 — behaviour change (a): the hold is symmetric, it covers the release edge
# ---------------------------------------------------------------------------


class TestReleaseEdgeIsHeldToo:
    """After the fix the hold applies to the RELEASE edge of low light as well.

    This is what the reporter described as correct behaviour (their 08:09 →
    08:19 case) — but until now it only looked correct because the still-pending
    ``CloudSuppressionHandler`` at priority 60 kept commanding the same default
    position. The climate branch itself flipped instantly.
    """

    def test_release_edge_holds_the_default_until_expiry(self) -> None:
        """Lux rises above threshold: sun tracking must wait out the hold."""
        manager = _manager()
        # Resting state: cloudy. The first reading seeds instantly (no restart
        # regression), so suppression is resolved True before the release edge.
        manager.evaluate(_readings(lux_below_threshold=True))
        assert manager.is_suppression_active is True

        # Release edge — the hold arms and the resolved bool stays True.
        assert manager.evaluate(_readings()) == "should_start_timeout"
        assert manager.is_suppression_active is True

        pending = _climate_band().evaluate(_snapshot(manager, _readings()))
        assert _winner(pending) == "climate"
        assert pending.climate_strategy is ClimateStrategy.LOW_LIGHT
        assert pending.position == DEFAULT_POS

    @pytest.mark.asyncio
    async def test_sun_tracking_resumes_after_the_release_hold_expires(self) -> None:
        """Once the 600 s elapse the climate branch releases and solar takes over."""
        manager = _manager()
        manager.evaluate(_readings(lux_below_threshold=True))
        manager.evaluate(_readings())

        await manager._on_hold_timeout_expired(AsyncMock())
        assert manager.is_suppression_active is False

        result = _climate_band().evaluate(_snapshot(manager, _readings()))
        assert _winner(result) == "solar"
        assert result.position == SOLAR_POS


# ---------------------------------------------------------------------------
# R5 — behaviour change (c): low light inherits the per-trigger Schmitt latch
# ---------------------------------------------------------------------------


class TestHysteresisWidening:
    """A configured ``lux_release_threshold`` now latches low light too.

    ``cloud_suppression_active`` carries the per-trigger Schmitt latches; the raw
    ``is_low_light`` predicate only ever read the activate edge. With hold-time 0
    the debounce is a no-op, so this isolates the hysteresis half of the change.
    """

    def test_low_light_stays_engaged_inside_the_release_band(self) -> None:
        manager = _manager(hold_time=0)
        # Below the activate edge → the lux latch engages, resolved True.
        manager.evaluate(_readings(lux_below_threshold=True))
        assert manager.is_suppression_active is True

        # Inside the band: above activate but not yet past the release edge.
        in_band = _readings(lux_below_threshold=False, lux_release_cleared=False)
        assert manager.evaluate(in_band) is None
        assert manager.is_suppression_active is True

        result = _climate_band().evaluate(_snapshot(manager, in_band))
        assert _winner(result) == "climate"
        assert result.climate_strategy is ClimateStrategy.LOW_LIGHT
        assert result.position == DEFAULT_POS


# ---------------------------------------------------------------------------
# R9 — the restart guard: a reload in cloudy weather must not hold for 600 s
# ---------------------------------------------------------------------------


class TestStartupSeed:
    """The first observation after construction/reload is not a *change*.

    Without the seed, a reload during cloudy weather leaves the resolved bool
    False for the whole hold time. That was harmless while only the cloud handler
    read it — the unsmoothed low-light sibling covered the gap. Gating low light
    on the same bool turns it into 600 s of sun tracking after every restart in
    cloudy weather, which would be a worse bug than the one being fixed.
    """

    def test_first_ever_reading_commits_immediately(self) -> None:
        manager = _manager()
        cloudy = _readings(lux_below_threshold=True)

        assert manager.evaluate(cloudy) is None
        assert manager.is_suppression_active is True
        assert manager.is_timeout_running is False

        result = _full_pipeline().evaluate(_snapshot(manager, cloudy))
        assert _winner(result) == "cloud_suppression"
        assert result.position == DEFAULT_POS

    def test_first_ever_sunny_reading_records_no_change_event(self) -> None:
        """Seeding must not synthesise a ``cloud_suppression_changed`` event."""
        buffer = MagicMock()
        manager = CloudSuppressionManager(logger=MagicMock(), event_buffer=buffer)
        manager.update_config(enabled=True, hold_time_seconds=HOLD)

        assert manager.evaluate(_readings()) is None
        assert manager.is_suppression_active is False
        buffer.record.assert_not_called()

    def test_the_second_transition_still_honours_the_hold(self) -> None:
        """Only the FIRST observation is seeded; real changes still debounce."""
        manager = _manager()
        manager.evaluate(_readings())

        assert manager.evaluate(_readings(lux_below_threshold=True)) == (
            "should_start_timeout"
        )
        assert manager.is_suppression_active is False
