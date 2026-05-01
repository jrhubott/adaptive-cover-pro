"""Tests for compute_effective_default() helper.

Covers:
- No sunset_pos configured → always returns h_def, is_sunset_active=False
- During daytime (between sunrise+offset and sunset+offset) → h_def
- After sunset+offset → sunset_pos
- Before sunrise+offset → sunset_pos (overnight window)
- Exactly at boundary (sunset+offset and sunrise+offset)
- Offset values shift the boundary correctly
- Polar edge cases (sentinel times from SunData)
- Return type is (int, bool)
"""

from __future__ import annotations

import datetime as dt
from datetime import UTC
from unittest.mock import MagicMock, patch


from custom_components.adaptive_cover_pro.helpers import compute_effective_default

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sun_data(
    *,
    sunset_hour: int = 20,
    sunset_minute: int = 0,
    sunrise_hour: int = 6,
    sunrise_minute: int = 0,
) -> MagicMock:
    """Return a mock SunData with controllable sunset/sunrise times (naive UTC)."""
    today = dt.date.today()
    sunset_dt = dt.datetime(
        today.year, today.month, today.day, sunset_hour, sunset_minute, 0
    )
    sunrise_dt = dt.datetime(
        today.year, today.month, today.day, sunrise_hour, sunrise_minute, 0
    )
    sun = MagicMock()
    sun.sunset.return_value = sunset_dt
    sun.sunrise.return_value = sunrise_dt
    return sun


def _freeze_now(naive_dt: dt.datetime):
    """Context-manager-compatible patcher that fixes datetime.now(UTC) to naive_dt."""
    aware_dt = naive_dt.replace(tzinfo=UTC)
    return patch(
        "custom_components.adaptive_cover_pro.helpers.dt.datetime",
        **{"now.return_value": aware_dt},
    )


# ---------------------------------------------------------------------------
# No sunset_pos configured
# ---------------------------------------------------------------------------


class TestNoSunsetPos:
    """When sunset_pos is None the function always returns h_def."""

    def test_returns_h_def_during_day(self):
        sun = _make_sun_data()
        result, active = compute_effective_default(
            h_def=0, sunset_pos=None, sun_data=sun, sunset_off=0, sunrise_off=0
        )
        assert result == 0
        assert active is False

    def test_returns_h_def_after_sunset(self):
        sun = _make_sun_data(sunset_hour=20)
        # Even if we're past sunset, no sunset_pos → h_def
        today = dt.date.today()
        after_sunset = dt.datetime(today.year, today.month, today.day, 21, 0, 0)
        with _freeze_now(after_sunset):
            result, active = compute_effective_default(
                h_def=30, sunset_pos=None, sun_data=sun, sunset_off=0, sunrise_off=0
            )
        assert result == 30
        assert active is False

    def test_returns_h_def_before_sunrise(self):
        sun = _make_sun_data(sunrise_hour=6)
        today = dt.date.today()
        before_sunrise = dt.datetime(today.year, today.month, today.day, 4, 0, 0)
        with _freeze_now(before_sunrise):
            result, active = compute_effective_default(
                h_def=15, sunset_pos=None, sun_data=sun, sunset_off=0, sunrise_off=0
            )
        assert result == 15
        assert active is False


# ---------------------------------------------------------------------------
# Daytime window (between sunrise+offset and sunset+offset)
# ---------------------------------------------------------------------------


class TestDaytime:
    """During operational daytime the base default h_def is returned."""

    def test_midday_returns_h_def(self):
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=20)
        today = dt.date.today()
        midday = dt.datetime(today.year, today.month, today.day, 12, 0, 0)
        with _freeze_now(midday):
            result, active = compute_effective_default(
                h_def=0, sunset_pos=80, sun_data=sun, sunset_off=0, sunrise_off=0
            )
        assert result == 0
        assert active is False

    def test_just_after_sunrise_offset_returns_h_def(self):
        """1 minute after sunrise+offset → daytime, use h_def."""
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=20)
        today = dt.date.today()
        # sunrise=06:00, offset=30 → window closes at 06:30; 06:31 is daytime
        just_after = dt.datetime(today.year, today.month, today.day, 6, 31, 0)
        with _freeze_now(just_after):
            result, active = compute_effective_default(
                h_def=0, sunset_pos=80, sun_data=sun, sunset_off=0, sunrise_off=30
            )
        assert result == 0
        assert active is False

    def test_just_before_sunset_offset_returns_h_def(self):
        """1 minute before sunset+offset → daytime, use h_def."""
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=20)
        today = dt.date.today()
        # sunset=20:00, offset=15 → window opens at 20:15; 20:14 is daytime
        just_before = dt.datetime(today.year, today.month, today.day, 20, 14, 0)
        with _freeze_now(just_before):
            result, active = compute_effective_default(
                h_def=0, sunset_pos=80, sun_data=sun, sunset_off=15, sunrise_off=0
            )
        assert result == 0
        assert active is False


