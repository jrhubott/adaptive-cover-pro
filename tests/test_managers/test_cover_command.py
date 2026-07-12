"""Tests for CoverCommandService."""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
    PositionContext,
    build_special_positions,
    route_service_call,
)
from custom_components.adaptive_cover_pro.managers.cover_command.gates import (
    check_position_delta,
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
        "custom_components.adaptive_cover_pro.cover_types.base.state_attr",
        return_value=42,
    ):
        result = cmd_svc._read_position_with_capabilities("cover.test", caps)

    assert result == 42


def test_read_position_with_capabilities_tilt_cover(tilt_cmd_svc, mock_hass):
    """Reads current_tilt_position for tilt cover."""
    caps = {"has_set_position": False, "has_set_tilt_position": True}

    with patch(
        "custom_components.adaptive_cover_pro.cover_types.base.state_attr",
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
        "custom_components.adaptive_cover_pro.cover_types.base.get_open_close_state",
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
    """Returns True for the loaded-but-unknown-position case (e.g. Z-Wave covers).

    The unloaded-entity case is gated upstream in apply_position via the
    cover_unavailable skip code, so a None here means the entity IS registered
    but its current_position attribute is unknown.
    """
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


def test_route_service_call_tilt_cover():
    """Routes to set_cover_tilt_position for a tilt cover with capable axis."""
    caps = {"has_set_position": False, "has_set_tilt_position": True}
    axis = get_policy("cover_tilt").select_default_axis(caps)
    plan = route_service_call(
        "cover.test",
        45,
        caps,
        axis=axis,
        use_my_position=False,
        open_close_threshold=50,
    )
    assert plan.service == "set_cover_tilt_position"
    assert plan.service_data["tilt_position"] == 45
    assert plan.supports_position is True


def test_prepare_service_call_endpoint_open_default_on(cmd_svc, grace_mgr):
    """Default-ON: position-capable blind commanded to 100 fires open_cover."""
    caps = {
        "has_set_position": True,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }
    service, data, supports_position = cmd_svc._prepare_service_call(
        "cover.test", 100, caps=caps
    )
    assert service == "open_cover"
    assert cmd_svc.get_target("cover.test") == 100
    assert supports_position is False


def test_prepare_service_call_endpoint_close_default_on(cmd_svc, grace_mgr):
    """Default-ON: position-capable blind commanded to 0 fires close_cover."""
    caps = {
        "has_set_position": True,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }
    service, data, supports_position = cmd_svc._prepare_service_call(
        "cover.test", 0, caps=caps
    )
    assert service == "close_cover"
    assert cmd_svc.get_target("cover.test") == 0
    assert supports_position is False


def test_prepare_service_call_endpoint_intermediate_default_on(cmd_svc, grace_mgr):
    """Default-ON: an intermediate target still uses set_cover_position."""
    caps = {
        "has_set_position": True,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }
    service, data, supports_position = cmd_svc._prepare_service_call(
        "cover.test", 60, caps=caps
    )
    assert service == "set_cover_position"
    assert data["position"] == 60
    assert supports_position is True


def test_update_endpoint_use_open_close_disables_substitution(cmd_svc, grace_mgr):
    """Turning the option off mid-session reverts 100 to set_cover_position."""
    caps = {
        "has_set_position": True,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }
    cmd_svc.update_endpoint_use_open_close(False)
    service, data, supports_position = cmd_svc._prepare_service_call(
        "cover.test", 100, caps=caps
    )
    assert service == "set_cover_position"
    assert data["position"] == 100
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


# --- Endpoint open/close substitution (issue #697) ---


def _endpoint_caps():
    return {
        "has_set_position": True,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }


def test_route_endpoint_open_cover_at_100():
    """state=100 routes to open_cover when endpoint_use_open_close is on."""
    caps = _endpoint_caps()
    axis = get_policy("cover_blind").select_default_axis(caps)
    plan = route_service_call(
        "cover.test",
        100,
        caps,
        axis=axis,
        use_my_position=False,
        open_close_threshold=50,
        endpoint_use_open_close=True,
    )
    assert plan.service == "open_cover"
    assert plan.routed_target == 100
    assert plan.supports_position is False
    assert plan.service_data == {"entity_id": "cover.test"}


def test_route_endpoint_close_cover_at_0():
    """state=0 routes to close_cover when endpoint_use_open_close is on."""
    caps = _endpoint_caps()
    axis = get_policy("cover_blind").select_default_axis(caps)
    plan = route_service_call(
        "cover.test",
        0,
        caps,
        axis=axis,
        use_my_position=False,
        open_close_threshold=50,
        endpoint_use_open_close=True,
    )
    assert plan.service == "close_cover"
    assert plan.routed_target == 0
    assert plan.supports_position is False
    assert plan.service_data == {"entity_id": "cover.test"}


def test_route_endpoint_intermediate_uses_set_position():
    """An intermediate target keeps set_cover_position even with the option on."""
    caps = _endpoint_caps()
    axis = get_policy("cover_blind").select_default_axis(caps)
    plan = route_service_call(
        "cover.test",
        50,
        caps,
        axis=axis,
        use_my_position=False,
        open_close_threshold=50,
        endpoint_use_open_close=True,
    )
    assert plan.service == "set_cover_position"
    assert plan.service_data["position"] == 50
    assert plan.supports_position is True
    assert plan.routed_target == 50


def test_route_endpoint_disabled_uses_set_position():
    """endpoint_use_open_close=False keeps the legacy set_cover_position(100)."""
    caps = _endpoint_caps()
    axis = get_policy("cover_blind").select_default_axis(caps)
    plan = route_service_call(
        "cover.test",
        100,
        caps,
        axis=axis,
        use_my_position=False,
        open_close_threshold=50,
        endpoint_use_open_close=False,
    )
    assert plan.service == "set_cover_position"
    assert plan.service_data["position"] == 100
    assert plan.supports_position is True
    assert plan.routed_target == 100


def test_route_endpoint_tilt_axis_is_no_op():
    """Tilt axis never substitutes open/close (would drive the carriage)."""
    caps = {"has_set_position": False, "has_set_tilt_position": True}
    axis = get_policy("cover_tilt").select_default_axis(caps)
    plan = route_service_call(
        "cover.test",
        100,
        caps,
        axis=axis,
        use_my_position=False,
        open_close_threshold=50,
        endpoint_use_open_close=True,
    )
    assert plan.service == "set_cover_tilt_position"
    assert plan.service_data["tilt_position"] == 100
    assert plan.supports_position is True


def test_route_endpoint_falls_back_when_no_open_capability():
    """Position-capable cover lacking open service falls back to set_position."""
    caps = {
        "has_set_position": True,
        "has_set_tilt_position": False,
        "has_open": False,
        "has_close": True,
    }
    axis = get_policy("cover_blind").select_default_axis(caps)
    plan = route_service_call(
        "cover.test",
        100,
        caps,
        axis=axis,
        use_my_position=False,
        open_close_threshold=50,
        endpoint_use_open_close=True,
    )
    assert plan.service == "set_cover_position"
    assert plan.service_data["position"] == 100
    assert plan.supports_position is True


def test_route_endpoint_falls_back_when_no_close_capability():
    """Position-capable cover lacking close service falls back to set_position."""
    caps = {
        "has_set_position": True,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": False,
    }
    axis = get_policy("cover_blind").select_default_axis(caps)
    plan = route_service_call(
        "cover.test",
        0,
        caps,
        axis=axis,
        use_my_position=False,
        open_close_threshold=50,
        endpoint_use_open_close=True,
    )
    assert plan.service == "set_cover_position"
    assert plan.service_data["position"] == 0
    assert plan.supports_position is True


def test_route_service_call_missing_open_close_caps():
    """Returns no service when no capable HA service is available."""
    caps = {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": False,
        "has_close": False,
    }
    axis = get_policy("cover_blind").select_default_axis(caps)
    plan = route_service_call(
        "cover.test",
        50,
        caps,
        axis=axis,
        use_my_position=False,
        open_close_threshold=50,
    )
    assert plan.service is None
    assert plan.service_data is None
    assert plan.supports_position is False


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


def test_position_tolerance_ctor_arg_honored(mock_hass, logger, grace_mgr):
    """A configured position_tolerance widens the check_target_reached band (issue #507)."""
    svc = CoverCommandService(
        hass=mock_hass,
        logger=logger,
        cover_type="cover_blind",
        grace_mgr=grace_mgr,
        position_tolerance=6,
    )
    assert svc._position_tolerance == 6
    svc.set_target("cover.test", 100)
    # 94 vs 100 → gap 6 ≤ 6 tolerance → reached.
    assert svc.check_target_reached("cover.test", 94) is True
    svc.set_target("cover.test", 100)
    # 93 vs 100 → gap 7 > 6 tolerance → not reached.
    assert svc.check_target_reached("cover.test", 93) is False


def test_update_position_tolerance(mock_hass, logger, grace_mgr):
    """update_position_tolerance mutates the backing field (issue #507)."""
    svc = CoverCommandService(
        hass=mock_hass,
        logger=logger,
        cover_type="cover_blind",
        grace_mgr=grace_mgr,
    )
    svc.update_position_tolerance(10)
    assert svc._position_tolerance == 10
    svc.set_target("cover.test", 100)
    # 91 vs 100 → gap 9 ≤ 10 tolerance → reached.
    assert svc.check_target_reached("cover.test", 91) is True


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


# --- enforce_delta_at_endpoints (issue #679) ---


def test_special_positions_omits_endpoints_when_enforced(logger):
    """Flag ON → the 0/100 endpoints are dropped from the special list (#679).

    Non-endpoint specials (default/sunset/my) are still bypassed, and with the
    endpoints gone the delta gate now applies to a target of 0: a 3 → 0 move
    below min_change=5 is gated out (False).
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_DEFAULT_HEIGHT,
        CONF_ENFORCE_DELTA_AT_ENDPOINTS,
    )

    positions = build_special_positions(
        {CONF_ENFORCE_DELTA_AT_ENDPOINTS: True, CONF_DEFAULT_HEIGHT: 40}
    )
    assert 0 not in positions
    assert 100 not in positions
    assert 40 in positions
    # Delta gate now runs for the 0 endpoint: 3 → 0 below min_change=5 → gated.
    assert (
        check_position_delta("cover.x", 0, 5, positions, position=3, logger=logger)
        is False
    )


def test_endpoint_100_gated_when_enforced(logger):
    """Flag ON → a near-100 target below min_change is gated (#679)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENFORCE_DELTA_AT_ENDPOINTS,
    )

    positions = build_special_positions({CONF_ENFORCE_DELTA_AT_ENDPOINTS: True})
    assert 100 not in positions
    assert (
        check_position_delta("cover.x", 100, 5, positions, position=97, logger=logger)
        is False
    )


def test_endpoint_current_side_gated_when_enforced(logger):
    """Flag ON → a move FROM the 0 endpoint to a near position is also gated.

    With endpoints removed the ``position in _special`` bypass no longer fires
    either, so 0 → 3 below min_change=5 is gated out.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENFORCE_DELTA_AT_ENDPOINTS,
    )

    positions = build_special_positions({CONF_ENFORCE_DELTA_AT_ENDPOINTS: True})
    assert (
        check_position_delta("cover.x", 3, 5, positions, position=0, logger=logger)
        is False
    )


def test_endpoints_bypass_delta_when_not_enforced(logger):
    """Flag OFF (default) → 0/100 still bypass delta (issue #629 regression lock).

    This is the byte-for-byte guarantee that must not regress: a target of 0
    from position 3 with min_change=5 bypasses the gate (True) because 0 stays
    in the special list.
    """
    positions = build_special_positions({})
    assert 0 in positions
    assert 100 in positions
    assert (
        check_position_delta("cover.x", 0, 5, positions, position=3, logger=logger)
        is True
    )


# --- enforce_delta_at_endpoints: endpoint re-injection via config values (#679 reopen) ---


def test_enforce_endpoints_drops_endpoint_when_sunset_position_is_0(logger):
    """enforce=True + sunset_position=0 must NOT re-inject 0 into specials (#679 reopen).

    The original v2.30.0 fix dropped [0, 100] from the seed but still appended
    sunset_position unconditionally. When sunset_position=0 (the reporter's exact
    config) the endpoint 0 was re-added, and check_position_delta bypassed the gate
    for target 0.  The fix must filter 0/100 AFTER appending config values.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENFORCE_DELTA_AT_ENDPOINTS,
        CONF_SUNSET_POS,
    )

    options = {CONF_ENFORCE_DELTA_AT_ENDPOINTS: True, CONF_SUNSET_POS: 0}
    positions = build_special_positions(options)
    assert 0 not in positions
    # 2 → 0, delta=2 < min_change=5; no longer bypassed — gate must return False.
    assert (
        check_position_delta("cover.x", 0, 5, positions, position=2, logger=logger)
        is False
    )


def test_enforce_endpoints_drops_endpoint_when_default_percentage_is_100(logger):
    """enforce=True + default_percentage=100 must NOT re-inject 100 into specials (#679 reopen).

    When default_percentage=100 (the reporter's exact config) the endpoint 100 was
    re-appended after the initial seed was dropped, letting 97→100 bypass the gate.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_DEFAULT_HEIGHT,
        CONF_ENFORCE_DELTA_AT_ENDPOINTS,
    )

    options = {CONF_ENFORCE_DELTA_AT_ENDPOINTS: True, CONF_DEFAULT_HEIGHT: 100}
    positions = build_special_positions(options)
    assert 100 not in positions
    # 97 → 100, delta=3 < min_change=5; endpoint must now be gated — False.
    assert (
        check_position_delta("cover.x", 100, 5, positions, position=97, logger=logger)
        is False
    )


def test_enforce_endpoints_off_keeps_0_and_100_via_config_values(logger):
    """Regression lock: flag OFF → 0/100 from config values remain in specials (#629).

    default_percentage=100 and sunset_position=0 must still bypass the gate when
    enforce_delta_at_endpoints is False (the default).
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_DEFAULT_HEIGHT,
        CONF_ENFORCE_DELTA_AT_ENDPOINTS,
        CONF_SUNSET_POS,
    )

    options = {
        CONF_ENFORCE_DELTA_AT_ENDPOINTS: False,
        CONF_DEFAULT_HEIGHT: 100,
        CONF_SUNSET_POS: 0,
    }
    positions = build_special_positions(options)
    assert 0 in positions
    assert 100 in positions
    # Both endpoints still bypass: gate returns True for near-endpoint moves.
    assert (
        check_position_delta("cover.x", 0, 5, positions, position=2, logger=logger)
        is True
    )
    assert (
        check_position_delta("cover.x", 100, 5, positions, position=97, logger=logger)
        is True
    )


def test_enforce_endpoints_mid_range_sunset_still_bypasses(logger):
    """enforce=True + sunset_position=40 → 40 remains special (only 0/100 are filtered)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENFORCE_DELTA_AT_ENDPOINTS,
        CONF_SUNSET_POS,
    )

    options = {CONF_ENFORCE_DELTA_AT_ENDPOINTS: True, CONF_SUNSET_POS: 40}
    positions = build_special_positions(options)
    assert 40 in positions
    assert 0 not in positions
    assert 100 not in positions
    # 38 → 40, delta=2 < min_change=5 but target IS special (40) → bypassed (True).
    assert (
        check_position_delta("cover.x", 40, 5, positions, position=38, logger=logger)
        is True
    )


# --- Tilt-only entity under cover_blind config (bug fix coverage) ---


def test_route_service_call_tilt_only_under_cover_blind():
    """Tilt-only entity (features=240) under cover_blind routes to set_cover_tilt_position."""
    # Caps that mimic supported_features=240 (tilt-only: SET_TILT_POSITION + OPEN_TILT + CLOSE_TILT + STOP_TILT)
    caps = {
        "has_set_position": False,
        "has_set_tilt_position": True,
        "has_open": False,
        "has_close": False,
    }
    # cover_blind's policy promotes the tilt axis when only tilt is capable.
    axis = get_policy("cover_blind").select_default_axis(caps)
    plan = route_service_call(
        "cover.tilt_only_blind",
        45,
        caps,
        axis=axis,
        use_my_position=False,
        open_close_threshold=50,
    )
    assert plan.service == "set_cover_tilt_position"
    assert plan.service_data["tilt_position"] == 45
    assert plan.service_data["entity_id"] == "cover.tilt_only_blind"
    assert plan.supports_position is True


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


# --- apply_position cover_unavailable gate (issue #342) ---


def _ctx() -> PositionContext:
    """Build a PositionContext that passes every gate downstream of cover_unavailable."""
    return PositionContext(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=5,
        time_threshold=0,
        special_positions=[0, 100],
    )


@pytest.mark.asyncio
async def test_apply_position_skips_when_entity_state_missing(cmd_svc, mock_hass):
    """Issue #342: skip with cover_unavailable when hass.states.get returns None.

    On HA restart, cover entities may not yet be registered in the state machine.
    Issuing a service call against an unregistered entity emits a HA warning
    and (on platforms that queue commands) executes once the entity loads,
    moving the cover to the wrong position.
    """
    mock_hass.states.get.return_value = None
    mock_hass.services.async_call = AsyncMock()

    outcome, reason = await cmd_svc.apply_position(
        "cover.unloaded", 100, "startup", _ctx()
    )

    assert (outcome, reason) == ("skipped", "cover_unavailable")
    mock_hass.services.async_call.assert_not_called()
    assert cmd_svc.last_skipped_action["reason"] == "cover_unavailable"
    assert cmd_svc.last_skipped_action["entity_id"] == "cover.unloaded"


@pytest.mark.asyncio
async def test_apply_position_skips_when_entity_state_unavailable(cmd_svc, mock_hass):
    """Issue #342: skip with cover_unavailable when state.state == 'unavailable'.

    Some platforms (e.g. Homematic IP) register the entity early but report
    unavailable until the device is reachable; commands against it are queued
    and replayed once available, producing the same wrong-position symptom.
    """
    mock_hass.states.get.return_value = MagicMock(state="unavailable", attributes={})
    mock_hass.services.async_call = AsyncMock()

    outcome, reason = await cmd_svc.apply_position(
        "cover.unavailable", 100, "startup", _ctx()
    )

    assert (outcome, reason) == ("skipped", "cover_unavailable")
    mock_hass.services.async_call.assert_not_called()
    assert cmd_svc.last_skipped_action["reason"] == "cover_unavailable"


@pytest.mark.asyncio
async def test_apply_position_proceeds_when_state_loaded_with_unknown_position(
    cmd_svc, mock_hass
):
    """Loaded entity with current_position=None must NOT trigger cover_unavailable.

    Z-Wave covers commonly report a real state (open/closed) without a numeric
    current_position attribute. The new gate must only short-circuit when the
    entity itself is unloaded or marked unavailable — not for unknown position.
    """
    mock_hass.states.get.return_value = MagicMock(
        state="open",
        attributes={"current_position": None, "supported_features": 15},
    )
    mock_hass.services.async_call = AsyncMock(return_value=None)

    with (
        patch.object(cmd_svc, "_get_current_position", return_value=None),
        patch.object(cmd_svc, "_check_position_delta", return_value=True),
        patch.object(cmd_svc, "_check_time_delta", return_value=True),
        patch.object(
            cmd_svc,
            "_prepare_service_call",
            return_value=("set_cover_position", {"entity_id": "cover.zw"}, True),
        ),
    ):
        outcome, reason = await cmd_svc.apply_position("cover.zw", 50, "solar", _ctx())

    assert reason != "cover_unavailable"


# --- same-position exact-equality gate + reconciliation tolerance (issues #507/#567) ---


def _ctx_with_special(*, use_my_position: bool = False) -> PositionContext:
    """PositionContext that passes every gate except same-position.

    ``use_my_position=True`` selects the My-preset routing variant (issue
    #779 follow-up) so tests can exercise the ``stop_cover``/My path through
    the same shared context builder used by every other test in this file.
    """
    return PositionContext(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=10,
        time_threshold=0,
        special_positions=[0, 100],
        use_my_position=use_my_position,
    )


def _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance: int):
    """CoverCommandService with a specific position_tolerance."""
    svc = CoverCommandService(
        hass=mock_hass,
        logger=logger,
        cover_type="cover_blind",
        grace_mgr=grace_mgr,
        position_tolerance=tolerance,
    )
    return svc


def _stub_state(mock_hass, current_position: int, state: str = "open") -> None:
    """Wire mock_hass so apply_position sees an available cover at *current_position*.

    ``state`` is the HA entity state string (e.g. "open", "closed") used by the
    mechanical-stop check (issue #755).
    """
    state_obj = MagicMock()
    state_obj.state = state
    state_obj.attributes = {
        "current_position": current_position,
        "supported_features": 15,
    }
    mock_hass.states.get.return_value = state_obj
    mock_hass.services.async_call = AsyncMock(return_value=None)


# --- endpoint-only tolerance (issue #507 regression fix) ---


@pytest.mark.asyncio
async def test_endpoint_target_within_tolerance_skips_on_main_path(
    mock_hass, logger, grace_mgr
):
    """Issue #507 regression — cover at 98, target 100, tolerance=8: SKIPPED.

    A motor that physically settles 2% short of the open endpoint (100) must
    not be re-commanded every solar update cycle.  The same-position gate
    applies a tolerance band when the target is a hard endpoint (0 or 100),
    so abs(98-100)=2 <= 8 is treated as same_position and the command is not
    sent.  The band is limited to endpoints so that mid-range tracking moves
    (issue #567) keep using exact equality.
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=8)
    _stub_state(mock_hass, current_position=98)

    with (
        patch.object(svc, "_get_current_position", return_value=98),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("set_cover_position", {"entity_id": "cover.test"}, True),
        ),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test", 100, "solar", _ctx_with_special()
        )

    assert outcome == "skipped"
    assert reason == "same_position"
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_endpoint_zero_within_tolerance_skips_on_main_path(
    mock_hass, logger, grace_mgr
):
    """Issue #507 regression — cover at 2, target 0, tolerance=8: SKIPPED.

    Symmetric case for the closed endpoint: a motor pinned 2% above fully
    closed must not be re-commanded every cycle.  The endpoint tolerance band
    applies to target=0 just as it does to target=100.
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=8)
    _stub_state(mock_hass, current_position=2)

    with (
        patch.object(svc, "_get_current_position", return_value=2),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("set_cover_position", {"entity_id": "cover.test"}, True),
        ),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test", 0, "solar", _ctx_with_special()
        )

    assert outcome == "skipped"
    assert reason == "same_position"
    mock_hass.services.async_call.assert_not_called()


# --- full mechanical endpoint forcing (issue #755) ---


def _ctx_full_endpoint(
    *, full_endpoint_target: bool, tilt: int | None
) -> PositionContext:
    """PositionContext for a venetian whose desired final state is a full endpoint."""
    return PositionContext(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=10,
        time_threshold=0,
        special_positions=[0, 100],
        tilt=tilt,
        full_endpoint_target=full_endpoint_target,
    )


@pytest.mark.asyncio
async def test_full_endpoint_zero_forces_close_cover_when_not_closed(
    mock_hass, logger, grace_mgr
):
    """Issue #755 — venetian 0/0 within tolerance but not mechanically closed: SENT.

    Cover reports current_position=3 and HA state "open". The full-endpoint
    flag is set (0/0), endpoint_use_open_close is on, so the same-position band
    must be bypassed and close_cover issued.
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=3)
    _stub_state(mock_hass, current_position=3, state="open")

    with (
        patch.object(svc, "_get_current_position", return_value=3),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("close_cover", {"entity_id": "cover.test"}, True),
        ),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test",
            0,
            "custom_position",
            _ctx_full_endpoint(full_endpoint_target=True, tilt=0),
        )

    assert outcome == "sent"
    assert reason == "close_cover"
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_full_endpoint_hundred_forces_open_cover_when_not_open(
    mock_hass, logger, grace_mgr
):
    """Issue #755 — venetian 100/100 within tolerance but not mechanically open: SENT."""
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=3)
    _stub_state(mock_hass, current_position=97, state="closed")

    with (
        patch.object(svc, "_get_current_position", return_value=97),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("open_cover", {"entity_id": "cover.test"}, True),
        ),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test",
            100,
            "custom_position",
            _ctx_full_endpoint(full_endpoint_target=True, tilt=100),
        )

    assert outcome == "sent"
    assert reason == "open_cover"
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_solar_zero_tilt_open_does_not_force(mock_hass, logger, grace_mgr):
    """Issue #679 regression guard — solar 0/100 is NOT a full endpoint: SKIPPED.

    Position 0 with tilt 100 is a legitimate non-endpoint state. With the
    full-endpoint flag absent, the existing same-position band still suppresses
    the no-op move at current_position=3.
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=3)
    _stub_state(mock_hass, current_position=3, state="open")

    with (
        patch.object(svc, "_get_current_position", return_value=3),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("close_cover", {"entity_id": "cover.test"}, True),
        ),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test",
            0,
            "solar",
            _ctx_full_endpoint(full_endpoint_target=False, tilt=100),
        )

    assert outcome == "skipped"
    assert reason == "same_position"
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_full_endpoint_zero_already_closed_skips(mock_hass, logger, grace_mgr):
    """Issue #755 idempotency — already mechanically closed (state closed): SKIPPED."""
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=3)
    _stub_state(mock_hass, current_position=0, state="closed")

    with (
        patch.object(svc, "_get_current_position", return_value=0),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("close_cover", {"entity_id": "cover.test"}, True),
        ),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test",
            0,
            "custom_position",
            _ctx_full_endpoint(full_endpoint_target=True, tilt=0),
        )

    assert outcome == "skipped"
    assert reason == "same_position"
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_full_endpoint_no_force_when_open_close_disabled(
    mock_hass, logger, grace_mgr
):
    """Issue #755 scoping (#507 guard) — endpoint_use_open_close off: SKIPPED.

    The forcing is scoped to the endpoint_use_open_close feature. With it
    disabled the same-position band behaves exactly as before #755.
    """
    svc = CoverCommandService(
        hass=mock_hass,
        logger=logger,
        cover_type="cover_blind",
        grace_mgr=grace_mgr,
        position_tolerance=3,
        endpoint_use_open_close=False,
    )
    _stub_state(mock_hass, current_position=3, state="open")

    with (
        patch.object(svc, "_get_current_position", return_value=3),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("close_cover", {"entity_id": "cover.test"}, True),
        ),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test",
            0,
            "custom_position",
            _ctx_full_endpoint(full_endpoint_target=True, tilt=0),
        )

    assert outcome == "skipped"
    assert reason == "same_position"
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_full_endpoint_inverted_zero_forces_open_cover(
    mock_hass, logger, grace_mgr
):
    """Issue #755 inversion lock — post-inverse 0/0 intent arrives as 100: SENT.

    Inversion happens upstream of apply_position. ACP's 0/0 intent under
    inverse_state becomes position 100; the manager adds no inversion logic and
    simply forces open_cover because the cover is not mechanically open.
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=3)
    _stub_state(mock_hass, current_position=97, state="closed")

    with (
        patch.object(svc, "_get_current_position", return_value=97),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("open_cover", {"entity_id": "cover.test"}, True),
        ),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test",
            100,
            "custom_position",
            _ctx_full_endpoint(full_endpoint_target=True, tilt=100),
        )

    assert outcome == "sent"
    assert reason == "open_cover"
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_apply_position_within_tolerance_band_skips_endpoint(
    mock_hass, logger, grace_mgr
):
    """Issue #507 regression lock — cover at 98, target 100, tolerance=8: SKIPPED.

    The same-position gate applies _position_tolerance when the target is a
    hard endpoint (0 or 100).  abs(98-100)=2 <= 8, so the cover is treated as
    at the target and the command is suppressed, preventing relay clicks when a
    motor physically settles a few percent short of its mechanical stop.
    Mid-range targets keep exact-equality semantics (issue #567 preserved).
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=8)
    _stub_state(mock_hass, current_position=98)

    with (
        patch.object(svc, "_get_current_position", return_value=98),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("set_cover_position", {"entity_id": "cover.test"}, True),
        ),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test", 100, "default", _ctx_with_special()
        )

    assert outcome == "skipped"
    assert reason == "same_position"
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_force_endpoint_within_tolerance_skips_command_gate(
    mock_hass, logger, grace_mgr
):
    """Issue #507 regression lock — force/safety endpoint within tolerance: SKIPPED.

    The same-position gate applies endpoint-tolerance BEFORE the force/safety
    bypass path.  Cover at 98, target 100, tolerance=8: abs(98-100)=2 <= 8 so
    the gate fires and no command is sent, even with force=True / is_safety=True.
    This prevents the relay-click storm that triggered issue #507.
    The reconciliation give-up path (test_reconcile_gives_up_on_unreachable_target)
    handles targets that are genuinely out of tolerance.
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=8)
    _stub_state(mock_hass, current_position=98)

    ctx = PositionContext(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=10,
        time_threshold=0,
        special_positions=[0, 100],
        force=True,
        is_safety=True,
    )

    with (
        patch.object(svc, "_get_current_position", return_value=98),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("set_cover_position", {"entity_id": "cover.test"}, True),
        ),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test", 100, "force_override", ctx
        )

    assert outcome == "skipped"
    assert reason == "same_position"
    mock_hass.services.async_call.assert_not_called()


# --- non-venetian (single-axis) full endpoint forcing + latch (issue #897) ---


def _endpoint_service(
    entity_id, position, inverse_state=False, is_safety=False, use_my_position=False
):
    """_prepare_service_call side_effect: route 0→close, 100→open, else set_position."""
    if position == 0:
        return ("close_cover", {"entity_id": entity_id}, True)
    if position == 100:
        return ("open_cover", {"entity_id": entity_id}, True)
    return ("set_cover_position", {"entity_id": entity_id, "position": position}, True)


def _sent_services(mock_hass) -> list[str]:
    """Return the cover service name from every async_call (positional arg 1)."""
    return [call.args[1] for call in mock_hass.services.async_call.call_args_list]


@pytest.mark.asyncio
async def test_non_venetian_endpoint_close_fires_once_when_never_reports_closed(
    mock_hass, logger, grace_mgr
):
    """Issue #897 — single-axis blind, target 0, state never 'closed': fire once.

    A ZHA rollershade that settles at current_position=2 and reports HA state
    'open' forever must route close_cover exactly once (so it can seat at the
    mechanical stop), then be latched so it does not relay-click every cycle
    (#507 anti-storm preserved for the generalized path).
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=3)
    _stub_state(mock_hass, current_position=2, state="open")
    ctx = _ctx_full_endpoint(full_endpoint_target=True, tilt=None)

    with (
        patch.object(svc, "_get_current_position", return_value=2),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(svc, "_prepare_service_call", side_effect=_endpoint_service),
    ):
        outcome1 = await svc.apply_position("cover.test", 0, "custom_position", ctx)
        outcome2 = await svc.apply_position("cover.test", 0, "custom_position", ctx)

    assert outcome1 == ("sent", "close_cover")
    assert outcome2 == ("skipped", "same_position")
    assert mock_hass.services.async_call.call_count == 1


