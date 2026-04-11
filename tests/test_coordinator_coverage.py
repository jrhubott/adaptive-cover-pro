"""Unit tests for coordinator.py uncovered branches."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.managers.toggles import ToggleManager


def _make_coordinator():
    """Build a minimal AdaptiveDataUpdateCoordinator using object.__new__."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._toggles = ToggleManager()
    return coord


# ---------------------------------------------------------------------------
# Toggle property getters and setters
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_motion_control_toggle_getter_setter():
    """motion_control delegates reads/writes to ToggleManager."""
    coord = _make_coordinator()
    coord.motion_control = True
    assert coord.motion_control is True
    coord.motion_control = False
    assert coord.motion_control is False


@pytest.mark.unit
def test_temp_toggle_getter_setter():
    """temp_toggle delegates reads/writes to ToggleManager."""
    coord = _make_coordinator()
    coord.temp_toggle = True
    assert coord.temp_toggle is True


@pytest.mark.unit
def test_lux_toggle_getter_setter():
    """lux_toggle delegates reads/writes to ToggleManager."""
    coord = _make_coordinator()
    coord.lux_toggle = True
    assert coord.lux_toggle is True


@pytest.mark.unit
def test_irradiance_toggle_getter_setter():
    """irradiance_toggle delegates reads/writes to ToggleManager."""
    coord = _make_coordinator()
    coord.irradiance_toggle = True
    assert coord.irradiance_toggle is True


@pytest.mark.unit
def test_return_to_default_toggle_getter_setter():
    """return_to_default_toggle delegates reads/writes to ToggleManager."""
    coord = _make_coordinator()
    coord.return_to_default_toggle = True
    assert coord.return_to_default_toggle is True


@pytest.mark.unit
def test_automatic_control_getter_setter():
    """automatic_control delegates reads/writes to ToggleManager."""
    coord = _make_coordinator()
    coord.automatic_control = True
    assert coord.automatic_control is True


@pytest.mark.unit
def test_manual_toggle_getter_setter():
    """manual_toggle delegates reads/writes to ToggleManager."""
    coord = _make_coordinator()
    coord.manual_toggle = True
    assert coord.manual_toggle is True


# ---------------------------------------------------------------------------
# _check_sun_validity_transition
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sun_validity_transition_returns_false_when_no_cover_data():
    """_check_sun_validity_transition returns False when _cover_data is None."""
    coord = _make_coordinator()
    coord._cover_data = None
    coord._last_sun_validity_state = None

    result = coord._check_sun_validity_transition()
    assert result is False


@pytest.mark.unit
def test_sun_validity_transition_initializes_on_first_call():
    """First call initializes _last_sun_validity_state, returns False."""
    coord = _make_coordinator()
    cover_data = MagicMock()
    cover_data.direct_sun_valid = True
    coord._cover_data = cover_data
    coord._last_sun_validity_state = None

    result = coord._check_sun_validity_transition()

    assert result is False
    assert coord._last_sun_validity_state is True


@pytest.mark.unit
def test_sun_validity_transition_detects_sun_appeared():
    """Sun just appeared (False→True) returns True and logs info."""
    coord = _make_coordinator()
    cover_data = MagicMock()
    cover_data.direct_sun_valid = True
    coord._cover_data = cover_data
    coord._last_sun_validity_state = False  # was False, now True

    result = coord._check_sun_validity_transition()

    assert result is True
    coord.logger.info.assert_called()


@pytest.mark.unit
def test_sun_validity_transition_detects_sun_left():
    """Sun just left (True→False) returns False and logs debug."""
    coord = _make_coordinator()
    cover_data = MagicMock()
    cover_data.direct_sun_valid = False
    coord._cover_data = cover_data
    coord._last_sun_validity_state = True  # was True, now False

    result = coord._check_sun_validity_transition()

    assert result is False
    coord.logger.debug.assert_called()


