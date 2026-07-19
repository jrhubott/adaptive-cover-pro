"""Tests for RepairManager — informational Repairs from config-coherence predicates.

The predicate manager has no entity to poll: the coordinator computes a boolean
each cycle and pushes it via ``update_predicate``. The manager stores it and
drives the shared debounce/raise/clear lifecycle, re-reading the stored boolean
at expiry so a config edit that fixes the incoherence mid-debounce suppresses
the Repair.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.helpers import issue_registry as ir

from custom_components.adaptive_cover_pro.managers.repair import RepairManager

pytestmark = pytest.mark.unit

# ``ir.*`` is resolved in the shared base module where ``_raise`` is defined.
_MOD = "custom_components.adaptive_cover_pro.managers.common.debounced_repair"
_ISSUE_KEY = "config_position_envelope_entry1"
_TRANSLATION_KEY = "config_position_envelope"


@pytest.fixture
def logger():
    return logging.getLogger("test.repair")


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


class TestRepairManager:
    """Raise-on-sustained-incoherence, clear-on-fix, debounce, shutdown."""

    async def test_predicate_raises_when_unhealthy_past_debounce(self, logger):
        """An incoherent config held past debounce raises the informational Repair."""
        mgr = RepairManager(
            _bg_hass(), logger, domain="adaptive_cover_pro", debounce_seconds=0
        )
        mgr.update_predicate(
            _ISSUE_KEY,
            True,
            translation_key=_TRANSLATION_KEY,
            placeholders={"name": "Bedroom"},
        )
        with patch(f"{_MOD}.ir.async_create_issue") as create:
            mgr.evaluate()
            await _drain()
        create.assert_called_once()
        _args, kwargs = create.call_args
        assert kwargs.get("is_fixable") is False
        assert kwargs.get("severity") == ir.IssueSeverity.WARNING
        assert kwargs.get("translation_key") == _TRANSLATION_KEY

    async def test_predicate_cleared_when_healthy(self, logger):
        """A raised Repair is deleted once the predicate turns coherent."""
        mgr = RepairManager(
            _bg_hass(), logger, domain="adaptive_cover_pro", debounce_seconds=0
        )
        mgr.update_predicate(_ISSUE_KEY, True, translation_key=_TRANSLATION_KEY)
        with (
            patch(f"{_MOD}.ir.async_create_issue"),
            patch(f"{_MOD}.ir.async_delete_issue") as delete,
        ):
            mgr.evaluate()
            await _drain()
            mgr.update_predicate(_ISSUE_KEY, False, translation_key=_TRANSLATION_KEY)
            mgr.evaluate()
        delete.assert_called_once()

    async def test_predicate_fixed_before_expiry_suppressed(self, logger):
        """Fixed before the debounce elapses → no Repair (debounce gate)."""
        mgr = RepairManager(
            _bg_hass(), logger, domain="adaptive_cover_pro", debounce_seconds=100
        )
        mgr.update_predicate(_ISSUE_KEY, True, translation_key=_TRANSLATION_KEY)
        with (
            patch(f"{_MOD}.ir.async_create_issue") as create,
            patch(f"{_MOD}.ir.async_delete_issue"),
        ):
            mgr.evaluate()  # starts the (long) debounce timer
            mgr.update_predicate(_ISSUE_KEY, False, translation_key=_TRANSLATION_KEY)
            mgr.evaluate()  # cancels the pending timer
            await _drain()
        create.assert_not_called()

    async def test_shutdown_cancels(self, logger):
        """``shutdown`` cancels an in-flight predicate debounce."""
        mgr = RepairManager(
            _bg_hass(), logger, domain="adaptive_cover_pro", debounce_seconds=100
        )
        mgr.update_predicate(_ISSUE_KEY, True, translation_key=_TRANSLATION_KEY)
        with patch(f"{_MOD}.ir.async_create_issue") as create:
            mgr.evaluate()
            mgr.shutdown()
            await _drain()
        create.assert_not_called()

    async def test_orphan_cleared_on_fresh_instance(self, logger):
        """A predicate healthy on a fresh manager clears a Repair from a prior lifetime.

        The options-flow fix reloads the entry → a new RepairManager with an
        empty ``_active`` set. Evaluating the (now coherent) predicate must still
        reconcile the registry once so the stale Repair clears without an HA
        restart.
        """
        mgr = RepairManager(
            _bg_hass(), logger, domain="adaptive_cover_pro", debounce_seconds=0
        )
        mgr.update_predicate(_ISSUE_KEY, False, translation_key=_TRANSLATION_KEY)
        with patch(f"{_MOD}.ir.async_delete_issue") as delete:
            mgr.evaluate()  # healthy on a brand-new instance (empty _active)
        delete.assert_called_once()
        assert delete.call_args.args[2] == _ISSUE_KEY
