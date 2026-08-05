"""set_position service — moves a cover to a position, clamping to min-mode floors.

Thin target-resolution layer over ``Coordinator.async_apply_user_position``,
which owns the floor-clamp + force-context + dispatch logic.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.helpers import config_validation as cv
from voluptuous.validators import Coerce, Range

from ..cover_types.base import AXIS_NAME_POSITION

if TYPE_CHECKING:
    from homeassistant.core import ServiceCall

_LOGGER = logging.getLogger(__name__)

SET_POSITION_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required("position"): vol.All(Coerce(int), Range(min=0, max=100)),
        vol.Optional("force", default=False): bool,
    }
)


def _resolve_targets(hass, call):
    """Thin re-export so tests can patch the local name."""
    from . import _resolve_targets as _rt  # noqa: PLC0415

    return _rt(hass, call)


async def async_handle_set_position(call: ServiceCall) -> None:
    """Handle the set_position service call.

    Resolves the target block to one or more coordinators (each with an
    optional entity filter), then delegates each command to
    ``coord.async_apply_user_axis`` on the position axis — the shared collapse
    point that routes to ``async_apply_user_position``, the single source of
    truth for floor clamping, pipeline preemption, and dispatch.

    ``force`` (default ``False``) propagates through: when ``False`` the
    service respects force_override / weather and engages manual override
    like a dashboard slider; when ``True`` it bypasses the pipeline check
    and skips manual-override engagement (legacy programmatic behavior).
    """
    hass = call.hass
    requested: int = call.data["position"]
    force: bool = call.data.get("force", False)
    targets = _resolve_targets(hass, call)

    for coord, entity_filter in targets.items():
        entity_ids: list[str] = (
            list(entity_filter) if entity_filter is not None else list(coord.entities)
        )
        # Policy-mandated dispatch order, shared with every other dispatch seam
        # (issue #1115) — applied to an explicit entity filter too, since a
        # Model C target block can name both rails. Identity for every cover
        # type whose entities are physically independent.
        #
        # Name the number and frame this loop fans out so the ordering view can
        # tell a raise from a lower (issue #1118). ``user_dispatch_position`` is
        # the shared derivation ``async_apply_user_position`` runs downstream,
        # floor clamp included — naming the raw request instead lets a floor
        # flip the direction between the ordering view and the gate.
        ordered = coord._policy.order_for_dispatch(  # noqa: SLF001
            entity_ids,
            position=coord.user_dispatch_position(requested),
            inverted=coord.position_axis_inverted,
        )
        for entity_id in ordered:
            await coord.async_apply_user_axis(
                entity_id,
                AXIS_NAME_POSITION,
                requested,
                trigger="set_position",
                force=force,
            )