# ---------------------------------------------------------------------------
# get_blind_data: unsupported cover type raises ValueError
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_blind_data_raises_for_unsupported_type():
    """get_blind_data raises ValueError for unknown cover types."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._toggles = ToggleManager()
    coord._cover_type = "unsupported_type"  # not blind, awning, or tilt
    coord._config_service = MagicMock()
    coord._sun_provider = MagicMock()
    coord._sun_provider.create_sun_data.return_value = MagicMock()
    coord.hass = MagicMock()
    coord.hass.config.time_zone = "UTC"
    coord._pos_sun = (180.0, 45.0)

    with (
        patch.object(
            type(coord),
            "pos_sun",
            new_callable=lambda: property(lambda self: (180.0, 45.0)),
        ),
        pytest.raises(ValueError, match="Unsupported cover type"),
    ):
        coord.get_blind_data(options={})


# ---------------------------------------------------------------------------
# _build_pipeline: custom position priority fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_pipeline_custom_position_priority_fallback():
    """_build_pipeline uses DEFAULT_CUSTOM_POSITION_PRIORITY when priority config is falsy."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
        DEFAULT_CUSTOM_POSITION_PRIORITY,
    )
    from custom_components.adaptive_cover_pro.const import (
        CONF_CUSTOM_POSITION_SENSOR_1,
        CONF_CUSTOM_POSITION_1,
        CONF_CUSTOM_POSITION_PRIORITY_1,
    )
    from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
        CustomPositionHandler,
    )

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._toggles = ToggleManager()

    options = {
        CONF_CUSTOM_POSITION_SENSOR_1: "binary_sensor.custom",
        CONF_CUSTOM_POSITION_1: 30,
        CONF_CUSTOM_POSITION_PRIORITY_1: 0,  # falsy — should fallback to default
    }
    config_entry = MagicMock()
    config_entry.options = options
    coord.config_entry = config_entry

    registry = coord._build_pipeline()

    custom_handlers = [
        h for h in registry._handlers if isinstance(h, CustomPositionHandler)
    ]
    assert len(custom_handlers) == 1
    assert custom_handlers[0].priority == DEFAULT_CUSTOM_POSITION_PRIORITY


# ---------------------------------------------------------------------------
# _read_custom_position_state: reads entity state
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_read_custom_position_sensor_states_reads_entity_state():
    """_read_custom_position_sensor_states correctly reads entity state from hass."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from custom_components.adaptive_cover_pro.const import (
        CONF_CUSTOM_POSITION_SENSOR_1,
        CONF_CUSTOM_POSITION_1,
        CONF_CUSTOM_POSITION_PRIORITY_1,
    )

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._toggles = ToggleManager()

    mock_state = MagicMock()
    mock_state.state = "on"
    mock_hass = MagicMock()
    mock_hass.states.get.return_value = mock_state
    coord.hass = mock_hass

    options = {
        CONF_CUSTOM_POSITION_SENSOR_1: "binary_sensor.custom_pos",
        CONF_CUSTOM_POSITION_1: 42,
        CONF_CUSTOM_POSITION_PRIORITY_1: 77,
    }

    result = coord._read_custom_position_sensor_states(options)

    assert len(result) == 1
    sensor, is_on, position, priority, min_mode, use_my = result[0]
    assert sensor == "binary_sensor.custom_pos"
    assert is_on is True
    assert position == 42
    assert priority == 77
    assert min_mode is False
    assert use_my is False


@pytest.mark.unit
def test_read_custom_position_sensor_states_with_priority_fallback():
    """_read_custom_position_sensor_states uses default priority when config is falsy."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
        DEFAULT_CUSTOM_POSITION_PRIORITY,
    )
    from custom_components.adaptive_cover_pro.const import (
        CONF_CUSTOM_POSITION_SENSOR_1,
        CONF_CUSTOM_POSITION_1,
        CONF_CUSTOM_POSITION_PRIORITY_1,
    )

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._toggles = ToggleManager()

    mock_state = MagicMock()
    mock_state.state = "off"
    mock_hass = MagicMock()
    mock_hass.states.get.return_value = mock_state
    coord.hass = mock_hass

    options = {
        CONF_CUSTOM_POSITION_SENSOR_1: "binary_sensor.custom",
        CONF_CUSTOM_POSITION_1: 0,  # position=0 is not None so this entry is included
        CONF_CUSTOM_POSITION_PRIORITY_1: None,  # None → use default
    }

    result = coord._read_custom_position_sensor_states(options)

    assert len(result) == 1
    _, _, _, priority, min_mode, use_my = result[0]
    assert priority == DEFAULT_CUSTOM_POSITION_PRIORITY
    assert min_mode is False
    assert use_my is False


