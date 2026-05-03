"""Tests for startup grace period functionality."""

import datetime as dt
from unittest.mock import MagicMock

import pytest


def test_is_in_startup_grace_period_returns_false_when_no_timestamp():
    """Test that _is_in_startup_grace_period returns False when no timestamp exists."""
    from custom_components.adaptive_cover_pro.const import STARTUP_GRACE_PERIOD_SECONDS
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    # Create minimal mock coordinator backed by a real GracePeriodManager
    coordinator = MagicMock()
    coordinator._grace_mgr = GracePeriodManager(
        logger=MagicMock(),
        startup_grace_seconds=STARTUP_GRACE_PERIOD_SECONDS,
    )

    # Import the method
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    # Call the method
    result = AdaptiveDataUpdateCoordinator._is_in_startup_grace_period(coordinator)

    assert result is False


def test_is_in_startup_grace_period_returns_true_when_within_period():
    """Test that _is_in_startup_grace_period returns True when within grace period."""
    from custom_components.adaptive_cover_pro.const import STARTUP_GRACE_PERIOD_SECONDS
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    # Create minimal mock coordinator backed by a real GracePeriodManager
    coordinator = MagicMock()
    coordinator._grace_mgr = GracePeriodManager(
        logger=MagicMock(),
        startup_grace_seconds=STARTUP_GRACE_PERIOD_SECONDS,
    )
    coordinator._grace_mgr._startup_timestamp = dt.datetime.now().timestamp()

    # Import the method
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    # Call the method
    result = AdaptiveDataUpdateCoordinator._is_in_startup_grace_period(coordinator)

    assert result is True


def test_is_in_startup_grace_period_returns_false_when_expired():
    """Test that _is_in_startup_grace_period returns False when grace period expired."""
    from custom_components.adaptive_cover_pro.const import STARTUP_GRACE_PERIOD_SECONDS
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    # Create minimal mock coordinator backed by a real GracePeriodManager
    coordinator = MagicMock()
    coordinator._grace_mgr = GracePeriodManager(
        logger=MagicMock(),
        startup_grace_seconds=STARTUP_GRACE_PERIOD_SECONDS,
    )
    # Set timestamp to 60 seconds ago (past the 30-second grace period)
    coordinator._grace_mgr._startup_timestamp = dt.datetime.now().timestamp() - 60

    # Import the method
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    # Call the method
    result = AdaptiveDataUpdateCoordinator._is_in_startup_grace_period(coordinator)

    assert result is False


@pytest.mark.asyncio
async def test_startup_grace_period_timeout_clears_tracking():
    """Test that startup grace period timeout clears tracking data."""
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    # Test the GracePeriodManager timeout directly (the coordinator delegates to it)
    mock_logger = MagicMock()
    mgr = GracePeriodManager(logger=mock_logger, startup_grace_seconds=0.1)
    mgr._startup_timestamp = dt.datetime.now().timestamp()
    mgr._startup_grace_period_task = None

    await mgr._startup_grace_period_timeout()

    # Verify tracking was cleared
    assert mgr._startup_timestamp is None
    assert mgr._startup_grace_period_task is None

    # Verify debug log was called for expiration
    mock_logger.debug.assert_called_once()
    assert "Startup grace period expired" in str(mock_logger.debug.call_args)


@pytest.mark.asyncio
async def test_start_startup_grace_period_sets_timestamp():
    """Test that _start_startup_grace_period sets the timestamp."""
    from unittest.mock import patch
    from custom_components.adaptive_cover_pro.const import STARTUP_GRACE_PERIOD_SECONDS
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    # Create minimal mock coordinator backed by a real GracePeriodManager
    mock_logger = MagicMock()
    coordinator = MagicMock()
    coordinator._grace_mgr = GracePeriodManager(
        logger=mock_logger,
        startup_grace_seconds=STARTUP_GRACE_PERIOD_SECONDS,
    )

    # Import the method
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    # Record time before call
    before = dt.datetime.now().timestamp()

    # Mock asyncio.create_task to avoid creating actual task
    def _close_coro(coro):
        coro.close()
        return MagicMock()

    with patch("asyncio.create_task", side_effect=_close_coro):
        # Call the method
        AdaptiveDataUpdateCoordinator._start_startup_grace_period(coordinator)

    # Record time after call
    after = dt.datetime.now().timestamp()

    # Verify timestamp was set within the expected range
    assert coordinator._grace_mgr._startup_timestamp is not None
    assert before <= coordinator._grace_mgr._startup_timestamp <= after

    # Verify info log was called for startup
    mock_logger.info.assert_called_once()
    assert "Started" in str(mock_logger.info.call_args)
    assert "startup grace period" in str(mock_logger.info.call_args)


