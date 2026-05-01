"""Weather override handler — safety retraction when weather conditions are severe."""

from __future__ import annotations

from ...enums import ControlMethod
from ..handler import OverrideHandler
from ..helpers import apply_minimum_mode, compute_raw_calculated_position
from ..types import PipelineResult, PipelineSnapshot


class WeatherOverrideHandler(OverrideHandler):
    """Retracts covers when any configured weather condition exceeds its threshold.

    Priority 90: between force_override (100) and motion_timeout (80).

    Conditions evaluated by WeatherManager (OR logic):
    - Wind speed sensor >= threshold (optionally filtered by wind direction)
    - Rain rate sensor >= threshold
    - IsRaining / IsWindy binary sensors
    - Severe weather binary sensors (hail, frost, storm)

    The manager also applies a configurable clear-delay timeout so covers
    stay retracted for a brief period after conditions clear, preventing
    rapid toggling in gusty or intermittent conditions.
    """

    name = "weather"
    priority = 90

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return override position when weather conditions are active."""
        if not snapshot.weather_override_active:
            return None
        configured = snapshot.weather_override_position
        bypass = snapshot.weather_bypass_auto_control
        raw = compute_raw_calculated_position(snapshot)
        pos, mode_note = apply_minimum_mode(
            configured, raw, enabled=snapshot.weather_override_min_mode
        )
        reason = f"weather override active — position {pos}%{mode_note}"
        if bypass:
            reason += " [bypasses automatic control]"
        return PipelineResult(
            position=pos,
            control_method=ControlMethod.WEATHER,
            reason=reason,
            bypass_auto_control=bypass,
            raw_calculated_position=raw,
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:  # noqa: ARG002
        """Reason when weather override is not active."""
        return "weather override not active"
