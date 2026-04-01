"""Configuration parsing service for Adaptive Cover Pro."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

from ..config_context_adapter import ConfigContextAdapter
from ..config_types import CoverConfig, HorizontalConfig, TiltConfig, VerticalConfig
from ..const import (
    CONF_AWNING_ANGLE,
    CONF_AZIMUTH,
    CONF_BLIND_SPOT_ELEVATION,
    CONF_BLIND_SPOT_LEFT,
    CONF_BLIND_SPOT_RIGHT,
    CONF_DEFAULT_HEIGHT,
    CONF_DISTANCE,
    CONF_ENABLE_BLIND_SPOT,
    CONF_ENABLE_MAX_POSITION,
    CONF_ENABLE_MIN_POSITION,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_HEIGHT_WIN,
    CONF_LENGTH_AWNING,
    CONF_MAX_ELEVATION,
    CONF_MAX_POSITION,
    CONF_MIN_ELEVATION,
    CONF_MIN_POSITION,
    CONF_SILL_HEIGHT,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_MODE,
    CONF_WINDOW_DEPTH,
)

_LOGGER = logging.getLogger(__name__)


class ConfigurationService:
    """Extracts and validates configuration parameters."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        logger: ConfigContextAdapter,
        cover_type: str | None,
        temp_toggle: bool | None,
        lux_toggle: bool | None,
        irradiance_toggle: bool | None,
    ) -> None:
        """Initialize configuration service."""
        self.hass = hass
        self.config_entry = config_entry
        self.logger = logger
        self._cover_type = cover_type
        self._temp_toggle = temp_toggle
        self._lux_toggle = lux_toggle
        self._irradiance_toggle = irradiance_toggle

    def get_common_data(self, options: dict) -> CoverConfig:
        """Extract shared parameters.

        Returns:
            CoverConfig with common configuration values

        """
        return CoverConfig(
            win_azi=options.get(CONF_AZIMUTH),
            fov_left=options.get(CONF_FOV_LEFT),
            fov_right=options.get(CONF_FOV_RIGHT),
            h_def=options.get(CONF_DEFAULT_HEIGHT),
            sunset_pos=options.get(CONF_SUNSET_POS),
            sunset_off=options.get(CONF_SUNSET_OFFSET),
            sunrise_off=options.get(
                CONF_SUNRISE_OFFSET, options.get(CONF_SUNSET_OFFSET)
            ),
            max_pos=options.get(CONF_MAX_POSITION),
            min_pos=options.get(CONF_MIN_POSITION),
            max_pos_sun_only=options.get(CONF_ENABLE_MAX_POSITION, False),
            min_pos_sun_only=options.get(CONF_ENABLE_MIN_POSITION, False),
            blind_spot_left=options.get(CONF_BLIND_SPOT_LEFT),
            blind_spot_right=options.get(CONF_BLIND_SPOT_RIGHT),
            blind_spot_elevation=options.get(CONF_BLIND_SPOT_ELEVATION),
            blind_spot_on=options.get(CONF_ENABLE_BLIND_SPOT, False),
            min_elevation=options.get(CONF_MIN_ELEVATION, None),
            max_elevation=options.get(CONF_MAX_ELEVATION, None),
        )

    def get_vertical_data(self, options: dict) -> VerticalConfig:
        """Extract vertical blind configuration.

        Returns:
            VerticalConfig with distance, window_height, window_depth, sill_height

        """
        return VerticalConfig(
            distance=options.get(CONF_DISTANCE),
            h_win=options.get(CONF_HEIGHT_WIN),
            window_depth=options.get(
                CONF_WINDOW_DEPTH, 0.0
            ),  # Default 0.0 for backward compatibility
            sill_height=options.get(CONF_SILL_HEIGHT)
            or 0.0,  # Default 0.0; handle None for non-vertical covers
        )

    def get_horizontal_data(self, options: dict) -> HorizontalConfig:
        """Extract horizontal awning configuration.

        Returns:
            HorizontalConfig with awning_length, awning_angle

        """
        return HorizontalConfig(
            awn_length=options.get(CONF_LENGTH_AWNING),
            awn_angle=options.get(CONF_AWNING_ANGLE),
        )

    def get_tilt_data(self, options: dict) -> TiltConfig:
        """Extract tilt blind configuration.

        Converts slat dimensions from centimeters (as entered in UI) to meters
        (as required by calculation formulas).

        Returns:
            TiltConfig with slat_distance_m, slat_depth_m, tilt_mode

        """
        depth = options.get(CONF_TILT_DEPTH)
        distance = options.get(CONF_TILT_DISTANCE)

        # Warn if values are suspiciously small (likely already in meters)
        if depth < 0.1 or distance < 0.1:
            _LOGGER.warning(
                "Tilt cover '%s': slat dimensions are very small (depth=%s, distance=%s). "
                "If you previously entered values in METERS, please reconfigure and enter in CENTIMETERS. "
                "For example: 2.5cm slats should be entered as '2.5', not '0.025'.",
                self.config_entry.data.get("name"),
                depth,
                distance,
            )

        return TiltConfig(
            slat_distance=distance / 100,  # Convert cm to meters
            depth=depth / 100,  # Convert cm to meters
            mode=options.get(CONF_TILT_MODE),
        )
