"""Tests for helper functions."""

import datetime as dt
import zoneinfo
from unittest.mock import MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.helpers import (
    check_cover_features,
    check_time_passed,
    dt_check_time_passed,
    get_datetime_from_str,
    get_domain,
    get_last_updated,
    get_open_close_state,
    get_safe_state,
    get_timedelta_str,
    should_use_tilt,
)
from custom_components.adaptive_cover_pro.state.snapshot import CoverCapabilities


@pytest.mark.unit
def test_get_safe_state_returns_state(mock_hass):
    """Test get_safe_state returns state when available."""
    state_obj = MagicMock()
    state_obj.state = "25.5"
    mock_hass.states.get.return_value = state_obj

    result = get_safe_state(mock_hass, "sensor.temperature")

    assert result == "25.5"
    mock_hass.states.get.assert_called_once_with("sensor.temperature")


@pytest.mark.unit
def test_get_safe_state_returns_none_when_unknown(mock_hass):
    """Test get_safe_state returns None when state is unknown."""
    state_obj = MagicMock()
    state_obj.state = "unknown"
    mock_hass.states.get.return_value = state_obj

    result = get_safe_state(mock_hass, "sensor.temperature")

    assert result is None


@pytest.mark.unit
def test_get_safe_state_returns_none_when_unavailable(mock_hass):
    """Test get_safe_state returns None when state is unavailable."""
    state_obj = MagicMock()
    state_obj.state = "unavailable"
    mock_hass.states.get.return_value = state_obj

    result = get_safe_state(mock_hass, "sensor.temperature")

    assert result is None


@pytest.mark.unit
def test_get_safe_state_returns_none_when_entity_missing(mock_hass):
    """Test get_safe_state returns None when entity doesn't exist."""
    mock_hass.states.get.return_value = None

    result = get_safe_state(mock_hass, "sensor.nonexistent")

    assert result is None


@pytest.mark.unit
def test_get_domain_extracts_domain():
    """Test get_domain extracts domain from entity_id."""
    assert get_domain("sensor.temperature") == "sensor"
    assert get_domain("cover.living_room") == "cover"
    assert get_domain("binary_sensor.motion") == "binary_sensor"


@pytest.mark.unit
def test_get_domain_returns_none_for_none():
    """Test get_domain returns None when entity is None."""
    assert get_domain(None) is None


@pytest.mark.unit
def test_get_timedelta_str_parses_timedelta():
    """Test get_timedelta_str parses time strings."""
    result = get_timedelta_str("1 hour")
    assert result.total_seconds() == 3600

    result = get_timedelta_str("30 minutes")
    assert result.total_seconds() == 1800

    result = get_timedelta_str("2 days")
    assert result.total_seconds() == 172800


@pytest.mark.unit
def test_get_timedelta_str_returns_none_for_none():
    """Test get_timedelta_str returns None when input is None."""
    assert get_timedelta_str(None) is None


@pytest.mark.unit
def test_get_datetime_from_str_parses_datetime():
    """Test get_datetime_from_str parses datetime strings."""
    result = get_datetime_from_str("2024-01-15 10:30:00")
    assert result.year == 2024
    assert result.month == 1
    assert result.day == 15
    assert result.hour == 10
    assert result.minute == 30

    result = get_datetime_from_str("2024-01-15T10:30:00")
    assert result.year == 2024
    assert result.hour == 10


@pytest.mark.unit
def test_get_datetime_from_str_returns_none_for_none():
    """Test get_datetime_from_str returns None when input is None."""
    assert get_datetime_from_str(None) is None


@pytest.mark.unit
def test_get_datetime_from_str_converts_utc_iso_to_local_wallclock():
    """ISO 8601 UTC string is converted to local-time wall clock.

    A sun sensor value "2026-04-18T04:46:00+00:00" read in America/New_York
    (UTC-4 DST) must become 00:46 local naive, not 04:46.
    """
    ny = zoneinfo.ZoneInfo("America/New_York")
    with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", ny):
        result = get_datetime_from_str("2026-04-18T04:46:00+00:00")

    # In April NY is UTC-4, so 04:46 UTC == 00:46 local
    assert result.hour == 0
    assert result.minute == 46
    assert result.day == 18
    assert result.tzinfo is None


