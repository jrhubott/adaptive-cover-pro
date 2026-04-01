"""Typed configuration dataclasses for cover calculations."""

from __future__ import annotations

from dataclasses import dataclass

from .enums import TiltMode


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


@dataclass
class VerticalConfig:
    """Configuration specific to vertical blinds."""

    distance: float
    h_win: float
    window_depth: float = 0.0
    sill_height: float = 0.0


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
