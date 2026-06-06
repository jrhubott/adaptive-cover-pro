"""Tests for motion-based automatic control feature."""

import asyncio
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
    """Unavailable sensor → True (fail-open; don't penalize covers for sensor outages)."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_motion_mgr(
        sensors=["binary_sensor.motion_living_room"]
    )

    # Sensor missing from hass.states → is_entity_active returns True (fail-open)
    hass.states.get.return_value = None

    result = AdaptiveDataUpdateCoordinator.is_motion_detected.fget(coordinator)
    assert result is True


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
    coordinator._motion_mgr.set_no_motion()  # flip active flag via public path

    result = AdaptiveDataUpdateCoordinator.is_motion_timeout_active.fget(coordinator)
    assert result is True


@pytest.mark.asyncio
async def test_motion_timeout_handler_sets_active_flag():
    """Test that the on-expire body sets the active flag and runs the refresh."""
    hass = MagicMock()
    logger = MagicMock()
    mgr = MotionManager(hass=hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)

    # Patch is_motion_detected to return False (no motion after timeout)
    original_prop = MotionManager.is_motion_detected
    MotionManager.is_motion_detected = property(lambda self: False)
    try:
        refresh = AsyncMock()
        await mgr._on_motion_timeout_expired(0, refresh)

        assert mgr.is_motion_timeout_active is True
        refresh.assert_called_once()
    finally:
        MotionManager.is_motion_detected = original_prop


@pytest.mark.asyncio
async def test_motion_timeout_handler_cancels_if_motion_detected():
    """Test that the on-expire body short-circuits when motion returned."""
    hass = MagicMock()
    logger = MagicMock()
    mgr = MotionManager(hass=hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion_room"], timeout_seconds=300)

    # Patch is_motion_detected to return True
    original_prop = MotionManager.is_motion_detected
    MotionManager.is_motion_detected = property(lambda self: True)
    try:
        refresh = AsyncMock()
        await mgr._on_motion_timeout_expired(0, refresh)

        assert mgr.is_motion_timeout_active is False
        refresh.assert_not_called()
    finally:
        MotionManager.is_motion_detected = original_prop


@pytest.mark.asyncio
async def test_cancel_motion_timeout():
    """Test canceling a pending motion timeout via coordinator delegate."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_motion_mgr(
        sensors=["binary_sensor.motion_living_room"]
    )

    # Spin up a real (long) pending timer via the public API.
    coordinator._motion_mgr.start_motion_timeout(AsyncMock())
    assert coordinator._motion_mgr.has_pending_timeout is True

    AdaptiveDataUpdateCoordinator._cancel_motion_timeout(coordinator)

    assert coordinator._motion_mgr.has_pending_timeout is False


def test_cancel_motion_timeout_no_task():
    """Test canceling motion timeout when no task exists."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_motion_mgr()
    # No timer running.
    assert coordinator._motion_mgr.has_pending_timeout is False

    # Should not raise an error.
    AdaptiveDataUpdateCoordinator._cancel_motion_timeout(coordinator)
    assert coordinator._motion_mgr.has_pending_timeout is False


@pytest.mark.asyncio
async def test_cancel_motion_timeout_task_done():
    """A completed-but-not-cleared timer must still cancel cleanly via the delegate."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, _ = _make_coordinator_with_motion_mgr(
        sensors=["binary_sensor.motion_living_room"]
    )

    # Drive a real timer to completion so the controller sees a done task.
    original_prop = MotionManager.is_motion_detected
    MotionManager.is_motion_detected = property(lambda self: True)
    try:
        coordinator._motion_mgr.start_motion_timeout(AsyncMock())
        for _ in range(5):
            if not coordinator._motion_mgr.has_pending_timeout:
                break
            await asyncio.sleep(0)
    finally:
        MotionManager.is_motion_detected = original_prop

    # Cancel must be a no-op now that the timer has settled.
    AdaptiveDataUpdateCoordinator._cancel_motion_timeout(coordinator)
    assert coordinator._motion_mgr.has_pending_timeout is False


