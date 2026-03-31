"""Tests for PositionVerificationManager."""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.managers.position_verification import (
    PositionVerificationManager,
)


@pytest.fixture
def logger():
    """Return a mock logger."""
    return MagicMock()


@pytest.fixture
def manager(logger):
    """Return a PositionVerificationManager with test settings."""
    return PositionVerificationManager(
        logger=logger,
        check_interval_minutes=1,
        position_tolerance=3,
        max_retries=3,
    )


def test_reset_retry_count(manager):
    """Removes entity from _retry_counts."""
    manager._retry_counts["cover.bedroom"] = 2
    manager.reset_retry_count("cover.bedroom")
    assert "cover.bedroom" not in manager._retry_counts


def test_reset_retry_count_noop_for_missing(manager):
    """No error when entity not tracked."""
    # Should not raise
    manager.reset_retry_count("cover.not_tracked")


def test_is_position_matched_within_tolerance(manager):
    """Returns True when abs(actual - target) <= tolerance."""
    # Exact match
    assert manager.is_position_matched(50, 50) is True
    # Within tolerance (tolerance=3)
    assert manager.is_position_matched(50, 53) is True
    assert manager.is_position_matched(53, 50) is True
    # At boundary
    assert manager.is_position_matched(50, 47) is True


def test_is_position_matched_outside_tolerance(manager):
    """Returns False when deviation exceeds tolerance."""
    # Outside tolerance (tolerance=3)
    assert manager.is_position_matched(50, 54) is False
    assert manager.is_position_matched(54, 50) is False
    assert manager.is_position_matched(50, 46) is False


def test_should_retry_under_max(manager):
    """Returns True and increments count when under max_retries."""
    entity_id = "cover.living_room"
    assert manager.get_retry_count(entity_id) == 0

    result = manager.should_retry(entity_id)

    assert result is True
    assert manager.get_retry_count(entity_id) == 1


def test_should_retry_increments_each_call(manager):
    """Each call to should_retry increments count until max."""
    entity_id = "cover.living_room"

    assert manager.should_retry(entity_id) is True  # count → 1
    assert manager.should_retry(entity_id) is True  # count → 2
    assert manager.should_retry(entity_id) is True  # count → 3 (max)
    assert manager.get_retry_count(entity_id) == 3


def test_should_retry_at_max(manager):
    """Returns False when count >= max_retries."""
    entity_id = "cover.living_room"
    manager._retry_counts[entity_id] = 3  # max_retries=3

    result = manager.should_retry(entity_id)

    assert result is False
    assert manager.get_retry_count(entity_id) == 3  # count unchanged


def test_should_retry_above_max(manager):
    """Returns False when count already exceeds max_retries."""
    entity_id = "cover.living_room"
    manager._retry_counts[entity_id] = 5

    result = manager.should_retry(entity_id)

    assert result is False


def test_never_commanded_tracking_first_time(manager):
    """mark_never_commanded returns True on first call for entity."""
    result = manager.mark_never_commanded("cover.bedroom")
    assert result is True


def test_never_commanded_tracking_second_time(manager):
    """mark_never_commanded returns False on subsequent calls for same entity."""
    manager.mark_never_commanded("cover.bedroom")
    result = manager.mark_never_commanded("cover.bedroom")
    assert result is False


def test_mark_commanded_clears_never_commanded(manager):
    """mark_commanded removes entity from never-commanded set."""
    manager.mark_never_commanded("cover.bedroom")
    assert "cover.bedroom" in manager._never_commanded

    manager.mark_commanded("cover.bedroom")
    assert "cover.bedroom" not in manager._never_commanded

    # After clearing, mark_never_commanded returns True again
    assert manager.mark_never_commanded("cover.bedroom") is True


def test_mark_commanded_noop_for_missing(manager):
    """mark_commanded doesn't raise for entity not in set."""
    # Should not raise
    manager.mark_commanded("cover.not_tracked")


def test_record_verification_stores_datetime(manager):
    """record_verification stores the provided datetime."""
    now = dt.datetime(2024, 6, 1, 12, 0, 0)
    manager.record_verification("cover.bedroom", now)
    assert manager._last_verification["cover.bedroom"] == now


def test_record_verification_multiple_entities(manager):
    """record_verification tracks each entity independently."""
    t1 = dt.datetime(2024, 6, 1, 12, 0, 0)
    t2 = dt.datetime(2024, 6, 1, 12, 5, 0)
    manager.record_verification("cover.bedroom", t1)
    manager.record_verification("cover.living_room", t2)
    assert manager._last_verification["cover.bedroom"] == t1
    assert manager._last_verification["cover.living_room"] == t2


def test_get_retry_count_default_zero(manager):
    """get_retry_count returns 0 for unknown entities."""
    assert manager.get_retry_count("cover.unknown") == 0


def test_reset_after_retries(manager):
    """reset_retry_count clears count set by should_retry calls."""
    entity_id = "cover.bedroom"
    manager.should_retry(entity_id)
    manager.should_retry(entity_id)
    assert manager.get_retry_count(entity_id) == 2

    manager.reset_retry_count(entity_id)
    assert manager.get_retry_count(entity_id) == 0


def test_initial_state(manager):
    """Manager starts with empty tracking dicts and no interval listener."""
    assert manager._retry_counts == {}
    assert manager._last_verification == {}
    assert manager._never_commanded == set()
    assert manager._position_check_interval is None
