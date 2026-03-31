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
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CLIMATE_MODE,
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

    # Auto-cleanup orphaned entities replaced by consolidated sensors
    er = entity_registry.async_get(hass)
    old_suffixes = [
        "sun_azimuth",
        "sun_elevation",
        "gamma",
        "control_state_reason",
        "time_window",
        "sun_validity",
        "active_temperature",
        "climate_conditions",
        "motion_timeout_end_time",
        "last_motion_time",
        "last_position_verification",
        "position_verification_retries",
    ]
    for suffix in old_suffixes:
        old_uid = f"{config_entry.entry_id}_{suffix}"
        if entity_id := er.async_get_entity_id("sensor", DOMAIN, old_uid):
            er.async_remove(entity_id)

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

    # Diagnostic sensors (always enabled)

    # Sun Position: combines Sun Azimuth + Sun Elevation + Gamma
    entities.append(
        AdaptiveCoverSunPositionSensor(
            config_entry.entry_id, hass, config_entry, name, coordinator
        )
    )

    # Control Status: combines Control Status + Reason + Time Window + Sun Validity
    entities.append(
        AdaptiveCoverControlStatusSensor(
            config_entry.entry_id, hass, config_entry, name, coordinator
        )
    )

    # Calculated Position
    entities.append(
        AdaptiveCoverCalculatedPositionSensor(
            config_entry.entry_id, hass, config_entry, name, coordinator
        )
    )

    # Position Explanation
    entities.append(
        AdaptiveCoverPositionExplanationSensor(
            config_entry.entry_id, hass, config_entry, name, coordinator
        )
    )

    # Last Skipped Action
    entities.append(
        AdaptiveCoverLastSkippedActionSensor(
            config_entry.entry_id, hass, config_entry, name, coordinator
        )
    )

    # Last Cover Action
    entities.append(
        AdaptiveCoverLastActionSensor(
            config_entry.entry_id, hass, config_entry, name, coordinator
        )
    )

    # Manual Override End Time
    entities.append(
        AdaptiveCoverManualOverrideEndSensor(
            config_entry.entry_id, hass, config_entry, name, coordinator
        )
    )

    # Position Verification: combines Last Verification + Retry Count
    entities.append(
        AdaptiveCoverPositionVerificationSensor(
            config_entry.entry_id, hass, config_entry, name, coordinator
        )
    )

    # Motion Status: combines Motion Timeout End + Last Motion Time
    # (only when motion sensors are configured)
    if config_entry.options.get(CONF_MOTION_SENSORS):
        entities.append(
            AdaptiveCoverMotionStatusSensor(
                config_entry.entry_id, hass, config_entry, name, coordinator
            )
        )

    # Force Override Triggers (only when force override sensors are configured)
    if config_entry.options.get(CONF_FORCE_OVERRIDE_SENSORS):
        entities.append(
            AdaptiveCoverForceOverrideTriggerSensor(
                config_entry.entry_id, hass, config_entry, name, coordinator
            )
        )

    # Climate Status: combines Active Temperature + Climate Conditions
    # (only when climate mode is enabled)
    if config_entry.options.get(CONF_CLIMATE_MODE, False):
        entities.append(
            AdaptiveCoverClimateStatusSensor(
                config_entry.entry_id, hass, config_entry, name, coordinator, hass
            )
        )

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Standard sensors
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Diagnostic sensors
# ---------------------------------------------------------------------------


