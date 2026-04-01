"""Cover state and climate orchestration classes.

Geometry classes have moved to engine/covers/:
  AdaptiveGeneralCover  → engine/covers/base.py
  AdaptiveVerticalCover → engine/covers/vertical.py
  AdaptiveHorizontalCover → engine/covers/horizontal.py
  AdaptiveTiltCover     → engine/covers/tilt.py

Re-exported here for backward compatibility with existing consumers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

import numpy as np

from .config_context_adapter import ConfigContextAdapter
from .const import (
    CLIMATE_DEFAULT_TILT_ANGLE,
    CLIMATE_SUMMER_TILT_ANGLE,
    POSITION_CLOSED,
)
from .engine.covers import (
    AdaptiveGeneralCover,
    AdaptiveHorizontalCover,
    AdaptiveTiltCover,
    AdaptiveVerticalCover,
)
from .enums import ClimateStrategy, CoverType, TiltMode
from .position_utils import PositionConverter

__all__ = [
    "AdaptiveGeneralCover",
    "AdaptiveHorizontalCover",
    "AdaptiveTiltCover",
    "AdaptiveVerticalCover",
    "ClimateCoverData",
    "ClimateCoverState",
    "NormalCoverState",
]


@dataclass
class NormalCoverState:
    """Compute state for normal operation."""

    cover: AdaptiveGeneralCover

    def get_state(self) -> int:
        """Calculate cover position using basic sun-tracking logic.

        Simple strategy for normal mode (no climate awareness):
        - If sun directly in front: Use calculated position to block glare
        - Otherwise: Use default position

        Applies configured min/max position limits before returning.

        Returns:
            Cover position as percentage (0-100).

        """
        self.cover.logger.debug("Determining normal position")
        dsv = self.cover.direct_sun_valid
        self.cover.logger.debug(
            "Sun directly in front of window & before sunset + offset? %s", dsv
        )
        if dsv:
            state = self.cover.calculate_percentage()
            # When sun is in the window, position must be at least 1% to prevent
            # open/close-only covers from closing while sun is still in the FOV.
            state = max(state, 1)
            self.cover.logger.debug(
                "Yes sun in window: using calculated percentage (%s)", state
            )
        else:
            state = self.cover.default
            self.cover.logger.debug("No sun in window: using default value (%s)", state)

        # Apply position limits using utility
        return PositionConverter.apply_limits(
            int(state),
            self.cover.config.min_pos,
            self.cover.config.max_pos,
            self.cover.config.min_pos_sun_only,
            self.cover.config.max_pos_sun_only,
            dsv,
        )


@dataclass
class ClimateCoverData:
    """Pure climate data container with computed properties.

    All Home Assistant state reads happen in ClimateProvider.read() before
    constructing this dataclass. The pre-read values are passed directly,
    making this class testable without mocking HA state.
    """

    logger: ConfigContextAdapter
    temp_low: float
    temp_high: float
    temp_switch: bool
    blind_type: str
    transparent_blind: bool
    temp_summer_outside: float
    # Pre-read values from ClimateProvider
    outside_temperature: float | str | None
    inside_temperature: float | str | None
    is_presence: bool
    is_sunny: bool
    lux_below_threshold: bool
    irradiance_below_threshold: bool

    @property
    def get_current_temperature(self) -> float | None:
        """Get current temperature based on configured priority.

        Uses temp_switch to determine whether outside or inside temperature should
        be used for climate mode decisions (is_winter, is_summer).

        Returns:
            Temperature as float. Priority: outside (if temp_switch enabled) >
            inside > None.

        """
        if self.temp_switch:
            if self.outside_temperature is not None:
                return float(self.outside_temperature)
        if self.inside_temperature is not None:
            return float(self.inside_temperature)
        return None

    @property
    def is_winter(self) -> bool:
        """Check if current temperature is below winter threshold.

        Winter mode enables covers to open fully for passive solar heating when
        the sun is present. Temperature is compared to temp_low threshold.

        Returns:
            True if current temperature < temp_low threshold. False if thresholds
            not configured or temperature unavailable.

        """
        if self.temp_low is not None and self.get_current_temperature is not None:
            is_it = self.get_current_temperature < self.temp_low
        else:
            is_it = False

        self.logger.debug(
            "is_winter(): current_temperature < temp_low: %s < %s = %s",
            self.get_current_temperature,
            self.temp_low,
            is_it,
        )
        return is_it

    @property
    def outside_high(self) -> bool:
        """Check if outdoor temperature is above summer threshold.

        Additional check for summer mode to ensure outdoor conditions warrant
        heat blocking. Only used in conjunction with is_summer check.

        Returns:
            True if outside temperature > temp_summer_outside threshold or
            threshold not configured. False if outdoor temp below threshold.

        """
        if self.temp_summer_outside is not None:
            temp = self.outside_temperature
            if temp is not None:
                return float(temp) > self.temp_summer_outside
        return True

    @property
    def is_summer(self) -> bool:
        """Check if current temperature is above summer threshold.

        Summer mode enables covers to close for heat blocking. Requires both
        indoor temperature above temp_high and outdoor temperature above
        temp_summer_outside to activate.

        Returns:
            True if current temperature > temp_high AND outside temperature >
            temp_summer_outside. False if thresholds not configured or temperatures
            below thresholds.

        """
        if self.temp_high is not None and self.get_current_temperature is not None:
            is_it = self.get_current_temperature > self.temp_high and self.outside_high
        else:
            is_it = False

        self.logger.debug(
            "is_summer(): current_temp > temp_high and outside_high?: %s > %s and %s = %s",
            self.get_current_temperature,
            self.temp_high,
            self.outside_high,
            is_it,
        )
        return is_it

    @property
    def lux(self) -> bool:
        """Check if illuminance is below lux threshold indicating low light.

        Returns the pre-read lux_below_threshold value from ClimateProvider.

        """
        return self.lux_below_threshold

    @property
    def irradiance(self) -> bool:
        """Check if solar irradiance is below threshold indicating low light.

        Returns the pre-read irradiance_below_threshold value from ClimateProvider.

        """
        return self.irradiance_below_threshold


@dataclass
class ClimateCoverState(NormalCoverState):
    """Compute state for climate control operation."""

    climate_data: ClimateCoverData
    climate_strategy: ClimateStrategy | None = field(default=None, init=False)

    def normal_type_cover(self) -> int:
        """Determine state for horizontal and vertical covers with climate logic.

        Routes to presence-aware or presence-unaware climate strategy based on
        occupancy detection. Used for cover_blind and cover_awning types.

        Returns:
            Cover position as percentage (0-100).

        """
        self.cover.logger.debug("Is presence? %s", self.climate_data.is_presence)

        if self.climate_data.is_presence:
            return self.normal_with_presence()

        return self.normal_without_presence()

    def normal_with_presence(self) -> int:
        """Determine state for horizontal and vertical covers with occupants present.

        Implements prioritized climate-aware decision tree optimized for comfort:
        1. Winter mode: Open fully when cold + sun present for passive solar heating
        2. Low light: Use default position when light insufficient or weather not sunny
        3. Summer + transparent: Close fully to block heat while maintaining view
        4. Normal: Use calculated position for glare control

        Returns:
            Cover position as percentage (0-100).

        """

        is_summer = self.climate_data.is_summer

        # Priority 1: Winter mode for solar heating
        # If it's winter and sun is in front, open fully regardless of light conditions
        if self.climate_data.is_winter and self.cover.valid:
            self.cover.logger.debug(
                "n_w_p(): Winter mode active with sun in window = use 100 for solar heating"
            )
            self.climate_strategy = ClimateStrategy.WINTER_HEATING
            return 100

        # Priority 2: Low light or non-sunny conditions
        # If it's not summer and light is low or weather is not sunny, use default
        if not is_summer and (
            self.climate_data.lux
            or self.climate_data.irradiance
            or not self.climate_data.is_sunny
        ):
            self.cover.logger.debug(
                "n_w_p(): Low light or not sunny = use default position"
            )
            self.climate_strategy = ClimateStrategy.LOW_LIGHT
            return round(self.cover.default)

        # Priority 3: Summer with transparent blinds
        if is_summer and self.climate_data.transparent_blind:
            self.cover.logger.debug(
                "n_w_p(): Summer with transparent blind = close to 0"
            )
            self.climate_strategy = ClimateStrategy.SUMMER_COOLING
            return 0

        # Priority 4: Normal glare calculation
        self.cover.logger.debug("n_w_p(): Use calculated position for glare control")
        self.climate_strategy = ClimateStrategy.GLARE_CONTROL
        return super().get_state()

    def normal_without_presence(self) -> int:
        """Determine state for horizontal and vertical covers without occupants.

        Energy-focused strategy when nobody home. Prioritizes passive solar heating
        in winter and heat blocking in summer over glare control. Ignores light
        conditions since occupant comfort not relevant.

        Returns:
            Cover position as percentage (0-100):
            - 100 (fully open) if winter + sun present for solar heating
            - 0 (fully closed) if summer + sun present for heat blocking
            - default position otherwise

        """
        if self.cover.valid:
            if self.climate_data.is_summer:
                self.cover.logger.debug(
                    "n_w/o_p(): Summer mode active with sun in window = close to 0"
                )
                self.climate_strategy = ClimateStrategy.SUMMER_COOLING
                return 0
            if self.climate_data.is_winter:
                self.cover.logger.debug(
                    "n_w/o_p(): Winter mode active with sun in window = use 100"
                )
                self.climate_strategy = ClimateStrategy.WINTER_HEATING
                return 100
        self.cover.logger.debug(
            "n_w/o_p(): Low light or not sunny = use default position"
        )
        self.climate_strategy = ClimateStrategy.LOW_LIGHT
        return round(self.cover.default)

    def tilt_with_presence(self, degrees: int) -> int:
        """Determine state for tilted blinds with occupants present.

        Climate-aware tilt strategy optimized for venetian/tilted blinds:
        - Summer: 45° tilt for heat blocking while allowing some light/view
        - Winter/Low light: Calculated position to block direct sun optimally
        - Default: 80° (mostly open) for maximum natural light

        Args:
            degrees: Maximum tilt angle (90° for MODE1, 180° for MODE2).

        Returns:
            Cover position as percentage (0-100) representing tilt angle.

        """

        # Priority 1: Climate-based decisions when sun is valid
        if self.cover.valid:
            # Summer: partial closure for heat blocking
            if self.climate_data.is_summer:
                self.cover.logger.debug(
                    "tilt_w_p(): Summer mode = %s degrees", CLIMATE_SUMMER_TILT_ANGLE
                )
                self.climate_strategy = ClimateStrategy.SUMMER_COOLING
                return round((CLIMATE_SUMMER_TILT_ANGLE / degrees) * 100)

            # Winter: Use calculated position for optimal light/heat
            if self.climate_data.is_winter:
                self.cover.logger.debug(
                    "tilt_w_p(): Winter mode = use calculated position"
                )
                self.climate_strategy = ClimateStrategy.WINTER_HEATING
                return super().get_state()

            # Low light or not sunny: Use calculated position
            if (
                self.climate_data.lux
                or self.climate_data.irradiance
                or not self.climate_data.is_sunny
            ):
                self.cover.logger.debug(
                    "tilt_w_p(): Low light or not sunny = use calculated position"
                )
                self.climate_strategy = ClimateStrategy.LOW_LIGHT
                return super().get_state()

        # Default: mostly open for natural light
        self.cover.logger.debug(
            "tilt_w_p(): Default = %s degrees", CLIMATE_DEFAULT_TILT_ANGLE
        )
        self.climate_strategy = ClimateStrategy.GLARE_CONTROL
        return round((CLIMATE_DEFAULT_TILT_ANGLE / degrees) * 100)

    def tilt_without_presence(self, degrees: int) -> int:
        """Determine state for tilted blinds without occupants.

        Energy-focused tilt strategy when nobody home:
        - Summer: Fully closed (0°) to block all heat
        - Winter + MODE2: Align slats parallel to sun beams (beta + 90°)
        - Winter + MODE1: Default 80° for passive heating
        - No sun: Use calculated/default position

        Args:
            degrees: Maximum tilt angle (90° for MODE1, 180° for MODE2).

        Returns:
            Cover position as percentage (0-100) representing tilt angle.

        """
        tilt_cover = cast(AdaptiveTiltCover, self.cover)
        beta = np.rad2deg(tilt_cover.beta)
        if tilt_cover.valid:
            if self.climate_data.is_summer:
                # block out all light in summer
                self.climate_strategy = ClimateStrategy.SUMMER_COOLING
                return POSITION_CLOSED
            # Check for MODE2 (handles both string and enum)
            is_mode2 = (
                tilt_cover.mode == TiltMode.MODE2
                or tilt_cover.mode == TiltMode.MODE2.value
            )
            if self.climate_data.is_winter and is_mode2:
                # parallel to sun beams, not possible with single direction
                self.climate_strategy = ClimateStrategy.WINTER_HEATING
                return round((beta + 90) / degrees * 100)
            self.climate_strategy = ClimateStrategy.GLARE_CONTROL
            return round((CLIMATE_DEFAULT_TILT_ANGLE / degrees) * 100)
        self.climate_strategy = ClimateStrategy.GLARE_CONTROL
        return super().get_state()

    def tilt_state(self) -> int:
        """Route tilt cover to appropriate climate strategy based on presence.

        Determines maximum tilt angle based on mode (MODE1: 90°, MODE2: 180°)
        and routes to presence-aware or presence-unaware tilt strategy.

        Returns:
            Cover position as percentage (0-100) representing tilt angle.

        """
        tilt_cover = cast(AdaptiveTiltCover, self.cover)
        # Check for MODE2 (handles both string and enum)
        is_mode2 = (
            tilt_cover.mode == TiltMode.MODE2 or tilt_cover.mode == TiltMode.MODE2.value
        )
        degrees = TiltMode.MODE2.max_degrees if is_mode2 else TiltMode.MODE1.max_degrees
        if self.climate_data.is_presence:
            return self.tilt_with_presence(degrees)
        return self.tilt_without_presence(degrees)

    def get_state(self) -> int:
        """Calculate cover position using climate-aware logic.

        Routes to appropriate strategy based on cover type:
        - Vertical/Horizontal: Uses normal_type_cover()
        - Tilt: Uses tilt_state()

        Each strategy considers temperature, presence, weather, and light conditions
        to optimize for comfort (when occupied) or energy efficiency (when empty).

        Applies configured min/max position limits before returning.

        Returns:
            Cover position as percentage (0-100).

        """
        result = self.normal_type_cover()
        # Check if cover type is tilt (handles both string and enum)
        is_tilt = (
            self.climate_data.blind_type == CoverType.TILT
            or self.climate_data.blind_type == CoverType.TILT.value
        )
        if is_tilt:
            result = self.tilt_state()

        # Apply position limits using utility
        final_result = PositionConverter.apply_limits(
            result,
            self.cover.config.min_pos,
            self.cover.config.max_pos,
            self.cover.config.min_pos_sun_only,
            self.cover.config.max_pos_sun_only,
            self.cover.direct_sun_valid,
        )

        if final_result != result:
            self.cover.logger.debug(
                "Climate state: Position limit applied (%s -> %s)", result, final_result
            )

        return final_result