@pytest.mark.asyncio
async def test_start_startup_grace_period_cancels_existing_task():
    """Test that _start_startup_grace_period cancels existing task."""
    from unittest.mock import patch
    from custom_components.adaptive_cover_pro.const import STARTUP_GRACE_PERIOD_SECONDS
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    # Create mock task
    mock_task = MagicMock()
    mock_task.done.return_value = False

    # Create minimal mock coordinator backed by a real GracePeriodManager
    coordinator = MagicMock()
    coordinator._grace_mgr = GracePeriodManager(
        logger=MagicMock(),
        startup_grace_seconds=STARTUP_GRACE_PERIOD_SECONDS,
    )
    coordinator._grace_mgr._startup_grace_period_task = mock_task

    # Import the method
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    # Mock asyncio.create_task to avoid creating actual task
    def _close_coro(coro):
        coro.close()
        return MagicMock()

    with patch("asyncio.create_task", side_effect=_close_coro):
        # Call the method
        AdaptiveDataUpdateCoordinator._start_startup_grace_period(coordinator)

    # Verify old task was cancelled
    mock_task.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_startup_grace_period_prevents_manual_override_detection():
    """Test that startup grace period prevents manual override detection."""
    from custom_components.adaptive_cover_pro.const import STARTUP_GRACE_PERIOD_SECONDS
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )

    # Create minimal mock coordinator backed by a real GracePeriodManager with active period
    coordinator = MagicMock()
    coordinator._grace_mgr = GracePeriodManager(
        logger=MagicMock(),
        startup_grace_seconds=STARTUP_GRACE_PERIOD_SECONDS,
    )
    coordinator._grace_mgr._startup_timestamp = dt.datetime.now().timestamp()  # Active
    coordinator.manual_toggle = True
    coordinator.automatic_control = True
    coordinator.cover_state_change = True
    coordinator.state_change_data = MagicMock()
    coordinator.state_change_data.entity_id = "cover.test"
    coordinator.logger = MagicMock()
    coordinator.manager = MagicMock()

    # Import the method
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    # Call async_handle_cover_state_change
    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 50)

    # Verify handle_state_change was NOT called (manual override prevented)
    coordinator.manager.handle_state_change.assert_not_called()

    # Verify debug log was called
    coordinator.logger.debug.assert_called()
    assert "startup grace period" in str(coordinator.logger.debug.call_args)

    # Verify cover_state_change was reset
    assert coordinator.cover_state_change is False


@pytest.mark.asyncio
async def test_startup_grace_period_allows_manual_override_after_expiration():
    """Test that manual override detection works after startup grace period expires."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    # Create minimal mock coordinator
    coordinator = MagicMock()
    # Set timestamp to 60 seconds ago (expired) — mock _is_in_startup_grace_period
    coordinator.manual_toggle = True
    coordinator.automatic_control = True
    coordinator.cover_state_change = True
    state_data = MagicMock()
    state_data.entity_id = "cover.test"
    coordinator.state_change_data = state_data
    coordinator._pending_cover_events = [state_data]
    coordinator._cover_type = "cover_blind"
    coordinator.manual_reset = False
    coordinator.manual_threshold = 3
    coordinator.logger = MagicMock()
    coordinator.manager = MagicMock()
    coordinator._target_just_reached = set()
    coordinator._cmd_svc = MagicMock()
    coordinator._cmd_svc.get_target = MagicMock(return_value=50)
    coordinator._cmd_svc.is_waiting_for_target = MagicMock(return_value=False)

    # Mock _is_in_startup_grace_period to return False (expired)
    coordinator._is_in_startup_grace_period = MagicMock(return_value=False)

    # Call async_handle_cover_state_change
    await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(coordinator, 50)

    # Verify _is_in_startup_grace_period was checked
    coordinator._is_in_startup_grace_period.assert_called_once()

    # Verify handle_state_change WAS called (manual override detection enabled)
    coordinator.manager.handle_state_change.assert_called_once()

    # Verify cover_state_change was reset
    assert coordinator.cover_state_change is False
