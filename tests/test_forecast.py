"""Tests for forecast.build_forecast — pure pure-function level coverage."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, UTC
from unittest.mock import MagicMock, patch

import pytest

import custom_components.adaptive_cover_pro.sun as _sun_mod

from custom_components.adaptive_cover_pro.forecast import (
    EVENT_FOV_ENTER,
    EVENT_FOV_EXIT,
    EVENT_SUNRISE,
    EVENT_SUNSET,
    FORECAST_STEP_MINUTES,
    Forecast,
    ForecastEvent,
    ForecastSample,
    build_forecast,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 1, 6, 0, tzinfo=UTC)
# Midnight anchor: real SunData.times start at local 00:00 on the current day.
_DAY_START = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)


def _make_sun_data(
    *,
    n_samples: int = 289,
    step_minutes: int = 5,
    azi_at: float = 180.0,
    ele_at: float = 30.0,
    sunrise: datetime | None = None,
    sunset: datetime | None = None,
    next_sunrise: datetime | None = None,
):
    """Build a minimal SunData stand-in for forecast tests.

    Produces a constant-sun timeline for *n_samples* steps of *step_minutes*
    starting at _DAY_START (local midnight), matching real SunData semantics
    (00:00 → 24:00 inclusive at 5-min cadence = 289 points).  Tests that need
    a varying sun pattern can patch the azimuth/elevation lists after construction.

    ``next_sunrise`` defaults to None so the forward-looking forecast event
    (issue #516) is omitted unless a test opts in — keeps event-count
    assertions on the today-only events stable.
    """
    times = [_DAY_START + timedelta(minutes=i * step_minutes) for i in range(n_samples)]
    sd = MagicMock()
    sd.times = times
    sd.solar_azimuth = [azi_at] * n_samples
    sd.solar_elevation = [ele_at] * n_samples
    sd.sunrise = MagicMock(return_value=sunrise)
    sd.sunset = MagicMock(return_value=sunset)
    sd.next_sunrise = MagicMock(return_value=next_sunrise)
    return sd


def _make_cover_factory(*, solar_valid: bool, percentage: int = 40):
    """Build a cover_factory closure used by build_forecast.

    The returned cover's direct_sun_valid always returns *solar_valid*; its
    calculate_percentage() always returns *percentage*.  Tests that want
    per-timestamp variation pass a custom factory.
    """

    def factory(azi: float, ele: float):  # noqa: ARG001
        cover = MagicMock()
        cover.direct_sun_valid = solar_valid
        cover.calculate_percentage = MagicMock(return_value=percentage)
        return cover

    return factory


def _make_config(**overrides):
    """Build a minimal CoverConfig, overriding individual fields by name.

    Starts from ``CoverConfig.from_options({})`` (all sane defaults:
    ``min_pos=0``, ``max_pos=100``, ``sunset_pos=None``, etc.) so tests only
    name the fields they care about — e.g. ``_make_config(h_def=10)`` or
    ``_make_config(min_pos=30, min_pos_sun_tracking=40)``.
    """
    import dataclasses

    from custom_components.adaptive_cover_pro.config_types import CoverConfig

    return dataclasses.replace(CoverConfig.from_options({}), **overrides)


def _make_policy(*, open_blocks_sun: bool = False):
    """Stub CoverTypePolicy exposing the single axis attribute the solar
    primitive reads for coverage direction (``full_coverage_at_zero``).
    """
    policy = MagicMock()
    axis = MagicMock()
    axis.open_blocks_sun = open_blocks_sun
    policy.axes = [axis]
    return policy


# ---------------------------------------------------------------------------
# Sample series shape
# ---------------------------------------------------------------------------


class TestBuildForecastSamples:
    """build_forecast emits one sample per tick over the configured window."""

    def test_default_cadence_emits_samples_for_full_calendar_day(self):
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            config=_make_config(h_def=10),
            now=_NOW,
        )
        # Full calendar day (00:00 → 24:00) at 15-minute steps inclusive = 24 * 60 / 15 + 1 = 97.
        expected = (24 * 60 // FORECAST_STEP_MINUTES) + 1
        assert len(f.samples) == expected
        # All samples carry the configured default since solar isn't valid.
        assert all(s.position == 10 and s.handler == "default" for s in f.samples)

    def test_solar_valid_samples_use_calculated_percentage(self):
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=True, percentage=55),
            config=_make_config(h_def=10),
            now=_NOW,
        )
        assert all(s.position == 55 and s.handler == "solar" for s in f.samples)

    def test_minimize_movements_quantizes_solar_samples(self):
        """N=2 steps snap a 55% solar sample to the 50% coverage level.

        Matches the live solar branch exactly (quantize → floor-at-1 → limits),
        with coverage direction taken from the policy's primary axis.
        """
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=True, percentage=55),
            config=_make_config(h_def=10),
            policy=_make_policy(open_blocks_sun=False),  # full_coverage_at_zero
            now=_NOW,
            minimize_movements=True,
            max_coverage_steps=2,
        )
        assert all(s.position == 50 and s.handler == "solar" for s in f.samples)

    def test_minimize_movements_without_policy_is_noop(self):
        """No policy → no quantization (matches the live `policy is None` guard)."""
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=True, percentage=55),
            config=_make_config(h_def=10),
            now=_NOW,
            minimize_movements=True,
            max_coverage_steps=1,
        )
        assert all(s.position == 55 and s.handler == "solar" for s in f.samples)

    def test_minimize_movements_does_not_touch_default_samples(self):
        """Default (non-solar) samples are never quantized."""
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            config=_make_config(h_def=10),
            policy=_make_policy(open_blocks_sun=False),
            now=_NOW,
            minimize_movements=True,
            max_coverage_steps=1,
        )
        assert all(s.position == 10 and s.handler == "default" for s in f.samples)

    def test_custom_step_covers_full_day_proportionally(self):
        sd = _make_sun_data(n_samples=289, step_minutes=5)
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            config=_make_config(),
            now=_NOW,
            step_minutes=30,
        )
        # Full calendar day (00:00 → 24:00) at 30-minute steps = 24 * 60 / 30 + 1 = 49.
        assert len(f.samples) == (24 * 60 // 30) + 1

    def test_full_day_forecast_starts_at_midnight_ends_at_midnight(self):
        """First sample is at local midnight, last sample is at the next midnight."""
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            config=_make_config(),
            now=_NOW,
        )
        assert len(f.samples) > 0, "forecast produced no samples"
        # First sample matches the first time in sun_data (midnight).
        assert (
            f.samples[0].t == _DAY_START
        ), f"first sample at {f.samples[0].t}, expected {_DAY_START}"
        # Last sample is at next midnight: _DAY_START + 24 h.
        expected_last = _DAY_START + timedelta(hours=24)
        assert (
            f.samples[-1].t == expected_last
        ), f"last sample at {f.samples[-1].t}, expected {expected_last}"

    def test_empty_sun_data_returns_empty_samples_and_events(self):
        sd = _make_sun_data(n_samples=0)
        sd.times = []
        sd.solar_azimuth = []
        sd.solar_elevation = []
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            config=_make_config(),
            now=_NOW,
        )
        assert f.samples == ()
        assert f.events == ()


class _EvalTimeCover:
    """Fake cover whose direct_sun_valid depends on the per-sample eval_time.

    Mirrors the real engine's time-dependent sunset gate (issue #516): solar
    is only valid between 10:00 and 16:00 of the *sample's own* timestamp. If
    the forecast failed to set ``eval_time`` per sample (the bug), reading
    ``direct_sun_valid`` would raise on ``None.hour`` — so this also guards
    against a regression to wall-clock evaluation.
    """

    def __init__(self) -> None:
        self.eval_time = None

    @property
    def direct_sun_valid(self) -> bool:
        return 10 <= self.eval_time.hour < 16

    def calculate_percentage(self) -> int:
        return 40


class TestForecastEvaluatesPerSampleTime:
    """Regression for #516: per-sample sunset gate, not wall-clock now."""

    def test_midday_samples_track_even_when_built_in_the_evening(self):
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=lambda _azi, _ele: _EvalTimeCover(),
            config=_make_config(h_def=100),
            now=datetime(2026, 6, 1, 23, 0, tzinfo=UTC),  # built late in the day
        )
        # The curve is NOT flat: midday samples track the sun...
        solar = [s for s in f.samples if s.handler == "solar"]
        assert (
            solar
        ), "no solar samples — forecast collapsed to all-default (the #516 bug)"
        assert all(10 <= s.t.hour < 16 for s in solar)
        assert all(s.position == 40 for s in solar)
        # ...while off-hours sit at the default.
        assert any(s.handler == "default" and s.position == 100 for s in f.samples)

    def test_forward_sunrise_event_is_appended(self):
        next_rise = _DAY_START + timedelta(days=1, hours=5, minutes=40)
        sd = _make_sun_data(
            sunrise=_DAY_START + timedelta(hours=5),
            sunset=_DAY_START + timedelta(hours=20),
            next_sunrise=next_rise,
        )
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            config=_make_config(),
            now=_NOW,
        )
        sunrise_events = [e for e in f.events if e.kind == EVENT_SUNRISE]
        # Today's sunrise plus tomorrow's forward-looking one.
        assert len(sunrise_events) == 2
        assert sunrise_events[-1].t == next_rise


