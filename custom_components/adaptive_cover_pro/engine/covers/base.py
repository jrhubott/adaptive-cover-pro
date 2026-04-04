"""Abstract base class for all adaptive cover calculations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from ...config_context_adapter import ConfigContextAdapter
from ...config_types import CoverConfig
from ...sun import SunData
from ..sun_geometry import SunGeometry

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

    @property
    def solar(self) -> SunGeometry:
        """Build a SunGeometry from current field values (always fresh)."""
        return SunGeometry(
            self.sol_azi,
            self.sol_elev,
            self.sun_data,
            self.config,
            self.logger,
        )

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

    # ------------------------------------------------------------------
    # Leaf properties delegated to SunGeometry
    # ------------------------------------------------------------------

    @property
    def azi_min_abs(self) -> int:
        """Delegate to SunGeometry.azi_min_abs."""
        return self.solar.azi_min_abs

    @property
    def azi_max_abs(self) -> int:
        """Delegate to SunGeometry.azi_max_abs."""
        return self.solar.azi_max_abs

    @property
    def gamma(self) -> float:
        """Delegate to SunGeometry.gamma."""
        return self.solar.gamma

    @property
    def valid_elevation(self) -> bool:
        """Delegate to SunGeometry.valid_elevation."""
        return self.solar.valid_elevation

    @property
    def sunset_valid(self) -> bool:
        """Delegate to SunGeometry.sunset_valid."""
        return self.solar.sunset_valid

    @property
    def is_sun_in_blind_spot(self) -> bool:
        """Delegate to SunGeometry.is_sun_in_blind_spot."""
        return self.solar.is_sun_in_blind_spot

    def solar_times(self) -> tuple[datetime | None, datetime | None]:
        """Delegate to SunGeometry.solar_times()."""
        return self.solar.solar_times()

    # ------------------------------------------------------------------
    # Compound properties (kept here so tests can patch individual parts)
    # ------------------------------------------------------------------

    @property
    def _get_azimuth_edges(self) -> tuple[int, int]:
        """Get absolute azimuth boundaries of window's field of view."""
        return (self.azi_min_abs, self.azi_max_abs)

    @property
    def valid(self) -> bool:
        """Check if sun is in front of window within field of view."""
        azi_min = self.config.fov_left
        azi_max = self.config.fov_right
        valid = (
            (self.gamma < azi_min) & (self.gamma > -azi_max) & (self.valid_elevation)
        )
        self.logger.debug("Sun in front of window (ignoring blindspot)? %s", valid)
        return valid

    def fov(self) -> list[int]:
        """Get absolute azimuth boundaries of field of view."""
        return [self.azi_min_abs, self.azi_max_abs]

    @property
    def direct_sun_valid(self) -> bool:
        """Check if sun is directly in front with no exclusions."""
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
        """Determine why the cover is tracking the sun or using the default position."""
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
