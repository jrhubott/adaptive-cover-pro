"""stop service — sends stop_cover via the ACP-stamped context.

Thin target-resolution layer over ``Coordinator.async_apply_user_stop``,
which owns the manual-override engagement and stop dispatch logic.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import ServiceCall

_LOGGER = logging.getLogger(__name__)


def _resolve_targets(hass, call):
    """Thin re-export so tests can patch the local name."""
    from . import _resolve_targets as _rt  # noqa: PLC0415

    return _rt(hass, call)


async def async_handle_stop(call: ServiceCall) -> None:
    """Handle the stop service call.

    Resolves the target block to one or more coordinators (each with an
    optional entity filter), then delegates each stop command to
    ``coord.async_apply_user_stop`` — the single delegation point for
    manual-override engagement and ACP-context-stamped stop dispatch.
    """
    hass = call.hass
    targets = _resolve_targets(hass, call)

    for coord, entity_filter in targets.items():
        entity_ids: list[str] = (
            list(entity_filter) if entity_filter is not None else list(coord.entities)
        )
        for entity_id in entity_ids:
            await coord.async_apply_user_stop(entity_id, trigger="stop")