@pytest.mark.asyncio
async def test_async_check_motion_state_change_on():
    """Test motion state change handler for motion detected."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_motion_mgr(
        sensors=["binary_sensor.motion_living_room"]
    )
    coordinator._motion_mgr.set_no_motion()  # active flag on, no timer pending
    coordinator.state_change = False
    coordinator.async_refresh = AsyncMock()

    # The state machine reflects the new state before the listener fires.
    hass.states.get.return_value = MagicMock(state="on")

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
    assert coordinator._motion_mgr.is_motion_timeout_active is False
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

    coordinator, hass = _make_coordinator_with_motion_mgr(
        sensors=["binary_sensor.motion_living_room"]
    )
    # Timer is PENDING: started but has NOT expired yet (long timeout).
    coordinator._motion_mgr.start_motion_timeout(AsyncMock())
    assert coordinator._motion_mgr.has_pending_timeout is True
    assert coordinator._motion_mgr.is_motion_timeout_active is False

    coordinator.state_change = False
    coordinator.async_refresh = AsyncMock()

    # The state machine reflects the new state before the listener fires.
    hass.states.get.return_value = MagicMock(state="on")

    event = MagicMock()
    event.data = {
        "entity_id": "binary_sensor.motion_living_room",
        "new_state": MagicMock(state="on"),
    }

    await AdaptiveDataUpdateCoordinator.async_check_motion_state_change(
        coordinator, event
    )

    # Timer must have been cancelled.
    assert coordinator._motion_mgr.has_pending_timeout is False

    # Refresh must be triggered even though the timer never expired.
    assert coordinator.state_change is True
    coordinator.async_refresh.assert_called_once()

    # Active flag must remain False (timer never expired).
    assert coordinator._motion_mgr.is_motion_timeout_active is False


@pytest.mark.asyncio
async def test_async_check_motion_state_change_on_no_timeout_no_refresh():
    """Motion detected when no timeout is pending or active — no refresh needed.

    Motion-to-motion transitions (sensor stays on, flickers, etc.) should not
    cause an unnecessary coordinator refresh.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_motion_mgr(
        sensors=["binary_sensor.motion_living_room"]
    )
    # Neither timer pending nor fallback active — already in motion_detected state.
    assert coordinator._motion_mgr.has_pending_timeout is False
    assert coordinator._motion_mgr.is_motion_timeout_active is False
    coordinator._motion_mgr._last_motion_time = 1700000000.0  # prior motion recorded

    coordinator.state_change = False
    coordinator.async_refresh = AsyncMock()

    # The state machine reflects the new state before the listener fires.
    hass.states.get.return_value = MagicMock(state="on")

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
    mgr.set_no_motion()  # fallback state activated, no pending timer

    result = mgr.record_motion_detected()

    assert result is True
    assert mgr.is_motion_timeout_active is False


