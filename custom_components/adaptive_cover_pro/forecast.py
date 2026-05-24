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

from .const import CONF_DEFAULT_HEIGHT, DEFAULT_DEFAULT_HEIGHT, SUN_DATA_STEP_SECONDS

if TYPE_CHECKING:
    from .coordinator import AdaptiveDataUpdateCoordinator
    from .engine.covers.base import AdaptiveGeneralCover
    from .sun import SunData


# Forecast sampling cadence. 15-minute steps over a 12-hour window is dense
# enough for the dashboard strip to read smoothly and cheap enough that the
# computation finishes in well under a second on a Pi 4.
FORECAST_STEP_MINUTES = 15
FORECAST_WINDOW_HOURS = 12

# Event kinds emitted on the forecast.
EVENT_SUNRISE = "sunrise"
EVENT_SUNSET = "sunset"
EVENT_FOV_ENTER = "fov_enter"
EVENT_FOV_EXIT = "fov_exit"


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
    default_position: int,
    now: datetime,
    step_minutes: int = FORECAST_STEP_MINUTES,
    window_hours: int = FORECAST_WINDOW_HOURS,
) -> Forecast:
    """Compute the forecast for one cover.

    ``cover_factory`` is a closure that builds a cover engine for an
    arbitrary (sol_azi, sol_elev) pair; the caller is responsible for
    passing the same configuration / sun_data the live cover uses.
    Decoupling the factory from this helper keeps the function pure and
    trivially testable with a stub cover.
    """
    samples = _build_samples(
        sun_data=sun_data,
        cover_factory=cover_factory,
        default_position=default_position,
        now=now,
        step_minutes=step_minutes,
        window_hours=window_hours,
    )
    events = _build_events(sun_data=sun_data, samples=samples)
    return Forecast(samples=tuple(samples), events=tuple(events))


def _build_samples(
    *,
    sun_data: SunData,
    cover_factory: Callable[[float, float], AdaptiveGeneralCover],
    default_position: int,
    now: datetime,
    step_minutes: int,
    window_hours: int,
) -> list[ForecastSample]:
    """Walk the sun_data table at *step_minutes* cadence for *window_hours*."""
    times = list(sun_data.times)
    azis = list(sun_data.solar_azimuth)
    eles = list(sun_data.solar_elevation)
    if not times:
        return []
    horizon = now + timedelta(hours=window_hours)
    step = timedelta(minutes=step_minutes)

    samples: list[ForecastSample] = []
    t = now
    while t <= horizon:
        idx = _nearest_index(times, t)
        if idx is None:
            t += step
            continue
        azi = float(azis[idx])
        ele = float(eles[idx])
        cover = cover_factory(azi, ele)
        if cover.direct_sun_valid:
            samples.append(
                ForecastSample(
                    t=t, position=int(cover.calculate_percentage()), handler="solar"
                )
            )
        else:
            samples.append(
                ForecastSample(t=t, position=int(default_position), handler="default")
            )
        t += step
    return samples


def _build_events(
    *, sun_data: SunData, samples: list[ForecastSample]
) -> list[ForecastEvent]:
    """Sunrise/sunset come from SunData; FOV transitions come from the samples."""
    events: list[ForecastEvent] = []
    sunrise = sun_data.sunrise()
    sunset = sun_data.sunset()
    if sunrise is not None:
        events.append(ForecastEvent(t=sunrise, kind=EVENT_SUNRISE, label="Sunrise"))
    if sunset is not None:
        events.append(ForecastEvent(t=sunset, kind=EVENT_SUNSET, label="Sunset"))

    # FOV transitions: walk samples, emit an event when handler switches.
    prev_handler: str | None = None
    for sample in samples:
        if prev_handler is None:
            prev_handler = sample.handler
            continue
        if sample.handler == prev_handler:
            continue
        if sample.handler == "solar":
            events.append(
                ForecastEvent(t=sample.t, kind=EVENT_FOV_ENTER, label="Sun enters FOV")
            )
        else:
            events.append(
                ForecastEvent(t=sample.t, kind=EVENT_FOV_EXIT, label="Sun exits FOV")
            )
        prev_handler = sample.handler

    return sorted(events, key=lambda e: e.t)


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

    return build_forecast(
        sun_data=sun_data,
        cover_factory=make_cover,
        default_position=int(options.get(CONF_DEFAULT_HEIGHT, DEFAULT_DEFAULT_HEIGHT)),
        now=dt_util.now(),
    )
