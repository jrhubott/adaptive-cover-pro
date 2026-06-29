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
import zoneinfo
from datetime import UTC
from unittest.mock import MagicMock, patch


from custom_components.adaptive_cover_pro.helpers import (
    compute_effective_default,
    get_datetime_from_str,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sun_data(
    *,
    sunset_hour: int = 20,
    sunset_minute: int = 0,
    sunrise_hour: int = 6,
    sunrise_minute: int = 0,
    day: dt.date | None = None,
) -> MagicMock:
    """Return a mock SunData with controllable sunset/sunrise times (naive UTC).

    ``day`` pins the astral fallback date; defaults to ``dt.date.today()``.
    Tests that freeze ``now`` to an explicit calendar date must pass the same
    ``day`` so the astral sunrise/sunset fallback stays on that date (otherwise
    the comparison drifts when the real wall-clock date rolls over).
    """
    today = day or dt.date.today()
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


# ---------------------------------------------------------------------------
# Entity override kwargs (sunset_time / sunrise_time)
# ---------------------------------------------------------------------------


class TestEntityOverride:
    """Tests for sunset_time and sunrise_time override kwargs."""

    def test_signature_accepts_new_kwargs(self):
        """Passing sunset_time=None, sunrise_time=None must not raise TypeError."""
        sun = _make_sun_data(sunset_hour=20, sunrise_hour=6)
        today = dt.date.today()
        midday = dt.datetime(today.year, today.month, today.day, 12, 0, 0)
        with _freeze_now(midday):
            result, active = compute_effective_default(
                h_def=0,
                sunset_pos=80,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                sunset_time=None,
                sunrise_time=None,
            )
        assert result == 0
        assert active is False

    def test_sunset_time_override_used_instead_of_astral(self):
        """When sunset_time is set, it replaces astral sunset as boundary.

        Astral sunset = 20:00; override = 22:00; now = 21:30 → daytime with astral,
        but still before override boundary → not active.
        """
        sun = _make_sun_data(sunset_hour=20, sunrise_hour=6)
        today = dt.date.today()
        now_dt = dt.datetime(today.year, today.month, today.day, 21, 30, 0)
        override_sunset = dt.datetime(today.year, today.month, today.day, 22, 0, 0)
        with _freeze_now(now_dt):
            result, active = compute_effective_default(
                h_def=0,
                sunset_pos=80,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                sunset_time=override_sunset,
                sunrise_time=None,
            )
        # 21:30 is after astral 20:00 but before override 22:00 → not active
        assert active is False
        assert result == 0

    def test_sunset_time_override_activates_window(self):
        """When now > override sunset boundary the window is active."""
        sun = _make_sun_data(sunset_hour=20, sunrise_hour=6)
        today = dt.date.today()
        now_dt = dt.datetime(today.year, today.month, today.day, 22, 30, 0)
        override_sunset = dt.datetime(today.year, today.month, today.day, 22, 0, 0)
        with _freeze_now(now_dt):
            result, active = compute_effective_default(
                h_def=0,
                sunset_pos=80,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                sunset_time=override_sunset,
                sunrise_time=None,
            )
        # 22:30 is after override 22:00 → active
        assert active is True
        assert result == 80

    def test_sunrise_time_override_extends_window(self):
        """sunrise_time override replaces astral sunrise as the window-close boundary.

        Astral sunrise = 06:00; override = 08:00; now = 07:00 → active per override.
        """
        sun = _make_sun_data(sunset_hour=20, sunrise_hour=6)
        today = dt.date.today()
        now_dt = dt.datetime(today.year, today.month, today.day, 7, 0, 0)
        override_sunrise = dt.datetime(today.year, today.month, today.day, 8, 0, 0)
        with _freeze_now(now_dt):
            result, active = compute_effective_default(
                h_def=0,
                sunset_pos=80,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                sunset_time=None,
                sunrise_time=override_sunrise,
            )
        # 07:00 < override sunrise 08:00 → before_sunrise → active
        assert active is True
        assert result == 80

    def test_offset_still_additive_with_entity_override(self):
        """Offset is applied on top of the entity override value.

        Override sunset = 20:00; offset = +30; boundary = 20:30.
        Now = 20:15 → still before boundary → not active.
        Now = 20:35 → after boundary → active.
        """
        sun = _make_sun_data(sunset_hour=18, sunrise_hour=6)  # astral far away
        today = dt.date.today()
        override_sunset = dt.datetime(today.year, today.month, today.day, 20, 0, 0)

        # Before override+offset boundary
        now_before = dt.datetime(today.year, today.month, today.day, 20, 15, 0)
        with _freeze_now(now_before):
            result, active = compute_effective_default(
                h_def=0,
                sunset_pos=80,
                sun_data=sun,
                sunset_off=30,
                sunrise_off=0,
                sunset_time=override_sunset,
                sunrise_time=None,
            )
        assert active is False

        # After override+offset boundary
        now_after = dt.datetime(today.year, today.month, today.day, 20, 35, 0)
        with _freeze_now(now_after):
            result, active = compute_effective_default(
                h_def=0,
                sunset_pos=80,
                sun_data=sun,
                sunset_off=30,
                sunrise_off=0,
                sunset_time=override_sunset,
                sunrise_time=None,
            )
        assert active is True
        assert result == 80


# ---------------------------------------------------------------------------
# Start-time suppression of before-sunrise branch (issue #438)
# ---------------------------------------------------------------------------


class TestStartTimeSuppressesBeforeSunrise:
    """When window_explicitly_started=True the before-sunrise branch must not activate sunset_pos.

    Regression for issue #438: start_time < astronomical_sunrise caused
    sunset_pos (0%) to be used instead of default_pos (100%) in the morning.
    """

    def test_before_sunrise_but_after_start_time_returns_h_def(self):
        """Regression: at 08:05 UTC, before sunrise at 08:10, but after start_time=08:00."""
        sun = _make_sun_data(sunrise_hour=8, sunrise_minute=10, sunset_hour=18)
        today = dt.date.today()
        now = dt.datetime(today.year, today.month, today.day, 8, 5, 0)
        with _freeze_now(now):
            result, active = compute_effective_default(
                h_def=100,
                sunset_pos=0,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                window_explicitly_started=True,
            )
        # Operational window is open → sunset_pos must NOT apply
        assert result == 100
        assert active is False

    def test_before_sunrise_without_start_time_still_returns_sunset_pos(self):
        """Existing behaviour preserved when window_explicitly_started=False (default)."""
        sun = _make_sun_data(sunrise_hour=8, sunrise_minute=10, sunset_hour=18)
        today = dt.date.today()
        now = dt.datetime(today.year, today.month, today.day, 8, 5, 0)
        with _freeze_now(now):
            result, active = compute_effective_default(
                h_def=100,
                sunset_pos=0,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                window_explicitly_started=False,
            )
        # No start_time context → classic before-sunrise behaviour
        assert result == 0
        assert active is True

    def test_before_sunrise_after_start_time_with_positive_sunrise_offset(self):
        """window_explicitly_started=True also suppresses when sunrise_off extends the window."""
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=18)
        today = dt.date.today()
        # sunrise=06:00, offset=120 → window would close at 08:00 without start_time fix
        now = dt.datetime(today.year, today.month, today.day, 7, 30, 0)
        with _freeze_now(now):
            result, active = compute_effective_default(
                h_def=100,
                sunset_pos=0,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=120,
                window_explicitly_started=True,
            )
        assert result == 100
        assert active is False

    def test_after_sunset_not_affected_by_after_start_time(self):
        """window_explicitly_started=True must NOT suppress the after-sunset branch."""
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=18)
        today = dt.date.today()
        now = dt.datetime(today.year, today.month, today.day, 19, 0, 0)  # after sunset
        with _freeze_now(now):
            result, active = compute_effective_default(
                h_def=100,
                sunset_pos=0,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                window_explicitly_started=True,
            )
        # after sunset → sunset_pos still applies regardless of start_time
        assert result == 0
        assert active is True


