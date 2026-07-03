"""Sun state provider — creates SunData instances from Home Assistant."""

from __future__ import annotations

from typing import TYPE_CHECKING

from astral import LocationInfo
from astral.location import Location

from ..sun import SunData

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class SunProvider:
    """Creates SunData instances using Home Assistant location data."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize with Home Assistant instance."""
        self._hass = hass

    def create_sun_data(self, timezone: str) -> SunData:
        """Create a SunData instance with current HA location.

        Args:
            timezone: Timezone string for solar calculations.

        Returns:
            SunData instance with location and elevation from HA config.

        Note:
            Builds the astral ``Location`` directly instead of calling HA's
            ``homeassistant.helpers.sun.get_astral_location`` — that helper is
            deprecated (breaks in HA 2027.7) in favor of
            ``get_astral_observer``, which returns a bare ``Observer`` with no
            ``.solar_azimuth``/``.solar_elevation``/``.sunset``/``.sunrise``
            methods and would require a larger rewrite of ``SunData``. This
            inlines exactly ``get_astral_location``'s own (non-deprecated)
            body. See issue #815.

        """
        location = Location(
            LocationInfo(
                "",
                "",
                str(self._hass.config.time_zone),
                self._hass.config.latitude,
                self._hass.config.longitude,
            )
        )
        elevation = self._hass.config.elevation
        return SunData(timezone, location, elevation)
