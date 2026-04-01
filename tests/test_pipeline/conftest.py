"""Shared fixtures and helpers for pipeline tests."""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.pipeline.types import PipelineContext


def make_ctx(
    *,
    calculated_position: int = 50,
    climate_position: int | None = None,
    default_position: int = 0,
    raw_calculated_position: int = 50,
    in_time_window: bool = True,
    direct_sun_valid: bool = False,
    force_override_active: bool = False,
    force_override_position: int = 0,
    motion_timeout_active: bool = False,
    manual_override_active: bool = False,
    climate_mode_enabled: bool = False,
    climate_is_summer: bool = False,
    climate_is_winter: bool = False,
    wind_active: bool = False,
    wind_retract_position: int = 100,
    cloud_suppression_active: bool = False,
) -> PipelineContext:
    """Build a PipelineContext with sensible defaults."""
    return PipelineContext(
        calculated_position=calculated_position,
        climate_position=climate_position,
        default_position=default_position,
        raw_calculated_position=raw_calculated_position,
        in_time_window=in_time_window,
        direct_sun_valid=direct_sun_valid,
        force_override_active=force_override_active,
        force_override_position=force_override_position,
        motion_timeout_active=motion_timeout_active,
        manual_override_active=manual_override_active,
        climate_mode_enabled=climate_mode_enabled,
        climate_is_summer=climate_is_summer,
        climate_is_winter=climate_is_winter,
        wind_active=wind_active,
        wind_retract_position=wind_retract_position,
        cloud_suppression_active=cloud_suppression_active,
    )