# ---------------------------------------------------------------------------
# Blank start_time must NOT suppress the night position (issue #492)
# ---------------------------------------------------------------------------


class TestBlankStartTimeNightAfterMidnight:
    """Blank-sentinel start_time must not suppress the overnight position.

    Regression for issue #492: with a blank/unset start_time the
    ``after_start_time`` signal is always True, but that must NOT cancel the
    before-sunrise night branch. The distinct ``window_explicitly_started``
    signal is False for a blank start_time, so the night position holds.
    """

    def test_blank_start_time_at_one_minute_past_midnight_holds_sunset_pos(self):
        # #492 reproducer: sunrise 04:19, sunset 21:31, now 00:01.
        sun = _make_sun_data(
            sunrise_hour=4,
            sunrise_minute=19,
            sunset_hour=21,
            sunset_minute=31,
        )
        today = dt.date.today()
        now = dt.datetime(today.year, today.month, today.day, 0, 1, 0)
        with _freeze_now(now):
            result, active = compute_effective_default(
                h_def=100,
                sunset_pos=0,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                window_explicitly_started=False,
            )
        # Blank start_time → window not explicitly started → night position holds
        assert result == 0
        assert active is True


# ---------------------------------------------------------------------------
# Timezone regression: entity-override boundaries must respect HA local tz
# (issue #531)
# ---------------------------------------------------------------------------


