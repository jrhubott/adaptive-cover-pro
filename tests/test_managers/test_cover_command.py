"""Tests for CoverCommandService."""

from __future__ import annotations

import datetime as dt
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
def mock_hass():
    """Return a mock Home Assistant instance."""
    return MagicMock()


@pytest.fixture
def grace_mgr():
    """Return a mock GracePeriodManager."""
    return MagicMock()


@pytest.fixture
def cmd_svc(mock_hass, logger, grace_mgr):
    """Return a CoverCommandService for vertical blind (default)."""
    return CoverCommandService(
        hass=mock_hass,
        logger=logger,
        cover_type="cover_blind",
        grace_mgr=grace_mgr,
        open_close_threshold=50,
    )


@pytest.fixture
def tilt_cmd_svc(mock_hass, logger, grace_mgr):
    """Return a CoverCommandService for tilt cover."""
    return CoverCommandService(
        hass=mock_hass,
        logger=logger,
        cover_type="cover_tilt",
        grace_mgr=grace_mgr,
        open_close_threshold=50,
    )


# --- Initial state ---


def test_initial_state(cmd_svc):
    """Empty tracking dicts are initialised on construction."""
    assert not cmd_svc.waiting_entities()
    assert not list(cmd_svc.iter_targets())
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


def test_read_position_with_capabilities_position_cover(cmd_svc, mock_hass):
    """Reads current_position for position-capable non-tilt cover."""
    caps = {"has_set_position": True, "has_set_tilt_position": False}
    mock_hass.states.get.return_value = MagicMock(attributes={"current_position": 42})

    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.state_attr",
        return_value=42,
    ):
        result = cmd_svc._read_position_with_capabilities("cover.test", caps)

    assert result == 42


def test_read_position_with_capabilities_tilt_cover(tilt_cmd_svc, mock_hass):
    """Reads current_tilt_position for tilt cover."""
    caps = {"has_set_position": False, "has_set_tilt_position": True}

    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.state_attr",
        return_value=35,
    ):
        result = tilt_cmd_svc._read_position_with_capabilities("cover.test", caps)

    assert result == 35


def test_read_position_with_capabilities_state_obj(cmd_svc):
    """Uses state_obj attributes instead of mock_hass.states when provided."""
    caps = {"has_set_position": True}
    state_obj = MagicMock()
    state_obj.attributes = {"current_position": 75}

    result = cmd_svc._read_position_with_capabilities("cover.test", caps, state_obj)
    assert result == 75


def test_read_position_open_close_fallback(cmd_svc, mock_hass):
    """Falls back to get_open_close_state when has_set_position is False."""
    caps = {"has_set_position": False, "has_set_tilt_position": False}

    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.get_open_close_state",
        return_value=100,
    ):
        result = cmd_svc._read_position_with_capabilities("cover.test", caps)

    assert result == 100


# --- _check_position_delta ---


def test_check_position_delta_above_threshold(cmd_svc):
    """Returns True when delta exceeds min_change."""
    with patch.object(cmd_svc, "_get_current_position", return_value=50):
        assert cmd_svc._check_position_delta("cover.test", 75, 20, [0, 100]) is True


def test_check_position_delta_below_threshold(cmd_svc):
    """Returns False when delta is below min_change."""
    with patch.object(cmd_svc, "_get_current_position", return_value=50):
        assert cmd_svc._check_position_delta("cover.test", 55, 20, [0, 100]) is False


def test_check_position_delta_special_target_bypass(cmd_svc):
    """Returns True when target is a special position regardless of delta."""
    with patch.object(cmd_svc, "_get_current_position", return_value=50):
        assert cmd_svc._check_position_delta("cover.test", 0, 20, [0, 100]) is True
        assert cmd_svc._check_position_delta("cover.test", 100, 20, [0, 100]) is True


def test_check_position_delta_from_special_bypass(cmd_svc):
    """Returns True when moving FROM a special position regardless of delta."""
    with patch.object(cmd_svc, "_get_current_position", return_value=0):
        assert cmd_svc._check_position_delta("cover.test", 5, 20, [0, 100]) is True


def test_check_position_delta_none_position(cmd_svc):
    """Returns True (send command) when position is unavailable."""
    with patch.object(cmd_svc, "_get_current_position", return_value=None):
        assert cmd_svc._check_position_delta("cover.test", 50, 20, [0, 100]) is True


def test_check_position_delta_sun_just_appeared_bypass(cmd_svc):
    """Returns True when sun_just_appeared bypasses delta check."""
    with patch.object(cmd_svc, "_get_current_position", return_value=60):
        # Same position, delta=0, but sun_just_appeared overrides
        assert (
            cmd_svc._check_position_delta(
                "cover.test", 60, 5, [0, 100], sun_just_appeared=True
            )
            is True
        )


