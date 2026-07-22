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

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..const import DIAG_CACHE_KEY

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@dataclass(frozen=True, slots=True)
class DiagnosticsRead:
    """The outcome of a read-only diagnostics resolution.

    ``source`` is one of ``"coordinator"`` (live ``coordinator.data``),
    ``"built"`` (a read-only rebuild), ``"cache"`` (the stale snapshot), or
    ``"unavailable"`` (nothing answered / a rebuild raised). ``error`` carries a
    formatted message only when a rebuild raised.
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