@pytest.mark.asyncio
async def test_endpoint_latch_clears_on_midrange_move(mock_hass, logger, grace_mgr):
    """Issue #897 — a mid-range move clears the endpoint latch so 0 re-fires.

    Force-close to 0 arms the latch; a mid-range 50 move (no endpoint flag)
    sends and clears it; commanding 0 again re-routes close_cover.
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=3)
    _stub_state(mock_hass, current_position=2, state="open")

    close_ctx = _ctx_full_endpoint(full_endpoint_target=True, tilt=None)
    mid_ctx = _ctx_full_endpoint(full_endpoint_target=False, tilt=None)

    with (
        patch.object(svc, "_get_current_position", return_value=2),
        patch.object(svc, "_check_position_delta", return_value=True),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(svc, "_prepare_service_call", side_effect=_endpoint_service),
    ):
        await svc.apply_position("cover.test", 0, "custom_position", close_ctx)
        await svc.apply_position("cover.test", 50, "solar", mid_ctx)
        await svc.apply_position("cover.test", 0, "custom_position", close_ctx)

    assert _sent_services(mock_hass).count("close_cover") == 2


@pytest.mark.asyncio
async def test_endpoint_latch_refires_on_endpoint_flip(mock_hass, logger, grace_mgr):
    """Issue #897 — flipping to the other endpoint re-fires (latch is per-target).

    Force-close to 0 arms the latch at 0; a subsequent open to 100 (cover still
    not mechanically open) routes open_cover because the latched endpoint (0)
    differs from the new target (100).
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=3)
    _stub_state(mock_hass, current_position=2, state="open")
    state_obj = mock_hass.states.get.return_value

    with (
        patch.object(svc, "_get_current_position", return_value=2),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(svc, "_prepare_service_call", side_effect=_endpoint_service),
    ):
        await svc.apply_position(
            "cover.test",
            0,
            "custom_position",
            _ctx_full_endpoint(full_endpoint_target=True, tilt=None),
        )
        # Cover now reports 'closed'; commanding the opposite endpoint (100) must
        # not be suppressed by the latch armed at 0.
        state_obj.state = "closed"
        await svc.apply_position(
            "cover.test",
            100,
            "custom_position",
            _ctx_full_endpoint(full_endpoint_target=True, tilt=None),
        )

    services = _sent_services(mock_hass)
    assert services == ["close_cover", "open_cover"]


