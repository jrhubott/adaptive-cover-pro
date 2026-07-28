"""Regression tests for issue #1052 — clearing a manual override must discard
the latched command target.

``ManualOverrideManager.set_transition_callbacks()`` exposes two edges:
``on_engaged`` (wired to ``CoverCommandService.discard_target``) and
``on_cleared``. Only ``on_engaged`` was wired, so a target latched *by* an
override outlived the override:

1. ``set_axes`` engages an override with target 0; ACP sends close_cover and
   latches ``target_call`` = 0.
2. The user stops the cover at 99.
3. The user cancels the override. The pipeline recomputes 100, but 100 vs 99
   is inside ``delta_position_threshold`` so the corrective command is gated
   away as ``same_position`` — ``target_call`` stays 0.
4. The entity leaves ``_manual_override_entities``, so the next reconcile tick
   stops skipping it, sees actual 99 against target 0, and resends 0.

The cover closes with no ``last_cover_action`` entry, because a reconcile
resend is not a pipeline command.
"""

from __future__ import annotations

import datetime as dt
import logging
from unittest.mock import MagicMock

import pytest
from homeassistant import config_entries as ha_config_entries
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.adaptive_cover_pro.const import (
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
)
from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
)
from tests.ha_helpers import VERTICAL_OPTIONS

pytestmark = pytest.mark.integration

ENTITY_ID = "cover.family_room_side_yard_shade"


def _build_coordinator(hass: HomeAssistant, entry_id: str):
    """Construct a real coordinator so the shipped callback wiring is exercised."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Side Yard Shade", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id=entry_id,
        title="Side Yard Shade",
    )
    entry.add_to_hass(hass)

    token = ha_config_entries.current_entry.set(entry)
    try:
        return AdaptiveDataUpdateCoordinator(hass)
    finally:
        ha_config_entries.current_entry.reset(token)


async def test_coordinator_wires_on_cleared_edge(hass: HomeAssistant) -> None:
    """The coordinator must subscribe to the override-cleared edge.

    ``on_cleared`` fires on every ``reset()`` but had no subscriber, which is
    what let the stale target survive the cancel.
    """
    coordinator = _build_coordinator(hass, "issue_1052_wiring")

    assert coordinator.manager._on_cleared is not None, (
        "coordinator must wire ManualOverrideManager.on_cleared — without a "
        "subscriber, cancelling an override leaves target_call latched and "
        "reconciliation resends it (#1052)"
    )


async def test_clearing_override_discards_latched_target(
    hass: HomeAssistant,
) -> None:
    """Cancelling an override drops the target that override latched."""
    coordinator = _build_coordinator(hass, "issue_1052_discard")
    manager = coordinator.manager
    cmd_svc = coordinator._cmd_svc

    manager.add_covers([ENTITY_ID])

    # The override's own command target: set_axes asked for 0, ACP sent
    # close_cover and latched it.
    cmd_svc.set_target(ENTITY_ID, 0)
    manager.manual_control[ENTITY_ID] = True

    manager.reset(ENTITY_ID)

    assert not cmd_svc.has_target(ENTITY_ID), (
        "clearing a manual override must discard the target the override "
        "latched — otherwise reconciliation chases it (#1052)"
    )


async def test_reconcile_does_not_resend_after_override_cleared(
    hass: HomeAssistant,
) -> None:
    """End-to-end: the reported failure, driven through a real reconcile pass.

    Cover parked at 99 with a stale target of 0. Before the fix the reconcile
    tick resent 0 and closed the shade.
    """
    coordinator = _build_coordinator(hass, "issue_1052_reconcile")
    manager = coordinator.manager
    cmd_svc = coordinator._cmd_svc

    manager.add_covers([ENTITY_ID])

    # Reconciliation only resends when position matching is on — the config
    # the reporting instance ran with.
    cmd_svc.enable_position_matching = True
    cmd_svc.auto_control_enabled = True
    cmd_svc.in_time_window = True

    cmd_svc.set_target(ENTITY_ID, 0)
    manager.manual_control[ENTITY_ID] = True
    cmd_svc.manual_override_entities = {ENTITY_ID}

    # User stops the cover at 99, then cancels the override.
    cmd_svc._get_current_position = MagicMock(return_value=99)
    cmd_svc._is_cover_in_transit = MagicMock(return_value=False)
    manager.reset(ENTITY_ID)
    cmd_svc.manual_override_entities = set(manager.manual_controlled)

    recorded = [
        async_mock_service(hass, "cover", service)
        for service in ("set_cover_position", "close_cover", "open_cover")
    ]

    await cmd_svc.run_reconciliation_pass(dt.datetime.now(dt.UTC))

    calls = [call for bucket in recorded for call in bucket]
    assert not calls, (
        "reconciliation resent the cancelled override's target — the shade "
        f"closes to 0 instead of holding the pipeline's 100 (#1052): {calls}"
    )


def test_discard_target_is_idempotent_for_untracked_entity() -> None:
    """``on_cleared`` fires for auto-expiry too, including entities that never
    had a target. The discard must be a no-op rather than raise.
    """
    svc = CoverCommandService(
        hass=MagicMock(),
        logger=logging.getLogger("test"),
        cover_type="cover_blind",
        grace_mgr=MagicMock(),
    )

    svc.discard_target(ENTITY_ID)  # never tracked

    assert not svc.has_target(ENTITY_ID)
