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
from enum import Enum, auto
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
    ISSUE_DATA_DEVICE_ID,
    ISSUE_DATA_ENTRY_ID,
    ISSUE_DUPLICATE_DEVICE,
    _LOGGER,
    duplicate_device_issue_id,
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
def device_config_entry_ids(device: DeviceEntry) -> set[str]:
    """Return the config entry ids that own *device*, on either registry model.

    The one place anything in this integration asks a device who owns it.
    Below HA 2026.8 a device genuinely belongs to several config entries and
    ``DeviceEntry.config_entries`` is the stored set.  From 2026.8 it belongs to
    exactly one, ``config_entry_id`` is the stored field, and ``config_entries``
    survives only as a compatibility property HA *reports*: ``ERROR`` for core
    and core-integration frames — a ``RuntimeError`` out of a bare test frame —
    and a deprecation log line for ours, removed outright in 2027.10.
    ``config_entry_id`` does not exist on the HA 2026.3 floor ``hacs.json``
    declares, so no single spelling works on both and the capability is probed.

    Probed with ``hasattr`` rather than by testing the value, so the deprecated
    property is never reached on a registry that reports it — a falsy-value gate
    would fall through to it on exactly the version where that raises.  The
    value cannot actually be absent here: ``config_entry_id`` is a mandatory
    ``str`` on a live ``DeviceEntry``, and only ``DeletedDeviceEntry`` (which
    nothing in this integration handles) may carry ``None``.  The empty set is
    what HA's own compatibility property answers for that one, so it is what
    this answers too.

    Faithful on both models.  Pre-v3 every owner is kept, which is what
    :func:`classify_own_device`'s sole-ownership comparison is written against;
    on v3 the answer is the single owner.  The one v3 device whose
    ``config_entries`` is *wider* than ``{config_entry_id}`` is the read-only
    composite ``async_get`` synthesizes for a pre-migration id, which reports
    the union of its splits (and is exempt from the deprecation report for that
    reason).  It is out of reach here: registry enumeration never yields one,
    and the v3 migration re-points every entity's ``device_id`` off one, so the
    only call site that can be handed one is
    :func:`resolve_linked_device`'s stored-id fallback — which refuses it one
    clause later on ``composite_device_id`` either way.
    """
    if hasattr(device, "config_entry_id"):
        entry_id = device.config_entry_id
        return {entry_id} if entry_id else set()
    return set(device.config_entries)


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

    Only the stored id is left as a fallback, and it refuses two things.  Any
    candidate our *own* config entry owns: a leftover duplicate of our service
    device would otherwise be resolved as "the physical cover" and we would link
    ourselves to ourselves.  And a **synthesized composite** — from HA 2026.9
    ``async_get`` reconstructs a read-only composite for a pre-migration id
    rather than returning nothing, and such a stand-in reports its own id as its
    ``composite_device_id`` (a genuine restored composite is evolved from a
    split and carries the split's id there).  Writing one as ``via_device_id``
    is accepted and then resolved to a split, so the stored value never equals
    what we wrote: every reload rewrites it, logs a deprecation, and the area
    listener ends up subscribed to an id that never fires.

    Fail-open: anything unresolvable yields ``None``, which reads as
    "standalone".
    """
    wanted = options.get(CONF_DEVICE_ID)
    if not wanted:
        return None

    for device in devices_for_entities(hass, options.get(CONF_ENTITIES) or []).values():
        if own_entry_id in device_config_entry_ids(device):
            continue
        if wanted in (device.id, getattr(device, "composite_device_id", None)):
            return device

    device = dr.async_get(hass).async_get(wanted)
    if (
        device is not None
        and own_entry_id not in device_config_entry_ids(device)
        and getattr(device, "composite_device_id", None) != device.id
    ):
        return device
    return None


class OwnDeviceRole(Enum):
    """What one device is to the config entry the registry associates it with.

    The module's whole vocabulary for "is this record safe to rename, safe to
    delete, or neither".  Both the setup-time scan and the Repair flow's removal
    guard decide through :func:`classify_own_device`, so what we *offer* to
    delete and what we will *actually* delete cannot drift apart — which is
    exactly what happened when they were two hand-mirrored guard trios.
    """

    # Not owned solely by this entry: co-owned with a real integration (only
    # representable below HA 2026.8) or another entry's device entirely.  Never
    # adopted, never removed — either would seize somebody else's record.
    SHARED = auto()
    # Carries ``(DOMAIN, entry_id)``: the service device itself.
    OWN = auto()
    # Sole-owned, foreign identifiers, and at least one of OUR entities lives on
    # it.  The duplicate HA 2026.8+ minted — adopt it in place.
    CLONE = auto()
    # Sole-owned, foreign identifiers, holding no entities at all.  The only
    # shape that is safe to delete, so the only one offered as a fixable Repair.
    STRAY = auto()
    # Sole-owned, foreign identifiers, holding entities — none of them ours.  A
    # user's helper on a card we own, say.  Adopting it would rename a device
    # the user thinks of as their helper's; removing it would delete that
    # helper's registry row, because ``async_remove_device`` takes the entities
    # of the removed device's config entries with it.  Neither is safe, so it is
    # logged and left exactly where it is — and gets NO Repair, because the only
    # fix a Repair could offer is the removal just ruled out.
    ENTANGLED = auto()


@dataclass(frozen=True, slots=True)
class OwnDeviceScan:
    """Every device this config entry owns, bucketed by :class:`OwnDeviceRole`.

    ``own`` is singular because ``(DOMAIN, entry_id)`` identifies at most one
    record; the rest are tuples because an install can carry several.
    """

    own: DeviceEntry | None
    clones: tuple[DeviceEntry, ...]
    strays: tuple[DeviceEntry, ...]
    entangled: tuple[DeviceEntry, ...]
    shared: tuple[DeviceEntry, ...]


@callback
def classify_own_device(
    hass: HomeAssistant, entry_id: str, device: DeviceEntry
) -> OwnDeviceRole:
    """Say what *device* is to the config entry *entry_id*.

    The single predicate behind both :func:`scan_own_devices` and
    :func:`remove_duplicate_device`.  The clauses are ordered so the dangerous
    answer is unreachable first: a device this entry does not solely own stops
    at ``SHARED`` before anything looks at identifiers or entities.

    No identifier *lookup*, deliberately — ``async_get_device(identifiers=...)``
    is deprecated from HA 2026.8 (ERROR for core and core-integration frames,
    i.e. a hard ``RuntimeError`` from a test frame) and its v3 replacement does
    not exist on the 2026.3 floor this integration supports.  Matching
    identifiers in Python over an index read is the one spelling that is neither
    deprecated nor version-specific.

    ``include_disabled_entities=True`` because a user who disabled everything on
    the duplicate still has it as the device their entities live on.  One of
    ours is enough to make it a ``CLONE``: a helper riding along does not make
    the record any less the home of this instance's entities, and refusing to
    adopt over it would re-home every one of them onto a fresh service device —
    the id churn adoption exists to prevent.
    """
    if device_config_entry_ids(device) != {entry_id}:
        return OwnDeviceRole.SHARED
    if (DOMAIN, entry_id) in device.identifiers:
        return OwnDeviceRole.OWN

    entities = er.async_entries_for_device(
        er.async_get(hass), device.id, include_disabled_entities=True
    )
    if not entities:
        return OwnDeviceRole.STRAY
    if any(entity.config_entry_id == entry_id for entity in entities):
        return OwnDeviceRole.CLONE
    return OwnDeviceRole.ENTANGLED


@callback
def scan_own_devices(hass: HomeAssistant, entry: ConfigEntry) -> OwnDeviceScan:
    """Partition every device this config entry owns.

    One pass over the registry's config-entry index, classifying each record
    through :func:`classify_own_device`.  ``scan.own`` is the only way anything
    in this module asks "which device is ours".
    """
    entry_id = entry.entry_id
    buckets: dict[OwnDeviceRole, list[DeviceEntry]] = {
        role: [] for role in OwnDeviceRole
    }
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry_id):
        buckets[classify_own_device(hass, entry_id, device)].append(device)

    owned = buckets[OwnDeviceRole.OWN]
    return OwnDeviceScan(
        own=owned[0] if owned else None,
        clones=tuple(buckets[OwnDeviceRole.CLONE]),
        strays=tuple(buckets[OwnDeviceRole.STRAY]),
        entangled=tuple(buckets[OwnDeviceRole.ENTANGLED]),
        shared=tuple(buckets[OwnDeviceRole.SHARED]),
    )


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
    with two, "which one is ours" is a question only the user can answer, so
    nothing is written and the post-forward sweep classifies whatever is left
    afresh — as a ``STRAY`` worth a Repair once the platform has re-homed our
    entities off it, or as ``ENTANGLED`` and untouchable if somebody else's
    entities are on it.  Returns the adopted device id, or ``None`` when nothing
    was written.
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
    prefix = duplicate_device_issue_id(entry_id)
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

    Re-classified against the live registry rather than trusted from the
    Repair's stored payload, because the payload was written at setup and the
    user has had every chance to change things since — and through the very
    :func:`classify_own_device` the Repair was raised from, so the set of
    devices we *offer* a delete button for and the set we will actually delete
    are the same set by construction.

    ``STRAY`` — sole-owned, not ours by identifier, holding nothing — is the
    only removable role, and the entity half of that is the one that matters:
    ``async_remove_device`` removes every entity belonging to the removed
    device's config entries along with it, so confirming a stale Repair on a
    device that has since acquired entities would silently delete them.

    Returns ``True`` only when the device was actually removed.
    """
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if device is None:
        return False
    if classify_own_device(hass, entry_id, device) is not OwnDeviceRole.STRAY:
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
        # device belongs to exactly one config entry, so
        # ``device_config_entry_ids`` always answers ``{entry_id}`` for a record
        # this entry's own index yielded, ``shared`` is always empty, and the
        # guard on ``remove_config_entry_id`` (ERROR for core and
        # core-integration frames) is never reached.  Do not "simplify" this
        # into a version check, and do not reach for ``new_config_entry_id``:
        # that MOVES the device to us rather than releasing it.
        _LOGGER.debug(
            "Removing stale config entry link from physical device %s", device.id
        )
        dev_reg.async_update_device(device.id, remove_config_entry_id=entry.entry_id)

    for device in scan.entangled:
        # No Repair, on purpose: the only fix one could offer is a removal that
        # would take another config entry's entities with it.  A log line is the
        # whole intervention — the record is left exactly as the user left it.
        _LOGGER.debug(
            "Leaving device %s alone for %s: this entry owns the record but "
            "every entity on it belongs to something else",
            device.id,
            entry.entry_id,
        )

    desired: set[str] = set()
    for device in scan.strays:
        issue_id = duplicate_device_issue_id(entry.entry_id, device.id)
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
            data={
                ISSUE_DATA_ENTRY_ID: entry.entry_id,
                ISSUE_DATA_DEVICE_ID: device.id,
            },
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