@pytest.mark.asyncio
async def test_non_venetian_open_endpoint_within_tolerance_still_skips(
    mock_hass, logger, grace_mgr
):
    """Issue #897 / #507 lock — target 100, current 98, state 'open': SKIPPED.

    On the OPEN endpoint the cover already reports state 'open' at any position
    > 0, so the mechanical-stop check suppresses forcing and the #507 tolerance
    band still applies — no open_cover relay click.
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=3)
    _stub_state(mock_hass, current_position=98, state="open")

    with (
        patch.object(svc, "_get_current_position", return_value=98),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(svc, "_prepare_service_call", side_effect=_endpoint_service),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test",
            100,
            "custom_position",
            _ctx_full_endpoint(full_endpoint_target=True, tilt=None),
        )

    assert outcome == "skipped"
    assert reason == "same_position"
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_non_venetian_close_endpoint_within_tolerance_fires(
    mock_hass, logger, grace_mgr
):
    """Issue #897 — target 0, current 1, state 'open': routes close_cover.

    Unlike the open endpoint, a cover only reports 'closed' at true 0, so a
    cover pinned at 1 with state 'open' is NOT at the mechanical stop and the
    within-tolerance close must route close_cover at least once (#697 promise).
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=3)
    _stub_state(mock_hass, current_position=1, state="open")

    with (
        patch.object(svc, "_get_current_position", return_value=1),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(svc, "_prepare_service_call", side_effect=_endpoint_service),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test",
            0,
            "custom_position",
            _ctx_full_endpoint(full_endpoint_target=True, tilt=None),
        )

    assert outcome == "sent"
    assert reason == "close_cover"
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_apply_position_exact_equality_still_skips(mock_hass, logger, grace_mgr):
    """The same-position SEND gate suppresses true no-ops (exact equality).

    Cover at exactly 100 commanded to 100 is a relay-click no-op and is
    skipped regardless of tolerance (issues #290/#567).
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=0)
    _stub_state(mock_hass, current_position=100)

    with patch.object(svc, "_get_current_position", return_value=100):
        outcome, reason = await svc.apply_position(
            "cover.test", 100, "default", _ctx_with_special()
        )

    assert outcome == "skipped"
    assert reason == "same_position"
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_apply_position_out_of_band_sends_command(mock_hass, logger, grace_mgr):
    """Cover at 90, target 100 (not equal): genuine move, command is sent.

    A cover not exactly at the target must be commanded (#127 preserved).  The
    reconciliation tolerance (8) plays no role in the SEND decision (#567).
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=8)
    _stub_state(mock_hass, current_position=90)

    with (
        patch.object(svc, "_get_current_position", return_value=90),
        patch.object(svc, "_check_position_delta", return_value=True),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("set_cover_position", {"entity_id": "cover.test"}, True),
        ),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test", 100, "default", _ctx_with_special()
        )

    assert outcome == "sent"
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_apply_position_zero_tolerance_sends_command(
    mock_hass, logger, grace_mgr
):
    """position_tolerance=0 (default): genuine moves proceed.

    Cover at 98 targeting 100 (not equal) is commanded regardless of the
    reconciliation tolerance setting (#567).
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=0)
    _stub_state(mock_hass, current_position=98)

    with (
        patch.object(svc, "_get_current_position", return_value=98),
        patch.object(svc, "_check_position_delta", return_value=True),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("set_cover_position", {"entity_id": "cover.test"}, True),
        ),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test", 100, "default", _ctx_with_special()
        )

    assert outcome == "sent"
    mock_hass.services.async_call.assert_called_once()


# --- same-position SEND gate uses exact equality (issue #567) ---


def _ctx_tracking(min_change: int = 1) -> PositionContext:
    """PositionContext for a normal solar tracking move.

    Passes every gate except possibly the real movement-delta gate.
    """
    return PositionContext(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=min_change,
        time_threshold=0,
        special_positions=[0, 100],
    )


@pytest.mark.asyncio
async def test_apply_position_tracking_move_within_tolerance_is_sent(
    mock_hass, logger, grace_mgr
):
    """Issue #567 — a 3% tracking move with delta_position=1 must be SENT.

    Cover at 24 commanded to 21 (gap=3) with position_tolerance=3 and the
    user's delta_position=1.  The same-position SEND gate must use exact
    equality, so this is NOT a no-op; the real movement-delta gate
    (abs(24-21)=3 >= 1) passes and the command is sent.  Before the fix the
    tolerance band (3 <= 3) swallowed it as same_position.
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=3)
    _stub_state(mock_hass, current_position=24)

    # NOTE: _check_position_delta is intentionally NOT patched — the real
    # movement-delta gate runs and must pass (abs(24-21)=3 >= min_change=1).
    with (
        patch.object(svc, "_get_current_position", return_value=24),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("set_cover_position", {"entity_id": "cover.test"}, True),
        ),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test", 21, "solar", _ctx_tracking(min_change=1)
        )

    assert outcome == "sent"
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_apply_position_one_percent_tracking_move_is_sent(
    mock_hass, logger, grace_mgr
):
    """Issue #567 — a 1% tracking move (gap=1) with delta_position=1 must be SENT.

    Cover at 28 commanded to 27 (gap=1), position_tolerance=3, min_change=1.
    The real movement-delta gate (abs(28-27)=1 >= 1) passes → command sent.
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=3)
    _stub_state(mock_hass, current_position=28)

    with (
        patch.object(svc, "_get_current_position", return_value=28),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("set_cover_position", {"entity_id": "cover.test"}, True),
        ),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test", 27, "solar", _ctx_tracking(min_change=1)
        )

    assert outcome == "sent"
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_endpoint_within_tolerance_skips_in_tracking_context(
    mock_hass, logger, grace_mgr
):
    """Issue #507/#567 combined — endpoint tolerance skips; mid-range stays exact.

    Cover at 98 commanded to 100 (endpoint) with tolerance=8 and min_change=10.
    The same-position gate uses _position_tolerance for endpoints (0/100):
    abs(98-100)=2 <= 8 fires and the command is skipped.
    In a tracking context a mid-range target (not 0 or 100) keeps exact equality
    so issue #567 (small tracking moves suppressed) stays fixed.
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=8)
    _stub_state(mock_hass, current_position=98)

    # _check_position_delta is NOT patched — the same-position gate fires first
    # so the delta gate is never reached.
    with (
        patch.object(svc, "_get_current_position", return_value=98),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("set_cover_position", {"entity_id": "cover.test"}, True),
        ),
    ):
        outcome, reason = await svc.apply_position(
            "cover.test", 100, "solar", _ctx_tracking(min_change=10)
        )

    assert outcome == "skipped"
    assert reason == "same_position"
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_midrange_special_position_within_tolerance_sends(
    mock_hass, logger, grace_mgr
):
    """Endpoint tolerance does NOT widen to mid-range special positions (#567 guard).

    Cover at 47 commanded to default_height=50 (a mid-range special position)
    with tolerance=8 and min_change=10.  The endpoint tolerance band applies
    only to targets 0 and 100; for all other targets the gate uses exact
    equality.  47 != 50, so the gate does not fire and the command is sent.
    This confirms the fix did not accidentally widen the band to all specials.
    """
    svc = _make_svc_with_tolerance(mock_hass, logger, grace_mgr, tolerance=8)
    _stub_state(mock_hass, current_position=47)

    ctx = PositionContext(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=10,
        time_threshold=0,
        # 50 is a mid-range special (default_height); 0 and 100 also present
        special_positions=[0, 50, 100],
    )

    with (
        patch.object(svc, "_get_current_position", return_value=47),
        patch.object(svc, "_check_time_delta", return_value=True),
        patch.object(
            svc,
            "_prepare_service_call",
            return_value=("set_cover_position", {"entity_id": "cover.test"}, True),
        ),
    ):
        outcome, reason = await svc.apply_position("cover.test", 50, "default", ctx)

    assert outcome == "sent"
    mock_hass.services.async_call.assert_called_once()


