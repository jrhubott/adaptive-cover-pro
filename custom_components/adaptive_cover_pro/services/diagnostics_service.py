"""Diagnostics service for Adaptive Cover Pro — returns live runtime diagnostics."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

from ..diagnostics import _sanitize
from ..diagnostics.resolve import read_from_coordinator

_LOGGER = logging.getLogger(__name__)


async def async_handle_get_diagnostics(call: ServiceCall) -> dict:
    """Handle the get_diagnostics service call and return live diagnostics."""
    hass: HomeAssistant = call.hass

    from . import _resolve_service_targets  # noqa: PLC0415

    coords_by_entry = _resolve_service_targets(hass, call)

    entries: dict[str, dict] = {}
    for entry_id, coord in coords_by_entry.items():
        # Read-only resolution (prefers coord.data, else a live build) — never an
        # update cycle. Unified with the download path via diagnostics.resolve.
        read = read_from_coordinator(coord)
        if read.error is not None:
            _LOGGER.warning(
                "get_diagnostics: could not build diagnostics for %s: %s",
                entry_id,
                read.error,
            )
            diag: dict | None = {"error": read.error}
        else:
            diag = read.payload

        last_success_time = coord._last_update_success_time  # noqa: SLF001
        entries[entry_id] = {
            "config_entry_id": entry_id,
            "name": coord.config_entry.data.get("name"),
            "cover_type": coord._cover_type,  # noqa: SLF001
            "last_update_success": coord.last_update_success,
            "last_update_success_time": (
                last_success_time.isoformat() if last_success_time else None
            ),
            "diagnostics": _sanitize(diag) if diag is not None else None,
        }

    from . import _build_response_envelope  # noqa: PLC0415

    return _build_response_envelope(entries)
