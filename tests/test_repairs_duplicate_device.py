"""The fixable Repair that deletes a leftover duplicate device (issue #1369).

Everything here runs against real device, entity and issue registries.  The flow
is driven directly rather than through ``hass.data_entry_flow`` because the
interesting behaviour is the guard in front of ``async_remove_device``, not
HA's flow plumbing — and because the guard is a data-loss guard: removing a
device removes every entity of that device's config entries with it
(``entity_registry.async_device_modified`` on ``action == "remove"``).
"""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.repairs import ConfirmRepairFlow
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.adaptive_cover_pro.const import (
    DOMAIN,
    ISSUE_DATA_DEVICE_ID,
    ISSUE_DATA_ENTRY_ID,
    ISSUE_DUPLICATE_DEVICE,
    duplicate_device_issue_id,
)
from custom_components.adaptive_cover_pro.repairs import async_create_fix_flow
from custom_components.adaptive_cover_pro.state.device_link import (
    device_config_entry_ids,
)
from tests.ha_helpers import setup_integration


async def _seed_leftover(
    hass,
    *,
    entry_id: str,
    with_entity: bool = False,
    with_foreign_entity: bool = False,
) -> tuple[MockConfigEntry, dr.DeviceEntry, str]:
    """Set an ACP entry up, then park a leftover device + its Repair beside it.

    ``with_foreign_entity`` puts an entity belonging to a *helper* config entry
    on the leftover — the shape that gets no Repair raised against it any more,
    but can still be named by one the issue registry persisted before the
    upgrade.  Returns ``(entry, leftover, issue_id)``.
    """
    entry = await setup_integration(hass, name="Repairable", entry_id=entry_id)

    dev_reg = dr.async_get(hass)
    leftover = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("demo", f"leftover-{entry_id}")},
        name="Leftover Duplicate",
    )
    if with_entity:
        er.async_get(hass).async_get_or_create(
            "sensor",
            DOMAIN,
            f"{entry.entry_id}_leftover_probe",
            config_entry=entry,
            device_id=leftover.id,
        )
    if with_foreign_entity:
        helper = MockConfigEntry(domain="input_number", entry_id=f"{entry_id}_helper")
        helper.add_to_hass(hass)
        er.async_get(hass).async_get_or_create(
            "sensor",
            helper.domain,
            f"{entry_id}_foreign_probe",
            config_entry=helper,
            device_id=leftover.id,
        )

    issue_id = duplicate_device_issue_id(entry.entry_id, leftover.id)
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_DUPLICATE_DEVICE,
        translation_placeholders={"name": entry.title, "device_name": leftover.name},
        data={
            ISSUE_DATA_ENTRY_ID: entry.entry_id,
            ISSUE_DATA_DEVICE_ID: leftover.id,
        },
    )
    return entry, leftover, issue_id


async def _start_flow(hass, issue_id: str, data: dict | None):
    """Build the fix flow for *issue_id* and bind the attributes HA would."""
    flow = await async_create_fix_flow(hass, issue_id, data)
    flow.hass = hass
    flow.handler = DOMAIN
    flow.issue_id = issue_id
    return flow


def _own_device_id(hass, entry_id: str) -> str:
    """Return the id of the ACP service device for *entry_id*."""
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry_id):
        if (DOMAIN, entry_id) in device.identifiers:
            return device.id
    raise AssertionError("the entry owns no service device")


@pytest.mark.asyncio
async def test_fix_flow_removes_entity_less_leftover(hass):
    """Confirming the Repair deletes the leftover and nothing else."""
    entry, leftover, issue_id = await _seed_leftover(hass, entry_id="repair_removes")
    own_id = _own_device_id(hass, entry.entry_id)
    ent_reg = er.async_get(hass)
    before = {
        entity.entity_id
        for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    }

    flow = await _start_flow(
        hass,
        issue_id,
        {ISSUE_DATA_ENTRY_ID: entry.entry_id, ISSUE_DATA_DEVICE_ID: leftover.id},
    )
    form = await flow.async_step_init()
    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "confirm"

    result = await flow.async_step_confirm({})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    dev_reg = dr.async_get(hass)
    assert dev_reg.async_get(leftover.id) is None
    assert dev_reg.async_get(own_id) is not None
    assert {
        entity.entity_id
        for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    } == before


