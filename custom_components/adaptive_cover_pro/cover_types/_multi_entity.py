"""Shared base for multi-*entity* cover types (dual-panel, split-panel).

These cover types differ from every single-entity type and from venetian: one
logical window is driven by TWO separate HA cover entities, each tagged with a
role and dispatched its own per-cycle target. The two entities are kept as the
flat ``self.entities`` list the rest of the integration iterates, with a side
``entity_id → role`` map so ``resolve_axis_targets`` can route the right value
to each.

This mixin factors out the role plumbing shared by both concrete types:
- two dedicated single-entity config pickers (one per role),
- folding those picks into ``CONF_ENTITIES``,
- the ``entity_id → role`` map, and
- building the per-role ``AxisTarget`` list (with a broadcast fallback when a
  role is unconfigured/unavailable).

Each concrete policy declares ``role_conf_keys`` (ordered ``(conf_key, role)``
pairs) and computes the per-role values in ``resolve_axis_targets``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import voluptuous as vol
from homeassistant.helpers import selector

from ..const import CONF_ENTITIES
from .axis_target import AxisTarget
from .base import POSITION_AXIS, CoverTypePolicy

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class MultiEntityPolicy(CoverTypePolicy):
    """Base for cover types backed by two role-tagged HA entities."""

    # Ordered ``(config_key, role)`` pairs — one dedicated single-entity picker
    # per role. The first pair's entity becomes ``self.entities[0]``.
    role_conf_keys: ClassVar[tuple[tuple[str, str], ...]] = ()

    # Multi-entity types surface per-role target sensors.
    exposes_dual_panel_sensors: ClassVar[bool] = True

    def multi_entity_extra_keys(self) -> tuple[str, ...]:
        """Extra CONF_* keys this multi-entity type owns beyond the role keys.

        Folded into ``live_option_keys`` so the options surface recognises
        them. Default none; concrete types (e.g. dual-panel's blackout
        triggers) override.
        """
        return ()

    def live_option_keys(self) -> frozenset[str]:
        """Include the per-role pickers and any extra keys as valid options."""
        keys = set(super().live_option_keys())
        keys.update(conf_key for conf_key, _role in self.role_conf_keys)
        keys.update(self.multi_entity_extra_keys())
        return frozenset(keys)

    def panel_role_map(self, options: dict) -> dict[str, str]:
        """Map each configured role entity_id to its role string."""
        mapping: dict[str, str] = {}
        for conf_key, role in self.role_conf_keys:
            entity = options.get(conf_key)
            if entity:
                mapping[entity] = str(role)
        return mapping

    def normalize_entities(self, options: dict) -> dict:
        """Fold the per-role single-entity picks into ``CONF_ENTITIES``."""
        entities = [
            options.get(conf_key)
            for conf_key, _role in self.role_conf_keys
            if options.get(conf_key)
        ]
        return {**options, CONF_ENTITIES: entities}

    def entity_step_schema(
        self,
        hass: HomeAssistant | None = None,  # noqa: ARG002
    ) -> vol.Schema:
        """One required single-entity ``cover`` picker per role."""
        cover_selector = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="cover")
        )
        return vol.Schema(
            {
                vol.Required(conf_key): cover_selector
                for conf_key, _role in self.role_conf_keys
            }
        )

    def _role_targets(
        self,
        entities: list[str],
        panel_role: dict[str, str],
        role_values: dict[str, int],
        fallback: int,
    ) -> list[AxisTarget]:
        """Build one position ``AxisTarget`` per entity, routed by role.

        ``role_values`` maps role → target value. An entity whose role is
        unconfigured/unknown falls back to ``fallback`` (the broadcast
        ``state``) so a half-configured or degraded instance still tracks
        rather than going silent.
        """
        primary = self.axes[0] if self.axes else POSITION_AXIS
        targets: list[AxisTarget] = []
        for entity in entities:
            role = panel_role.get(entity)
            value = role_values.get(role, fallback) if role is not None else fallback
            targets.append(
                AxisTarget(
                    entity_id=entity,
                    axis=primary,
                    value=value,
                    role=role,
                )
            )
        return targets