def test_check_position_delta_custom_special_positions(cmd_svc):
    """Custom special positions (default_height, sunset_pos) also bypass delta."""
    with patch.object(cmd_svc, "_get_current_position", return_value=50):
        assert cmd_svc._check_position_delta("cover.test", 40, 20, [0, 100, 40]) is True


# --- _check_time_delta ---


def test_check_time_delta_exceeds_threshold(cmd_svc):
    """Returns True when time since last update exceeds threshold."""
    old_time = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=10)
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
        return_value=old_time,
    ):
        assert cmd_svc._check_time_delta("cover.test", time_threshold=5) is True


def test_check_time_delta_below_threshold(cmd_svc):
    """Returns False when time since last update is below threshold."""
    recent_time = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=30)
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
        return_value=recent_time,
    ):
        assert cmd_svc._check_time_delta("cover.test", time_threshold=5) is False


def test_check_time_delta_no_last_updated(cmd_svc):
    """Returns True when entity has no last_updated time."""
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
        return_value=None,
    ):
        assert cmd_svc._check_time_delta("cover.test", time_threshold=5) is True


# --- _prepare_service_call ---


def test_prepare_service_call_position_cover(cmd_svc, grace_mgr):
    """Prepares set_cover_position service call for position-capable cover."""
    caps = {"has_set_position": True, "has_set_tilt_position": False}
    service, data, supports_position = cmd_svc._prepare_service_call(
        "cover.test", 75, caps=caps
    )
    assert service == "set_cover_position"
    assert data["position"] == 75
    assert data["entity_id"] == "cover.test"
    assert supports_position is True
    assert cmd_svc.is_waiting_for_target("cover.test") is True
    assert cmd_svc.get_target("cover.test") == 75
    grace_mgr.start_command_grace_period.assert_called_once_with("cover.test")


