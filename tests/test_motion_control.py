"""Tests for motion-based automatic control feature."""

from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from custom_components.adaptive_cover_pro.managers.motion import MotionManager


def _make_coordinator_with_motion_mgr(sensors=None, timeout_seconds=300):
    """Create a MagicMock coordinator with a real MotionManager pre-configured."""
    hass = MagicMock()
    logger = MagicMock()

    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.logger = logger

    mgr = MotionManager(hass=hass, logger=logger)
    mgr.update_config(
        sensors=sensors if sensors is not None else [],
        timeout_seconds=timeout_seconds,
    )
    coordinator._motion_mgr = mgr

    return coordinator, hass


def test_is_motion_detected_no_sensors_configured():
    """Test motion detection when no sensors configured (feature disabled)."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_motion_mgr(sensors=[])

    # Should return True (assume presence) when no sensors configured
    result = AdaptiveDataUpdateCoordinator.is_motion_detected.fget(coordinator)
    assert result is True


def test_is_motion_detected_single_sensor_on():
    """Test motion detected with single sensor on."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_motion_mgr(
        sensors=["binary_sensor.motion_living_room"]
    )

    # Mock sensor state as "on"
    state = MagicMock()
    state.state = "on"
    hass.states.get.return_value = state

    result = AdaptiveDataUpdateCoordinator.is_motion_detected.fget(coordinator)
    assert result is True


def test_is_motion_detected_single_sensor_off():
    """Test no motion detected with single sensor off."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_motion_mgr(
        sensors=["binary_sensor.motion_living_room"]
    )

    # Mock sensor state as "off"
    state = MagicMock()
    state.state = "off"
    hass.states.get.return_value = state

    result = AdaptiveDataUpdateCoordinator.is_motion_detected.fget(coordinator)
    assert result is False


def test_is_motion_detected_multiple_sensors_or_logic():
    """Test OR logic: ANY sensor on means motion detected."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    sensors = [
        "binary_sensor.motion_living_room",
        "binary_sensor.motion_kitchen",
        "binary_sensor.motion_bedroom",
    ]
    coordinator, hass = _make_coordinator_with_motion_mgr(sensors=sensors)

    # Mock: only kitchen sensor is on
    def get_state(entity_id):
        s = MagicMock()
        s.state = "on" if entity_id == "binary_sensor.motion_kitchen" else "off"
        return s

    hass.states.get.side_effect = get_state

    result = AdaptiveDataUpdateCoordinator.is_motion_detected.fget(coordinator)
    assert result is True


def test_is_motion_detected_all_sensors_off():
    """Test no motion when all sensors are off."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    sensors = [
        "binary_sensor.motion_living_room",
        "binary_sensor.motion_kitchen",
        "binary_sensor.motion_bedroom",
    ]
    coordinator, hass = _make_coordinator_with_motion_mgr(sensors=sensors)

    # Mock: all sensors off
    state = MagicMock()
    state.state = "off"
    hass.states.get.return_value = state

    result = AdaptiveDataUpdateCoordinator.is_motion_detected.fget(coordinator)
    assert result is False


def test_is_motion_detected_sensor_unavailable():
    """Test that unavailable sensors are treated as off."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_motion_mgr(
        sensors=["binary_sensor.motion_living_room"]
    )

    # Mock sensor unavailable (None state)
    hass.states.get.return_value = None

    result = AdaptiveDataUpdateCoordinator.is_motion_detected.fget(coordinator)
    assert result is False


def test_is_motion_timeout_active_no_sensors():
    """Test motion timeout inactive when no sensors configured."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_motion_mgr(sensors=[])

    result = AdaptiveDataUpdateCoordinator.is_motion_timeout_active.fget(coordinator)
    assert result is False


def test_is_motion_timeout_active_with_sensors():
    """Test motion timeout active flag when sensors configured."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_motion_mgr(
        sensors=["binary_sensor.motion_living_room"]
    )
    coordinator._motion_mgr._motion_timeout_active = True

    result = AdaptiveDataUpdateCoordinator.is_motion_timeout_active.fget(coordinator)
    assert result is True


