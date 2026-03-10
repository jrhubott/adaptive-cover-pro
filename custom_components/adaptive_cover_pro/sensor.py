"""Sensor platform for Adaptive Cover Pro integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CLIMATE_MODE,
    CONF_ENABLE_DIAGNOSTICS,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_MOTION_SENSORS,
    DOMAIN,
)
from .coordinator import AdaptiveDataUpdateCoordinator
from .entity_base import AdaptiveCoverDiagnosticSensorBase, AdaptiveCoverSensorBase


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Initialize Adaptive Cover Pro config entry."""

    name = config_entry.data["name"]
    coordinator: AdaptiveDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    entities = []

    # Standard sensors
    entities.append(
        AdaptiveCoverSensorEntity(
            config_entry.entry_id, hass, config_entry, name, coordinator
        )
    )
    entities.append(
        AdaptiveCoverTimeSensorEntity(
            config_entry.entry_id,
            hass,
            config_entry,
            name,
            "Start Sun",
            "start",
            "mdi:sun-clock-outline",
            coordinator,
        )
    )
    entities.append(
        AdaptiveCoverTimeSensorEntity(
            config_entry.entry_id,
            hass,
            config_entry,
            name,
            "End Sun",
            "end",
            "mdi:sun-clock",
            coordinator,
        )
    )
    entities.append(
        AdaptiveCoverControlSensorEntity(
            config_entry.entry_id, hass, config_entry, name, coordinator
        )
    )

    # Diagnostic sensors (if enabled)
    if config_entry.options.get(CONF_ENABLE_DIAGNOSTICS, False):
        # P0: Solar position sensors
        entities.append(
            AdaptiveCoverDiagnosticSensor(
                config_entry.entry_id,
                hass,
                config_entry,
                name,
                coordinator,
                "Sun Azimuth",
                "sun_azimuth",
                "°",
                "mdi:compass-outline",
                SensorStateClass.MEASUREMENT,
            )
        )
        entities.append(
            AdaptiveCoverDiagnosticSensor(
                config_entry.entry_id,
                hass,
                config_entry,
                name,
                coordinator,
                "Sun Elevation",
                "sun_elevation",
                "°",
                "mdi:angle-acute",
                SensorStateClass.MEASUREMENT,
            )
        )
        entities.append(
            AdaptiveCoverDiagnosticSensor(
                config_entry.entry_id,
                hass,
                config_entry,
                name,
                coordinator,
                "Gamma",
                "gamma",
                "°",
                "mdi:angle-right",
                SensorStateClass.MEASUREMENT,
            )
        )
        entities.append(
            AdaptiveCoverDiagnosticEnumSensor(
                config_entry.entry_id,
                hass,
                config_entry,
                name,
                coordinator,
                "Control Status",
                "control_status",
                "mdi:information-outline",
            )
        )
        entities.append(
            AdaptiveCoverDiagnosticEnumSensor(
                config_entry.entry_id,
                hass,
                config_entry,
                name,
                coordinator,
                "Control State Reason",
                "control_state_reason",
                "mdi:information-variant",
            )
        )
        entities.append(
            AdaptiveCoverDiagnosticSensor(
                config_entry.entry_id,
                hass,
                config_entry,
                name,
                coordinator,
                "Calculated Position",
                "calculated_position",
                PERCENTAGE,
                "mdi:calculator",
                SensorStateClass.MEASUREMENT,
            )
        )

        # P0: Last Cover Action
        entities.append(
            AdaptiveCoverLastActionSensor(
                config_entry.entry_id,
                hass,
                config_entry,
                name,
                coordinator,
            )
        )

        # P0: Manual Override End Time
        entities.append(
            AdaptiveCoverManualOverrideEndSensor(
                config_entry.entry_id,
                hass,
                config_entry,
                name,
                coordinator,
            )
        )

        # P0: Motion Timeout End Time (only when motion sensors are configured)
        if config_entry.options.get(CONF_MOTION_SENSORS):
            entities.append(
                AdaptiveCoverMotionTimeoutEndSensor(
                    config_entry.entry_id,
                    hass,
                    config_entry,
                    name,
                    coordinator,
                )
            )

        # P1: Force Override Triggers (only when force override sensors are configured)
        if config_entry.options.get(CONF_FORCE_OVERRIDE_SENSORS):
            entities.append(
                AdaptiveCoverForceOverrideTriggerSensor(
                    config_entry.entry_id,
                    hass,
                    config_entry,
                    name,
                    coordinator,
                )
            )

        # P1: Last Motion Time (only when motion sensors are configured)
        if config_entry.options.get(CONF_MOTION_SENSORS):
            entities.append(
                AdaptiveCoverLastMotionTimeSensor(
                    config_entry.entry_id,
                    hass,
                    config_entry,
                    name,
                    coordinator,
                )
            )

        # P1: Position Verification sensors (disabled by default)
        entities.append(
            AdaptiveCoverLastVerificationSensor(
                config_entry.entry_id,
                hass,
                config_entry,
                name,
                coordinator,
            )
        )
        entities.append(
            AdaptiveCoverRetryCountSensor(
                config_entry.entry_id,
                hass,
                config_entry,
                name,
                coordinator,
            )
        )

        # P1: Advanced diagnostic sensors (disabled by default)
        # Only add climate-specific sensors if climate mode is enabled
        if config_entry.options.get(CONF_CLIMATE_MODE, False):
            # Active Temperature sensor
            entities.append(
                AdaptiveCoverAdvancedDiagnosticSensor(
                    config_entry.entry_id,
                    hass,
                    config_entry,
                    name,
                    coordinator,
                    "Active Temperature",
                    "active_temperature",
                    None,  # Unit will be determined by HA
                    "mdi:thermometer",
                    SensorStateClass.MEASUREMENT,
                    SensorDeviceClass.TEMPERATURE,
                )
            )

            # Climate Conditions sensor
            entities.append(
                AdaptiveCoverAdvancedDiagnosticEnumSensor(
                    config_entry.entry_id,
                    hass,
                    config_entry,
                    name,
                    coordinator,
                    "Climate Conditions",
                    "climate_conditions",
                    "mdi:weather-partly-cloudy",
                )
            )

        # Time Window Status sensor (always created if diagnostics enabled)
        entities.append(
            AdaptiveCoverAdvancedDiagnosticEnumSensor(
                config_entry.entry_id,
                hass,
                config_entry,
                name,
                coordinator,
                "Time Window Status",
                "time_window",
                "mdi:clock-check-outline",
            )
        )

        # Sun Validity Status sensor (always created if diagnostics enabled)
        entities.append(
            AdaptiveCoverAdvancedDiagnosticEnumSensor(
                config_entry.entry_id,
                hass,
                config_entry,
                name,
                coordinator,
                "Sun Validity Status",
                "sun_validity",
                "mdi:weather-sunny-alert",
            )
        )

    async_add_entities(entities)