# ---------------------------------------------------------------------------
# Event detection
# ---------------------------------------------------------------------------


class TestBuildForecastEvents:
    """Sunrise / sunset / FOV transitions land in the events list."""

    def test_sunrise_and_sunset_emitted_when_present(self):
        sunrise = _NOW + timedelta(hours=2)
        sunset = _NOW + timedelta(hours=10)
        sd = _make_sun_data(sunrise=sunrise, sunset=sunset)
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            config=_make_config(),
            now=_NOW,
        )
        kinds = [e.kind for e in f.events]
        assert EVENT_SUNRISE in kinds
        assert EVENT_SUNSET in kinds

    def test_sunrise_sunset_skipped_when_none_returned(self):
        sd = _make_sun_data(sunrise=None, sunset=None)
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            config=_make_config(),
            now=_NOW,
        )
        assert EVENT_SUNRISE not in [e.kind for e in f.events]
        assert EVENT_SUNSET not in [e.kind for e in f.events]

    def test_handler_switch_emits_fov_enter_and_exit(self):
        """Cover-factory swings direct_sun_valid mid-window → enter + exit events."""
        sd = _make_sun_data()
        # solar valid during minutes 30-90 (i.e. samples 2-6 at 15-min step).
        valid_window_start = _NOW + timedelta(minutes=30)
        valid_window_end = _NOW + timedelta(minutes=90)

        def factory(_azi, _ele):
            cover = MagicMock()
            cover.calculate_percentage = MagicMock(return_value=50)
            # Mutated per call via closure to time-of-call check is awkward; the
            # forecast walker passes (azi, ele) at *target* time, so we need a
            # different signal — use a counter tracking call index.
            return cover

        # Simpler: drive the switch by providing per-tick solar validity via a
        # cover_factory that toggles based on the call counter.
        call_state = {"calls": 0}
        toggle_points = [2, 6]  # sample indices where direct_sun_valid flips

        def toggling_factory(_azi, _ele):
            idx = call_state["calls"]
            call_state["calls"] += 1
            cover = MagicMock()
            cover.direct_sun_valid = toggle_points[0] <= idx < toggle_points[1]
            cover.calculate_percentage = MagicMock(return_value=50)
            return cover

        # Silence linters: factory + tick variables are intentionally unused.
        _ = factory
        _ = valid_window_start
        _ = valid_window_end

        f = build_forecast(
            sun_data=sd,
            cover_factory=toggling_factory,
            config=_make_config(),
            now=_NOW,
        )
        kinds = [e.kind for e in f.events]
        assert EVENT_FOV_ENTER in kinds
        assert EVENT_FOV_EXIT in kinds

    def test_fov_enter_event_refines_to_actual_crossing_not_next_sample(self):
        """FOV-enter event lands on the true crossing time, not the first solar sample.

        Pre-fix the event was placed at the first sample where handler='solar',
        which lags the real FOV crossing by up to one full sample step (15 min).
        Post-fix the event time is the SunData grid point where
        ``direct_sun_valid`` actually flips True — accurate to the 5-min grid.
        """
        # Full calendar day at 5-min step = 289 samples (00:00 → 24:00).
        n_samples = 24 * 60 // 5 + 1
        sd = _make_sun_data(n_samples=n_samples, step_minutes=5)
        # Encode "time" into azimuth so a factory ignoring ele can decide by azi.
        sd.solar_azimuth = [float(i) for i in range(n_samples)]

        # Crossing index 20 = 100 min from _DAY_START (00:00); 15-min samples bracket
        # it at 90 min (azi 18) and 105 min (azi 21) — so a naive enter event
        # would land at 105 min, but the true crossing is at 100 min.
        crossing_idx = 20
        crossing_time = _DAY_START + timedelta(minutes=crossing_idx * 5)

        def factory(azi, _ele):
            cover = MagicMock()
            cover.direct_sun_valid = azi >= crossing_idx
            cover.calculate_percentage = MagicMock(return_value=50)
            return cover

        f = build_forecast(
            sun_data=sd, cover_factory=factory, config=_make_config(), now=_NOW
        )

        enter_events = [e for e in f.events if e.kind == EVENT_FOV_ENTER]
        assert len(enter_events) == 1
        assert (
            enter_events[0].t == crossing_time
        ), f"FOV-enter at {enter_events[0].t}, expected {crossing_time}"

    def test_events_returned_sorted_by_time(self):
        sd = _make_sun_data(
            sunrise=_NOW + timedelta(hours=4),
            sunset=_NOW + timedelta(hours=10),
        )
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            config=_make_config(),
            now=_NOW,
        )
        times = [e.t for e in f.events]
        assert times == sorted(times)


