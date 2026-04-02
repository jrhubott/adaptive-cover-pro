"""Shared fixtures and helpers for pipeline tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.pipeline.types import (
    ClimateOptions,
    PipelineSnapshot,
)


def _make_mock_cover(
    *,
    direct_sun_valid: bool = False,
    calculate_percentage_return: float = 50.0,
    default: float = 0.0,
    distance: float = 3.0,
    gamma: float = 0.0,
    config=None,
):
    """Build a mock AdaptiveGeneralCover for pipeline tests."""
    cover = MagicMock()
    cover.direct_sun_valid = direct_sun_valid
    cover.calculate_percentage = MagicMock(return_value=calculate_percentage_return)
    cover.default = default
    cover.distance = distance
    cover.gamma = gamma
    if config is None:
        config = MagicMock()
        config.min_pos = None
        config.max_pos = None
        config.min_pos_sun_only = False
        config.max_pos_sun_only = False
    cover.config = config
    return cover


def make_snapshot(
    *,
    cover=None,
    cover_type: str = "cover_blind",
    default_position: int = 0,
    climate_readings=None,
    climate_mode_enabled: bool = False,
    climate_options: ClimateOptions | None = None,
    force_override_sensors: dict[str, bool] | None = None,
    force_override_position: int = 0,
    manual_override_active: bool = False,
    motion_timeout_active: bool = False,
    glare_zones=None,
    active_zone_names: set[str] | frozenset[str] | None = None,
    # Convenience: configure mock cover
    direct_sun_valid: bool = False,
    calculate_percentage_return: float = 50.0,
    cover_default: float = 0.0,
) -> PipelineSnapshot:
    """Build a PipelineSnapshot with sensible defaults for testing."""
    if cover is None:
        cover = _make_mock_cover(
            direct_sun_valid=direct_sun_valid,
            calculate_percentage_return=calculate_percentage_return,
            default=cover_default,
        )
    return PipelineSnapshot(
        cover=cover,
        config=cover.config,
        cover_type=cover_type,
        default_position=default_position,
        climate_readings=climate_readings,
        climate_mode_enabled=climate_mode_enabled,
        climate_options=climate_options,
        force_override_sensors=force_override_sensors
        if force_override_sensors is not None
        else {},
        force_override_position=force_override_position,
        manual_override_active=manual_override_active,
        motion_timeout_active=motion_timeout_active,
        glare_zones=glare_zones,
        active_zone_names=frozenset(active_zone_names) if active_zone_names is not None else frozenset(),
    )
