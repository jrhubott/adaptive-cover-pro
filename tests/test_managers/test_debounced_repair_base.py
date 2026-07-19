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


class _Probe(_DebouncedRepairBase):
    """Trivial concrete subclass — the base carries all behavior under test."""


class TestDebouncedRepairBase:
    """Debounce-once, still-unhealthy re-check at expiry, shutdown cancel."""

    async def test_schedule_debounces_once_and_raises(self, logger):
        """A single debounce raises exactly one informational Repair."""
        hass = MagicMock()
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
        hass = MagicMock()
        probe = _Probe(hass, logger, domain="adaptive_cover_pro", debounce_seconds=0)
        unhealthy = True
        with patch(f"{_MOD}.ir.async_create_issue") as create:
            probe._schedule("k1", "tk", {}, still_unhealthy=lambda: unhealthy)
            unhealthy = False  # recovered before the expiry re-check
            await _drain()
        create.assert_not_called()

    async def test_shutdown_cancels_inflight_timers(self, logger):
        """``shutdown`` cancels a pending debounce so it never raises."""
        hass = MagicMock()
        probe = _Probe(hass, logger, domain="adaptive_cover_pro", debounce_seconds=100)
        with patch(f"{_MOD}.ir.async_create_issue") as create:
            probe._schedule("k1", "tk", {}, still_unhealthy=lambda: True)
            probe.shutdown()
            await _drain()
        create.assert_not_called()
