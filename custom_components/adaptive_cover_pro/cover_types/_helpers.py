"""Shared helpers for cover-type policy summary rendering and cover-type inference."""

from __future__ import annotations

from typing import Any

from ..const import (
    CONF_DISTANCE,
    CONF_HEIGHT_WIN,
    CONF_SILL_HEIGHT,
    CONF_WINDOW_DEPTH,
    SensorType,
)

INFERENCE_UNSUPPORTED = "_unsupported"


def infer_cover_type_from_device_class(device_class: str | None) -> str | None:
    """Map an HA cover device_class to one of the integration's SensorType values.

    Returns:
        - A SensorType value when the mapping is unambiguous.
        - ``INFERENCE_UNSUPPORTED`` for cover-domain classes the integration cannot drive
          (garage/gate/door/damper).
        - ``None`` when the class is missing or could reasonably map to multiple types
          (e.g. ``blind`` could be a roller blind or a venetian); the caller should
          ask the user to disambiguate.

    """
    if device_class is None:
        return None
    match device_class:
        case "awning":
            return SensorType.AWNING
        case "shutter" | "curtain" | "shade":
            return SensorType.BLIND
        case "blind":
            return None
        case "garage" | "gate" | "door" | "damper":
            return INFERENCE_UNSUPPORTED
    return None


def window_dimensions_lines(config: dict[str, Any]) -> list[str]:
    """Render the "<H>m tall window, blocking sun <D>m..." block.

    Used by both ``BlindPolicy`` and ``VenetianPolicy`` since their geometry
    summary leads with the same window-dimensions sentence.
    """
    h = config.get(CONF_HEIGHT_WIN)
    d = config.get(CONF_DISTANCE)
    depth = config.get(CONF_WINDOW_DEPTH) or 0
    sill = config.get(CONF_SILL_HEIGHT) or 0
    dim_parts: list[str] = []
    if h is not None:
        dim_parts.append(f"{h}m tall window")
    if d is not None:
        dim_parts.append(f"blocking sun {d}m from the glass")
    extras: list[str] = []
    if depth > 0:
        extras.append(f"reveal {depth}m")
    if sill > 0:
        extras.append(f"sill {sill}m")
    dim_str = ", ".join(dim_parts)
    if extras:
        dim_str += f" ({', '.join(extras)})"
    return [dim_str] if dim_str else []