class TestEntityOverrideTimezone:
    """Regression for issue #531: entity-derived sunset/sunrise boundaries are
    naive-LOCAL; compute_effective_default must normalise them to the same
    naive-UTC frame as now_naive before comparing.

    Reporter in France (CEST = UTC+2) configured a sunset-time entity at
    11:00 local.  At 11:38 local (= 09:38 UTC) the window should already be
    active, but without the fix it was not triggered until 13:00 local (=
    11:00 UTC), i.e. two hours late.
    """

    def _paris_tz(self):
        return zoneinfo.ZoneInfo("Europe/Paris")

    def test_sunset_time_override_respects_local_timezone(self):
        """Sunset entity at 11:00 Paris must activate at 11:00 Paris, not 13:00.

        Setup:
          - HA timezone: Europe/Paris (UTC+2 in summer, CEST)
          - Sunset-time entity state: "2026-06-06T11:00:00+02:00"
            → get_datetime_from_str produces naive-local 11:00
          - Frozen UTC clock: 09:38 UTC (= 11:38 Paris) — window should be active
          - Expected: is_sunset_active=True, effective=sunset_pos (0)

        Without the fix the comparison is naive-local 11:00 vs naive-UTC 09:38
        → 09:38 < 11:00 → not active (the bug). With the fix the boundary is
        converted to naive-UTC 09:00, so 09:38 > 09:00 → active (correct).
        """
        paris = self._paris_tz()
        sun = _make_sun_data(sunset_hour=20, sunrise_hour=4, day=dt.date(2026, 6, 6))

        # Produce the boundary the same way the real coordinator does:
        # get_datetime_from_str on a tz-aware entity string.
        entity_state = "2026-06-06T11:00:00+02:00"
        with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", paris):
            sunset_boundary = get_datetime_from_str(entity_state)
        # Confirm the producer gave us naive-local 11:00
        assert sunset_boundary == dt.datetime(2026, 6, 6, 11, 0, 0)
        assert sunset_boundary.tzinfo is None

        # Freeze "now" to 09:38 UTC (= 11:38 Paris) — after local sunset boundary
        frozen_utc = dt.datetime(2026, 6, 6, 9, 38, 0)
        with (
            patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", paris),
            _freeze_now(frozen_utc),
        ):
            result, active = compute_effective_default(
                h_def=100,
                sunset_pos=0,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                sunset_time=sunset_boundary,
                sunrise_time=None,
            )

        # 11:38 Paris is after 11:00 Paris → sunset must be active
        assert active is True, (
            f"Expected is_sunset_active=True at 11:38 Paris with sunset boundary "
            f"11:00 Paris, but got active={active} (UTC-vs-local mismatch?)"
        )
        assert result == 0

    def test_sunrise_time_override_respects_local_timezone(self):
        """Sunrise entity at 07:00 Paris must hold the sunset window until 07:00 Paris.

        Setup:
          - HA timezone: Europe/Paris (UTC+2 CEST)
          - Sunrise-time entity state: "2026-06-06T07:00:00+02:00"
            → get_datetime_from_str produces naive-local 07:00
          - Frozen UTC clock: 04:45 UTC (= 06:45 Paris) — before local sunrise → still in window
          - Expected: is_sunset_active=True (before_sunrise branch), effective=0

        Without the fix the comparison is naive-local 07:00 vs naive-UTC 04:45
        → 04:45 < 07:00 → active, BUT for the wrong reason — it happens to work
        only because naive-UTC is always less than naive-local for positive offsets.
        The test below is written for the symmetric negative-offset case to be a true
        regression check; here we verify the positive-UTC-offset case is also correct.
        """
        paris = self._paris_tz()
        sun = _make_sun_data(sunset_hour=21, sunrise_hour=5, day=dt.date(2026, 6, 6))

        entity_state = "2026-06-06T07:00:00+02:00"
        with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", paris):
            sunrise_boundary = get_datetime_from_str(entity_state)
        assert sunrise_boundary == dt.datetime(2026, 6, 6, 7, 0, 0)
        assert sunrise_boundary.tzinfo is None

        # 04:45 UTC = 06:45 Paris → before 07:00 sunrise → should still be in window
        frozen_utc = dt.datetime(2026, 6, 6, 4, 45, 0)
        with (
            patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", paris),
            _freeze_now(frozen_utc),
        ):
            result, active = compute_effective_default(
                h_def=100,
                sunset_pos=0,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                sunset_time=None,
                sunrise_time=sunrise_boundary,
            )

        # 06:45 Paris < 07:00 Paris → before_sunrise → window still active
        assert active is True, (
            f"Expected is_sunset_active=True at 06:45 Paris with sunrise boundary "
            f"07:00 Paris, but got active={active}"
        )
        assert result == 0

    def test_sunset_boundary_not_yet_reached_in_local_time(self):
        """Before the local sunset boundary the window must NOT be active.

        Setup:
          - HA timezone: Europe/Paris (UTC+2 CEST)
          - Sunset entity: 11:00 Paris → naive-local 11:00 → naive-UTC boundary 09:00
          - Frozen UTC: 08:45 UTC (= 10:45 Paris) — 15 min before local boundary
          - Expected: is_sunset_active=False
        """
        paris = self._paris_tz()
        sun = _make_sun_data(sunset_hour=20, sunrise_hour=4, day=dt.date(2026, 6, 6))

        entity_state = "2026-06-06T11:00:00+02:00"
        with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", paris):
            sunset_boundary = get_datetime_from_str(entity_state)

        # 08:45 UTC = 10:45 Paris → before 11:00 local sunset → NOT active
        frozen_utc = dt.datetime(2026, 6, 6, 8, 45, 0)
        with (
            patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", paris),
            _freeze_now(frozen_utc),
        ):
            result, active = compute_effective_default(
                h_def=100,
                sunset_pos=0,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                sunset_time=sunset_boundary,
                sunrise_time=None,
            )

        assert active is False, (
            f"Expected is_sunset_active=False at 10:45 Paris (before 11:00 boundary), "
            f"got active={active}"
        )
        assert result == 100

    def test_future_next_setting_entity_activates_night_position(self):
        """A future-dated ``sensor.sun_next_setting`` still activates the night position.

        Issue #531 follow-up: once today's sun has set, a "next setting" sensor
        reports *tomorrow's* setting. The coordinator re-anchors that onto
        today's date, so after dusk the after-sunset branch fires correctly.
        """
        from unittest.mock import MagicMock as _MagicMock

        from custom_components.adaptive_cover_pro.coordinator import _read_time_entity

        paris = self._paris_tz()
        sun = _make_sun_data(sunset_hour=20, sunrise_hour=4, day=dt.date(2026, 6, 7))

        mock_state = _MagicMock()
        mock_state.state = "2026-06-08T19:01:00+00:00"  # tomorrow UTC = 21:01 Paris
        mock_hass = _MagicMock()
        mock_hass.states.get.return_value = mock_state

        now_local = dt.datetime(2026, 6, 7, 21, 30, 0, tzinfo=paris)
        with (
            patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", paris),
            patch(
                "custom_components.adaptive_cover_pro.coordinator.dt_util.now",
                return_value=now_local,
            ),
        ):
            sunset_boundary = _read_time_entity(mock_hass, "sensor.sun_next_setting")

        # 19:30 UTC = 21:30 Paris → after the 21:01 Paris (= 19:01 UTC) boundary
        frozen_utc = dt.datetime(2026, 6, 7, 19, 30, 0)
        with (
            patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", paris),
            _freeze_now(frozen_utc),
        ):
            result, active = compute_effective_default(
                h_def=100,
                sunset_pos=0,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                sunset_time=sunset_boundary,
                sunrise_time=None,
            )

        assert active is True, (
            f"Expected is_sunset_active=True after dusk with a future next_setting "
            f"boundary, got active={active}"
        )
        assert result == 0

    def test_future_next_rising_entity_holds_then_releases(self):
        """A future-dated ``sensor.sun_next_rising`` holds the night position pre-dawn.

        Before the re-anchored sunrise the overnight window must remain active.
        """
        from unittest.mock import MagicMock as _MagicMock

        from custom_components.adaptive_cover_pro.coordinator import _read_time_entity

        paris = self._paris_tz()
        sun = _make_sun_data(sunset_hour=21, sunrise_hour=5, day=dt.date(2026, 6, 7))

        mock_state = _MagicMock()
        mock_state.state = "2026-06-08T04:46:00+00:00"  # tomorrow UTC = 06:46 Paris
        mock_hass = _MagicMock()
        mock_hass.states.get.return_value = mock_state

        now_local = dt.datetime(2026, 6, 7, 3, 0, 0, tzinfo=paris)
        with (
            patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", paris),
            patch(
                "custom_components.adaptive_cover_pro.coordinator.dt_util.now",
                return_value=now_local,
            ),
        ):
            sunrise_boundary = _read_time_entity(mock_hass, "sensor.sun_next_rising")

        # 01:00 UTC = 03:00 Paris → before the 06:46 Paris (= 04:46 UTC) boundary
        frozen_utc = dt.datetime(2026, 6, 7, 1, 0, 0)
        with (
            patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", paris),
            _freeze_now(frozen_utc),
        ):
            result, active = compute_effective_default(
                h_def=100,
                sunset_pos=0,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                sunset_time=None,
                sunrise_time=sunrise_boundary,
                window_explicitly_started=False,
            )

        assert active is True, (
            f"Expected is_sunset_active=True before a future next_rising boundary, "
            f"got active={active}"
        )
        assert result == 0


