"""Climate state provider — reads Home Assistant entities into pure data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.helpers.template import state_attr

from ..helpers import get_domain, get_safe_state

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..config_context_adapter import ConfigContextAdapter


@dataclass(frozen=True)
class ClimateReadings:
    """Pre-read climate values — no Home Assistant dependency."""

    outside_temperature: float | str | None
    inside_temperature: float | str | None
    is_presence: bool
    is_sunny: bool
    lux_below_threshold: bool
    irradiance_below_threshold: bool
    cloud_coverage_above_threshold: bool


class ClimateProvider:
    """Reads climate-related HA entities and returns a ClimateReadings snapshot."""

    def __init__(self, hass: HomeAssistant, logger: ConfigContextAdapter) -> None:
        """Initialize with HA instance and logger."""
        self._hass = hass
        self._logger = logger

    def read(
        self,
        *,
        temp_entity: str | None = None,
        outside_entity: str | None = None,
        weather_entity: str | None = None,
        weather_condition: list[str] | None = None,
        presence_entity: str | None = None,
        use_lux: bool = False,
        lux_entity: str | None = None,
        lux_threshold: int | None = None,
        use_irradiance: bool = False,
        irradiance_entity: str | None = None,
        irradiance_threshold: int | None = None,
        use_cloud_coverage: bool = False,
        cloud_coverage_entity: str | None = None,
        cloud_coverage_threshold: int | None = None,
    ) -> ClimateReadings:
        """Read all climate entities and return a frozen snapshot."""
        return ClimateReadings(
            outside_temperature=self._read_outside_temperature(
                outside_entity, weather_entity
            ),
            inside_temperature=self._read_inside_temperature(temp_entity),
            is_presence=self._read_presence(presence_entity),
            is_sunny=self._read_sunny(weather_entity, weather_condition),
            lux_below_threshold=self._read_lux(use_lux, lux_entity, lux_threshold),
            irradiance_below_threshold=self._read_irradiance(
                use_irradiance, irradiance_entity, irradiance_threshold
            ),
            cloud_coverage_above_threshold=self._read_cloud_coverage(
                use_cloud_coverage, cloud_coverage_entity, cloud_coverage_threshold
            ),
        )

    # ------------------------------------------------------------------
    # Private readers
    # ------------------------------------------------------------------

    def _read_outside_temperature(
        self,
        outside_entity: str | None,
        weather_entity: str | None,
    ) -> float | str | None:
        """Read outside temperature from entity or weather fallback."""
        if outside_entity:
            return get_safe_state(self._hass, outside_entity)
        if weather_entity:
            return state_attr(self._hass, weather_entity, "temperature")
        return None

    def _read_inside_temperature(
        self,
        temp_entity: str | None,
    ) -> float | str | None:
        """Read inside temperature from sensor or climate entity."""
        if temp_entity is None:
            return None
        if get_domain(temp_entity) != "climate":
            return get_safe_state(self._hass, temp_entity)
        return state_attr(self._hass, temp_entity, "current_temperature")

    def _read_presence(self, presence_entity: str | None) -> bool:
        """Read presence with domain-specific logic."""
        if presence_entity is None:
            return True
        presence = get_safe_state(self._hass, presence_entity)
        if presence is None:
            return True
        domain = get_domain(presence_entity)
        if domain == "device_tracker":
            return presence == "home"
        if domain == "zone":
            return int(presence) > 0
        if domain in ("binary_sensor", "input_boolean"):
            return presence == "on"
        return True

    def _read_sunny(
        self,
        weather_entity: str | None,
        weather_condition: list[str] | None,
    ) -> bool:
        """Read weather state and check against sunny conditions."""
        if weather_entity is None:
            self._logger.debug("is_sunny(): No weather entity defined")
            return True
        weather_state = get_safe_state(self._hass, weather_entity)
        if weather_condition is not None:
            matches = weather_state in weather_condition
            self._logger.debug("is_sunny(): Weather: %s = %s", weather_state, matches)
            return matches
        self._logger.debug("is_sunny(): No weather condition defined")
        return True

    def _read_lux(
        self,
        use_lux: bool,
        lux_entity: str | None,
        lux_threshold: int | None,
    ) -> bool:
        """Check if lux is at or below threshold (low light)."""
        if not use_lux:
            return False
        if lux_entity is not None and lux_threshold is not None:
            value = get_safe_state(self._hass, lux_entity)
            if value is None:
                return False
            try:
                return float(value) <= lux_threshold
            except (ValueError, TypeError):
                self._logger.debug(
                    "Lux entity %s returned non-numeric value: %r", lux_entity, value
                )
                return False
        return False

    def _read_irradiance(
        self,
        use_irradiance: bool,
        irradiance_entity: str | None,
        irradiance_threshold: int | None,
    ) -> bool:
        """Check if irradiance is at or below threshold (low radiation)."""
        if not use_irradiance:
            return False
        if irradiance_entity is not None and irradiance_threshold is not None:
            value = get_safe_state(self._hass, irradiance_entity)
            if value is None:
                return False
            try:
                return float(value) <= irradiance_threshold
            except (ValueError, TypeError):
                self._logger.debug(
                    "Irradiance entity %s returned non-numeric value: %r",
                    irradiance_entity,
                    value,
                )
                return False
        return False

    def _read_cloud_coverage(
        self,
        use_cloud_coverage: bool,
        cloud_coverage_entity: str | None,
        cloud_coverage_threshold: int | None,
    ) -> bool:
        """Check if cloud coverage is at or above threshold (overcast)."""
        if not use_cloud_coverage:
            return False
        if cloud_coverage_entity is not None and cloud_coverage_threshold is not None:
            value = get_safe_state(self._hass, cloud_coverage_entity)
            if value is None:
                return False
            try:
                return float(value) >= cloud_coverage_threshold
            except (ValueError, TypeError):
                self._logger.debug(
                    "Cloud coverage entity %s returned non-numeric value: %r",
                    cloud_coverage_entity,
                    value,
                )
                return False
        return False
