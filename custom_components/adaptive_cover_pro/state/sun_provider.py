"""Sun state provider — creates SunData instances from Home Assistant."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.sun import get_astral_location

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

        """
        location, elevation = get_astral_location(self._hass)
        return SunData(timezone, location, elevation)