@pytest.mark.unit
def test_get_datetime_from_str_preserves_naive_static_time():
    """Static "06:30" has no tzinfo — parsed as-is, naive, wall-clock preserved."""
    ny = zoneinfo.ZoneInfo("America/New_York")
    with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", ny):
        result = get_datetime_from_str("06:30")

    assert result.hour == 6
    assert result.minute == 30
    assert result.tzinfo is None


@pytest.mark.unit
def test_get_datetime_from_str_preserves_naive_iso_datetime():
    """ISO string without tzinfo is treated as naive-local, not shifted."""
    ny = zoneinfo.ZoneInfo("America/New_York")
    with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", ny):
        result = get_datetime_from_str("2024-06-21T20:00:00")

    assert result == dt.datetime(2024, 6, 21, 20, 0, 0)
    assert result.tzinfo is None


@pytest.mark.unit
def test_get_last_updated_returns_timestamp(mock_hass):
    """Test get_last_updated returns last_updated timestamp."""
    last_updated = dt.datetime(2024, 1, 15, 10, 30, 0, tzinfo=dt.UTC)
    state_obj = MagicMock()
    state_obj.last_updated = last_updated
    mock_hass.states.get.return_value = state_obj

    result = get_last_updated("sensor.temperature", mock_hass)

    assert result == last_updated


@pytest.mark.unit
def test_get_last_updated_returns_none_when_entity_missing(mock_hass):
    """Test get_last_updated returns None when entity doesn't exist."""
    mock_hass.states.get.return_value = None

    result = get_last_updated("sensor.nonexistent", mock_hass)

    assert result is None


@pytest.mark.unit
def test_get_last_updated_returns_none_when_entity_id_none(mock_hass):
    """Test get_last_updated returns None when entity_id is None."""
    result = get_last_updated(None, mock_hass)

    assert result is None


@pytest.mark.unit
def test_check_time_passed_returns_true_when_passed():
    """Test check_time_passed returns True when time has passed."""
    # Create a datetime that's definitely in the past
    past_time = dt.datetime.now() - dt.timedelta(hours=1)

    result = check_time_passed(past_time)

    assert result is True


@pytest.mark.unit
def test_check_time_passed_returns_false_when_future():
    """Test check_time_passed returns False when time is in future."""
    # Create a datetime that's definitely in the future
    future_time = dt.datetime.now() + dt.timedelta(hours=1)

    result = check_time_passed(future_time)

    assert result is False


@pytest.mark.unit
def test_dt_check_time_passed_returns_true_when_passed_today():
    """Test dt_check_time_passed returns True when time passed today."""
    # Create a UTC datetime that's 1 hour ago
    past_time = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)

    result = dt_check_time_passed(past_time)

    assert result is True


@pytest.mark.unit
def test_dt_check_time_passed_returns_false_when_future_today():
    """Test dt_check_time_passed returns False when time is future today."""
    # Create a UTC datetime that's 1 hour from now
    future_time = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)

    result = dt_check_time_passed(future_time)

    assert result is False


@pytest.mark.unit
def test_dt_check_time_passed_returns_true_for_past_date():
    """Test dt_check_time_passed returns True for past dates."""
    # Create a UTC datetime from yesterday
    yesterday = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)

    result = dt_check_time_passed(yesterday)

    assert result is True


@pytest.mark.unit
def test_check_cover_features_detects_set_position(mock_hass):
    """Test check_cover_features detects SET_POSITION feature."""
    from homeassistant.components.cover import CoverEntityFeature

    state_obj = MagicMock()
    state_obj.attributes = {"supported_features": CoverEntityFeature.SET_POSITION}
    mock_hass.states.get.return_value = state_obj

    result = check_cover_features(mock_hass, "cover.test")

    assert result["has_set_position"] is True
    assert result["has_set_tilt_position"] is False
    assert result["has_open"] is False
    assert result["has_close"] is False


@pytest.mark.unit
def test_check_cover_features_detects_set_tilt_position(mock_hass):
    """Test check_cover_features detects SET_TILT_POSITION feature."""
    from homeassistant.components.cover import CoverEntityFeature

    state_obj = MagicMock()
    state_obj.attributes = {"supported_features": CoverEntityFeature.SET_TILT_POSITION}
    mock_hass.states.get.return_value = state_obj

    result = check_cover_features(mock_hass, "cover.test")

    assert result["has_set_position"] is False
    assert result["has_set_tilt_position"] is True
    assert result["has_open"] is False
    assert result["has_close"] is False