@pytest.mark.asyncio
async def test_reconcile_gives_up_on_unreachable_target(mock_hass, logger, grace_mgr):
    """Issue #507's relay-click suppression lives in the reconciliation path.

    Characterization test for the proper home of #507 suppression (so the
    command-emission gate no longer needs to carry it):

    1. A motor pinned at 97 when commanded to 100 (gap=3 ≤ tolerance=3) is
       treated as arrived → no resend, retry_count stays 0.
    2. An unreachable target outside tolerance (pinned at 96, gap=4 > 3) that
       has already exhausted max_retries gives up — no further _execute_command
       and gave_up flips True.
    """
    svc = CoverCommandService(
        hass=mock_hass,
        logger=logger,
        cover_type="cover_blind",
        grace_mgr=grace_mgr,
        open_close_threshold=50,
        check_interval_minutes=1,
        position_tolerance=3,
        max_retries=2,
    )
    # Reconciliation resend/give-up is exercised here; opt into matching
    # (default is off per issue #591).
    svc.enable_position_matching = True
    mock_hass.services.async_call = AsyncMock(return_value=None)

    caps = patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
        return_value={
            "has_set_position": True,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
        },
    )

    # (1) Reachable rest within tolerance (97, gap=3 ≤ 3) → arrived, no resend.
    svc.set_target("cover.reachable", 100)
    svc.set_waiting("cover.reachable", False)
    with patch.object(svc, "_get_current_position", return_value=97), caps:
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))
    mock_hass.services.async_call.assert_not_called()
    assert svc.state("cover.reachable").retry_count == 0

    # Clear scenario (1)'s target so it doesn't participate in scenario (2)'s
    # pass (where _get_current_position is patched globally to a different value).
    svc.set_target("cover.reachable", None)

    # (2) Unreachable (96, gap=4 > 3) already at max_retries → give up silently.
    svc.set_target("cover.unreachable", 100)
    svc.set_waiting("cover.unreachable", False)
    svc.state("cover.unreachable").retry_count = 2  # already at max_retries=2
    with patch.object(svc, "_get_current_position", return_value=96), caps:
        await svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    mock_hass.services.async_call.assert_not_called()
    assert svc.state("cover.unreachable").gave_up is True
    assert svc.state("cover.unreachable").retry_count == 2  # not incremented


