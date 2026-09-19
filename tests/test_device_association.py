"""Tests for optional device association feature."""

import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceEntryType

from custom_components.adaptive_cover_pro.const import (
    CONF_DEVICE_ID,
    CONF_ENTITIES,
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
    duplicate_device_issue_id,
)
from tests.ha_helpers import (
    HA_DEVICE_REGISTRY_V3,
    VERTICAL_OPTIONS,
    _patch_coordinator_refresh,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_hass():
    """Return a mock HomeAssistant instance."""
    return MagicMock()


# ---------------------------------------------------------------------------
# _get_devices_from_entities helper tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_devices_from_entities_no_entities(mock_hass):
    """Helper returns empty dict when entity_ids list is empty."""
    from custom_components.adaptive_cover_pro.config_flow import (
        _get_devices_from_entities,
    )

    with (
        patch("custom_components.adaptive_cover_pro.state.device_link.er") as mock_er,
        patch("custom_components.adaptive_cover_pro.state.device_link.dr") as mock_dr,
    ):
        mock_er.async_get.return_value = MagicMock()
        mock_dr.async_get.return_value = MagicMock()

        result = await _get_devices_from_entities(mock_hass, [])

    assert result == {}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_devices_from_entities_entity_has_no_device(mock_hass):
    """Helper returns empty dict when entity has no device_id."""
    from custom_components.adaptive_cover_pro.config_flow import (
        _get_devices_from_entities,
    )

    with (
        patch("custom_components.adaptive_cover_pro.state.device_link.er") as mock_er,
        patch("custom_components.adaptive_cover_pro.state.device_link.dr") as mock_dr,
    ):
        entity_reg = MagicMock()
        entity_entry = MagicMock()
        entity_entry.device_id = None
        entity_reg.async_get.return_value = entity_entry
        mock_er.async_get.return_value = entity_reg
        mock_dr.async_get.return_value = MagicMock()

        result = await _get_devices_from_entities(mock_hass, ["cover.test_blind"])

    assert result == {}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_devices_from_entities_entity_has_device(mock_hass):
    """Helper returns device dict when entity has an associated device."""
    from custom_components.adaptive_cover_pro.config_flow import (
        _get_devices_from_entities,
    )

    with (
        patch("custom_components.adaptive_cover_pro.state.device_link.er") as mock_er,
        patch("custom_components.adaptive_cover_pro.state.device_link.dr") as mock_dr,
    ):
        entity_reg = MagicMock()
        entity_entry = MagicMock()
        entity_entry.device_id = "device-abc-123"
        entity_reg.async_get.return_value = entity_entry
        mock_er.async_get.return_value = entity_reg

        device_reg = MagicMock()
        device_entry = MagicMock()
        device_entry.name_by_user = None
        device_entry.name = "My Blind Motor"
        device_reg.async_get.return_value = device_entry
        mock_dr.async_get.return_value = device_reg

        result = await _get_devices_from_entities(mock_hass, ["cover.test_blind"])

    assert "device-abc-123" in result
    assert result["device-abc-123"] == "My Blind Motor"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_devices_from_entities_deduplicates(mock_hass):
    """Helper de-duplicates devices when multiple entities share one device."""
    from custom_components.adaptive_cover_pro.config_flow import (
        _get_devices_from_entities,
    )

    with (
        patch("custom_components.adaptive_cover_pro.state.device_link.er") as mock_er,
        patch("custom_components.adaptive_cover_pro.state.device_link.dr") as mock_dr,
    ):
        entity_reg = MagicMock()
        entity_entry = MagicMock()
        entity_entry.device_id = "device-abc-123"
        entity_reg.async_get.return_value = entity_entry
        mock_er.async_get.return_value = entity_reg

        device_reg = MagicMock()
        device_entry = MagicMock()
        device_entry.name_by_user = "Custom Name"
        device_entry.name = "Motor"
        device_reg.async_get.return_value = device_entry
        mock_dr.async_get.return_value = device_reg

        result = await _get_devices_from_entities(
            mock_hass, ["cover.blind1", "cover.blind2"]
        )

    # Two entities, same device → only one entry
    assert len(result) == 1
    assert result["device-abc-123"] == "Custom Name"


# ---------------------------------------------------------------------------
# entity_base.device_info tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_device_info_standalone_when_no_device_id(mock_hass):
    """device_info returns standalone virtual device when CONF_DEVICE_ID not set."""
    from custom_components.adaptive_cover_pro.entity_base import AdaptiveCoverBaseEntity

    config_entry = MagicMock()
    config_entry.data = {"name": "My Blind", CONF_SENSOR_TYPE: "cover_blind"}
    config_entry.options = {}  # No CONF_DEVICE_ID

    entity = AdaptiveCoverBaseEntity.__new__(AdaptiveCoverBaseEntity)
    entity.hass = mock_hass
    entity.config_entry = config_entry
    entity._name = "My Blind"
    entity._cover_type = "cover_blind"
    entity._device_id = "test-entry-id"

    info = entity.device_info
    assert (DOMAIN, "test-entry-id") in info["identifiers"]
    assert info.get("manufacturer") == "Jason Rhubottom"


@pytest.mark.unit
def test_device_info_is_own_service_device_when_linked(mock_hass):
    """A linked instance still reports ACP's OWN service device (issue #1369).

    ``device_info`` used to borrow the physical device's identifiers so HA would
    union the two config entries onto one device record.  HA 2026.8's storage-v3
    registry scopes the identifier lookup by ``config_entry_id``, so the borrow
    no longer merges — it mints a *second* device with the same identifiers.  The
    link is now expressed as a ``via_device_id`` written after platform setup by
    ``state/device_link.mirror_link``, which is why ``device_info`` itself carries
    no via key in either spelling: ``via_device`` is rejected on 2026.9.2 and
    ``via_device_id`` is not a ``DeviceInfo`` field on 2026.6.3.

    The registry is patched to hand back a *foreign* device on purpose: if
    anything here ever reaches for it again, the borrowed identifiers show up in
    the assertion below.
    """
    from custom_components.adaptive_cover_pro.entity_base import AdaptiveCoverBaseEntity

    config_entry = MagicMock()
    config_entry.data = {"name": "My Blind", CONF_SENSOR_TYPE: "cover_blind"}
    config_entry.options = {CONF_DEVICE_ID: "device-abc-123"}

    device_entry = MagicMock()
    device_entry.identifiers = {("some_integration", "motor-id")}
    device_entry.connections = set()

    device_reg = MagicMock(spec=dr.DeviceRegistry)
    device_reg.async_get.return_value = device_entry

    with patch(
        "homeassistant.helpers.device_registry.async_get", return_value=device_reg
    ):
        entity = AdaptiveCoverBaseEntity.__new__(AdaptiveCoverBaseEntity)
        entity.hass = mock_hass
        entity.config_entry = config_entry
        entity._name = "My Blind"
        entity._cover_type = "cover_blind"
        entity._device_id = "test-entry-id"

        info = entity.device_info

    assert info["identifiers"] == {(DOMAIN, "test-entry-id")}
    assert "via_device" not in info
    assert "via_device_id" not in info
    assert info["manufacturer"] == "Jason Rhubottom"
    assert info["entry_type"] == DeviceEntryType.SERVICE


# ---------------------------------------------------------------------------
# Options flow: pre-population and removal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_options_device_step_prefills_live_device_for_stale_id(hass):
    """The device picker opens on the live device, not the stale stored id.

    The stored id can name a composite HA has since split, which owns no
    entities and so is not even in the picker's option list — leaving the field
    blank and inviting the user to "fix" a link that is already correct.  The
    picker resolves the same way setup does, so what it shows is what is
    actually in force.
    """
    from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler

    owner = MockConfigEntry(domain="demo", entry_id="prefill_owner")
    owner.add_to_hass(hass)
    physical = _make_physical_device(
        hass, owner=owner, identifiers={("demo", "prefill-1")}
    )
    acp_entry = _make_acp_entry(
        hass,
        "prefill_entry",
        {
            **VERTICAL_OPTIONS,
            CONF_DEVICE_ID: "old-composite",
            CONF_ENTITIES: ["cover.test_blind"],
        },
    )

    live = SimpleNamespace(
        id=physical.id,
        config_entries={owner.entry_id},
        composite_device_id="old-composite",
        name_by_user=None,
        name="Physical Cover",
    )
    standin = SimpleNamespace(
        id="old-composite",
        config_entries={acp_entry.entry_id},
        composite_device_id=None,
        name_by_user=None,
        name="Composite",
    )
    views = {physical.id: live, "old-composite": standin}

    def _async_get(self, device_id, **kwargs):
        return views.get(device_id)

    captured: dict = {}

    def _capture(self, schema, suggested):
        captured.update(suggested)
        return schema

    flow = OptionsFlowHandler(acp_entry)
    flow.hass = hass

    with (
        patch.object(dr.DeviceRegistry, "async_get", _async_get),
        patch.object(OptionsFlowHandler, "add_suggested_values_to_schema", _capture),
    ):
        await flow.async_step_cover_entities()

    assert captured[CONF_DEVICE_ID] == physical.id


@pytest.mark.unit
def test_options_device_step_removal_clears_device_id():
    """Options flow: selecting 'None' removes CONF_DEVICE_ID from options."""
    options: dict = {CONF_DEVICE_ID: "device-abc-123"}

    user_input = {CONF_DEVICE_ID: ""}
    device_id = user_input.get(CONF_DEVICE_ID, "")
    if device_id:
        options[CONF_DEVICE_ID] = device_id
    else:
        options.pop(CONF_DEVICE_ID, None)

    assert CONF_DEVICE_ID not in options


# ---------------------------------------------------------------------------
# async_setup_entry: device-link reconciliation (issue #1369)
# ---------------------------------------------------------------------------
#
# These use the real ``hass`` fixture with real device/entity/area registries —
# a mock would only re-pin whichever registry API the production code happens to
# call today, which is exactly what HA's storage-v3 device model changed.  They
# deliberately carry no ``integration`` mark so they run in every
# ``scripts/test`` mode.


def _seed_cover_states(hass) -> None:
    """Seed the ``sun.sun`` / ``cover.test_blind`` states setup reads."""
    now = datetime.datetime.now(datetime.UTC).isoformat()
    hass.states.async_set(
        "sun.sun",
        "above_horizon",
        {
            "azimuth": 180.0,
            "elevation": 45.0,
            "rising": True,
            "next_rising": now,
            "next_setting": now,
        },
    )
    hass.states.async_set(
        "cover.test_blind",
        "open",
        {"current_position": 100, "supported_features": 143},
    )


def _acp_devices(hass, entry_id) -> list:
    """Every device the ACP entry owns, through the registry's own index."""
    return dr.async_entries_for_config_entry(dr.async_get(hass), entry_id)


def _own_device(hass, entry_id):
    """Return the ACP service device for ``entry_id`` (ours carries our identifier)."""
    for device in _acp_devices(hass, entry_id):
        if (DOMAIN, entry_id) in device.identifiers:
            return device
    return None


def _bind_acp_entity(
    hass, acp_entry, device_id: str, suffix: str, *, disabled: bool = False
):
    """Register one ACP-owned entity on *device_id*.

    A plain ``sensor`` row with a unique_id none of the legacy prunes match, so
    it is still there when setup looks at what the device holds.
    """
    return er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{acp_entry.entry_id}_{suffix}",
        config_entry=acp_entry,
        device_id=device_id,
        disabled_by=er.RegistryEntryDisabler.USER if disabled else None,
    )


