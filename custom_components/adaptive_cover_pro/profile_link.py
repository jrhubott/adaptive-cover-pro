"""Shared Building Profile link helpers.

A neutral home for the profile/cover linkage helpers so both ``config_flow``
(link/unlink UI, plus the clear propagation a profile save drives) and
``__init__`` (live propagation + deletion cleanup) can reuse a single source.
It must not live in ``helpers.py`` — that module is imported by
``cover_types.base``, and these helpers need ``get_policy`` from
``cover_types``, which would create an import cycle.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    BUILDING_PROFILE_SENSOR_KEYS,
    CONF_BUILDING_PROFILE_ID,
    CONF_PROFILE_SENSOR_OVERRIDES,
    CONF_SENSOR_TYPE,
    DOMAIN,
)
from .cover_types import get_policy


def _is_set(value: Any) -> bool:
    """Return True when an option value counts as configured (non-empty)."""
    return value not in (None, "", [])


def effective_profile_overrides(cover_options: dict | None) -> frozenset[str]:
    """Return the set of shared keys this cover has locally overridden."""
    return frozenset((cover_options or {}).get(CONF_PROFILE_SENSOR_OVERRIDES, []) or [])


def classify_profile_sensor_source(
    key: str, cover_options: dict, profile_options: dict
) -> tuple[str, Any]:
    """Return ``(source, effective_value)`` for one shared-sensor key.

    The single source of truth for "is this cover using the profile's value, an
    override, or its own?" — shared by the diagnostics sensor-source block, the
    overview, and the Local Overrides step. Three-way:

    - ``"profile"`` — the profile defines the key and the cover inherits it
      (not in the cover's override list); effective value = the profile's.
    - ``"override"`` — the profile defines the key but the cover overrides it
      (key in the override list); effective value = the cover's.
    - ``"local"`` — the profile leaves the key blank; the cover keeps its own.
    """
    overridden = key in effective_profile_overrides(cover_options)
    if _is_set(profile_options.get(key)) and not overridden:
        return "profile", profile_options.get(key)
    if overridden:
        return "override", (cover_options or {}).get(key)
    return "local", (cover_options or {}).get(key)


def compute_override_keys(cover_options: dict, profile_options: dict) -> list[str]:
    """Profile-defined shared keys whose cover value differs from the profile.

    Recomputed on every cover save against the THEN-current profile value: an
    inherited key (cover value == profile value) drops out, a changed key is
    recorded. Stored (not re-derived) because at propagation time the profile
    value is changing, so a value-diff alone could not tell an inherited key
    (cover == old profile) apart from a genuine override.
    """
    return sorted(
        key
        for key in BUILDING_PROFILE_SENSOR_KEYS
        if _is_set(profile_options.get(key))
        and (cover_options or {}).get(key) != profile_options.get(key)
    )


def _building_profile_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Return all Building Profile config entries (controls_cover == False)."""
    return [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if not get_policy(e.data.get(CONF_SENSOR_TYPE)).controls_cover
    ]


def _cover_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Return all physical cover config entries.

    Filters on capability, never a cover-type string: virtual entry types are
    excluded — the Building Profile (``controls_cover == False``) and the
    cover group (``is_orchestrator == True``), which controls covers but is
    not itself a physical cover. Consumers (duplicate sources, profile link
    lists, group membership pickers) all want real covers only.
    """
    return [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if get_policy(e.data.get(CONF_SENSOR_TYPE)).controls_cover
        and not get_policy(e.data.get(CONF_SENSOR_TYPE)).is_orchestrator
    ]


def _covers_linked_to(
    hass: HomeAssistant, profile_entry: ConfigEntry
) -> list[ConfigEntry]:
    """Return every ACP entry linked to ``profile_entry`` via its id."""
    return [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.options.get(CONF_BUILDING_PROFILE_ID) == profile_entry.entry_id
    ]


def profile_for_cover(
    hass: HomeAssistant | None, cover_options: dict | None
) -> ConfigEntry | None:
    """Return the Building Profile a cover is linked to, or None.

    None when the cover is unlinked, ``hass`` is absent, or the profile entry
    no longer exists (deleted).
    """
    profile_id = (cover_options or {}).get(CONF_BUILDING_PROFILE_ID)
    if not profile_id or hass is None:
        return None
    return hass.config_entries.async_get_entry(profile_id)


def merge_profile_into_config(
    profile_entry: ConfigEntry,
    config_dict: dict,
    *,
    overridden: frozenset[str] = frozenset(),
) -> None:
    """Merge a profile's non-empty shared-sensor keys into a plain dict.

    Skips keys the cover has locally overridden. Safe to call from both the
    create flow (no existing entry, no overrides) and ``_copy_profile_to_cover``
    (existing entry, may have overrides). Does NOT stamp
    ``CONF_BUILDING_PROFILE_ID`` — callers handle that so each context can
    write it in the right place.

    Copy-only, never remove: a blank profile key means "the profile does not
    define this", which is indistinguishable from "the user never filled it
    in" once stored, so it must leave the cover's own value alone. Removing
    the key a profile save actually emptied is a separate, save-time job —
    ``propagate_profile_clears`` (issue #1085).
    """
    subset = {
        k: v
        for k, v in profile_entry.options.items()
        if k in BUILDING_PROFILE_SENSOR_KEYS and _is_set(v) and k not in overridden
    }
    config_dict.update(subset)


def cleared_profile_keys(previous_options: dict, new_options: dict) -> frozenset[str]:
    """Shared-sensor keys a profile save emptied: set before, blank after.

    A stored option cannot say whether a blank field was cleared or simply
    never filled in — the profile form writes ``None`` for both (issue #1085).
    The transition can, so the profile's stored options are diffed against the
    ones the options dialog is about to write. Blank in either shape counts
    (``None``, ``""``, ``[]`` — the multi-selects clear to the last), and a key
    that was already blank, or that the dialog re-filled before saving, is not
    a clear.
    """
    return frozenset(
        key
        for key in BUILDING_PROFILE_SENSOR_KEYS
        if _is_set((previous_options or {}).get(key))
        and not _is_set((new_options or {}).get(key))
    )


def propagate_profile_clears(
    hass: HomeAssistant, profile_entry: ConfigEntry, cleared: frozenset[str]
) -> None:
    """Drop the keys a profile save emptied from the covers inheriting them.

    The counterpart to ``_copy_profile_to_cover``: that copier only ever
    applies the profile's non-empty keys, so on its own a cleared field would
    leave the last-copied sensor sitting on every linked cover. Only keys in
    ``cleared`` are touched, and only where the cover was inheriting — a key in
    the cover's ``CONF_PROFILE_SENSOR_OVERRIDES`` is that cover's own
    deliberate choice and stays. Removing rather than blanking keeps a dropped
    key indistinguishable from one never configured, which is what the
    diagnostics and overview classification already expects.

    Driven from the options flow's save, not from the propagation listener:
    the listener sees only the profile's new options, where a cleared field is
    indistinguishable from an unfilled one. Scoped by ``_covers_linked_to``, so
    calling it for a non-profile entry is a no-op — only a Building Profile's
    ``entry_id`` ever appears in a cover's ``CONF_BUILDING_PROFILE_ID``. Writes
    nothing when nothing was cleared, which is the overwhelmingly common save.
    """
    if not cleared:
        return
    for cover in _covers_linked_to(hass, profile_entry):
        drop = {
            key
            for key in cleared - effective_profile_overrides(cover.options)
            if key in cover.options
        }
        if not drop:
            continue
        options = {k: v for k, v in cover.options.items() if k not in drop}
        hass.config_entries.async_update_entry(cover, options=options)


def _copy_profile_to_cover(
    hass: HomeAssistant, profile_entry: ConfigEntry, cover_entry: ConfigEntry
) -> None:
    """Copy a profile's inherited shared-sensor subset into a linked cover.

    Override-aware: only non-empty profile keys the cover has NOT overridden are
    copied, so a profile that leaves a field blank never wipes the cover's own
    value, and a cover's deliberate local override survives profile edits. Stamps
    ``CONF_BUILDING_PROFILE_ID``, preserves the cover's override list, and reuses
    the ``async_update_entry`` merge — the update fires the cover's self-reload
    listener. The single shared copier; both linking and the profile-change
    propagation listener reuse it.

    Delegates the key-subset logic to ``merge_profile_into_config`` so there is
    a single source of truth for which keys are copied and how overrides are
    respected.
    """
    overridden = effective_profile_overrides(cover_entry.options)
    new_options: dict = {
        **cover_entry.options,
        CONF_BUILDING_PROFILE_ID: profile_entry.entry_id,
    }
    merge_profile_into_config(profile_entry, new_options, overridden=overridden)
    hass.config_entries.async_update_entry(cover_entry, options=new_options)


def clear_cover_override(
    hass: HomeAssistant,
    profile_entry: ConfigEntry,
    cover_entry: ConfigEntry,
    key: str,
) -> None:
    """Clear one local override on a linked cover, re-syncing it to the profile.

    Removes ``key`` from the cover's override list, then re-inherits: sets the
    cover's value to the profile's when the profile defines it, else removes the
    key entirely (a "Local sensor" the profile does not own). ``async_update_entry``
    fires the cover's self-reload listener.
    """
    options = dict(cover_entry.options)
    overrides = [k for k in effective_profile_overrides(options) if k != key]
    if overrides:
        options[CONF_PROFILE_SENSOR_OVERRIDES] = overrides
    else:
        options.pop(CONF_PROFILE_SENSOR_OVERRIDES, None)

    profile_value = (profile_entry.options or {}).get(key)
    if _is_set(profile_value):
        options[key] = profile_value
    else:
        options.pop(key, None)

    hass.config_entries.async_update_entry(cover_entry, options=options)
