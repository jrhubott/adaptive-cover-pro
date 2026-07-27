"""Troubleshoot service for Adaptive Cover Pro — returns triage findings (issue #1059)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

    from ..diagnostics.triage import Finding

from ..const import CONF_SENSOR_TYPE, CoverType
from ..diagnostics.resolve import build_troubleshoot_result, read_from_coordinator
from ..diagnostics.triage import wiki_anchor_for
from ..reason_i18n import reason_to_dict, render
from ..troubleshoot_i18n import load_troubleshoot_labels

_LOGGER = logging.getLogger(__name__)


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

    A single targeted instance failing (a bad options shape, an unexpected
    ``build_troubleshoot_result`` exception) degrades that one entry to an
    ``error`` entry rather than losing the whole response — the other
    targeted instances still come back (issue #1059).
    """
    hass: HomeAssistant = call.hass

    from . import _resolve_service_targets  # noqa: PLC0415

    coords_by_entry = _resolve_service_targets(hass, call)

    language = hass.config.language or "en"
    labels = await hass.async_add_executor_job(load_troubleshoot_labels, language)

    entries: dict[str, dict] = {}
    for entry_id, coord in coords_by_entry.items():
        name = coord.config_entry.data.get("name")
        try:
            # Read-only resolution (prefers coord.data, else a live build) —
            # never an update cycle. Same reader get_diagnostics uses.
            read = read_from_coordinator(coord)
            if read.error is not None:
                _LOGGER.warning(
                    "get_troubleshooting: could not read diagnostics for %s: %s",
                    entry_id,
                    read.error,
                )
            # A group/orchestrator or a not-yet-configured cover can carry no
            # sensor_type; get_policy(None) raises, so fall back to BLIND —
            # the same default the options flow uses (config_flow.py
            # OptionsFlowHandler.__init__).
            sensor_type = (
                coord.config_entry.data.get(CONF_SENSOR_TYPE) or CoverType.BLIND
            )
            result = build_troubleshoot_result(
                hass,
                read,
                options=coord.config_entry.options,
                sensor_type=sensor_type,
                labels=labels,
            )
        except Exception as exc:  # noqa: BLE001 - one bad entry must not sink the batch
            _LOGGER.warning(
                "get_troubleshooting: could not build results for %s: %s",
                entry_id,
                exc,
            )
            entries[entry_id] = {
                "config_entry_id": entry_id,
                "name": name,
                "error": f"troubleshoot_unavailable: {exc!r}",
            }
            continue

        entry: dict[str, object] = {
            "config_entry_id": entry_id,
            "name": name,
            "source": result.source,
            "report": result.report,
            "findings": [_finding_to_dict(f, labels) for f in result.findings],
        }
        if read.error is not None:
            entry["error"] = read.error
        entries[entry_id] = entry

    from . import _build_response_envelope  # noqa: PLC0415

    return _build_response_envelope(entries)
