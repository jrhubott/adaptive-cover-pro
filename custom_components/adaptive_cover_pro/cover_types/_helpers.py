"""Shared helpers for cover-type policy summary rendering."""

from __future__ import annotations

from typing import Any

from ..const import CONF_DISTANCE, CONF_HEIGHT_WIN, CONF_SILL_HEIGHT, CONF_WINDOW_DEPTH
from ._summary_labels import GEOMETRY_LABELS_EN


def window_dimensions_lines(
    config: dict[str, Any], labels: dict[str, str] | None = None
) -> list[str]:
    """Render the "<H>m tall window, blocking sun <D>m..." block.

    Used by both ``BlindPolicy`` and ``VenetianPolicy`` since their geometry
    summary leads with the same window-dimensions sentence. ``labels`` overlays
    translated templates on the English base (``GEOMETRY_LABELS_EN``); ``None``
    or a missing key falls back to English.
    """
    L = {**GEOMETRY_LABELS_EN, **(labels or {})}
    h = config.get(CONF_HEIGHT_WIN)
    d = config.get(CONF_DISTANCE)
    depth = config.get(CONF_WINDOW_DEPTH) or 0
    sill = config.get(CONF_SILL_HEIGHT) or 0
    dim_parts: list[str] = []
    if h is not None:
        dim_parts.append(L["geometry.window.tall"].format(h=h))
    if d is not None:
        dim_parts.append(L["geometry.window.blocking_glass"].format(d=d))
    extras: list[str] = []
    if depth > 0:
        extras.append(L["geometry.window.reveal"].format(depth=depth))
    if sill > 0:
        extras.append(L["geometry.window.sill"].format(sill=sill))
    dim_str = ", ".join(dim_parts)
    if extras:
        dim_str += f" ({', '.join(extras)})"
    return [dim_str] if dim_str else []
