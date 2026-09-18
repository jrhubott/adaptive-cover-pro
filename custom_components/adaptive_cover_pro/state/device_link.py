"""Adaptive Cover Pro's own service device, and its link to the physical cover.

Part of the ``state/`` boundary: every device-registry read and write that is
*about the integration's own device record* lives here, beside
:mod:`.area_resolver`'s device<->area hop, so no caller outside ``state/``
touches the device registry directly.

Why this module exists (issue #1369)
------------------------------------
Adaptive Cover Pro used to express "this cover instance belongs to that physical
blind" by handing the physical device's own ``identifiers`` back to Home
Assistant from ``entity_base.device_info``.  Before HA 2026.8 the device
registry unioned the two config entries onto a single record and the cover's
entities appeared on the physical device's card.  HA 2026.8 moved the registry
to storage v3, where **a device belongs to exactly one config entry** and
``async_get_or_create``'s lookup is scoped by ``config_entry_id``: the same
identifiers registered under a second entry no longer find the first device,
they mint a *second* one.  Users on 2026.8+ have had two cards ever since.

The replacement is the one cross-entry link HA explicitly sanctions: we own our
own ``DeviceEntryType.SERVICE`` device and point it at the physical one with
``via_device_id``, mirroring the physical device's ``area_id`` so the card still
lands in the right room.  Child devices are ruled out (a child must share its
parent's config entry) and so is ``new_config_entry_id`` (it *moves* a device
rather than sharing it) — either would have us seize the physical record.

Why the via link is written here and not in ``device_info``
-----------------------------------------------------------
``DeviceInfo`` spells the link differently on the two HA generations — it has
``via_device`` before 2026.8 and ``via_device_id`` after, and each version
rejects the other's spelling — whereas ``async_update_device(via_device_id=...)``
is spelled identically on both.  So ``device_info`` stays version-neutral and
carries no via key at all, and one reconciliation pass writes the link after
platform setup.  No capability probe, one code path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_device_registry_updated_event

from ..const import (
    CONF_DEVICE_ID,
    CONF_ENTITIES,
    DOMAIN,
    ISSUE_DUPLICATE_DEVICE,
    _LOGGER,
)
from .area_resolver import device_area_id

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from typing import Any

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry


@callback
def _noop() -> None:
    """Unsubscribe placeholder for a reconciliation that registered no listener."""


@callback
def devices_for_entities(
    hass: HomeAssistant, entity_ids: Iterable[str]
) -> dict[str, DeviceEntry]:
    """Return ``{device_id: device}`` for the devices behind *entity_ids*.

    The integration's single entity → device hop.  Both the options flow's
    device picker and :func:`resolve_linked_device` need it, and two copies of
    it is how one HA registry change breaks two call sites at once.
    Insertion-ordered and de-duplicated: several cover entities commonly share
    one motor.  Fail-open — an unknown entity, an entity with no device, or a
    device that has since been deleted is skipped rather than raising.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    devices: dict[str, DeviceEntry] = {}
    for entity_id in entity_ids:
        entity = ent_reg.async_get(entity_id)
        if entity is None or not entity.device_id or entity.device_id in devices:
            continue
        device = dev_reg.async_get(entity.device_id)
        if device is not None:
            devices[entity.device_id] = device
    return devices