class AdaptiveCoverSensorEntity(AdaptiveCoverSensorBase, SensorEntity):
    """Adaptive Cover Pro Sensor."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(
        self,
        unique_id: str,
        hass,
        config_entry,
        name: str,
        coordinator: AdaptiveDataUpdateCoordinator,
    ) -> None:
        """Initialize adaptive_cover Sensor."""
        super().__init__(
            unique_id,
            hass,
            config_entry,
            coordinator,
            "Cover_Position",
            "mdi:sun-compass",
        )
        self._sensor_name = "Cover Position"

    @property
    def name(self):
        """Name of the entity."""
        return self._sensor_name

    @property
    def native_value(self) -> str | None:
        """Handle when entity is added."""
        return self.data.states["state"]

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:  # noqa: D102
        return self.data.attributes


class AdaptiveCoverTimeSensorEntity(AdaptiveCoverSensorBase, SensorEntity):
    """Adaptive Cover Pro Time Sensor."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        unique_id: str,
        hass,
        config_entry,
        name: str,
        sensor_name: str,
        key: str,
        icon: str,
        coordinator: AdaptiveDataUpdateCoordinator,
    ) -> None:
        """Initialize adaptive_cover Sensor."""
        super().__init__(unique_id, hass, config_entry, coordinator, sensor_name, icon)
        self.key = key
        self._sensor_name = sensor_name

    @property
    def name(self):
        """Name of the entity."""
        return self._sensor_name

    @property
    def native_value(self) -> str | None:
        """Handle when entity is added."""
        return self.data.states[self.key]