# ---------------------------------------------------------------------------
# eval_time override (forecast parity)
#
# The forecast projects the effective default at each future sample time by
# passing eval_time, instead of evaluating against wall-clock now.
# ---------------------------------------------------------------------------


class TestEvalTime:
    """eval_time replaces wall-clock now when provided; None preserves it."""

    def test_aware_eval_time_in_sunset_window_activates(self):
        """A tz-aware eval_time past sunset → sunset position active."""
        sun = _make_sun_data(sunset_hour=20, sunrise_hour=6)
        today = dt.date.today()
        # Build eval_t BEFORE _freeze_now: that patcher replaces the global
        # datetime class, so constructing a datetime inside the block yields a
        # MagicMock. Wall-clock now is frozen to midday to prove eval_time wins.
        eval_t = dt.datetime(today.year, today.month, today.day, 22, 0, 0, tzinfo=UTC)
        with _freeze_now(dt.datetime(today.year, today.month, today.day, 12, 0, 0)):
            result, active = compute_effective_default(
                h_def=80,
                sunset_pos=20,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                eval_time=eval_t,
            )
        assert active is True
        assert result == 20

    def test_aware_eval_time_at_midday_is_daytime_even_when_now_is_night(self):
        """eval_time overrides a post-sunset wall clock → daytime h_def."""
        sun = _make_sun_data(sunset_hour=20, sunrise_hour=6)
        today = dt.date.today()
        # eval_t built before the patcher (see note above); now is frozen to
        # deep night, which would otherwise be sunset-active.
        eval_t = dt.datetime(today.year, today.month, today.day, 12, 0, 0, tzinfo=UTC)
        with _freeze_now(dt.datetime(today.year, today.month, today.day, 23, 0, 0)):
            result, active = compute_effective_default(
                h_def=80,
                sunset_pos=20,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                eval_time=eval_t,
            )
        assert active is False
        assert result == 80

    def test_naive_eval_time_interpreted_as_local(self):
        """A naive eval_time is read as local wall-clock (UTC zone here)."""
        sun = _make_sun_data(sunset_hour=20, sunrise_hour=6)
        today = dt.date.today()
        naive_night = dt.datetime(today.year, today.month, today.day, 22, 0, 0)
        with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", UTC):
            result, active = compute_effective_default(
                h_def=80,
                sunset_pos=20,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                eval_time=naive_night,
            )
        assert active is True
        assert result == 20

    def test_eval_time_none_falls_back_to_now(self):
        """eval_time=None preserves the wall-clock behaviour (no regression)."""
        sun = _make_sun_data(sunset_hour=20, sunrise_hour=6)
        today = dt.date.today()
        with _freeze_now(dt.datetime(today.year, today.month, today.day, 22, 0, 0)):
            result, active = compute_effective_default(
                h_def=80,
                sunset_pos=20,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                eval_time=None,
            )
        assert active is True
        assert result == 20