@pytest.mark.asyncio
async def test_fix_flow_aborts_when_entities_still_attached(hass):
    """A device that still holds entities is never removed.

    The data-loss guard, and the reason the flow checks the registry rather than
    trusting the Repair's own payload: ``async_remove_device`` removes every
    entity belonging to the removed device's config entries, so confirming a
    stale Repair would silently delete the user's sensors.
    """
    entry, leftover, issue_id = await _seed_leftover(
        hass, entry_id="repair_in_use", with_entity=True
    )

    flow = await _start_flow(
        hass,
        issue_id,
        {ISSUE_DATA_ENTRY_ID: entry.entry_id, ISSUE_DATA_DEVICE_ID: leftover.id},
    )
    result = await flow.async_step_confirm({})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "device_in_use"
    assert dr.async_get(hass).async_get(leftover.id) is not None
    assert er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_leftover_probe"
    )


@pytest.mark.asyncio
async def test_fix_flow_aborts_for_a_device_holding_only_foreign_entities(hass):
    """A leftover carrying somebody else's entities is refused too.

    Setup no longer raises a Repair for this shape, but the issue registry is
    persisted, so a Repair raised by an older build can still be sitting there
    naming one after the upgrade.  Removing the device would delete the helper's
    registry row along with it, which is the whole reason the shape stopped
    being offered as fixable.
    """
    entry, leftover, issue_id = await _seed_leftover(
        hass, entry_id="repair_foreign_entity", with_foreign_entity=True
    )

    flow = await _start_flow(
        hass,
        issue_id,
        {ISSUE_DATA_ENTRY_ID: entry.entry_id, ISSUE_DATA_DEVICE_ID: leftover.id},
    )
    result = await flow.async_step_confirm({})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "device_in_use"
    assert dr.async_get(hass).async_get(leftover.id) is not None
    assert er.async_get(hass).async_get_entity_id(
        "sensor", "input_number", "repair_foreign_entity_foreign_probe"
    )


@pytest.mark.asyncio
async def test_fix_flow_ignores_device_it_does_not_own(hass):
    """The stored device id is never trusted on its own.

    A Repair payload naming a device this entry does not solely own — the
    physical cover, say — must be refused outright: "remove by stored id" is how
    a repair flow deletes a real integration's device.
    """
    entry, leftover, issue_id = await _seed_leftover(hass, entry_id="repair_foreign")

    owner = MockConfigEntry(domain="demo", entry_id="repair_foreign_owner")
    owner.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    physical = dev_reg.async_get_or_create(
        config_entry_id=owner.entry_id,
        identifiers={("demo", "physical-repair")},
        name="Physical Cover",
    )

    flow = await _start_flow(
        hass,
        issue_id,
        {ISSUE_DATA_ENTRY_ID: entry.entry_id, ISSUE_DATA_DEVICE_ID: physical.id},
    )
    result = await flow.async_step_confirm({})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "device_in_use"
    assert dev_reg.async_get(physical.id) is not None
    assert dev_reg.async_get(leftover.id) is not None


@pytest.mark.asyncio
async def test_other_issue_ids_still_get_confirm_flow(hass):
    """Every other Repair this integration raises keeps the plain confirm flow.

    The dispatch is on the duplicate-device prefix only; the informational
    sensor-health Repairs (issue #786) are ``is_fixable=False`` and must not
    acquire a side effect by accident.
    """
    flow = await async_create_fix_flow(hass, "temp_sensor_unavailable_x", None)

    assert type(flow) is ConfirmRepairFlow