class AdaptiveCoverControlSensorEntity(AdaptiveCoverSensorBase, SensorEntity):
    """Adaptive Cover Pro Control method Sensor."""

    _attr_translation_key = "control"

    def __init__(
        self,
        unique_id: str,
        hass,
        config_entry,
        name: str,
        coordinator: AdaptiveDataUpdateCoordinator,
    ) -> None:
        """Initialize adaptive_cover Sensor."""
        super().__init__(
            unique_id, hass, config_entry, coordinator, "Control_Method", None
        )
        self._sensor_name = "Control Method"
        self.id = unique_id

    @property
    def name(self):
        """Name of the entity."""
        return self._sensor_name

    @property
    def native_value(self) -> str | None:
        """Handle when entity is added."""
        return self.data.states["control"]


class AdaptiveCoverDiagnosticSensor(AdaptiveCoverDiagnosticSensorBase, SensorEntity):
    """Adaptive Cover Pro Diagnostic Sensor."""

    def __init__(
        self,
        unique_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        name: str,
        coordinator: AdaptiveDataUpdateCoordinator,
        sensor_name: str,
        diagnostic_key: str,
        unit: str | None,
        icon: str,
        state_class: SensorStateClass | None = None,
    ) -> None:
        """Initialize diagnostic sensor."""
        super().__init__(
            unique_id,
            hass,
            config_entry,
            coordinator,
            diagnostic_key,
            icon,
            unit,
            state_class,
        )
        self._sensor_name = sensor_name
        self._diagnostic_key = diagnostic_key

    @property
    def name(self) -> str:
        """Name of the entity."""
        return self._sensor_name

    @property
    def native_value(self) -> float | int | None:
        """Return sensor value."""
        if self.data.diagnostics is None:
            return None
        diagnostics = self.data.diagnostics
        return diagnostics.get(self._diagnostic_key)

    def _build_azimuth_attributes(self) -> dict[str, Any] | None:
        """Build attributes for sun azimuth sensor."""
        if self.data.diagnostics is None:
            return None
        diagnostics = self.data.diagnostics
        config = diagnostics.get("configuration", {})
        window_azi = config.get("azimuth")
        fov_left = config.get("fov_left")
        fov_right = config.get("fov_right")

        if window_azi is None or fov_left is None or fov_right is None:
            return None

        azi_min = (window_azi - fov_left + 360) % 360
        azi_max = (window_azi + fov_right + 360) % 360

        return {
            "window_azimuth": window_azi,
            "fov_left": fov_left,
            "fov_right": fov_right,
            "azimuth_min": azi_min,
            "azimuth_max": azi_max,
            "in_fov": self._check_azimuth_in_fov(azi_min, azi_max),
        }

    def _check_azimuth_in_fov(self, azi_min: float, azi_max: float) -> bool:
        """Check if current sun azimuth is within field of view."""
        if self.data.diagnostics is None:
            return False
        diagnostics = self.data.diagnostics
        sun_azimuth = diagnostics.get("sun_azimuth")
        if sun_azimuth is None:
            return False

        # Handle wraparound (FOV crosses 0/360 boundary)
        if azi_min <= azi_max:
            return azi_min <= sun_azimuth <= azi_max
        else:
            return sun_azimuth >= azi_min or sun_azimuth <= azi_max

    def _build_elevation_attributes(self) -> dict[str, Any] | None:
        """Build attributes for sun elevation sensor."""
        if self.data.diagnostics is None:
            return None
        diagnostics = self.data.diagnostics
        config = diagnostics.get("configuration", {})
        min_elev = config.get("min_elevation")
        max_elev = config.get("max_elevation")

        attrs = {
            "valid_elevation": diagnostics.get("sun_validity", {}).get(
                "valid_elevation"
            ),
        }

        # Only include min/max if configured
        if min_elev is not None:
            attrs["min_elevation"] = min_elev
        if max_elev is not None:
            attrs["max_elevation"] = max_elev

        # Include blind spot if enabled
        if config.get("enable_blind_spot", False):
            blind_spot_elev = config.get("blind_spot_elevation")
            if blind_spot_elev is not None:
                attrs["blind_spot_elevation"] = blind_spot_elev
                attrs["in_blind_spot"] = diagnostics.get("sun_validity", {}).get(
                    "in_blind_spot", False
                )

        return attrs

    def _build_gamma_attributes(self) -> dict[str, Any] | None:
        """Build attributes for gamma sensor."""
        if self.data.diagnostics is None:
            return None
        diagnostics = self.data.diagnostics
        gamma = diagnostics.get("gamma")
        if gamma is None:
            return None

        # Gamma interpretation
        abs_gamma = abs(gamma)
        if abs_gamma < 10:
            interpretation = "nearly perpendicular"
        elif abs_gamma < 45:
            interpretation = "oblique angle"
        elif abs_gamma < 80:
            interpretation = "steep angle"
        else:
            interpretation = "nearly parallel"

        attrs = {
            "interpretation": interpretation,
            "absolute_angle": abs_gamma,
            "direction": "left" if gamma < 0 else "right" if gamma > 0 else "center",
        }

        # Include blind spot range if configured
        config = diagnostics.get("configuration", {})
        if config.get("enable_blind_spot", False):
            blind_spot_left = config.get("blind_spot_left")
            blind_spot_right = config.get("blind_spot_right")
            fov_left = config.get("fov_left")
            if (
                blind_spot_left is not None
                and blind_spot_right is not None
                and fov_left is not None
            ):
                left_edge = fov_left - blind_spot_left
                right_edge = fov_left - blind_spot_right
                attrs["blind_spot_range"] = [right_edge, left_edge]

        return attrs

    def _build_calculated_position_attributes(self) -> dict[str, Any] | None:
        """Build attributes for calculated position sensor."""
        if self.data.diagnostics is None:
            return None
        diagnostics = self.data.diagnostics
        config = diagnostics.get("configuration", {})
        calculated = diagnostics.get("calculated_position")
        if calculated is None:
            return None

        attrs = {
            "final_position": self.data.states.get("state"),
            "direct_sun_valid": self.data.states.get("sun_motion"),
        }

        # Show applied limits if they affected the result
        min_pos = config.get("min_position")
        max_pos = config.get("max_position")
        enable_min = config.get("enable_min_position", False)
        enable_max = config.get("enable_max_position", False)

        if min_pos is not None and enable_min and calculated < min_pos:
            attrs["min_limit_applied"] = min_pos
            attrs["limited_by"] = "min_position"

        if max_pos is not None and enable_max and calculated > max_pos:
            attrs["max_limit_applied"] = max_pos
            attrs["limited_by"] = "max_position"

        # Show if inverse state is applied
        if config.get("inverse_state", False):
            attrs["inverse_state_enabled"] = True

        # Show if interpolation is applied
        if config.get("interpolation", False):
            attrs["interpolation_enabled"] = True

        # Show climate mode position if different
        if diagnostics.get("calculated_position_climate") is not None:
            climate_pos = diagnostics.get("calculated_position_climate")
            if climate_pos != calculated:
                attrs["climate_position"] = climate_pos

        return attrs


