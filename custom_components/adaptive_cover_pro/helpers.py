"""Helper functions."""

import datetime as dt
import logging
from datetime import UTC, timedelta
from typing import TYPE_CHECKING

import pandas as pd
from dateutil import parser
from homeassistant.core import HomeAssistant, split_entity_id
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .sun import SunData

_LOGGER = logging.getLogger(__name__)


def get_safe_state(hass: HomeAssistant, entity_id: str):
    """Get a safe state value if not available."""
    state = hass.states.get(entity_id)
    if not state or state.state in ["unknown", "unavailable"]:
        return None
    return state.state


def state_attr(hass: HomeAssistant, entity_id: str, attribute: str):
    """Return an entity attribute value, or None if the entity or attribute is absent.

    Replaces homeassistant.helpers.template.state_attr, which was removed from
    the public Python API in HA 2026.5. Same contract: None when the entity is
    unknown or the attribute is absent, otherwise the raw value.
    """
    state = hass.states.get(entity_id)
    if state is None:
        return None
    return state.attributes.get(attribute)


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
    """Convert a datetime string to a naive-local datetime.

    Tz-aware inputs (e.g., sun-sensor UTC values like "2026-04-18T04:46:00+00:00")
    are converted to HA's configured local timezone and then stripped of tzinfo so
    downstream naive comparisons work correctly in non-UTC installs.
    Tz-naive inputs (e.g., static "06:30") are returned unchanged.
    """
    if string is None:
        return None
    parsed = parser.parse(string)
    if parsed.tzinfo is not None:
        parsed = dt_util.as_local(parsed).replace(tzinfo=None)
    return parsed


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

    # STATE_UNAVAILABLE means the entity has no data at all — skip it.
    # STATE_UNKNOWN is safe to proceed: Z-Wave covers often report unknown
    # positional state permanently but still populate supported_features.
    if state.state == STATE_UNAVAILABLE:
        _LOGGER.debug("Cover %s unavailable, skipping capability check", entity_id)
        return None

    if state.state == STATE_UNKNOWN and "supported_features" not in state.attributes:
        _LOGGER.debug("Cover %s unknown state with no features, skipping", entity_id)
        return None

    if state.state == STATE_UNKNOWN and "supported_features" in state.attributes:
        _LOGGER.debug(
            "Cover %s: unknown state but supported_features=%s present — proceeding with capability check",
            entity_id,
            state.attributes.get("supported_features"),
        )

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
            "has_stop": True,
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
        "has_stop": bool(supported_features & CoverEntityFeature.STOP),
    }


def compute_effective_default(
    h_def: int,
    sunset_pos: int | None,
    sun_data: "SunData",
    sunset_off: int,
    sunrise_off: int,
) -> tuple[int, bool]:
    """Return the effective default cover position based on astronomical sunset/sunrise.

    If a ``sunset_pos`` is configured and the current wall-clock time falls
    within the astronomical sunset/sunrise window (i.e. after
    ``sunset + sunset_off`` minutes **or** before
    ``sunrise + sunrise_off`` minutes) the sunset position is active.

    Unlike the legacy timer-based approach this function is stateless and
    re-evaluated every coordinator update cycle, so a Home Assistant restart
    during the sunset window immediately returns the correct position without
    any timer re-scheduling.

    Args:
        h_def:      Configured default position (0–100 %).
        sunset_pos: Configured sunset/night position, or ``None`` when not set.
        sun_data:   ``SunData`` instance providing today's sunset/sunrise times.
        sunset_off: Minutes *added* to astronomical sunset before the window opens.
        sunrise_off: Minutes *added* to astronomical sunrise before the window closes.

    Returns:
        A ``(effective_default, is_sunset_active)`` tuple where
        ``is_sunset_active`` is ``True`` when the sunset position is in effect.

    """
    if sunset_pos is None:
        return h_def, False

    sunset = sun_data.sunset().replace(tzinfo=None)
    sunrise = sun_data.sunrise().replace(tzinfo=None)
    now_naive = dt.datetime.now(UTC).replace(tzinfo=None)

    after_sunset = now_naive > (sunset + timedelta(minutes=sunset_off))
    before_sunrise = now_naive < (sunrise + timedelta(minutes=sunrise_off))
    is_sunset_active = after_sunset or before_sunrise

    effective = int(sunset_pos) if is_sunset_active else int(h_def)
    return effective, is_sunset_active


def should_use_tilt(is_tilt_cover: bool, caps) -> bool:
    """Return True if tilt services/attributes should be used for this cover.

    Activates when the cover is configured as tilt OR when the entity only
    supports tilt operations (has SET_TILT_POSITION but not SET_POSITION),
    regardless of config-level sensor_type.

    Args:
        is_tilt_cover: Whether the cover is configured as ``cover_tilt``.
        caps: Capability source — either a ``dict`` (from ``check_cover_features``)
              or a ``CoverCapabilities`` dataclass.

    """
    if is_tilt_cover:
        return True
    if isinstance(caps, dict):
        return not caps.get("has_set_position", True) and caps.get(
            "has_set_tilt_position", False
        )
    # CoverCapabilities dataclass
    return not caps.has_set_position and caps.has_set_tilt_position


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
