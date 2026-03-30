"""Export service for Adaptive Cover Pro — returns cover config as JSON response."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

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
    CONF_SENSOR_TYPE,
    CONF_SILL_HEIGHT,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_MODE,
    CONF_WINDOW_DEPTH,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

EXPORT_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
    }
)


async def async_handle_export(hass: HomeAssistant, call: ServiceCall) -> dict:
    """Handle the export_config service call and return config as a dict."""
    entry_id = call.data["config_entry_id"]
    entry = hass.config_entries.async_get_entry(entry_id)

    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            f"Config entry '{entry_id}' not found or does not belong to {DOMAIN}"
        )

    options = entry.options
    name = entry.data.get("name", "unknown")
    cover_type = entry.data.get(CONF_SENSOR_TYPE) or options.get(
        CONF_SENSOR_TYPE, "cover_blind"
    )

    return {
        "export_version": 1,
        "name": name,
        "cover_type": cover_type,
        "location": {
            "latitude": hass.config.latitude,
            "longitude": hass.config.longitude,
            "elevation": hass.config.elevation,
            "timezone": hass.config.time_zone,
        },
        "common": {
            CONF_AZIMUTH: options.get(CONF_AZIMUTH),
            CONF_FOV_LEFT: options.get(CONF_FOV_LEFT),
            CONF_FOV_RIGHT: options.get(CONF_FOV_RIGHT),
            CONF_DEFAULT_HEIGHT: options.get(CONF_DEFAULT_HEIGHT),
            CONF_SUNSET_POS: options.get(CONF_SUNSET_POS),
            CONF_SUNSET_OFFSET: options.get(CONF_SUNSET_OFFSET, 0),
            CONF_SUNRISE_OFFSET: options.get(
                CONF_SUNRISE_OFFSET, options.get(CONF_SUNSET_OFFSET, 0)
            ),
            CONF_MAX_POSITION: options.get(CONF_MAX_POSITION, 100),
            CONF_MIN_POSITION: options.get(CONF_MIN_POSITION, 0),
            CONF_ENABLE_MAX_POSITION: options.get(CONF_ENABLE_MAX_POSITION, False),
            CONF_ENABLE_MIN_POSITION: options.get(CONF_ENABLE_MIN_POSITION, False),
            CONF_ENABLE_BLIND_SPOT: options.get(CONF_ENABLE_BLIND_SPOT, False),
            CONF_BLIND_SPOT_LEFT: options.get(CONF_BLIND_SPOT_LEFT, 0),
            CONF_BLIND_SPOT_RIGHT: options.get(CONF_BLIND_SPOT_RIGHT, 0),
            CONF_BLIND_SPOT_ELEVATION: options.get(CONF_BLIND_SPOT_ELEVATION, 0),
            CONF_MIN_ELEVATION: options.get(CONF_MIN_ELEVATION),
            CONF_MAX_ELEVATION: options.get(CONF_MAX_ELEVATION),
        },
        "vertical": {
            CONF_DISTANCE: options.get(CONF_DISTANCE),
            CONF_HEIGHT_WIN: options.get(CONF_HEIGHT_WIN),
            CONF_WINDOW_DEPTH: options.get(CONF_WINDOW_DEPTH, 0.0),
            CONF_SILL_HEIGHT: options.get(CONF_SILL_HEIGHT) or 0.0,
        },
        "horizontal": {
            CONF_LENGTH_AWNING: options.get(CONF_LENGTH_AWNING),
            CONF_AWNING_ANGLE: options.get(CONF_AWNING_ANGLE, 0),
        },
        "tilt": {
            # Stored in cm as entered in UI — notebook divides by 100 to get meters
            CONF_TILT_DISTANCE: options.get(CONF_TILT_DISTANCE),
            CONF_TILT_DEPTH: options.get(CONF_TILT_DEPTH),
            CONF_TILT_MODE: options.get(CONF_TILT_MODE),
        },
    }
