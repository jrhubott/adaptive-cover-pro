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
    """Recursively convert non-JSON-serializable types to serializable equivalents."""
    import dataclasses  # noqa: PLC0415
    import enum  # noqa: PLC0415

    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, set | frozenset):
        return sorted(_sanitize(v) for v in obj)
    if isinstance(obj, dt.datetime | dt.date | dt.time):
        return obj.isoformat()
    if isinstance(obj, enum.Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _sanitize(dataclasses.asdict(obj))
    # numpy scalars (numpy not imported at module level — check by duck-typing)
    if hasattr(obj, "item") and hasattr(obj, "dtype"):
        return obj.item()
    return obj


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
):
    """Return config entry diagnostics."""
    from custom_components.adaptive_cover_pro.const import (
        DOMAIN as _DOMAIN,
    )  # noqa: PLC0415

    coordinator = hass.data.get(_DOMAIN, {}).get(config_entry.entry_id)
    if coordinator is None:
        coordinator_diagnostics = {
            "status": "unavailable",
            "reason": "coordinator missing — the integration is not set up for this entry",
        }
    else:
        if coordinator.data is None:
            # Diagnostics requested before the first completed update cycle (e.g.
            # right after a restart/reload). Trigger one refresh so the download
            # captures a full snapshot instead of an empty marker. Scoped to the
            # data-is-None case, so a normal download (data already present) never
            # triggers an extra update cycle or cover commands.
            await coordinator.async_refresh()

        if coordinator.data is not None:
            coordinator_diagnostics = _sanitize(coordinator.data.diagnostics)
        else:
            # The refresh did not yield data (first cycle still failing). Surface
            # an explicit marker (not a bare None) and include the event buffer so
            # there is still a timeline to triage from.
            marker = {
                "status": "unavailable",
                "reason": "no completed update cycle yet — coordinator.data is None",
            }
            event_buffer = getattr(coordinator, "_event_buffer", None)
            if event_buffer is not None:
                timeline = _sanitize(event_buffer.snapshot())
                marker["event_timeline"] = timeline
                marker["data_window"] = DiagnosticsBuilder._compute_data_window(
                    timeline
                )
            coordinator_diagnostics = marker

    return {
        "title": "Adaptive Cover Pro Configuration",
        "type": "config_entry",
        "identifier": config_entry.entry_id,
        "config_data": dict(config_entry.data),
        "config_options": dict(config_entry.options),
        "diagnostics": coordinator_diagnostics,
    }
