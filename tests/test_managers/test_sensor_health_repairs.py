"""Tests for SensorHealthManager — Repair on sustained sensor unavailability (#786).

The manager is entity-agnostic: it watches an ``{issue_key -> entity_id}``
registry, debounces via ``TimeoutController`` so restarts / device re-adds don't
nag, and raises/clears an informational Repair via the issue registry.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.managers.sensor_health import (
    SensorHealthManager,
)

pytestmark = pytest.mark.unit

_MOD = "custom_components.adaptive_cover_pro.managers.sensor_health"
_ISSUE_KEY = "temp_sensor_unavailable_entry1"
_TRANSLATION_KEY = "temp_sensor_unavailable"


@pytest.fixture
def logger():
    return logging.getLogger("test.sensor_health")


def _make_hass(state):
    """Mock hass whose states.get returns ``state`` (a mock or None)."""
    hass = MagicMock()
    hass.states.get.return_value = state
    return hass


def _state(value):
    s = MagicMock()
    s.state = value
    return s


async def _drain():
    """Let the debounce task run (seconds=0)."""
    for _ in range(4):
        await asyncio.sleep(0)


class TestSensorHealthManager:
    """Raise-on-sustained-unavailable, debounce, clear-on-recovery."""

    async def test_repair_raised_on_sustained_unavailable(self, logger):
        """An entity that stays unavailable past debounce raises the Repair."""
        hass = _make_hass(_state("unavailable"))
        mgr = SensorHealthManager(
            hass, logger, domain="adaptive_cover_pro", debounce_seconds=0
        )
        mgr.update_watch(
            _ISSUE_KEY, "sensor.bedroom_temp", translation_key=_TRANSLATION_KEY
        )
        with patch(f"{_MOD}.ir.async_create_issue") as create:
            mgr.evaluate()
            await _drain()
        create.assert_called_once()
        # issue_id is the issue_key; informational (not fixable).
        _args, kwargs = create.call_args
        assert kwargs.get("is_fixable") is False
        assert kwargs.get("translation_key") == _TRANSLATION_KEY

    async def test_no_repair_on_transient_blip(self, logger):
        """Unavailable then recovers before debounce → no Repair (debounce gate)."""
        hass = _make_hass(_state("unavailable"))
        mgr = SensorHealthManager(
            hass, logger, domain="adaptive_cover_pro", debounce_seconds=100
        )
        mgr.update_watch(
            _ISSUE_KEY, "sensor.bedroom_temp", translation_key=_TRANSLATION_KEY
        )
        with (
            patch(f"{_MOD}.ir.async_create_issue") as create,
            patch(f"{_MOD}.ir.async_delete_issue"),
        ):
            mgr.evaluate()  # starts the (long) debounce timer
            hass.states.get.return_value = _state("21.0")  # recovered
            mgr.evaluate()  # cancels the pending timer
            await _drain()
        create.assert_not_called()

    async def test_repair_cleared_on_recovery(self, logger):
        """A raised Repair is deleted once the entity recovers."""
        hass = _make_hass(_state("unavailable"))
        mgr = SensorHealthManager(
            hass, logger, domain="adaptive_cover_pro", debounce_seconds=0
        )
        mgr.update_watch(
            _ISSUE_KEY, "sensor.bedroom_temp", translation_key=_TRANSLATION_KEY
        )
        with (
            patch(f"{_MOD}.ir.async_create_issue"),
            patch(f"{_MOD}.ir.async_delete_issue") as delete,
        ):
            mgr.evaluate()
            await _drain()
            hass.states.get.return_value = _state("21.0")  # recovered
            mgr.evaluate()
        delete.assert_called_once()

    async def test_missing_from_registry_triggers_repair(self, logger):
        """An entity with no state at all (missing) is treated as unhealthy."""
        hass = _make_hass(None)
        mgr = SensorHealthManager(
            hass, logger, domain="adaptive_cover_pro", debounce_seconds=0
        )
        mgr.update_watch(_ISSUE_KEY, "sensor.gone", translation_key=_TRANSLATION_KEY)
        with patch(f"{_MOD}.ir.async_create_issue") as create:
            mgr.evaluate()
            await _drain()
        create.assert_called_once()

    async def test_healthy_entity_never_raises(self, logger):
        """A healthy entity never schedules or raises anything."""
        hass = _make_hass(_state("21.0"))
        mgr = SensorHealthManager(
            hass, logger, domain="adaptive_cover_pro", debounce_seconds=0
        )
        mgr.update_watch(
            _ISSUE_KEY, "sensor.bedroom_temp", translation_key=_TRANSLATION_KEY
        )
        with patch(f"{_MOD}.ir.async_create_issue") as create:
            mgr.evaluate()
            await _drain()
        create.assert_not_called()

    async def test_unwatch_clears_issue_and_timer(self, logger):
        """Clearing the watch (entity_id None) deletes any active Repair."""
        hass = _make_hass(_state("unavailable"))
        mgr = SensorHealthManager(
            hass, logger, domain="adaptive_cover_pro", debounce_seconds=0
        )
        mgr.update_watch(
            _ISSUE_KEY, "sensor.bedroom_temp", translation_key=_TRANSLATION_KEY
        )
        with (
            patch(f"{_MOD}.ir.async_create_issue"),
            patch(f"{_MOD}.ir.async_delete_issue") as delete,
        ):
            mgr.evaluate()
            await _drain()
            mgr.update_watch(_ISSUE_KEY, None, translation_key=_TRANSLATION_KEY)
        delete.assert_called_once()