@callback
def resolve_linked_device(
    hass: HomeAssistant,
    options: Mapping[str, Any],
    *,
    own_entry_id: str,
) -> DeviceEntry | None:
    """Return the physical device this instance is linked to, or ``None``.

    The stored ``CONF_DEVICE_ID`` is a user's months-old pick and is not assumed
    to still name a live record: HA can split one device into several and keep
    the original id on the *composite*, which owns no entities.  So the live
    cover entities are consulted first — a bound entity's device is the truth —
    and the stored id is accepted either as that device's own id or as its
    composite's.  ``composite_device_id`` is read defensively because the
    attribute does not exist below HA 2026.9.

    Only the stored id is left as a fallback, and any candidate our *own* config
    entry owns is refused: a leftover duplicate of our service device would
    otherwise be resolved as "the physical cover" and we would link ourselves to
    ourselves.  Fail-open: anything unresolvable yields ``None``, which reads as
    "standalone".
    """
    wanted = options.get(CONF_DEVICE_ID)
    if not wanted:
        return None

    for device in devices_for_entities(hass, options.get(CONF_ENTITIES) or []).values():
        if own_entry_id in device.config_entries:
            continue
        if wanted in (device.id, getattr(device, "composite_device_id", None)):
            return device

    device = dr.async_get(hass).async_get(wanted)
    if device is not None and own_entry_id not in device.config_entries:
        return device
    return None


@dataclass(frozen=True, slots=True)
class OwnDeviceScan:
    """How every device this config entry owns is classified.

    ``own``    — carries ``(DOMAIN, entry_id)``; the service device, if it exists.
    ``clones`` — sole-owned, foreign identifiers, holding only our entities.
                 Adoptable: this is the duplicate HA 2026.8+ minted.
    ``strays`` — sole-owned, foreign identifiers, holding none of our entities.
                 A leftover the user should be offered a way to delete.
    ``shared`` — co-owned with another config entry.  Only representable below
                 HA 2026.8, and never adopted whatever else it looks like.
    """

    own: DeviceEntry | None
    clones: tuple[DeviceEntry, ...]
    strays: tuple[DeviceEntry, ...]
    shared: tuple[DeviceEntry, ...]


@callback
def scan_own_devices(hass: HomeAssistant, entry: ConfigEntry) -> OwnDeviceScan:
    """Partition every device this config entry owns.

    One pass over the registry's config-entry index, one entity lookup per
    device — and no identifier lookup, deliberately.
    ``async_get_device(identifiers=...)`` is deprecated from HA 2026.8 (ERROR for
    core and core-integration frames, i.e. a hard ``RuntimeError`` from a test
    frame) and its v3 replacement does not exist on the 2026.3 floor this
    integration supports; matching identifiers in Python over an index read is
    the one spelling that is neither deprecated nor version-specific.  It also
    means ``scan.own`` is the only way anything here asks "which device is ours".

    The guards are ordered so the dangerous one is unreachable first:

    1. **Sole ownership.**  A device another config entry also owns is
       ``shared`` and stops there — adoption would rename a real integration's
       record out from under it.
    2. **Our identifier.**  Already ours, so it is ``own``, not a candidate.
    3. **Holds only our entities.**  ``include_disabled_entities=True`` because a
       user who disabled everything on the duplicate still has it as the device
       their entities live on.  Anything else is a ``stray``.
    """
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    entry_id = entry.entry_id

    own: DeviceEntry | None = None
    clones: list[DeviceEntry] = []
    strays: list[DeviceEntry] = []
    shared: list[DeviceEntry] = []

    for device in dr.async_entries_for_config_entry(dev_reg, entry_id):
        if device.config_entries != {entry_id}:
            shared.append(device)
            continue
        if (DOMAIN, entry_id) in device.identifiers:
            own = device
            continue
        entities = er.async_entries_for_device(
            ent_reg, device.id, include_disabled_entities=True
        )
        if entities and all(entity.config_entry_id == entry_id for entity in entities):
            clones.append(device)
        else:
            strays.append(device)

    return OwnDeviceScan(own, tuple(clones), tuple(strays), tuple(shared))


