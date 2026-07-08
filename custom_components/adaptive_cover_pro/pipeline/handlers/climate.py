"""Climate handler — temperature/season-aware position control.

Also contains ClimateCoverData and ClimateCoverState which were
previously in calculation.py. Moving them here keeps the full
climate strategy self-contained in one plugin handler file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np

from ...cover_types import get_policy
from ...cover_types.base import AXIS_NAME_TILT, CoverTypePolicy
from ...engine.covers import AdaptiveTiltCover
from ...const import ClimateInactiveReason, ClimateStrategy, ControlMethod
from ..handler import OverrideHandler
from ..helpers import (
    apply_snapshot_limits,
    compute_raw_calculated_position,
    compute_solar_position,
)
from ..types import PipelineResult, PipelineSnapshot
from .climate_modes import (
    NORMAL_WITH_PRESENCE,
    NORMAL_WITHOUT_PRESENCE,
    TILT_WITH_PRESENCE,
    TILT_WITHOUT_PRESENCE,
    ClimateContext,
    ClimateRule,
    evaluate_rules,
)

# ---------------------------------------------------------------------------
# Climate data container (moved from calculation.py)
# ---------------------------------------------------------------------------


@dataclass
class ClimateCoverData:
    """Pure climate data container with computed properties.

    All Home Assistant state reads happen in ClimateProvider.read() before
    constructing this dataclass.
    """

    temp_low: float
    temp_high: float
    temp_switch: bool
    policy: CoverTypePolicy
    transparent_blind: bool
    temp_summer_outside: float
    outside_temperature: float | str | None
    inside_temperature: float | str | None
    is_presence: bool
    is_sunny: bool
    lux_below_threshold: bool
    irradiance_below_threshold: bool
    winter_close_insulation: bool
    summer_close_bypass_sun_floor: bool = False
    cloud_coverage_above_threshold: bool = False
    # Extreme-heat mode (issue #766). ``temp_extreme_heat`` None = feature off.
    temp_extreme_heat: float | None = None
    extreme_heat_position: int | None = None

    @property
    def get_current_temperature(self) -> float | None:
        """Get temperature based on configured source (outside/inside)."""
        if self.temp_switch and self.outside_temperature is not None:
            try:
                return float(self.outside_temperature)
            except (ValueError, TypeError):
                return None
        if self.inside_temperature is not None:
            try:
                return float(self.inside_temperature)
            except (ValueError, TypeError):
                return None
        return None

    @property
    def is_winter(self) -> bool:
        """True when current temperature is below temp_low."""
        if self.temp_low is not None and self.get_current_temperature is not None:
            return self.get_current_temperature < self.temp_low
        return False

    @property
    def outside_high(self) -> bool:
        """True when outdoor temperature exceeds temp_summer_outside."""
        if (
            self.temp_summer_outside is not None
            and self.outside_temperature is not None
        ):
            try:
                return float(self.outside_temperature) > self.temp_summer_outside
            except (ValueError, TypeError):
                return True
        return True

    @property
    def is_summer(self) -> bool:
        """True when current temperature is above temp_high AND outside_high."""
        if self.temp_high is not None and self.get_current_temperature is not None:
            return self.get_current_temperature > self.temp_high and self.outside_high
        return False

    @property
    def is_extreme_heat(self) -> bool:
        """True when the OUTSIDE temperature exceeds the extreme-heat threshold.

        Keys on ``outside_temperature`` (mirroring ``outside_high``), NOT
        ``get_current_temperature`` — extreme heat is about the outdoor load and
        must never flip to the inside sensor via ``temp_switch`` (issue #766).
        Returns False when the feature is off (threshold None), the outside
        reading is unavailable (None), or either value is non-numeric.
        """
        if self.temp_extreme_heat is None or self.outside_temperature is None:
            return False
        try:
            return float(self.outside_temperature) > float(self.temp_extreme_heat)
        except (ValueError, TypeError):
            return False

    @property
    def lux(self) -> bool:
        """Return whether lux is below threshold."""
        return self.lux_below_threshold

    @property
    def irradiance(self) -> bool:
        """Return whether irradiance is below threshold."""
        return self.irradiance_below_threshold


# ---------------------------------------------------------------------------
# Climate state calculator (moved from calculation.py)
# ---------------------------------------------------------------------------


@dataclass
class ClimateCoverState:
    """Compute state for climate control operation."""

    snapshot: PipelineSnapshot
    climate_data: ClimateCoverData
    climate_strategy: ClimateStrategy | None = field(default=None, init=False)

    @property
    def cover(self):
        """Convenience accessor for the cover engine object."""
        return self.snapshot.cover

    @property
    def default_position(self) -> int:
        """Effective default position from the snapshot."""
        return self.snapshot.default_position

    def get_state(self) -> int | None:
        """Calculate climate-aware position, applying position limits.

        Returns None when the strategy is GLARE_CONTROL for normal covers,
        signalling that the pipeline should fall through to GlareZone/Solar.
        """
        # Tilt-only covers are the ones whose primary axis is the slat axis;
        # blind/awning have a position primary, venetian has position primary
        # with a tilt secondary. The policy describes this without the climate
        # handler having to know any cover-type identifiers.
        is_tilt = self.climate_data.policy.axes[0].name == AXIS_NAME_TILT
        result = self.tilt_state() if is_tilt else self.normal_type_cover()
        if result is None:
            return None
        # Summer cooling fires only when the sun is in the window's FOV
        # (cover_valid is required by the rule predicate).  Pass direct_sun_valid
        # so that "sun-tracking-only" position limits — specifically min_position
        # with enable_min_position=True — are honoured during summer close
        # (issue #631).  Other climate states (winter heating, low-light, glare)
        # retain sun_valid=False to preserve existing behaviour: winter heating
        # should not be capped by sun-only max position limits (regression #105).
        sun_valid = self.cover.direct_sun_valid and self.climate_data.is_summer
        # When summer_close_bypass_sun_floor is set, summer close ignores the
        # sun-in-FOV min floor (min_position_sun_tracking) and reaches the global
        # min_position instead (issue #689).  Only the min floor is affected — the
        # direct_sun_valid-driven max clamp stays intact (winter/#105 untouched).
        # The flag is harmless outside summer because the floor only engages when
        # sun_valid is True, which already requires is_summer.
        return apply_snapshot_limits(
            self.snapshot,
            result,
            sun_valid=sun_valid,
            suppress_sun_tracking_min=self.climate_data.summer_close_bypass_sun_floor,
        )

    def _solar_position(self) -> int:
        """Compute solar-tracked position with limits applied."""
        if self.cover.direct_sun_valid:
            return compute_solar_position(self.snapshot)
        return self.default_position

    def normal_type_cover(self) -> int | None:
        """Route horizontal/vertical covers based on presence."""
        if self.climate_data.is_presence:
            return self.normal_with_presence()
        return self.normal_without_presence()

    def _build_context(self, *, tilt: bool) -> ClimateContext:
        """Bundle data + (for tilt covers) precomputed slat geometry for the rules.

        ``gamma_deg``/``beta_deg`` are computed once here for tilt covers — the
        same ``float(tilt_cover.gamma)`` / ``np.rad2deg(tilt_cover.beta)`` the
        original routers used, evaluated regardless of validity to match the
        prior behavior.
        """
        gamma_deg = 0.0
        beta_deg = 0.0
        if tilt:
            tilt_cover = cast(AdaptiveTiltCover, self.cover)
            # SunGeometry.gamma is already in degrees; pass it through unconverted.
            gamma_deg = float(tilt_cover.gamma)
            beta_deg = float(np.rad2deg(tilt_cover.beta))
        return ClimateContext(
            data=self.climate_data,
            cover=self.cover,
            default_position=self.default_position,
            solar_position=self._solar_position,
            gamma_deg=gamma_deg,
            beta_deg=beta_deg,
        )

    def _run(self, rules: tuple[ClimateRule, ...], *, tilt: bool) -> int | None:
        """Evaluate a rule table, record the chosen strategy, return its position."""
        strategy, position = evaluate_rules(rules, self._build_context(tilt=tilt))
        self.climate_strategy = strategy
        return position

    def normal_with_presence(self) -> int | None:
        """Climate strategy for normal covers with occupants present.

        Returns None for the GLARE_CONTROL case — the pipeline falls through
        to GlareZoneHandler (priority 45) then SolarHandler (priority 40).
        """
        return self._run(NORMAL_WITH_PRESENCE, tilt=False)

    def normal_without_presence(self) -> int:
        """Climate strategy for normal covers without occupants."""
        return cast(int, self._run(NORMAL_WITHOUT_PRESENCE, tilt=False))

    def tilt_with_presence(self) -> int:
        """Climate strategy for tilt covers with occupants present."""
        return cast(int, self._run(TILT_WITH_PRESENCE, tilt=True))

    def tilt_without_presence(self) -> int:
        """Climate strategy for tilt covers without occupants."""
        return cast(int, self._run(TILT_WITHOUT_PRESENCE, tilt=True))

    def tilt_state(self) -> int:
        """Route tilt cover based on presence.

        Cover-type-specific mode handling lives inside the helper
        (``TiltPolicy.climate_tilt_percentage``) — this router no longer
        needs to know about MODE1 vs MODE2 max-degrees.
        """
        if self.climate_data.is_presence:
            return self.tilt_with_presence()
        return self.tilt_without_presence()


# ---------------------------------------------------------------------------
# Inactive-reason slug derivation — shared by ClimateHandler and sensor.py
# ---------------------------------------------------------------------------

# Maps each ClimateInactiveReason slug to a human-readable prose string.
# This is the single source of truth so that describe_skip and the slug helper
# stay in sync without duplicating branch logic.
_SLUG_TO_PROSE: dict[str, str] = {
    ClimateInactiveReason.MODE_OFF: "climate mode not enabled",
    ClimateInactiveReason.OUTSIDE_TIME_WINDOW: "outside time window",
    ClimateInactiveReason.READINGS_UNAVAILABLE: "climate readings or options unavailable",
    ClimateInactiveReason.THRESHOLDS_NOT_MET: "deferred glare-control to solar/glare handlers",
    # ACTIVE and OTHER_MODE_ACTIVE have no describe_skip prose (handler wins / outprioritized)
}

# Reverse map for deriving a slug from a describe_skip prose string (sensor.py use).
# Built once at module load from _SLUG_TO_PROSE — stays in sync automatically.
_PROSE_TO_SLUG: dict[str, str] = {v: k for k, v in _SLUG_TO_PROSE.items()}


def inactive_reason(
    snapshot: PipelineSnapshot,
    pipeline_result: PipelineResult | None,
) -> str:
    """Derive a ClimateInactiveReason slug from pipeline state.

    Priority order mirrors the _build_climate_data gating so the slug and
    the describe_skip prose always agree:

        not in_time_window            → OUTSIDE_TIME_WINDOW
        not climate_mode_enabled      → MODE_OFF
        readings/options unavailable  → READINGS_UNAVAILABLE
        climate step outprioritized   → OTHER_MODE_ACTIVE
        climate step deferred         → THRESHOLDS_NOT_MET
        climate handler won           → ACTIVE
    """
    # Time-window check takes precedence over mode-enabled check (mirrors
    # _build_climate_data which returns None for outside-window first).
    if not snapshot.in_time_window:
        return ClimateInactiveReason.OUTSIDE_TIME_WINDOW

    if not snapshot.climate_mode_enabled:
        return ClimateInactiveReason.MODE_OFF

    if snapshot.climate_readings is None or snapshot.climate_options is None:
        return ClimateInactiveReason.READINGS_UNAVAILABLE

    # Inspect the decision trace for the climate step.
    if pipeline_result is not None:
        for step in pipeline_result.decision_trace:
            if step.handler == "climate":
                if step.matched:
                    return ClimateInactiveReason.ACTIVE
                if step.reason.startswith("outprioritized by"):
                    return ClimateInactiveReason.OTHER_MODE_ACTIVE
                # Present but not matched and not outprioritized → deferred
                return ClimateInactiveReason.THRESHOLDS_NOT_MET

    # No climate step in trace (e.g. climate deferred without a trace entry)
    return ClimateInactiveReason.THRESHOLDS_NOT_MET


def inactive_reason_from_result(
    pipeline_result: PipelineResult | None,
) -> str:
    """Derive a ClimateInactiveReason slug from a PipelineResult alone.

    Used by sensor.py where no PipelineSnapshot is in scope.  Inspects the
    decision_trace climate step and maps via _PROSE_TO_SLUG (the reverse of
    the _SLUG_TO_PROSE map that describe_skip uses) — single source of truth.

    Falls back to MODE_OFF when the result is None or has no climate step.
    """
    if pipeline_result is None:
        return ClimateInactiveReason.MODE_OFF

    for step in pipeline_result.decision_trace:
        if step.handler == "climate":
            if step.matched:
                return ClimateInactiveReason.ACTIVE
            if step.reason.startswith("outprioritized by"):
                return ClimateInactiveReason.OTHER_MODE_ACTIVE
            # Map the prose reason back to a slug via the reverse map.
            return _PROSE_TO_SLUG.get(
                step.reason, ClimateInactiveReason.THRESHOLDS_NOT_MET
            )

    # No climate step in trace → climate was never considered → mode off or not applicable.
    return ClimateInactiveReason.MODE_OFF


# ---------------------------------------------------------------------------
# ClimateHandler
# ---------------------------------------------------------------------------


class ClimateHandler(OverrideHandler):
    """Return the climate-calculated position when climate mode is enabled.

    Priority 50 — lower than override handlers, higher than solar/default.
    Builds ClimateCoverData from ClimateReadings + ClimateOptions, runs
    ClimateCoverState strategy, and returns the computed position.
    The control method is set based on the climate season:
    - SUMMER when over the high-temp threshold (heat blocking)
    - WINTER when under the low-temp threshold (solar heat gain)
    - SOLAR for all other climate-mode states (glare control)
    """

    name = "climate"
    priority = 50

    def _build_climate_data(
        self, snapshot: PipelineSnapshot
    ) -> ClimateCoverData | None:
        """Build ClimateCoverData from the snapshot, or None when not applicable.

        Single source of truth — both evaluate() and contribute() delegate here
        so ClimateCoverData is constructed in exactly one place.
        """
        if not snapshot.in_time_window:
            return None
        if not snapshot.climate_mode_enabled:
            return None
        if snapshot.climate_readings is None or snapshot.climate_options is None:
            return None

        opts = snapshot.climate_options
        r = snapshot.climate_readings
        return ClimateCoverData(
            temp_low=opts.temp_low,
            temp_high=opts.temp_high,
            temp_switch=opts.temp_switch,
            policy=snapshot.policy or get_policy(snapshot.cover_type),
            transparent_blind=opts.transparent_blind,
            temp_summer_outside=opts.temp_summer_outside,
            outside_temperature=r.outside_temperature,
            inside_temperature=r.inside_temperature,
            is_presence=r.is_presence,
            is_sunny=r.is_sunny,
            lux_below_threshold=r.lux_below_threshold,
            irradiance_below_threshold=r.irradiance_below_threshold,
            winter_close_insulation=opts.winter_close_insulation,
            summer_close_bypass_sun_floor=opts.summer_close_bypass_sun_floor,
            cloud_coverage_above_threshold=r.cloud_coverage_above_threshold,
            temp_extreme_heat=opts.temp_extreme_heat,
            extreme_heat_position=opts.extreme_heat_position,
        )

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Run climate strategy and return position when climate mode is active."""
        climate_data = self._build_climate_data(snapshot)
        if climate_data is None:
            return None

        climate_cover_state = ClimateCoverState(snapshot, climate_data)
        raw_position = climate_cover_state.get_state()

        if raw_position is None:
            return None

        position = round(raw_position)

        if climate_cover_state.climate_strategy == ClimateStrategy.EXTREME_HEAT:
            # Checked FIRST: extreme heat is an all-day force-hold that pre-empts
            # every season strategy (issue #766). The hold position already rode
            # get_state()/apply_snapshot_limits above — this branch only sets the
            # label + control method, never a short-circuit before get_state().
            method = ControlMethod.EXTREME_HEAT
            season = "extreme heat"
        elif climate_data.is_summer:
            method = ControlMethod.SUMMER
            season = "summer"
        elif climate_data.is_winter:
            method = ControlMethod.WINTER
            season = "winter"
        elif climate_cover_state.climate_strategy == ClimateStrategy.LOW_LIGHT:
            # Low-light / no-sun branch — the cover returns to its default
            # position rather than tracking the sun.  Emitting SOLAR here
            # would cause VenetianPolicy to synthesise a tilt from the
            # still-drifting azimuth even when the sun has set (issue #33).
            method = ControlMethod.DEFAULT
            season = "glare control (low light)"
        else:
            method = ControlMethod.SOLAR
            season = "glare control"

        return PipelineResult(
            position=position,
            control_method=method,
            reason=f"climate mode active ({season}) — position {position}%",
            climate_state=position,
            climate_strategy=climate_cover_state.climate_strategy,
            climate_data=climate_data,
            raw_calculated_position=compute_raw_calculated_position(snapshot),
        )

    def contribute(self, snapshot: PipelineSnapshot) -> dict[str, Any]:
        """Surface climate_data on the winner's result even when evaluate() deferred.

        Called by the registry after evaluation so that GLARE_CONTROL defers
        (evaluate() returns None) still populate climate diagnostics on the
        winning SolarHandler/GlareZoneHandler result.
        """
        climate_data = self._build_climate_data(snapshot)
        if climate_data is None:
            return {}
        return {"climate_data": climate_data}

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:
        """Reason when climate handler does not match.

        Delegates to the inactive_reason slug helper (single source of truth)
        and maps each slug to its prose via _SLUG_TO_PROSE.  The other_mode_active
        and active slugs never reach describe_skip (those paths win the handler),
        so their prose entries are omitted from the map.
        """
        slug = inactive_reason(snapshot, pipeline_result=None)
        return _SLUG_TO_PROSE.get(slug, slug)
