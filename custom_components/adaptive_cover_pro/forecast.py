"""Forecast helpers: today's sun-vs-window timeline for the companion card.

Walks the per-coordinator solar position table that ``SunData`` already
computes for the current day and emits a coarse-grained series of
(timestamp, position) samples plus the boundary events the dashboard
needs (sunrise, sunset, FOV entry, FOV exit).

Only solar tracking is projected forward — the other handlers in the
pipeline (manual override, motion, weather safety, custom positions)
depend on inherently real-time inputs and would mislead a forecast if
naively held at their current state. Holding the geometry constant and
walking the sun gives the user the answer to the question that matters
most for a tile dashboard: *when will this window get direct sun next,
and roughly where will the cover sit through the rest of the day?*
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from collections.abc import Callable

from .const import (
    CONF_MAX_COVERAGE_STEPS,
    CONF_MINIMIZE_MOVEMENTS,
    DEFAULT_MAX_COVERAGE_STEPS,
    DEFAULT_MINIMIZE_MOVEMENTS,
    EVENT_FOV_ENTER,
    EVENT_FOV_EXIT,
    EVENT_SUNRISE,
    EVENT_SUNSET,
    FORECAST_STEP_MINUTES,
    SUN_DATA_STEP_SECONDS,
)
from .helpers import compute_effective_default
from .pipeline.helpers import (
    default_position_with_limits,
    solar_position_from_geometry,
)

if TYPE_CHECKING:
    from .config_types import CoverConfig
    from .coordinator import AdaptiveDataUpdateCoordinator
    from .cover_types.base import CoverTypePolicy
    from .engine.covers.base import AdaptiveGeneralCover
    from .sun import SunData


@dataclass(frozen=True, slots=True)
class ForecastSample:
    """One (time, position) pair on the forecast strip."""

    t: datetime
    position: int
    handler: str  # "solar" when direct sun is valid at t, else "default"


@dataclass(frozen=True, slots=True)
class ForecastEvent:
    """A boundary event on the forecast timeline."""

    t: datetime
    kind: str
    label: str


@dataclass(frozen=True, slots=True)
class Forecast:
    """Result of :func:`build_forecast` — samples + events for one cover."""

    samples: tuple[ForecastSample, ...]
    events: tuple[ForecastEvent, ...]

    def to_attrs(self) -> dict[str, list[dict]]:
        """Serialize to the wire format the diagnostic sensor exposes.

        Times become ISO 8601 strings so the Lovelace card can parse them
        without a special date type.
        """
        return {
            "forecast": [
                {"t": s.t.isoformat(), "position": s.position, "handler": s.handler}
                for s in self.samples
            ],
            "events": [
                {"t": e.t.isoformat(), "kind": e.kind, "label": e.label}
                for e in self.events
            ],
        }


def build_forecast(
    *,
    sun_data: SunData,
    cover_factory: Callable[[float, float], AdaptiveGeneralCover],
    config: CoverConfig,
    policy: CoverTypePolicy | None = None,
    now: datetime,
    step_minutes: int = FORECAST_STEP_MINUTES,
    minimize_movements: bool = False,
    max_coverage_steps: int = 1,
    floor_active: bool = True,
) -> Forecast:
    """Compute the forecast for one cover.

    Walks the full local calendar day (00:00 → 24:00) using the solar position
    table already stored in *sun_data*, so the companion card's elevation chart
    and sample strip share the same time axis.

    Each sample's position is computed through the **same** shared primitives
    the live pipeline uses (``solar_position_from_geometry`` /
    ``default_position_with_limits`` in :mod:`pipeline.helpers`), so the
    forecast strip matches what the cover is actually commanded to — including
    min/max position limits, the 1 % floor, movement minimization, and the
    sunset-aware effective default. *config* and *policy* supply everything
    those primitives need.

    ``cover_factory`` is a closure that builds a cover engine for an
    arbitrary (sol_azi, sol_elev) pair; the caller is responsible for
    passing the same configuration / sun_data the live cover uses.
    Decoupling the factory from this helper keeps the function pure and
    trivially testable with a stub cover.

    ``now`` is retained on the signature for caller context (e.g. tests
    anchoring time, scripts passing wall-clock time) and for future
    use — the samples deliberately cover the full day regardless of ``now``.
    """
    samples = _build_samples(
        sun_data=sun_data,
        cover_factory=cover_factory,
        config=config,
        policy=policy,
        step_minutes=step_minutes,
        minimize_movements=minimize_movements,
        max_coverage_steps=max_coverage_steps,
        floor_active=floor_active,
    )
    events = _build_events(
        sun_data=sun_data, cover_factory=cover_factory, samples=samples
    )
    return Forecast(samples=tuple(samples), events=tuple(events))


def _build_samples(
    *,
    sun_data: SunData,
    cover_factory: Callable[[float, float], AdaptiveGeneralCover],
    config: CoverConfig,
    policy: CoverTypePolicy | None = None,
    step_minutes: int,
    minimize_movements: bool = False,
    max_coverage_steps: int = 1,
    floor_active: bool = True,
) -> list[ForecastSample]:
    """Walk the sun_data table at *step_minutes* cadence over the full calendar day.

    Uses ``times[0]`` (local midnight 00:00) as the loop start and
    ``times[-1]`` (next midnight 24:00) as the loop end, so the sample
    strip always covers the same 24-hour window as the companion card's
    elevation chart regardless of what time ``build_forecast`` is called.

    Each sample routes through the same ``pipeline.helpers`` primitives the live
    pipeline uses, so positions are identical to runtime. The effective default
    (and whether the sunset position is active) is recomputed at *each sample's*
    time via :func:`compute_effective_default`, mirroring the live snapshot
    builder rather than holding a static default. Note: the forecast projects
    solar tracking whenever the sun is in the FOV regardless of the
    ``enable_sun_tracking`` toggle — the card's purpose is to show where the
    cover *would* sit, so that mode gate is deliberately not applied here.
    For the same reason the operational start/end-time window is not modeled,
    so ``compute_effective_default`` is called without ``window_explicitly_started``
    (defaults False) — the night position is governed purely by the
    astronomical sunset/sunrise window at each sample time.
    """
    times = list(sun_data.times)
    azis = list(sun_data.solar_azimuth)
    eles = list(sun_data.solar_elevation)
    if not times:
        return []
    day_start = times[0]
    horizon = times[-1]
    step = timedelta(minutes=step_minutes)

    samples: list[ForecastSample] = []
    t = day_start
    while t <= horizon:
        idx = _nearest_index(times, t)
        if idx is None:
            t += step
            continue
        azi = float(azis[idx])
        ele = float(eles[idx])
        cover = cover_factory(azi, ele)
        # Evaluate the cover's time-dependent gates (sunset/sunrise offset) at
        # *this sample's* time, not wall-clock now — otherwise a forecast
        # recomputed after sunset marks the whole projected day as suppressed
        # and every sample collapses to the default position (issue #516).
        cover.eval_time = t
        if cover.direct_sun_valid:
            pos = solar_position_from_geometry(
                cover,
                config,
                minimize_movements=minimize_movements,
                max_coverage_steps=max_coverage_steps,
                policy=policy,
                floor_active=floor_active,
            )
            samples.append(ForecastSample(t=t, position=pos, handler="solar"))
        else:
            # Sunset-aware effective default at this sample's projected time,
            # then the same limit treatment the live default branch applies.
            eff_default, is_sunset = compute_effective_default(
                config.h_def,
                config.sunset_pos,
                sun_data,
                config.sunset_off,
                config.sunrise_off,
                eval_time=t,
            )
            pos = default_position_with_limits(
                eff_default, config, is_sunset_active=is_sunset
            )
            samples.append(ForecastSample(t=t, position=pos, handler="default"))
        t += step
    return samples


def _build_events(
    *,
    sun_data: SunData,
    cover_factory: Callable[[float, float], AdaptiveGeneralCover],
    samples: list[ForecastSample],
) -> list[ForecastEvent]:
    """Sunrise/sunset come from SunData; FOV transitions come from the samples.

    FOV-enter/exit timestamps are refined from the coarse forecast cadence
    (default 15 min) down to SunData's native 5-min grid by scanning the
    grid points between the two samples that bracket the handler change —
    otherwise the marker can lag the visible cover-position drop by up to
    one full sample step.
    """
    events: list[ForecastEvent] = []
    sunrise = sun_data.sunrise()
    sunset = sun_data.sunset()
    if sunrise is not None:
        events.append(ForecastEvent(t=sunrise, kind=EVENT_SUNRISE, label="Sunrise"))
    if sunset is not None:
        events.append(ForecastEvent(t=sunset, kind=EVENT_SUNSET, label="Sunset"))
    # Forward-looking event so the sensor's "next event" state stays a real
    # timestamp late in the evening once today's events are all in the past,
    # instead of resolving to None / Unknown (issue #516).
    next_sunrise = sun_data.next_sunrise()
    if next_sunrise is not None:
        events.append(
            ForecastEvent(t=next_sunrise, kind=EVENT_SUNRISE, label="Sunrise")
        )

    prev_sample: ForecastSample | None = None
    for sample in samples:
        if prev_sample is None:
            prev_sample = sample
            continue
        if sample.handler == prev_sample.handler:
            prev_sample = sample
            continue
        target_valid = sample.handler == "solar"
        crossing = _refine_fov_crossing(
            sun_data=sun_data,
            cover_factory=cover_factory,
            t_before=prev_sample.t,
            t_after=sample.t,
            target_valid=target_valid,
        )
        t_event = crossing if crossing is not None else sample.t
        if target_valid:
            events.append(
                ForecastEvent(t=t_event, kind=EVENT_FOV_ENTER, label="Sun enters FOV")
            )
        else:
            events.append(
                ForecastEvent(t=t_event, kind=EVENT_FOV_EXIT, label="Sun exits FOV")
            )
        prev_sample = sample

    return sorted(events, key=lambda e: e.t)


def _refine_fov_crossing(
    *,
    sun_data: SunData,
    cover_factory: Callable[[float, float], AdaptiveGeneralCover],
    t_before: datetime,
    t_after: datetime,
    target_valid: bool,
) -> datetime | None:
    """First grid time in [t_before, t_after] where direct_sun_valid matches target_valid.

    Used to refine FOV-enter/exit event timestamps from the 15-min sample
    cadence down to SunData's native 5-min grid; returns None when no
    match is found.
    """
    times = list(sun_data.times)
    if not times:
        return None
    azis = sun_data.solar_azimuth
    eles = sun_data.solar_elevation
    start_idx = _nearest_index(times, t_before)
    end_idx = _nearest_index(times, t_after)
    if start_idx is None or end_idx is None:
        return None
    for i in range(start_idx, min(end_idx, len(times) - 1) + 1):
        cover = cover_factory(float(azis[i]), float(eles[i]))
        cover.eval_time = times[i]
        if bool(cover.direct_sun_valid) == target_valid:
            return times[i]
    return None


def _nearest_index(
    times: list[datetime], target: datetime, step_seconds: int = SUN_DATA_STEP_SECONDS
) -> int | None:
    """Index of the time in *times* closest to *target* (O(1) arithmetic lookup).

    ``times`` is expected to be the fixed 5-minute grid from ``SunData.times``.
    ``step_seconds`` is parameterised so this stays correct if the cadence changes.
    Returns None when *times* is empty.
    """
    if not times:
        return None
    if target.tzinfo is None and times[0].tzinfo is not None:
        target = target.replace(tzinfo=times[0].tzinfo)
    delta = (target - times[0]).total_seconds()
    return max(0, min(len(times) - 1, round(delta / step_seconds)))


def build_forecast_for_coord(coord: AdaptiveDataUpdateCoordinator) -> Forecast:
    """Coordinator shim around :func:`build_forecast`.

    Reads the coordinator's policy, sun provider, config service, and options
    to drive the pure helper. Kept thin so unit tests can exercise the pure
    function directly with stubs.

    Executor-safe: always invoked from
    :meth:`AdaptiveDataUpdateCoordinator.async_recompute_forecast` via
    :func:`hass.async_add_executor_job` so the ~289-call astral walk × 49-step
    sampling loop never blocks the event loop (issue #437).
    """
    from homeassistant.util import dt as dt_util

    options = coord.config_entry.options
    sun_data = coord._sun_provider.create_sun_data(  # noqa: SLF001
        coord.hass.config.time_zone
    )
    config = coord._config_service.get_common_data(options)  # noqa: SLF001

    def make_cover(azi: float, ele: float) -> AdaptiveGeneralCover:
        return coord._policy.build_calc_engine(  # noqa: SLF001
            logger=coord.logger,
            sol_azi=azi,
            sol_elev=ele,
            sun_data=sun_data,
            config=config,
            config_service=coord._config_service,  # noqa: SLF001
            options=options,
        )

    # Sun-tracking floor rollup (#569): mirror the live snapshot builder so the
    # forecast strip matches what the cover is actually commanded to. The floor
    # is off only when every bound entity supports the policy's position axis;
    # missing caps (snapshot not built yet) default to floor active.
    caps = getattr(coord._snapshot, "cover_capabilities", None) or {}  # noqa: SLF001
    all_positionable = bool(caps) and all(
        coord._policy.position_axis_supported(c) for c in caps.values()  # noqa: SLF001
    )

    # The coverage direction the primitives need is read from the policy's
    # primary axis (single source of truth), so the shim passes the policy
    # straight through rather than precomputing full_coverage_at_zero.
    return build_forecast(
        sun_data=sun_data,
        cover_factory=make_cover,
        config=config,
        policy=coord._policy,  # noqa: SLF001
        now=dt_util.now(),
        minimize_movements=bool(
            options.get(CONF_MINIMIZE_MOVEMENTS, DEFAULT_MINIMIZE_MOVEMENTS)
        ),
        max_coverage_steps=int(
            options.get(CONF_MAX_COVERAGE_STEPS, DEFAULT_MAX_COVERAGE_STEPS)
        ),
        floor_active=not all_positionable,
    )