@callback
def adopt_clone_device(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Rename this entry's duplicate device into its service device, in place.

    Must run *before* ``async_forward_entry_setups``: after it, the entity
    platform has already created a competing service device and re-pointed every
    entity at it, and the duplicate is an empty husk.

    Renaming rather than deleting-and-recreating is the whole point — the record
    keeps its ``id``, so dashboards, automations, entity bindings and the
    user's own device-name override all survive an upgrade that would otherwise
    look like every entity moved house.

    Deliberately **not** conditional on ``CONF_DEVICE_ID``.  A user who linked a
    device, ran 2026.8+, then unlinked it would otherwise reach the stale-link
    sweep with a duplicate that is the last config entry on the record — and
    ``remove_config_entry_id`` deletes such a device outright, which takes every
    entity on it with it.  Adoption reclaims the record before the sweep sees it.

    Adopts only when there is no service device yet and exactly one candidate:
    with two, "which one is ours" is a question only the user can answer, and
    the post-forward sweep raises a Repair instead.  Returns the adopted device
    id, or ``None`` when nothing was written.
    """
    scan = scan_own_devices(hass, entry)
    if scan.own is not None or len(scan.clones) != 1:
        return None

    clone = scan.clones[0]
    try:
        dr.async_get(hass).async_update_device(
            clone.id,
            new_identifiers={(DOMAIN, entry.entry_id)},
            new_connections=set(),
        )
    except (
        dr.DeviceCollisionError
    ):  # pragma: no cover — HA-internal collision the G4 pre-check already excludes
        _LOGGER.warning(
            "Could not adopt duplicate device %s for %s: its identifiers collide "
            "with another device. The duplicate will be reported as a repairable "
            "issue instead",
            clone.id,
            entry.entry_id,
        )
        return None

    _LOGGER.debug(
        "Adopted duplicate device %s as the service device for %s",
        clone.id,
        entry.entry_id,
    )
    return clone.id


@callback
def clear_duplicate_device_issues(
    hass: HomeAssistant, entry_id: str, *, keep: frozenset[str] = frozenset()
) -> None:
    """Delete this entry's duplicate-device Repairs except the ones in *keep*.

    A prefix sweep rather than per-issue bookkeeping, because the primary fix is
    *deleting the device*, which reloads nothing and leaves no in-memory key
    behind: without this the warning outlives the thing it warns about until an
    HA restart.  Also called when the config entry itself is removed.
    """
    registry = ir.async_get(hass)
    prefix = f"{ISSUE_DUPLICATE_DEVICE}_{entry_id}_"
    for reg_domain, issue_id in list(registry.issues):
        if (
            reg_domain == DOMAIN
            and issue_id.startswith(prefix)
            and issue_id not in keep
        ):
            ir.async_delete_issue(hass, DOMAIN, issue_id)


@callback
def remove_duplicate_device(hass: HomeAssistant, entry_id: str, device_id: str) -> bool:
    """Delete a leftover duplicate device, or report why it was refused.

    Every condition is re-checked against the live registry rather than trusted
    from the Repair's stored payload, because the payload was written at setup
    and the user has had every chance to change things since.  The entity check
    is the one that matters: ``async_remove_device`` removes every entity
    belonging to the removed device's config entries along with it, so
    confirming a stale Repair on a device that has since acquired entities would
    silently delete the user's sensors.

    Returns ``True`` only when the device was actually removed.
    """
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if device is None:
        return False
    if (DOMAIN, entry_id) in device.identifiers:
        return False
    if device.config_entries != {entry_id}:
        return False
    if er.async_entries_for_device(
        er.async_get(hass), device_id, include_disabled_entities=True
    ):
        return False

    dev_reg.async_remove_device(device_id)
    return True


@callback
def mirror_link(hass: HomeAssistant, own_id: str, physical_id: str | None) -> None:
    """Point our service device at *physical_id* and mirror its area.

    The single writer of the link — setup and the physical device's
    registry-updated callback both come through here, so "what the link should
    be" is stated once.

    ``via_device_id`` is written as ``None`` whenever the physical device does
    not resolve or would be our own device.  That is not defensive padding: from
    HA 2026.8 ``async_update_device`` *raises* ``HomeAssistantError`` for an
    unknown via device and for a self-reference, so a user whose stored link
    points at a since-deleted device would get a failed ``async_setup_entry``
    rather than a log line.

    The area is read through :func:`.area_resolver.device_area_id` rather than
    off ``.area_id`` here, so the device → area hop stays in one place.  With no
    physical device the area is left alone: it is a placement the user may have
    chosen, and only the link we created is ours to withdraw.  Values that
    already match are not rewritten, so a reload fires no registry event and the
    area-mirror callback cannot ping-pong.
    """
    dev_reg = dr.async_get(hass)
    own = dev_reg.async_get(own_id)
    if own is None:
        return

    physical = dev_reg.async_get(physical_id) if physical_id else None
    via = physical.id if physical is not None and physical.id != own_id else None

    changes: dict[str, Any] = {}
    if own.via_device_id != via:
        changes["via_device_id"] = via
    if via is not None:
        area_id = device_area_id(hass, via)
        if own.area_id != area_id:
            changes["area_id"] = area_id
    if changes:
        dev_reg.async_update_device(own_id, **changes)


@callback
def _reconcile_stale_devices(hass: HomeAssistant, entry: ConfigEntry) -> OwnDeviceScan:
    """Clean up after the old device model, and report what cannot be cleaned.

    Runs on a scan taken *after* platform setup, so our entities are already on
    our own service device and nothing below can strand one.  Returns that scan
    so the caller does not take a second one.
    """
    dev_reg = dr.async_get(hass)
    scan = scan_own_devices(hass, entry)

    for device in scan.shared:
        # Pre-v3 only, with no version check and none wanted: from HA 2026.8 a
        # device belongs to exactly one config entry, so ``config_entries`` is
        # always ``{entry_id}``, ``shared`` is always empty, and the guard on
        # ``remove_config_entry_id`` (ERROR for core and core-integration
        # frames) is never reached.  Do not "simplify" this into a version
        # check, and do not reach for ``new_config_entry_id``: that MOVES the
        # device to us rather than releasing it.
        _LOGGER.debug(
            "Removing stale config entry link from physical device %s", device.id
        )
        dev_reg.async_update_device(device.id, remove_config_entry_id=entry.entry_id)

    desired: set[str] = set()
    for device in scan.strays:
        issue_id = f"{ISSUE_DUPLICATE_DEVICE}_{entry.entry_id}_{device.id}"
        desired.add(issue_id)
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_DUPLICATE_DEVICE,
            translation_placeholders={
                "name": entry.title,
                "device_name": device.name_by_user or device.name or device.id,
            },
            data={"entry_id": entry.entry_id, "device_id": device.id},
        )

    clear_duplicate_device_issues(hass, entry.entry_id, keep=frozenset(desired))
    return scan


async def async_reconcile_device_link(
    hass: HomeAssistant, entry: ConfigEntry
) -> CALLBACK_TYPE:
    """Reconcile our service device against the physical cover, and keep it so.

    Runs once after ``async_forward_entry_setups``, when the entity platform has
    already created (or re-found) our service device.  Returns the unsubscribe
    for the physical device's registry-updated listener — a no-op callable when
    there is nothing to watch — so the caller can hand it straight to
    ``entry.async_on_unload``.
    """
    own = _reconcile_stale_devices(hass, entry).own
    if own is None:
        return _noop

    physical = resolve_linked_device(hass, entry.options, own_entry_id=entry.entry_id)
    mirror_link(hass, own.id, physical.id if physical is not None else None)
    if physical is None:
        return _noop

    own_id = own.id
    physical_id = physical.id

    @callback
    def _physical_device_changed(event: Event) -> None:
        """Re-mirror when the physical device moves between areas."""
        data = event.data
        if data["action"] == "update" and "area_id" in data["changes"]:
            mirror_link(hass, own_id, physical_id)

    return async_track_device_registry_updated_event(
        hass, physical_id, _physical_device_changed
    )