class AdaptiveCoverDiagnosticEnumSensor(
    AdaptiveCoverDiagnosticSensorBase, SensorEntity
):
    """Adaptive Cover Pro Diagnostic Enum Sensor."""

    def __init__(
        self,
        unique_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        name: str,
        coordinator: AdaptiveDataUpdateCoordinator,
        sensor_name: str,
        diagnostic_key: str,
        icon: str,
    ) -> None:
        """Initialize diagnostic enum sensor."""
        super().__init__(
            unique_id, hass, config_entry, coordinator, diagnostic_key, icon
        )
        self._sensor_name = sensor_name
        self._diagnostic_key = diagnostic_key

    @property
    def name(self) -> str:
        """Name of the entity."""
        return self._sensor_name

    @property
    def native_value(self) -> str | None:
        """Return sensor value."""
        if self.data.diagnostics is None:
            return None
        diagnostics = self.data.diagnostics
        return diagnostics.get(self._diagnostic_key)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return additional state attributes."""
        if self.data.diagnostics is None:
            return None

        # Special handling for control_status sensor
        if self._diagnostic_key == "control_status":
            return self._build_control_status_attributes()

        # No attributes for other enum sensors
        return None

    def _build_control_status_attributes(self) -> dict[str, Any] | None:
        """Build attributes for control status sensor."""
        if self.data.diagnostics is None:
            return None
        diagnostics = self.data.diagnostics
        attrs = {}

        # Always show automatic control status
        attrs["automatic_control_enabled"] = self.coordinator.automatic_control

        control_status = diagnostics.get("control_status")

        # Add context-specific attributes based on status
        if control_status == "outside_time_window":
            time_window = diagnostics.get("time_window", {})
            attrs["after_start_time"] = time_window.get("after_start_time")
            attrs["before_end_time"] = time_window.get("before_end_time")

        elif control_status == "sun_not_visible":
            sun_validity = diagnostics.get("sun_validity", {})
            attrs["valid_elevation"] = sun_validity.get("valid_elevation")
            attrs["in_blind_spot"] = sun_validity.get("in_blind_spot")

        elif control_status == "manual_override":
            attrs["manual_covers"] = self.data.states.get("manual_list", [])

        return (
            attrs if len(attrs) > 1 else None
        )  # Return None if only automatic_control_enabled


class AdaptiveCoverAdvancedDiagnosticSensor(AdaptiveCoverDiagnosticSensor):
    """Advanced diagnostic sensor (P1 - disabled by default)."""

    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        unique_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        name: str,
        coordinator: AdaptiveDataUpdateCoordinator,
        sensor_name: str,
        diagnostic_key: str,
        unit: str | None,
        icon: str,
        state_class: SensorStateClass | None = None,
        device_class: SensorDeviceClass | None = None,
    ) -> None:
        """Initialize advanced diagnostic sensor."""
        super().__init__(
            unique_id,
            hass,
            config_entry,
            name,
            coordinator,
            sensor_name,
            diagnostic_key,
            unit,
            icon,
            state_class,
        )
        if device_class:
            self._attr_device_class = device_class

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return additional state attributes."""
        if self.data.diagnostics is None:
            return None
        diagnostics = self.data.diagnostics

        # For temperature sensor, add temperature details
        if self._diagnostic_key == "active_temperature":
            return diagnostics.get("temperature_details")

        return None