def _bind_foreign_entity(hass, owner, device_id: str, unique_id: str):
    """Register one entity belonging to *owner* on *device_id*.

    Nothing stops a config entry's entity from naming a device another entry
    owns — the entity registry only checks that the device exists — and that is
    exactly how a user-created helper ends up riding on a record Adaptive Cover
    Pro owns.  ``unique_id`` is used verbatim so a test can look the row back up.
    """
    return er.async_get(hass).async_get_or_create(
        "sensor",
        owner.domain,
        unique_id,
        config_entry=owner,
        device_id=device_id,
    )


def _duplicate_device_issues(hass, entry_id) -> set[str]:
    """Every ``duplicate_device`` Repair this entry currently has raised."""
    registry = ir.async_get(hass)
    prefix = duplicate_device_issue_id(entry_id)
    return {
        issue_id
        for reg_domain, issue_id in registry.issues
        if reg_domain == DOMAIN and issue_id.startswith(prefix)
    }


def _make_acp_entry(hass, entry_id: str, options: dict, *, title: str = "Linked Cover"):
    """Register (but do not set up) an ACP cover entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": title, CONF_SENSOR_TYPE: CoverType.BLIND},
        options=options,
        entry_id=entry_id,
        title=title,
    )
    entry.add_to_hass(hass)
    return entry


def _make_physical_device(
    hass,
    *,
    owner: MockConfigEntry,
    identifiers: set[tuple[str, str]],
    area_id: str | None = None,
    bind_cover_entity: bool = True,
):
    """Create a foreign-owned cover device carrying ``cover.test_blind``."""
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=owner.entry_id,
        identifiers=identifiers,
        name="Physical Cover",
    )
    if area_id is not None:
        dev_reg.async_update_device(device.id, area_id=area_id)
    if bind_cover_entity:
        er.async_get(hass).async_get_or_create(
            "cover",
            "demo",
            f"blind-{device.id}",
            suggested_object_id="test_blind",
            config_entry=owner,
            device_id=device.id,
        )
    return dev_reg.async_get(device.id)


async def _setup_acp_entry_owning_device(
    hass,
    *,
    entry_id: str,
    physical_identifiers: set[tuple[str, str]],
    link: bool = True,
    area_id: str | None = None,
):
    """Set an ACP entry up beside a foreign-owned physical cover device.

    The physical device belongs to a ``demo`` config entry and carries the real
    ``cover.test_blind`` registry row, exactly as a cover integration would leave
    it.  ``link`` decides whether the ACP entry stores the device id in
    ``CONF_DEVICE_ID``.  Nothing here ever co-owns the physical device — that is
    the whole point of issue #1369 — so it runs unchanged on both registry
    models.  Returns ``(acp_entry, physical_device)``, the device re-read from
    the registry after setup.
    """
    owner = MockConfigEntry(domain="demo", entry_id=f"{entry_id}_owner")
    owner.add_to_hass(hass)
    device = _make_physical_device(
        hass, owner=owner, identifiers=physical_identifiers, area_id=area_id
    )

    options = dict(VERTICAL_OPTIONS)
    if link:
        options[CONF_DEVICE_ID] = device.id
    acp_entry = _make_acp_entry(hass, entry_id, options)

    _seed_cover_states(hass)

    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(acp_entry.entry_id)
        await hass.async_block_till_done()

    return acp_entry, dr.async_get(hass).async_get(device.id)


@pytest.mark.asyncio
async def test_linked_setup_creates_own_device_via_physical(hass):
    """A linked entry owns its own service device and points it at the physical one.

    The closed TEST_GAP: this is the assertion no mocked test could make.  It has
    to hold on both registry models — on storage v3 the physical device simply
    cannot carry our config entry id, and before v3 it must not, because that
    union is the duplicate-device bug.
    """
    acp_entry, physical = await _setup_acp_entry_owning_device(
        hass,
        entry_id="link_via_device",
        physical_identifiers={("demo", "physical-1")},
    )

    own = _own_device(hass, acp_entry.entry_id)
    assert own is not None
    assert own.via_device_id == physical.id
    assert physical.identifiers == {("demo", "physical-1")}
    assert acp_entry.entry_id not in physical.config_entries
    assert len(_acp_devices(hass, acp_entry.entry_id)) == 1

    ent_reg = er.async_get(hass)
    entities = er.async_entries_for_config_entry(ent_reg, acp_entry.entry_id)
    assert entities
    assert {entity.device_id for entity in entities} == {own.id}


@pytest.mark.asyncio
async def test_linked_setup_mirrors_physical_area(hass):
    """Our service device adopts the physical device's area at setup.

    ``via_device_id`` alone would leave the ACP card in "no area", which is the
    visible half of what the old identifier borrow bought.
    """
    area = ar.async_get(hass).async_create("Living Room")
    acp_entry, physical = await _setup_acp_entry_owning_device(
        hass,
        entry_id="area_mirror",
        physical_identifiers={("demo", "physical-2")},
        area_id=area.id,
    )

    assert physical.area_id == area.id
    assert _own_device(hass, acp_entry.entry_id).area_id == area.id


@pytest.mark.asyncio
async def test_area_mirror_follows_physical_moves(hass):
    """Moving the physical cover between areas drags our service device along.

    Clearing the physical device's area clears ours too — a one-way mirror, not
    a sticky copy.
    """
    area_reg = ar.async_get(hass)
    first = area_reg.async_create("Kitchen")
    other = area_reg.async_create("Study")

    acp_entry, physical = await _setup_acp_entry_owning_device(
        hass,
        entry_id="area_follow",
        physical_identifiers={("demo", "physical-3")},
        area_id=first.id,
    )
    assert _own_device(hass, acp_entry.entry_id).area_id == first.id

    dev_reg = dr.async_get(hass)
    dev_reg.async_update_device(physical.id, area_id=other.id)
    await hass.async_block_till_done()
    assert _own_device(hass, acp_entry.entry_id).area_id == other.id

    dev_reg.async_update_device(physical.id, area_id=None)
    await hass.async_block_till_done()
    assert _own_device(hass, acp_entry.entry_id).area_id is None


@pytest.mark.asyncio
async def test_unlinked_setup_clears_via_link(hass):
    """Clearing the device association drops the via link but keeps the area.

    The area is a user-visible placement they may well have kept on purpose;
    only the link we created is ours to withdraw.
    """
    area = ar.async_get(hass).async_create("Hall")
    acp_entry, physical = await _setup_acp_entry_owning_device(
        hass,
        entry_id="unlink_clears",
        physical_identifiers={("demo", "physical-4")},
        area_id=area.id,
    )
    assert _own_device(hass, acp_entry.entry_id).via_device_id == physical.id

    with _patch_coordinator_refresh():
        hass.config_entries.async_update_entry(
            acp_entry,
            options={
                key: value
                for key, value in acp_entry.options.items()
                if key != CONF_DEVICE_ID
            },
        )
        await hass.async_block_till_done()

    own = _own_device(hass, acp_entry.entry_id)
    assert own.via_device_id is None
    assert own.area_id == area.id


@pytest.mark.asyncio
async def test_stored_id_that_is_a_composite_resolves_to_live_split(hass):
    """A stored id that now names a composite resolves to the live split device.

    HA 2026.9 can split one device into several and keep the old id as the
    composite's.  The bound cover entity is the source of truth, so the resolver
    hops entity → device first; the stored id is only a fallback, and the
    fallback refuses anything our own entry owns so a reload can never link us
    to ourselves.
    """
    from custom_components.adaptive_cover_pro.state.device_link import (
        resolve_linked_device,
    )

    owner = MockConfigEntry(domain="demo", entry_id="composite_owner")
    owner.add_to_hass(hass)
    physical = _make_physical_device(
        hass, owner=owner, identifiers={("demo", "split-1")}
    )

    live = SimpleNamespace(
        id=physical.id,
        config_entries={owner.entry_id},
        composite_device_id="old-composite",
    )
    standin = SimpleNamespace(
        id="old-composite",
        config_entries={"acp_composite"},
        composite_device_id=None,
    )
    views = {physical.id: live, "old-composite": standin}

    def _async_get(self, device_id, **kwargs):
        return views.get(device_id)

    options = {CONF_DEVICE_ID: "old-composite", CONF_ENTITIES: ["cover.test_blind"]}
    with patch.object(dr.DeviceRegistry, "async_get", _async_get):
        resolved = resolve_linked_device(hass, options, own_entry_id="acp_composite")

    assert resolved is live


@pytest.mark.asyncio
async def test_stored_single_owner_device_resolves_to_itself(hass):
    """The inverse: a live, foreign-owned device stored directly needs no hop.

    Without this the composite test above could pass on a resolver that ignored
    the stored id entirely.
    """
    from custom_components.adaptive_cover_pro.state.device_link import (
        resolve_linked_device,
    )

    owner = MockConfigEntry(domain="demo", entry_id="direct_owner")
    owner.add_to_hass(hass)
    physical = _make_physical_device(
        hass,
        owner=owner,
        identifiers={("demo", "direct-1")},
        bind_cover_entity=False,
    )

    options = {CONF_DEVICE_ID: physical.id, CONF_ENTITIES: ["cover.test_blind"]}
    resolved = resolve_linked_device(hass, options, own_entry_id="acp_direct")

    assert resolved is not None
    assert resolved.id == physical.id


@pytest.mark.asyncio
async def test_resolve_refuses_a_device_our_own_entry_owns(hass):
    """A device this entry owns is never resolved as "the physical cover".

    An install carrying a pre-fix duplicate has our own cover entities bound to
    a device *we* own.  Resolving that as the physical cover would mirror our
    own area back onto ourselves and point ``via_device_id`` at our own record —
    which HA 2026.8 raises on.  Both halves of the resolver refuse it: the
    entity hop skips it, and so does the stored-id fallback, so the answer is
    ``None`` — standalone — rather than a link to ourselves.
    """
    from custom_components.adaptive_cover_pro.state.device_link import (
        resolve_linked_device,
    )

    acp_entry = _make_acp_entry(hass, "resolve_self", dict(VERTICAL_OPTIONS))
    dev_reg = dr.async_get(hass)
    ours = dev_reg.async_get_or_create(
        config_entry_id=acp_entry.entry_id,
        identifiers={("demo", "duplicate-of-ours")},
        name="Duplicate",
    )
    er.async_get(hass).async_get_or_create(
        "cover",
        DOMAIN,
        f"blind-{ours.id}",
        suggested_object_id="test_blind",
        config_entry=acp_entry,
        device_id=ours.id,
    )

    options = {CONF_DEVICE_ID: ours.id, CONF_ENTITIES: ["cover.test_blind"]}

    assert resolve_linked_device(hass, options, own_entry_id=acp_entry.entry_id) is None


@pytest.mark.asyncio
async def test_resolve_refuses_a_synthesized_composite_from_the_fallback(hass):
    """The stored-id fallback refuses a composite HA reconstructed for it.

    From HA 2026.9 ``async_get`` does not return ``None`` for a pre-migration
    id: it synthesizes a read-only composite whose own ``id`` is the composite
    id.  ``mirror_link`` would write that as ``via_device_id``; HA accepts it
    and then resolves it to a split, so the stored value never equals what we
    wrote — every reload rewrites it, logs a deprecation that breaks in 2027.8,
    and the area listener subscribes to an id no event ever names.

    A *genuine* restored composite is evolved from a split and carries that
    split's id in ``composite_device_id``, which is why the guard compares the
    two rather than merely testing the attribute for truthiness.  Reachable only
    when the cover-entity hop finds nothing, so this options dict carries no
    entities.
    """
    from custom_components.adaptive_cover_pro.state.device_link import (
        resolve_linked_device,
    )

    synthesized = SimpleNamespace(
        id="synth-composite",
        config_entries={"some_other_entry"},
        composite_device_id="synth-composite",
    )
    real_split = SimpleNamespace(
        id="live-split",
        config_entries={"some_other_entry"},
        composite_device_id="synth-composite",
    )
    views = {device.id: device for device in (synthesized, real_split)}

    def _async_get(self, device_id, **kwargs):
        return views.get(device_id)

    with patch.object(dr.DeviceRegistry, "async_get", _async_get):
        refused = resolve_linked_device(
            hass, {CONF_DEVICE_ID: "synth-composite"}, own_entry_id="acp_synth"
        )
        accepted = resolve_linked_device(
            hass, {CONF_DEVICE_ID: "live-split"}, own_entry_id="acp_synth"
        )

    assert refused is None
    assert accepted is real_split


# ---------------------------------------------------------------------------
# Legacy co-ownership helper — pre-v3 only
# ---------------------------------------------------------------------------
#
# ``add_config_entry_id`` is the repo's only remaining call site.  It builds the
# device shape a user carried before issue #1369: a physical device co-owned by
# a real integration *and* by Adaptive Cover Pro.  HA 2026.8's storage-v3
# registry cannot represent it at all, so every test that needs one is
# ``skipif``-gated rather than rewritten — the shape they characterise genuinely
# does not exist there.


async def _setup_acp_entry_coowning_device(
    hass,
    *,
    identifiers: set[tuple[str, str]],
    entry_id: str,
    hold_acp_entity: bool = False,
) -> tuple[MockConfigEntry, str]:
    """Co-own a foreign device with a fresh ACP entry, then set that entry up.

    The ACP entry's options carry no ``CONF_DEVICE_ID``.  ``hold_acp_entity``
    binds one of our entities to the co-owned device, which makes it satisfy
    every adoption guard *except* sole ownership — so a test built on it pins
    that guard and nothing else.  Returns the ACP entry and the device id.
    """
    owner = MockConfigEntry(domain="demo", entry_id=f"{entry_id}_owner")
    owner.add_to_hass(hass)

    acp_entry = _make_acp_entry(
        hass, entry_id, dict(VERTICAL_OPTIONS), title="Stale Link"
    )

    device_reg = dr.async_get(hass)
    device = device_reg.async_get_or_create(
        config_entry_id=owner.entry_id,
        identifiers=identifiers,
        name="Physical Cover",
    )
    device_reg.async_update_device(device.id, add_config_entry_id=acp_entry.entry_id)
    assert acp_entry.entry_id in device_reg.async_get(device.id).config_entries
    if hold_acp_entity:
        _bind_acp_entity(hass, acp_entry, device.id, "coowned_probe")

    _seed_cover_states(hass)

    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(acp_entry.entry_id)
        await hass.async_block_till_done()

    return acp_entry, device.id


@pytest.mark.skipif(
    HA_DEVICE_REGISTRY_V3,
    reason="a device cannot be co-owned under the v3 registry, so the leftover "
    "this characterises cannot be built there",
)
@pytest.mark.asyncio
async def test_stale_config_entry_link_removed_from_physical_device(hass):
    """Setup strips this entry's id off a physical device it does not identify.

    The device carries our config entry id but not our ``(DOMAIN, entry_id)``
    identifier — the leftover of a device association the user has since
    cleared.  Setting up with no ``CONF_DEVICE_ID`` must unlink it.
    """
    acp_entry, device_id = await _setup_acp_entry_coowning_device(
        hass,
        identifiers={("demo", "physical-cover-1")},
        entry_id="stale_link_removed",
    )

    device = dr.async_get(hass).async_get(device_id)
    assert acp_entry.entry_id not in device.config_entries


@pytest.mark.asyncio
async def test_own_virtual_device_link_preserved(hass):
    """Our own pre-existing service device survives setup untouched.

    The control for the test above, rewritten version-neutrally: a device that
    already carries ``(DOMAIN, entry_id)`` is ours, not a leftover, so setup must
    reuse its record rather than unlink it, rewrite it, or flag it as a
    duplicate.  Pins the identifier guard that separates "our device" from "a
    clone worth adopting".
    """
    acp_entry = _make_acp_entry(
        hass, "virtual_device_kept", dict(VERTICAL_OPTIONS), title="Kept"
    )
    dev_reg = dr.async_get(hass)
    existing = dev_reg.async_get_or_create(
        config_entry_id=acp_entry.entry_id,
        identifiers={(DOMAIN, acp_entry.entry_id)},
        name="Kept",
    )
    _bind_acp_entity(hass, acp_entry, existing.id, "kept_probe")

    _seed_cover_states(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(acp_entry.entry_id)
        await hass.async_block_till_done()

    own = _own_device(hass, acp_entry.entry_id)
    assert own is not None
    assert own.id == existing.id
    assert own.identifiers == {(DOMAIN, acp_entry.entry_id)}
    assert _duplicate_device_issues(hass, acp_entry.entry_id) == set()


# ---------------------------------------------------------------------------
# Adopting the duplicate device HA 2026.8+ already created (issue #1369)
# ---------------------------------------------------------------------------
#
# Every install that ran 2026.8+ against the pre-fix code is carrying a second,
# ACP-owned device record with the physical cover's identifiers, and all of this
# instance's entities have lived on it since August.  Setup renames that record
# into our service device *in place*, keeping its ``id`` — so entity bindings,
# dashboards and automations survive — and does so before the entity platform
# can mint a competing one.


async def _setup_with_acp_owned_device(
    hass,
    *,
    entry_id: str,
    leftover_identifiers: set[tuple[str, str]],
    entity_suffixes: tuple[str, ...] = (),
    disabled_suffixes: tuple[str, ...] = (),
    foreign_unique_ids: tuple[str, ...] = (),
    link: bool = True,
):
    """Set an ACP entry up with a pre-existing device of its own already in place.

    Builds the physical cover (owned by a ``demo`` entry), then a second device
    owned solely by the ACP entry carrying ``leftover_identifiers`` and whatever
    entities the caller asks for.  ``foreign_unique_ids`` puts entities belonging
    to a *helper* config entry on that same record — the shape a user creates by
    attaching a helper to the duplicate's card.  Returns
    ``(acp_entry, physical, leftover_id)``.
    """
    owner = MockConfigEntry(domain="demo", entry_id=f"{entry_id}_owner")
    owner.add_to_hass(hass)
    physical = _make_physical_device(
        hass, owner=owner, identifiers={("demo", f"physical-{entry_id}")}
    )

    options = dict(VERTICAL_OPTIONS)
    if link:
        options[CONF_DEVICE_ID] = physical.id
    acp_entry = _make_acp_entry(hass, entry_id, options)

    dev_reg = dr.async_get(hass)
    leftover = dev_reg.async_get_or_create(
        config_entry_id=acp_entry.entry_id,
        identifiers=leftover_identifiers,
        name="Duplicate Cover",
    )
    for suffix in entity_suffixes:
        _bind_acp_entity(hass, acp_entry, leftover.id, suffix)
    for suffix in disabled_suffixes:
        _bind_acp_entity(hass, acp_entry, leftover.id, suffix, disabled=True)
    if foreign_unique_ids:
        helper = MockConfigEntry(domain="input_number", entry_id=f"{entry_id}_helper")
        helper.add_to_hass(hass)
        for unique_id in foreign_unique_ids:
            _bind_foreign_entity(hass, helper, leftover.id, unique_id)

    _seed_cover_states(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(acp_entry.entry_id)
        await hass.async_block_till_done()

    return acp_entry, physical, leftover.id


@pytest.mark.asyncio
async def test_linked_setup_adopts_existing_clone_in_place(hass):
    """The duplicate HA already minted is renamed into our service device.

    Not removed and replaced: the record keeps its id, so everything already
    pointing at it keeps working.
    """
    acp_entry, physical, clone_id = await _setup_with_acp_owned_device(
        hass,
        entry_id="adopt_in_place",
        leftover_identifiers={("demo", "clone-marker-1")},
        entity_suffixes=("legacy_probe",),
    )

    adopted = dr.async_get(hass).async_get(clone_id)
    assert adopted is not None
    assert adopted.identifiers == {(DOMAIN, acp_entry.entry_id)}
    assert adopted.connections == set()
    assert adopted.via_device_id == physical.id
    assert len(_acp_devices(hass, acp_entry.entry_id)) == 1


@pytest.mark.asyncio
async def test_adopted_device_id_and_entity_bindings_survive_setup(hass):
    """The churn-free pin: no entity is re-homed and no id changes.

    A disabled entity is in the mix on purpose — the guard that decides whether
    a device still holds our entities has to look past
    ``include_disabled_entities``'s default, or a user who disabled everything on
    the duplicate would get it orphaned instead of adopted.
    """
    acp_entry, _physical, clone_id = await _setup_with_acp_owned_device(
        hass,
        entry_id="adopt_bindings",
        leftover_identifiers={("demo", "clone-marker-2")},
        entity_suffixes=("probe_enabled",),
        disabled_suffixes=("probe_disabled",),
    )
    ent_reg = er.async_get(hass)
    recorded = {
        ent_reg.async_get_entity_id("sensor", DOMAIN, f"{acp_entry.entry_id}_{suffix}")
        for suffix in ("probe_enabled", "probe_disabled")
    }
    assert None not in recorded

    assert dr.async_get(hass).async_get(clone_id) is not None
    for entity_id in recorded:
        assert ent_reg.async_get(entity_id).device_id == clone_id
    on_device = {
        entry.entity_id
        for entry in er.async_entries_for_device(
            ent_reg, clone_id, include_disabled_entities=True
        )
    }
    assert recorded <= on_device
    assert {device.id for device in _acp_devices(hass, acp_entry.entry_id)} == {
        clone_id
    }


@pytest.mark.skipif(
    HA_DEVICE_REGISTRY_V3,
    reason="a device cannot be co-owned under the v3 registry",
)
@pytest.mark.asyncio
async def test_adoption_refuses_a_coowned_physical_device(hass):
    """A device someone else also owns is never renamed, however clone-shaped.

    It holds our entities and carries foreign identifiers, so sole ownership is
    the only guard standing between it and adoption — and adoption would rename
    a real integration's device out from under it.
    """
    acp_entry, device_id = await _setup_acp_entry_coowning_device(
        hass,
        identifiers={("demo", "coowned-physical")},
        entry_id="coowned_refused",
        hold_acp_entity=True,
    )

    device = dr.async_get(hass).async_get(device_id)
    assert device.identifiers == {("demo", "coowned-physical")}
    assert device.name == "Physical Cover"

    own = _own_device(hass, acp_entry.entry_id)
    assert own is not None
    assert own.id != device_id


@pytest.mark.asyncio
async def test_adoption_refuses_a_device_holding_no_entities_of_ours(hass):
    """A leftover with no entities on it is repaired, not adopted.

    Adopting it would resurrect a record the user has nothing on; raising a
    fixable Repair lets them delete it, which is the only safe direction.
    """
    acp_entry, _physical, leftover_id = await _setup_with_acp_owned_device(
        hass,
        entry_id="empty_leftover",
        leftover_identifiers={("demo", "empty-leftover")},
    )

    leftover = dr.async_get(hass).async_get(leftover_id)
    assert leftover is not None
    assert leftover.identifiers == {("demo", "empty-leftover")}
    assert _duplicate_device_issues(hass, acp_entry.entry_id) == {
        duplicate_device_issue_id(acp_entry.entry_id, leftover_id)
    }


@pytest.mark.asyncio
async def test_clone_carrying_a_user_helper_is_still_adopted(hass):
    """A duplicate holding our entities *and* a user's helper is adopted anyway.

    HA helpers bind to a device, and the duplicate is where this instance's
    entities have lived since August, so it is a perfectly ordinary place for
    one to be attached.  "Every entity on it is ours" would disqualify the
    record, we would mint a fresh service device, every entity would be re-homed
    onto it — the id churn adoption exists to prevent — and the leftover would
    be left behind holding the helper.

    Adoption rewrites identifiers and nothing else, so the helper keeps riding
    on the same record it always did, and no Repair is raised: this device is
    ours now, not a leftover to offer the user a delete button for.
    """
    acp_entry, physical, clone_id = await _setup_with_acp_owned_device(
        hass,
        entry_id="adopt_with_helper",
        leftover_identifiers={("demo", "clone-with-helper")},
        entity_suffixes=("legacy_probe",),
        foreign_unique_ids=("user_helper_on_clone",),
    )

    adopted = dr.async_get(hass).async_get(clone_id)
    assert adopted.identifiers == {(DOMAIN, acp_entry.entry_id)}
    assert adopted.via_device_id == physical.id
    assert len(_acp_devices(hass, acp_entry.entry_id)) == 1

    ent_reg = er.async_get(hass)
    helper_id = ent_reg.async_get_entity_id(
        "sensor", "input_number", "user_helper_on_clone"
    )
    assert helper_id is not None
    assert ent_reg.async_get(helper_id).device_id == clone_id
    assert {
        entity.device_id
        for entity in er.async_entries_for_config_entry(ent_reg, acp_entry.entry_id)
    } == {clone_id}
    assert _duplicate_device_issues(hass, acp_entry.entry_id) == set()


@pytest.mark.asyncio
async def test_leftover_holding_only_foreign_entities_is_left_alone(hass):
    """A device of ours carrying only somebody else's entities is not touched.

    The third classification, and the one with no safe action: adopting it would
    rename a card the user sees as their helper's, and removing it would take
    that helper's registry row with it — ``async_remove_device`` deletes the
    entities of the removed device's config entries.  So setup leaves it exactly
    where it is and raises **no** Repair, because the only fix a Repair could
    offer is the removal we just ruled out.  The scan still reports it, so the
    state is visible rather than silently dropped on the floor.
    """
    from custom_components.adaptive_cover_pro.state.device_link import scan_own_devices

    acp_entry, _physical, leftover_id = await _setup_with_acp_owned_device(
        hass,
        entry_id="entangled_leftover",
        leftover_identifiers={("demo", "entangled-marker")},
        foreign_unique_ids=("orphan_helper",),
    )

    leftover = dr.async_get(hass).async_get(leftover_id)
    assert leftover is not None
    assert leftover.identifiers == {("demo", "entangled-marker")}
    assert _duplicate_device_issues(hass, acp_entry.entry_id) == set()

    ent_reg = er.async_get(hass)
    helper_id = ent_reg.async_get_entity_id("sensor", "input_number", "orphan_helper")
    assert ent_reg.async_get(helper_id).device_id == leftover_id

    scan = scan_own_devices(hass, acp_entry)
    assert [device.id for device in scan.entangled] == [leftover_id]
    assert scan.strays == ()
    assert scan.clones == ()
    assert scan.own is not None and scan.own.id != leftover_id


@pytest.mark.asyncio
async def test_adoption_skipped_when_own_device_already_exists(hass):
    """With our device already present, an extra one is a Repair, not a rename.

    Renaming a second record to ``(DOMAIN, entry_id)`` would collide with the one
    we already own, so the ambiguous case writes nothing and asks the user.
    """
    acp_entry, physical = await _setup_acp_entry_owning_device(
        hass,
        entry_id="own_plus_clone",
        physical_identifiers={("demo", "physical-own-plus")},
    )
    own_id = _own_device(hass, acp_entry.entry_id).id
    ent_reg = er.async_get(hass)
    victim = er.async_entries_for_config_entry(ent_reg, acp_entry.entry_id)[0]

    await hass.config_entries.async_unload(acp_entry.entry_id)
    await hass.async_block_till_done()

    dev_reg = dr.async_get(hass)
    ex_clone = dev_reg.async_get_or_create(
        config_entry_id=acp_entry.entry_id,
        identifiers={("demo", "ex-clone")},
        name="Ex Clone",
    )
    ent_reg.async_update_entity(victim.entity_id, device_id=ex_clone.id)

    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(acp_entry.entry_id)
        await hass.async_block_till_done()

    assert acp_entry.state is ConfigEntryState.LOADED
    assert dev_reg.async_get(ex_clone.id).identifiers == {("demo", "ex-clone")}
    assert _own_device(hass, acp_entry.entry_id).id == own_id
    assert {
        entity.device_id
        for entity in er.async_entries_for_config_entry(ent_reg, acp_entry.entry_id)
    } == {own_id}
    assert _duplicate_device_issues(hass, acp_entry.entry_id) == {
        duplicate_device_issue_id(acp_entry.entry_id, ex_clone.id)
    }
    assert physical.id != ex_clone.id


@pytest.mark.asyncio
async def test_adoption_ignores_a_device_owned_by_another_entry(hass):
    """Another ACP instance's duplicate is none of this instance's business.

    Multi-instance installs are the norm here — sixteen covers on one house —
    so "clone-shaped" has to mean "clone-shaped *and ours*".
    """
    acp_entry, _physical = await _setup_acp_entry_owning_device(
        hass,
        entry_id="ignores_neighbour",
        physical_identifiers={("demo", "physical-neighbour")},
    )

    # Registered after the first setup so HA does not set the neighbour up as
    # part of loading the component — this test is about what *our* entry's
    # setup does to someone else's device, not about the neighbour's own run.
    neighbour = _make_acp_entry(
        hass, "neighbour_entry", dict(VERTICAL_OPTIONS), title="Neighbour"
    )
    dev_reg = dr.async_get(hass)
    neighbour_device = dev_reg.async_get_or_create(
        config_entry_id=neighbour.entry_id,
        identifiers={("demo", "neighbour-clone")},
        name="Neighbour Duplicate",
    )
    _bind_acp_entity(hass, neighbour, neighbour_device.id, "neighbour_probe")

    with _patch_coordinator_refresh():
        await hass.config_entries.async_reload(acp_entry.entry_id)
        await hass.async_block_till_done()

    assert dev_reg.async_get(neighbour_device.id).identifiers == {
        ("demo", "neighbour-clone")
    }
    assert {device.id for device in _acp_devices(hass, acp_entry.entry_id)} == {
        _own_device(hass, acp_entry.entry_id).id
    }
    assert _duplicate_device_issues(hass, acp_entry.entry_id) == set()


@pytest.mark.asyncio
async def test_adoption_is_idempotent_across_reload(hass):
    """Reloading an adopted instance is a no-op, not a second rename.

    After adoption the record carries our identifier, so the next scan classifies
    it as ours and the whole path short-circuits.
    """
    acp_entry, _physical, clone_id = await _setup_with_acp_owned_device(
        hass,
        entry_id="adopt_idempotent",
        leftover_identifiers={("demo", "clone-marker-3")},
        entity_suffixes=("legacy_probe",),
    )

    with _patch_coordinator_refresh():
        await hass.config_entries.async_reload(acp_entry.entry_id)
        await hass.async_block_till_done()

    adopted = dr.async_get(hass).async_get(clone_id)
    assert adopted.identifiers == {(DOMAIN, acp_entry.entry_id)}
    assert len(_acp_devices(hass, acp_entry.entry_id)) == 1
    assert _duplicate_device_issues(hass, acp_entry.entry_id) == set()


@pytest.mark.asyncio
async def test_repair_cleared_when_leftover_gone(hass):
    """Deleting the leftover clears its Repair on the next load.

    Without the sweep the warning would outlive the thing it warns about until
    an HA restart.
    """
    acp_entry, _physical, leftover_id = await _setup_with_acp_owned_device(
        hass,
        entry_id="repair_cleared",
        leftover_identifiers={("demo", "cleared-leftover")},
    )
    assert _duplicate_device_issues(hass, acp_entry.entry_id)

    dr.async_get(hass).async_remove_device(leftover_id)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_reload(acp_entry.entry_id)
        await hass.async_block_till_done()

    assert _duplicate_device_issues(hass, acp_entry.entry_id) == set()


@pytest.mark.asyncio
async def test_repair_cleared_when_leftover_reclassifies(hass):
    """A leftover that gains somebody else's entity loses its Repair, not itself.

    The other half of the sweep, and the one no deletion covers: the device is
    still there, still ours, still carrying foreign identifiers — only its
    *role* moved, from ``STRAY`` (safe to delete, so worth a fixable Repair) to
    ``ENTANGLED`` (removing it would take the helper's registry row with it, so
    no Repair can be offered).  Raising nothing for it on the next load is not
    enough on its own: the issue registry is persisted, so the Repair raised
    before the helper was attached would sit there offering a delete button the
    flow now refuses.  The prefix sweep is what drops it.
    """
    from custom_components.adaptive_cover_pro.state.device_link import scan_own_devices

    acp_entry, _physical, leftover_id = await _setup_with_acp_owned_device(
        hass,
        entry_id="repair_reclassified",
        leftover_identifiers={("demo", "reclassified-leftover")},
    )
    assert _duplicate_device_issues(hass, acp_entry.entry_id) == {
        duplicate_device_issue_id(acp_entry.entry_id, leftover_id)
    }

    helper = MockConfigEntry(
        domain="input_number", entry_id="repair_reclassified_helper"
    )
    helper.add_to_hass(hass)
    _bind_foreign_entity(hass, helper, leftover_id, "helper_on_reclassified")

    with _patch_coordinator_refresh():
        await hass.config_entries.async_reload(acp_entry.entry_id)
        await hass.async_block_till_done()

    assert _duplicate_device_issues(hass, acp_entry.entry_id) == set()

    leftover = dr.async_get(hass).async_get(leftover_id)
    assert leftover is not None
    assert leftover.identifiers == {("demo", "reclassified-leftover")}

    ent_reg = er.async_get(hass)
    helper_id = ent_reg.async_get_entity_id(
        "sensor", "input_number", "helper_on_reclassified"
    )
    assert helper_id is not None
    assert ent_reg.async_get(helper_id).device_id == leftover_id

    scan = scan_own_devices(hass, acp_entry)
    assert [device.id for device in scan.entangled] == [leftover_id]
    assert scan.strays == ()


@pytest.mark.asyncio
async def test_scan_classifies_a_coowned_device_as_shared(hass):
    """Sole ownership is checked before anything else, on every HA version.

    The integration test that pins this can only run below HA 2026.8, because a
    co-owned device cannot exist above it.  This one hands the scan a
    hand-built co-owned entry instead, so the guard stays pinned on the CI leg
    where the real shape is unconstructible.

    The stand-in is a ``SimpleNamespace`` and not a real ``DeviceEntry`` for
    exactly that reason: from HA 2026.8 ``config_entries`` is a read-only
    property derived from the stored single ``config_entry_id``, so passing it
    as a constructor kwarg is a ``TypeError`` — on the very CI leg this test
    exists to cover.  ``scan_own_devices`` reads three attributes off a device
    (``config_entries``, ``identifiers``, ``id``) and this carries all three.
    """
    from custom_components.adaptive_cover_pro.state.device_link import scan_own_devices

    acp_entry = _make_acp_entry(hass, "scan_shared", dict(VERTICAL_OPTIONS))
    coowned = SimpleNamespace(
        id="coowned-device",
        config_entries={acp_entry.entry_id, "another_entry"},
        identifiers={("demo", "coowned-1")},
    )

    with patch.object(dr, "async_entries_for_config_entry", return_value=[coowned]):
        scan = scan_own_devices(hass, acp_entry)

    assert scan.shared == (coowned,)
    assert scan.own is None
    assert scan.clones == ()
    assert scan.strays == ()


@pytest.mark.asyncio
async def test_mirror_clears_via_when_physical_device_is_gone(hass):
    """A stored link to a device that no longer exists is written as ``None``.

    From HA 2026.8 ``async_update_device`` *raises* for an unknown via device, so
    passing the stale id through would fail ``async_setup_entry`` outright rather
    than log — the user would lose the whole instance over a deleted blind.
    """
    from custom_components.adaptive_cover_pro.state.device_link import mirror_link

    acp_entry = _make_acp_entry(hass, "mirror_gone", dict(VERTICAL_OPTIONS))
    owner = MockConfigEntry(domain="demo", entry_id="mirror_gone_owner")
    owner.add_to_hass(hass)

    dev_reg = dr.async_get(hass)
    own = dev_reg.async_get_or_create(
        config_entry_id=acp_entry.entry_id,
        identifiers={(DOMAIN, acp_entry.entry_id)},
    )
    physical = dev_reg.async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("demo", "gone-1")}
    )

    mirror_link(hass, own.id, physical.id)
    assert dev_reg.async_get(own.id).via_device_id == physical.id

    mirror_link(hass, own.id, "a-device-id-that-never-existed")
    assert dev_reg.async_get(own.id).via_device_id is None


@pytest.mark.asyncio
async def test_mirror_never_targets_our_own_device_id(hass):
    """We never become our own via device.

    A stale ``CONF_DEVICE_ID`` that resolves back to a duplicate of our own
    record is exactly how that happens; HA 2026.8 raises on it.
    """
    from custom_components.adaptive_cover_pro.state.device_link import mirror_link

    acp_entry = _make_acp_entry(hass, "mirror_self", dict(VERTICAL_OPTIONS))
    dev_reg = dr.async_get(hass)
    own = dev_reg.async_get_or_create(
        config_entry_id=acp_entry.entry_id,
        identifiers={(DOMAIN, acp_entry.entry_id)},
    )

    mirror_link(hass, own.id, own.id)

    assert dev_reg.async_get(own.id).via_device_id is None


@pytest.mark.asyncio
async def test_mirror_is_a_no_op_when_our_own_device_is_gone(hass):
    """Deleting our service device must not make the area mirror raise.

    ``mirror_link`` is also driven by the physical device's registry-updated
    callback, which holds an ``own_id`` captured back at setup.  A user who
    deletes our card from the UI and then moves the physical cover between areas
    would otherwise get an exception out of a registry event handler — and the
    physical device is not ours to write to on the way past.
    """
    from custom_components.adaptive_cover_pro.state.device_link import mirror_link

    owner = MockConfigEntry(domain="demo", entry_id="mirror_own_gone_owner")
    owner.add_to_hass(hass)
    physical = _make_physical_device(
        hass,
        owner=owner,
        identifiers={("demo", "own-gone-1")},
        bind_cover_entity=False,
    )

    mirror_link(hass, "a-device-id-that-never-existed", physical.id)

    assert dr.async_get(hass).async_get(physical.id).via_device_id is None


@pytest.mark.asyncio
async def test_reconcile_hands_back_a_no_op_unsub_with_no_service_device(hass):
    """An entry that owns no service device reconciles to a no-op unsubscribe.

    The return value goes straight to ``entry.async_on_unload``, so "nothing to
    watch" has to be a callable rather than ``None`` — and the pass must not
    reach for ``own.id`` on the way there.
    """
    from custom_components.adaptive_cover_pro.state.device_link import (
        async_reconcile_device_link,
    )

    owner = MockConfigEntry(domain="demo", entry_id="reconcile_bare_owner")
    owner.add_to_hass(hass)
    physical = _make_physical_device(
        hass, owner=owner, identifiers={("demo", "reconcile-bare")}
    )
    acp_entry = _make_acp_entry(
        hass, "reconcile_bare", {**VERTICAL_OPTIONS, CONF_DEVICE_ID: physical.id}
    )

    unsub = await async_reconcile_device_link(hass, acp_entry)

    assert _own_device(hass, acp_entry.entry_id) is None
    assert unsub() is None
