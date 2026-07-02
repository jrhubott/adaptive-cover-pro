"""engage_manual_override service — engage/extend manual override, no movement.

Thin target-resolution + lenient-coercion layer over
``Coordinator.async_engage_manual_override``, which drives the override state
machine (no ``apply_position`` — sends NO cover command). Two optional params:

* ``end_time`` — absolute end (datetime or ISO string). Parse failure falls
  through to the manager, which validates against ``now``.
* ``duration`` — relative extend-by (duration dict or timedelta). Non-positive
  or unparseable coerces to ``None``.

The handler does TYPE coercion + tz-normalization only (naive → UTC); it never
raises on a bad value. Semantic validation against ``now`` lives in the manager.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from homeassistant.core import ServiceCall

_LOGGER = logging.getLogger(__name__)


def _accept_any(value):
    """Passthrough validator — coercion happens leniently in the handler."""
    return value


ENGAGE_MANUAL_OVERRIDE_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Optional("end_time"): _accept_any,
        vol.Optional("duration"): _accept_any,
    }
)


def _resolve_targets(hass, call):
    """Thin re-export so tests can patch the local name."""
    from . import _resolve_targets as _rt  # noqa: PLC0415

    return _rt(hass, call)


def _coerce_end_time(value) -> dt.datetime | None:
    """Coerce a service ``end_time`` value to an aware datetime, or None.

    Accepts a ``datetime`` or a string (ISO / HA datetime). A naive result is
    treated as UTC (mirrors the tz-normalize precedent in the coordinator). Any
    parse failure returns ``None`` — the manager then falls through to its
    default rather than the service raising.
    """
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        parsed: dt.datetime | None = value
    elif isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
    else:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed


def _coerce_duration(value) -> dt.timedelta | None:
    """Coerce a service ``duration`` value to a positive timedelta, or None.

    Accepts a ``timedelta`` or a duration dict (``days``/``hours``/``minutes``/
    ``seconds``). Non-positive or unparseable → ``None``.
    """
    if value is None:
        return None
    if isinstance(value, dt.timedelta):
        td = value
    elif isinstance(value, dict):
        try:
            td = dt.timedelta(
                days=float(value.get("days", 0) or 0),
                hours=float(value.get("hours", 0) or 0),
                minutes=float(value.get("minutes", 0) or 0),
                seconds=float(value.get("seconds", 0) or 0),
            )
        except (TypeError, ValueError):
            return None
    else:
        return None
    if td <= dt.timedelta(0):
        return None
    return td


async def async_handle_engage_manual_override(call: ServiceCall) -> None:
    """Handle the engage_manual_override service call.

    Resolves the target block to one or more coordinators (each with an optional
    entity filter), coerces the optional ``end_time`` / ``duration`` leniently,
    then delegates each coordinator to ``async_engage_manual_override`` — which
    engages via the override state machine and refreshes once. No cover command
    is sent.
    """
    hass = call.hass
    end_time = _coerce_end_time(call.data.get("end_time"))
    duration = _coerce_duration(call.data.get("duration"))
    targets = _resolve_targets(hass, call)

    for coord, entity_filter in targets.items():
        entity_ids: list[str] = (
            list(entity_filter) if entity_filter is not None else list(coord.entities)
        )
        await coord.async_engage_manual_override(
            entity_ids,
            end_time=end_time,
            duration=duration,
            trigger="engage_manual_override",
        )
