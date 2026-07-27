"""Troubleshoot service for Adaptive Cover Pro — returns triage findings (issue #1059)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

    from ..diagnostics.triage import Finding

from ..const import CONF_SENSOR_TYPE
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

    Every entry has exactly one shape — ``config_entry_id``, ``name``,
    ``source``, ``report``, ``findings``, ``error`` — regardless of whether
    the read succeeded (issue #1059 audit, findings #1/#2). ``error`` is
    ``None`` on a fully healthy entry; any other value means something
    degraded, and ``source`` says how far triage got:

    - ``"coordinator"`` / ``"built"`` / ``"cache"`` — a full, trustworthy
      read; ``error`` is ``None``.
    - ``"unavailable"`` — diagnostics weren't ready yet, so only the CONFIG
      rules ran; ``findings``/``report`` reflect that partial (but valid)
      result, and ``error`` carries the read failure that produced it.
    - ``"error"`` — building the troubleshoot result itself raised (a bad
      options shape, an unexpected exception): nothing ran at all, so
      ``findings`` is ``[]`` and ``report`` is a human-readable failure
      message rather than blank.

    A single targeted instance failing must not sink the whole response — the
    other targeted instances still come back (issue #1059).
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
            result = build_troubleshoot_result(
                hass,
                read,
                options=coord.config_entry.options,
                sensor_type=coord.config_entry.data.get(CONF_SENSOR_TYPE),
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
                "source": "error",
                "report": f"⚠️ Troubleshooting could not run for this cover: {exc}",
                "findings": [],
                "error": f"troubleshoot_unavailable: {exc!r}",
            }
            continue

        entries[entry_id] = {
            "config_entry_id": entry_id,
            "name": name,
            "source": result.source,
            "report": result.report,
            "findings": [_finding_to_dict(f, labels) for f in result.findings],
            "error": read.error,
        }

    from . import _build_response_envelope  # noqa: PLC0415

    return _build_response_envelope(entries)
