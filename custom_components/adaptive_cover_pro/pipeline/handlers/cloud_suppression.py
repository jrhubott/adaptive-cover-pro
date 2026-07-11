"""Cloud suppression handler — use default position when no real direct sun."""

from __future__ import annotations

from ...const import ControlMethod
from ..handler import OverrideHandler
from ..helpers import (
    apply_snapshot_limits,
    compute_default_position,
    compute_raw_calculated_position,
)
from ..types import PipelineResult, PipelineSnapshot


class CloudSuppressionHandler(OverrideHandler):
    """Uses default position when weather/lux/irradiance indicate no real direct sun.

    Priority 60: between manual_override (70) and climate (50).
    Evaluates ClimateReadings directly from the snapshot:
    - Not sunny (weather state not in sunny_conditions list)
    - OR lux below configured threshold
    - OR solar irradiance below configured threshold
    - OR cloud coverage above configured threshold
    """

    name = "cloud_suppression"
    priority = 60

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return default position when no direct sun is detected.

        The activate/deactivate decision now lives in ``CloudSuppressionManager``
        (issue #864): it owns the hysteresis latches + hold-time debounce and
        hands us a single resolved bool. This handler keeps only the FOV +
        time-window guards and the cloudy/default/sunset position selection. The
        guards run AHEAD of the resolved-bool gate so suppression can never fire
        while the sun is outside the window FOV (#417).
        """
        if not snapshot.in_time_window:
            return None
        if snapshot.climate_readings is None:
            return None
        if snapshot.climate_options is None:
            return None
        if not snapshot.climate_options.cloud_suppression_enabled:
            return None
        if not snapshot.cover.direct_sun_valid:
            return None
        if not snapshot.cloud_suppression_active:
            return None

        r = snapshot.climate_readings
        triggers = []
        if not r.is_sunny:
            triggers.append("weather not sunny")
        if r.lux_below_threshold:
            triggers.append("lux below threshold")
        if r.irradiance_below_threshold:
            triggers.append("irradiance below threshold")
        if r.cloud_coverage_above_threshold:
            triggers.append("cloud coverage above threshold")
        # The latch may be held by hysteresis / hold-time with no raw trigger
        # momentarily met — label that as a smoothing hold (issue #864).
        if not triggers:
            triggers.append("smoothing hold")

        cloudy = snapshot.climate_options.cloudy_position
        if snapshot.is_sunset_active:
            position = compute_default_position(snapshot)
            pos_label = "sunset position"
        elif cloudy is not None:
            position = apply_snapshot_limits(snapshot, cloudy, sun_valid=False)
            pos_label = "cloudy position"
        else:
            position = compute_default_position(snapshot)
            pos_label = "default position"

        trigger_detail = ", ".join(triggers)
        return PipelineResult(
            position=position,
            control_method=ControlMethod.CLOUD,
            reason=f"cloud/low-light suppression — {trigger_detail} → {pos_label} {position}%",
            raw_calculated_position=compute_raw_calculated_position(snapshot),
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:
        """Reason when cloud suppression is not active."""
        if not snapshot.in_time_window:
            return "outside time window"
        if not snapshot.cover.direct_sun_valid:
            return "cloud suppression skipped (sun outside acceptance angle)"
        return "cloud suppression inactive (direct sun present or feature disabled)"
