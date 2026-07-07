"""Export service for Adaptive Cover Pro — returns cover config as JSON response."""

from __future__ import annotations

import json
import logging
import os
import pathlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

from ..const import (
    CONF_AWNING_ANGLE,
    CONF_AZIMUTH,
    CONF_BLIND_SPOT_ELEVATION,
    CONF_BLIND_SPOT_ELEVATION_MODE,
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
    CONF_MIN_POSITION_SUN_TRACKING,
    CONF_SENSOR_TYPE,
    CONF_SILL_HEIGHT,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_TILT_ANGLE_0,
    CONF_TILT_ANGLE_100,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_MODE,
    CONF_WINDOW_DEPTH,
    DEFAULT_BLIND_SPOT_ELEVATION_MODE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

EXPORT_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
    }
)

EXPORT_ALL_CONFIG_SCHEMA = vol.Schema({})

_EXPORT_FILENAME = "adaptive_cover_pro_export.json"
# Kept as a schema-default hint for the import service (relative filename only).
DEFAULT_EXPORT_PATH = _EXPORT_FILENAME


async def async_handle_export(call: ServiceCall) -> dict:
    """Handle the export_config service call and return config as a dict."""
    hass: HomeAssistant = call.hass
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
            CONF_MIN_POSITION_SUN_TRACKING: options.get(CONF_MIN_POSITION_SUN_TRACKING),
            CONF_ENABLE_MAX_POSITION: options.get(CONF_ENABLE_MAX_POSITION, False),
            CONF_ENABLE_MIN_POSITION: options.get(CONF_ENABLE_MIN_POSITION, False),
            CONF_ENABLE_BLIND_SPOT: options.get(CONF_ENABLE_BLIND_SPOT, False),
            CONF_BLIND_SPOT_LEFT: options.get(CONF_BLIND_SPOT_LEFT, 0),
            CONF_BLIND_SPOT_RIGHT: options.get(CONF_BLIND_SPOT_RIGHT, 0),
            CONF_BLIND_SPOT_ELEVATION: options.get(CONF_BLIND_SPOT_ELEVATION, 0),
            CONF_BLIND_SPOT_ELEVATION_MODE: options.get(
                CONF_BLIND_SPOT_ELEVATION_MODE, DEFAULT_BLIND_SPOT_ELEVATION_MODE
            ),
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
            CONF_TILT_ANGLE_0: options.get(CONF_TILT_ANGLE_0),
            CONF_TILT_ANGLE_100: options.get(CONF_TILT_ANGLE_100),
        },
    }


async def async_handle_export_all(call: ServiceCall) -> dict:
    """Handle the export_all_config service call.

    Writes all non-virtual ACP cover entries (those with a loaded coordinator) to
    ``<config_dir>/adaptive_cover_pro_export.json`` as a lossless JSON snapshot.
    The file is written atomically (write to ``.tmp`` then ``os.replace``) via the
    executor so the event loop is never blocked and a concurrent read never sees a
    partial file.

    Returns ``{"file": <path>, "count": <n>}`` so the call is also useful in
    Developer Tools (the response is visible immediately).
    """
    from . import (
        loaded_coordinators,
    )  # local import avoids circular dependency  # noqa: PLC0415

    hass: HomeAssistant = call.hass
    coordinators = loaded_coordinators(hass)

    entries = []
    for entry_id, _coord in coordinators.items():
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            continue
        entries.append(
            {
                "entry_id": entry_id,
                "title": entry.title,
                "options": dict(entry.options),
            }
        )

    payload = {
        "export_version": 1,
        "exported_at": datetime.now(UTC).isoformat(),
        "entries": entries,
    }

    path = pathlib.Path(hass.config.path(_EXPORT_FILENAME))
    json_text = json.dumps(payload, indent=2, ensure_ascii=False)

    def _atomic_write() -> None:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json_text, "utf-8")
        os.replace(tmp, path)

    await hass.async_add_executor_job(_atomic_write)

    _LOGGER.info("export_all_config: wrote %d entries to %s", len(entries), path)
    return {"file": str(path), "count": len(entries)}