@pytest.mark.unit
def test_check_cover_features_detects_open_close(mock_hass):
    """Test check_cover_features detects OPEN and CLOSE features."""
    from homeassistant.components.cover import CoverEntityFeature

    state_obj = MagicMock()
    state_obj.attributes = {
        "supported_features": CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    }
    mock_hass.states.get.return_value = state_obj

    result = check_cover_features(mock_hass, "cover.test")

    assert result["has_set_position"] is False
    assert result["has_set_tilt_position"] is False
    assert result["has_open"] is True
    assert result["has_close"] is True


@pytest.mark.unit
def test_check_cover_features_detects_all_features(mock_hass):
    """Test check_cover_features detects all features combined."""
    from homeassistant.components.cover import CoverEntityFeature

    state_obj = MagicMock()
    state_obj.attributes = {
        "supported_features": (
            CoverEntityFeature.SET_POSITION
            | CoverEntityFeature.SET_TILT_POSITION
            | CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
        )
    }
    mock_hass.states.get.return_value = state_obj

    result = check_cover_features(mock_hass, "cover.test")

    assert result["has_set_position"] is True
    assert result["has_set_tilt_position"] is True
    assert result["has_open"] is True
    assert result["has_close"] is True


@pytest.mark.unit
def test_check_cover_features_returns_none_when_entity_missing(mock_hass):
    """Test check_cover_features returns None when entity missing."""
    mock_hass.states.get.return_value = None

    result = check_cover_features(mock_hass, "cover.nonexistent")

    assert result is None


@pytest.mark.unit
def test_check_cover_features_returns_optimistic_defaults_when_no_features(mock_hass):
    """Test check_cover_features returns optimistic defaults when no features attribute."""
    state_obj = MagicMock()
    state_obj.state = "closed"  # Entity is ready
    state_obj.attributes = {}  # No supported_features attribute
    mock_hass.states.get.return_value = state_obj

    result = check_cover_features(mock_hass, "cover.test")

    # Should return optimistic defaults when entity is ready but has no supported_features
    assert result["has_set_position"] is True
    assert result["has_set_tilt_position"] is False
    assert result["has_open"] is True
    assert result["has_close"] is True


@pytest.mark.unit
def test_check_cover_features_returns_none_when_unavailable(mock_hass):
    """Test check_cover_features returns None when entity unavailable."""
    state_obj = MagicMock()
    state_obj.state = "unavailable"
    state_obj.attributes = {"supported_features": 15}
    mock_hass.states.get.return_value = state_obj

    result = check_cover_features(mock_hass, "cover.test")

    assert result is None


@pytest.mark.unit
def test_check_cover_features_returns_none_when_unknown(mock_hass):
    """Test check_cover_features returns None when entity unknown."""
    state_obj = MagicMock()
    state_obj.state = "unknown"
    state_obj.attributes = {}
    mock_hass.states.get.return_value = state_obj

    result = check_cover_features(mock_hass, "cover.test")

    assert result is None


@pytest.mark.unit
def test_get_open_close_state_returns_0_when_closed(mock_hass):
    """Test get_open_close_state returns 0 for closed state."""
    state_obj = MagicMock()
    state_obj.state = "closed"
    mock_hass.states.get.return_value = state_obj

    result = get_open_close_state(mock_hass, "cover.test")

    assert result == 0


@pytest.mark.unit
def test_get_open_close_state_returns_100_when_open(mock_hass):
    """Test get_open_close_state returns 100 for open state."""
    state_obj = MagicMock()
    state_obj.state = "open"
    mock_hass.states.get.return_value = state_obj

    result = get_open_close_state(mock_hass, "cover.test")

    assert result == 100


@pytest.mark.unit
def test_get_open_close_state_returns_none_when_unknown(mock_hass):
    """Test get_open_close_state returns None for unknown state."""
    state_obj = MagicMock()
    state_obj.state = "unknown"
    mock_hass.states.get.return_value = state_obj

    result = get_open_close_state(mock_hass, "cover.test")

    assert result is None


@pytest.mark.unit
def test_get_open_close_state_returns_none_when_unavailable(mock_hass):
    """Test get_open_close_state returns None for unavailable state."""
    state_obj = MagicMock()
    state_obj.state = "unavailable"
    mock_hass.states.get.return_value = state_obj

    result = get_open_close_state(mock_hass, "cover.test")

    assert result is None


@pytest.mark.unit
def test_get_open_close_state_returns_none_when_entity_missing(mock_hass):
    """Test get_open_close_state returns None when entity doesn't exist."""
    mock_hass.states.get.return_value = None

    result = get_open_close_state(mock_hass, "cover.nonexistent")

    assert result is None