@pytest.mark.asyncio
async def test_motion_timeout_handler_sets_active_flag():
    """Test that motion timeout handler sets the active flag."""
    hass = MagicMock()
    logger = MagicMock()
    mgr = MotionManager(hass=hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)

    # Patch is_motion_detected to return False (no motion after timeout)
    original_prop = MotionManager.is_motion_detected
    MotionManager.is_motion_detected = property(lambda self: False)
    try:
        refresh = AsyncMock()
        await mgr._motion_timeout_handler(0.01, refresh)

        assert mgr._motion_timeout_active is True
        refresh.assert_called_once()
    finally:
        MotionManager.is_motion_detected = original_prop


@pytest.mark.asyncio
async def test_motion_timeout_handler_cancels_if_motion_detected():
    """Test that motion timeout handler cancels if motion detected during timeout."""
    hass = MagicMock()
    logger = MagicMock()
    mgr = MotionManager(hass=hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)

    # Patch is_motion_detected to return True
    original_prop = MotionManager.is_motion_detected
    MotionManager.is_motion_detected = property(lambda self: True)
    try:
        refresh = AsyncMock()
        await mgr._motion_timeout_handler(0.01, refresh)

        assert mgr._motion_timeout_active is False
        refresh.assert_not_called()
    finally:
        MotionManager.is_motion_detected = original_prop


def test_cancel_motion_timeout():
    """Test canceling motion timeout task."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_motion_mgr()

    # Create a mock task on the manager
    task = MagicMock()
    task.done.return_value = False
    coordinator._motion_mgr._motion_timeout_task = task

    # Cancel timeout via coordinator delegate
    AdaptiveDataUpdateCoordinator._cancel_motion_timeout(coordinator)

    # Verify task was canceled and cleared
    task.cancel.assert_called_once()
    assert coordinator._motion_mgr._motion_timeout_task is None


def test_cancel_motion_timeout_no_task():
    """Test canceling motion timeout when no task exists."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_motion_mgr()
    coordinator._motion_mgr._motion_timeout_task = None

    # Should not raise an error
    AdaptiveDataUpdateCoordinator._cancel_motion_timeout(coordinator)
    assert coordinator._motion_mgr._motion_timeout_task is None


