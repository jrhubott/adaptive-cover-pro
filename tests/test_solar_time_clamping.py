"""Tests for solar time min/max clamping feature (Issue #27)."""

import datetime as dt
from unittest.mock import MagicMock

import pytz


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = pytz.UTC


def _dt(hour: int, minute: int = 0) -> dt.datetime:
    """Return a UTC datetime for today at the given time."""
    today = dt.date.today()
    return dt.datetime(today.year, today.month, today.day, hour, minute, tzinfo=UTC)


def _make_coordinator(options: dict):
    """Return a MagicMock coordinator wired with the given options dict."""
    coordinator = MagicMock()
    coordinator.config_entry.options = options
    coordinator.logger = MagicMock()
    return coordinator


def _clamp(coordinator, start, end, elev_start=None, elev_end=None):
    """Call _apply_solar_time_clamping on the real method with a mock self."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    return AdaptiveDataUpdateCoordinator._apply_solar_time_clamping(
        coordinator, start, end, elev_start, elev_end
    )


# ---------------------------------------------------------------------------
# Smoke test — all 6 constants are importable
# ---------------------------------------------------------------------------


def test_constants_importable():
    """All 6 new CONF_* constants should be importable."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_MAX_SUN_END,
        CONF_MAX_SUN_START,
        CONF_MIN_SUN_END,
        CONF_MIN_SUN_START,
        CONF_SUN_END_ELEVATION,
        CONF_SUN_START_ELEVATION,
    )

    assert CONF_MIN_SUN_START == "min_sun_start"
    assert CONF_MAX_SUN_START == "max_sun_start"
    assert CONF_MIN_SUN_END == "min_sun_end"
    assert CONF_MAX_SUN_END == "max_sun_end"
    assert CONF_SUN_START_ELEVATION == "sun_start_elevation"
    assert CONF_SUN_END_ELEVATION == "sun_end_elevation"


# ---------------------------------------------------------------------------
# No options — returns unchanged
# ---------------------------------------------------------------------------


def test_no_options_returns_unchanged():
    """When no clamping options are set, raw times are returned as-is."""
    coordinator = _make_coordinator({})
    start = _dt(7)
    end = _dt(17)

    result_start, result_end = _clamp(coordinator, start, end)

    assert result_start == start
    assert result_end == end


# ---------------------------------------------------------------------------
# min_sun_start
# ---------------------------------------------------------------------------


def test_min_sun_start_clamps_early_raw_start():
    """min_sun_start pushes start forward when raw start is before it."""
    coordinator = _make_coordinator({"min_sun_start": "08:00:00"})
    start = _dt(5)   # raw start at 05:00 — too early
    end = _dt(17)

    result_start, result_end = _clamp(coordinator, start, end)

    assert result_start.hour == 8
    assert result_end == end


def test_min_sun_start_no_effect_when_raw_is_later():
    """min_sun_start has no effect when raw start is already after it."""
    coordinator = _make_coordinator({"min_sun_start": "06:00:00"})
    start = _dt(8)
    end = _dt(17)

    result_start, _ = _clamp(coordinator, start, end)

    assert result_start == start


# ---------------------------------------------------------------------------
# max_sun_start
# ---------------------------------------------------------------------------


def test_max_sun_start_clamps_late_raw_start():
    """max_sun_start pulls start back when raw start is after it."""
    coordinator = _make_coordinator({"max_sun_start": "09:00:00"})
    start = _dt(11)   # raw start at 11:00 — too late
    end = _dt(17)

    result_start, _ = _clamp(coordinator, start, end)

    assert result_start.hour == 9


def test_max_sun_start_no_effect_when_raw_is_earlier():
    """max_sun_start has no effect when raw start is already before it."""
    coordinator = _make_coordinator({"max_sun_start": "10:00:00"})
    start = _dt(7)
    end = _dt(17)

    result_start, _ = _clamp(coordinator, start, end)

    assert result_start == start


# ---------------------------------------------------------------------------
# min_sun_end
# ---------------------------------------------------------------------------


def test_min_sun_end_floors_early_raw_end():
    """min_sun_end pushes end forward when raw end is before it."""
    coordinator = _make_coordinator({"min_sun_end": "15:00:00"})
    start = _dt(8)
    end = _dt(12)   # raw end at 12:00 — too early

    _, result_end = _clamp(coordinator, start, end)

    assert result_end.hour == 15


def test_min_sun_end_no_effect_when_raw_is_later():
    """min_sun_end has no effect when raw end is already after it."""
    coordinator = _make_coordinator({"min_sun_end": "12:00:00"})
    start = _dt(8)
    end = _dt(17)

    _, result_end = _clamp(coordinator, start, end)

    assert result_end == end


# ---------------------------------------------------------------------------
# max_sun_end
# ---------------------------------------------------------------------------