# ---------------------------------------------------------------------------
# Wire-format serialization
# ---------------------------------------------------------------------------


class TestForecastToAttrs:
    """to_attrs() produces a stable wire shape for the diagnostic sensor."""

    def test_samples_serialise_with_iso_timestamps(self):
        f = Forecast(
            samples=(ForecastSample(t=_NOW, position=42, handler="solar"),),
            events=(),
        )
        attrs = f.to_attrs()
        assert attrs["forecast"] == [
            {"t": _NOW.isoformat(), "position": 42, "handler": "solar"}
        ]

    def test_events_serialise_with_iso_timestamps(self):
        f = Forecast(
            samples=(),
            events=(ForecastEvent(t=_NOW, kind=EVENT_SUNRISE, label="Sunrise"),),
        )
        attrs = f.to_attrs()
        assert attrs["events"] == [
            {"t": _NOW.isoformat(), "kind": "sunrise", "label": "Sunrise"}
        ]


class TestForecastSecondaryAxes:
    """ForecastSample carries a generic secondary-axis map (issue #724).

    The primary ``position`` scalar is unchanged; multi-axis covers (venetian)
    project their non-primary axes into ``axes`` keyed by ``CoverAxis.name``.
    ``to_attrs()`` spreads the map additively so single-axis covers emit a
    byte-identical wire shape.
    """

    def test_sample_defaults_to_no_secondary_axes(self):
        assert ForecastSample(t=_NOW, position=40, handler="default").axes == {}

    def test_to_attrs_spreads_secondary_axes_additively(self):
        f = Forecast(
            samples=(
                ForecastSample(t=_NOW, position=55, handler="solar", axes={"tilt": 30}),
                ForecastSample(t=_NOW, position=40, handler="default"),
            ),
            events=(),
        )
        attrs = f.to_attrs()
        # Non-empty axes → the secondary key is spread into the sample dict.
        assert attrs["forecast"][0] == {
            "t": _NOW.isoformat(),
            "position": 55,
            "handler": "solar",
            "tilt": 30,
        }
        # Empty axes → no extra keys (byte-identical to the pre-#724 shape).
        assert attrs["forecast"][1] == {
            "t": _NOW.isoformat(),
            "position": 40,
            "handler": "default",
        }

    def test_solar_samples_get_secondary_axes_from_factory(self):
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=True, percentage=55),
            config=_make_config(),
            policy=_make_policy(),
            now=_NOW,
            secondary_axis_factory=lambda pos, azi, ele, t: {"tilt": 30},
        )
        solar = [s for s in f.samples if s.handler == "solar"]
        assert solar  # at least one solar sample was produced
        assert all(s.axes == {"tilt": 30} for s in solar)

    def test_default_samples_have_no_secondary_axes(self):
        # A stub factory that would raise if the default branch ever calls it.
        def _boom(pos, azi, ele, t):  # noqa: ARG001
            raise AssertionError("factory must not run on default samples")

        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            config=_make_config(),
            policy=_make_policy(),
            now=_NOW,
            secondary_axis_factory=_boom,
        )
        assert f.samples
        assert all(s.axes == {} for s in f.samples)

    def test_no_factory_yields_empty_axes(self):
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=True, percentage=55),
            config=_make_config(),
            policy=_make_policy(),
            now=_NOW,
        )
        assert f.samples
        assert all(s.axes == {} for s in f.samples)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("default", [0, 50, 100])
def test_default_position_round_trips_through_samples(default: int):
    """The configured default height appears verbatim in non-solar samples.

    With no min/max limits configured and no sunset position, the default
    branch passes the effective default through unchanged.
    """
    sd = _make_sun_data()
    f = build_forecast(
        sun_data=sd,
        cover_factory=_make_cover_factory(solar_valid=False),
        config=_make_config(h_def=default),
        now=_NOW,
    )
    assert {s.position for s in f.samples} == {default}


# ---------------------------------------------------------------------------
# Runtime parity: limits, floor-at-1, rounding, sunset — the forecast must
# match what the live pipeline commands, because it routes every sample
# through the same pipeline.helpers primitives.
# ---------------------------------------------------------------------------


def _solar_cover_factory(percentage):
    """Cover factory: direct sun valid, calculate_percentage() → *percentage*."""

    def factory(_azi, _ele):
        cover = MagicMock()
        cover.direct_sun_valid = True
        cover.calculate_percentage = MagicMock(return_value=percentage)
        return cover

    return factory


class TestForecastLimits:
    """min/max position settings flow into the forecast (the reported gap)."""

    def test_min_position_floors_solar_samples(self):
        """min_position=30 with a 10% geometry → solar samples floored to 30."""
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_solar_cover_factory(10),
            config=_make_config(min_pos=30),
            now=_NOW,
        )
        assert all(s.handler == "solar" and s.position == 30 for s in f.samples)

    def test_max_position_caps_solar_samples(self):
        """max_position=70 with a 90% geometry → solar samples capped at 70."""
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_solar_cover_factory(90),
            config=_make_config(max_pos=70),
            now=_NOW,
        )
        assert all(s.handler == "solar" and s.position == 70 for s in f.samples)

    def test_sun_only_min_not_applied_to_default_samples(self):
        """enable_min_position (sun-only) → floor skipped when sun is out of FOV."""
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            config=_make_config(h_def=5, min_pos=30, min_pos_sun_only=True),
            now=_NOW,
        )
        # Sun-only floor must NOT raise the default to 30 — it stays 5.
        assert all(s.handler == "default" and s.position == 5 for s in f.samples)

    def test_always_enforced_min_applied_to_default_samples(self):
        """Always-enforce min (sun_only=False) raises default samples to the floor."""
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            config=_make_config(h_def=5, min_pos=30, min_pos_sun_only=False),
            now=_NOW,
        )
        assert all(s.position == 30 for s in f.samples)

    def test_sun_tracking_floor_overrides_min_on_solar_samples(self):
        """min_position_sun_tracking overrides min_position while sun-tracking."""
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_solar_cover_factory(10),
            config=_make_config(min_pos=20, min_pos_sun_tracking=40),
            now=_NOW,
        )
        assert all(s.position == 40 for s in f.samples)