class AdaptiveCoverAdvancedDiagnosticEnumSensor(AdaptiveCoverDiagnosticEnumSensor):
    """Advanced diagnostic enum sensor (P1 - disabled by default)."""

    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> str | None:
        """Return computed state from dict data."""
        if self.data.diagnostics is None:
            return None
        diagnostics = self.data.diagnostics

        data = diagnostics.get(self._diagnostic_key)
        if data is None:
            return None

        # Compute human-readable state from dict
        if self._diagnostic_key == "time_window":
            return "Active" if data.get("check_adaptive_time") else "Outside Window"
        elif self._diagnostic_key == "sun_validity":
            if not data.get("valid"):
                if data.get("in_blind_spot"):
                    return "In Blind Spot"
                elif not data.get("valid_elevation"):
                    return "Invalid Elevation"
                return "Invalid"
            return "Valid"
        elif self._diagnostic_key == "climate_conditions":
            if data.get("is_summer"):
                return "Summer Mode"
            elif data.get("is_winter"):
                return "Winter Mode"
            return "Intermediate"

        return str(data)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return dict data as attributes."""
        if self.data.diagnostics is None:
            return None
        diagnostics = self.data.diagnostics
        return diagnostics.get(self._diagnostic_key)


class AdaptiveCoverLastActionSensor(AdaptiveCoverAdvancedDiagnosticSensor):
    """Sensor showing the last cover action performed."""

    # Override parent class to enable by default (matches P0 classification and docs)
    _attr_entity_registry_enabled_default = True

    def __init__(
        self,
        config_entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        name: str,
        coordinator: AdaptiveDataUpdateCoordinator,
    ):
        """Initialize the sensor."""
        super().__init__(
            config_entry_id,
            hass,
            config_entry,
            name,
            coordinator,
            "Last Cover Action",
            "last_cover_action",
            None,  # unit (text sensor has no unit)
            "mdi:history",
        )

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        if not self.data or not self.data.diagnostics:
            return None
        diagnostics = self.data.diagnostics

        action = diagnostics.get("last_cover_action")
        if not action or not action.get("entity_id"):
            return "No action recorded"

        # Format: "service → entity at timestamp"
        service = action.get("service", "unknown")
        entity = action.get("entity_id", "unknown")
        timestamp_str = action.get("timestamp", "")

        # Parse and format timestamp to be more readable
        if timestamp_str:
            try:
                ts = dt_util.parse_datetime(timestamp_str)
                if ts:
                    time_str = ts.strftime("%H:%M:%S")
                    return f"{service} → {entity.split('.')[-1]} at {time_str}"
            except (ValueError, AttributeError):
                pass

        return f"{service} → {entity.split('.')[-1]}"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional attributes."""
        if not self.data or not self.data.diagnostics:
            return None
        diagnostics = self.data.diagnostics

        action = diagnostics.get("last_cover_action")
        if not action or not action.get("entity_id"):
            return None

        attrs = {
            "entity_id": action.get("entity_id"),
            "service": action.get("service"),
            "position": action.get("position"),
            "calculated_position": action.get("calculated_position"),
            "inverse_state_applied": action.get("inverse_state_applied", False),
            "timestamp": action.get("timestamp"),
            "covers_controlled": action.get("covers_controlled", 1),
        }

        # Only include threshold for open/close-only covers
        if action.get("threshold_used") is not None:
            attrs["threshold_used"] = action.get("threshold_used")
            attrs["threshold_comparison"] = (
                f"{action.get('calculated_position')} >= {action.get('threshold_used')}"
            )

        return attrs


