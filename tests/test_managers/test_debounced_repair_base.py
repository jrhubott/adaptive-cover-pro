"""Tests for _DebouncedRepairBase — the shared debounce/raise/clear lifecycle.

The base owns the machinery both ``SensorHealthManager`` (entity-availability
watches) and ``RepairManager`` (config predicates) reuse: a per-key debounce
timer, a re-check at expiry via an injected ``still_unhealthy`` callable, and
raise/clear of an informational Home Assistant Repair. Extracted so the two
managers share one lifecycle rather than copy it (no-duplication rule).
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.managers.common.debounced_repair import (
    _DebouncedRepairBase,
)

pytestmark = pytest.mark.unit

_MOD = "custom_components.adaptive_cover_pro.managers.common.debounced_repair"


@pytest.fixture
def logger():
    return logging.getLogger("test.debounced_repair")


async def _drain():
    """Let the debounce task run (seconds=0)."""
    for _ in range(4):
        await asyncio.sleep(0)


def _bg_hass():
    """Mock hass whose async_create_background_task creates a real task.

    The debounce timer is now spawned via HA's tracked-task helper (issue #975
    lingering-task fix), so the double must return a real asyncio task for
    ``_drain()`` to fire the debounce.
    """
    hass = MagicMock()
    hass.async_create_background_task = (
        lambda coro, name=None, eager_start=True: asyncio.create_task(coro)
    )
    return hass


class _Probe(_DebouncedRepairBase):
    """Trivial concrete subclass — the base carries all behavior under test."""


class TestDebouncedRepairBase:
    """Debounce-once, still-unhealthy re-check at expiry, shutdown cancel."""

    async def test_schedule_debounces_once_and_raises(self, logger):
        """A single debounce raises exactly one informational Repair."""
        hass = _bg_hass()
        probe = _Probe(hass, logger, domain="adaptive_cover_pro", debounce_seconds=0)
        with patch(f"{_MOD}.ir.async_create_issue") as create:
            probe._schedule("k1", "tk", {"name": "x"}, still_unhealthy=lambda: True)
            # Second schedule while the timer is in flight is a no-op (debounce).
            probe._schedule("k1", "tk", {"name": "x"}, still_unhealthy=lambda: True)
            await _drain()
        create.assert_called_once()
        _args, kwargs = create.call_args
        assert kwargs.get("is_fixable") is False
        assert kwargs.get("translation_key") == "tk"

    async def test_cancel_before_expiry_suppresses_raise(self, logger):
        """If ``still_unhealthy`` flips False before expiry, no Repair is raised."""
        hass = _bg_hass()
        probe = _Probe(hass, logger, domain="adaptive_cover_pro", debounce_seconds=0)
        unhealthy = True
        with patch(f"{_MOD}.ir.async_create_issue") as create:
            probe._schedule("k1", "tk", {}, still_unhealthy=lambda: unhealthy)
            unhealthy = False  # recovered before the expiry re-check
            await _drain()
        create.assert_not_called()

    async def test_shutdown_cancels_inflight_timers(self, logger):
        """``shutdown`` cancels a pending debounce so it never raises."""
        hass = _bg_hass()
        probe = _Probe(hass, logger, domain="adaptive_cover_pro", debounce_seconds=100)
        with patch(f"{_MOD}.ir.async_create_issue") as create:
            probe._schedule("k1", "tk", {}, still_unhealthy=lambda: True)
            probe.shutdown()
            await _drain()
        create.assert_not_called()

    async def test_orphaned_issue_cleared_after_reload(self, logger):
        """A Repair raised in a prior lifetime clears when a fresh instance is healthy.

        The main fix path (options flow) reloads the config entry → a brand-new
        manager whose in-memory ``_active`` set is empty. A healthy ``_recover``
        in that fresh lifetime must still reconcile the issue registry once
        (``async_delete_issue`` is idempotent) so the stale Repair does not
        persist until an HA restart.
        """
        hass = _bg_hass()
        # Prior lifetime raised the Repair; the registry now carries it.
        prior = _Probe(hass, logger, domain="adaptive_cover_pro", debounce_seconds=0)
        with patch(f"{_MOD}.ir.async_create_issue"):
            prior._schedule("k1", "tk", {}, still_unhealthy=lambda: True)
            await _drain()
        # New lifetime: empty state, the condition is already healthy.
        fresh = _Probe(hass, logger, domain="adaptive_cover_pro", debounce_seconds=0)
        with patch(f"{_MOD}.ir.async_delete_issue") as delete:
            fresh._recover("k1")
        delete.assert_called_once()
        assert delete.call_args.args[2] == "k1"
        # Reconciled once per lifetime: a second healthy pass is a no-op.
        with patch(f"{_MOD}.ir.async_delete_issue") as delete2:
            fresh._recover("k1")
        delete2.assert_not_called()