def test_cancel_motion_timeout_task_done():
    """Test canceling motion timeout when task is already done."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_motion_mgr()

    # Create a mock task that's done
    task = MagicMock()
    task.done.return_value = True
    coordinator._motion_mgr._motion_timeout_task = task

    # Cancel timeout
    AdaptiveDataUpdateCoordinator._cancel_motion_timeout(coordinator)

    # Task should not be canceled (already done), but should be cleared
    task.cancel.assert_not_called()
    assert coordinator._motion_mgr._motion_timeout_task is None


@pytest.mark.asyncio
async def test_async_check_motion_state_change_on():
    """Test motion state change handler for motion detected."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_motion_mgr(
        sensors=["binary_sensor.motion_living_room"]
    )
    coordinator._motion_mgr._motion_timeout_active = True
    coordinator.state_change = False
    coordinator.async_refresh = AsyncMock()

    # Create event with motion detected
    event = MagicMock()
    event.data = {
        "entity_id": "binary_sensor.motion_living_room",
        "new_state": MagicMock(state="on"),
    }

    # Call handler
    await AdaptiveDataUpdateCoordinator.async_check_motion_state_change(
        coordinator, event
    )

    # Verify last motion time was updated
    assert coordinator._motion_mgr.last_motion_time is not None

    # Verify motion timeout was deactivated and refresh was called
    assert coordinator._motion_mgr._motion_timeout_active is False
    assert coordinator.state_change is True
    coordinator.async_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_async_check_motion_state_change_on_during_timeout_pending():
    """Motion detected while timeout is pending (task running, not yet expired).

    This is the core regression test: when motion status is 'timeout_pending',
    a new motion event must cancel the timeout AND trigger an async_refresh so
    the cover resumes automatic positioning and the sensor updates immediately.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_motion_mgr(
        sensors=["binary_sensor.motion_living_room"]
    )
    # Timeout is PENDING: task is running but has NOT expired yet
    coordinator._motion_mgr._motion_timeout_active = False
    pending_task = MagicMock()
    pending_task.done.return_value = False
    coordinator._motion_mgr._motion_timeout_task = pending_task

    coordinator.state_change = False
    coordinator.async_refresh = AsyncMock()

    event = MagicMock()
    event.data = {
        "entity_id": "binary_sensor.motion_living_room",
        "new_state": MagicMock(state="on"),
    }

    await AdaptiveDataUpdateCoordinator.async_check_motion_state_change(
        coordinator, event
    )

    # Timeout task must be cancelled
    pending_task.cancel.assert_called_once()
    assert coordinator._motion_mgr._motion_timeout_task is None

    # Refresh must be triggered even though _motion_timeout_active was False
    assert coordinator.state_change is True
    coordinator.async_refresh.assert_called_once()

    # Active flag must remain False (timeout never expired)
    assert coordinator._motion_mgr._motion_timeout_active is False


@pytest.mark.asyncio
async def test_async_check_motion_state_change_on_no_timeout_no_refresh():
    """Motion detected when no timeout is pending or active — no refresh needed.

    Motion-to-motion transitions (sensor stays on, flickers, etc.) should not
    cause an unnecessary coordinator refresh.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_motion_mgr(
        sensors=["binary_sensor.motion_living_room"]
    )
    # Neither timeout active nor pending — already in motion_detected state
    coordinator._motion_mgr._motion_timeout_active = False
    coordinator._motion_mgr._motion_timeout_task = None
    coordinator._motion_mgr._last_motion_time = 1700000000.0  # prior motion recorded

    coordinator.state_change = False
    coordinator.async_refresh = AsyncMock()

    event = MagicMock()
    event.data = {
        "entity_id": "binary_sensor.motion_living_room",
        "new_state": MagicMock(state="on"),
    }

    await AdaptiveDataUpdateCoordinator.async_check_motion_state_change(
        coordinator, event
    )

    # No refresh — was already in motion_detected state
    assert coordinator.state_change is False
    coordinator.async_refresh.assert_not_called()


# --- MotionManager.record_motion_detected return value tests ---


