"""Cloud suppression handler — use default position when no real direct sun."""

from __future__ import annotations

from ...const import ControlMethod, ReasonCode
from ...reason_i18n import Reason
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
        triggers: list[Reason] = []
        if not r.is_sunny:
            triggers.append(Reason(ReasonCode.FRAGMENT_TRIGGER_NOT_SUNNY))
        if r.lux_below_threshold:
            triggers.append(Reason(ReasonCode.FRAGMENT_TRIGGER_LUX_BELOW))
        if r.irradiance_below_threshold:
            triggers.append(Reason(ReasonCode.FRAGMENT_TRIGGER_IRRADIANCE_BELOW))
        if r.cloud_coverage_above_threshold:
            triggers.append(Reason(ReasonCode.FRAGMENT_TRIGGER_CLOUD_ABOVE))
        # The latch may be held by hysteresis / hold-time with no raw trigger
        # momentarily met — label that as a smoothing hold (issue #864).
        if not triggers:
            triggers.append(Reason(ReasonCode.FRAGMENT_TRIGGER_SMOOTHING_HOLD))

        cloudy = snapshot.climate_options.cloudy_position
        if snapshot.is_sunset_active:
            position = compute_default_position(snapshot)
            pos_label = Reason(ReasonCode.FRAGMENT_SUNSET_POSITION)
        elif cloudy is not None:
            position = apply_snapshot_limits(snapshot, cloudy, sun_valid=False)
            pos_label = Reason(ReasonCode.FRAGMENT_CLOUDY_POSITION)
        else:
            position = compute_default_position(snapshot)
            pos_label = Reason(ReasonCode.FRAGMENT_DEFAULT_POSITION)

        return PipelineResult(
            position=position,
            control_method=ControlMethod.CLOUD,
            reason_payload=Reason(
                ReasonCode.CLOUD_SUPPRESSION,
                {
                    "triggers": tuple(triggers),
                    "pos_label": pos_label,
                    "position": position,
                },
            ),
            raw_calculated_position=compute_raw_calculated_position(snapshot),
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> Reason:
        """Reason when cloud suppression is not active."""
        if not snapshot.in_time_window:
            return Reason(ReasonCode.SKIP_OUTSIDE_WINDOW)
        if not snapshot.cover.direct_sun_valid:
            return Reason(ReasonCode.SKIP_CLOUD_SKIPPED)
        return Reason(ReasonCode.SKIP_CLOUD_INACTIVE)
