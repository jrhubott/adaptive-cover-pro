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
