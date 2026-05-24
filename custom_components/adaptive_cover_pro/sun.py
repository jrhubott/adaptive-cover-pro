"""Fetch sun data.

`SunData` caches the day's solar timeline (`pd.date_range` plus the
per-tick azimuth/elevation lists from astral) so a single property read
doesn't pay the full ~289-call astral walk every time. The cache is
keyed on `(timezone, lat, lon, elevation, date.today())` — it
self-invalidates at midnight without any explicit refresh.

This shape exists because `position_forecast` (and any future
forecast-style consumer) reads ``solar_azimuth`` / ``solar_elevation``
in a tight loop. The plain `@property` form recomputed everything on
every access, with a nested ``for _i in self.times`` clause that
re-evaluated ``self.times`` on every iteration — pathological inside
a 49-step forecast walker. See issue #437.

The module-level ``_DAY_CACHE`` shares one fill across all ``SunData``
instances at the same location (e.g. 10 covers at the same address).
The fill runs inside ``hass.async_add_executor_job`` (off the event
loop), so the cache is guarded by a ``threading.Lock``. See issue #441.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd


# ---------------------------------------------------------------------------
# Module-level day cache — shared across all SunData instances
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _SunDayData:
    """Module-cache value: one day's computed times/azimuth/elevation for a location."""

    times: pd.DatetimeIndex
    azi: list[float]
    ele: list[float]


_CACHE_LOCK: threading.Lock = threading.Lock()
_DAY_CACHE: dict[tuple, _SunDayData] = {}


def _cache_key(
    timezone: str, location, elevation: float
) -> tuple[str, float, float, float, date]:
    """Module-level cache key — date.today() component self-invalidates at midnight."""
    return (timezone, location.latitude, location.longitude, elevation, date.today())


class SunData:
    """Access local sun data.

    Properties are computed lazily on first access per day and memoised
    on the instance until ``date.today()`` advances. ``functools.cached_property``
    would lock to construction day, so we maintain an explicit
    `_cache_day` key instead.
    """

    def __init__(self, timezone, location, elevation) -> None:  # noqa: D107
        self.location = location  # astral.location.Location
        self.elevation = elevation
        self.timezone = timezone
        # Day-keyed memoisation. None on first access; populated by
        # `_ensure_today()` and invalidated when `date.today()` rolls over.
        self._cache_day: date | None = None
        self._cache_times: pd.DatetimeIndex | None = None
        self._cache_azi: list[float] | None = None
        self._cache_ele: list[float] | None = None

    def _ensure_today(self) -> None:
        """Refresh the cached timeline + solar angles when the day rolls over.

        Three-tier lookup:
        1. Fast path — instance fields already populated for today.
        2. Module lookup — dict GET (no lock); assign from cached _SunDayData.
        3. Miss — acquire lock, double-check, build timeline, purge stale
           entries, store, release; then assign from the new entry.
        """
        today = date.today()
        # Tier 1: instance fast path.
        if self._cache_day == today and self._cache_times is not None:
            return

        key = _cache_key(self.timezone, self.location, self.elevation)

        # Tier 2: module cache hit (no lock — CPython GIL + immutable value safe).
        cached = _DAY_CACHE.get(key)
        if cached is not None:
            self._cache_day = today
            self._cache_times = cached.times
            self._cache_azi = cached.azi
            self._cache_ele = cached.ele
            return

        # Tier 3: fill under lock.
        with _CACHE_LOCK:
            # Double-checked locking: another thread may have filled while we waited.
            cached = _DAY_CACHE.get(key)
            if cached is None:
                end_date = today + timedelta(days=1)
                times = pd.date_range(
                    start=today,
                    end=end_date,
                    freq="5min",
                    tz=self.timezone,
                    name="time",
                )
                azi_list = [
                    self.location.solar_azimuth(t, self.elevation) for t in times
                ]
                ele_list = [
                    self.location.solar_elevation(t, self.elevation) for t in times
                ]
                cached = _SunDayData(times=times, azi=azi_list, ele=ele_list)
                # Purge stale (non-today) entries to keep memory bounded.
                stale = [k for k in _DAY_CACHE if k[4] != today]
                for k in stale:
                    del _DAY_CACHE[k]
                _DAY_CACHE[key] = cached

        self._cache_day = today
        self._cache_times = cached.times
        self._cache_azi = cached.azi
        self._cache_ele = cached.ele

    @property
    def times(self) -> pd.DatetimeIndex:
        """Today's 5-minute timeline (cached per day)."""
        self._ensure_today()
        assert self._cache_times is not None  # for type narrowing
        return self._cache_times

    @property
    def solar_azimuth(self) -> list[float]:
        """Solar azimuth at each entry in :attr:`times` (cached per day)."""
        self._ensure_today()
        assert self._cache_azi is not None
        return self._cache_azi

    @property
    def solar_elevation(self) -> list[float]:
        """Solar elevation at each entry in :attr:`times` (cached per day)."""
        self._ensure_today()
        assert self._cache_ele is not None
        return self._cache_ele

    def sunset(self) -> datetime:
        """Fetch sunset time.

        Returns a far-future sentinel (midnight tonight) at polar latitudes
        during midnight sun when astral raises ValueError.
        """
        try:
            return self.location.sunset(date.today(), local=False)
        except (ValueError, AttributeError):
            # Polar midnight sun: sun never sets — treat as end of day
            today = date.today()
            return datetime(
                today.year, today.month, today.day, 23, 59, 59
            )  # noqa: DTZ001

    def sunrise(self) -> datetime:
        """Fetch sunrise time.

        Returns an early-morning sentinel (00:01 today) at polar latitudes
        during polar night when astral raises ValueError.
        """
        try:
            return self.location.sunrise(date.today(), local=False)
        except (ValueError, AttributeError):
            # Polar night: sun never rises — treat as very early morning
            today = date.today()
            return datetime(today.year, today.month, today.day, 0, 1, 0)  # noqa: DTZ001
