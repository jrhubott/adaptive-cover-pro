"""Configuration parsing service for Adaptive Cover Pro."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

from ..config_context_adapter import ConfigContextAdapter
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
    CONF_IRRADIANCE_ENTITY,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_LENGTH_AWNING,
    CONF_LUX_ENTITY,
    CONF_LUX_THRESHOLD,
    CONF_MAX_ELEVATION,
    CONF_MAX_POSITION,
    CONF_MIN_ELEVATION,
    CONF_MIN_POSITION,
    CONF_OUTSIDE_THRESHOLD,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_TEMP_ENTITY,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_MODE,
    CONF_TRANSPARENT_BLIND,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_STATE,
    CONF_SILL_HEIGHT,
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

    def get_common_data(self, options: dict) -> list:
        """Extract shared parameters.

        Returns:
            List of common configuration values

        """
        return [
            options.get(CONF_SUNSET_POS),
            options.get(CONF_SUNSET_OFFSET),
            options.get(CONF_SUNRISE_OFFSET, options.get(CONF_SUNSET_OFFSET)),
            self.hass.config.time_zone,
            options.get(CONF_FOV_LEFT),
            options.get(CONF_FOV_RIGHT),
            options.get(CONF_AZIMUTH),
            options.get(CONF_DEFAULT_HEIGHT),
            options.get(CONF_MAX_POSITION),
            options.get(CONF_MIN_POSITION),
            options.get(CONF_ENABLE_MAX_POSITION, False),
            options.get(CONF_ENABLE_MIN_POSITION, False),
            options.get(CONF_BLIND_SPOT_LEFT),
            options.get(CONF_BLIND_SPOT_RIGHT),
            options.get(CONF_BLIND_SPOT_ELEVATION),
            options.get(CONF_ENABLE_BLIND_SPOT, False),
            options.get(CONF_MIN_ELEVATION, None),
            options.get(CONF_MAX_ELEVATION, None),
        ]

    def get_climate_data(self, options: dict) -> list:
        """Extract climate mode configuration.

        Returns:
            List of climate configuration values

        """
        return [
            self.hass,
            self.logger,
            options.get(CONF_TEMP_ENTITY),
            options.get(CONF_TEMP_LOW),
            options.get(CONF_TEMP_HIGH),
            options.get(CONF_PRESENCE_ENTITY),
            options.get(CONF_WEATHER_ENTITY),
            options.get(CONF_WEATHER_STATE),
            options.get(CONF_OUTSIDETEMP_ENTITY),
            self._temp_toggle,
            self._cover_type,
            options.get(CONF_TRANSPARENT_BLIND),
            options.get(CONF_LUX_ENTITY),
            options.get(CONF_IRRADIANCE_ENTITY),
            options.get(CONF_LUX_THRESHOLD),
            options.get(CONF_IRRADIANCE_THRESHOLD),
            options.get(CONF_OUTSIDE_THRESHOLD),
            self._lux_toggle,
            self._irradiance_toggle,
        ]

    def get_vertical_data(self, options: dict) -> list:
        """Extract vertical blind configuration.

        Returns:
            List of [distance, window_height, window_depth, sill_height]

        """
        return [
            options.get(CONF_DISTANCE),
            options.get(CONF_HEIGHT_WIN),
            options.get(CONF_WINDOW_DEPTH, 0.0),  # Default 0.0 for backward compatibility
            options.get(CONF_SILL_HEIGHT, 0.0),  # Default 0.0 for backward compatibility
        ]

    def get_horizontal_data(self, options: dict) -> list:
        """Extract horizontal awning configuration.

        Returns:
            List of [awning_length, awning_angle]

        """
        return [
            options.get(CONF_LENGTH_AWNING),
            options.get(CONF_AWNING_ANGLE),
        ]

    def get_tilt_data(self, options: dict) -> list:
        """Extract tilt blind configuration.

        Converts slat dimensions from centimeters (as entered in UI) to meters
        (as required by calculation formulas).

        Returns:
            List of [slat_distance_m, slat_depth_m, tilt_mode]

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

        return [
            distance / 100,  # Convert cm to meters
            depth / 100,  # Convert cm to meters
            options.get(CONF_TILT_MODE),
        ]