class TestForecastFloorAndRound:
    """Solar samples are floored at 1% and rounded, matching the live branch."""

    def test_solar_floored_at_one_percent(self):
        """A 0% geometry never closes an open/close blind — floors to 1."""
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_solar_cover_factory(0),
            config=_make_config(),
            now=_NOW,
            floor_active=True,
        )
        assert all(s.handler == "solar" and s.position == 1 for s in f.samples)

    def test_positionable_solar_reaches_zero(self):
        """A 0% geometry reaches a true 0% when the instance is positionable (#569)."""
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_solar_cover_factory(0),
            config=_make_config(),
            now=_NOW,
            floor_active=False,
        )
        assert all(s.handler == "solar" and s.position == 0 for s in f.samples)

    def test_solar_percentage_is_rounded_not_truncated(self):
        """54.6% rounds to 55 (the live branch rounds; the old forecast truncated)."""
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_solar_cover_factory(54.6),
            config=_make_config(),
            now=_NOW,
        )
        assert all(s.position == 55 for s in f.samples)


class TestForecastSunsetParity:
    """Default samples use the sunset position inside the sunset window."""

    def test_sunset_position_used_and_not_clamped_in_window(self):
        sunrise = _DAY_START + timedelta(hours=6)
        sunset = _DAY_START + timedelta(hours=20)
        sd = _make_sun_data(sunrise=sunrise, sunset=sunset)
        # sunset_pos=20 sits BELOW an always-enforced min_pos=30: the sunset
        # window must bypass the clamp (#128), while daytime defaults still clamp.
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            config=_make_config(
                h_def=80, sunset_pos=20, min_pos=30, min_pos_sun_only=False
            ),
            now=_NOW,
        )
        pos_at = {s.t: s.position for s in f.samples}
        # Deep night (before sunrise / after sunset): sunset position, unclamped.
        assert pos_at[_DAY_START + timedelta(hours=2)] == 20
        assert pos_at[_DAY_START + timedelta(hours=22)] == 20
        # Midday (outside the sunset window): daytime default, clamped to min.
        assert pos_at[_DAY_START + timedelta(hours=12)] == 80
        assert {s.position for s in f.samples} == {20, 80}


class TestForecastEndOfWindowPosition:
    """Forecast reflects the end-of-window position with the two-phase handoff.

    issue #625: between window-end and astral sunset the projected default is
    the end-of-window position (phase 1); after astral sunset it hands off to
    the astral sunset position (phase 2). With no sunset_pos the eow position
    persists across the evening.
    """

    def test_two_phase_handoff_in_forecast(self):
        sunrise = _DAY_START + timedelta(hours=6)
        sunset = _DAY_START + timedelta(hours=20)
        sd = _make_sun_data(sunrise=sunrise, sunset=sunset)
        eow_time = _DAY_START + timedelta(hours=19, minutes=30)  # window-end 19:30
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            config=_make_config(h_def=80, sunset_pos=20),
            now=_NOW,
            end_of_window_pos=0,
            end_of_window_time=eow_time,
        )
        pos_at = {s.t: s.position for s in f.samples}
        # Before window-end → daytime default.
        assert pos_at[_DAY_START + timedelta(hours=12)] == 80
        # After window-end but before astral sunset → end-of-window (phase 1).
        assert pos_at[_DAY_START + timedelta(hours=19, minutes=45)] == 0
        # After astral sunset → astral sunset position (phase 2 handoff).
        assert pos_at[_DAY_START + timedelta(hours=22)] == 20

    def test_no_sunset_pos_persists_across_evening(self):
        sunrise = _DAY_START + timedelta(hours=6)
        sunset = _DAY_START + timedelta(hours=20)
        sd = _make_sun_data(sunrise=sunrise, sunset=sunset)
        eow_time = _DAY_START + timedelta(hours=19, minutes=30)
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            config=_make_config(h_def=80, sunset_pos=None),
            now=_NOW,
            end_of_window_pos=0,
            end_of_window_time=eow_time,
        )
        pos_at = {s.t: s.position for s in f.samples}
        assert pos_at[_DAY_START + timedelta(hours=12)] == 80
        assert pos_at[_DAY_START + timedelta(hours=19, minutes=45)] == 0
        assert pos_at[_DAY_START + timedelta(hours=22)] == 0

    def test_omitting_eow_kwargs_is_no_regression(self):
        sunrise = _DAY_START + timedelta(hours=6)
        sunset = _DAY_START + timedelta(hours=20)
        sd = _make_sun_data(sunrise=sunrise, sunset=sunset)
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            config=_make_config(h_def=80, sunset_pos=20),
            now=_NOW,
        )
        pos_at = {s.t: s.position for s in f.samples}
        # Without the feature: daytime default before sunset, sunset pos after.
        assert pos_at[_DAY_START + timedelta(hours=19, minutes=45)] == 80
        assert pos_at[_DAY_START + timedelta(hours=22)] == 20


class TestForecastMatchesLivePipeline:
    """Anti-drift lock: forecast samples == the live snapshot-based helpers."""

    def test_solar_sample_equals_compute_solar_position(self):
        from custom_components.adaptive_cover_pro.pipeline.helpers import (
            compute_solar_position,
        )

        from tests.conftest import make_snapshot_for_cover

        config = _make_config(min_pos=30, max_pos=90)
        # Live snapshot over an equivalent cover at the same geometry/config.
        live_cover = MagicMock()
        live_cover.config = config
        live_cover.calculate_percentage = MagicMock(return_value=10)
        snapshot = make_snapshot_for_cover(live_cover)
        live = compute_solar_position(snapshot)

        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_solar_cover_factory(10),
            config=config,
            now=_NOW,
        )
        assert all(s.position == live for s in f.samples)

    def test_default_sample_equals_compute_default_position(self):
        from custom_components.adaptive_cover_pro.pipeline.helpers import (
            compute_default_position,
        )

        from tests.conftest import make_snapshot_for_cover

        config = _make_config(h_def=5, min_pos=30, min_pos_sun_only=False)
        live_cover = MagicMock()
        live_cover.config = config
        snapshot = make_snapshot_for_cover(live_cover, default_position=5)
        live = compute_default_position(snapshot)

        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            config=config,
            now=_NOW,
        )
        assert all(s.position == live for s in f.samples)


# ---------------------------------------------------------------------------
# Coordinator-level forecast caching (issue #437)
#
# These tests pin the contract for the executor-offloaded forecast field
# on `AdaptiveDataUpdateCoordinator`. The sensor reads from
# `coordinator.data.position_forecast`; the coordinator recomputes the
# forecast on a slow cadence inside an executor job, never inline on the
# event loop, and never on every refresh.
# ---------------------------------------------------------------------------


class _AsyncCallRecorder:
    """Awaitable wrapper that records the function passed to `async_add_executor_job`.

    The HA stub returned by `hass.async_add_executor_job(fn, *args)` is
    awaitable and resolves to `fn(*args)`. We mimic the same shape so the
    coordinator's recompute helper sees a real future.
    """

    def __init__(self) -> None:
        self.calls: list = []

    async def __call__(self, fn, *args):
        self.calls.append((fn, args))
        return fn(*args)