def test_prepare_service_call_tilt_cover(tilt_cmd_svc, grace_mgr):
    """Prepares set_cover_tilt_position service call for tilt cover."""
    caps = {"has_set_position": False, "has_set_tilt_position": True}
    service, data, supports_position = tilt_cmd_svc._prepare_service_call(
        "cover.test", 45, caps=caps
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
    service, data, supports_position = cmd_svc._prepare_service_call(
        "cover.test", 70, caps=caps
    )
    assert service == "open_cover"
    assert cmd_svc.get_target("cover.test") == 100
    assert supports_position is False


def test_prepare_service_call_close_cover(cmd_svc, grace_mgr):
    """Uses close_cover for position < threshold when has_set_position is False."""
    caps = {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }
    service, data, supports_position = cmd_svc._prepare_service_call(
        "cover.test", 30, caps=caps
    )
    assert service == "close_cover"
    assert cmd_svc.get_target("cover.test") == 0
    assert supports_position is False


def test_prepare_service_call_missing_open_close_caps(cmd_svc):
    """Returns (None, None, False) when open/close capabilities are missing."""
    caps = {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": False,
        "has_close": False,
    }
    service, data, supports_position = cmd_svc._prepare_service_call(
        "cover.test", 50, caps=caps
    )
    assert service is None
    assert data is None
    assert supports_position is False


def test_prepare_service_call_reset_retries_true_clears_state(cmd_svc, grace_mgr):
    """reset_retries=True (default) clears retry count and gave_up for new target."""
    cmd_svc.state("cover.test").retry_count = 2
    cmd_svc.state("cover.test").gave_up = True
    caps = {"has_set_position": True, "has_set_tilt_position": False}
    cmd_svc._prepare_service_call("cover.test", 60, caps=caps, reset_retries=True)
    assert cmd_svc.state("cover.test").retry_count == 0
    assert not cmd_svc.state("cover.test").gave_up


def test_prepare_service_call_reset_retries_false_preserves_state(cmd_svc, grace_mgr):
    """reset_retries=False preserves retry count and gave_up (reconciliation retries)."""
    cmd_svc.state("cover.test").retry_count = 2
    cmd_svc.state("cover.test").gave_up = True
    caps = {"has_set_position": True, "has_set_tilt_position": False}
    cmd_svc._prepare_service_call("cover.test", 60, caps=caps, reset_retries=False)
    assert cmd_svc.state("cover.test").retry_count == 2
    assert cmd_svc.state("cover.test").gave_up


# --- _track_action ---


def test_track_action_position_service(cmd_svc):
    """Records last_cover_action correctly for position-capable service."""
    cmd_svc.set_target("cover.test", 80)
    cmd_svc._track_action("cover.test", "set_cover_position", 80, True)

    action = cmd_svc.last_cover_action
    assert action["entity_id"] == "cover.test"
    assert action["service"] == "set_cover_position"
    assert action["position"] == 80
    assert action["calculated_position"] == 80
    assert action["threshold_used"] is None
    assert action["inverse_state_applied"] is False
    assert action["covers_controlled"] == 1
    assert action["timestamp"] is not None


def test_track_action_open_close_service(cmd_svc):
    """Records last_cover_action correctly for open/close service."""
    cmd_svc.set_target("cover.test", 100)
    cmd_svc._track_action("cover.test", "open_cover", 70, False)

    action = cmd_svc.last_cover_action
    assert action["position"] == 100  # target_call value
    assert action["threshold_used"] == 50
    assert action["covers_controlled"] == 1


def test_track_action_inverse_state(cmd_svc):
    """Records inverse_state_applied correctly."""
    cmd_svc.set_target("cover.test", 30)
    cmd_svc._track_action(
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
    assert action["current_position"] is None
    assert action["trigger"] is None
    assert action["inverse_state_applied"] is False
    assert action["timestamp"] is not None


def test_record_skipped_action_with_extras(cmd_svc):
    """Records reason-specific extras alongside base fields."""
    cmd_svc.record_skipped_action(
        "cover.bedroom",
        "delta_too_small",
        45,
        trigger="solar",
        current_position=42,
        inverse_state=True,
        extras={"position_delta": 3, "min_delta_required": 5},
    )

    action = cmd_svc.last_skipped_action
    assert action["entity_id"] == "cover.bedroom"
    assert action["trigger"] == "solar"
    assert action["current_position"] == 42
    assert action["inverse_state_applied"] is True
    assert action["position_delta"] == 3
    assert action["min_delta_required"] == 5


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

    # Verify it's used in subsequent _prepare_service_call
    caps = {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }
    # 70 < 75 threshold → should close
    cmd_svc._prepare_service_call("cover.test", 70, caps=caps)
    assert cmd_svc.get_target("cover.test") == 0


# --- _gave_up (max retry tracking) ---


def test_gave_up_cleared_on_new_target(cmd_svc, grace_mgr):
    """_gave_up entry is cleared when a new target is set via reset_retries=True."""
    cmd_svc.state("cover.test").gave_up = True
    cmd_svc.state("cover.test").retry_count = 3
    caps = {"has_set_position": True, "has_set_tilt_position": False}
    cmd_svc._prepare_service_call("cover.test", 80, caps=caps, reset_retries=True)
    assert not cmd_svc.state("cover.test").gave_up
    assert cmd_svc.state("cover.test").retry_count == 0


# --- build_special_positions ---


def test_build_special_positions_minimal():
    """Returns [0, 100] when no optional positions configured."""
    positions = build_special_positions({})
    assert positions == [0, 100]


def test_build_special_positions_with_options():
    """Includes default_height and sunset_pos when configured."""
    positions = build_special_positions({"default_percentage": 40, "sunset_pos": 10})
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


# --- Tilt-only entity under cover_blind config (bug fix coverage) ---


def test_prepare_service_call_tilt_only_under_cover_blind(mock_hass, logger, grace_mgr):
    """Tilt-only entity (features=240) under cover_blind must use set_cover_tilt_position."""
    svc = CoverCommandService(
        hass=mock_hass,
        logger=logger,
        cover_type="cover_blind",
        grace_mgr=grace_mgr,
        open_close_threshold=50,
    )
    # Caps that mimic supported_features=240 (tilt-only: SET_TILT_POSITION + OPEN_TILT + CLOSE_TILT + STOP_TILT)
    caps = {
        "has_set_position": False,
        "has_set_tilt_position": True,
        "has_open": False,
        "has_close": False,
    }
    service, data, supports_position = svc._prepare_service_call(
        "cover.tilt_only_blind", 45, caps=caps
    )
    assert service == "set_cover_tilt_position"
    assert data["tilt_position"] == 45
    assert data["entity_id"] == "cover.tilt_only_blind"
    assert supports_position is True


def test_read_position_tilt_only_under_cover_blind(mock_hass, logger, grace_mgr):
    """Tilt-only entity under cover_blind must read current_tilt_position, not current_position."""
    svc = CoverCommandService(
        hass=mock_hass,
        logger=logger,
        cover_type="cover_blind",
        grace_mgr=grace_mgr,
        open_close_threshold=50,
    )
    caps = {"has_set_position": False, "has_set_tilt_position": True}
    state_obj = MagicMock()
    state_obj.attributes = {"current_tilt_position": 60, "current_position": None}

    result = svc._read_position_with_capabilities(
        "cover.tilt_only_blind", caps, state_obj
    )
    assert result == 60
