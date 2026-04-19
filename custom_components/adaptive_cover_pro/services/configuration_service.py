"""Configuration parsing service for Adaptive Cover Pro."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

from ..config_context_adapter import ConfigContextAdapter
from ..config_types import (
    CoverConfig,
    GlareZone,
    GlareZonesConfig,
    HorizontalConfig,
    TiltConfig,
    VerticalConfig,
)
from ..const import (
    CONF_AWNING_ANGLE,
    CONF_DISTANCE,
    CONF_ENABLE_GLARE_ZONES,
    CONF_HEIGHT_WIN,
    CONF_LENGTH_AWNING,
    CONF_SILL_HEIGHT,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_MODE,
    CONF_WINDOW_DEPTH,
    CONF_WINDOW_WIDTH,
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
        return CoverConfig.from_options(options)

    def get_vertical_data(self, options: dict) -> VerticalConfig:
        """Extract vertical blind configuration.

        Returns:
            VerticalConfig with distance, window_height, window_depth, sill_height

        """
        return VerticalConfig(
            distance=options.get(CONF_DISTANCE) or 1.0,
            h_win=options.get(CONF_HEIGHT_WIN) or 2.1,
            window_depth=options.get(CONF_WINDOW_DEPTH)
            or 0.0,  # Default 0.0; handle None for non-vertical covers
            sill_height=options.get(CONF_SILL_HEIGHT)
            or 0.0,  # Default 0.0; handle None for non-vertical covers
        )

    def get_horizontal_data(self, options: dict) -> HorizontalConfig:
        """Extract horizontal awning configuration.

        Returns:
            HorizontalConfig with awning_length, awning_angle

        """
        return HorizontalConfig(
            awn_length=options.get(CONF_LENGTH_AWNING) or 2.0,
            awn_angle=options.get(CONF_AWNING_ANGLE) or 0,
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

        if depth is None or distance is None:
            _LOGGER.warning(
                "Tilt cover '%s': slat depth or distance is missing from config "
                "(depth=%s, distance=%s). Using safe defaults.",
                self.config_entry.data.get("name"),
                depth,
                distance,
            )
            depth = depth if depth is not None else 2.5
            distance = distance if distance is not None else 2.5

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

    def get_glare_zones_config(self, options: dict) -> GlareZonesConfig | None:
        """Build GlareZonesConfig from config entry options.

        Returns None if glare zones are disabled or no zones have names.
        """
        if not options.get(CONF_ENABLE_GLARE_ZONES):
            return None

        zones = []
        for i in range(1, 5):  # zones 1–4
            name = options.get(f"glare_zone_{i}_name", "")
            if not name:
                continue
            zones.append(
                GlareZone(
                    name=name,
                    x=float(options.get(f"glare_zone_{i}_x", 0.0)),
                    y=float(options.get(f"glare_zone_{i}_y", 1.0)),
                    radius=float(options.get(f"glare_zone_{i}_radius", 0.3)),
                )
            )

        if not zones:
            return None

        return GlareZonesConfig(
            zones=zones,
            window_width=float(options.get(CONF_WINDOW_WIDTH, 1.0)),
        )
