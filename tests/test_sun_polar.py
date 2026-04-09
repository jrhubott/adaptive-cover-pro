"""Tests for SunData polar region fallbacks (midnight sun / polar night)."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.sun import SunData


def _make_sun_data() -> SunData:
    """Build a SunData with a MagicMock location."""
    location = MagicMock()
    return SunData(timezone="UTC", location=location, elevation=0)


@pytest.mark.unit
def test_sunset_polar_midnight_sun_returns_sentinel():
    """SunData.sunset() returns 23:59:59 when location raises ValueError (midnight sun)."""
    sd = _make_sun_data()
    sd.location.sunset.side_effect = ValueError("Sun never sets at this latitude")

    result = sd.sunset()

    today = date.today()
    assert result == datetime(today.year, today.month, today.day, 23, 59, 59)


@pytest.mark.unit
def test_sunset_polar_attribute_error_returns_sentinel():
    """SunData.sunset() returns 23:59:59 when location raises AttributeError."""
    sd = _make_sun_data()
    sd.location.sunset.side_effect = AttributeError

    result = sd.sunset()

    today = date.today()
    assert result == datetime(today.year, today.month, today.day, 23, 59, 59)


@pytest.mark.unit
def test_sunrise_polar_night_returns_sentinel():
    """SunData.sunrise() returns 00:01:00 when location raises ValueError (polar night)."""
    sd = _make_sun_data()
    sd.location.sunrise.side_effect = ValueError("Sun never rises at this latitude")

    result = sd.sunrise()

    today = date.today()
    assert result == datetime(today.year, today.month, today.day, 0, 1, 0)


@pytest.mark.unit
def test_sunrise_polar_attribute_error_returns_sentinel():
    """SunData.sunrise() returns 00:01:00 when location raises AttributeError."""
    sd = _make_sun_data()
    sd.location.sunrise.side_effect = AttributeError

    result = sd.sunrise()

    today = date.today()
    assert result == datetime(today.year, today.month, today.day, 0, 1, 0)


@pytest.mark.unit
def test_sunset_normal_returns_location_result():
    """SunData.sunset() returns location result when no exception raised."""
    sd = _make_sun_data()
    expected = datetime(2024, 6, 21, 20, 30, 0)
    sd.location.sunset.return_value = expected

    result = sd.sunset()
    assert result == expected


@pytest.mark.unit
def test_sunrise_normal_returns_location_result():
    """SunData.sunrise() returns location result when no exception raised."""
    sd = _make_sun_data()
    expected = datetime(2024, 6, 21, 4, 15, 0)
    sd.location.sunrise.return_value = expected

    result = sd.sunrise()
    assert result == expected
