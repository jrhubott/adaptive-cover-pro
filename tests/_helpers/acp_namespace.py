"""Seed an ACP instance's own entity-registry rows for ``acp`` namespace tests.

The ``acp`` self-reference namespace (issue #1159) resolves a canonical key to
this config entry's own ``entity_id`` by matching ``RegistryEntry.translation_key``.
Every test that renders a template against the namespace therefore needs two
things: a registered config entry, and at least one registry row hanging off it.

Per CODING_GUIDELINES § cross-file test helpers, these live here rather than
being copied into each of the eight test modules that need them.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.templates import (
    build_acp_template_variables,
)


def make_acp_entry(
    hass: HomeAssistant,
    entry_id: str,
    *,
    name: str = "Dining Room Shade",
    title: str | None = None,
    options: dict | None = None,
) -> MockConfigEntry:
    """Register a config entry for seeded namespace rows to hang off.

    ``title`` is deliberately different from ``data["name"]`` — the namespace's
    error messages name the instance by title, and getting that difference wrong
    in a fixture would lock the tests to a shape no real install produces. It is
    therefore *derived* rather than typed: ``config_flow`` builds the title as
    ``f"{_cover_type_label(sensor_type)} {name}"``, and ``_cover_type_label``
    delegates to ``get_policy(sensor_type).display_label()``. For a blind that
    label is "Vertical Blind", so the default title here is
    "Vertical Blind Dining Room Shade" — not "Vertical Dining Room Shade".
    """
    if title is None:
        title = f"{get_policy(CoverType.BLIND).display_label()} {name}"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": name, CONF_SENSOR_TYPE: CoverType.BLIND},
        options=options if options is not None else {},
        entry_id=entry_id,
        title=title,
    )
    entry.add_to_hass(hass)
    return entry


def seed_acp_row(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    domain: str,
    translation_key: str,
    object_id: str,
) -> str:
    """Seed one entity-registry row the way a real ACP platform would.

    Returns the assigned ``entity_id``. The unique_id is given the real
    ``f"{entry_id}_{key}"`` shape so a subsequent real platform setup adopts this
    exact row — but the resolver never reads it, only ``translation_key``.
    """
    reg = er.async_get(hass)
    return reg.async_get_or_create(
        domain,
        DOMAIN,
        f"{entry.entry_id}_{translation_key}",
        config_entry=entry,
        translation_key=translation_key,
        suggested_object_id=object_id,
    ).entity_id


def seed_sun_infront(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    state: str | None = None,
    *,
    object_id: str = "dining_room_shade_sun_infront",
) -> str:
    """Seed the Sun Infront binary sensor (key ``sun_motion``) and optionally its state.

    The canonical namespace example from issue #1159, and the one key whose
    display slug (``sun_infront``) and internal key (``sun_motion``) differ.
    """
    entity_id = seed_acp_row(hass, entry, "binary_sensor", "sun_motion", object_id)
    if state is not None:
        hass.states.async_set(entity_id, state)
    return entity_id


def acp_variables(
    hass: HomeAssistant, entry: MockConfigEntry, *, include_state: bool = False
) -> dict:
    """Build the render context for *entry* — the same factory production uses."""
    return build_acp_template_variables(
        hass, entry.entry_id, include_state=include_state
    )
