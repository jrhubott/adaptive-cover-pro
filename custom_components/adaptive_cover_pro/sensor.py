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
import datetime as dt

from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CLIMATE_MODE,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_CLOUD_SUPPRESSION,
    CONF_ENABLE_GLARE_ZONES,
    CONF_ENABLE_SUN_TRACKING,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_MOTION_SENSORS,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_IS_RAINING_SENSOR,
    CONF_WEATHER_IS_WINDY_SENSOR,
    CONF_WEATHER_RAIN_SENSOR,
    CONF_WEATHER_SEVERE_SENSORS,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    CUSTOM_POSITION_SLOTS,
    DOMAIN,
)
from .coordinator import AdaptiveDataUpdateCoordinator
from .entity_base import AdaptiveCoverDiagnosticSensorBase, AdaptiveCoverSensorBase
from .enums import ControlMethod


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
        "position_explanation",
        "calculated_position",
        "Control_Method",
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

    # Decision Trace
    entities.append(
        AdaptiveCoverDecisionTraceSensor(
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

    # Motion Status: always created; shows not_configured when no sensors set
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
    _attr_suggested_display_precision = 0  # Positions are integers (0–100%)

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
        self._sensor_name = "Target Position"

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
        attrs = dict(self.data.attributes) if self.data.attributes else {}
        # Enrich with pipeline context
        attrs["control_method"] = self.data.states.get("control")
        pipeline_result = self.coordinator._pipeline_result
        if pipeline_result is not None:
            attrs["reason"] = pipeline_result.reason
        diagnostics = (
            self.coordinator.data.diagnostics if self.coordinator.data else None
        )
        if diagnostics:
            position_explanation = diagnostics.get("position_explanation")
            if position_explanation is not None:
                attrs["position_explanation"] = position_explanation
            attrs["raw_calculated_position"] = diagnostics.get("calculated_position")
            calc_details = diagnostics.get("calculation_details")
            if calc_details:
                attrs["edge_case_detected"] = calc_details.get("edge_case_detected")
                attrs["safety_margin"] = calc_details.get("safety_margin")
                attrs["effective_distance"] = calc_details.get("effective_distance")

        # Actual positions — show what every managed cover currently reports
        snapshot = self.coordinator._snapshot
        if snapshot and snapshot.cover_positions:
            actual_positions = dict(snapshot.cover_positions)
            attrs["actual_positions"] = actual_positions

            # all_at_target: True when every cover with a known position is
            # within tolerance of the coordinator's current target position.
            target = self.data.states.get("state")
            tolerance = self.coordinator._cmd_svc._position_tolerance
            if target is not None:
                try:
                    target_int = int(target)
                    attrs["all_at_target"] = all(
                        pos is not None and abs(pos - target_int) <= tolerance
                        for pos in actual_positions.values()
                    )
                except (TypeError, ValueError):
                    attrs["all_at_target"] = None
            else:
                attrs["all_at_target"] = None

        return attrs


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

    @property
    def extra_state_attributes(self) -> dict[str, float] | None:
        """Expose the sun's azimuth and elevation at this entry/exit moment.

        Returns None when the sun never enters the FOV today (state is also
        unknown in that case).
        """
        pos = self.data.states.get(f"{self.key}_position")
        if pos is None:
            return None
        return {
            "azimuth": round(float(pos["azimuth"]), 1),
            "elevation": round(float(pos["elevation"]), 1),
        }


# ---------------------------------------------------------------------------
# Diagnostic sensors
# ---------------------------------------------------------------------------


class AdaptiveCoverSunPositionSensor(AdaptiveCoverDiagnosticSensorBase, SensorEntity):
    """Diagnostic sensor combining sun azimuth, elevation, and gamma."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "°"
    _attr_suggested_display_precision = 1  # 0.1° is sufficient for azimuth display

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
            attrs["elevation"] = round(elevation, 1)

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
            gamma = round(gamma, 1)
            attrs["gamma"] = gamma
            abs_gamma = round(abs(gamma), 1)
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
    AdaptiveCoverDiagnosticSensorBase, SensorEntity, RestoreEntity
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

    async def async_added_to_hass(self) -> None:
        """Restore manual override state from last known HA state after a reboot."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None:
            return
        per_entity = (last.attributes or {}).get("per_entity") or {}
        self._restore_from_attributes(per_entity)

    def _restore_from_attributes(self, per_entity: dict[str, str]) -> None:
        """Rehydrate coordinator manager from a per_entity expiry dict.

        per_entity maps cover entity_id → ISO-8601 UTC expiry string.
        Entries that are expired or not in the current cover set are dropped.
        """
        now = dt.datetime.now(dt.UTC)
        manager = self.coordinator.manager
        restored_any = False

        for eid, expiry_iso in per_entity.items():
            if eid not in manager.covers:
                continue
            expiry = dt.datetime.fromisoformat(expiry_iso)
            if expiry <= now:
                continue
            started_at = expiry - manager.reset_duration
            manager.manual_control[eid] = True
            manager.manual_control_time[eid] = started_at
            manager._record_event(
                eid,
                "restored",
                our_state=None,
                new_position=None,
                reason="restored from RestoreEntity after reboot",
            )
            restored_any = True

        if restored_any:
            self.async_write_ha_state()


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
    def native_value(self) -> int:
        """Return the maximum active retry count across all tracked entities."""
        entities = self.coordinator.entities
        if not entities:
            return 0
        return max(
            self.coordinator._cmd_svc.get_diagnostics(e)["retry_count"]
            for e in entities
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return per-entity reconciliation diagnostics."""
        entities = self.coordinator.entities
        if not entities:
            return {}

        per_entity = {e: self.coordinator._cmd_svc.get_diagnostics(e) for e in entities}

        # Aggregate last reconciliation time
        recon_times = [
            d["last_reconcile_time"]
            for d in per_entity.values()
            if d["last_reconcile_time"] is not None
        ]

        attrs: dict[str, Any] = {
            "max_retries": self.coordinator._cmd_svc._max_retries,
            "per_entity": per_entity,
        }
        if recon_times:
            attrs["last_reconcile_time"] = max(recon_times)

        return attrs


class AdaptiveCoverMotionStatusSensor(AdaptiveCoverDiagnosticSensorBase, SensorEntity):
    """Diagnostic sensor showing current motion control state."""

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "motion_status"
    _attr_options = [
        "not_configured",
        "motion_detected",
        "timeout_pending",
        "no_motion",
        "waiting_for_data",
    ]

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
        if not self.config_entry.options.get(CONF_MOTION_SENSORS):
            return "not_configured"
        mgr = self.coordinator._motion_mgr
        if mgr._motion_timeout_active:
            return "no_motion"

        if mgr.last_motion_time is None:
            return "waiting_for_data"

        if self.coordinator.is_motion_detected:
            return "motion_detected"

        task = mgr._motion_timeout_task
        if task is not None and not task.done():
            return "timeout_pending"

        return "waiting_for_data"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return motion timeout config, end time, and last motion time."""
        if not self.config_entry.options.get(CONF_MOTION_SENSORS):
            return None
        mgr = self.coordinator._motion_mgr
        attrs: dict[str, Any] = {
            "motion_timeout_seconds": mgr._timeout_seconds,
        }

        if mgr.last_motion_time is not None:
            task = mgr._motion_timeout_task
            timeout_pending = task is not None and not task.done()

            if timeout_pending or mgr._motion_timeout_active:
                end_ts = mgr.last_motion_time + mgr._timeout_seconds
                attrs["motion_timeout_end_time"] = dt_util.utc_from_timestamp(
                    end_ts
                ).isoformat()

            attrs["last_motion_time"] = dt_util.utc_from_timestamp(
                mgr.last_motion_time
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
            attrs["active_temperature"] = active_temp  # already rounded in builder
            attrs["temperature_unit"] = self._temp_unit

        temp_details = diagnostics.get("temperature_details", {})
        if temp_details:
            attrs["indoor_temperature"] = temp_details.get(
                "inside_temperature"
            )  # rounded in builder
            attrs["outdoor_temperature"] = temp_details.get(
                "outside_temperature"
            )  # rounded in builder
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


class AdaptiveCoverDecisionTraceSensor(AdaptiveCoverDiagnosticSensorBase, SensorEntity):
    """Diagnostic sensor showing the full pipeline decision trace."""

    _attr_translation_key = "decision_trace"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [m.value for m in ControlMethod] + ["unknown"]

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
            "decision_trace",
            "mdi:list-status",
        )
        self._sensor_name = "Decision Trace"

    @property
    def name(self) -> str:
        """Name of the entity."""
        return self._sensor_name

    @property
    def native_value(self) -> str:
        """Return the winning handler name."""
        result = self.coordinator._pipeline_result
        if result is None:
            return "unknown"
        return result.control_method.value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the full decision trace, sun position, and sunset/default context."""
        attrs: dict[str, Any] = {}
        result = self.coordinator._pipeline_result
        if result:
            trace = []
            for step in result.decision_trace:
                trace.append(
                    {
                        "handler": step.handler,
                        "matched": step.matched,
                        "reason": step.reason,
                        "position": step.position,
                    }
                )
            attrs["trace"] = trace
            attrs["reason"] = result.reason
            attrs["bypass_auto_control"] = result.bypass_auto_control

            # Sunset-aware default context — lets users see at a glance what
            # the effective default is this cycle and why.
            attrs["default_position"] = result.default_position
            attrs["is_sunset_active"] = result.is_sunset_active
            attrs["configured_default"] = result.configured_default
            attrs["configured_sunset_pos"] = result.configured_sunset_pos

        # Operational time window
        attrs["in_time_window"] = self.coordinator.check_adaptive_time

        # Configured pipeline handlers — read by the card's decision-strip to
        # hide handlers the user hasn't set up. Names match the card-normalized
        # form (force, manual, motion, cloud) — not the pipeline's internal
        # names (force_override, manual_override, motion_timeout, cloud_suppression).
        attrs["enabled_handlers"] = self._configured_handlers()

        diagnostics = self.coordinator.data.diagnostics if self.coordinator.data else {}
        if diagnostics:
            attrs["sun_azimuth"] = diagnostics.get("sun_azimuth")
            attrs["sun_elevation"] = diagnostics.get("sun_elevation")
            attrs["gamma"] = diagnostics.get("gamma")

            sun_validity = diagnostics.get("sun_validity", {})
            if sun_validity:
                attrs["in_field_of_view"] = sun_validity.get("valid")
                attrs["elevation_valid"] = sun_validity.get("valid_elevation")
                attrs["in_blind_spot"] = sun_validity.get("in_blind_spot")
                # True when between (sunset+offset) and (sunrise+offset)
                attrs["sunset_window_active"] = sun_validity.get("sunset_window_active")

            if self.coordinator._cover_data is not None:
                attrs["direct_sun_valid"] = (
                    self.coordinator._cover_data.direct_sun_valid
                )

        return attrs or None

    def _configured_handlers(self) -> list[str]:
        """Return the list of pipeline handlers that are configured to fire.

        Configuration-based, not runtime-based: a handler is "enabled" if the
        user has set it up in options, regardless of whether its runtime
        switch is currently on. Names match the card's normalized handler
        names so the card can use the list directly without translation.
        """
        opts = self.config_entry.options
        enabled: list[str] = ["manual", "default"]

        if opts.get(CONF_FORCE_OVERRIDE_SENSORS):
            enabled.append("force")

        if any(
            opts.get(k)
            for k in (
                CONF_WEATHER_ENTITY,
                CONF_WEATHER_WIND_SPEED_SENSOR,
                CONF_WEATHER_RAIN_SENSOR,
                CONF_WEATHER_IS_RAINING_SENSOR,
                CONF_WEATHER_IS_WINDY_SENSOR,
                CONF_WEATHER_SEVERE_SENSORS,
            )
        ):
            enabled.append("weather")

        if any(
            opts.get(slot_keys["sensor"])
            and opts.get(slot_keys["position"]) is not None
            for slot_keys in CUSTOM_POSITION_SLOTS.values()
        ):
            enabled.append("custom_position")

        if opts.get(CONF_MOTION_SENSORS):
            enabled.append("motion")

        if opts.get(CONF_CLOUD_SUPPRESSION) and opts.get(CONF_CLOUD_COVERAGE_ENTITY):
            enabled.append("cloud")

        if opts.get(CONF_CLIMATE_MODE):
            enabled.append("climate")

        if opts.get(CONF_ENABLE_GLARE_ZONES):
            enabled.append("glare_zone")

        if opts.get(CONF_ENABLE_SUN_TRACKING, True):
            enabled.append("solar")

        return enabled


class AdaptiveCoverLastSkippedActionSensor(
    AdaptiveCoverDiagnosticSensorBase, SensorEntity
):
    """Diagnostic sensor showing why the last cover move was skipped.

    Records when automatic cover movement was suppressed and the reason,
    making it possible to debug why a cover did not move when expected.
    """

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
        """Return diagnostic context for the last skipped action.

        Always-present attributes (when a skip has been recorded):
            entity_id, calculated_position, current_position,
            trigger, inverse_state_applied, timestamp.

        Reason-specific attributes (present only when relevant):
            delta_too_small   → position_delta, min_delta_required
            time_delta_too_small → elapsed_minutes, time_threshold_minutes
        """
        if not self.data or not self.data.diagnostics:
            return None
        action = self.data.diagnostics.get("last_skipped_action")
        if not action or not action.get("entity_id"):
            return None

        attrs: dict[str, Any] = {
            "entity_id": action.get("entity_id"),
            "calculated_position": action.get("calculated_position"),
            "current_position": action.get("current_position"),
            "trigger": action.get("trigger"),
            "inverse_state_applied": action.get("inverse_state_applied", False),
            "timestamp": action.get("timestamp"),
        }

        # Reason-specific extras — only add when present in the record
        for key in (
            "position_delta",
            "min_delta_required",
            "elapsed_minutes",
            "time_threshold_minutes",
        ):
            if key in action:
                attrs[key] = action[key]

        return attrs
