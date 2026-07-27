"""Troubleshoot service for Adaptive Cover Pro — returns triage findings (issue #1059)."""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.helpers import config_validation as cv

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

    from ..diagnostics.triage import Finding

from ..diagnostics.resolve import build_troubleshoot_result, read_from_coordinator
from ..diagnostics.triage import wiki_anchor_for
from ..reason_i18n import reason_to_dict, render
from ..troubleshoot_i18n import load_troubleshoot_labels

_LOGGER = logging.getLogger(__name__)

GET_TROUBLESHOOTING_SCHEMA = vol.Schema(
    {
        vol.Optional("entity_id"): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional("device_id"): vol.All(cv.ensure_list, [str]),
        vol.Optional("area_id"): vol.All(cv.ensure_list, [str]),
        vol.Optional("config_entry_id"): vol.All(cv.ensure_list, [str]),
    }
)


def _finding_to_dict(
    finding: Finding, labels: dict[str, str] | None
) -> dict[str, object]:
    """Serialize a Finding into the JSON-safe shape the service returns.

    Reuses :func:`..reason_i18n.reason_to_dict` — the single JSON-serialization
    path for a ``Reason`` (it already handles nested ``Reason`` fragments, e.g.
    ``TriageCode.SKIP_AGE``) — rather than hand-rolling a second one. ``severity``
    is emitted as its plain string value so the response stays JSON-serializable
    for the HA websocket API.
    """
    payload = reason_to_dict(finding.reason)
    return {
        "code": payload["code"],
        "params": payload["params"],
        "severity": finding.severity.value,
        "fix_step": finding.fix_step,
        "wiki": wiki_anchor_for(finding.reason.code),
        "message": render(finding.reason, labels),
    }


async def async_handle_get_troubleshooting(call: ServiceCall) -> dict:
    """Handle the get_troubleshooting service call and return triage findings.

    Mirrors ``get_diagnostics``'s targeting contract exactly (entity/device/area
    target block, or an explicit ``config_entry_id`` list) and delegates the
    view-build/triage/render sequence to
    :func:`~..diagnostics.resolve.build_troubleshoot_result` — the same seam
    the options-flow Troubleshoot step uses, so findings never diverge between
    the two surfaces. Never triggers an update cycle.
    """
    hass: HomeAssistant = call.hass

    explicit_entry_ids: list[str] = call.data.get("config_entry_id") or []

    if explicit_entry_ids:
        from . import _resolve_by_config_entry  # noqa: PLC0415

        coords_by_entry = _resolve_by_config_entry(hass, explicit_entry_ids)
    else:
        from . import _resolve_targets  # noqa: PLC0415

        target_map = _resolve_targets(hass, call)
        coords_by_entry = {coord.config_entry.entry_id: coord for coord in target_map}

    language = hass.config.language or "en"
    labels = await hass.async_add_executor_job(load_troubleshoot_labels, language)

    entries: dict[str, dict] = {}
    for entry_id, coord in coords_by_entry.items():
        # Read-only resolution (prefers coord.data, else a live build) — never an
        # update cycle. Same reader get_diagnostics uses.
        read = read_from_coordinator(coord)
        result = build_troubleshoot_result(
            hass,
            read,
            options=coord.config_entry.options,
            sensor_type=coord.config_entry.data.get("sensor_type"),
            labels=labels,
        )
        entries[entry_id] = {
            "config_entry_id": entry_id,
            "name": coord.config_entry.data.get("name"),
            "source": result.source,
            "report": result.report,
            "findings": [_finding_to_dict(f, labels) for f in result.findings],
        }

    return {
        "version": 1,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "count": len(entries),
        "entries": entries,
    }