# ---------------------------------------------------------------------------
# _start_motion_timeout inner callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_start_motion_timeout_callback_triggers_refresh():
    """_start_motion_timeout passes a callback that sets state_change=True and refreshes."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from custom_components.adaptive_cover_pro.managers.motion import MotionManager

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._toggles = ToggleManager()
    coord.state_change = False
    coord.async_refresh = AsyncMock()
    coord._motion_mgr = MagicMock(spec=MotionManager)

    # Capture the callback passed to start_motion_timeout
    captured_callback = None

    def _capture_callback(refresh_callback):
        nonlocal captured_callback
        captured_callback = refresh_callback

    coord._motion_mgr.start_motion_timeout.side_effect = _capture_callback

    coord._start_motion_timeout()

    assert captured_callback is not None
    await captured_callback()
    assert coord.state_change is True
    coord.async_refresh.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_start_weather_timeout_callback_triggers_refresh():
    """_start_weather_timeout passes a callback that sets state_change=True and refreshes."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from custom_components.adaptive_cover_pro.managers.weather import WeatherManager

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._toggles = ToggleManager()
    coord.state_change = False
    coord.async_refresh = AsyncMock()
    coord._weather_mgr = MagicMock(spec=WeatherManager)

    captured_callback = None

    def _capture_callback(refresh_callback):
        nonlocal captured_callback
        captured_callback = refresh_callback

    coord._weather_mgr.start_weather_timeout.side_effect = _capture_callback

    coord._start_weather_timeout()

    assert captured_callback is not None
    await captured_callback()
    assert coord.state_change is True
    coord.async_refresh.assert_called_once()


# ---------------------------------------------------------------------------
# _check_time_window_transition: auto_control gate at window close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_window_close_skips_reposition_when_auto_control_off():
    """_on_window_closed must not send positions when automatic control is OFF.

    Regression: end-of-time-window force-send bypassed the auto_control gate
    via force=True, moving covers even when the user had disabled automatic control.
    """
    import datetime as dt

    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._toggles = ToggleManager()
    coord.automatic_control = False
    coord._track_end_time = True

    cmd_svc = MagicMock()
    cmd_svc.apply_position = AsyncMock()
    cmd_svc.clear_non_safety_targets = MagicMock()
    coord._cmd_svc = cmd_svc

    time_mgr = MagicMock()

    async def _invoke(track_end_time, refresh_callback):
        await refresh_callback()

    time_mgr.check_transition = _invoke
    coord._time_mgr = time_mgr

    await coord._check_time_window_transition(dt.datetime.now(dt.UTC))

    # Stale-target cleanup must still run regardless of auto_control state
    cmd_svc.clear_non_safety_targets.assert_called_once()
    # Cover must NOT be repositioned
    cmd_svc.apply_position.assert_not_called()
    # Debug log must explain the skip
    coord.logger.debug.assert_called()
    logged = coord.logger.debug.call_args[0][0]
    assert "automatic control is OFF" in logged


@pytest.mark.asyncio
@pytest.mark.unit
async def test_window_close_sends_reposition_when_auto_control_on():
    """_on_window_closed sends the effective default when automatic control is ON.

    Happy-path companion to the regression test above.
    """
    import datetime as dt

    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from custom_components.adaptive_cover_pro.const import CONF_DEFAULT_HEIGHT

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._toggles = ToggleManager()
    coord.automatic_control = True
    coord._track_end_time = True
    coord._inverse_state = False
    coord.entities = [MagicMock()]

    cmd_svc = MagicMock()
    cmd_svc.apply_position = AsyncMock(return_value=("sent", ""))
    cmd_svc.clear_non_safety_targets = MagicMock()
    coord._cmd_svc = cmd_svc
    coord.async_refresh = AsyncMock()

    options = {CONF_DEFAULT_HEIGHT: 0}
    config_entry = MagicMock()
    config_entry.options = options
    coord.config_entry = config_entry

    cover_data = MagicMock()
    cover_data.sun_data = MagicMock()
    coord.get_blind_data = MagicMock(return_value=cover_data)
    coord._build_position_context = MagicMock(return_value=MagicMock(force=True))

    with patch(
        "custom_components.adaptive_cover_pro.coordinator.compute_effective_default",
        return_value=(0, False),
    ):
        time_mgr = MagicMock()

        async def _invoke_happy(track_end_time, refresh_callback):
            await refresh_callback()

        time_mgr.check_transition = _invoke_happy
        coord._time_mgr = time_mgr

        await coord._check_time_window_transition(dt.datetime.now(dt.UTC))

    cmd_svc.apply_position.assert_called_once()
    assert cmd_svc.apply_position.call_args[0][2] == "end_time_default"