class AdaptiveCoverLastVerificationSensor(AdaptiveCoverAdvancedDiagnosticSensor):
    """Sensor showing when position was last verified."""

    _attr_entity_registry_enabled_default = False  # P1 sensor
    _attr_native_unit_of_measurement = ""  # Exclude from logbook
    _attr_should_poll = False  # Prevent unnecessary state polling
    _attr_has_entity_name = True  # Modern entity naming (HA 2024.5+)

    def __init__(
        self,
        config_entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        name: str,
        coordinator: AdaptiveDataUpdateCoordinator,
    ):
        """Initialize the sensor."""
        super().__init__(
            config_entry_id,
            hass,
            config_entry,
            name,
            coordinator,
            "Last Position Verification",
            "last_position_verification",
            None,
            "mdi:clock-check-outline",
            None,
            SensorDeviceClass.TIMESTAMP,
        )

    @property
    def native_value(self):
        """Return last verification time."""
        # Return the most recent verification time from all entities
        if not self.coordinator._last_verification:
            return None
        return max(self.coordinator._last_verification.values())

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return additional state attributes."""
        if not self.coordinator._last_verification:
            return None

        return {
            "per_entity": {
                entity_id: time.isoformat()
                for entity_id, time in self.coordinator._last_verification.items()
            }
        }


class AdaptiveCoverManualOverrideEndSensor(AdaptiveCoverAdvancedDiagnosticSensor):
    """Sensor showing when manual override will expire."""

    _attr_entity_registry_enabled_default = True  # P0: enabled by default
    _attr_should_poll = False

    def __init__(
        self,
        config_entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        name: str,
        coordinator: AdaptiveDataUpdateCoordinator,
    ):
        """Initialize the sensor."""
        super().__init__(
            config_entry_id,
            hass,
            config_entry,
            name,
            coordinator,
            "Manual Override End Time",
            "manual_override_end_time",
            None,
            "mdi:timer-outline",
            None,
            SensorDeviceClass.TIMESTAMP,
        )

    @property
    def native_value(self):
        """Return latest manual override expiry time, or None if no override is active."""
        times = self.coordinator.manager.manual_control_time
        if not times:
            return None
        duration = self.coordinator.manager.reset_duration
        return max(t + duration for t in times.values())

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return per-entity override expiry times."""
        times = self.coordinator.manager.manual_control_time
        if not times:
            return None
        duration = self.coordinator.manager.reset_duration
        return {
            "per_entity": {
                entity_id: (t + duration).isoformat()
                for entity_id, t in times.items()
            }
        }


class AdaptiveCoverRetryCountSensor(AdaptiveCoverAdvancedDiagnosticSensor):
    """Sensor showing current retry count for position verification."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False  # P1 sensor
    _attr_native_unit_of_measurement = "retries"

    def __init__(
        self,
        config_entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        name: str,
        coordinator: AdaptiveDataUpdateCoordinator,
    ):
        """Initialize the sensor."""
        super().__init__(
            config_entry_id,
            hass,
            config_entry,
            name,
            coordinator,
            "Position Verification Retries",
            "position_verification_retries",
            None,
            "mdi:refresh",
        )

    @property
    def native_value(self):
        """Return retry count."""
        # Return the maximum retry count from all entities
        if not self.coordinator._retry_counts:
            return 0
        return max(self.coordinator._retry_counts.values())

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return additional attributes."""
        return {
            "max_retries": self.coordinator._max_retries,
            "retries_remaining": max(
                0,
                self.coordinator._max_retries
                - max(self.coordinator._retry_counts.values(), default=0),
            ),
            "per_entity": dict(self.coordinator._retry_counts),
        }