def test_record_motion_detected_returns_true_when_timeout_active():
    """record_motion_detected returns True when timeout had fully expired."""
    hass = MagicMock()
    logger = MagicMock()
    mgr = MotionManager(hass=hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion"], timeout_seconds=300)
    mgr._motion_timeout_active = True
    mgr._motion_timeout_task = None

    result = mgr.record_motion_detected()

    assert result is True
    assert mgr._motion_timeout_active is False


def test_record_motion_detected_returns_true_when_task_pending():
    """record_motion_detected returns True when timeout task was still running."""
    hass = MagicMock()
    logger = MagicMock()
    mgr = MotionManager(hass=hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion"], timeout_seconds=300)
    mgr._motion_timeout_active = False
    task = MagicMock()
    task.done.return_value = False
    mgr._motion_timeout_task = task

    result = mgr.record_motion_detected()

    assert result is True
    assert mgr._motion_timeout_active is False
    assert mgr._motion_timeout_task is None
    task.cancel.assert_called_once()


def test_record_motion_detected_returns_false_when_no_timeout():
    """record_motion_detected returns False when no timeout was pending or active."""
    hass = MagicMock()
    logger = MagicMock()
    mgr = MotionManager(hass=hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion"], timeout_seconds=300)
    mgr._motion_timeout_active = False
    mgr._motion_timeout_task = None

    result = mgr.record_motion_detected()

    assert result is False
    assert mgr.last_motion_time is not None


def test_record_motion_detected_returns_false_when_task_done():
    """record_motion_detected returns False when timeout task had already completed."""
    hass = MagicMock()
    logger = MagicMock()
    mgr = MotionManager(hass=hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion"], timeout_seconds=300)
    mgr._motion_timeout_active = False
    task = MagicMock()
    task.done.return_value = True  # task completed but flag somehow not set
    mgr._motion_timeout_task = task

    result = mgr.record_motion_detected()

    # done() task is treated the same as no task — not pending
    assert result is False


@pytest.mark.asyncio
async def test_async_check_motion_state_change_off():
    """Test motion state change handler for motion stopped."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_motion_mgr(
        sensors=["binary_sensor.motion_living_room"]
    )

    # Mock is_motion_detected to return False (no other sensors active)
    type(coordinator).is_motion_detected = property(lambda self: False)

    # Mock _start_motion_timeout
    coordinator._start_motion_timeout = Mock()

    # Create event with motion stopped
    event = MagicMock()
    event.data = {
        "entity_id": "binary_sensor.motion_living_room",
        "new_state": MagicMock(state="off"),
    }

    # Call handler
    await AdaptiveDataUpdateCoordinator.async_check_motion_state_change(
        coordinator, event
    )

    # Verify timeout was started
    coordinator._start_motion_timeout.assert_called_once()


@pytest.mark.asyncio
async def test_async_check_motion_state_change_off_other_sensors_active():
    """Test motion stopped but other sensors still active."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_motion_mgr(
        sensors=["binary_sensor.motion_living_room", "binary_sensor.motion_kitchen"]
    )

    # Mock is_motion_detected to return True (other sensors still active)
    type(coordinator).is_motion_detected = property(lambda self: True)

    # Mock _start_motion_timeout
    coordinator._start_motion_timeout = Mock()

    # Create event with motion stopped
    event = MagicMock()
    event.data = {
        "entity_id": "binary_sensor.motion_living_room",
        "new_state": MagicMock(state="off"),
    }

    # Call handler
    await AdaptiveDataUpdateCoordinator.async_check_motion_state_change(
        coordinator, event
    )

    # Verify timeout was NOT started (other sensors still active)
    coordinator._start_motion_timeout.assert_not_called()


def test_determine_control_status_motion_timeout():
    """Test control status returns MOTION_TIMEOUT when active."""
    from custom_components.adaptive_cover_pro.const import ControlStatus
    from custom_components.adaptive_cover_pro.enums import ControlMethod
    from custom_components.adaptive_cover_pro.diagnostics.builder import (
        DiagnosticContext,
        DiagnosticsBuilder,
    )
    from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

    pipeline_result = PipelineResult(
        position=60,
        control_method=ControlMethod.MOTION,
        reason="motion timeout active",
    )

    ctx = DiagnosticContext(
        pos_sun=[180.0, 45.0],
        cover=None,
        pipeline_result=pipeline_result,
        climate_mode=False,
        check_adaptive_time=True,
        after_start_time=True,
        before_end_time=True,
        start_time=None,
        end_time=None,
        automatic_control=True,
    )

    result = DiagnosticsBuilder._determine_control_status(ctx)
    assert result == ControlStatus.MOTION_TIMEOUT


def test_determine_control_status_force_override_precedence():
    """Test force override takes precedence over motion timeout."""
    from custom_components.adaptive_cover_pro.const import ControlStatus
    from custom_components.adaptive_cover_pro.enums import ControlMethod
    from custom_components.adaptive_cover_pro.diagnostics.builder import (
        DiagnosticContext,
        DiagnosticsBuilder,
    )
    from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

    pipeline_result = PipelineResult(
        position=0,
        control_method=ControlMethod.FORCE,
        reason="force override active",
    )

    ctx = DiagnosticContext(
        pos_sun=[180.0, 45.0],
        cover=None,
        pipeline_result=pipeline_result,
        climate_mode=False,
        check_adaptive_time=True,
        after_start_time=True,
        before_end_time=True,
        start_time=None,
        end_time=None,
        automatic_control=True,
    )

    result = DiagnosticsBuilder._determine_control_status(ctx)
    assert result == ControlStatus.FORCE_OVERRIDE_ACTIVE


def test_state_property_motion_timeout_uses_pipeline_result():
    """Test state property uses pipeline result position during motion timeout.

    The pipeline MotionTimeoutHandler computes position with min/max limits applied.
    The state property must not bypass the pipeline result with raw default_state.
    """
    from custom_components.adaptive_cover_pro.enums import ControlMethod
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

    coordinator = MagicMock()
    coordinator.default_state = 60
    coordinator.logger = MagicMock()
    coordinator._use_interpolation = False
    coordinator._inverse_state = False
    coordinator._pipeline_bypasses_auto_control = False

    # Mock property access for direct checks in state property
    type(coordinator).is_force_override_active = property(lambda self: False)
    type(coordinator).is_motion_timeout_active = property(lambda self: True)

    # Pipeline result has limits applied — position differs from raw default_state
    coordinator._pipeline_result = PipelineResult(
        position=10,
        control_method=ControlMethod.MOTION,
        reason="motion timeout active — default position 10%",
    )

    result = AdaptiveDataUpdateCoordinator.state.fget(coordinator)
    # Must return the pipeline result (10), not the raw default_state (60)
    assert result == 10


def test_state_property_force_override_precedence():
    """Test state property prioritizes force override over motion timeout."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_FORCE_OVERRIDE_POSITION,
    )
    from custom_components.adaptive_cover_pro.enums import ControlMethod
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

    coordinator = MagicMock()
    coordinator.logger = MagicMock()

    def get_option(key, default=None):
        if key == CONF_FORCE_OVERRIDE_POSITION:
            return 0
        return default

    coordinator.config_entry.options.get.side_effect = get_option

    # Both active: force override takes precedence
    type(coordinator).is_force_override_active = property(lambda self: True)
    type(coordinator).is_motion_timeout_active = property(lambda self: True)

    # Pipeline result indicates force override with position 0
    coordinator._pipeline_result = PipelineResult(
        position=0,
        control_method=ControlMethod.FORCE,
        reason="force override active",
    )

    result = AdaptiveDataUpdateCoordinator.state.fget(coordinator)
    assert result == 0


def test_build_configuration_diagnostics_includes_motion_data():
    """Test diagnostic data includes motion control information."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_MOTION_SENSORS,
        CONF_MOTION_TIMEOUT,
    )
    from custom_components.adaptive_cover_pro.diagnostics.builder import (
        DiagnosticContext,
        DiagnosticsBuilder,
    )

    ctx = DiagnosticContext(
        pos_sun=[180.0, 45.0],
        cover=None,
        pipeline_result=None,
        climate_mode=False,
        check_adaptive_time=True,
        after_start_time=True,
        before_end_time=True,
        start_time=None,
        end_time=None,
        automatic_control=True,
        motion_detected=True,
        motion_timeout_active=False,
        config_options={
            CONF_MOTION_SENSORS: ["binary_sensor.motion_living_room"],
            CONF_MOTION_TIMEOUT: 300,
        },
    )

    result = DiagnosticsBuilder._build_configuration(ctx)

    config = result["configuration"]
    assert "motion_sensors" in config
    assert config["motion_sensors"] == ["binary_sensor.motion_living_room"]
    assert "motion_timeout" in config
    assert config["motion_timeout"] == 300
    assert "motion_detected" in config
    assert config["motion_detected"] is True
    assert "motion_timeout_active" in config
    assert config["motion_timeout_active"] is False


@pytest.mark.asyncio
async def test_async_shutdown_cancels_motion_timeout():
    """Test shutdown cancels motion timeout task."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_motion_mgr()
    coordinator._grace_period_tasks = {}

    # Mock _cancel_motion_timeout to verify it's called
    coordinator._cancel_motion_timeout = Mock()

    # Mock _cmd_svc.stop (replaces _stop_position_verification)
    coordinator._cmd_svc = MagicMock()
    coordinator._cmd_svc.stop = Mock()

    # Call shutdown
    await AdaptiveDataUpdateCoordinator.async_shutdown(coordinator)

    # Verify motion timeout was canceled
    coordinator._cancel_motion_timeout.assert_called_once()


# --- AdaptiveCoverMotionStatusSensor tests ---


def _make_motion_mgr(
    last_motion_time=None, timeout_active=False, timeout_task=None, timeout_seconds=300
):
    """Create a MotionManager with pre-set internal state."""
    hass = MagicMock()
    logger = MagicMock()
    mgr = MotionManager(hass=hass, logger=logger)
    mgr.update_config(sensors=[], timeout_seconds=timeout_seconds)
    mgr._last_motion_time = last_motion_time
    mgr._motion_timeout_active = timeout_active
    mgr._motion_timeout_task = timeout_task
    return mgr


def _make_motion_status_sensor(coordinator, motion_sensors=None):
    """Create a motion status sensor with a mocked coordinator.

    Args:
        coordinator: Mocked coordinator instance.
        motion_sensors: List of sensor entity IDs. Defaults to a single
            sensor so existing tests exercise the configured path.

    """
    from custom_components.adaptive_cover_pro.sensor import (
        AdaptiveCoverMotionStatusSensor,
    )

    if motion_sensors is None:
        motion_sensors = ["binary_sensor.motion"]

    config_entry = MagicMock()
    config_entry.options.get.return_value = motion_sensors

    sensor = AdaptiveCoverMotionStatusSensor.__new__(AdaptiveCoverMotionStatusSensor)
    sensor.coordinator = coordinator
    sensor.config_entry = config_entry
    return sensor


def test_motion_status_sensor_not_configured():
    """Sensor returns not_configured when no motion sensors are set up."""
    coordinator = MagicMock()
    sensor = _make_motion_status_sensor(coordinator, motion_sensors=[])
    assert sensor.native_value == "not_configured"
    assert sensor.extra_state_attributes is None


def test_motion_status_sensor_waiting_for_data_no_history():
    """Sensor returns waiting_for_data when no motion has ever been detected."""
    coordinator = MagicMock()
    coordinator._motion_mgr = _make_motion_mgr(last_motion_time=None)

    sensor = _make_motion_status_sensor(coordinator)
    assert sensor.native_value == "waiting_for_data"


def test_motion_status_sensor_motion_detected():
    """Sensor returns motion_detected when occupancy is active."""
    coordinator = MagicMock()
    coordinator._motion_mgr = _make_motion_mgr(
        last_motion_time=1700000000.0,
        timeout_active=False,
        timeout_task=None,
    )
    type(coordinator).is_motion_detected = property(lambda self: True)

    sensor = _make_motion_status_sensor(coordinator)
    assert sensor.native_value == "motion_detected"


def test_motion_status_sensor_timeout_pending():
    """Sensor returns timeout_pending when countdown task is running."""
    task = MagicMock()
    task.done.return_value = False

    coordinator = MagicMock()
    coordinator._motion_mgr = _make_motion_mgr(
        last_motion_time=1700000000.0,
        timeout_active=False,
        timeout_task=task,
    )
    type(coordinator).is_motion_detected = property(lambda self: False)

    sensor = _make_motion_status_sensor(coordinator)
    assert sensor.native_value == "timeout_pending"


def test_motion_status_sensor_no_motion():
    """Sensor returns no_motion when timeout has expired."""
    coordinator = MagicMock()
    coordinator._motion_mgr = _make_motion_mgr(
        last_motion_time=1700000000.0,
        timeout_active=True,
        timeout_task=None,
    )
    type(coordinator).is_motion_detected = property(lambda self: False)

    sensor = _make_motion_status_sensor(coordinator)
    assert sensor.native_value == "no_motion"


def test_motion_status_sensor_waiting_for_data_fallback():
    """Sensor returns waiting_for_data when no state matches (e.g. after task completes but flag not set)."""
    task = MagicMock()
    task.done.return_value = True

    coordinator = MagicMock()
    coordinator._motion_mgr = _make_motion_mgr(
        last_motion_time=1700000000.0,
        timeout_active=False,
        timeout_task=task,
    )
    type(coordinator).is_motion_detected = property(lambda self: False)

    sensor = _make_motion_status_sensor(coordinator)
    assert sensor.native_value == "waiting_for_data"


def test_motion_status_sensor_attributes_with_timeout():
    """Attributes include motion_timeout_end_time when timeout is pending."""
    task = MagicMock()
    task.done.return_value = False

    coordinator = MagicMock()
    coordinator._motion_mgr = _make_motion_mgr(
        last_motion_time=1700000000.0,
        timeout_active=False,
        timeout_task=task,
        timeout_seconds=300,
    )

    sensor = _make_motion_status_sensor(coordinator)
    attrs = sensor.extra_state_attributes

    assert attrs["motion_timeout_seconds"] == 300
    assert "motion_timeout_end_time" in attrs
    assert "last_motion_time" in attrs


def test_motion_status_sensor_attributes_no_timeout():
    """Attributes do not include motion_timeout_end_time when motion is active."""
    coordinator = MagicMock()
    coordinator._motion_mgr = _make_motion_mgr(
        last_motion_time=1700000000.0,
        timeout_active=False,
        timeout_task=None,
        timeout_seconds=300,
    )

    sensor = _make_motion_status_sensor(coordinator)
    attrs = sensor.extra_state_attributes

    assert attrs["motion_timeout_seconds"] == 300
    assert "motion_timeout_end_time" not in attrs
    assert "last_motion_time" in attrs


def test_motion_status_sensor_attributes_no_data():
    """Attributes contain only motion_timeout_seconds when no motion data exists."""
    coordinator = MagicMock()
    coordinator._motion_mgr = _make_motion_mgr(
        last_motion_time=None,
        timeout_seconds=300,
    )

    sensor = _make_motion_status_sensor(coordinator)
    attrs = sensor.extra_state_attributes

    assert attrs == {"motion_timeout_seconds": 300}
    assert "motion_timeout_end_time" not in attrs
    assert "last_motion_time" not in attrs


def test_motion_status_sensor_no_timestamp_device_class():
    """Sensor does not use TIMESTAMP device class (regression for issue #75)."""
    from homeassistant.components.sensor import SensorDeviceClass

    from custom_components.adaptive_cover_pro.sensor import (
        AdaptiveCoverMotionStatusSensor,
    )

    assert (
        getattr(AdaptiveCoverMotionStatusSensor, "_attr_device_class", None)
        != SensorDeviceClass.TIMESTAMP
    )


# --- Startup initialization tests ---


def test_set_no_motion_activates_immediately():
    """set_no_motion() sets _motion_timeout_active without starting a task."""
    hass = MagicMock()
    logger = MagicMock()
    mgr = MotionManager(hass=hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion"], timeout_seconds=300)

    mgr.set_no_motion()

    assert mgr._motion_timeout_active is True
    assert mgr._motion_timeout_task is None


def test_set_no_motion_cancels_pending_timeout():
    """set_no_motion() cancels any running timeout task."""
    hass = MagicMock()
    logger = MagicMock()
    mgr = MotionManager(hass=hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion"], timeout_seconds=300)

    task = MagicMock()
    task.done.return_value = False
    mgr._motion_timeout_task = task

    mgr.set_no_motion()

    task.cancel.assert_called_once()
    assert mgr._motion_timeout_active is True


def test_check_initial_motion_state_all_off_sets_no_motion():
    """_check_initial_motion_state sets no_motion when all sensors are off at startup."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator.config_entry.options.get.return_value = ["binary_sensor.motion"]
    type(coordinator).is_motion_detected = property(lambda self: False)

    AdaptiveDataUpdateCoordinator._check_initial_motion_state(coordinator)

    coordinator._motion_mgr.set_no_motion.assert_called_once()


def test_check_initial_motion_state_sensor_on_records_motion():
    """_check_initial_motion_state calls record_motion_detected when motion is active at startup.

    Before this fix the method did nothing, leaving last_motion_time=None so the
    Motion Status sensor showed ``waiting_for_data`` after a reload.  The fix calls
    record_motion_detected() which populates last_motion_time and keeps the sensor
    showing ``motion_detected`` immediately.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator.config_entry.options.get.return_value = ["binary_sensor.motion"]
    type(coordinator).is_motion_detected = property(lambda self: True)

    AdaptiveDataUpdateCoordinator._check_initial_motion_state(coordinator)

    coordinator._motion_mgr.record_motion_detected.assert_called_once()
    coordinator._motion_mgr.set_no_motion.assert_not_called()


def test_check_initial_motion_state_no_sensors_noop():
    """_check_initial_motion_state does nothing when no motion sensors are configured."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator.config_entry.options.get.return_value = []

    AdaptiveDataUpdateCoordinator._check_initial_motion_state(coordinator)

    coordinator._motion_mgr.set_no_motion.assert_not_called()


def test_check_initial_motion_state_sensor_on_sets_last_motion_time():
    """record_motion_detected populates last_motion_time so the sensor shows motion_detected.

    Integration test using the real MotionManager to confirm the state is fully
    initialized (not just that the mock method was invoked).
    """
    from custom_components.adaptive_cover_pro.managers.motion import MotionManager

    hass = MagicMock()
    mgr = MotionManager(hass=hass, logger=MagicMock())
    mgr.update_config(sensors=["binary_sensor.motion"], timeout_seconds=300)

    # Simulate: sensor is currently on
    hass.states.get.return_value = MagicMock(state="on")

    # This is what _check_initial_motion_state now calls
    mgr.record_motion_detected()

    assert mgr.last_motion_time is not None
    assert mgr._motion_timeout_active is False
    assert mgr.is_motion_detected is True  # still reads live sensor


def test_motion_status_sensor_shows_motion_detected_after_reload():
    """Sensor shows motion_detected immediately after reload when a sensor is on.

    After the fix, _check_initial_motion_state calls record_motion_detected(),
    which sets last_motion_time.  The sensor logic uses last_motion_time to
    determine the state so it must show motion_detected, not waiting_for_data.
    """
    coordinator = MagicMock()
    coordinator._motion_mgr = _make_motion_mgr(
        last_motion_time=1000.0,
        timeout_active=False,
        timeout_task=None,
    )
    type(coordinator).is_motion_detected = property(lambda self: True)

    sensor = _make_motion_status_sensor(coordinator)
    assert sensor.native_value == "motion_detected"


def test_motion_status_sensor_startup_no_motion():
    """Sensor shows no_motion at startup when sensors are configured but all off.

    set_no_motion() sets _motion_timeout_active=True without last_motion_time.
    The sensor must check _motion_timeout_active before last_motion_time so it
    shows no_motion rather than waiting_for_data.
    """
    coordinator = MagicMock()
    coordinator._motion_mgr = _make_motion_mgr(
        last_motion_time=None,
        timeout_active=True,
        timeout_task=None,
    )
    type(coordinator).is_motion_detected = property(lambda self: False)

    sensor = _make_motion_status_sensor(coordinator)
    assert sensor.native_value == "no_motion"