@pytest.mark.asyncio
async def test_record_motion_detected_returns_true_when_task_pending():
    """record_motion_detected returns True when a timer is in flight."""
    hass = MagicMock()
    logger = MagicMock()
    mgr = MotionManager(hass=hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion"], timeout_seconds=300)
    mgr.start_motion_timeout(AsyncMock())
    assert mgr.has_pending_timeout is True
    assert mgr.is_motion_timeout_active is False

    result = mgr.record_motion_detected()

    assert result is True
    assert mgr.is_motion_timeout_active is False
    assert mgr.has_pending_timeout is False


def test_record_motion_detected_returns_false_when_no_timeout():
    """record_motion_detected returns False when no timer or fallback is active."""
    hass = MagicMock()
    logger = MagicMock()
    mgr = MotionManager(hass=hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion"], timeout_seconds=300)
    # Fresh manager — neither flag nor pending timer.

    result = mgr.record_motion_detected()

    assert result is False
    assert mgr.last_motion_time is not None


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
    from custom_components.adaptive_cover_pro.const import ControlMethod
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
    from custom_components.adaptive_cover_pro.const import ControlMethod
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
    from custom_components.adaptive_cover_pro.const import ControlMethod
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
    from custom_components.adaptive_cover_pro.const import ControlMethod
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
    last_motion_time=None,
    timeout_active=False,
    timeout_pending=False,
    timeout_seconds=300,
):
    """Create a MotionManager configured for a specific state combination.

    Drives all observable state through the public API: ``set_no_motion``
    for the fallback-active flag, ``start_motion_timeout`` (long timeout)
    for the "timer in flight" case. A sentinel sensor is always
    registered so ``is_motion_timeout_active`` is not gated off by the
    "no sensors → feature disabled" guard. Internal field assignment is
    reserved for ``last_motion_time`` (no public setter; diagnostic).

    Note: ``timeout_pending=True`` requires an active asyncio loop —
    callers must be marked ``@pytest.mark.asyncio``.
    """
    hass = MagicMock()
    logger = MagicMock()
    mgr = MotionManager(hass=hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion"], timeout_seconds=timeout_seconds)
    mgr._last_motion_time = last_motion_time
    if timeout_active:
        mgr.set_no_motion()
    if timeout_pending:
        mgr.start_motion_timeout(AsyncMock())
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
    )
    type(coordinator).is_motion_detected = property(lambda self: True)

    sensor = _make_motion_status_sensor(coordinator)
    assert sensor.native_value == "motion_detected"


@pytest.mark.asyncio
async def test_motion_status_sensor_timeout_pending():
    """Sensor returns timeout_pending when countdown task is running."""
    coordinator = MagicMock()
    coordinator._motion_mgr = _make_motion_mgr(
        last_motion_time=1700000000.0,
        timeout_active=False,
        timeout_pending=True,
    )
    type(coordinator).is_motion_detected = property(lambda self: False)

    sensor = _make_motion_status_sensor(coordinator)
    assert sensor.native_value == "timeout_pending"
    coordinator._motion_mgr.cancel_motion_timeout()


def test_motion_status_sensor_no_motion():
    """Sensor returns no_motion when timeout has expired."""
    coordinator = MagicMock()
    coordinator._motion_mgr = _make_motion_mgr(
        last_motion_time=1700000000.0,
        timeout_active=True,
    )
    type(coordinator).is_motion_detected = property(lambda self: False)

    sensor = _make_motion_status_sensor(coordinator)
    assert sensor.native_value == "no_motion"


def test_motion_status_sensor_waiting_for_data_fallback():
    """Sensor returns waiting_for_data when neither active nor pending.

    Post-TimeoutController the "stale completed task" state can't occur
    (the handle auto-nulls), so the genuine fallback is "no flags set".
    """
    coordinator = MagicMock()
    coordinator._motion_mgr = _make_motion_mgr(
        last_motion_time=1700000000.0,
        timeout_active=False,
        timeout_pending=False,
    )
    type(coordinator).is_motion_detected = property(lambda self: False)

    sensor = _make_motion_status_sensor(coordinator)
    assert sensor.native_value == "waiting_for_data"


@pytest.mark.asyncio
async def test_motion_status_sensor_attributes_with_timeout():
    """Attributes include motion_timeout_end_time when timeout is pending."""
    coordinator = MagicMock()
    coordinator._motion_mgr = _make_motion_mgr(
        last_motion_time=1700000000.0,
        timeout_active=False,
        timeout_pending=True,
        timeout_seconds=300,
    )

    sensor = _make_motion_status_sensor(coordinator)
    attrs = sensor.extra_state_attributes

    assert attrs["motion_timeout_seconds"] == 300
    assert "motion_timeout_end_time" in attrs
    assert "last_motion_time" in attrs
    coordinator._motion_mgr.cancel_motion_timeout()


def test_motion_status_sensor_attributes_no_timeout():
    """Attributes do not include motion_timeout_end_time when motion is active."""
    coordinator = MagicMock()
    coordinator._motion_mgr = _make_motion_mgr(
        last_motion_time=1700000000.0,
        timeout_active=False,
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
    """set_no_motion() activates the fallback state without starting a timer."""
    hass = MagicMock()
    logger = MagicMock()
    mgr = MotionManager(hass=hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion"], timeout_seconds=300)

    mgr.set_no_motion()

    assert mgr.is_motion_timeout_active is True
    assert mgr.has_pending_timeout is False


@pytest.mark.asyncio
async def test_set_no_motion_cancels_pending_timeout():
    """set_no_motion() cancels any running timeout task."""
    hass = MagicMock()
    logger = MagicMock()
    mgr = MotionManager(hass=hass, logger=logger)
    mgr.update_config(sensors=["binary_sensor.motion"], timeout_seconds=300)
    mgr.start_motion_timeout(AsyncMock())
    assert mgr.has_pending_timeout is True

    mgr.set_no_motion()

    assert mgr.has_pending_timeout is False
    assert mgr.is_motion_timeout_active is True


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
    assert mgr.is_motion_timeout_active is False
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
    )
    type(coordinator).is_motion_detected = property(lambda self: True)

    sensor = _make_motion_status_sensor(coordinator)
    assert sensor.native_value == "motion_detected"


def test_motion_status_sensor_startup_no_motion():
    """Sensor shows no_motion at startup when sensors are configured but all off.

    set_no_motion() sets the fallback-active flag without last_motion_time.
    The sensor must check is_motion_timeout_active before last_motion_time
    so it shows no_motion rather than waiting_for_data.
    """
    coordinator = MagicMock()
    coordinator._motion_mgr = _make_motion_mgr(
        last_motion_time=None,
        timeout_active=True,
    )
    type(coordinator).is_motion_detected = property(lambda self: False)

    sensor = _make_motion_status_sensor(coordinator)
    assert sensor.native_value == "no_motion"


# --- Expanded domain tests for is_motion_detected ---


def _motion_result(hass_state_str, entity_id):
    """Create a MotionManager with one sensor, set state, return is_motion_detected."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_motion_mgr(sensors=[entity_id])
    state = MagicMock()
    state.state = hass_state_str
    hass.states.get.return_value = state
    return AdaptiveDataUpdateCoordinator.is_motion_detected.fget(coordinator)


def _motion_result_missing(entity_id):
    """Create a MotionManager with one sensor and a missing entity state."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_motion_mgr(sensors=[entity_id])
    hass.states.get.return_value = None
    return AdaptiveDataUpdateCoordinator.is_motion_detected.fget(coordinator)


def test_is_motion_detected_device_tracker_home():
    """device_tracker 'home' state → motion detected (True)."""
    assert _motion_result("home", "device_tracker.dog") is True


def test_is_motion_detected_device_tracker_away():
    """device_tracker 'away' state → no motion (False)."""
    assert _motion_result("away", "device_tracker.dog") is False


def test_is_motion_detected_person_home():
    """Person 'home' state → motion detected (True)."""
    assert _motion_result("home", "person.dad") is True


def test_is_motion_detected_person_away():
    """Person 'not_home' state → no motion (False)."""
    assert _motion_result("not_home", "person.dad") is False


def test_is_motion_detected_zone_occupied():
    """Zone with occupant count > 0 → motion detected (True)."""
    assert _motion_result("2", "zone.lounge") is True


def test_is_motion_detected_zone_empty():
    """Zone with occupant count 0 → no motion (False)."""
    assert _motion_result("0", "zone.lounge") is False


def test_is_motion_detected_switch_on():
    """Switch 'on' state → motion detected (True)."""
    assert _motion_result("on", "switch.guests") is True


def test_is_motion_detected_switch_off():
    """Switch 'off' state → no motion (False)."""
    assert _motion_result("off", "switch.guests") is False


def test_is_motion_detected_schedule_on():
    """Schedule 'on' state → motion detected (True)."""
    assert _motion_result("on", "schedule.evening") is True


def test_is_motion_detected_schedule_off():
    """Schedule 'off' state → no motion (False)."""
    assert _motion_result("off", "schedule.evening") is False


def test_is_motion_detected_sensor_unavailable_fail_open():
    """Unavailable sensor (state=None) → True (fail-open, matches presence semantics)."""
    assert _motion_result_missing("binary_sensor.motion_living_room") is True


@pytest.mark.parametrize(
    ("player_state", "expected"),
    [
        ("playing", True),
        ("paused", True),
        ("idle", True),
        ("buffering", True),
        ("standby", True),
        ("on", True),
        ("off", False),
        ("unavailable", False),
        ("unknown", False),
    ],
)
def test_is_motion_detected_media_player_states(player_state, expected):
    """media_player counts as occupancy unless off/unavailable/unknown (fail-closed)."""
    assert _motion_result(player_state, "media_player.living_room") is expected


def test_is_motion_detected_media_player_missing_fail_closed():
    """Missing media_player (state=None) → False (fail-closed, unlike binary sensors)."""
    assert _motion_result_missing("media_player.living_room") is False


@pytest.mark.asyncio
async def test_async_check_motion_state_change_media_player_playing():
    """A media player turning on (playing) is treated as occupancy detected."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_motion_mgr()
    coordinator._motion_mgr.update_config(
        sensors=[], timeout_seconds=300, media_players=["media_player.tv"]
    )
    coordinator._motion_mgr.set_no_motion()
    coordinator.state_change = False
    coordinator.async_refresh = AsyncMock()

    hass.states.get.return_value = MagicMock(state="playing")

    event = MagicMock()
    event.data = {
        "entity_id": "media_player.tv",
        "new_state": MagicMock(state="playing"),
    }

    await AdaptiveDataUpdateCoordinator.async_check_motion_state_change(
        coordinator, event
    )

    assert coordinator._motion_mgr.last_motion_time is not None
    coordinator.async_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_async_check_motion_state_change_media_player_off_starts_timeout():
    """A media player turning off (no other source) starts the no-motion timeout."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator, hass = _make_coordinator_with_motion_mgr()
    coordinator._motion_mgr.update_config(
        sensors=[], timeout_seconds=300, media_players=["media_player.tv"]
    )
    type(coordinator).is_motion_detected = property(lambda self: False)
    coordinator._start_motion_timeout = Mock()

    hass.states.get.return_value = MagicMock(state="off")

    event = MagicMock()
    event.data = {
        "entity_id": "media_player.tv",
        "new_state": MagicMock(state="off"),
    }

    await AdaptiveDataUpdateCoordinator.async_check_motion_state_change(
        coordinator, event
    )

    coordinator._start_motion_timeout.assert_called_once()