def _make_coord_for_forecast_helper():
    """Minimal coordinator stand-in for `async_recompute_forecast` tests.

    Builds an actual `AdaptiveDataUpdateCoordinator` would require a full
    HA stack — overkill for testing the executor-offload helper. Instead
    we exercise the method as an unbound function on a mock instance,
    matching the pattern used in `test_coordinator_integration.py`.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveCoverData,
        AdaptiveDataUpdateCoordinator,
    )

    coord = MagicMock(spec=AdaptiveDataUpdateCoordinator)
    coord.hass = MagicMock()
    coord.hass.async_add_executor_job = _AsyncCallRecorder()
    coord.data = AdaptiveCoverData(climate_mode_toggle=False, states={}, attributes={})
    coord._position_forecast = None
    return coord


@pytest.mark.asyncio
@pytest.mark.unit
async def test_async_recompute_forecast_runs_in_executor(monkeypatch):
    """`async_recompute_forecast` offloads `build_forecast_for_coord` to the executor.

    Issue #437: the forecast must NOT block the event loop. The
    coordinator's helper has to route the synchronous compute through
    `hass.async_add_executor_job` and stash the result on
    `coordinator.data.position_forecast`.
    """
    from custom_components.adaptive_cover_pro import coordinator as coord_mod

    sentinel = MagicMock(name="Forecast")
    build_mock = MagicMock(return_value=sentinel)
    monkeypatch.setattr(
        coord_mod, "build_forecast_for_coord", build_mock, raising=False
    )
    # Also patch the source module so the lazy import inside
    # `async_recompute_forecast` resolves to the same mock.
    monkeypatch.setattr(
        "custom_components.adaptive_cover_pro.forecast.build_forecast_for_coord",
        build_mock,
    )

    coord = _make_coord_for_forecast_helper()

    await coord_mod.AdaptiveDataUpdateCoordinator.async_recompute_forecast(coord)

    # Executor was called exactly once with the build helper.
    assert len(coord.hass.async_add_executor_job.calls) == 1
    fn, args = coord.hass.async_add_executor_job.calls[0]
    assert fn is build_mock
    assert args == (coord,)
    # Result lands on coordinator.data.
    assert coord.data.position_forecast is sentinel
    # Listeners are notified so the sensor publishes the fresh forecast
    # immediately, not on the next coordinator update cycle.
    coord.async_update_listeners.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_async_recompute_forecast_skips_listener_notify_when_data_missing(
    monkeypatch,
):
    """Pre-first-refresh: no listener notify (there's no data to publish yet)."""
    from custom_components.adaptive_cover_pro import coordinator as coord_mod

    monkeypatch.setattr(
        "custom_components.adaptive_cover_pro.forecast.build_forecast_for_coord",
        MagicMock(return_value=MagicMock(name="Forecast")),
    )

    coord = _make_coord_for_forecast_helper()
    coord.data = None

    await coord_mod.AdaptiveDataUpdateCoordinator.async_recompute_forecast(coord)
    coord.async_update_listeners.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_async_recompute_forecast_swallows_exceptions(monkeypatch):
    """A failing forecast computation must NOT propagate — sensor degrades to None.

    The pre-refactor sensor wrapped the build call in try/except for the
    same reason. Coordinator-side defensive degradation preserves that
    behaviour now that the call site has moved.
    """
    from custom_components.adaptive_cover_pro import coordinator as coord_mod

    def _boom(_coord):
        raise RuntimeError("forecast failed")

    monkeypatch.setattr(
        "custom_components.adaptive_cover_pro.forecast.build_forecast_for_coord",
        _boom,
    )

    coord = _make_coord_for_forecast_helper()

    # No exception escapes.
    await coord_mod.AdaptiveDataUpdateCoordinator.async_recompute_forecast(coord)
    assert coord.data.position_forecast is None


class TestForecastShimEndOfWindow:
    """build_forecast_for_coord threads the eow option through, gated on return_sunset."""

    def _stub_coord(self, *, options, end_time):
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = MagicMock(spec=AdaptiveDataUpdateCoordinator)
        coord.config_entry = MagicMock()
        coord.config_entry.options = options
        coord.logger = MagicMock()
        coord.hass = MagicMock()
        coord.hass.config.time_zone = "UTC"
        sun_data = _make_sun_data(
            sunrise=_DAY_START + timedelta(hours=6),
            sunset=_DAY_START + timedelta(hours=20),
        )
        coord._sun_provider = MagicMock()
        coord._sun_provider.create_sun_data = MagicMock(return_value=sun_data)
        coord._config_service = MagicMock()
        coord._config_service.get_common_data = MagicMock(
            return_value=_make_config(h_def=80, sunset_pos=20)
        )
        coord._policy = MagicMock()
        coord._policy.position_axis_supported = MagicMock(return_value=False)
        coord._snapshot = MagicMock()
        coord._snapshot.cover_capabilities = {}
        coord._time_mgr = MagicMock()
        coord._time_mgr.end_time = end_time
        return coord

    def _run(self, coord):
        from custom_components.adaptive_cover_pro import forecast as fc_mod

        spy = MagicMock(return_value=MagicMock(name="Forecast"))
        with patch.object(fc_mod, "build_forecast", spy):
            fc_mod.build_forecast_for_coord(coord)
        return spy

    def test_passes_eow_pos_and_time_when_gate_on(self):
        from custom_components.adaptive_cover_pro.const import (
            CONF_END_OF_WINDOW_POS,
            CONF_RETURN_SUNSET,
        )

        end_time = (_DAY_START + timedelta(hours=19, minutes=30)).replace(tzinfo=None)
        coord = self._stub_coord(
            options={CONF_RETURN_SUNSET: True, CONF_END_OF_WINDOW_POS: 0},
            end_time=end_time,
        )
        spy = self._run(coord)
        _, kwargs = spy.call_args
        assert kwargs["end_of_window_pos"] == 0
        assert kwargs["end_of_window_time"] is not None
        # tz attached so it's comparable to the tz-aware sun_data.times grid.
        assert kwargs["end_of_window_time"].tzinfo is not None

    def test_gate_off_passes_none(self):
        from custom_components.adaptive_cover_pro.const import (
            CONF_END_OF_WINDOW_POS,
            CONF_RETURN_SUNSET,
        )

        end_time = (_DAY_START + timedelta(hours=19, minutes=30)).replace(tzinfo=None)
        coord = self._stub_coord(
            options={CONF_RETURN_SUNSET: False, CONF_END_OF_WINDOW_POS: 0},
            end_time=end_time,
        )
        spy = self._run(coord)
        _, kwargs = spy.call_args
        assert kwargs["end_of_window_pos"] is None

    def test_no_end_time_makes_feature_inert(self):
        from custom_components.adaptive_cover_pro.const import (
            CONF_END_OF_WINDOW_POS,
            CONF_RETURN_SUNSET,
        )

        coord = self._stub_coord(
            options={CONF_RETURN_SUNSET: True, CONF_END_OF_WINDOW_POS: 0},
            end_time=None,
        )
        spy = self._run(coord)
        _, kwargs = spy.call_args
        assert kwargs["end_of_window_pos"] is None
        assert kwargs["end_of_window_time"] is None


class TestForecastShimSecondaryAxes:
    """build_forecast_for_coord builds the secondary-axis factory from the policy hook (#724).

    The shim never asks "is this venetian" — it wires ``policy.forecast_secondary_axes``
    into a closure and passes it as ``secondary_axis_factory``. Single-axis
    policies inherit the base no-op, so the same closure returns ``{}``.
    """

    def _stub_coord(self, *, policy):
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = MagicMock(spec=AdaptiveDataUpdateCoordinator)
        coord.config_entry = MagicMock()
        coord.config_entry.options = {}
        coord.logger = MagicMock()
        coord.hass = MagicMock()
        coord.hass.config.time_zone = "UTC"
        sun_data = _make_sun_data()
        coord._sun_provider = MagicMock()
        coord._sun_provider.create_sun_data = MagicMock(return_value=sun_data)
        coord._config_service = MagicMock()
        coord._config_service.get_common_data = MagicMock(return_value=_make_config())
        coord._policy = policy
        coord._snapshot = MagicMock()
        coord._snapshot.cover_capabilities = {}
        coord._time_mgr = MagicMock()
        coord._time_mgr.end_time = None
        return coord, sun_data

    def _run(self, coord):
        from custom_components.adaptive_cover_pro import forecast as fc_mod

        spy = MagicMock(return_value=MagicMock(name="Forecast"))
        with patch.object(fc_mod, "build_forecast", spy):
            fc_mod.build_forecast_for_coord(coord)
        return spy

    def test_shim_passes_secondary_axis_factory_for_venetian(self):
        policy = MagicMock()
        policy.position_axis_supported = MagicMock(return_value=False)
        policy.forecast_secondary_axes = MagicMock(return_value={"tilt": 30})
        coord, sun_data = self._stub_coord(policy=policy)
        spy = self._run(coord)
        _, kwargs = spy.call_args
        factory = kwargs["secondary_axis_factory"]
        assert factory is not None
        # Calling the closure routes straight to policy.forecast_secondary_axes.
        assert factory(55, 180.0, 45.0, _NOW) == {"tilt": 30}
        policy.forecast_secondary_axes.assert_called_once()
        call_kwargs = policy.forecast_secondary_axes.call_args.kwargs
        assert call_kwargs["position"] == 55
        assert call_kwargs["sol_azi"] == 180.0
        assert call_kwargs["sol_elev"] == 45.0
        assert call_kwargs["sun_data"] is sun_data
        assert call_kwargs["config_service"] is coord._config_service
        assert "minimize_movements" in call_kwargs
        assert "max_coverage_steps" in call_kwargs

    def test_shim_single_axis_policy_still_passes_factory_returning_empty(self):
        policy = MagicMock()
        policy.position_axis_supported = MagicMock(return_value=False)
        policy.forecast_secondary_axes = MagicMock(return_value={})
        coord, _ = self._stub_coord(policy=policy)
        spy = self._run(coord)
        _, kwargs = spy.call_args
        factory = kwargs["secondary_axis_factory"]
        assert factory is not None
        assert factory(40, 0.0, 0.0, _NOW) == {}


# ---------------------------------------------------------------------------
# Phase D: forecast is scheduled, not recomputed on every refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_start_forecast_scheduler_kicks_off_initial_background_task(monkeypatch):
    """The coordinator schedules one background forecast compute on setup.

    Subsequent `await coord.async_refresh()` calls must NOT trigger a
    recompute — the periodic timer is the only writer (issue #437).
    """
    from custom_components.adaptive_cover_pro import coordinator as coord_mod

    coord = MagicMock(spec=coord_mod.AdaptiveDataUpdateCoordinator)
    coord.hass = MagicMock()
    coord.config_entry = MagicMock()
    coord._forecast_unsub = None

    # Capture background tasks instead of running them.  Close the coroutine
    # passed in to avoid "coroutine was never awaited" warnings — we're
    # asserting on call_count, not on coroutine completion.
    def _capture_bg(_hass, coro, name=None):  # noqa: ARG001
        coro.close()
        return MagicMock(name="task")

    coord.config_entry.async_create_background_task = MagicMock(side_effect=_capture_bg)

    # Capture the wall-clock time-change registration.
    track_calls: list = []

    def _fake_track_time_change(_hass, _cb, **kwargs):
        track_calls.append((_hass, _cb, kwargs))
        return MagicMock(name="unsub")

    monkeypatch.setattr(
        "homeassistant.helpers.event.async_track_time_change",
        _fake_track_time_change,
    )

    coord_mod.AdaptiveDataUpdateCoordinator._start_forecast_scheduler(coord)

    # One initial background task fired via the config-entry helper (NOT
    # hass.async_create_background_task — see coordinator.py for why).
    assert coord.config_entry.async_create_background_task.call_count == 1
    # One wall-clock timer registered.
    assert len(track_calls) == 1
    # Schedule fires at :00, :05, :10, …, :55 — the cron-style equivalent
    # of */5 so every entry's forecast updates in lockstep.
    from custom_components.adaptive_cover_pro.const import (
        FORECAST_RECOMPUTE_INTERVAL_MIN,
    )

    _, _, kwargs = track_calls[0]
    assert list(kwargs["minute"]) == list(range(0, 60, FORECAST_RECOMPUTE_INTERVAL_MIN))
    assert kwargs["second"] == 0
    # Unsub handle stored.
    assert coord._forecast_unsub is not None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_start_forecast_scheduler_is_idempotent(monkeypatch):
    """Calling `_start_forecast_scheduler` twice does NOT register a second timer.

    A reload path could call this twice; we must not leak timers.
    """
    from custom_components.adaptive_cover_pro import coordinator as coord_mod

    coord = MagicMock(spec=coord_mod.AdaptiveDataUpdateCoordinator)
    coord.hass = MagicMock()
    coord.config_entry = MagicMock()
    coord._forecast_unsub = MagicMock(name="existing_unsub")

    def _capture_bg(_hass, coro, name=None):  # noqa: ARG001
        coro.close()
        return MagicMock(name="task")

    coord.config_entry.async_create_background_task = MagicMock(side_effect=_capture_bg)

    track_mock = MagicMock(return_value=MagicMock(name="new_unsub"))
    monkeypatch.setattr(
        "homeassistant.helpers.event.async_track_time_change", track_mock
    )

    coord_mod.AdaptiveDataUpdateCoordinator._start_forecast_scheduler(coord)

    # Nothing scheduled — early return on existing handle.
    assert coord.config_entry.async_create_background_task.call_count == 0
    assert track_mock.call_count == 0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_forecast_scheduler_tick_fires_background_task(monkeypatch):
    """The periodic timer callback launches a background task — not a sync run."""
    from custom_components.adaptive_cover_pro import coordinator as coord_mod

    coord = MagicMock(spec=coord_mod.AdaptiveDataUpdateCoordinator)
    coord.hass = MagicMock()
    coord.config_entry = MagicMock()
    coord._forecast_unsub = None

    def _capture_bg(_hass, coro, name=None):  # noqa: ARG001
        coro.close()
        return MagicMock(name="task")

    coord.config_entry.async_create_background_task = MagicMock(side_effect=_capture_bg)

    captured_cb: list = []

    def _fake_track_time_change(_hass, cb, **_kwargs):
        captured_cb.append(cb)
        return MagicMock(name="unsub")

    monkeypatch.setattr(
        "homeassistant.helpers.event.async_track_time_change",
        _fake_track_time_change,
    )

    coord_mod.AdaptiveDataUpdateCoordinator._start_forecast_scheduler(coord)
    assert len(captured_cb) == 1

    # Tick must be a HA `@callback`, otherwise HA classifies the sync
    # `def` as `HassJobType.Executor` and dispatches it to a worker
    # thread — where `loop.create_task(..., eager_start=True)` raises
    # `RuntimeError: loop is not the running loop` and the recompute
    # silently never happens.
    assert getattr(captured_cb[0], "_hass_callback", False) is True

    # Initial schedule already created one background task.
    initial_count = coord.config_entry.async_create_background_task.call_count
    # Fire two ticks.
    captured_cb[0](datetime.now(UTC))
    captured_cb[0](datetime.now(UTC))
    assert (
        coord.config_entry.async_create_background_task.call_count == initial_count + 2
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_forecast_scheduler_uses_entry_task_helper_not_hass(monkeypatch):
    """Regression: must use `config_entry.async_create_background_task`.

    The hass-level helper let tasks be destroyed before reaching their
    first await when scheduled from a sync timer callback, producing
    "Task was destroyed but it is pending!" in the log and silently
    skipping the 5-min forecast refresh.  The entry-level helper holds
    a hard reference for the lifetime of the entry, which fixes it.
    """
    from custom_components.adaptive_cover_pro import coordinator as coord_mod

    coord = MagicMock(spec=coord_mod.AdaptiveDataUpdateCoordinator)
    coord.hass = MagicMock()
    coord.config_entry = MagicMock()
    coord._forecast_unsub = None

    def _capture_bg(_hass, coro, name=None):  # noqa: ARG001
        coro.close()
        return MagicMock(name="task")

    coord.config_entry.async_create_background_task = MagicMock(side_effect=_capture_bg)
    # Mark the hass helper so we can assert it is NOT used.
    coord.hass.async_create_background_task = MagicMock(name="hass_helper")

    captured_cb: list = []

    def _fake_track_time_change(_hass, cb, **_kwargs):
        captured_cb.append(cb)
        return MagicMock(name="unsub")

    monkeypatch.setattr(
        "homeassistant.helpers.event.async_track_time_change",
        _fake_track_time_change,
    )

    coord_mod.AdaptiveDataUpdateCoordinator._start_forecast_scheduler(coord)
    captured_cb[0](datetime.now(UTC))

    # Initial + one tick = 2 calls, all on the entry helper.
    assert coord.config_entry.async_create_background_task.call_count == 2
    coord.hass.async_create_background_task.assert_not_called()

    # First positional arg passed to the entry helper is hass, per the HA
    # `ConfigEntry.async_create_background_task(hass, target, name=...)` signature.
    for call in coord.config_entry.async_create_background_task.call_args_list:
        args, _kwargs = call
        assert args[0] is coord.hass


# ---------------------------------------------------------------------------
# Phase E: real SunData regression — `build_forecast` must NOT pay the
# pre-fix per-iteration `pd.date_range` cost when given a real SunData.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_forecast_with_real_sun_data_caches_timeline():
    """A real `SunData` only rebuilds `pd.date_range` once per `build_forecast` call.

    Before the issue-#437 fix, `SunData.solar_azimuth` re-ran `pd.date_range`
    on every loop iteration (the nested `for _i in self.times` re-evaluated
    the property). One forecast pass exercised the accessor 49+ times, each
    walking 289 entries — so `pd.date_range` was called in the thousands.
    """
    from unittest.mock import patch

    import pandas as pd

    from custom_components.adaptive_cover_pro.sun import SunData

    location = MagicMock()
    # Real astral returns floats; the value doesn't matter for this test.
    location.solar_azimuth = MagicMock(return_value=180.0)
    location.solar_elevation = MagicMock(return_value=45.0)
    location.sunset = MagicMock(side_effect=ValueError("ignore"))
    location.sunrise = MagicMock(side_effect=ValueError("ignore"))
    sd = SunData(timezone="UTC", location=location, elevation=0)

    with patch(
        "custom_components.adaptive_cover_pro.sun.pd.date_range",
        wraps=pd.date_range,
    ) as spy:
        build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            config=_make_config(h_def=10),
            now=_NOW,
        )
    assert (
        spy.call_count <= 1
    ), f"pd.date_range called {spy.call_count}× during one forecast — expected ≤ 1"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_async_recompute_forecast_handles_missing_data(monkeypatch):
    """If `coordinator.data` is None (pre-first-refresh), the shadow attribute is still set.

    Forecast recompute can be scheduled before the first coordinator
    refresh has populated `self.data`. The helper must tolerate that and
    write to `_position_forecast` so the next `_async_update_data`
    promotes the value into `AdaptiveCoverData`.
    """
    from custom_components.adaptive_cover_pro import coordinator as coord_mod

    sentinel = MagicMock(name="Forecast")
    monkeypatch.setattr(
        "custom_components.adaptive_cover_pro.forecast.build_forecast_for_coord",
        MagicMock(return_value=sentinel),
    )

    coord = _make_coord_for_forecast_helper()
    coord.data = None  # simulate pre-first-refresh

    await coord_mod.AdaptiveDataUpdateCoordinator.async_recompute_forecast(coord)
    assert coord._position_forecast is sentinel


# ---------------------------------------------------------------------------
# Module-level SunData cache (issue #441 — Part 1)
#
# These tests pin the cross-entry shared-cache contract introduced in #441.
# Two SunData instances at the same (timezone, lat, lon, elevation) must
# share a single pd.date_range+astral walk per day; instances at different
# keys must remain independent; the cache self-invalidates at midnight via
# date.today() in the key; and concurrent fills are guarded by a lock.
# ---------------------------------------------------------------------------


class TestNearestIndex:
    """_nearest_index returns correct O(1) arithmetic result for all real SunData grid points."""

    def test_exact_grid_points_match_linear_scan(self):
        """Every 5-min mark in the sun_data timeline gives the same index as the linear scan."""
        from custom_components.adaptive_cover_pro.forecast import _nearest_index
        from datetime import UTC, datetime, timedelta

        origin = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
        times = [origin + timedelta(minutes=5 * i) for i in range(289)]

        for expected_idx, t in enumerate(times):
            result = _nearest_index(times, t)
            assert result == expected_idx, f"idx {expected_idx}: got {result}"

    def test_target_before_start_clamps_to_zero(self):
        from custom_components.adaptive_cover_pro.forecast import _nearest_index
        from datetime import UTC, datetime, timedelta

        origin = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
        times = [origin + timedelta(minutes=5 * i) for i in range(10)]
        assert _nearest_index(times, origin - timedelta(hours=2)) == 0

    def test_target_after_end_clamps_to_last(self):
        from custom_components.adaptive_cover_pro.forecast import _nearest_index
        from datetime import UTC, datetime, timedelta

        origin = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
        times = [origin + timedelta(minutes=5 * i) for i in range(10)]
        assert _nearest_index(times, origin + timedelta(days=1)) == 9

    def test_tz_naive_target_coerces_to_match_tz_aware_list(self):
        from custom_components.adaptive_cover_pro.forecast import _nearest_index
        from datetime import UTC, datetime, timedelta

        origin = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
        times = [origin + timedelta(minutes=5 * i) for i in range(10)]
        naive = datetime(2026, 6, 1, 0, 0)
        result = _nearest_index(times, naive)
        assert result is not None

    def test_empty_times_returns_none(self):
        from custom_components.adaptive_cover_pro.forecast import _nearest_index
        from datetime import UTC, datetime

        assert _nearest_index([], datetime(2026, 6, 1, tzinfo=UTC)) is None

    @pytest.mark.parametrize("offset_minutes", [0, 1, 2, 4, 5, 10, 600, 1440])
    def test_midpoint_rounds_to_nearest(self, offset_minutes):
        from custom_components.adaptive_cover_pro.forecast import _nearest_index
        from datetime import UTC, datetime, timedelta

        origin = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
        times = [origin + timedelta(minutes=5 * i) for i in range(289)]
        target = origin + timedelta(minutes=offset_minutes)
        result = _nearest_index(times, target)
        expected = max(0, min(288, round(offset_minutes / 5)))
        assert result == expected


@pytest.fixture(autouse=True)
def _clear_sun_day_cache():
    """Wipe module-level SunData cache before/after each test."""
    _sun_mod._DAY_CACHE.clear()
    yield
    _sun_mod._DAY_CACHE.clear()


def _make_real_sun_data(
    *, latitude: float = 37.0, longitude: float = -122.0, elevation: float = 0.0
):
    """Build a real SunData instance backed by a mock astral Location."""
    from custom_components.adaptive_cover_pro.sun import SunData

    location = MagicMock()
    location.latitude = latitude
    location.longitude = longitude
    location.solar_azimuth = MagicMock(return_value=180.0)
    location.solar_elevation = MagicMock(return_value=45.0)
    return SunData(timezone="UTC", location=location, elevation=elevation)


class TestSunDataModuleCache:
    """Module-level day-keyed cache shares astral computation across SunData instances."""

    def test_two_instances_same_location_share_one_fill(self):
        """Two SunData at the same key trigger pd.date_range at most once."""
        import pandas as pd

        sd1 = _make_real_sun_data()
        sd2 = _make_real_sun_data()

        with patch(
            "custom_components.adaptive_cover_pro.sun.pd.date_range",
            wraps=pd.date_range,
        ) as spy:
            _ = sd1.times
            _ = sd2.times

        assert spy.call_count <= 1, (
            f"pd.date_range called {spy.call_count}× for two instances at the same "
            "location — expected ≤ 1 (module cache should prevent the second fill)"
        )

    def test_instances_different_elevation_get_independent_fills(self):
        """Two SunData at different elevations each trigger their own fill."""
        import pandas as pd

        sd1 = _make_real_sun_data(elevation=0.0)
        sd2 = _make_real_sun_data(elevation=500.0)

        with patch(
            "custom_components.adaptive_cover_pro.sun.pd.date_range",
            wraps=pd.date_range,
        ) as spy:
            _ = sd1.times
            _ = sd2.times

        assert spy.call_count == 2, (
            f"pd.date_range called {spy.call_count}× — expected exactly 2 for "
            "instances at different elevations (independent cache entries)"
        )

    def test_cache_invalidates_at_midnight(self):
        """After a simulated day-rollover the cache fills again on the new date."""
        import pandas as pd

        sd = _make_real_sun_data()

        with patch(
            "custom_components.adaptive_cover_pro.sun.pd.date_range",
            wraps=pd.date_range,
        ) as spy:
            _ = sd.times  # fills the cache for today

            # Simulate midnight: clear the module cache as a new day would.
            _sun_mod._DAY_CACHE.clear()
            # Reset instance fields so _ensure_today doesn't short-circuit.
            sd._cache_day = None
            sd._cache_times = None
            sd._cache_azi = None
            sd._cache_ele = None

            _ = sd.times  # must fill again

        assert (
            spy.call_count == 2
        ), f"pd.date_range called {spy.call_count}× — expected 2 (one per day)"

    def test_cache_key_helper_exists_and_is_deterministic(self):
        """_cache_key is importable and returns the same tuple on repeated calls."""
        from custom_components.adaptive_cover_pro.sun import _cache_key

        location = MagicMock()
        location.latitude = 37.0
        location.longitude = -122.0

        key1 = _cache_key("UTC", location, 0.0)
        key2 = _cache_key("UTC", location, 0.0)

        assert key1 == key2
        # Key is a tuple with at least 5 elements: tz, lat, lon, elevation, date.
        assert isinstance(key1, tuple)
        assert len(key1) >= 5

    def test_concurrent_fills_produce_single_result(self):
        """Two threads racing on the same key trigger pd.date_range at most once."""
        import pandas as pd

        # We need two SunData with the same key but separate instances so
        # neither has a warm instance cache.
        sd1 = _make_real_sun_data()
        sd2 = _make_real_sun_data()

        results: list[int] = []
        barrier = threading.Barrier(2)

        with patch(
            "custom_components.adaptive_cover_pro.sun.pd.date_range",
            wraps=pd.date_range,
        ) as spy:

            def _fill(sd):
                barrier.wait()  # both threads start at the same moment
                _ = sd.times
                results.append(1)

            t1 = threading.Thread(target=_fill, args=(sd1,))
            t2 = threading.Thread(target=_fill, args=(sd2,))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        assert len(results) == 2  # both threads completed
        assert spy.call_count <= 1, (
            f"pd.date_range called {spy.call_count}× under concurrent access — "
            "expected ≤ 1 (lock should prevent duplicate fills)"
        )
