"""Proxy cover preemption + manual-override engagement (integration).

Covers the contract added to ``Coordinator.async_apply_user_position`` when
the proxy managed cover is moved:

- A higher-priority pipeline winner (force_override, weather) silently
  drops the move and records into ``last_skipped_action``.
- An allowed move engages manual override pre-emptively so the next
  coordinator cycle does not yank the cover off the user's set point.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_ENABLE_PROXY_COVER,
    CONF_ENTITIES,
    CONF_SENSOR_TYPE,
    DOMAIN,
    SensorType,
)
from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.types import (
    DecisionStep,
    PipelineResult,
)
from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

pytestmark = pytest.mark.integration


async def _setup_proxy(hass, *, source: str = "cover.living_room"):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from homeassistant.helpers import entity_registry as er

    base = dict(VERTICAL_OPTIONS)
    base[CONF_ENTITIES] = [source]
    base[CONF_ENABLE_PROXY_COVER] = True

    hass.states.async_set(
        source,
        "open",
        {"current_position": 50, "supported_features": 143},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Proxy Pre", CONF_SENSOR_TYPE: SensorType.BLIND},
        options=base,
        entry_id="proxy_pre",
        title="Proxy Pre",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    reg = er.async_get(hass)
    proxy_eid = next(
        e.entity_id
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id
        and e.unique_id.startswith(f"{entry.entry_id}_proxy_")
    )
    return entry, coordinator, proxy_eid


async def test_proxy_managed_blocked_when_force_override_active(hass) -> None:
    """force_override (priority 100) wins → no cover command, no manual override."""
    _, coord, proxy_eid = await _setup_proxy(hass)

    coord._pipeline = MagicMock()
    coord._pipeline.evaluate.return_value = PipelineResult(
        position=10,
        control_method=ControlMethod.FORCE,
        reason="force_override active",
        decision_trace=[
            DecisionStep(
                handler="force_override",
                matched=True,
                reason="force",
                position=10,
            )
        ],
    )
    fo = MagicMock()
    fo.priority = 100
    coord._handler_by_name = {"force_override": fo}
    coord._cmd_svc.apply_position = MagicMock()
    coord._cmd_svc.apply_position.side_effect = AssertionError(
        "apply_position must not be called when force_override preempts"
    )
    coord.manager.mark_user_command = MagicMock()

    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": proxy_eid, "position": 30},
        blocking=True,
    )

    coord.manager.mark_user_command.assert_not_called()
    assert coord._cmd_svc.last_skipped_action.get("reason") == "preempted_by_handler"
    assert coord._cmd_svc.last_skipped_action.get("winner") == "force_override"


async def test_proxy_managed_engages_manual_override_on_success(hass) -> None:
    """A successful proxy managed cover move marks the source cover as manually overridden."""
    _, coord, proxy_eid = await _setup_proxy(hass)

    # Pipeline winner: solar (priority 40 < ManualOverride priority 80).
    coord._pipeline = MagicMock()
    coord._pipeline.evaluate.return_value = PipelineResult(
        position=80,
        control_method=ControlMethod.SOLAR,
        reason="solar",
        decision_trace=[
            DecisionStep(handler="solar", matched=True, reason="solar", position=80)
        ],
    )
    handler = MagicMock()
    handler.priority = 40
    coord._handler_by_name = {"solar": handler}

    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": proxy_eid, "position": 30},
        blocking=True,
    )

    assert coord.manager.is_cover_manual("cover.living_room")
