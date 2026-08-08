"""Shared helpers for cover-type policy summary rendering."""

from __future__ import annotations

from typing import Any

from ..const import (
    CONF_DISTANCE,
    CONF_HEIGHT_WIN,
    CONF_SILL_HEIGHT,
    CONF_TILT_ANGLE_0,
    CONF_TILT_ANGLE_100,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_HORIZONTAL_PERCENT,
    CONF_TILT_MODE,
    CONF_WINDOW_DEPTH,
    TiltMode,
)
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


def slat_geometry_parts(
    config: dict[str, Any], labels: dict[str, str] | None = None
) -> list[str]:
    """Render the slat depth / spacing / mode / calibration fragment.

    One geometry fragment reaches tilt-only, louvered roof (which inherits the
    tilt policy) and venetian — ``geometry_venetian_schema`` composes
    ``geometry_tilt_schema`` — so one renderer describes it, for the same
    reason ``window_dimensions_lines`` is shared by blind and venetian. The two
    policies previously carried byte-identical copies, and a field added to the
    shared schema reached both while only one summary learned about it.

    Returned as parts rather than a line: the tilt policy's whole summary IS
    this block, while the venetian's prefixes window dimensions and appends its
    own skip/mode lines.

    The endpoint calibration — the two angles and the optional three-point
    mid-point (issue #1222) — renders only on ``specify_angles``, because the
    presets carry their own scale and leave the stored values inert. The
    mid-point additionally hides at its ``0`` disabled sentinel: printing
    "horizontal at 0%" would state the opposite of what it means.
    """
    L = {**GEOMETRY_LABELS_EN, **(labels or {})}
    parts: list[str] = []
    if (v := config.get(CONF_TILT_DEPTH)) is not None:
        parts.append(L["geometry.slat.depth"].format(v=v))
    if (v := config.get(CONF_TILT_DISTANCE)) is not None:
        parts.append(L["geometry.slat.spacing"].format(v=v))
    if (v := config.get(CONF_TILT_MODE)) is not None:
        parts.append(L["geometry.slat.mode"].format(v=v))
    if config.get(CONF_TILT_MODE) == TiltMode.SPECIFY_ANGLES.value:
        if (v := config.get(CONF_TILT_ANGLE_0)) is not None:
            parts.append(L["geometry.slat.angle_0"].format(v=v))
        if (v := config.get(CONF_TILT_ANGLE_100)) is not None:
            parts.append(L["geometry.slat.angle_100"].format(v=v))
        if v := config.get(CONF_TILT_HORIZONTAL_PERCENT):
            parts.append(L["geometry.slat.horizontal_percent"].format(v=v))
    return parts
