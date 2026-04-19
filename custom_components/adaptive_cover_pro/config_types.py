"""Typed configuration dataclasses for cover calculations."""

from __future__ import annotations

from dataclasses import dataclass

from .enums import TiltMode


@dataclass
class GlareZone:
    """A single glare protection zone on the floor.

    Coordinates are relative to the window centre projected onto the floor:
      x = along the wall (positive = right when facing window from inside), metres
      y = into the room (perpendicular to window), metres — must be positive
      radius = zone radius, metres
    """

    name: str
    x: float
    y: float
    radius: float


@dataclass
class GlareZonesConfig:
    """All glare zone configuration for a vertical cover."""

    zones: list[GlareZone]
    window_width: float  # metres — used to check if a sun ray can reach a zone


@dataclass
class CoverConfig:
    """Common configuration for all cover types."""

    win_azi: int
    fov_left: int
    fov_right: int
    h_def: int
    sunset_pos: int | None
    sunset_off: int
    sunrise_off: int
    max_pos: int
    min_pos: int
    max_pos_sun_only: bool  # enable_max_position
    min_pos_sun_only: bool  # enable_min_position
    blind_spot_left: int | None
    blind_spot_right: int | None
    blind_spot_elevation: int | None
    blind_spot_on: bool
    min_elevation: int | None
    max_elevation: int | None

    @classmethod
    def from_options(cls, options: dict) -> CoverConfig:
        """Build a CoverConfig from a raw options/config dict (CONF_* keys)."""
        from .const import (
            CONF_AZIMUTH,
            CONF_BLIND_SPOT_ELEVATION,
            CONF_BLIND_SPOT_LEFT,
            CONF_BLIND_SPOT_RIGHT,
            CONF_DEFAULT_HEIGHT,
            CONF_ENABLE_BLIND_SPOT,
            CONF_ENABLE_MAX_POSITION,
            CONF_ENABLE_MIN_POSITION,
            CONF_FOV_LEFT,
            CONF_FOV_RIGHT,
            CONF_MAX_ELEVATION,
            CONF_MAX_POSITION,
            CONF_MIN_ELEVATION,
            CONF_MIN_POSITION,
            CONF_SUNRISE_OFFSET,
            CONF_SUNSET_OFFSET,
            CONF_SUNSET_POS,
        )

        return cls(
            win_azi=options.get(CONF_AZIMUTH) or 180,
            fov_left=options.get(CONF_FOV_LEFT) or 90,
            fov_right=options.get(CONF_FOV_RIGHT) or 90,
            h_def=options.get(CONF_DEFAULT_HEIGHT) or 0,
            sunset_pos=options.get(CONF_SUNSET_POS),
            sunset_off=options.get(CONF_SUNSET_OFFSET) or 0,
            sunrise_off=options.get(
                CONF_SUNRISE_OFFSET, options.get(CONF_SUNSET_OFFSET)
            )
            or 0,
            max_pos=options.get(CONF_MAX_POSITION) or 100,
            min_pos=options.get(CONF_MIN_POSITION) or 0,
            max_pos_sun_only=options.get(CONF_ENABLE_MAX_POSITION, False),
            min_pos_sun_only=options.get(CONF_ENABLE_MIN_POSITION, False),
            blind_spot_left=options.get(CONF_BLIND_SPOT_LEFT),
            blind_spot_right=options.get(CONF_BLIND_SPOT_RIGHT),
            blind_spot_elevation=options.get(CONF_BLIND_SPOT_ELEVATION),
            blind_spot_on=options.get(CONF_ENABLE_BLIND_SPOT, False),
            min_elevation=options.get(CONF_MIN_ELEVATION, None),
            max_elevation=options.get(CONF_MAX_ELEVATION, None),
        )


@dataclass
class VerticalConfig:
    """Configuration specific to vertical blinds."""

    distance: float
    h_win: float
    window_depth: float = 0.0
    sill_height: float = 0.0
    glare_zones: GlareZonesConfig | None = None


@dataclass
class HorizontalConfig:
    """Configuration specific to horizontal awnings."""

    awn_length: float = 2.0
    awn_angle: float = 0.0


@dataclass
class TiltConfig:
    """Configuration specific to tilt/venetian blinds."""

    slat_distance: float
    depth: float
    mode: TiltMode | str
