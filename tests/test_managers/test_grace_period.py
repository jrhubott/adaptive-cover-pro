"""Tests for GracePeriodManager."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.managers.grace_period import (
    GracePeriodManager,
)


@pytest.fixture
def logger():
    """Return a mock logger."""
    return MagicMock()


@pytest.fixture
def manager(logger):
    """Return a GracePeriodManager with test settings."""
    return GracePeriodManager(
        logger=logger,
        command_grace_seconds=5.0,
        startup_grace_seconds=30.0,
    )


def test_is_in_grace_period_no_timestamp(manager):
    """Returns False when no timestamp exists for entity."""
    assert manager.is_in_command_grace_period("cover.bedroom") is False


def test_is_in_grace_period_within_period(manager):
    """Returns True when within grace period."""
    manager._command_timestamps["cover.bedroom"] = time.time()
    assert manager.is_in_command_grace_period("cover.bedroom") is True


def test_is_in_grace_period_expired(manager):
    """Returns False when grace period has expired (timestamp 10s ago, grace is 5s)."""
    manager._command_timestamps["cover.bedroom"] = time.time() - 10
    assert manager.is_in_command_grace_period("cover.bedroom") is False


def test_is_in_startup_grace_period_no_timestamp(manager):
    """Returns False when no startup timestamp exists."""
    assert manager.is_in_startup_grace_period() is False


def test_is_in_startup_grace_period_within_period(manager):
    """Returns True when within startup grace period."""
    manager._startup_timestamp = time.time()
    assert manager.is_in_startup_grace_period() is True


def test_is_in_startup_grace_period_expired(manager):
    """Returns False when startup grace period has expired."""
    manager._startup_timestamp = time.time() - 60
    assert manager.is_in_startup_grace_period() is False


def test_cancel_grace_period_clears_state(manager):
    """Cancels task, clears timestamp and task from dicts."""
    mock_task = MagicMock()
    mock_task.done.return_value = False

    entity_id = "cover.living_room"
    manager._command_timestamps[entity_id] = time.time()
    manager._grace_period_tasks[entity_id] = mock_task

    manager.cancel_command_grace_period(entity_id)

    mock_task.cancel.assert_called_once()
    assert entity_id not in manager._command_timestamps
    assert entity_id not in manager._grace_period_tasks


def test_cancel_all_clears_all_command_grace_periods(manager):
    """Cancels all tasks for multiple entities."""
    task1 = MagicMock()
    task1.done.return_value = False
    task2 = MagicMock()
    task2.done.return_value = False

    manager._command_timestamps["cover.room1"] = time.time()
    manager._grace_period_tasks["cover.room1"] = task1
    manager._command_timestamps["cover.room2"] = time.time()
    manager._grace_period_tasks["cover.room2"] = task2

    manager.cancel_all()

    task1.cancel.assert_called_once()
    task2.cancel.assert_called_once()
    assert len(manager._command_timestamps) == 0
    assert len(manager._grace_period_tasks) == 0