def test_max_sun_end_caps_late_raw_end():
    """max_sun_end pulls end back when raw end is after it."""
    coordinator = _make_coordinator({"max_sun_end": "18:00:00"})
    start = _dt(8)
    end = _dt(20)   # raw end at 20:00 — too late

    _, result_end = _clamp(coordinator, start, end)

    assert result_end.hour == 18


def test_max_sun_end_no_effect_when_raw_is_earlier():
    """max_sun_end has no effect when raw end is already before it."""
    coordinator = _make_coordinator({"max_sun_end": "20:00:00"})
    start = _dt(8)
    end = _dt(17)

    _, result_end = _clamp(coordinator, start, end)

    assert result_end == end


# ---------------------------------------------------------------------------
# Elevation-based bounds
# ---------------------------------------------------------------------------


def test_elev_start_pushes_start_later():
    """elev_start is treated as a lower bound, pushing start forward."""
    coordinator = _make_coordinator({})
    start = _dt(6)
    end = _dt(17)
    elev_start = _dt(8)   # elevation crossing at 08:00

    result_start, _ = _clamp(coordinator, start, end, elev_start=elev_start)

    assert result_start == elev_start


def test_elev_end_pulls_end_earlier():
    """elev_end is treated as an upper bound, pulling end back."""
    coordinator = _make_coordinator({})
    start = _dt(8)
    end = _dt(19)
    elev_end = _dt(17)   # last time above threshold at 17:00

    _, result_end = _clamp(coordinator, start, end, elev_end=elev_end)

    assert result_end == elev_end


def test_elev_start_no_effect_when_raw_start_is_already_later():
    """elev_start has no effect when raw start is already past it."""
    coordinator = _make_coordinator({})
    start = _dt(10)
    end = _dt(17)
    elev_start = _dt(8)

    result_start, _ = _clamp(coordinator, start, end, elev_start=elev_start)

    assert result_start == start


def test_elev_end_no_effect_when_raw_end_is_already_earlier():
    """elev_end has no effect when raw end is already before it."""
    coordinator = _make_coordinator({})
    start = _dt(8)
    end = _dt(15)
    elev_end = _dt(17)

    _, result_end = _clamp(coordinator, start, end, elev_end=elev_end)

    assert result_end == end


# ---------------------------------------------------------------------------
# Combined: both min_sun_start and elev_start active — use the later of the two
# ---------------------------------------------------------------------------


def test_min_sun_start_and_elev_start_uses_later():
    """When both min_sun_start and elev_start are set, the later one wins."""
    coordinator = _make_coordinator({"min_sun_start": "07:00:00"})
    start = _dt(5)
    end = _dt(17)
    elev_start = _dt(9)   # elevation crossing is later

    result_start, _ = _clamp(coordinator, start, end, elev_start=elev_start)

    # elev_start (09:00) > min_sun_start (07:00), so 09:00 should win
    assert result_start.hour == 9


def test_min_sun_start_wins_over_elev_start_when_later():
    """When min_sun_start is later than elev_start, min_sun_start wins."""
    coordinator = _make_coordinator({"min_sun_start": "10:00:00"})
    start = _dt(5)
    end = _dt(17)
    elev_start = _dt(7)   # elevation crossing is earlier

    result_start, _ = _clamp(coordinator, start, end, elev_start=elev_start)

    assert result_start.hour == 10


# ---------------------------------------------------------------------------
# None inputs
# ---------------------------------------------------------------------------


def test_none_start_and_end_returns_none():
    """When both start and end are None, returns (None, None)."""
    coordinator = _make_coordinator({"min_sun_start": "08:00:00"})

    result_start, result_end = _clamp(coordinator, None, None)

    assert result_start is None
    assert result_end is None


# ---------------------------------------------------------------------------
# Inversion guard
# ---------------------------------------------------------------------------


def test_inversion_guard_reverts_to_raw():
    """Clamping that produces start > end should revert to raw times."""
    # min_sun_start = 15:00 but max_sun_end = 10:00 → clamped start > clamped end
    coordinator = _make_coordinator(
        {
            "min_sun_start": "15:00:00",
            "max_sun_end": "10:00:00",
        }
    )
    start = _dt(8)
    end = _dt(17)

    result_start, result_end = _clamp(coordinator, start, end)

    # Inversion guard: raw times returned
    assert result_start == start
    assert result_end == end
    coordinator.logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# "00:00:00" treated as disabled
# ---------------------------------------------------------------------------


def test_zero_time_string_treated_as_disabled():
    """'00:00:00' should be treated as 'not set', not as midnight."""
    coordinator = _make_coordinator({"min_sun_start": "00:00:00"})
    start = _dt(5)
    end = _dt(17)

    result_start, _ = _clamp(coordinator, start, end)

    # Should NOT clamp to midnight; raw start should be preserved
    assert result_start == start