# ---------------------------------------------------------------------------
# Daytime gate override (issue #632)
# ---------------------------------------------------------------------------


class TestDaytimeGateOverride:
    """When a gate is configured it OWNS the day/night boundary.

    ``daytime_gate`` is a tri-state override:
      - ``None`` (default, unconfigured)  → astronomical decision, no regression.
      - ``True`` (gate says daytime)       → never sunset, regardless of astral.
      - ``False`` (gate says dark)         → always sunset, regardless of astral.
    """

    def test_gate_false_forces_sunset_at_midday(self):
        # Astronomically midday (would be h_def) but the gate says dark.
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=20)
        today = dt.date.today()
        midday = dt.datetime(today.year, today.month, today.day, 12, 0, 0)
        with _freeze_now(midday):
            result, active = compute_effective_default(
                h_def=80,
                sunset_pos=20,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                daytime_gate=False,
            )
        assert active is True
        assert result == 20

    def test_gate_true_suppresses_sunset_after_astral_sunset(self):
        # Bright evening: astronomically past sunset but the gate says daytime.
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=20)
        today = dt.date.today()
        evening = dt.datetime(today.year, today.month, today.day, 21, 0, 0)
        with _freeze_now(evening):
            result, active = compute_effective_default(
                h_def=80,
                sunset_pos=20,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                daytime_gate=True,
            )
        assert active is False
        assert result == 80

    def test_gate_true_suppresses_sunset_before_astral_sunrise(self):
        # Overcast-inverse: astronomically pre-sunrise but the gate says daytime.
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=20)
        today = dt.date.today()
        early = dt.datetime(today.year, today.month, today.day, 4, 0, 0)
        with _freeze_now(early):
            result, active = compute_effective_default(
                h_def=80,
                sunset_pos=20,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                daytime_gate=True,
            )
        assert active is False
        assert result == 80

    def test_gate_none_preserves_astronomical_after_sunset(self):
        # Regression: None must behave exactly like the no-gate astral path.
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=20)
        today = dt.date.today()
        evening = dt.datetime(today.year, today.month, today.day, 21, 0, 0)
        with _freeze_now(evening):
            result, active = compute_effective_default(
                h_def=80,
                sunset_pos=20,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                daytime_gate=None,
            )
        assert active is True
        assert result == 20

    def test_gate_none_preserves_astronomical_at_midday(self):
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=20)
        today = dt.date.today()
        midday = dt.datetime(today.year, today.month, today.day, 12, 0, 0)
        with _freeze_now(midday):
            result, active = compute_effective_default(
                h_def=80,
                sunset_pos=20,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                daytime_gate=None,
            )
        assert active is False
        assert result == 80

    def test_gate_none_is_the_fell_back_astral_target(self):
        """Issue #742: the graceful-fallback target (daytime_gate=None) is astral.

        When the gate has been indeterminate past its grace window the manager
        resolves ``effective_daytime_gate`` to ``None``; this confirms that value
        re-enters the astronomical sunset/sunrise branch (no new astral code) —
        same outcome as an unconfigured gate at the same instant.
        """
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=20)
        today = dt.date.today()
        # Past astral sunset → astral branch must activate the sunset position.
        evening = dt.datetime(today.year, today.month, today.day, 21, 0, 0)
        with _freeze_now(evening):
            fell_back = compute_effective_default(
                h_def=80,
                sunset_pos=20,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                daytime_gate=None,
            )
            no_gate = compute_effective_default(
                h_def=80,
                sunset_pos=20,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
            )
        assert fell_back == no_gate == (20, True)

    def test_gate_false_ignores_window_explicitly_started(self):
        # The gate short-circuits before the window_explicitly_started branch.
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=20)
        today = dt.date.today()
        early = dt.datetime(today.year, today.month, today.day, 4, 0, 0)
        with _freeze_now(early):
            result, active = compute_effective_default(
                h_def=80,
                sunset_pos=20,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                window_explicitly_started=True,
                daytime_gate=False,
            )
        assert active is True
        assert result == 20


