"""Troubleshoot service for Adaptive Cover Pro — returns triage findings (issue #1059)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

    from ..diagnostics.triage import Finding

from ..const import CONF_SENSOR_TYPE
from ..diagnostics.resolve import (
    TROUBLESHOOT_BUILD_FAILED,
    build_troubleshoot_result,
    read_from_coordinator,
)
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


def _build_error_entry(
    entry_id: str, name: str | None, reason: str
) -> dict[str, object]:
    """Build the degraded, six-key entry shape for a total-failure instance.

    Shared by two producers of the same "total failure" shape (issue #1059
    audit round 3, defect #1 / nit #3): an explicitly-targeted config entry
    with no cover coordinator (a cover group, a Building Profile, or an entry
    that isn't currently loaded), and an unexpected exception raised while
    building the result for an otherwise-resolved coordinator. Both give the
    caller exactly the same six keys a healthy entry has — ``config_entry_id``,
    ``name``, ``source``, ``report``, ``findings``, ``error`` — so nothing
    downstream has to branch on which producer built it. ``reason`` is used,
    unchanged, as the single human-readable representation of what went wrong
    in both ``report`` and ``error`` — never a mix of ``str(exc)`` in one and
    ``repr(exc)`` in the other.
    """
    return {
        "config_entry_id": entry_id,
        "name": name,
        "source": "error",
        "report": TROUBLESHOOT_BUILD_FAILED.format(reason=reason),
        "findings": [],
        "error": f"troubleshoot_unavailable: {reason}",
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
    degraded, and ``source`` says how far triage got. This function only ever
    reads via :func:`~..diagnostics.resolve.read_from_coordinator` (never
    :func:`~..diagnostics.resolve.read_diagnostics`), so of the vocabulary in
    :data:`~..diagnostics.resolve.RESOLVE_READ_SOURCES` it can only ever
    produce ``"coordinator"`` or ``"built"`` — ``"cache"`` is reachable only
    through the options-flow Troubleshoot step, which resolves via
    ``read_diagnostics`` instead:

    - ``"coordinator"`` / ``"built"`` — a full, trustworthy read; ``error``
      is ``None``.
    - ``"unavailable"`` — diagnostics weren't ready yet, so only the CONFIG
      rules ran; ``findings``/``report`` reflect that partial (but valid)
      result, and ``error`` carries the read failure that produced it.
    - ``"error"`` — a value this function adds itself, not part of
      :data:`~..diagnostics.resolve.RESOLVE_READ_SOURCES`: either building the
      troubleshoot result raised (a bad options shape, an unexpected
      exception), or an explicitly-targeted ``config_entry_id`` resolved to no
      cover coordinator at all (a cover group, a Building Profile, or an entry
      that isn't currently loaded). Nothing ran, so ``findings`` is ``[]`` and
      ``report`` is a human-readable failure message rather than blank.

    A single targeted instance failing must not sink the whole response — the
    other targeted instances still come back (issue #1059).
    """
    hass: HomeAssistant = call.hass

    from . import _resolve_service_targets  # noqa: PLC0415

    coords_by_entry, unresolved = _resolve_service_targets(hass, call)

    language = hass.config.language or "en"
    labels = await hass.async_add_executor_job(load_troubleshoot_labels, language)

    entries: dict[str, dict] = {}
    for entry_id, unresolved_entry in unresolved.items():
        entries[entry_id] = _build_error_entry(
            entry_id, unresolved_entry.name, unresolved_entry.reason
        )

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
            reason = f"{type(exc).__name__}: {exc}"
            _LOGGER.warning(
                "get_troubleshooting: could not build results for %s: %s",
                entry_id,
                reason,
                exc_info=True,
            )
            entries[entry_id] = _build_error_entry(entry_id, name, reason)
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
