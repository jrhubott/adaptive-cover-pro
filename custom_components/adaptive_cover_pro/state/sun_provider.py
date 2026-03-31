"""Sun data provider -- reads astral location from Home Assistant."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.sun import get_astral_location

from ..sun import SunData

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class SunProvider:
    """Bridge between Home Assistant and pure SunData."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize with HA instance to extract astral location."""
        self._location, self._elevation = get_astral_location(hass)

    def create_sun_data(self, timezone: str) -> SunData:
        """Create a SunData instance with pre-computed location."""
        return SunData(timezone, self._location, self._elevation)
