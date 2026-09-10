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
    CONF_TILT_MIN_REFLECTED_ELEVATION,
    CONF_TILT_MODE,
    CONF_WINDOW_DEPTH,
    CONF_WINDOW_WIDTH,
    TiltMode,
)
from ._summary_labels import GEOMETRY_LABELS_EN


def window_glass_area_m2(options: dict[str, Any]) -> float | None:
    """Glazed area in m² from the stored window height × width, or ``None``.

    The one place the rough-aperture area is computed (#1237). Only the cover
    types whose geometry step collects BOTH dimensions delegate to it — the
    rest inherit ``CoverTypePolicy.glass_area_m2``'s ``None``, which the
    solar-gain estimate reports as ``glass_area_unknown`` rather than guessing.

    ``None`` for any missing, non-numeric or non-positive dimension: an area of
    zero is not an area, and reporting 0 W as a fact would be worse than
    reporting "unknown".

    This is the ROUGH aperture, not the glazed area — frames typically eat
    10-25 %. Users who care set ``CONF_GLASS_AREA``, which wins at the
    diagnostics layer so the override works for every cover type, including
    the ones this helper cannot answer for.
    """
    try:
        height = float(options.get(CONF_HEIGHT_WIN))  # type: ignore[arg-type]
        width = float(options.get(CONF_WINDOW_WIDTH))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if height <= 0 or width <= 0:
        return None
    return height * width


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
    "horizontal at 0%" would state the opposite of what it means. The
    reflected-beam floor (issue #1282) hides at its own ``0`` sentinel for the
    same reason but is NOT mode-scoped — it constrains the solved angle on
    every tilt mode.
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
    # The reflected-beam floor (#1282) sits OUTSIDE the calibration block on
    # purpose: it constrains the solved slat angle on every mode, not just the
    # calibrated one. Like the mid-point it hides at its ``0`` sentinel —
    # "reflected sun kept at least 0° up" would read as a constraint where
    # there is none.
    if v := config.get(CONF_TILT_MIN_REFLECTED_ELEVATION):
        parts.append(L["geometry.slat.min_reflected_elevation"].format(v=v))
    return parts