class AdaptiveCoverSunPositionSensor(AdaptiveCoverDiagnosticSensorBase, SensorEntity):
    """Diagnostic sensor combining sun azimuth, elevation, and gamma."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "°"

    def __init__(
        self,
        unique_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        name: str,
        coordinator: AdaptiveDataUpdateCoordinator,
    ) -> None:
        """Initialize sun position sensor."""
        super().__init__(
            unique_id,
            hass,
            config_entry,
            coordinator,
            "sun_position",
            "mdi:compass-outline",
        )
        self._sensor_name = "Sun Position"

    @property
    def name(self) -> str:
        """Name of the entity."""
        return self._sensor_name

    @property
    def native_value(self) -> float | None:
        """Return sun azimuth as primary state."""
        if self.data.diagnostics is None:
            return None
        return self.data.diagnostics.get("sun_azimuth")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return elevation, gamma, and all FOV/blind-spot attrs."""
        if self.data.diagnostics is None:
            return None
        diagnostics = self.data.diagnostics
        config = diagnostics.get("configuration", {})

        attrs: dict[str, Any] = {}

        # Elevation
        elevation = diagnostics.get("sun_elevation")
        if elevation is not None:
            attrs["elevation"] = elevation

        # Elevation limits from config
        min_elev = config.get("min_elevation")
        max_elev = config.get("max_elevation")
        if min_elev is not None:
            attrs["min_elevation"] = min_elev
        if max_elev is not None:
            attrs["max_elevation"] = max_elev

        # Gamma
        gamma = diagnostics.get("gamma")
        if gamma is not None:
            attrs["gamma"] = gamma
            abs_gamma = abs(gamma)
            if abs_gamma < 10:
                interpretation = "nearly perpendicular"
            elif abs_gamma < 45:
                interpretation = "oblique angle"
            elif abs_gamma < 80:
                interpretation = "steep angle"
            else:
                interpretation = "nearly parallel"
            attrs["gamma_interpretation"] = interpretation
            attrs["gamma_absolute_angle"] = abs_gamma
            attrs["gamma_direction"] = (
                "left" if gamma < 0 else "right" if gamma > 0 else "center"
            )

        # Azimuth FOV
        window_azi = config.get("azimuth")
        fov_left = config.get("fov_left")
        fov_right = config.get("fov_right")
        if window_azi is not None:
            attrs["window_azimuth"] = window_azi
        if fov_left is not None:
            attrs["fov_left"] = fov_left
        if fov_right is not None:
            attrs["fov_right"] = fov_right

        if window_azi is not None and fov_left is not None and fov_right is not None:
            azi_min = (window_azi - fov_left + 360) % 360
            azi_max = (window_azi + fov_right + 360) % 360
            attrs["azimuth_min"] = azi_min
            attrs["azimuth_max"] = azi_max
            sun_azimuth = diagnostics.get("sun_azimuth")
            if sun_azimuth is not None:
                if azi_min <= azi_max:
                    attrs["in_fov"] = azi_min <= sun_azimuth <= azi_max
                else:
                    attrs["in_fov"] = sun_azimuth >= azi_min or sun_azimuth <= azi_max

        # Blind spot range
        if config.get("enable_blind_spot", False):
            blind_spot_left = config.get("blind_spot_left")
            blind_spot_right = config.get("blind_spot_right")
            if (
                fov_left is not None
                and blind_spot_left is not None
                and blind_spot_right is not None
            ):
                left_edge = fov_left - blind_spot_left
                right_edge = fov_left - blind_spot_right
                attrs["blind_spot_range"] = [right_edge, left_edge]

        return attrs or None