# ---------------------------------------------------------------------------
# End-of-window position (issue #625) — TWO-PHASE astral handoff
#
# When the operating window is clock-closed and an end-of-window position is
# configured, it overrides the astronomical effective default from window-end
# until astral sunset (phase 1); then the astral sunset_position takes over
# (phase 2). When sunset_pos is None there is nothing to hand off to, so the
# end-of-window position persists the whole evening.
# ---------------------------------------------------------------------------


class TestEndOfWindowPosition:
    """end_of_window_pos overrides the effective default at/after window-end."""

    def test_phase1_before_astral_sunset_uses_eow(self):
        """Eow active, before astral sunset → end-of-window position wins."""
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=20)
        today = dt.date.today()
        # 19:30 is before astral sunset (20:00) → astral would give h_def (80).
        eval_t = dt.datetime(today.year, today.month, today.day, 19, 30, 0, tzinfo=UTC)
        with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", UTC):
            result, active = compute_effective_default(
                h_def=80,
                sunset_pos=20,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                eval_time=eval_t,
                end_of_window_pos=0,
                end_of_window_active=True,
            )
        assert result == 0
        assert active is True

    def test_phase2_after_astral_sunset_hands_off_to_sunset(self):
        """Eow active, after astral sunset → astral sunset_position takes over."""
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=20)
        today = dt.date.today()
        eval_t = dt.datetime(today.year, today.month, today.day, 21, 0, 0, tzinfo=UTC)
        with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", UTC):
            result, active = compute_effective_default(
                h_def=80,
                sunset_pos=20,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                eval_time=eval_t,
                end_of_window_pos=0,
                end_of_window_active=True,
            )
        assert result == 20
        assert active is True

    def test_no_handoff_target_persists_all_evening(self):
        """sunset_pos None + eow active → eow persists across the evening."""
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=20)
        today = dt.date.today()
        # Both before and after astral sunset return the eow position.
        for hour in (19, 22):
            eval_t = dt.datetime(
                today.year, today.month, today.day, hour, 0, 0, tzinfo=UTC
            )
            with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", UTC):
                result, active = compute_effective_default(
                    h_def=80,
                    sunset_pos=None,
                    sun_data=sun,
                    sunset_off=0,
                    sunrise_off=0,
                    eval_time=eval_t,
                    end_of_window_pos=0,
                    end_of_window_active=True,
                )
            assert result == 0
            assert active is True

    def test_daytime_gate_owns_boundary_when_sunset_pos_set(self):
        """A daytime gate still owns the boundary; eow phase-1 does not override it."""
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=20)
        today = dt.date.today()
        eval_t = dt.datetime(today.year, today.month, today.day, 19, 30, 0, tzinfo=UTC)
        with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", UTC):
            result, active = compute_effective_default(
                h_def=80,
                sunset_pos=20,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                eval_time=eval_t,
                daytime_gate=True,  # gate says daytime
                end_of_window_pos=0,
                end_of_window_active=True,
            )
        # Gate owns it → daytime h_def, eow does not fire.
        assert result == 80
        assert active is False

    def test_no_handoff_target_outranks_daytime_gate(self):
        """sunset_pos None + eow active beats a daytime gate (top short-circuit)."""
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=20)
        today = dt.date.today()
        eval_t = dt.datetime(today.year, today.month, today.day, 19, 30, 0, tzinfo=UTC)
        with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", UTC):
            result, active = compute_effective_default(
                h_def=80,
                sunset_pos=None,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                eval_time=eval_t,
                daytime_gate=True,
                end_of_window_pos=0,
                end_of_window_active=True,
            )
        assert result == 0
        assert active is True

    def test_inactive_eow_is_no_regression_astral(self):
        """end_of_window_active=False → byte-identical to today's astral path."""
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=20)
        today = dt.date.today()
        eval_t = dt.datetime(today.year, today.month, today.day, 19, 30, 0, tzinfo=UTC)
        with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", UTC):
            result, active = compute_effective_default(
                h_def=80,
                sunset_pos=20,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                eval_time=eval_t,
                end_of_window_pos=0,
                end_of_window_active=False,
            )
        assert result == 80
        assert active is False

    def test_eow_none_is_no_regression_astral(self):
        """end_of_window_pos=None → byte-identical to today's astral path."""
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=20)
        today = dt.date.today()
        eval_t = dt.datetime(today.year, today.month, today.day, 19, 30, 0, tzinfo=UTC)
        with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", UTC):
            result, active = compute_effective_default(
                h_def=80,
                sunset_pos=20,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                eval_time=eval_t,
                end_of_window_pos=None,
                end_of_window_active=True,
            )
        assert result == 80
        assert active is False

    def test_explicit_zero_eow_distinct_from_unset(self):
        """0% is a valid end-of-window position (None-preserving check)."""
        sun = _make_sun_data(sunrise_hour=6, sunset_hour=20)
        today = dt.date.today()
        eval_t = dt.datetime(today.year, today.month, today.day, 19, 30, 0, tzinfo=UTC)
        with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", UTC):
            result, active = compute_effective_default(
                h_def=80,
                sunset_pos=None,
                sun_data=sun,
                sunset_off=0,
                sunrise_off=0,
                eval_time=eval_t,
                end_of_window_pos=0,
                end_of_window_active=True,
            )
        assert result == 0
        assert active is True