# ---------------------------------------------------------------------------
# After sunset + offset
# ---------------------------------------------------------------------------


class TestAfterSunset:
    """After (sunset + sunset_off) the sunset position is active."""

    def test_immediately_after_sunset_no_offset(self):
        sun = _make_sun_data(sunset_hour=20)
        today = dt.date.today()
        just_after = dt.datetime(today.year, today.month, today.day, 20, 1, 0)
        with _freeze_now(just_after):
            result, active = compute_effective_default(
                h_def=0, sunset_pos=80, sun_data=sun, sunset_off=0, sunrise_off=0
            )
        assert result == 80
        assert active is True

    def test_well_after_sunset(self):
        sun = _make_sun_data(sunset_hour=20)
        today = dt.date.today()
        night = dt.datetime(today.year, today.month, today.day, 23, 0, 0)
        with _freeze_now(night):
            result, active = compute_effective_default(
                h_def=0, sunset_pos=75, sun_data=sun, sunset_off=0, sunrise_off=0
            )
        assert result == 75
        assert active is True

    def test_after_sunset_with_offset(self):
        """Offset pushes the window boundary forward; must be after sunset+offset."""
        sun = _make_sun_data(sunset_hour=20)
        today = dt.date.today()
        # sunset=20:00, offset=30 → boundary at 20:30
        at_boundary = dt.datetime(today.year, today.month, today.day, 20, 31, 0)
        with _freeze_now(at_boundary):
            result, active = compute_effective_default(
                h_def=0, sunset_pos=80, sun_data=sun, sunset_off=30, sunrise_off=0
            )
        assert result == 80
        assert active is True

    def test_before_sunset_offset_still_daytime(self):
        """Inside offset window but before sunset+offset → still daytime."""
        sun = _make_sun_data(sunset_hour=20)
        today = dt.date.today()
        # sunset=20:00, offset=30 → boundary at 20:30; 20:29 is still daytime
        before_boundary = dt.datetime(today.year, today.month, today.day, 20, 29, 0)
        with _freeze_now(before_boundary):
            result, active = compute_effective_default(
                h_def=0, sunset_pos=80, sun_data=sun, sunset_off=30, sunrise_off=0
            )
        assert result == 0
        assert active is False


# ---------------------------------------------------------------------------
# Before sunrise + offset (overnight window)
# ---------------------------------------------------------------------------


class TestBeforeSunrise:
    """Before (sunrise + sunrise_off) the sunset position is also active."""

    def test_early_morning_before_sunrise(self):
        sun = _make_sun_data(sunrise_hour=6)
        today = dt.date.today()
        early = dt.datetime(today.year, today.month, today.day, 4, 0, 0)
        with _freeze_now(early):
            result, active = compute_effective_default(
                h_def=0, sunset_pos=80, sun_data=sun, sunset_off=0, sunrise_off=0
            )
        assert result == 80
        assert active is True

    def test_just_before_sunrise(self):
        sun = _make_sun_data(sunrise_hour=6)
        today = dt.date.today()
        just_before = dt.datetime(today.year, today.month, today.day, 5, 59, 0)
        with _freeze_now(just_before):
            result, active = compute_effective_default(
                h_def=0, sunset_pos=80, sun_data=sun, sunset_off=0, sunrise_off=0
            )
        assert result == 80
        assert active is True

    def test_before_sunrise_with_positive_offset(self):
        """sunrise=06:00, offset=30 → window closes at 06:30; 06:29 is sunset-active."""
        sun = _make_sun_data(sunrise_hour=6)
        today = dt.date.today()
        during_offset = dt.datetime(today.year, today.month, today.day, 6, 29, 0)
        with _freeze_now(during_offset):
            result, active = compute_effective_default(
                h_def=0, sunset_pos=80, sun_data=sun, sunset_off=0, sunrise_off=30
            )
        assert result == 80
        assert active is True

    def test_after_sunrise_offset_is_daytime(self):
        """After sunrise+offset the window is closed."""
        sun = _make_sun_data(sunrise_hour=6)
        today = dt.date.today()
        after_offset = dt.datetime(today.year, today.month, today.day, 6, 31, 0)
        with _freeze_now(after_offset):
            result, active = compute_effective_default(
                h_def=0, sunset_pos=80, sun_data=sun, sunset_off=0, sunrise_off=30
            )
        assert result == 0
        assert active is False