class AdaptiveCoverControlStatusSensor(AdaptiveCoverDiagnosticSensorBase, SensorEntity):
    """Diagnostic sensor combining control status, reason, time window, and sun validity."""

    _attr_translation_key = "control_status"

    def __init__(
        self,
        unique_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        name: str,
        coordinator: AdaptiveDataUpdateCoordinator,
    ) -> None:
        """Initialize control status sensor."""
        super().__init__(
            unique_id,
            hass,
            config_entry,
            coordinator,
            "control_status",
            "mdi:information-outline",
        )
        self._sensor_name = "Control Status"

    @property
    def name(self) -> str:
        """Name of the entity."""
        return self._sensor_name

    @property
    def native_value(self) -> str | None:
        """Return control status enum value."""
        if self.data.diagnostics is None:
            return None
        return self.data.diagnostics.get("control_status")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return reason, time window, sun validity, and delta diagnostics."""
        if self.data.diagnostics is None:
            return None
        diagnostics = self.data.diagnostics
        attrs: dict[str, Any] = {}

        attrs["reason"] = diagnostics.get("control_state_reason")
        attrs["automatic_control_enabled"] = self.coordinator.automatic_control

        # Time window details
        time_window = diagnostics.get("time_window", {})
        attrs["time_window_status"] = (
            "Active" if time_window.get("check_adaptive_time") else "Outside Window"
        )
        attrs["after_start_time"] = time_window.get("after_start_time")
        attrs["before_end_time"] = time_window.get("before_end_time")

        # Sun validity details
        sun_validity = diagnostics.get("sun_validity", {})
        if sun_validity:
            if not sun_validity.get("valid"):
                if sun_validity.get("in_blind_spot"):
                    attrs["sun_validity_status"] = "In Blind Spot"
                elif not sun_validity.get("valid_elevation"):
                    attrs["sun_validity_status"] = "Invalid Elevation"
                else:
                    attrs["sun_validity_status"] = "Invalid"
            else:
                attrs["sun_validity_status"] = "Valid"
            attrs["valid_elevation"] = sun_validity.get("valid_elevation")
            attrs["in_blind_spot"] = sun_validity.get("in_blind_spot")

        # Context: manual covers when in manual override
        if diagnostics.get("control_status") == "manual_override":
            attrs["manual_covers"] = self.data.states.get("manual_list", [])

        # Delta tracking (helps debug delta_too_small states)
        attrs["delta_position_threshold"] = diagnostics.get("delta_position_threshold")
        attrs["delta_time_threshold_minutes"] = diagnostics.get(
            "delta_time_threshold_minutes"
        )
        if "position_delta_from_last_action" in diagnostics:
            attrs["position_delta_from_last_action"] = diagnostics[
                "position_delta_from_last_action"
            ]
        if "seconds_since_last_action" in diagnostics:
            attrs["seconds_since_last_action"] = diagnostics[
                "seconds_since_last_action"
            ]

        attrs["last_updated"] = diagnostics.get("last_updated")

        return attrs


class AdaptiveCoverCalculatedPositionSensor(
    AdaptiveCoverDiagnosticSensorBase, SensorEntity
):
    """Diagnostic sensor for calculated position with full decision chain."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(
        self,
        unique_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        name: str,
        coordinator: AdaptiveDataUpdateCoordinator,
    ) -> None:
        """Initialize calculated position sensor."""
        super().__init__(
            unique_id,
            hass,
            config_entry,
            coordinator,
            "calculated_position",
            "mdi:calculator",
        )
        self._sensor_name = "Calculated Position"

    @property
    def name(self) -> str:
        """Name of the entity."""
        return self._sensor_name

    @property
    def native_value(self) -> float | int | None:
        """Return raw sun-calculated position."""
        if self.data.diagnostics is None:
            return None
        return self.data.diagnostics.get("calculated_position")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return full position decision chain attributes."""
        if self.data.diagnostics is None:
            return None
        diagnostics = self.data.diagnostics
        config = diagnostics.get("configuration", {})
        calculated = diagnostics.get("calculated_position")
        if calculated is None:
            return None

        attrs: dict[str, Any] = {
            "sun_calculated_position": calculated,
            "final_position": self.data.states.get("state"),
            "direct_sun_valid": self.data.states.get("sun_motion"),
        }

        # Position limits
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

        if config.get("inverse_state", False):
            attrs["inverse_state_enabled"] = True

        if config.get("interpolation", False):
            attrs["interpolation_enabled"] = True

        # Climate
        climate_pos = diagnostics.get("calculated_position_climate")
        if climate_pos is not None and climate_pos != calculated:
            attrs["climate_position"] = climate_pos

        if diagnostics.get("climate_strategy") is not None:
            attrs["climate_strategy"] = diagnostics["climate_strategy"]

        if diagnostics.get("position_explanation") is not None:
            attrs["position_explanation"] = diagnostics["position_explanation"]

        calc_details = diagnostics.get("calculation_details")
        if calc_details:
            attrs["edge_case_detected"] = calc_details.get("edge_case_detected")
            attrs["safety_margin"] = calc_details.get("safety_margin")
            attrs["effective_distance"] = calc_details.get("effective_distance")
            attrs["window_depth_contribution"] = calc_details.get(
                "window_depth_contribution"
            )
            attrs["sill_height_offset"] = calc_details.get("sill_height_offset")

        return attrs


class AdaptiveCoverLastActionSensor(AdaptiveCoverDiagnosticSensorBase, SensorEntity):
    """Sensor showing the last cover action performed."""

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
            coordinator,
            "last_cover_action",
            "mdi:history",
        )
        self._sensor_name = "Last Cover Action"

    @property
    def name(self) -> str:
        """Name of the entity."""
        return self._sensor_name

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        if not self.data or not self.data.diagnostics:
            return None
        diagnostics = self.data.diagnostics

        action = diagnostics.get("last_cover_action")
        if not action or not action.get("entity_id"):
            return "No action recorded"

        service = action.get("service", "unknown")
        entity = action.get("entity_id", "unknown")
        timestamp_str = action.get("timestamp", "")

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

        if action.get("threshold_used") is not None:
            attrs["threshold_used"] = action.get("threshold_used")
            attrs["threshold_comparison"] = (
                f"{action.get('calculated_position')} >= {action.get('threshold_used')}"
            )

        return attrs


class AdaptiveCoverManualOverrideEndSensor(
    AdaptiveCoverDiagnosticSensorBase, SensorEntity
):
    """Sensor showing when manual override will expire."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
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
            coordinator,
            "manual_override_end_time",
            "mdi:timer-outline",
        )
        self._sensor_name = "Manual Override End Time"

    @property
    def name(self) -> str:
        """Name of the entity."""
        return self._sensor_name

    @property
    def native_value(self):
        """Return latest manual override expiry time, or None if no override is active."""
        times = self.coordinator.manager.manual_control_time
        if not times:
            return None
        duration = self.coordinator.manager.reset_duration
        return max(t + duration for t in times.values())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return per-entity override expiry times."""
        times = self.coordinator.manager.manual_control_time
        if not times:
            return None
        duration = self.coordinator.manager.reset_duration
        return {
            "per_entity": {
                entity_id: (t + duration).isoformat() for entity_id, t in times.items()
            }
        }


class AdaptiveCoverPositionVerificationSensor(
    AdaptiveCoverDiagnosticSensorBase, SensorEntity
):
    """Diagnostic sensor combining position verification retries and last verification time."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "retries"
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
            coordinator,
            "position_verification",
            "mdi:refresh",
        )
        self._sensor_name = "Position Verification"

    @property
    def name(self) -> str:
        """Name of the entity."""
        return self._sensor_name

    @property
    def native_value(self):
        """Return maximum retry count across all entities."""
        if not self.coordinator._retry_counts:
            return 0
        return max(self.coordinator._retry_counts.values())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return verification details and retry counts."""
        attrs: dict[str, Any] = {
            "max_retries": self.coordinator._max_retries,
            "retries_remaining": max(
                0,
                self.coordinator._max_retries
                - max(self.coordinator._retry_counts.values(), default=0),
            ),
        }

        if self.coordinator._retry_counts:
            attrs["per_entity_retries"] = dict(self.coordinator._retry_counts)

        if self.coordinator._last_verification:
            attrs["last_verification"] = max(
                self.coordinator._last_verification.values()
            ).isoformat()
            attrs["per_entity_verification"] = {
                entity_id: t.isoformat()
                for entity_id, t in self.coordinator._last_verification.items()
            }

        return attrs


class AdaptiveCoverMotionStatusSensor(AdaptiveCoverDiagnosticSensorBase, SensorEntity):
    """Diagnostic sensor showing current motion control state."""

    _attr_native_unit_of_measurement = ""
    _attr_should_poll = False
    _attr_translation_key = "motion_status"

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
            coordinator,
            "motion_status",
            "mdi:motion-sensor",
        )
        self._sensor_name = "Motion Status"

    @property
    def name(self) -> str:
        """Name of the entity."""
        return self._sensor_name

    @property
    def native_value(self) -> str:
        """Return motion control state as a human-readable string."""
        if self.coordinator._last_motion_time is None:
            return "waiting_for_data"

        if self.coordinator.is_motion_detected:
            return "motion_detected"

        task = self.coordinator._motion_timeout_task
        if task is not None and not task.done():
            return "timeout_pending"

        if self.coordinator._motion_timeout_active:
            return "no_motion"

        return "waiting_for_data"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return motion timeout config, end time, and last motion time."""
        attrs: dict[str, Any] = {
            "motion_timeout_seconds": self.coordinator._motion_timeout_seconds,
        }

        if self.coordinator._last_motion_time is not None:
            task = self.coordinator._motion_timeout_task
            timeout_pending = task is not None and not task.done()

            if timeout_pending or self.coordinator._motion_timeout_active:
                end_ts = (
                    self.coordinator._last_motion_time
                    + self.coordinator._motion_timeout_seconds
                )
                attrs["motion_timeout_end_time"] = dt_util.utc_from_timestamp(
                    end_ts
                ).isoformat()

            attrs["last_motion_time"] = dt_util.utc_from_timestamp(
                self.coordinator._last_motion_time
            ).isoformat()

        return attrs


