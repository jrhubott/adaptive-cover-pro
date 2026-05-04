"""Shared fixtures and helpers for pipeline tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.pipeline.types import (
    ClimateOptions,
    CustomPositionSensorState,
    PipelineSnapshot,
)


def _make_mock_cover(
    *,
    direct_sun_valid: bool = False,
    calculate_percentage_return: float = 50.0,
    distance: float = 3.0,
    gamma: float = 0.0,
    config=None,
):
    """Build a mock AdaptiveGeneralCover for pipeline tests.

    Note: cover.default is intentionally NOT set here.  The .default property
    was removed from AdaptiveGeneralCover to prevent handlers from bypassing
    the centrally-computed snapshot.default_position.  All default-position
    logic must flow through snapshot.default_position.
    """
    cover = MagicMock(
        spec=[
            "direct_sun_valid",
            "calculate_percentage",
            "distance",
            "gamma",
            "config",
            "valid",
            "valid_elevation",
            "is_sun_in_blind_spot",
            "sunset_valid",
            "calculate_position",
            "control_state_reason",
            "sun_data",
        ]
    )
    cover.direct_sun_valid = direct_sun_valid
    cover.calculate_percentage = MagicMock(return_value=calculate_percentage_return)
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
    is_sunset_active: bool = False,
    climate_readings=None,
    climate_mode_enabled: bool = False,
    climate_options: ClimateOptions | None = None,
    force_override_sensors: dict[str, bool] | None = None,
    force_override_position: int = 0,
    force_override_min_mode: bool = False,
    manual_override_active: bool = False,
    motion_timeout_active: bool = False,
    weather_override_active: bool = False,
    weather_override_position: int = 0,
    weather_override_min_mode: bool = False,
    weather_bypass_auto_control: bool = True,
    glare_zones=None,
    active_zone_names: set[str] | frozenset[str] | None = None,
    in_time_window: bool = True,
    motion_control_enabled: bool = True,
    custom_position_sensors: list[CustomPositionSensorState] | None = None,
    my_position_value: int | None = None,
    sunset_use_my: bool = False,
    enable_sun_tracking: bool = True,
    motion_timeout_mode: str = "return_to_default",
    current_cover_position: int | None = None,
    # Convenience: configure mock cover
    direct_sun_valid: bool = False,
    calculate_percentage_return: float = 50.0,
) -> PipelineSnapshot:
    """Build a PipelineSnapshot with sensible defaults for testing."""
    if cover is None:
        cover = _make_mock_cover(
            direct_sun_valid=direct_sun_valid,
            calculate_percentage_return=calculate_percentage_return,
        )
    return PipelineSnapshot(
        cover=cover,
        config=cover.config,
        cover_type=cover_type,
        default_position=default_position,
        is_sunset_active=is_sunset_active,
        climate_readings=climate_readings,
        climate_mode_enabled=climate_mode_enabled,
        climate_options=climate_options,
        force_override_sensors=(
            force_override_sensors if force_override_sensors is not None else {}
        ),
        force_override_position=force_override_position,
        force_override_min_mode=force_override_min_mode,
        manual_override_active=manual_override_active,
        motion_timeout_active=motion_timeout_active,
        weather_override_active=weather_override_active,
        weather_override_position=weather_override_position,
        weather_override_min_mode=weather_override_min_mode,
        weather_bypass_auto_control=weather_bypass_auto_control,
        glare_zones=glare_zones,
        active_zone_names=(
            frozenset(active_zone_names)
            if active_zone_names is not None
            else frozenset()
        ),
        in_time_window=in_time_window,
        motion_control_enabled=motion_control_enabled,
        custom_position_sensors=(
            custom_position_sensors if custom_position_sensors is not None else []
        ),
        my_position_value=my_position_value,
        sunset_use_my=sunset_use_my,
        enable_sun_tracking=enable_sun_tracking,
        motion_timeout_mode=motion_timeout_mode,
        current_cover_position=current_cover_position,
    )
