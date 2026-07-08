"""Climate state provider — reads Home Assistant entities into pure data."""

from __future__ import annotations

from dataclasses import dataclass
from operator import ge, le
from typing import TYPE_CHECKING
from collections.abc import Callable

from ..const import DEFAULT_TEMPLATE_COMBINE_MODE
from ..helpers import get_domain, get_safe_state, is_entity_active, state_attr
from ..templates import fold_condition_template
from .area_resolver import AreaSensorResolver

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
    # Effective indoor temperature entity actually read, and its provenance
    # (issue #786): "explicit" (configured), "area" (auto-resolved from the
    # cover's HA area), or "none". ``inside_temperature_area_id`` is set only
    # for the "area" source. Surfaced in diagnostics + config summary.
    inside_temperature_entity_id: str | None = None
    inside_temperature_source: str = "none"
    inside_temperature_area_id: str | None = None


class ClimateProvider:
    """Reads climate-related HA entities and returns a ClimateReadings snapshot."""

    def __init__(self, hass: HomeAssistant, logger: ConfigContextAdapter) -> None:
        """Initialize with HA instance and logger."""
        self._hass = hass
        self._logger = logger
        self._area_resolver = AreaSensorResolver(hass)

    def read(
        self,
        *,
        temp_entity: str | None = None,
        temp_device_id: str | None = None,
        auto_resolve_temp_from_area: bool = True,
        outside_entity: str | None = None,
        weather_entity: str | None = None,
        weather_condition: list[str] | None = None,
        presence_entity: str | None = None,
        presence_template: str | None = None,
        presence_template_mode: str = DEFAULT_TEMPLATE_COMBINE_MODE,
        use_lux: bool = False,
        lux_entity: str | None = None,
        lux_threshold: int | None = None,
        use_irradiance: bool = False,
        irradiance_entity: str | None = None,
        irradiance_threshold: int | None = None,
        use_cloud_coverage: bool = False,
        cloud_coverage_entity: str | None = None,
        cloud_coverage_threshold: int | None = None,
        is_sunny_sensor: str | None = None,
        is_sunny_template: str | None = None,
        is_sunny_template_mode: str = DEFAULT_TEMPLATE_COMBINE_MODE,
    ) -> ClimateReadings:
        """Read all climate entities and return a frozen snapshot."""
        resolved_temp = self._area_resolver.resolve_temperature_entity(
            explicit_entity=temp_entity,
            device_id=temp_device_id,
            auto_resolve=auto_resolve_temp_from_area,
        )
        return ClimateReadings(
            outside_temperature=self._read_outside_temperature(
                outside_entity, weather_entity
            ),
            inside_temperature=self._read_inside_temperature(resolved_temp.entity_id),
            inside_temperature_entity_id=resolved_temp.entity_id,
            inside_temperature_source=resolved_temp.source,
            inside_temperature_area_id=resolved_temp.area_id,
            is_presence=self._read_presence(
                presence_entity, presence_template, presence_template_mode
            ),
            is_sunny=self._read_sunny(
                weather_entity,
                weather_condition,
                is_sunny_sensor,
                is_sunny_template,
                is_sunny_template_mode,
            ),
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

    def _read_presence(
        self,
        presence_entity: str | None,
        presence_template: str | None = None,
        presence_template_mode: str = DEFAULT_TEMPLATE_COMBINE_MODE,
    ) -> bool:
        """Read presence, folding in an optional condition template (issue #639).

        The entity (when configured) keeps its existing domain-aware,
        fail-open evaluation; an optional Jinja template combines with it via
        ``presence_template_mode``. With no template and no entity the existing
        fail-open default (present) is preserved.
        """
        combined = fold_condition_template(
            self._hass,
            presence_template,
            presence_template_mode,
            others_truthy=is_entity_active(self._hass, presence_entity),
            has_others=bool(presence_entity),
        )
        if combined is not None:
            return combined
        return is_entity_active(self._hass, presence_entity)

    def _read_sunny(
        self,
        weather_entity: str | None,
        weather_condition: list[str] | None,
        is_sunny_sensor: str | None = None,
        is_sunny_template: str | None = None,
        is_sunny_template_mode: str = DEFAULT_TEMPLATE_COMBINE_MODE,
    ) -> bool:
        """Read weather state and check against sunny conditions.

        When ``is_sunny_sensor`` and/or ``is_sunny_template`` is configured, the
        sensor's on/off state and the rendered template combine via
        ``is_sunny_template_mode`` (issue #639). A sensor that is
        unavailable/unknown and a template that is empty or fails to render are
        each treated as "no opinion": when NEITHER source is authoritative the
        code falls through to the weather-entity logic so a stale source cannot
        strand the integration in a fixed state.
        """
        sensor_state = (
            get_safe_state(self._hass, is_sunny_sensor) if is_sunny_sensor else None
        )
        has_sensor = sensor_state in ("on", "off")
        combined = fold_condition_template(
            self._hass,
            is_sunny_template,
            is_sunny_template_mode,
            others_truthy=sensor_state == "on",
            has_others=has_sensor,
        )
        if combined is not None:
            self._logger.debug(
                "is_sunny(): sensor=%r template=%r → %s",
                is_sunny_sensor,
                is_sunny_template,
                combined,
            )
            return combined
        if is_sunny_sensor:
            self._logger.debug(
                "is_sunny(): sensor %s unavailable (%r) — falling through to weather",
                is_sunny_sensor,
                sensor_state,
            )
        if weather_entity is None:
            self._logger.debug("is_sunny(): No weather entity defined")
            return True
        weather_state = get_safe_state(self._hass, weather_entity)
        if weather_state is None:
            self._logger.debug("is_sunny(): Weather entity unavailable, assuming sunny")
            return True
        if weather_condition is not None:
            matches = weather_state in weather_condition
            self._logger.debug("is_sunny(): Weather: %s = %s", weather_state, matches)
            return matches
        self._logger.debug("is_sunny(): No weather condition defined")
        return True

    def _read_numeric_threshold(
        self,
        *,
        enabled: bool,
        entity: str | None,
        threshold: int | None,
        comparison: Callable[[float, float], bool],
        label: str,
    ) -> bool:
        """Compare an entity's numeric state to a threshold.

        Shared shape used by lux / irradiance / cloud-coverage readings:
        feature-flag gate, then read the entity, coerce to float, and compare
        against the configured threshold. Non-numeric or unavailable values
        return False so the climate snapshot stays bool-typed.
        """
        if not enabled or entity is None or threshold is None:
            return False
        value = get_safe_state(self._hass, entity)
        if value is None:
            return False
        try:
            return comparison(float(value), threshold)
        except (ValueError, TypeError):
            self._logger.debug(
                "%s entity %s returned non-numeric value: %r", label, entity, value
            )
            return False

    def _read_lux(
        self,
        use_lux: bool,
        lux_entity: str | None,
        lux_threshold: int | None,
    ) -> bool:
        """Check if lux is at or below threshold (low light)."""
        return self._read_numeric_threshold(
            enabled=use_lux,
            entity=lux_entity,
            threshold=lux_threshold,
            comparison=le,
            label="Lux",
        )

    def _read_irradiance(
        self,
        use_irradiance: bool,
        irradiance_entity: str | None,
        irradiance_threshold: int | None,
    ) -> bool:
        """Check if irradiance is at or below threshold (low radiation)."""
        return self._read_numeric_threshold(
            enabled=use_irradiance,
            entity=irradiance_entity,
            threshold=irradiance_threshold,
            comparison=le,
            label="Irradiance",
        )

    def _read_cloud_coverage(
        self,
        use_cloud_coverage: bool,
        cloud_coverage_entity: str | None,
        cloud_coverage_threshold: int | None,
    ) -> bool:
        """Check if cloud coverage is at or above threshold (overcast)."""
        return self._read_numeric_threshold(
            enabled=use_cloud_coverage,
            entity=cloud_coverage_entity,
            threshold=cloud_coverage_threshold,
            comparison=ge,
            label="Cloud coverage",
        )
