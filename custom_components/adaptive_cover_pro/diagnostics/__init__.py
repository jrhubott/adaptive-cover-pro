"""Diagnostics package for Adaptive Cover Pro."""

from __future__ import annotations

import datetime as dt

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .builder import DiagnosticContext, DiagnosticsBuilder

__all__ = [
    "DiagnosticContext",
    "DiagnosticsBuilder",
    "async_get_config_entry_diagnostics",
]


def _sanitize(obj):
    """Recursively convert non-JSON-serializable types to strings."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, (dt.datetime, dt.date, dt.time)):
        return obj.isoformat()
    return obj


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
):
    """Return config entry diagnostics."""
    from custom_components.adaptive_cover_pro.const import DOMAIN as _DOMAIN  # noqa: PLC0415

    coordinator = hass.data.get(_DOMAIN, {}).get(config_entry.entry_id)
    coordinator_diagnostics = None
    if coordinator is not None and coordinator.data is not None:
        coordinator_diagnostics = _sanitize(coordinator.data.diagnostics)

    return {
        "title": "Adaptive Cover Pro Configuration",
        "type": "config_entry",
        "identifier": config_entry.entry_id,
        "config_data": dict(config_entry.data),
        "config_options": dict(config_entry.options),
        "diagnostics": coordinator_diagnostics,
    }