class AdaptiveCoverForceOverrideTriggerSensor(
    AdaptiveCoverDiagnosticSensorBase, SensorEntity
):
    """Sensor showing how many force override sensors are currently active."""

    _attr_state_class = SensorStateClass.MEASUREMENT
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
            coordinator,
            "force_override_triggers",
            "mdi:shield-alert-outline",
        )
        self._sensor_name = "Force Override Triggers"

    @property
    def name(self) -> str:
        """Name of the entity."""
        return self._sensor_name

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
    def extra_state_attributes(self) -> dict[str, Any] | None:
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


class AdaptiveCoverClimateStatusSensor(AdaptiveCoverDiagnosticSensorBase, SensorEntity):
    """Diagnostic sensor combining climate conditions and active temperature."""

    def __init__(
        self,
        config_entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        name: str,
        coordinator: AdaptiveDataUpdateCoordinator,
        hass_ref: HomeAssistant,
    ):
        """Initialize the sensor."""
        super().__init__(
            config_entry_id,
            hass,
            config_entry,
            coordinator,
            "climate_status",
            "mdi:weather-partly-cloudy",
        )
        self._sensor_name = "Climate Status"
        self._temp_unit = hass_ref.config.units.temperature_unit

    @property
    def name(self) -> str:
        """Name of the entity."""
        return self._sensor_name

    @property
    def native_value(self) -> str | None:
        """Return climate conditions string."""
        if self.data.diagnostics is None:
            return None
        diagnostics = self.data.diagnostics
        data = diagnostics.get("climate_conditions")
        if data is None:
            return None

        if data.get("is_summer"):
            return "Summer Mode"
        if data.get("is_winter"):
            return "Winter Mode"
        return "Intermediate"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return active temperature and all climate detail attributes."""
        if self.data.diagnostics is None:
            return None
        diagnostics = self.data.diagnostics

        attrs: dict[str, Any] = {}

        active_temp = diagnostics.get("active_temperature")
        if active_temp is not None:
            attrs["active_temperature"] = active_temp
            attrs["temperature_unit"] = self._temp_unit

        temp_details = diagnostics.get("temperature_details", {})
        if temp_details:
            attrs["indoor_temperature"] = temp_details.get("inside_temperature")
            attrs["outdoor_temperature"] = temp_details.get("outside_temperature")
            attrs["temp_switch"] = temp_details.get("temp_switch")

        climate_conditions = diagnostics.get("climate_conditions", {})
        if climate_conditions:
            attrs["is_presence"] = climate_conditions.get("is_presence")
            attrs["is_sunny"] = climate_conditions.get("is_sunny")
            if climate_conditions.get("lux_active") is not None:
                attrs["lux_active"] = climate_conditions["lux_active"]
            if climate_conditions.get("irradiance_active") is not None:
                attrs["irradiance_active"] = climate_conditions["irradiance_active"]

        return attrs or None


class AdaptiveCoverPositionExplanationSensor(
    AdaptiveCoverDiagnosticSensorBase, SensorEntity
):
    """Diagnostic sensor showing the current position decision explanation.

    State is the full position_explanation string so HA records it in state
    history, enabling time-based troubleshooting.
    """

    _attr_native_unit_of_measurement = ""  # Text sensor — excluded from logbook

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
            coordinator,
            "position_explanation",
            "mdi:text-box-outline",
        )
        self._sensor_name = "Position Explanation"

    @property
    def name(self) -> str:
        """Name of the entity."""
        return self._sensor_name

    @property
    def native_value(self) -> str | None:
        """Return the position explanation string as the sensor state."""
        if not self.data or not self.data.diagnostics:
            return None
        return self.data.diagnostics.get("position_explanation")


class AdaptiveCoverLastSkippedActionSensor(
    AdaptiveCoverDiagnosticSensorBase, SensorEntity
):
    """Diagnostic sensor showing why the last cover move was skipped.

    Records when automatic cover movement was suppressed and the reason,
    making it possible to debug why a cover did not move when expected.
    """

    _attr_native_unit_of_measurement = ""  # Text sensor — excluded from logbook

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
            coordinator,
            "last_skipped_action",
            "mdi:debug-step-over",
        )
        self._sensor_name = "Last Skipped Action"

    @property
    def name(self) -> str:
        """Name of the entity."""
        return self._sensor_name

    @property
    def native_value(self) -> str | None:
        """Return the skip reason as the sensor state."""
        if not self.data or not self.data.diagnostics:
            return None
        action = self.data.diagnostics.get("last_skipped_action")
        if not action or not action.get("entity_id"):
            return "No action skipped"
        return action.get("reason")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return entity, position, and timestamp of the skipped action."""
        if not self.data or not self.data.diagnostics:
            return None
        action = self.data.diagnostics.get("last_skipped_action")
        if not action or not action.get("entity_id"):
            return None
        return {
            "entity_id": action.get("entity_id"),
            "calculated_position": action.get("calculated_position"),
            "timestamp": action.get("timestamp"),
        }
