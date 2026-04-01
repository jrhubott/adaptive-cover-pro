"""Unified state snapshot — all HA reads captured as frozen data."""

from __future__ import annotations

from dataclasses import dataclass

from .climate_provider import ClimateReadings


@dataclass(frozen=True)
class SunSnapshot:
    """Sun position at time of snapshot."""

    azimuth: float
    elevation: float


@dataclass(frozen=True)
class CoverCapabilities:
    """Cover entity capabilities."""

    has_set_position: bool
    has_set_tilt_position: bool
    has_open: bool
    has_close: bool


@dataclass(frozen=True)
class CoverStateSnapshot:
    """Complete state snapshot built at start of each update cycle."""

    sun: SunSnapshot
    climate: ClimateReadings | None
    cover_positions: dict[str, int | None]
    cover_capabilities: dict[str, CoverCapabilities]
    motion_detected: bool
    force_override_active: bool
