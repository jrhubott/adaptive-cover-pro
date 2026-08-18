"""Cloud suppression handler — use default position when no real direct sun."""

from __future__ import annotations

from ...const import ControlMethod, ReasonCode
from ...reason_i18n import Reason
from ..handler import OverrideHandler
from ..helpers import (
    apply_snapshot_limits,
    compute_default_position,
    compute_default_tilt,
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
        if snapshot.climate_extreme_heat_active:
            # Issue #1272: a narrow carve-out, not a priority reordering. Cloud
            # suppression (60) still outranks climate (50) for every other
            # branch; only when climate would produce EXTREME_HEAT this same
            # cycle does suppression stand down so the heat hold isn't briefly
            # overridden by the default/cloudy position.
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

        # Each branch states its own tilt (issue #1214). The two that resolve
        # the position from the effective default pair it with the effective
        # default/sunset tilt; the cloudy_position branch answers with a
        # configured override, so the slats hold their current angle — the
        # #1153 rule for hold-type winners. Provenance, not value: a
        # cloudy_position that happens to equal the default position (0 %
        # alongside a venetian's default_percentage of 0 %, the #1214
        # reporter's own config) is still an override.
        cloudy = snapshot.climate_options.cloudy_position
        tilt: int | None
        if snapshot.is_sunset_active:
            position = compute_default_position(snapshot)
            pos_label = Reason(ReasonCode.FRAGMENT_SUNSET_POSITION)
            tilt = compute_default_tilt(snapshot)
        elif cloudy is not None:
            position = apply_snapshot_limits(snapshot, cloudy, sun_valid=False)
            pos_label = Reason(ReasonCode.FRAGMENT_CLOUDY_POSITION)
            tilt = None
        else:
            position = compute_default_position(snapshot)
            pos_label = Reason(ReasonCode.FRAGMENT_DEFAULT_POSITION)
            tilt = compute_default_tilt(snapshot)

        return PipelineResult(
            position=position,
            control_method=ControlMethod.CLOUD,
            tilt=tilt,
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
        if snapshot.climate_extreme_heat_active:
            return Reason(ReasonCode.SKIP_CLOUD_DEFERRED_EXTREME_HEAT)
        return Reason(ReasonCode.SKIP_CLOUD_INACTIVE)
