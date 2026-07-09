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

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from collections.abc import Callable

from .const import (
    CONF_END_OF_WINDOW_POS,
    CONF_MAX_COVERAGE_STEPS,
    CONF_MINIMIZE_MOVEMENTS,
    CONF_RETURN_SUNSET,
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


# Closure that projects a cover-type's non-primary axes at one forecast step.
# Mirrors the ``cover_factory`` decoupling: the pure sample loop stays free of
# ``config_service``/``options``/policy plumbing and is trivially stub-testable.
#   (position, sol_azi, sol_elev, t) -> {axis_name: value}
# ``t`` is passed for signature symmetry / future time-dependent axes even
# though venetian tilt is a pure function of the sun geometry + position.
SecondaryAxisFactory = Callable[[int, float, float, datetime], dict[str, int]]


@dataclass(frozen=True, slots=True)
class ForecastSample:
    """One (time, position [+ secondary axes]) point on the forecast strip.

    ``position`` is the primary-axis target (the stable wire contract older
    cards read). ``axes`` carries any non-primary axis projections for
    multi-axis covers (venetian tilt today), keyed by ``CoverAxis.name`` —
    empty for single-axis covers so the serialized shape is unchanged (#724).
    """

    t: datetime
    position: int
    handler: str  # "solar" when direct sun is valid at t, else "default"
    axes: dict[str, int] = field(default_factory=dict)


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
                {
                    "t": s.t.isoformat(),
                    "position": s.position,
                    "handler": s.handler,
                    # Spread the secondary-axis map additively (#724): empty for
                    # single-axis covers → the pre-#724 keys only.
                    **s.axes,
                }
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
    end_of_window_pos: int | None = None,
    end_of_window_time: datetime | None = None,
    secondary_axis_factory: SecondaryAxisFactory | None = None,
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

    ``end_of_window_pos`` / ``end_of_window_time`` (issue #625) project the
    optional end-of-window position into the rest-of-day strip. When both are
    set, samples at/after ``end_of_window_time`` get the end-of-window position
    until astral sunset, then hand off to the astral sunset position — the same
    two-phase rule the live path uses (delegated to ``compute_effective_default``
    per sample). ``None``/``None`` (default) preserves today's behavior.

    ``secondary_axis_factory`` (issue #724) projects a multi-axis cover's
    non-primary axes (venetian tilt) alongside each *solar* sample. ``None``
    (default) — or a single-axis cover — leaves every sample's ``axes`` empty.
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
        end_of_window_pos=end_of_window_pos,
        end_of_window_time=end_of_window_time,
        secondary_axis_factory=secondary_axis_factory,
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
    end_of_window_pos: int | None = None,
    end_of_window_time: datetime | None = None,
    secondary_axis_factory: SecondaryAxisFactory | None = None,
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
    For the same reason the operational *start*-time window is not modeled,
    so ``compute_effective_default`` is called without ``window_explicitly_started``
    (defaults False) — the night position is governed purely by the
    astronomical sunset/sunrise window at each sample time. The operational
    *end* time IS modeled when ``end_of_window_time`` is supplied (issue #625):
    samples at/after it apply the end-of-window position via the same
    two-phase astral handoff the live path uses.
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
            # Secondary-axis projection (#724) runs on solar samples only —
            # mirroring the live path, where tilt is meaningful only when the
            # solar engine drives the position with direct sun on the window.
            axes: dict[str, int] = {}
            if secondary_axis_factory is not None:
                axes = secondary_axis_factory(pos, azi, ele, t)
            samples.append(
                ForecastSample(t=t, position=pos, handler="solar", axes=axes)
            )
        else:
            # Sunset-aware effective default at this sample's projected time,
            # then the same limit treatment the live default branch applies.
            # End-of-window (issue #625): this future sample is "window-closed"
            # once it is at/after the resolved window-end. The two-phase astral
            # handoff is automatic inside compute_effective_default (it evaluates
            # after_sunset against eval_time=t), so no parallel branch is needed.
            eow_active = (
                end_of_window_pos is not None
                and end_of_window_time is not None
                and t >= end_of_window_time
            )
            eff_default, is_sunset = compute_effective_default(
                config.h_def,
                config.sunset_pos,
                sun_data,
                config.sunset_off,
                config.sunrise_off,
                eval_time=t,
                end_of_window_pos=end_of_window_pos,
                end_of_window_active=eow_active,
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

    # End-of-window position (issue #625): gate on CONF_RETURN_SUNSET exactly
    # like the live path (when the toggle is off the live cover never applies the
    # end-of-window position, so the forecast must match). Resolve the operating
    # window-end from the time manager (None → no end configured → feature inert)
    # and normalize its tz so the pure walker can compare it against the tz-aware
    # sun_data.times grid.
    eow_pos: int | None = None
    eow_time: datetime | None = None
    if options.get(CONF_RETURN_SUNSET):
        eow_pos = options.get(CONF_END_OF_WINDOW_POS)
        if eow_pos is not None:
            end_time = coord._time_mgr.end_time  # noqa: SLF001
            if end_time is not None:
                if end_time.tzinfo is None:
                    end_time = end_time.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
                eow_time = end_time
            else:
                # No end time configured → the feature cannot fire.
                eow_pos = None

    # Read once so the value the primitives quantize with is the same one the
    # secondary-axis closure hands the policy hook (no drift between axes).
    minimize_movements = bool(
        options.get(CONF_MINIMIZE_MOVEMENTS, DEFAULT_MINIMIZE_MOVEMENTS)
    )
    max_coverage_steps = int(
        options.get(CONF_MAX_COVERAGE_STEPS, DEFAULT_MAX_COVERAGE_STEPS)
    )

    # Secondary-axis projection (#724): build the closure from the polymorphic
    # policy hook — the shim never branches on the cover type. Single-axis
    # policies inherit the base no-op returning ``{}``; venetian projects tilt.
    def make_secondary_axes(
        position: int, azi: float, ele: float, t: datetime
    ) -> dict[str, int]:
        return coord._policy.forecast_secondary_axes(  # noqa: SLF001
            position=position,
            logger=coord.logger,
            sol_azi=azi,
            sol_elev=ele,
            sun_data=sun_data,
            config=config,
            config_service=coord._config_service,  # noqa: SLF001
            options=options,
            minimize_movements=minimize_movements,
            max_coverage_steps=max_coverage_steps,
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
        minimize_movements=minimize_movements,
        max_coverage_steps=max_coverage_steps,
        floor_active=not all_positionable,
        end_of_window_pos=eow_pos,
        end_of_window_time=eow_time,
        secondary_axis_factory=make_secondary_axes,
    )
