"""Helper functions."""

import datetime as dt
import logging

import pandas as pd
from dateutil import parser
from homeassistant.core import HomeAssistant, split_entity_id

_LOGGER = logging.getLogger(__name__)


def get_safe_state(hass: HomeAssistant, entity_id: str):
    """Get a safe state value if not available."""
    state = hass.states.get(entity_id)
    if not state or state.state in ["unknown", "unavailable"]:
        return None
    return state.state


def get_domain(entity: str):
    """Get domain of entity."""
    if entity is not None:
        domain, object_id = split_entity_id(entity)
        return domain


def get_timedelta_str(string: str):
    """Convert string to timedelta."""
    if string is not None:
        return pd.to_timedelta(string)


def get_datetime_from_str(string: str):
    """Convert datetime string to datetime."""
    if string is not None:
        return parser.parse(string, ignoretz=True)


def get_last_updated(entity_id: str, hass: HomeAssistant):
    """Get last updated attribute from entity."""
    if entity_id is not None:
        if hass.states.get(entity_id):
            return hass.states.get(entity_id).last_updated


def check_time_passed(time: dt.datetime):
    """Check if time is passed for datetime."""
    now = dt.datetime.now()
    return now >= time


def dt_check_time_passed(time: dt.datetime):
    """Check if time is passed for UTC datetime."""
    now = dt.datetime.now(dt.UTC)
    return now >= time


def check_cover_features(hass: HomeAssistant, entity_id: str) -> dict[str, bool] | None:
    """Check which features a cover entity supports.

    Returns:
        Dict of capabilities if entity is ready, None if not yet initialized

    Dict keys:
    - has_set_position: bool
    - has_set_tilt_position: bool
    - has_open: bool
    - has_close: bool

    """
    from homeassistant.components.cover import CoverEntityFeature
    from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

    state = hass.states.get(entity_id)
    if not state:
        _LOGGER.debug("Cover %s state not available yet", entity_id)
        return None

    # Check if entity is ready
    if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        _LOGGER.debug("Cover %s not ready (state: %s)", entity_id, state.state)
        return None

    # Check if supported_features attribute exists
    if "supported_features" not in state.attributes:
        _LOGGER.debug(
            "Cover %s missing supported_features attribute, assuming position control",
            entity_id,
        )
        # Return optimistic defaults for entities without explicit capabilities
        return {
            "has_set_position": True,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
        }

    supported_features = state.attributes.get("supported_features", 0)

    _LOGGER.debug(
        "Cover %s supported_features: %s (binary: %s)",
        entity_id,
        supported_features,
        bin(supported_features),
    )

    return {
        "has_set_position": bool(supported_features & CoverEntityFeature.SET_POSITION),
        "has_set_tilt_position": bool(
            supported_features & CoverEntityFeature.SET_TILT_POSITION
        ),
        "has_open": bool(supported_features & CoverEntityFeature.OPEN),
        "has_close": bool(supported_features & CoverEntityFeature.CLOSE),
    }


def get_open_close_state(hass: HomeAssistant, entity_id: str) -> int | None:
    """Map open/closed state to position value for open/close-only covers.

    Returns:
    - 0 if closed
    - 100 if open
    - None if state is unknown/unavailable

    """
    state = hass.states.get(entity_id)
    if not state or state.state in ["unknown", "unavailable"]:
        return None

    if state.state == "closed":
        return 0
    elif state.state == "open":
        return 100

    return None