@pytest.mark.asyncio
async def test_fix_flow_aborts_when_the_device_is_already_gone(hass):
    """A Repair whose device has since been deleted aborts instead of raising.

    The user can delete the duplicate from the device page and reach the Repair
    afterwards — the issue is only swept on the next load.  Confirming it then
    must land on the same "nothing to do" abort as every other refusal, not an
    exception out of the flow.
    """
    entry, leftover, issue_id = await _seed_leftover(hass, entry_id="repair_vanished")
    own_id = _own_device_id(hass, entry.entry_id)
    ent_reg = er.async_get(hass)
    before = {
        entity.entity_id
        for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    }

    dev_reg = dr.async_get(hass)
    dev_reg.async_remove_device(leftover.id)
    await hass.async_block_till_done()
    assert dev_reg.async_get(leftover.id) is None

    flow = await _start_flow(
        hass,
        issue_id,
        {ISSUE_DATA_ENTRY_ID: entry.entry_id, ISSUE_DATA_DEVICE_ID: leftover.id},
    )
    result = await flow.async_step_confirm({})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "device_in_use"
    assert dev_reg.async_get(own_id) is not None
    assert {
        entity.entity_id
        for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    } == before


@pytest.mark.asyncio
async def test_fix_flow_never_removes_our_own_service_device(hass):
    """A Repair payload naming our own service device is refused.

    The identifier guard, and the one with the largest blast radius: our service
    device is where every entity of this instance lives, and
    ``async_remove_device`` takes the entities of the removed device's config
    entries with it.  A stale Repair raised against a record setup has since
    adopted as our own would otherwise wipe the whole instance on one click.
    """
    entry, leftover, issue_id = await _seed_leftover(hass, entry_id="repair_own")
    own_id = _own_device_id(hass, entry.entry_id)
    ent_reg = er.async_get(hass)
    on_own = {
        entity.entity_id
        for entity in er.async_entries_for_device(
            ent_reg, own_id, include_disabled_entities=True
        )
    }
    assert on_own, "the service device is where this instance's entities live"

    flow = await _start_flow(
        hass,
        issue_id,
        {ISSUE_DATA_ENTRY_ID: entry.entry_id, ISSUE_DATA_DEVICE_ID: own_id},
    )
    result = await flow.async_step_confirm({})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "device_in_use"
    assert dr.async_get(hass).async_get(own_id) is not None
    assert {
        entity.entity_id
        for entity in er.async_entries_for_device(
            ent_reg, own_id, include_disabled_entities=True
        )
    } == on_own
    assert dr.async_get(hass).async_get(leftover.id) is not None


@pytest.mark.asyncio
async def test_fix_flow_refuses_our_own_device_that_holds_no_entities(hass):
    """The identifier guard stands on its own, with no entity check behind it.

    The test above is also caught by the entity check, so it cannot tell whether
    the identifier guard is still there.  This one hands the flow an ACP service
    device that is sole-owned and entity-less — every other guard passes — so
    only ``(DOMAIN, entry_id) in device.identifiers`` can refuse it.  Deleting or
    reordering that clause turns this red.
    """
    _entry, _leftover, issue_id = await _seed_leftover(hass, entry_id="repair_own_bare")

    bare = MockConfigEntry(domain=DOMAIN, entry_id="repair_own_bare_second")
    bare.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    bare_own = dev_reg.async_get_or_create(
        config_entry_id=bare.entry_id,
        identifiers={(DOMAIN, bare.entry_id)},
        name="Service Device",
    )
    assert device_config_entry_ids(bare_own) == {bare.entry_id}
    assert not er.async_entries_for_device(
        er.async_get(hass), bare_own.id, include_disabled_entities=True
    )

    flow = await _start_flow(
        hass,
        issue_id,
        {ISSUE_DATA_ENTRY_ID: bare.entry_id, ISSUE_DATA_DEVICE_ID: bare_own.id},
    )
    result = await flow.async_step_confirm({})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "device_in_use"
    assert dev_reg.async_get(bare_own.id) is not None
