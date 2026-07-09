"""set_axes service — dispatches a per-axis target map in one call (issue #725).

Generalizes ``set_position`` / ``set_tilt`` into a single service that accepts
``axes: {position, tilt}`` plus ``force``. Each requested axis is validated
against the target entity's ``policy.supported_axes(caps)`` and then dispatched
through ``Coordinator.async_apply_user_axis`` — the same collapse point the two
single-axis services now use. Requesting an axis the cover does not support is a
``ServiceValidationError`` (the issue is explicit: an error, not a no-op).

No cover-type string branching: the supported-axis set and dispatch are keyed on
the ``AXIS_NAME_*`` constants and ``policy.supported_axes`` / ``describe`` — the
service works unchanged for a ninth cover type.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from voluptuous.validators import Coerce, Range

from ..cover_types.base import (
    AXIS_NAME_POSITION,
    AXIS_NAME_TILT,
    AXIS_VALUE_MAX,
    AXIS_VALUE_MIN,
)

if TYPE_CHECKING:
    from homeassistant.core import ServiceCall

_LOGGER = logging.getLogger(__name__)

_AXIS_VALUE = vol.All(Coerce(int), Range(min=AXIS_VALUE_MIN, max=AXIS_VALUE_MAX))

SET_AXES_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required("axes"): vol.Schema(
            {
                vol.Optional(AXIS_NAME_POSITION): _AXIS_VALUE,
                vol.Optional(AXIS_NAME_TILT): _AXIS_VALUE,
            }
        ),
        vol.Optional("force", default=False): bool,
    }
)


def _resolve_targets(hass, call):
    """Thin re-export so tests can patch the local name."""
    from . import _resolve_targets as _rt  # noqa: PLC0415

    return _rt(hass, call)


async def async_handle_set_axes(call: ServiceCall) -> None:
    """Handle the set_axes service call.

    Resolves targets, validates every requested axis against each target
    entity's supported axes (raising ``ServiceValidationError`` for an
    unsupported axis or an empty request), then dispatches each axis through
    ``coord.async_apply_user_axis``. Validation happens for all targets before
    any dispatch, so a rejected axis never leaves a partial move behind.
    """
    hass = call.hass
    axes: dict[str, int] = dict(call.data["axes"])
    force: bool = call.data.get("force", False)

    if not axes:
        raise ServiceValidationError(
            "set_axes requires at least one axis in 'axes' (position and/or tilt)"
        )

    targets = _resolve_targets(hass, call)

    # Validate every (entity, axis) pair up front so an unsupported axis rejects
    # the whole call rather than dispatching a partial set.
    resolved: list[tuple] = []
    for coord, entity_filter in targets.items():
        entity_ids = (
            list(entity_filter) if entity_filter is not None else list(coord.entities)
        )
        policy = coord._policy  # noqa: SLF001
        for entity_id in entity_ids:
            caps = coord._cover_provider.read_single_capabilities(  # noqa: SLF001
                entity_id
            )
            supported = {a.name for a in policy.supported_axes(caps)}
            for axis_name in axes:
                if axis_name not in supported:
                    raise ServiceValidationError(
                        f"{policy.display_label()} cover {entity_id} does not "
                        f"support the '{axis_name}' axis"
                    )
            resolved.append((coord, entity_id))

    for coord, entity_id in resolved:
        for axis_name, value in axes.items():
            await coord.async_apply_user_axis(
                entity_id, axis_name, int(value), trigger="set_axes", force=force
            )
