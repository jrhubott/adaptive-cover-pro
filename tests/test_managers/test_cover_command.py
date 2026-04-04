"""Tests for CoverCommandService."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
    build_special_positions,
)


@pytest.fixture
def logger():
    """Return a mock logger."""
    return MagicMock()


@pytest.fixture
def hass():
    """Return a mock Home Assistant instance."""
    return MagicMock()


@pytest.fixture
def grace_mgr():
    """Return a mock GracePeriodManager."""
    return MagicMock()


@pytest.fixture
def cmd_svc(hass, logger, grace_mgr):
    """Return a CoverCommandService for vertical blind (default)."""
    return CoverCommandService(
        hass=hass,
        logger=logger,
        cover_type="cover_blind",
        grace_mgr=grace_mgr,
        open_close_threshold=50,
    )


@pytest.fixture
def tilt_cmd_svc(hass, logger, grace_mgr):
    """Return a CoverCommandService for tilt cover."""
    return CoverCommandService(
        hass=hass,
        logger=logger,
        cover_type="cover_tilt",
        grace_mgr=grace_mgr,
        open_close_threshold=50,
    )


# --- Initial state ---


def test_initial_state(cmd_svc):
    """Empty tracking dicts are initialised on construction."""
    assert cmd_svc.wait_for_target == {}
    assert cmd_svc.target_call == {}
    assert cmd_svc.last_cover_action["entity_id"] is None
    assert cmd_svc.last_skipped_action["entity_id"] is None


# --- Capability detection ---


def test_get_cover_capabilities_default(cmd_svc):
    """Returns safe defaults when entity is not ready (check_cover_features returns None)."""
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
        return_value=None,
    ):
        caps = cmd_svc.get_cover_capabilities("cover.test")

    assert caps["has_set_position"] is True
    assert caps["has_set_tilt_position"] is False
    assert caps["has_open"] is True
    assert caps["has_close"] is True


def test_get_cover_capabilities_from_entity(cmd_svc):
    """Returns actual capabilities when entity is ready."""
    real_caps = {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
        return_value=real_caps,
    ):
        caps = cmd_svc.get_cover_capabilities("cover.test")

    assert caps == real_caps


# --- Position reading ---


def test_read_position_with_capabilities_position_cover(cmd_svc, hass):
    """Reads current_position for position-capable non-tilt cover."""
    caps = {"has_set_position": True, "has_set_tilt_position": False}
    hass.states.get.return_value = MagicMock(attributes={"current_position": 42})

    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.state_attr",
        return_value=42,
    ):
        result = cmd_svc._read_position_with_capabilities("cover.test", caps)

    assert result == 42


def test_read_position_with_capabilities_tilt_cover(tilt_cmd_svc, hass):
    """Reads current_tilt_position for tilt cover."""
    caps = {"has_set_position": False, "has_set_tilt_position": True}

    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.state_attr",
        return_value=35,
    ):
        result = tilt_cmd_svc._read_position_with_capabilities("cover.test", caps)

    assert result == 35


def test_read_position_with_capabilities_state_obj(cmd_svc):
    """Uses state_obj attributes instead of hass.states when provided."""
    caps = {"has_set_position": True}
    state_obj = MagicMock()
    state_obj.attributes = {"current_position": 75}

    result = cmd_svc._read_position_with_capabilities("cover.test", caps, state_obj)
    assert result == 75


def test_read_position_open_close_fallback(cmd_svc, hass):
    """Falls back to get_open_close_state when has_set_position is False."""
    caps = {"has_set_position": False, "has_set_tilt_position": False}

    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.get_open_close_state",
        return_value=100,
    ):
        result = cmd_svc._read_position_with_capabilities("cover.test", caps)

    assert result == 100


# --- check_position ---


def test_check_position_differs(cmd_svc):
    """Returns True when current position differs from target."""
    with patch.object(cmd_svc, "_get_current_position", return_value=30):
        assert cmd_svc.check_position("cover.test", 60) is True


def test_check_position_same(cmd_svc):
    """Returns False when current position matches target."""
    with patch.object(cmd_svc, "_get_current_position", return_value=60):
        assert cmd_svc.check_position("cover.test", 60) is False


def test_check_position_sun_just_appeared_bypass(cmd_svc):
    """Returns True when sun_just_appeared bypasses equality check."""
    with patch.object(cmd_svc, "_get_current_position", return_value=60):
        assert cmd_svc.check_position("cover.test", 60, sun_just_appeared=True) is True


def test_check_position_none_position(cmd_svc):
    """Returns False when current position is None (unavailable)."""
    with patch.object(cmd_svc, "_get_current_position", return_value=None):
        assert cmd_svc.check_position("cover.test", 60) is False


# --- check_position_delta ---


def test_check_position_delta_above_threshold(cmd_svc):
    """Returns True when delta exceeds min_change."""
    with patch.object(cmd_svc, "_get_current_position", return_value=50):
        result = cmd_svc.check_position_delta(
            "cover.test", 75, min_change=20, special_positions=[0, 100]
        )
    assert result is True


def test_check_position_delta_below_threshold(cmd_svc):
    """Returns False when delta is below min_change."""
    with patch.object(cmd_svc, "_get_current_position", return_value=50):
        result = cmd_svc.check_position_delta(
            "cover.test", 55, min_change=20, special_positions=[0, 100]
        )
    assert result is False


def test_check_position_delta_special_position_bypass(cmd_svc):
    """Returns True when target is a special position (0 or 100) regardless of delta."""
    with patch.object(cmd_svc, "_get_current_position", return_value=50):
        result = cmd_svc.check_position_delta(
            "cover.test", 0, min_change=20, special_positions=[0, 100]
        )
    assert result is True

    with patch.object(cmd_svc, "_get_current_position", return_value=50):
        result = cmd_svc.check_position_delta(
            "cover.test", 100, min_change=20, special_positions=[0, 100]
        )
    assert result is True


def test_check_position_delta_from_special_position_bypass(cmd_svc):
    """Returns True when moving FROM a special position regardless of delta."""
    with patch.object(cmd_svc, "_get_current_position", return_value=0):
        result = cmd_svc.check_position_delta(
            "cover.test", 5, min_change=20, special_positions=[0, 100]
        )
    assert result is True


def test_check_position_delta_none_position(cmd_svc):
    """Returns True when position is unavailable."""
    with patch.object(cmd_svc, "_get_current_position", return_value=None):
        result = cmd_svc.check_position_delta(
            "cover.test", 50, min_change=20, special_positions=[0, 100]
        )
    assert result is True


def test_check_position_delta_custom_special_positions(cmd_svc):
    """Custom special positions (default_height, sunset_pos) also bypass delta."""
    with patch.object(cmd_svc, "_get_current_position", return_value=50):
        # default_height=40 is special
        result = cmd_svc.check_position_delta(
            "cover.test", 40, min_change=20, special_positions=[0, 100, 40]
        )
    assert result is True


# --- check_time_delta ---


def test_check_time_delta_exceeds_threshold(cmd_svc):
    """Returns True when time since last update exceeds threshold."""
    import datetime as dt

    old_time = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=10)
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
        return_value=old_time,
    ):
        result = cmd_svc.check_time_delta("cover.test", time_threshold=5)
    assert result is True


def test_check_time_delta_below_threshold(cmd_svc):
    """Returns False when time since last update is below threshold."""
    import datetime as dt

    recent_time = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=30)
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
        return_value=recent_time,
    ):
        result = cmd_svc.check_time_delta("cover.test", time_threshold=5)
    assert result is False


def test_check_time_delta_no_last_updated(cmd_svc):
    """Returns True when entity has no last_updated time."""
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
        return_value=None,
    ):
        result = cmd_svc.check_time_delta("cover.test", time_threshold=5)
    assert result is True


# --- prepare_position_service_call ---


def test_prepare_service_call_position_cover(cmd_svc, grace_mgr):
    """Prepares set_cover_position service call for position-capable cover."""
    caps = {"has_set_position": True, "has_set_tilt_position": False}
    service, data, supports_position = cmd_svc.prepare_position_service_call(
        "cover.test", 75, caps
    )
    assert service == "set_cover_position"
    assert data["position"] == 75
    assert data["entity_id"] == "cover.test"
    assert supports_position is True
    assert cmd_svc.wait_for_target["cover.test"] is True
    assert cmd_svc.target_call["cover.test"] == 75
    grace_mgr.start_command_grace_period.assert_called_once_with("cover.test")


def test_prepare_service_call_tilt_cover(tilt_cmd_svc, grace_mgr):
    """Prepares set_cover_tilt_position service call for tilt cover."""
    caps = {"has_set_position": False, "has_set_tilt_position": True}
    service, data, supports_position = tilt_cmd_svc.prepare_position_service_call(
        "cover.test", 45, caps
    )
    assert service == "set_cover_tilt_position"
    assert data["tilt_position"] == 45
    assert supports_position is True


def test_prepare_service_call_open_cover(cmd_svc, grace_mgr):
    """Uses open_cover for position >= threshold when has_set_position is False."""
    caps = {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }
    service, data, supports_position = cmd_svc.prepare_position_service_call(
        "cover.test", 70, caps
    )
    assert service == "open_cover"
    assert cmd_svc.target_call["cover.test"] == 100
    assert supports_position is False


def test_prepare_service_call_close_cover(cmd_svc, grace_mgr):
    """Uses close_cover for position < threshold when has_set_position is False."""
    caps = {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }
    service, data, supports_position = cmd_svc.prepare_position_service_call(
        "cover.test", 30, caps
    )
    assert service == "close_cover"
    assert cmd_svc.target_call["cover.test"] == 0
    assert supports_position is False


def test_prepare_service_call_missing_open_close_caps(cmd_svc):
    """Returns (None, None, False) when open/close capabilities are missing."""
    caps = {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": False,
        "has_close": False,
    }
    service, data, supports_position = cmd_svc.prepare_position_service_call(
        "cover.test", 50, caps
    )
    assert service is None
    assert data is None
    assert supports_position is False


# --- track_cover_action ---


def test_track_cover_action_position_service(cmd_svc):
    """Records last_cover_action correctly for position-capable service."""
    cmd_svc.target_call["cover.test"] = 80
    cmd_svc.track_cover_action("cover.test", "set_cover_position", 80, True)

    action = cmd_svc.last_cover_action
    assert action["entity_id"] == "cover.test"
    assert action["service"] == "set_cover_position"
    assert action["position"] == 80
    assert action["calculated_position"] == 80
    assert action["threshold_used"] is None
    assert action["inverse_state_applied"] is False
    assert action["covers_controlled"] == 1
    assert action["timestamp"] is not None


def test_track_cover_action_open_close_service(cmd_svc):
    """Records last_cover_action correctly for open/close service."""
    cmd_svc.target_call["cover.test"] = 100
    cmd_svc.track_cover_action("cover.test", "open_cover", 70, False)

    action = cmd_svc.last_cover_action
    assert action["position"] == 100  # target_call value
    assert action["threshold_used"] == 50
    assert action["covers_controlled"] == 1


def test_track_cover_action_inverse_state(cmd_svc):
    """Records inverse_state_applied correctly."""
    cmd_svc.target_call["cover.test"] = 30
    cmd_svc.track_cover_action(
        "cover.test", "set_cover_position", 30, True, inverse_state=True
    )
    assert cmd_svc.last_cover_action["inverse_state_applied"] is True


# --- record_skipped_action ---


def test_record_skipped_action(cmd_svc):
    """Records skipped action details correctly."""
    cmd_svc.record_skipped_action("cover.bedroom", "Outside time window", 45)

    action = cmd_svc.last_skipped_action
    assert action["entity_id"] == "cover.bedroom"
    assert action["reason"] == "Outside time window"
    assert action["calculated_position"] == 45
    assert action["timestamp"] is not None


def test_record_skipped_action_overwrites_previous(cmd_svc):
    """Overwrites previous skipped action with new one."""
    cmd_svc.record_skipped_action("cover.bedroom", "Manual override active", 40)
    cmd_svc.record_skipped_action("cover.living_room", "Time delta too small", 60)

    assert cmd_svc.last_skipped_action["entity_id"] == "cover.living_room"
    assert cmd_svc.last_skipped_action["reason"] == "Time delta too small"


# --- update_threshold ---


def test_update_threshold(cmd_svc):
    """update_threshold changes the open/close threshold."""
    cmd_svc.update_threshold(75)
    assert cmd_svc._open_close_threshold == 75

    # Verify it's used in subsequent calls
    caps = {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }
    # 70 >= 75 is False so should close
    cmd_svc.prepare_position_service_call("cover.test", 70, caps)
    assert cmd_svc.target_call["cover.test"] == 0


# --- build_special_positions ---


def test_build_special_positions_minimal():
    """Returns [0, 100] when no optional positions configured."""
    positions = build_special_positions({})
    assert positions == [0, 100]


def test_build_special_positions_with_options():
    """Includes default_height and sunset_pos when configured."""
    positions = build_special_positions({"default_percentage": 40, "sunset_pos": 10})
    # build_special_positions uses CONF_DEFAULT_HEIGHT ("default_percentage") and CONF_SUNSET_POS ("sunset_pos")
    assert 0 in positions
    assert 100 in positions


def test_build_special_positions_with_actual_keys():
    """Uses CONF_DEFAULT_HEIGHT and CONF_SUNSET_POS constant values."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_DEFAULT_HEIGHT,
        CONF_SUNSET_POS,
    )

    positions = build_special_positions({CONF_DEFAULT_HEIGHT: 35, CONF_SUNSET_POS: 10})
    assert 35 in positions
    assert 10 in positions
    assert 0 in positions
    assert 100 in positions
