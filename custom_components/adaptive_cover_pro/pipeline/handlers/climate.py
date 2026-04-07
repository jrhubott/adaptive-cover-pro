"""Climate handler — temperature/season-aware position control.

Also contains ClimateCoverData and ClimateCoverState which were
previously in calculation.py. Moving them here keeps the full
climate strategy self-contained in one plugin handler file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

import numpy as np

from ...const import (
    CLIMATE_DEFAULT_TILT_ANGLE,
    CLIMATE_SUMMER_TILT_ANGLE,
    POSITION_CLOSED,
)
from ...engine.covers import AdaptiveTiltCover
from ...enums import ClimateStrategy, ControlMethod, CoverType, TiltMode
from ..handler import OverrideHandler
from ..helpers import apply_snapshot_limits, compute_raw_calculated_position, compute_solar_position
from ..types import PipelineResult, PipelineSnapshot

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
    blind_type: str
    transparent_blind: bool
    temp_summer_outside: float
    outside_temperature: float | str | None
    inside_temperature: float | str | None
    is_presence: bool
    is_sunny: bool
    lux_below_threshold: bool
    irradiance_below_threshold: bool
    winter_close_insulation: bool

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

    def get_state(self) -> int:
        """Calculate climate-aware position, applying position limits."""
        is_tilt = (
            self.climate_data.blind_type == CoverType.TILT
            or self.climate_data.blind_type == CoverType.TILT.value
        )
        result = self.tilt_state() if is_tilt else self.normal_type_cover()
        return apply_snapshot_limits(self.snapshot, result, sun_valid=self.cover.direct_sun_valid)

    def _solar_position(self) -> int:
        """Compute solar-tracked position with limits applied."""
        if self.cover.direct_sun_valid:
            return compute_solar_position(self.snapshot)
        return self.default_position

    def normal_type_cover(self) -> int:
        """Route horizontal/vertical covers based on presence."""
        if self.climate_data.is_presence:
            return self.normal_with_presence()
        return self.normal_without_presence()

    def normal_with_presence(self) -> int:
        """Climate strategy for normal covers with occupants present."""
        is_summer = self.climate_data.is_summer
        if self.climate_data.is_winter and self.cover.valid:
            self.climate_strategy = ClimateStrategy.WINTER_HEATING
            return 100
        # Close for insulation when in winter and sun not hitting window.
        if self.climate_data.is_winter and self.climate_data.winter_close_insulation:
            self.climate_strategy = ClimateStrategy.WINTER_INSULATION
            return 0
        # Low-light check applies in ALL seasons — if irradiance/lux indicates
        # no real sun (even in summer), use default position rather than closing.
        if (
            self.climate_data.lux
            or self.climate_data.irradiance
            or not self.climate_data.is_sunny
        ):
            self.climate_strategy = ClimateStrategy.LOW_LIGHT
            return self.default_position
        if is_summer and self.climate_data.transparent_blind:
            self.climate_strategy = ClimateStrategy.SUMMER_COOLING
            return 0
        self.climate_strategy = ClimateStrategy.GLARE_CONTROL
        return self._solar_position()

    def normal_without_presence(self) -> int:
        """Climate strategy for normal covers without occupants."""
        if self.cover.valid:
            # Low-light check overrides season logic — if irradiance/lux indicates
            # no real sun (even in summer), use default position rather than closing.
            if (
                self.climate_data.lux
                or self.climate_data.irradiance
                or not self.climate_data.is_sunny
            ):
                self.climate_strategy = ClimateStrategy.LOW_LIGHT
                return self.default_position
            if self.climate_data.is_summer:
                self.climate_strategy = ClimateStrategy.SUMMER_COOLING
                return 0
            if self.climate_data.is_winter:
                self.climate_strategy = ClimateStrategy.WINTER_HEATING
                return 100
        # Close for insulation when in winter and sun not hitting window.
        if self.climate_data.is_winter and self.climate_data.winter_close_insulation:
            self.climate_strategy = ClimateStrategy.WINTER_INSULATION
            return 0
        self.climate_strategy = ClimateStrategy.LOW_LIGHT
        return self.default_position

    def tilt_with_presence(self, degrees: int) -> int:
        """Climate strategy for tilt covers with occupants present."""
        if self.cover.valid:
            if self.climate_data.is_summer and self.climate_data.is_winter:
                # Conflicting season signals — fall through to glare control
                pass
            elif self.climate_data.is_winter:
                self.climate_strategy = ClimateStrategy.WINTER_HEATING
                return self._solar_position()
            elif (
                self.climate_data.lux
                or self.climate_data.irradiance
                or not self.climate_data.is_sunny
            ):
                # Low-light check applies in ALL seasons for tilt covers too
                self.climate_strategy = ClimateStrategy.LOW_LIGHT
                return self._solar_position()
            elif self.climate_data.is_summer:
                self.climate_strategy = ClimateStrategy.SUMMER_COOLING
                return round((CLIMATE_SUMMER_TILT_ANGLE / degrees) * 100)
        # Close for insulation when in winter and sun not hitting window.
        if self.climate_data.is_winter and self.climate_data.winter_close_insulation:
            self.climate_strategy = ClimateStrategy.WINTER_INSULATION
            return POSITION_CLOSED
        self.climate_strategy = ClimateStrategy.GLARE_CONTROL
        return round((CLIMATE_DEFAULT_TILT_ANGLE / degrees) * 100)

    def tilt_without_presence(self, degrees: int) -> int:
        """Climate strategy for tilt covers without occupants."""
        tilt_cover = cast(AdaptiveTiltCover, self.cover)
        beta = np.rad2deg(tilt_cover.beta)
        if tilt_cover.valid:
            # Low-light check applies before summer cooling
            if (
                self.climate_data.lux
                or self.climate_data.irradiance
                or not self.climate_data.is_sunny
            ):
                self.climate_strategy = ClimateStrategy.LOW_LIGHT
                return self._solar_position()
            if self.climate_data.is_summer:
                self.climate_strategy = ClimateStrategy.SUMMER_COOLING
                return POSITION_CLOSED
            is_mode2 = (
                tilt_cover.mode == TiltMode.MODE2
                or tilt_cover.mode == TiltMode.MODE2.value
            )
            if self.climate_data.is_winter and is_mode2:
                self.climate_strategy = ClimateStrategy.WINTER_HEATING
                return round((beta + 90) / degrees * 100)
            self.climate_strategy = ClimateStrategy.GLARE_CONTROL
            return round((CLIMATE_DEFAULT_TILT_ANGLE / degrees) * 100)
        # Close for insulation when in winter and sun not hitting window.
        if self.climate_data.is_winter and self.climate_data.winter_close_insulation:
            self.climate_strategy = ClimateStrategy.WINTER_INSULATION
            return POSITION_CLOSED
        self.climate_strategy = ClimateStrategy.GLARE_CONTROL
        return self._solar_position()

    def tilt_state(self) -> int:
        """Route tilt cover based on presence and mode."""
        tilt_cover = cast(AdaptiveTiltCover, self.cover)
        is_mode2 = (
            tilt_cover.mode == TiltMode.MODE2 or tilt_cover.mode == TiltMode.MODE2.value
        )
        degrees = TiltMode.MODE2.max_degrees if is_mode2 else TiltMode.MODE1.max_degrees
        if self.climate_data.is_presence:
            return self.tilt_with_presence(degrees)
        return self.tilt_without_presence(degrees)


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

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Run climate strategy and return position when climate mode is active."""
        if not snapshot.in_time_window:
            return None
        if not snapshot.climate_mode_enabled:
            return None
        if snapshot.climate_readings is None or snapshot.climate_options is None:
            return None

        opts = snapshot.climate_options
        r = snapshot.climate_readings

        climate_data = ClimateCoverData(
            temp_low=opts.temp_low,
            temp_high=opts.temp_high,
            temp_switch=opts.temp_switch,
            blind_type=snapshot.cover_type,
            transparent_blind=opts.transparent_blind,
            temp_summer_outside=opts.temp_summer_outside,
            outside_temperature=r.outside_temperature,
            inside_temperature=r.inside_temperature,
            is_presence=r.is_presence,
            is_sunny=r.is_sunny,
            lux_below_threshold=r.lux_below_threshold,
            irradiance_below_threshold=r.irradiance_below_threshold,
            winter_close_insulation=opts.winter_close_insulation,
        )

        climate_cover_state = ClimateCoverState(snapshot, climate_data)
        position = round(climate_cover_state.get_state())

        if climate_data.is_summer:
            method = ControlMethod.SUMMER
            season = "summer"
        elif climate_data.is_winter:
            method = ControlMethod.WINTER
            season = "winter"
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

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:
        """Reason when climate handler does not match."""
        if not snapshot.in_time_window:
            return "outside time window"
        if not snapshot.climate_mode_enabled:
            return "climate mode not enabled"
        return "climate readings or options unavailable"
