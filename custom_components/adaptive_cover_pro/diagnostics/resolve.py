"""Read-only diagnostics access (issue #970, Phase 1).

Unifies the two divergent diagnostics reads (``diagnostics/__init__.py`` and
``services/diagnostics_service.py``) onto a single resolver that NEVER triggers a
coordinator update cycle. It deliberately does not call ``async_refresh`` — that
runs the full pipeline and can move a blind. The resolution order is:

1. the last completed cycle's payload (``coordinator.data.diagnostics``),
2. a read-only rebuild (``coordinator.build_diagnostic_data()``), and
3. the stale ``DIAG_CACHE_KEY`` snapshot cached in ``hass.data``.

Every read yields a :class:`DiagnosticsRead` (``payload`` + ``source`` +
``error``) so callers see which surface answered without re-deriving it. Unlike
the pure ``triage.py`` engine, this module sits at the HA boundary and may import
Home Assistant collaborators.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..const import DIAG_CACHE_KEY, CoverType
from ..helpers import check_cover_capabilities
from . import triage

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .triage import Finding

# Server-rendered wrapper lines for the read-only Troubleshoot step (issue
# #970, moved here for issue #1059 so the options-flow menu and the
# get_troubleshooting service share exactly one copy). The per-finding
# bullets are translated via the troubleshoot_i18n bundle; these two
# "envelope" states are not finding codes, so they are not in that bundle
# (its parity lock is strictly TriageCode-keyed) and live here instead.
TROUBLESHOOT_NO_ISSUES = (
    "✅ No configuration or runtime issues detected — everything looks correctly "
    "set up."
)
TROUBLESHOOT_UNAVAILABLE = (
    "ℹ️ Diagnostics aren't available yet for this cover — it may not have "
    "completed a first update cycle. Re-check once it has run at least once."
)
# A third envelope string, alongside the two above: the get_troubleshooting
# service's per-entry failure text for an instance where building the
# troubleshoot result raised entirely (a bad options shape, an unexpected
# exception, or an explicitly-targeted config entry with no cover coordinator
# — issue #1059 audit round 3, nit #3). ``{reason}`` is filled in at the call
# site with the human-readable form of whatever went wrong, so the same
# representation appears in both the ``report`` and ``error`` fields of the
# degraded entry rather than one carrying ``str(exc)`` and the other ``repr(exc)``.
TROUBLESHOOT_BUILD_FAILED = "⚠️ Troubleshooting could not run for this cover: {reason}"

# The vocabulary of resolve-layer "source" values (issue #1059 audit round 3,
# nit #4): every :class:`DiagnosticsRead` and :class:`TroubleshootResult` this
# module produces has a ``source`` drawn from exactly these four. The
# get_troubleshooting service response adds one more, service-layer-only value
# — ``"error"`` — that this module's dataclasses can never carry (see
# ``troubleshoot_service.async_handle_get_troubleshooting``'s docstring).
RESOLVE_READ_SOURCES: tuple[str, ...] = ("coordinator", "built", "cache", "unavailable")


@dataclass(frozen=True, slots=True)
class DiagnosticsRead:
    """The outcome of a read-only diagnostics resolution.

    ``source`` is one of :data:`RESOLVE_READ_SOURCES` — ``"coordinator"``
    (live ``coordinator.data``), ``"built"`` (a read-only rebuild), ``"cache"``
    (the stale snapshot), or ``"unavailable"`` (nothing answered / a rebuild
    raised). ``error`` carries a formatted message only when a rebuild raised.
    """

    payload: dict | None
    source: str
    error: str | None = None


def read_from_coordinator(coord) -> DiagnosticsRead:
    """Read a coordinator's diagnostics without ever running an update cycle.

    Prefers the last completed cycle's ``coord.data.diagnostics``; when
    ``coord.data`` is ``None`` (no completed cycle yet) falls back to a read-only
    ``build_diagnostic_data()``. A rebuild that raises is wrapped into a
    :class:`DiagnosticsRead` with a populated ``error`` (never propagates).
    """
    if coord.data is not None:
        return DiagnosticsRead(coord.data.diagnostics, "coordinator")
    try:
        return DiagnosticsRead(coord.build_diagnostic_data(), "built")
    except Exception as exc:  # noqa: BLE001 - a bad build must not break the read
        return DiagnosticsRead(None, "unavailable", f"diagnostics_unavailable: {exc!r}")


def read_diagnostics(hass: HomeAssistant, entry_id: str) -> DiagnosticsRead:
    """Resolve read-only diagnostics for ``entry_id``.

    Resolves the cover coordinator via :func:`..services.cover_coordinators`
    (groups/profiles are filtered out there) and delegates to
    :func:`read_from_coordinator`. With no live coordinator, falls back to the
    stale ``DIAG_CACHE_KEY`` snapshot (source ``"cache"``); when even that is
    absent the source is ``"unavailable"``.
    """
    from ..services import cover_coordinators  # noqa: PLC0415 - avoid import cycle

    coord = cover_coordinators(hass).get(entry_id)
    if coord is not None:
        return read_from_coordinator(coord)
    cached = hass.data.get(DIAG_CACHE_KEY, {}).get(entry_id, {}).get("diagnostics")
    if cached is not None:
        return DiagnosticsRead(cached, "cache")
    return DiagnosticsRead(None, "unavailable")


@dataclass(frozen=True, slots=True)
class TroubleshootResult:
    """The outcome of a troubleshoot triage run.

    ``findings`` are the raw :class:`~.triage.Finding` objects (code + params +
    severity + fix_step), ``report`` is the rendered bullet report (or one of
    the envelope strings when there is nothing to show), and ``source`` is
    copied verbatim from the :class:`DiagnosticsRead` that fed it — so it is
    always one of :data:`RESOLVE_READ_SOURCES`, never the
    ``get_troubleshooting`` service response's service-layer-only ``"error"``
    value, which this dataclass is never even constructed for (that case
    short-circuits before :func:`build_troubleshoot_result` returns anything).
    """

    findings: list[Finding]
    report: str
    source: str


def build_troubleshoot_result(
    hass: HomeAssistant,
    read: DiagnosticsRead,
    *,
    options: Mapping,
    sensor_type: str | None,
    labels: Mapping[str, str] | None,
) -> TroubleshootResult:
    """Run the triage engine against ``read`` and render the report.

    The single seam shared by ``OptionsFlowHandler.async_step_troubleshoot``
    and the ``get_troubleshooting`` service, so view-building/gating/rendering
    never diverge (issue #1059). ``read`` is resolved by the caller (via
    :func:`read_diagnostics` or :func:`read_from_coordinator`) — this function
    never triggers an update cycle itself. ``labels`` is the already-loaded
    translated ``troubleshoot_i18n`` bundle (or ``None`` for English); language
    resolution is the caller's concern.

    A falsy ``sensor_type`` falls back to ``CoverType.BLIND`` — the same
    default ``OptionsFlowHandler.__init__`` applies to ``self.sensor_type`` —
    so ``get_policy(None)`` never raises here. The fallback lives here, once,
    rather than in each caller (issue #1059 audit, nit A): the options flow
    already resolves a real ``sensor_type`` before this function ever runs, so
    the fallback is a no-op there; it only matters for the
    ``get_troubleshooting`` service, which passes a raw config-entry value
    through unchanged.

    When ``read.source == "unavailable"`` there is no runtime payload, so only
    the CONFIG rules run (they read options + capabilities alone) and the
    report leads with the "diagnostics aren't available" preamble, followed by
    any CONFIG findings that could still be evaluated. Otherwise every rule
    runs and the report is either the rendered findings or the "all good" text.
    """
    from ..cover_types import get_policy  # noqa: PLC0415

    sensor_type = sensor_type or CoverType.BLIND
    cap_map, _ = check_cover_capabilities(options, sensor_type, hass)
    policy = get_policy(sensor_type)
    view: dict = {
        "options": dict(options),
        "capabilities": cap_map,
        # Policy-derived capability requirements for rule 13
        # (COVER_FEATURE_MISMATCH): folded in here at the HA boundary where
        # get_policy lives, so the pure triage engine never branches on the
        # cover-type string (CODING_GUIDELINES § cover-type boundary).
        "axis_requirements": policy.axis_requirements(),
        # Policy-derived axis polarity for rule 26 (WEATHER_OVERRIDE_INVERTED),
        # folded in the same way and for the same reason as axis_requirements
        # above. ``axes`` is empty for the group/building_profile default
        # policy, which read_diagnostics already filters out of the live
        # troubleshoot path — but guard it anyway rather than index blindly.
        "open_blocks_sun": policy.axes[0].open_blocks_sun if policy.axes else None,
        **(read.payload or {}),
    }
    unavailable = read.source == "unavailable"
    findings = triage.run_triage(
        view, only=triage.RuleInput.CONFIG if unavailable else None
    )

    if unavailable:
        # Lead with the note that runtime checks need diagnostics, then list
        # any config findings that could still be evaluated.
        report = TROUBLESHOOT_UNAVAILABLE
        if findings:
            report = f"{report}\n\n{triage.render_report(findings, labels)}"
    elif findings:
        report = triage.render_report(findings, labels)
    else:
        report = TROUBLESHOOT_NO_ISSUES

    return TroubleshootResult(findings=findings, report=report, source=read.source)
