"""Custom position handler — sensor-driven fixed cover positions."""

from __future__ import annotations

from ...enums import ControlMethod
from ..handler import OverrideHandler
from ..helpers import compute_raw_calculated_position
from ..types import PipelineResult, PipelineSnapshot


class CustomPositionHandler(OverrideHandler):
    """Return a configured position when any custom position sensor is active.

    Priority 77 — lower than manual override (80), higher than motion timeout (75).
    Up to 4 binary sensors can be configured, each with an associated cover
    position.  Sensors are evaluated in order (1 → 4); the first sensor that
    is "on" wins and its configured position is applied.  If all sensors are
    "off" (or no sensors are configured) the handler passes through.

    Intended use: Home Assistant automations toggle these sensors to drive
    the cover to scene-based or schedule-based positions without fighting the
    normal solar/climate pipeline.
    """

    name = "custom_position"
    priority = 77

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return the first active sensor's configured position, or None."""
        if not snapshot.custom_position_sensors:
            return None

        for entity_id, is_on, position in snapshot.custom_position_sensors:
            if is_on:
                return PipelineResult(
                    position=position,
                    control_method=ControlMethod.CUSTOM_POSITION,
                    reason=f"custom position active ({entity_id}) — position {position}%",
                    raw_calculated_position=compute_raw_calculated_position(snapshot),
                )

        return None

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:
        """Reason when no custom position sensor is active."""
        if not snapshot.custom_position_sensors:
            return "custom positions not configured"
        return "no custom position sensor active"