# ---------------------------------------------------------------------------
# Boundary precision
# ---------------------------------------------------------------------------


class TestBoundaries:
    """Exact boundary values: strictly greater-than / less-than semantics."""

    def test_exactly_at_sunset_offset_boundary_is_not_active(self):
        """Now == sunset+offset is NOT after the boundary (uses strict >)."""
        sun = _make_sun_data(sunset_hour=20)
        today = dt.date.today()
        exactly_at = dt.datetime(today.year, today.month, today.day, 20, 15, 0)
        with _freeze_now(exactly_at):
            result, active = compute_effective_default(
                h_def=0, sunset_pos=80, sun_data=sun, sunset_off=15, sunrise_off=0
            )
        # now == boundary → not strictly after → daytime
        assert result == 0
        assert active is False

    def test_exactly_at_sunrise_offset_boundary_is_not_active(self):
        """Now == sunrise+offset is NOT before the boundary (uses strict <)."""
        sun = _make_sun_data(sunrise_hour=6)
        today = dt.date.today()
        exactly_at = dt.datetime(today.year, today.month, today.day, 6, 30, 0)
        with _freeze_now(exactly_at):
            result, active = compute_effective_default(
                h_def=0, sunset_pos=80, sun_data=sun, sunset_off=0, sunrise_off=30
            )
        # now == boundary → not strictly before → daytime
        assert result == 0
        assert active is False


# ---------------------------------------------------------------------------
# Return type guarantees
# ---------------------------------------------------------------------------


class TestReturnTypes:
    """Return values must always be (int, bool)."""

    def test_daytime_returns_int_bool(self):
        sun = _make_sun_data()
        today = dt.date.today()
        midday = dt.datetime(today.year, today.month, today.day, 12, 0, 0)
        with _freeze_now(midday):
            result, active = compute_effective_default(
                h_def=10, sunset_pos=80, sun_data=sun, sunset_off=0, sunrise_off=0
            )
        assert isinstance(result, int)
        assert isinstance(active, bool)

    def test_sunset_returns_int_bool(self):
        sun = _make_sun_data(sunset_hour=20)
        today = dt.date.today()
        night = dt.datetime(today.year, today.month, today.day, 22, 0, 0)
        with _freeze_now(night):
            result, active = compute_effective_default(
                h_def=10, sunset_pos=80, sun_data=sun, sunset_off=0, sunrise_off=0
            )
        assert isinstance(result, int)
        assert isinstance(active, bool)

    def test_float_inputs_are_cast_to_int(self):
        """h_def and sunset_pos stored as floats in config must round-trip cleanly."""
        sun = _make_sun_data(sunset_hour=20)
        today = dt.date.today()
        night = dt.datetime(today.year, today.month, today.day, 22, 0, 0)
        with _freeze_now(night):
            result, active = compute_effective_default(
                h_def=10.0,  # type: ignore[arg-type]
                sunset_pos=80.0,  # type: ignore[arg-type]
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
            )
        assert result == 80
        assert isinstance(result, int)

    def test_none_sunset_pos_returns_h_def_as_int(self):
        sun = _make_sun_data()
        result, active = compute_effective_default(
            h_def=25, sunset_pos=None, sun_data=sun, sunset_off=0, sunrise_off=0
        )
        assert result == 25
        assert isinstance(result, int)
        assert active is False


# ---------------------------------------------------------------------------
# Zero-offset edge case
# ---------------------------------------------------------------------------


class TestZeroOffset:
    """Both offsets at zero: boundary is exactly astronomical sunset/sunrise."""

    def test_zero_offset_active_right_after_sunset(self):
        sun = _make_sun_data(sunset_hour=19, sunset_minute=30)
        today = dt.date.today()
        after = dt.datetime(today.year, today.month, today.day, 19, 31, 0)
        with _freeze_now(after):
            result, active = compute_effective_default(
                h_def=0, sunset_pos=100, sun_data=sun, sunset_off=0, sunrise_off=0
            )
        assert active is True
        assert result == 100

    def test_zero_offset_inactive_right_before_sunset(self):
        sun = _make_sun_data(sunset_hour=19, sunset_minute=30)
        today = dt.date.today()
        before = dt.datetime(today.year, today.month, today.day, 19, 29, 0)
        with _freeze_now(before):
            result, active = compute_effective_default(
                h_def=0, sunset_pos=100, sun_data=sun, sunset_off=0, sunrise_off=0
            )
        assert active is False
        assert result == 0
