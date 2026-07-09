"""set_tilt service — drives the slat/tilt axis on dual-axis covers (issue #684).

Thin target-resolution layer over ``Coordinator.async_apply_user_tilt``, which
owns the policy-hook dispatch (venetian slats untouched carriage) and the
``cover_tilt`` position fall-back. Mirrors ``set_position_service`` exactly,
delegating to the dedicated tilt entry point instead of the position one.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.helpers import config_validation as cv
from voluptuous.validators import Coerce, Range

from ..cover_types.base import AXIS_NAME_TILT

if TYPE_CHECKING:
    from homeassistant.core import ServiceCall

_LOGGER = logging.getLogger(__name__)

SET_TILT_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required("tilt"): vol.All(Coerce(int), Range(min=0, max=100)),
        vol.Optional("force", default=False): bool,
    }
)


def _resolve_targets(hass, call):
    """Thin re-export so tests can patch the local name."""
    from . import _resolve_targets as _rt  # noqa: PLC0415

    return _rt(hass, call)


async def async_handle_set_tilt(call: ServiceCall) -> None:
    """Handle the set_tilt service call.

    Resolves the target block to one or more coordinators (each with an
    optional entity filter), then delegates each command to
    ``coord.async_apply_user_axis`` on the tilt axis — the shared collapse point
    that routes to ``async_apply_user_tilt``, the single source of truth for
    tilt-axis dispatch (venetian slats, ``cover_tilt`` position fall-back).

    ``force`` (default ``False``) propagates through: when ``False`` the service
    engages manual override like a dashboard slider; when ``True`` it skips
    manual-override engagement, matching ``set_position``'s semantics.
    """
    hass = call.hass
    requested: int = call.data["tilt"]
    force: bool = call.data.get("force", False)
    targets = _resolve_targets(hass, call)

    for coord, entity_filter in targets.items():
        entity_ids: list[str] = (
            list(entity_filter) if entity_filter is not None else list(coord.entities)
        )
        for entity_id in entity_ids:
            await coord.async_apply_user_axis(
                entity_id, AXIS_NAME_TILT, requested, trigger="set_tilt", force=force
            )