# --- same-position gate: unknown current position fallback (issue #779) ---


@pytest.mark.asyncio
async def test_apply_position_same_position_gate_uses_last_target_when_current_unknown(
    cmd_svc, mock_hass
):
    """Issue #779: Somfy RTS covers sit at HA state unknown/unavailable forever,
    so _get_current_position always returns None and the same-position gate
    never fires — open_cover/close_cover gets resent every cycle even though
    the cover was already commanded to that state.

    First call (current unknown, no prior target): must proceed and send
    close_cover, recording routed_target=0 as the commanded target. Second
    call with the same continuously-computed position (30, which is not
    itself an endpoint but still routes to close_cover) must be recognised
    as a no-op via the routed-decision fallback and skipped as
    same_position.
    """
    mock_hass.states.get.return_value = MagicMock(state="unknown", attributes={})
    mock_hass.services.async_call = AsyncMock(return_value=None)
    caps = {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }

    with (
        patch.object(cmd_svc, "_get_current_position", return_value=None),
        patch.object(cmd_svc, "_check_time_delta", return_value=True),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
            return_value=caps,
        ),
    ):
        outcome, reason = await cmd_svc.apply_position(
            "cover.rts", 30, "solar", _ctx_with_special()
        )
        assert (outcome, reason) == ("sent", "close_cover")
        assert cmd_svc.get_target("cover.rts") == 0

        mock_hass.services.async_call.reset_mock()
        outcome2, reason2 = await cmd_svc.apply_position(
            "cover.rts", 30, "solar", _ctx_with_special()
        )

    assert (outcome2, reason2) == ("skipped", "same_position")
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_apply_position_same_position_gate_literal_endpoint_when_current_unknown(
    cmd_svc, mock_hass
):
    """Issue #779 boundary case: raw position is itself a literal endpoint (0).

    Confirms the amended fallback still suppresses the resend in the
    "parked at a literal 0/100 default" case that the reporter's original
    (unamended) proposal handled, not just the general mid-range case
    covered above.
    """
    mock_hass.states.get.return_value = MagicMock(state="unknown", attributes={})
    mock_hass.services.async_call = AsyncMock(return_value=None)
    caps = {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }

    with (
        patch.object(cmd_svc, "_get_current_position", return_value=None),
        patch.object(cmd_svc, "_check_time_delta", return_value=True),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
            return_value=caps,
        ),
    ):
        outcome, reason = await cmd_svc.apply_position(
            "cover.rts_endpoint", 0, "solar", _ctx_with_special()
        )
        assert (outcome, reason) == ("sent", "close_cover")
        assert cmd_svc.get_target("cover.rts_endpoint") == 0

        mock_hass.services.async_call.reset_mock()
        outcome2, reason2 = await cmd_svc.apply_position(
            "cover.rts_endpoint", 0, "solar", _ctx_with_special()
        )

    assert (outcome2, reason2) == ("skipped", "same_position")
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_apply_position_position_capable_current_unknown_uses_raw_target_fallback(
    cmd_svc, mock_hass
):
    """Position-capable axis: no regression from the issue #779 fallback.

    For a position-capable axis, routed_target == the raw commanded position
    (see route_service_call), so the fallback must compare the last
    commanded target against ``position`` directly (no routed-decision
    translation) — the same outcome as if ``_current`` had been read
    successfully.
    """
    mock_hass.states.get.return_value = MagicMock(
        state="unknown", attributes={"current_position": None}
    )
    mock_hass.services.async_call = AsyncMock(return_value=None)
    caps = {
        "has_set_position": True,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
    }
    cmd_svc.set_target("cover.rts_pos", 42)

    with (
        patch.object(cmd_svc, "_get_current_position", return_value=None),
        patch.object(cmd_svc, "_check_time_delta", return_value=True),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
            return_value=caps,
        ),
    ):
        outcome, reason = await cmd_svc.apply_position(
            "cover.rts_pos", 42, "solar", _ctx_with_special()
        )

    assert (outcome, reason) == ("skipped", "same_position")
    mock_hass.services.async_call.assert_not_called()


