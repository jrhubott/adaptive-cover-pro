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
    FORECAST_WINDOW_HOURS,
    Forecast,
    ForecastEvent,
    ForecastSample,
    build_forecast,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 1, 6, 0, tzinfo=UTC)


def _make_sun_data(
    *,
    n_samples: int = 96,
    step_minutes: int = 5,
    azi_at: float = 180.0,
    ele_at: float = 30.0,
    sunrise: datetime | None = None,
    sunset: datetime | None = None,
):
    """Build a minimal SunData stand-in for forecast tests.

    Produces a constant-sun timeline for *n_samples* steps of *step_minutes*
    starting at _NOW.  Tests that need a varying sun pattern can patch the
    azimuth/elevation lists after construction.
    """
    times = [_NOW + timedelta(minutes=i * step_minutes) for i in range(n_samples)]
    sd = MagicMock()
    sd.times = times
    sd.solar_azimuth = [azi_at] * n_samples
    sd.solar_elevation = [ele_at] * n_samples
    sd.sunrise = MagicMock(return_value=sunrise)
    sd.sunset = MagicMock(return_value=sunset)
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


# ---------------------------------------------------------------------------
# Sample series shape
# ---------------------------------------------------------------------------


class TestBuildForecastSamples:
    """build_forecast emits one sample per tick over the configured window."""

    def test_default_cadence_emits_step_per_15_minutes_for_12_hours(self):
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            default_position=10,
            now=_NOW,
        )
        # 12 hours of 15-minute steps inclusive at both ends = 12 * 60 / 15 + 1.
        expected = (FORECAST_WINDOW_HOURS * 60 // FORECAST_STEP_MINUTES) + 1
        assert len(f.samples) == expected
        # All samples carry the configured default since solar isn't valid.
        assert all(s.position == 10 and s.handler == "default" for s in f.samples)

    def test_solar_valid_samples_use_calculated_percentage(self):
        sd = _make_sun_data()
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=True, percentage=55),
            default_position=10,
            now=_NOW,
        )
        assert all(s.position == 55 and s.handler == "solar" for s in f.samples)

    def test_custom_step_and_window_produce_proportional_sample_count(self):
        sd = _make_sun_data(n_samples=200, step_minutes=5)
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            default_position=0,
            now=_NOW,
            step_minutes=30,
            window_hours=4,
        )
        assert len(f.samples) == (4 * 60 // 30) + 1

    def test_empty_sun_data_returns_empty_samples_and_events(self):
        sd = _make_sun_data(n_samples=0)
        sd.times = []
        sd.solar_azimuth = []
        sd.solar_elevation = []
        f = build_forecast(
            sun_data=sd,
            cover_factory=_make_cover_factory(solar_valid=False),
            default_position=0,
            now=_NOW,
        )
        assert f.samples == ()
        assert f.events == ()


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
            default_position=0,
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
            default_position=0,
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
            default_position=0,
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
        # 12 hours at 5-min step covers the full forecast window exactly.
        n_samples = 12 * 60 // 5 + 1
        sd = _make_sun_data(n_samples=n_samples, step_minutes=5)
        # Encode "time" into azimuth so a factory ignoring ele can decide by azi.
        sd.solar_azimuth = [float(i) for i in range(n_samples)]

        # Crossing index 20 = 100 min from _NOW; 15-min samples bracket it
        # at 90 min (azi 18) and 105 min (azi 21) — so a naive enter event
        # would land at 105 min, but the true crossing is at 100 min.
        crossing_idx = 20
        crossing_time = _NOW + timedelta(minutes=crossing_idx * 5)

        def factory(azi, _ele):
            cover = MagicMock()
            cover.direct_sun_valid = azi >= crossing_idx
            cover.calculate_percentage = MagicMock(return_value=50)
            return cover

        f = build_forecast(
            sun_data=sd, cover_factory=factory, default_position=0, now=_NOW
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
            default_position=0,
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


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("default", [0, 50, 100])
def test_default_position_round_trips_through_samples(default: int):
    """Whatever default_position we pass appears verbatim in non-solar samples."""
    sd = _make_sun_data()
    f = build_forecast(
        sun_data=sd,
        cover_factory=_make_cover_factory(solar_valid=False),
        default_position=default,
        now=_NOW,
    )
    assert {s.position for s in f.samples} == {default}


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
            default_position=10,
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
