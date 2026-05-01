"""Diagnostics service for Adaptive Cover Pro — returns live runtime diagnostics."""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

from ..const import DOMAIN
from ..diagnostics import _sanitize

_LOGGER = logging.getLogger(__name__)

GET_DIAGNOSTICS_SCHEMA = vol.Schema(
    {
        vol.Optional("entity_id"): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional("device_id"): vol.All(cv.ensure_list, [str]),
        vol.Optional("area_id"): vol.All(cv.ensure_list, [str]),
        vol.Optional("config_entry_id"): vol.All(cv.ensure_list, [str]),
    }
)


def _resolve_by_config_entry(
    hass: HomeAssistant, entry_ids: list[str]
) -> dict[str, object]:
    """Resolve explicit config entry IDs to {entry_id: coordinator}.

    Raises ServiceValidationError for any ID that doesn't exist or isn't ACP.
    """
    all_coordinators: dict = hass.data.get(DOMAIN, {})
    result = {}
    for entry_id in entry_ids:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                f"Config entry '{entry_id}' not found or does not belong to {DOMAIN}"
            )
        coord = all_coordinators.get(entry_id)
        if coord is not None:
            result[entry_id] = coord
    return result


async def async_handle_get_diagnostics(call: ServiceCall) -> dict:
    """Handle the get_diagnostics service call and return live diagnostics."""
    hass: HomeAssistant = call.hass

    explicit_entry_ids: list[str] = call.data.get("config_entry_id") or []

    if explicit_entry_ids:
        coords_by_entry = _resolve_by_config_entry(hass, explicit_entry_ids)
    else:
        # Standard target resolution (entity_id / device_id / area_id / no target)
        from . import _resolve_targets  # noqa: PLC0415

        target_map = _resolve_targets(hass, call)
        coords_by_entry = {coord.config_entry.entry_id: coord for coord in target_map}

    entries: dict[str, dict] = {}
    for entry_id, coord in coords_by_entry.items():
        diag: dict | None = None

        if coord.data is not None:
            diag = coord.data.diagnostics
        else:
            # First refresh hasn't completed yet — try a live build
            try:
                diag = coord.build_diagnostic_data()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "get_diagnostics: could not build diagnostics for %s: %s",
                    entry_id,
                    exc,
                )
                diag = {"error": f"diagnostics_unavailable: {exc!r}"}

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

    return {
        "version": 1,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "count": len(entries),
        "entries": entries,
    }
