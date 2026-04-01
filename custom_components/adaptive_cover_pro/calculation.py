"""Generate values for all types of covers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import cast

import numpy as np
import pandas as pd
from numpy import cos, sin, tan
from numpy import radians as rad

from .config_context_adapter import ConfigContextAdapter
from .const import (
    CLIMATE_DEFAULT_TILT_ANGLE,
    CLIMATE_SUMMER_TILT_ANGLE,
    POSITION_CLOSED,
    WINDOW_DEPTH_GAMMA_THRESHOLD,
)
from .config_types import CoverConfig, HorizontalConfig, TiltConfig, VerticalConfig
from .enums import ClimateStrategy, CoverType, TiltMode
from .geometry import EdgeCaseHandler, SafetyMarginCalculator
from .position_utils import PositionConverter
from .sun import SunData


_COVER_CONFIG_FIELDS = frozenset(
    {
        "win_azi",
        "fov_left",
        "fov_right",
        "h_def",
        "sunset_pos",
        "sunset_off",
        "sunrise_off",
        "max_pos",
        "min_pos",
        "max_pos_sun_only",
        "min_pos_sun_only",
        "blind_spot_left",
        "blind_spot_right",
        "blind_spot_elevation",
        "blind_spot_on",
        "min_elevation",
        "max_elevation",
    }
)

_COVER_CONFIG_RENAMES = {
    "max_pos_bool": "max_pos_sun_only",
    "min_pos_bool": "min_pos_sun_only",
}

_VERT_CONFIG_FIELDS = frozenset({"distance", "h_win", "window_depth", "sill_height"})
_HORIZ_CONFIG_FIELDS = frozenset({"awn_length", "awn_angle"})
_TILT_CONFIG_FIELDS = frozenset({"slat_distance", "depth", "mode"})


@dataclass
class AdaptiveGeneralCover(ABC):
    """Collect common data."""

    logger: ConfigContextAdapter
    sol_azi: float
    sol_elev: float
    sun_data: SunData
    config: CoverConfig

    def __getattr__(self, name: str) -> object:
        """Route old flat field names to the appropriate config dataclass for reads.

        Note: __getattr__ is only called when normal lookup fails, so this
        won't intercept accesses to real dataclass fields (logger, sol_azi, etc.).
        """
        canonical = _COVER_CONFIG_RENAMES.get(name, name)
        if canonical in _COVER_CONFIG_FIELDS:
            # Access config via __dict__ to avoid infinite recursion
            config = object.__getattribute__(self, "config")
            return getattr(config, canonical)
        if canonical in _VERT_CONFIG_FIELDS:
            try:
                vert_config = object.__getattribute__(self, "vert_config")
                return getattr(vert_config, canonical)
            except AttributeError:
                pass
        if canonical in _TILT_CONFIG_FIELDS:
            try:
                tilt_config = object.__getattribute__(self, "tilt_config")
                return getattr(tilt_config, canonical)
            except AttributeError:
                pass
        if canonical in _HORIZ_CONFIG_FIELDS:
            try:
                horiz_config = object.__getattribute__(self, "horiz_config")
                return getattr(horiz_config, canonical)
            except AttributeError:
                pass
        msg = f"'{type(self).__name__}' object has no attribute '{name}'"
        raise AttributeError(msg)

    def __setattr__(self, name: str, value: object) -> None:
        """Route old flat field names to the appropriate config dataclass for writes."""
        canonical = _COVER_CONFIG_RENAMES.get(name, name)
        if canonical in _COVER_CONFIG_FIELDS:
            try:
                object.__setattr__(self.config, canonical, value)
            except AttributeError:
                # During __init__, self.config may not exist yet
                object.__setattr__(self, name, value)
            return
        if canonical in _VERT_CONFIG_FIELDS and hasattr(self, "vert_config"):
            object.__setattr__(self.vert_config, canonical, value)
            return
        if canonical in _TILT_CONFIG_FIELDS and hasattr(self, "tilt_config"):
            object.__setattr__(self.tilt_config, canonical, value)
            return
        if canonical in _HORIZ_CONFIG_FIELDS and hasattr(self, "horiz_config"):
            object.__setattr__(self.horiz_config, canonical, value)
            return
        object.__setattr__(self, name, value)

    def solar_times(self) -> tuple[datetime | None, datetime | None]:
        """Calculate when sun enters and exits window's field of view today.

        Uses today's solar position data to determine the time window when the sun
        is within the configured azimuth field of view, elevation limits, and outside
        the sunset/sunrise offset periods. Matches the same conditions used by
        direct_sun_valid so that "End Sun Time" accurately reflects when automatic
        sun tracking genuinely ends.

        Returns:
            Tuple of (start_time, end_time) as datetime objects. Returns (None, None)
            if sun never enters the field of view today.

        """
        df_today = pd.DataFrame(
            {
                "azimuth": self.sun_data.solar_azimuth,
                "elevation": self.sun_data.solar_elevation,
            }
        )
        solpos = df_today.set_index(self.sun_data.times)

        alpha = solpos["azimuth"]
        elev = solpos["elevation"]

        # Azimuth in FOV
        in_fov = (alpha - self.azi_min_abs) % 360 <= (
            self.azi_max_abs - self.azi_min_abs
        ) % 360

        # Elevation check — matches valid_elevation property logic
        if self.config.min_elevation is None and self.config.max_elevation is None:
            valid_elev = elev > 0
        elif self.config.min_elevation is None:
            valid_elev = elev <= self.config.max_elevation
        elif self.config.max_elevation is None:
            valid_elev = elev >= self.config.min_elevation
        else:
            valid_elev = (elev >= self.config.min_elevation) & (
                elev <= self.config.max_elevation
            )

        # Sunset/sunrise offset — exclude times within the offset windows.
        # Matches sunset_valid: True when after (sunset+offset) or before (sunrise+offset).
        # Convert series index to naive UTC for comparison with sun_data.sunset()/sunrise().
        sunset_utc = self.sun_data.sunset().replace(tzinfo=None)
        sunrise_utc = self.sun_data.sunrise().replace(tzinfo=None)
        offset_sunset = sunset_utc + timedelta(minutes=self.config.sunset_off)
        offset_sunrise = sunrise_utc + timedelta(minutes=self.config.sunrise_off)
        times_utc = solpos.index.tz_convert("UTC").tz_localize(None)
        in_sun_window = (times_utc >= offset_sunrise) & (times_utc <= offset_sunset)

        frame = in_fov & valid_elev & in_sun_window

        if solpos[frame].empty:
            return None, None
        else:
            return (
                solpos[frame].index[0].to_pydatetime(),
                solpos[frame].index[-1].to_pydatetime(),
            )

    @property
    def _get_azimuth_edges(self) -> tuple[int, int]:
        """Get absolute azimuth boundaries of window's field of view.

        Returns:
            Tuple of (min_azimuth, max_azimuth) in degrees (0-360).

        """
        return (self.azi_min_abs, self.azi_max_abs)

    @property
    def is_sun_in_blind_spot(self) -> bool:
        """Check if sun is currently within configured blind spot area.

        Blind spots are areas where the calculated position should not be used
        (e.g., architectural obstructions, tree coverage). Defined by horizontal
        angles (left/right) and optional elevation limit.

        Returns:
            True if sun is within blind spot area and blind spot enabled.
            False if blind spot not configured, disabled, or sun outside area.

        """
        if (
            self.config.blind_spot_left is not None
            and self.config.blind_spot_right is not None
            and self.config.blind_spot_on
        ):
            left_edge = self.config.fov_left - self.config.blind_spot_left
            right_edge = self.config.fov_left - self.config.blind_spot_right
            blindspot = (self.gamma <= left_edge) & (self.gamma >= right_edge)
            if self.config.blind_spot_elevation is not None:
                blindspot = blindspot & (
                    self.sol_elev <= self.config.blind_spot_elevation
                )
            self.logger.debug("Is sun in blind spot? %s", blindspot)
            return blindspot
        return False

    @property
    def azi_min_abs(self) -> int:
        """Calculate absolute minimum azimuth of window's field of view.

        Returns:
            Minimum azimuth angle in degrees (0-360).

        """
        azi_min_abs = (self.config.win_azi - self.config.fov_left + 360) % 360
        return azi_min_abs

    @property
    def azi_max_abs(self) -> int:
        """Calculate absolute maximum azimuth of window's field of view.

        Returns:
            Maximum azimuth angle in degrees (0-360).

        """
        azi_max_abs = (self.config.win_azi + self.config.fov_right + 360) % 360
        return azi_max_abs

    @property
    def gamma(self) -> float:
        """Calculate gamma (surface solar azimuth).

        Gamma is the horizontal angle between the window's perpendicular and the
        sun's position, normalized to -180 to +180 degrees. Positive values indicate
        sun to the right of window normal, negative to the left.

        Returns:
            Gamma angle in degrees (-180 to +180).

        """
        # surface solar azimuth
        gamma = (self.config.win_azi - self.sol_azi + 180) % 360 - 180
        return gamma

    @property
    def valid_elevation(self) -> bool:
        """Check if sun elevation is within configured limits.

        Used to exclude times when sun is too low (glare not an issue) or too high
        (directly overhead, no horizontal glare).

        Returns:
            True if sun elevation within configured min/max range (or no limits set).
            False if sun below horizon or outside configured limits.

        """
        if self.config.min_elevation is None and self.config.max_elevation is None:
            return self.sol_elev >= 0
        if self.config.min_elevation is None:
            return self.sol_elev <= self.config.max_elevation
        if self.config.max_elevation is None:
            return self.sol_elev >= self.config.min_elevation
        within_range = (
            self.config.min_elevation <= self.sol_elev <= self.config.max_elevation
        )
        self.logger.debug("elevation within range? %s", within_range)
        return within_range

    @property
    def valid(self) -> bool:
        """Check if sun is in front of window within field of view.

        Combines azimuth check (gamma within FOV) and elevation check to determine
        if sun is positioned where it could create glare. Does not consider blind
        spots or sunset offset.

        Returns:
            True if sun within configured azimuth field of view and valid elevation.
            False if sun behind window, outside FOV, or elevation invalid.

        """
        # Use configured FOV values directly without clipping
        azi_min = self.config.fov_left
        azi_max = self.config.fov_right

        # valid sun positions are those within the blind's azimuth range and above the horizon (FOV)
        valid = (
            (self.gamma < azi_min) & (self.gamma > -azi_max) & (self.valid_elevation)
        )
        self.logger.debug("Sun in front of window (ignoring blindspot)? %s", valid)
        return valid

    @property
    def sunset_valid(self) -> bool:
        """Check if current time is within sunset/sunrise offset period.

        Determines if default "sunset position" should be used instead of calculated
        position. Useful for returning covers to a preferred night position or
        accounting for late/early twilight.

        Returns:
            True if current time is after (sunset + offset) or before (sunrise + offset).
            False during normal daytime operation.

        """
        sunset = self.sun_data.sunset().replace(tzinfo=None)
        sunrise = self.sun_data.sunrise().replace(tzinfo=None)
        after_sunset = datetime.utcnow() > (
            sunset + timedelta(minutes=self.config.sunset_off)
        )
        before_sunrise = datetime.utcnow() < (
            sunrise + timedelta(minutes=self.config.sunrise_off)
        )
        self.logger.debug(
            "After sunset plus offset? %s", (after_sunset or before_sunrise)
        )
        return after_sunset or before_sunrise

    @property
    def default(self) -> float:
        """Get default position considering sunset offset.

        Returns:
            Sunset position if within sunset/sunrise offset period, otherwise
            normal default position.

        """
        default = self.config.h_def
        if self.sunset_valid and self.config.sunset_pos is not None:
            default = self.config.sunset_pos
        return default

    def fov(self) -> list[int]:
        """Get absolute azimuth boundaries of field of view.

        Returns:
            List of [min_azimuth, max_azimuth] in degrees (0-360).

        """
        return [self.azi_min_abs, self.azi_max_abs]

    @property
    def direct_sun_valid(self) -> bool:
        """Check if sun is directly in front with no exclusions.

        Combines all sun position checks to determine if calculated position should
        be used. Excludes blind spots and sunset offset periods.

        Returns:
            True if sun in FOV, not in blind spot, and not in sunset/sunrise offset.
            False otherwise.

        """
        result = self.valid and not self.sunset_valid and not self.is_sun_in_blind_spot
        self.logger.debug(
            "direct_sun_valid=%s (valid=%s, sunset_valid=%s, in_blind_spot=%s)",
            result,
            self.valid,
            self.sunset_valid,
            self.is_sun_in_blind_spot,
        )
        return result

    @property
    def control_state_reason(self) -> str:
        """Determine why the cover is tracking the sun or using the default position.

        Evaluates conditions in the same priority order as direct_sun_valid to
        provide a human-readable explanation for the current cover state. This
        helps users understand why the cover is in its current position.

        Returns:
            Reason string: "Direct Sun", "Default: FOV Exit", "Default: Elevation Limit",
            "Default: Sunset Offset", or "Default: Blind Spot".

        """
        if self.direct_sun_valid:
            return "Direct Sun"
        if self.sunset_valid:
            return "Default: Sunset Offset"
        if not self.valid:
            if not self.valid_elevation:
                return "Default: Elevation Limit"
            return "Default: FOV Exit"
        if self.is_sun_in_blind_spot:
            return "Default: Blind Spot"
        return "Default"

    @abstractmethod
    def calculate_position(self) -> float:
        """Calculate the position of the blind."""

    @abstractmethod
    def calculate_percentage(self) -> int:
        """Calculate percentage from position."""


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


@dataclass
class AdaptiveVerticalCover(AdaptiveGeneralCover):
    """Calculate state for Vertical blinds."""

    vert_config: VerticalConfig = None  # type: ignore[assignment]

    @property
    def distance(self) -> float:
        """Get distance from vert_config."""
        return self.vert_config.distance

    @property
    def h_win(self) -> float:
        """Get window height from vert_config."""
        return self.vert_config.h_win

    @property
    def window_depth(self) -> float:
        """Get window depth from vert_config."""
        return self.vert_config.window_depth

    @property
    def sill_height(self) -> float:
        """Get sill height from vert_config."""
        return self.vert_config.sill_height

    def _calculate_safety_margin(self, gamma: float, sol_elev: float) -> float:
        """Calculate angle-dependent safety margin multiplier (≥1.0).

        Delegates to SafetyMarginCalculator utility class.

        Args:
            gamma: Surface solar azimuth in degrees (-180 to 180)
            sol_elev: Sun elevation angle in degrees (0-90)

        Returns:
            Safety margin multiplier (1.0 to 1.45)

        """
        return SafetyMarginCalculator.calculate(gamma, sol_elev)

    def _handle_edge_cases(self) -> tuple[bool, float]:
        """Handle extreme angles with safe fallbacks.

        Delegates to EdgeCaseHandler utility class.

        Returns:
            Tuple of (is_edge_case: bool, position: float)
            - is_edge_case: True if edge case detected
            - position: Safe fallback position (only valid if is_edge_case=True)

        """
        return EdgeCaseHandler.check_and_handle(
            self.sol_elev, self.gamma, self.distance, self.h_win
        )

    def calculate_position(self) -> float:
        """Calculate blind height with enhanced geometric accuracy.

        Implements Phase 1 and Phase 2 geometric enhancements (v2.7.0+):

        Phase 1 (Automatic):
        - Edge case handling: Safe fallbacks for extreme sun angles (elev<2°, |gamma|>85°)
        - Safety margins: Angle-dependent multipliers (1.0-1.45x) for better sun blocking:
          * Gamma margin: 1.0 at 45° → 1.2 at 90° (smoothstep)
          * Low elevation: 1.0 at 10° → 1.15 at 0° (linear)
          * High elevation: 1.0 at 75° → 1.1 at 90° (linear)
        - Smooth transitions: Prevents jarring position changes

        Phase 2 (Optional):
        - Window depth: Accounts for window reveals/frames (0.0-0.5m)
        - Only active when window_depth > 0 and |gamma| > 10°
        - Adds horizontal offset: depth × sin(|gamma|)
        - Sill height: Accounts for windows not starting at floor level (0.0-3.0m)
        - Only active when sill_height > 0
        - Subtracts horizontal offset: sill_height / tan(elevation), capped at 0.05 minimum

        Returns:
            Blind height in meters (0 to h_win).

        """
        # Check edge cases first
        is_edge_case, edge_position = self._handle_edge_cases()
        if is_edge_case:
            self.logger.debug(
                "Vertical calc: edge case detected (elev=%.1f°, gamma=%.1f°) → %.3fm",
                self.sol_elev,
                self.gamma,
                edge_position,
            )
            self._last_calc_details = {
                "edge_case_detected": True,
                "safety_margin": 1.0,
                "effective_distance": self.distance,
                "window_depth_contribution": 0.0,
                "sill_height_offset": 0.0,
            }
            return edge_position

        # Account for window depth at angles (creates additional shadow)
        effective_distance = self.distance
        depth_contribution = 0.0
        if self.window_depth > 0 and abs(self.gamma) > WINDOW_DEPTH_GAMMA_THRESHOLD:
            # At angles, window depth creates additional horizontal offset
            depth_contribution = self.window_depth * sin(rad(abs(self.gamma)))
            effective_distance += depth_contribution

        # Account for window sill height (window not starting at floor)
        # Sill at height S means blind bottom is S meters above floor,
        # providing S/tan(elevation) meters of "free" horizontal protection.
        # Subtract from effective_distance to account for this.
        sill_offset = 0.0
        if self.sill_height > 0:
            sill_offset = self.sill_height / max(
                tan(rad(self.sol_elev)), 0.05
            )  # ~2.9° minimum
            effective_distance -= sill_offset

        # Base calculation: project glare zone to vertical blind height
        path_length = effective_distance / cos(rad(self.gamma))
        base_height = path_length * tan(rad(self.sol_elev))

        # Apply safety margin for extreme angles
        safety_margin = self._calculate_safety_margin(self.gamma, self.sol_elev)
        adjusted_height = base_height * safety_margin
        result = float(np.clip(adjusted_height, 0, self.h_win))

        self.logger.debug(
            "Vertical calc: elev=%.1f°, gamma=%.1f°, dist=%.3f→%.3f (depth=%.3f, sill=%.3f), "
            "base=%.3f, margin=%.3f, adjusted=%.3f, clipped=%.3f",
            self.sol_elev,
            self.gamma,
            self.distance,
            effective_distance,
            depth_contribution,
            sill_offset,
            base_height,
            safety_margin,
            adjusted_height,
            result,
        )
        # Store for diagnostic sensor access
        self._last_calc_details = {
            "edge_case_detected": False,
            "safety_margin": round(safety_margin, 4),
            "effective_distance": round(effective_distance, 4),
            "window_depth_contribution": round(depth_contribution, 4),
            "sill_height_offset": round(sill_offset, 4),
        }
        return result

    def calculate_percentage(self) -> float:
        """Convert blind height to percentage for Home Assistant.

        Converts calculated blind height (meters) to percentage (0-100) for
        cover entity position attribute.

        Returns:
            Position as percentage (0-100).

        """
        position = self.calculate_position()
        self.logger.debug(
            "Converting height to percentage: %s / %s * 100", position, self.h_win
        )
        return PositionConverter.to_percentage(position, self.h_win)


@dataclass
class AdaptiveHorizontalCover(AdaptiveVerticalCover):
    """Calculate state for Horizontal blinds."""

    horiz_config: HorizontalConfig = None  # type: ignore[assignment]

    @property
    def awn_length(self) -> float:
        """Get awning length from horiz_config."""
        return self.horiz_config.awn_length

    @property
    def awn_angle(self) -> float:
        """Get awning angle from horiz_config."""
        return self.horiz_config.awn_angle

    def calculate_position(self) -> float:
        """Calculate awning extension length using trigonometric projection.

        Converts vertical blind height to horizontal awning length using the law
        of sines based on sun elevation and awning mounting angle.

        Calculation:
        1. Get vertical blind position that would block sun
        2. Convert to gap above blind: h_win - vertical_position
        3. Project to awning length using triangle geometry:
           length = gap × sin(sun_angle) / sin(awning_closure_angle)

        Returns:
            Awning extension length in meters (may exceed awn_length if full
            extension insufficient to block sun).

        """
        awn_angle = 90 - self.awn_angle
        a_angle = 90 - self.sol_elev
        c_angle = 180 - awn_angle - a_angle

        vertical_position = super().calculate_position()
        length = ((self.h_win - vertical_position) * sin(rad(a_angle))) / sin(
            rad(c_angle)
        )
        self.logger.debug(
            "Horizontal calc: elev=%.1f°, gamma=%.1f°, awn_angle=%s°, "
            "vertical_pos=%.3f, length=%.3f",
            self.sol_elev,
            self.gamma,
            self.awn_angle,
            vertical_position,
            length,
        )
        # return np.clip(length, 0, self.awn_length)
        return length

    def calculate_percentage(self) -> float:
        """Convert awning extension to percentage for Home Assistant.

        Converts calculated awning length (meters) to percentage (0-100) for
        cover entity position attribute.

        Returns:
            Position as percentage (0-100).

        """
        return PositionConverter.to_percentage(
            self.calculate_position(), self.awn_length
        )


@dataclass
class AdaptiveTiltCover(AdaptiveGeneralCover):
    """Calculate state for tilted blinds."""

    tilt_config: TiltConfig = None  # type: ignore[assignment]

    @property
    def slat_distance(self) -> float:
        """Get slat distance from tilt_config."""
        return self.tilt_config.slat_distance

    @property
    def depth(self) -> float:
        """Get depth from tilt_config."""
        return self.tilt_config.depth

    @property
    def mode(self) -> TiltMode | str:
        """Get mode from tilt_config."""
        return self.tilt_config.mode

    @property
    def beta(self) -> float:
        """Calculate beta angle (incident angle of sun on slat plane).

        Beta represents the effective sun elevation angle as seen from the slat's
        perspective, accounting for both sun elevation and horizontal angle (gamma).
        Used in slat tilt calculation to block direct sun while maximizing view/light.

        Returns:
            Beta angle in radians.

        """
        beta = np.arctan(tan(rad(self.sol_elev)) / cos(rad(self.gamma)))
        return beta

    def calculate_position(self) -> float:
        """Calculate optimal slat tilt angle to block direct sun.

        Implements venetian blind optimization algorithm from:
        https://www.mdpi.com/1996-1073/13/7/1731

        Uses slat geometry (depth, spacing) and sun incident angle (beta) to
        calculate the tilt angle that blocks direct solar radiation while
        maximizing view and diffuse light.

        Supports two modes:
        - MODE1 (90°): Single-direction tilt (0° closed → 90° fully open)
        - MODE2 (180°): Bi-directional tilt (0° closed → 90° horizontal → 180° closed)

        Returns:
            Optimal slat tilt angle in degrees (0-90 for MODE1, 0-180 for MODE2).

        """
        beta = self.beta

        slat = 2 * np.arctan(
            (
                tan(beta)
                + np.sqrt(
                    (tan(beta) ** 2) - ((self.slat_distance / self.depth) ** 2) + 1
                )
            )
            / (1 + self.slat_distance / self.depth)
        )
        result = np.rad2deg(slat)

        self.logger.debug(
            "Tilt calc: elev=%.1f°, gamma=%.1f°, beta=%.4f rad, slat_angle=%.1f°",
            self.sol_elev,
            self.gamma,
            beta,
            result,
        )
        return result

    def calculate_percentage(self) -> float:
        """Convert slat tilt angle to percentage for Home Assistant.

        Converts calculated tilt angle (degrees) to percentage (0-100) for cover
        entity position attribute. Maximum degrees depends on mode:
        - MODE1: 0° (closed) → 90° (fully open) = 0-100%
        - MODE2: 0° (closed) → 180° (closed inverted) = 0-100%

        Returns:
            Position as percentage (0-100).

        """
        # 0 degrees is closed, 90 degrees is open (mode1), 180 degrees is closed (mode2)
        position = self.calculate_position()

        # Handle both string and TiltMode enum for backward compatibility
        if isinstance(self.mode, TiltMode):
            max_degrees = self.mode.max_degrees
        else:
            # Convert string to TiltMode
            mode_enum = TiltMode(self.mode)
            max_degrees = mode_enum.max_degrees

        return PositionConverter.to_percentage(position, max_degrees)