@pytest.mark.unit
def test_get_open_close_state_returns_none_for_other_states(mock_hass):
    """Test get_open_close_state returns None for other states."""
    state_obj = MagicMock()
    state_obj.state = "opening"
    mock_hass.states.get.return_value = state_obj

    result = get_open_close_state(mock_hass, "cover.test")

    assert result is None


# --- should_use_tilt ---


@pytest.mark.unit
def test_should_use_tilt_true_when_is_tilt_cover():
    """should_use_tilt returns True when is_tilt_cover is True, regardless of caps."""
    caps = {"has_set_position": True, "has_set_tilt_position": False}
    assert should_use_tilt(True, caps) is True


@pytest.mark.unit
def test_should_use_tilt_true_when_is_tilt_cover_empty_caps():
    """should_use_tilt returns True when is_tilt_cover is True, even with empty caps."""
    assert should_use_tilt(True, {}) is True


@pytest.mark.unit
def test_should_use_tilt_tilt_only_fallback_dict():
    """should_use_tilt returns True for tilt-only entity under non-tilt config (dict caps)."""
    caps = {"has_set_position": False, "has_set_tilt_position": True}
    assert should_use_tilt(False, caps) is True


@pytest.mark.unit
def test_should_use_tilt_false_when_has_set_position_dict():
    """should_use_tilt returns False when entity supports position (dict caps)."""
    caps = {"has_set_position": True, "has_set_tilt_position": True}
    assert should_use_tilt(False, caps) is False


@pytest.mark.unit
def test_should_use_tilt_false_open_close_only_dict():
    """should_use_tilt returns False for open/close-only entity (dict caps)."""
    caps = {"has_set_position": False, "has_set_tilt_position": False}
    assert should_use_tilt(False, caps) is False


@pytest.mark.unit
def test_should_use_tilt_false_empty_dict_defaults():
    """should_use_tilt returns False with empty dict (safe defaults)."""
    assert should_use_tilt(False, {}) is False


@pytest.mark.unit
def test_should_use_tilt_tilt_only_fallback_dataclass():
    """should_use_tilt returns True for tilt-only entity (CoverCapabilities dataclass)."""
    caps = CoverCapabilities(
        has_set_position=False,
        has_set_tilt_position=True,
        has_open=True,
        has_close=True,
    )
    assert should_use_tilt(False, caps) is True


@pytest.mark.unit
def test_should_use_tilt_false_when_has_set_position_dataclass():
    """should_use_tilt returns False when entity supports position (CoverCapabilities)."""
    caps = CoverCapabilities(
        has_set_position=True,
        has_set_tilt_position=True,
        has_open=True,
        has_close=True,
    )
    assert should_use_tilt(False, caps) is False


@pytest.mark.unit
def test_should_use_tilt_false_open_close_only_dataclass():
    """should_use_tilt returns False for open/close-only entity (CoverCapabilities)."""
    caps = CoverCapabilities(
        has_set_position=False,
        has_set_tilt_position=False,
        has_open=True,
        has_close=True,
    )
    assert should_use_tilt(False, caps) is False


# --- state_attr ---


@pytest.mark.unit
def test_state_attr_returns_none_when_entity_missing(mock_hass):
    """state_attr returns None when the entity does not exist."""
    from custom_components.adaptive_cover_pro.helpers import state_attr

    mock_hass.states.get.return_value = None

    assert state_attr(mock_hass, "sensor.nonexistent", "temperature") is None


@pytest.mark.unit
def test_state_attr_returns_none_when_attribute_missing(mock_hass):
    """state_attr returns None when the entity exists but the attribute is absent."""
    from custom_components.adaptive_cover_pro.helpers import state_attr
    from unittest.mock import MagicMock

    state_obj = MagicMock()
    state_obj.attributes = {}
    mock_hass.states.get.return_value = state_obj

    assert state_attr(mock_hass, "cover.test", "current_position") is None


@pytest.mark.unit
def test_state_attr_returns_value_when_present(mock_hass):
    """state_attr returns the attribute value when entity and attribute exist."""
    from custom_components.adaptive_cover_pro.helpers import state_attr
    from unittest.mock import MagicMock

    state_obj = MagicMock()
    state_obj.attributes = {"current_position": 42}
    mock_hass.states.get.return_value = state_obj

    assert state_attr(mock_hass, "cover.test", "current_position") == 42
