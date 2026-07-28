"""Area-based sensor resolution — cover area → configured sensor entity.

Part of the ``state/`` boundary: this is the only place the device and area
registries are read to derive a cover's indoor temperature sensor from its
Home Assistant area (issue #786). Home Assistant's area registry stores a
per-area ``temperature_entity_id`` (added HA 2024.11); when a cover has no
explicit temp sensor configured, we fall back to its area's configured one.

The resolver is named generically (``resolve_temperature_entity``) so a future
device-class-scan resolver for motion/wind can be a sibling method — but only
temperature is implemented today, because the area registry exposes only
``temperature_entity_id`` / ``humidity_entity_id`` and no motion/wind field.

Fail-open: any missing hop (no device, device has no area, area has no
temperature entity) resolves to ``None`` — identical to "no sensor configured"
— and never raises into the update cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# Provenance of a resolved sensor entity — surfaced in diagnostics and the
# config summary so a user can see why a cover reacts to a sensor they never
# configured on it.
SENSOR_SOURCE_EXPLICIT = "explicit"
SENSOR_SOURCE_AREA = "area"
SENSOR_SOURCE_NONE = "none"


@dataclass(frozen=True, slots=True)
class ResolvedSensor:
    """A resolved sensor entity and where it came from.

    ``entity_id`` is the effective entity (``None`` when nothing resolved).
    ``source`` is one of :data:`SENSOR_SOURCE_EXPLICIT` / ``_AREA`` / ``_NONE``.
    ``area_id`` is the area the entity was resolved from (only set for the
    ``area`` source).
    """

    entity_id: str | None
    source: str
    area_id: str | None


def device_area_id(hass: HomeAssistant, device_id: str | None) -> str | None:
    """Return the area a device belongs to, or ``None``.

    Shared device→area lookup used by both :class:`AreaSensorResolver` and the
    cover-group coordinator's entity→area resolution, so the registry hop lives
    in exactly one place. Fail-open: an unknown or unlinked device yields
    ``None``.
    """
    if not device_id:
        return None
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    return device.area_id


class AreaSensorResolver:
    """Resolve a cover's sensor entity from explicit config or its HA area."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Bind the resolver to the Home Assistant instance."""
        self._hass = hass

    def resolve_temperature_entity(
        self,
        *,
        explicit_entity: str | None,
        device_id: str | None,
        auto_resolve: bool = True,
    ) -> ResolvedSensor:
        """Resolve the effective indoor temperature entity for a cover.

        Precedence: an explicit ``explicit_entity`` (the configured
        ``CONF_TEMP_ENTITY``) always wins. Otherwise, when ``auto_resolve`` is
        on, follow ``device_id`` → area → the area's configured
        ``temperature_entity_id``. Any missing hop yields a ``None`` /
        :data:`SENSOR_SOURCE_NONE` result.
        """
        if explicit_entity:
            return ResolvedSensor(explicit_entity, SENSOR_SOURCE_EXPLICIT, None)
        if not auto_resolve:
            return ResolvedSensor(None, SENSOR_SOURCE_NONE, None)
        area_id = device_area_id(self._hass, device_id)
        if area_id:
            area = ar.async_get(self._hass).async_get_area(area_id)
            temp_entity = getattr(area, "temperature_entity_id", None)
            if temp_entity:
                return ResolvedSensor(temp_entity, SENSOR_SOURCE_AREA, area_id)
        return ResolvedSensor(None, SENSOR_SOURCE_NONE, None)