# --- same-position gate: My-preset fallback correctness (issue #779 regression
# from PR #781 — the fallback ignored use_my_position and hand-rolled the
# open/close threshold math, so a My move collapsed to the same routed
# decision as a stored full-close target) ---


@pytest.mark.asyncio
async def test_apply_position_my_preset_move_not_suppressed_by_stored_close_target_when_current_unknown(
    cmd_svc, mock_hass
):
    """A My-preset move to a sub-threshold value (e.g. 2%) must not be
    swallowed by a stored full-close target (0).

    ``_same_position_via_target_fallback`` used to hand-roll
    ``100 if position >= threshold else 0``, which ignores
    ``use_my_position`` entirely: a My move to 2% collapsed to the same
    routed decision (0) as a prior full close, so the gate suppressed the
    My move as ``same_position`` and the cover never received
    ``stop_cover``. The fallback must delegate to ``route_service_call``
    (the single routing source of truth) so a My target routes to its real
    decision (``stop_cover``, routed_target=2) instead of the hand-rolled
    open/close snap.
    """
    mock_hass.states.get.return_value = MagicMock(state="unknown", attributes={})
    mock_hass.services.async_call = AsyncMock(return_value=None)
    caps = {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
        "has_stop": True,
    }

    with (
        patch.object(cmd_svc, "_get_current_position", return_value=None),
        patch.object(cmd_svc, "_check_time_delta", return_value=True),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
            return_value=caps,
        ),
    ):
        outcome, reason = await cmd_svc.apply_position(
            "cover.rts_my", 0, "solar", _ctx_with_special()
        )
        assert (outcome, reason) == ("sent", "close_cover")
        assert cmd_svc.get_target("cover.rts_my") == 0

        mock_hass.services.async_call.reset_mock()
        outcome2, reason2 = await cmd_svc.apply_position(
            "cover.rts_my", 2, "solar", _ctx_with_special(use_my_position=True)
        )

    assert (outcome2, reason2) == ("sent", "stop_cover")


