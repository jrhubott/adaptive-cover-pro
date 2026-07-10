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
    # Hysteresis release edges consumed by CloudSuppressionManager's Schmitt
    # latch (issue #864). ``*_release_cleared`` is True when the value has passed
    # the configured release threshold so the latch may drop. With a blank
    # release threshold the band is zero-width and this equals ``not activate``,
    # so a manager that ORs them each cycle reproduces today's instantaneous
    # behaviour exactly. Default True (cleared) so a snapshot built without the
    # new fields never holds a latch.
    lux_release_cleared: bool = True
    irradiance_release_cleared: bool = True
    cloud_coverage_release_cleared: bool = True
    # Effective indoor temperature entity actually read, and its provenance
    # (issue #786): "explicit" (configured), "area" (auto-resolved from the
    # cover's HA area), or "none". ``inside_temperature_area_id`` is set only
    # for the "area" source. Surfaced in diagnostics + config summary.
    inside_temperature_entity_id: str | None = None
    inside_temperature_source: str = "none"
    inside_temperature_area_id: str | None = None
    # Provenance of the outdoor temperature actually used (issue #547):
    # "live" (sensor state / weather temp attr), "forecast_max" (pre-fetched
    # daily high), "max_of_live_and_forecast" (true combine of both), or
    # "live_fallback" (a forecast source was selected but no numeric forecast
    # was available). Surfaced in diagnostics.
    outside_temperature_source: str = "live"


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
        outside_temp_source: str = "live",
        forecast_max_outside: float | None = None,
        weather_condition: list[str] | None = None,
        presence_entity: str | None = None,
        presence_template: str | None = None,
        presence_template_mode: str = DEFAULT_TEMPLATE_COMBINE_MODE,
        use_lux: bool = False,
        lux_entity: str | None = None,
        lux_threshold: int | None = None,
        lux_release_threshold: float | None = None,
        use_irradiance: bool = False,
        irradiance_entity: str | None = None,
        irradiance_threshold: int | None = None,
        irradiance_release_threshold: float | None = None,
        use_cloud_coverage: bool = False,
        cloud_coverage_entity: str | None = None,
        cloud_coverage_threshold: int | None = None,
        cloud_coverage_release_threshold: float | None = None,
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
        outside_temperature, outside_temperature_source = (
            self._read_outside_temperature(
                outside_entity,
                weather_entity,
                outside_temp_source,
                forecast_max_outside,
            )
        )
        return ClimateReadings(
            outside_temperature=outside_temperature,
            outside_temperature_source=outside_temperature_source,
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
            **self._read_lux(use_lux, lux_entity, lux_threshold, lux_release_threshold),
            **self._read_irradiance(
                use_irradiance,
                irradiance_entity,
                irradiance_threshold,
                irradiance_release_threshold,
            ),
            **self._read_cloud_coverage(
                use_cloud_coverage,
                cloud_coverage_entity,
                cloud_coverage_threshold,
                cloud_coverage_release_threshold,
            ),
        )

    # ------------------------------------------------------------------
    # Private readers
    # ------------------------------------------------------------------

    def _read_outside_temperature(
        self,
        outside_entity: str | None,
        weather_entity: str | None,
        source: str = "live",
        forecast_max_outside: float | None = None,
    ) -> tuple[float | str | None, str]:
        """Read outside temperature and its provenance (issue #547).

        Returns a ``(value, source_label)`` tuple. ``source`` selects the
        strategy; every forecast path degrades to the live read when no
        numeric forecast is available so climate mode is never stranded:

        - ``live`` → sensor state / weather temp attr, label ``live``.
        - ``forecast_max`` → the pre-fetched daily high when numeric
          (label ``forecast_max``), else the live read (label
          ``live_fallback``).
        - ``max_of_live_and_forecast`` → ``max(live, forecast)`` when both
          are numeric (label ``max_of_live_and_forecast``); if only one is
          available it is used with the matching single-source label.
        """
        live_value = self._read_live_outside(outside_entity, weather_entity)

        if source == "live":
            return live_value, "live"

        forecast = self._coerce_float(forecast_max_outside)
        live_num = self._coerce_float(live_value)

        if source == "forecast_max":
            if forecast is not None:
                return forecast, "forecast_max"
            return live_value, "live_fallback"

        if source == "max_of_live_and_forecast":
            if forecast is not None and live_num is not None:
                return max(live_num, forecast), "max_of_live_and_forecast"
            if forecast is not None:
                return forecast, "forecast_max"
            return live_value, "live_fallback"

        # Unknown/absent source → safe live default.
        return live_value, "live"

    def _read_live_outside(
        self,
        outside_entity: str | None,
        weather_entity: str | None,
    ) -> float | str | None:
        """Live outdoor read: dedicated sensor, else weather temp attr."""
        if outside_entity:
            return get_safe_state(self._hass, outside_entity)
        if weather_entity:
            return state_attr(self._hass, weather_entity, "temperature")
        return None

    @staticmethod
    def _coerce_float(value: float | str | None) -> float | None:
        """Coerce a reading to float, or None when non-numeric/unavailable."""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
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
        release_threshold: float | None,
        release_comparison: Callable[[float, float], bool],
        label: str,
    ) -> tuple[bool, bool]:
        """Compare an entity's numeric state to its activate + release edges.

        Returns ``(activate_met, release_cleared)`` from a SINGLE read (issue
        #864). ``activate_met`` is the existing single-crossing comparison
        against ``threshold``. ``release_cleared`` is True when the value has
        passed the ``release_threshold`` edge (via ``release_comparison``), so a
        downstream Schmitt latch may drop.

        A blank ``release_threshold`` collapses the band to zero width →
        ``release_cleared = not activate_met``, reproducing today's
        instantaneous behaviour. A disabled / unavailable / non-numeric read
        reports ``(False, True)`` — inactive and cleared, so no latch is created
        or held (fail-open: sensor failure never strands the cover suppressed).
        """
        if not enabled or entity is None or threshold is None:
            return False, True
        value = get_safe_state(self._hass, entity)
        if value is None:
            return False, True
        try:
            fvalue = float(value)
        except (ValueError, TypeError):
            self._logger.debug(
                "%s entity %s returned non-numeric value: %r", label, entity, value
            )
            return False, True
        activate_met = comparison(fvalue, threshold)
        if release_threshold is None:
            return activate_met, not activate_met
        return activate_met, release_comparison(fvalue, release_threshold)

    def _read_lux(
        self,
        use_lux: bool,
        lux_entity: str | None,
        lux_threshold: int | None,
        lux_release_threshold: float | None = None,
    ) -> dict[str, bool]:
        """Read lux activate (at/below threshold) + release-cleared (issue #864)."""
        activate, cleared = self._read_numeric_threshold(
            enabled=use_lux,
            entity=lux_entity,
            threshold=lux_threshold,
            comparison=le,
            release_threshold=lux_release_threshold,
            release_comparison=ge,
            label="Lux",
        )
        return {
            "lux_below_threshold": activate,
            "lux_release_cleared": cleared,
        }

    def _read_irradiance(
        self,
        use_irradiance: bool,
        irradiance_entity: str | None,
        irradiance_threshold: int | None,
        irradiance_release_threshold: float | None = None,
    ) -> dict[str, bool]:
        """Read irradiance activate (at/below) + release-cleared (issue #864)."""
        activate, cleared = self._read_numeric_threshold(
            enabled=use_irradiance,
            entity=irradiance_entity,
            threshold=irradiance_threshold,
            comparison=le,
            release_threshold=irradiance_release_threshold,
            release_comparison=ge,
            label="Irradiance",
        )
        return {
            "irradiance_below_threshold": activate,
            "irradiance_release_cleared": cleared,
        }

    def _read_cloud_coverage(
        self,
        use_cloud_coverage: bool,
        cloud_coverage_entity: str | None,
        cloud_coverage_threshold: int | None,
        cloud_coverage_release_threshold: float | None = None,
    ) -> dict[str, bool]:
        """Read cloud activate (at/above) + release-cleared (issue #864).

        The cloud band is inverted vs lux/irradiance — activate is "at or above"
        (overcast), so the release edge clears "at or below" a lower value.
        """
        activate, cleared = self._read_numeric_threshold(
            enabled=use_cloud_coverage,
            entity=cloud_coverage_entity,
            threshold=cloud_coverage_threshold,
            comparison=ge,
            release_threshold=cloud_coverage_release_threshold,
            release_comparison=le,
            label="Cloud coverage",
        )
        return {
            "cloud_coverage_above_threshold": activate,
            "cloud_coverage_release_cleared": cleared,
        }
