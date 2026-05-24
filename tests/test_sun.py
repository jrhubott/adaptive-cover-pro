"""Tests for `SunData` day-cached property accessors.

Regression guards for issue #437: the original implementation re-ran
`pd.date_range` and the full astral walk on **every** property access,
so a single forecast computation paid ~289 calls per accessor and
``solar_azimuth`` itself paid ~290× per call because the nested
``for _i in self.times`` clause re-evaluated ``self.times`` on every
iteration.

The fix moves the timeline behind a date-keyed memo that invalidates
on day rollover.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from custom_components.adaptive_cover_pro.sun import SunData


def _make_sun_data() -> SunData:
    """Build a SunData backed by a MagicMock astral location.

    The mock returns deterministic azimuth/elevation values so we can
    assert call counts and shapes without depending on the real astral
    library.
    """
    location = MagicMock()
    location.solar_azimuth = MagicMock(return_value=180.0)
    location.solar_elevation = MagicMock(return_value=30.0)
    return SunData(timezone="UTC", location=location, elevation=0)


@pytest.mark.unit
def test_times_cached_within_day():
    """`SunData.times` returns the same object on back-to-back reads.

    Before the fix, every property access re-ran ``pd.date_range``.
    The behavioural assertion is "no work was repeated within the same
    calendar day" — strict identity is the simplest way to express it.
    """
    sd = _make_sun_data()
    first = sd.times
    second = sd.times
    assert first is second


@pytest.mark.unit
def test_solar_azimuth_recomputes_date_range_at_most_once_per_day():
    """Reading `.solar_azimuth` twice + `.times` once must hit `pd.date_range` ≤ 1 time.

    Today's implementation calls `pd.date_range` per property *and* once
    per iteration inside the loop — the call count would be in the
    thousands. After the fix the timeline is cached on first access and
    reused.
    """
    sd = _make_sun_data()
    with patch(
        "custom_components.adaptive_cover_pro.sun.pd.date_range",
        wraps=pd.date_range,
    ) as spy:
        _ = sd.solar_azimuth
        _ = sd.solar_azimuth
        _ = sd.times
    assert (
        spy.call_count == 1
    ), f"pd.date_range called {spy.call_count}× — expected ≤ 1 per day"


@pytest.mark.unit
def test_times_invalidates_on_day_rollover():
    """When `date.today()` advances, the cached timeline is rebuilt for the new day."""
    sd = _make_sun_data()

    day_one = date(2026, 5, 23)
    day_two = date(2026, 5, 24)

    class _FakeDate:
        """Stand-in for `datetime.date` so we can swap `today()` at will."""

        _today = day_one

        @classmethod
        def today(cls) -> date:
            return cls._today

    with patch("custom_components.adaptive_cover_pro.sun.date", _FakeDate):
        first = sd.times
        assert first[0].date() == day_one
        # Roll the clock forward one day.
        _FakeDate._today = day_two
        second = sd.times
        assert second[0].date() == day_two
        assert first is not second


@pytest.mark.unit
def test_solar_elevation_called_once_per_sample_per_day():
    """`location.solar_elevation` is called exactly N times across the entire day.

    Where N is the number of 5-minute samples (289). Pre-fix, every
    `.solar_elevation` access on the property re-walked the timeline
    AND each iteration re-ran `pd.date_range` (because `for _i in
    self.times` re-evaluated `self.times` each step). Multiple property
    reads in the same day would push the call count into the thousands.
    """
    sd = _make_sun_data()
    _ = sd.times  # warm cache
    _ = sd.solar_elevation
    _ = sd.solar_elevation  # second read must not re-walk
    _ = sd.solar_elevation
    n_expected = len(sd.times)
    assert sd.location.solar_elevation.call_count == n_expected


@pytest.mark.unit
def test_solar_azimuth_and_elevation_share_one_timeline_build():
    """A single day's azimuth + elevation reads must rebuild the timeline at most once."""
    sd = _make_sun_data()
    with patch(
        "custom_components.adaptive_cover_pro.sun.pd.date_range",
        wraps=pd.date_range,
    ) as spy:
        _ = sd.solar_azimuth
        _ = sd.solar_elevation
    assert spy.call_count == 1


@pytest.mark.unit
def test_polar_sentinels_still_work():
    """Date-cached refactor must preserve the polar midnight-sun / polar-night fallbacks."""
    sd = _make_sun_data()
    sd.location.sunset.side_effect = ValueError("never sets")
    sd.location.sunrise.side_effect = ValueError("never rises")

    result_sunset = sd.sunset()
    result_sunrise = sd.sunrise()

    today = date.today()
    assert result_sunset == datetime(today.year, today.month, today.day, 23, 59, 59)
    assert result_sunrise == datetime(today.year, today.month, today.day, 0, 1, 0)


@pytest.mark.unit
def test_timeline_spans_one_day_at_5min_freq():
    """Sanity: cached timeline should span today→tomorrow at 5-minute cadence (289 entries)."""
    sd = _make_sun_data()
    times = sd.times
    assert isinstance(times, pd.DatetimeIndex)
    # date_range with end=start+1 day and freq=5min yields 289 inclusive entries.
    assert len(times) == 289
    span = times[-1] - times[0]
    assert span == timedelta(days=1)