class AdaptiveCoverMotionTimeoutEndSensor(AdaptiveCoverAdvancedDiagnosticSensor):
    """Sensor showing when motion timeout will expire (covers auto-close time)."""

    _attr_entity_registry_enabled_default = True  # P0: enabled by default
    _attr_should_poll = False

    def __init__(
        self,
        config_entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        name: str,
        coordinator: AdaptiveDataUpdateCoordinator,
    ):
        """Initialize the sensor."""
        super().__init__(
            config_entry_id,
            hass,
            config_entry,
            name,
            coordinator,
            "Motion Timeout End Time",
            "motion_timeout_end_time",
            None,
            "mdi:motion-sensor-off",
            None,
            SensorDeviceClass.TIMESTAMP,
        )

    @property
    def native_value(self):
        """Return when motion timeout will/did fire, or None if no timeout is active."""
        if self.coordinator._last_motion_time is None:
            return None

        task = self.coordinator._motion_timeout_task
        timeout_pending = task is not None and not task.done()

        if timeout_pending or self.coordinator._motion_timeout_active:
            end_ts = (
                self.coordinator._last_motion_time
                + self.coordinator._motion_timeout_seconds
            )
            return dt_util.utc_from_timestamp(end_ts)

        return None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return motion timeout details."""
        if self.coordinator._last_motion_time is None:
            return None

        attrs: dict[str, Any] = {
            "motion_timeout_seconds": self.coordinator._motion_timeout_seconds,
        }

        last_motion = dt_util.utc_from_timestamp(self.coordinator._last_motion_time)
        attrs["last_motion_detected"] = last_motion.isoformat()

        return attrs


class AdaptiveCoverForceOverrideTriggerSensor(AdaptiveCoverAdvancedDiagnosticSensor):
    """Sensor showing how many force override sensors are currently active."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False  # P1 sensor
    _attr_native_unit_of_measurement = "sensors"

    def __init__(
        self,
        config_entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        name: str,
        coordinator: AdaptiveDataUpdateCoordinator,
    ):
        """Initialize the sensor."""
        super().__init__(
            config_entry_id,
            hass,
            config_entry,
            name,
            coordinator,
            "Force Override Triggers",
            "force_override_triggers",
            None,
            "mdi:shield-alert-outline",
        )

    @property
    def native_value(self):
        """Return count of currently active force override sensors."""
        sensors = self.config_entry.options.get(CONF_FORCE_OVERRIDE_SENSORS, [])
        if not sensors:
            return None
        return sum(
            1
            for entity_id in sensors
            if (state := self.hass.states.get(entity_id)) and state.state == "on"
        )

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return per-sensor state details."""
        sensors = self.config_entry.options.get(CONF_FORCE_OVERRIDE_SENSORS, [])
        if not sensors:
            return None

        per_sensor: dict[str, str] = {}
        for entity_id in sensors:
            state = self.hass.states.get(entity_id)
            per_sensor[entity_id] = state.state if state is not None else "unavailable"

        return {
            "per_sensor": per_sensor,
            "total_configured": len(sensors),
        }


class AdaptiveCoverLastMotionTimeSensor(AdaptiveCoverAdvancedDiagnosticSensor):
    """Sensor showing when motion was last detected."""

    _attr_entity_registry_enabled_default = False  # P1 sensor
    _attr_should_poll = False

    def __init__(
        self,
        config_entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        name: str,
        coordinator: AdaptiveDataUpdateCoordinator,
    ):
        """Initialize the sensor."""
        super().__init__(
            config_entry_id,
            hass,
            config_entry,
            name,
            coordinator,
            "Last Motion Time",
            "last_motion_time",
            None,
            "mdi:motion-sensor",
            None,
            SensorDeviceClass.TIMESTAMP,
        )

    @property
    def native_value(self):
        """Return last motion detection time as UTC datetime."""
        if self.coordinator._last_motion_time is None:
            return None
        return dt_util.utc_from_timestamp(self.coordinator._last_motion_time)