@pytest.mark.asyncio
async def test_apply_position_repeated_my_preset_suppressed_when_current_unknown(
    cmd_svc, mock_hass
):
    """A second identical My-preset move must be suppressed as
    ``same_position``, not resent — the residual #779 relay-click bug for
    the My path.

    ``_same_position_via_target_fallback`` stores the raw My value
    (``routed_target=state``) but compared it against the hand-rolled
    ``100 if position >= threshold else 0`` snap, which never matches a
    mid-range My value — so every cycle re-sent ``stop_cover`` even though
    the cover was already commanded to the same My preset.
    """
    mock_hass.states.get.return_value = MagicMock(state="unknown", attributes={})
    mock_hass.services.async_call = AsyncMock(return_value=None)
    caps = {
        "has_set_position": False,
        "has_set_tilt_position": False,
        "has_open": True,
        "has_close": True,
        "has_stop": True,
    }

    with (
        patch.object(cmd_svc, "_get_current_position", return_value=None),
        patch.object(cmd_svc, "_check_time_delta", return_value=True),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
            return_value=caps,
        ),
    ):
        outcome, reason = await cmd_svc.apply_position(
            "cover.rts_my_repeat", 2, "solar", _ctx_with_special(use_my_position=True)
        )
        assert (outcome, reason) == ("sent", "stop_cover")
        assert cmd_svc.get_target("cover.rts_my_repeat") == 2

        mock_hass.services.async_call.reset_mock()
        outcome2, reason2 = await cmd_svc.apply_position(
            "cover.rts_my_repeat", 2, "solar", _ctx_with_special(use_my_position=True)
        )

    assert (outcome2, reason2) == ("skipped", "same_position")
    mock_hass.services.async_call.assert_not_called()
