"""Sensor platform for Adaptive Cover Pro integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_FRIENDLY_NAME, MATCH_ALL, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import (
    BLIND_SPOT_SLOTS,
    CONF_CLIMATE_MODE,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_CLOUD_SUPPRESSION,
    CONF_ENABLE_GLARE_ZONES,
    CONF_ENABLE_SUN_TRACKING,
    CONF_IRRADIANCE_ENTITY,
    CONF_IS_SUNNY_SENSOR,
    CONF_LUX_ENTITY,
    CONF_OUTSIDE_THRESHOLD,
    CONF_SENSOR_TYPE,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_IS_RAINING_SENSOR,
    CONF_WEATHER_IS_WINDY_SENSOR,
    CONF_WEATHER_RAIN_SENSOR,
    CONF_WEATHER_SEVERE_SENSORS,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    CUSTOM_POSITION_SLOTS,
    DEFAULT_CUSTOM_POSITION_ENABLED,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
    DEFAULT_TEMPLATE_COMBINE_MODE,
    DEGREES_IN_CIRCLE,
)
from .coordinator import AdaptiveConfigEntry, AdaptiveDataUpdateCoordinator
from .entity_base import AdaptiveCoverDiagnosticSensorBase, AdaptiveCoverSensorBase
from .const import ControlMethod
from .managers.manual_override.expiry import (
    expiry_for_started_at,
    started_at_for_expiry,
)
from .helpers import (
    custom_position_slot_configured,
    custom_position_slot_name,
    custom_position_slot_sensors,
    motion_entities,
)
from .reason_i18n import Reason, render, reason_to_dict
from .templates import is_template_string
from .unit_system import length_display_unit, to_display_length


def _localized_reason(
    coordinator: AdaptiveDataUpdateCoordinator,
    payload: Reason | None,
    fallback: str,
) -> str:
    """Render a reason payload in the instance language (issue #882).

    Uses the coordinator's primed ``_reason_labels`` overlay (``None`` → English
    defaults, byte-identical). Legacy payload-less reasons keep their English
    ``fallback`` string. Shared by the cover-position and decision-trace sensors
    so the render path lives in exactly one place.
    """
    if payload is None:
        return fallback
    return render(payload, coordinator._reason_labels)  # noqa: SLF001


def _reason_to_dict_attrs(payload: Reason) -> dict[str, Any]:
    """Map a reason payload to the additive ``reason_code`` + ``reason_params`` attrs.

    ``reason_to_dict`` yields a JSON-safe ``{"code", "params"}`` payload (nested
    fragments preserved); this flattens it into the two attribute names the
    companion card reads to localize with its own templates (issue #882).
    """
    payload_dict = reason_to_dict(payload)
    return {
        "reason_code": payload_dict["code"],
        "reason_params": payload_dict["params"],
    }


# ---------------------------------------------------------------------------
# Description dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _SensorSpec:
    """Spec for one Adaptive Cover Pro sensor.

    `suffix` becomes the locked unique_id (`f"{entry_id}_{suffix}"`) — DO NOT
    rename without a migration in `migrations.py`. `value_fn` and `attrs_fn`
    receive the sensor instance and return native_value / extra_state_attributes
    respectively. Class-level HA attrs (state_class, device_class, …) are
    applied as instance attributes during __init__ so a single generic class
    can host every sensor.
    """

    suffix: str  # → unique_id; LOCKED
    display_name: str
    icon: str | None
    value_fn: Callable[[Any], Any]
    attrs_fn: Callable[[Any], Mapping[str, Any] | None] | None = None
    translation_key: str | None = None
    state_class: SensorStateClass | None = None
    device_class: SensorDeviceClass | None = None
    unit: str | None = None
    suggested_display_precision: int | None = None
    options: tuple[str, ...] | None = None
    should_poll: bool = True
    enabled_when: Callable[[ConfigEntry], bool] = field(default=lambda _: True)
    diagnostic: bool = (
        True  # False → uses AdaptiveCoverSensorBase (Cover_Position et al.)
    )
    unrecorded_attributes: frozenset[str] = frozenset()


def _exposes_dual_axis_sensor(entry: ConfigEntry) -> bool:
    """Gate the dual-axis Target Tilt sensor on the cover-type policy.

    Modelled on ``binary_sensor._glare_zones_enabled_for_blind`` so a new
    cover type opts in by flipping ``CoverTypePolicy.exposes_dual_axis_sensor``
    on its subclass — not by editing sensor.py.
    """
    from .cover_types import POLICY_REGISTRY, get_policy

    sensor_type = entry.data.get(CONF_SENSOR_TYPE)
    if sensor_type not in POLICY_REGISTRY:
        return False
    return get_policy(sensor_type).exposes_dual_axis_sensor


# ---------------------------------------------------------------------------
# Generic sensor classes — one per base
# ---------------------------------------------------------------------------


def _apply_spec_attrs(entity: SensorEntity, spec: _SensorSpec) -> None:
    """Set per-instance HA attrs from a spec.

    Instance attrs win over class attrs in attribute lookup, so this is
    equivalent to setting them on the class — but it lets one generic class
    serve every sensor.
    """
    if spec.translation_key is not None:
        entity._attr_translation_key = spec.translation_key
    if spec.state_class is not None:
        entity._attr_state_class = spec.state_class
    if spec.device_class is not None:
        entity._attr_device_class = spec.device_class
    if spec.unit is not None:
        entity._attr_native_unit_of_measurement = spec.unit
    if spec.suggested_display_precision is not None:
        entity._attr_suggested_display_precision = spec.suggested_display_precision
    if spec.options is not None:
        entity._attr_options = list(spec.options)
    if not spec.should_poll:
        entity._attr_should_poll = False


class _ACPSensor(AdaptiveCoverSensorBase, SensorEntity):
    """Generic standard sensor (Cover_Position, Start Sun, End Sun)."""

    def __init__(
        self,
        entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        coordinator: AdaptiveDataUpdateCoordinator,
        spec: _SensorSpec,
    ) -> None:
        """Initialize from a spec."""
        super().__init__(
            entry_id, hass, config_entry, coordinator, spec.suffix, spec.icon
        )
        self._spec = spec
        self._sensor_name = spec.display_name
        _apply_spec_attrs(self, spec)

    @property
    def name(self) -> str:
        """Display name (combined with device name when has_entity_name=True)."""
        return self._sensor_name

    @property
    def native_value(self) -> Any:
        """Return the spec-driven value."""
        return self._spec.value_fn(self)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return spec-driven attrs, or None if no attrs_fn."""
        if self._spec.attrs_fn is None:
            return None
        return self._spec.attrs_fn(self)


class _ACPDiagnosticSensor(AdaptiveCoverDiagnosticSensorBase, SensorEntity):
    """Generic diagnostic sensor — same as _ACPSensor but with diagnostic base."""

    def __init__(
        self,
        entry_id: str,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        coordinator: AdaptiveDataUpdateCoordinator,
        spec: _SensorSpec,
    ) -> None:
        """Initialize from a spec."""
        super().__init__(
            entry_id, hass, config_entry, coordinator, spec.suffix, spec.icon
        )
        self._spec = spec
        self._sensor_name = spec.display_name
        _apply_spec_attrs(self, spec)

    @property
    def name(self) -> str:
        """Display name."""
        return self._sensor_name

    @property
    def native_value(self) -> Any:
        """Return the spec-driven value."""
        return self._spec.value_fn(self)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return spec-driven attrs, or None if no attrs_fn."""
        if self._spec.attrs_fn is None:
            return None
        return self._spec.attrs_fn(self)


class _ACPRestorableDiagnosticSensor(_ACPDiagnosticSensor, RestoreEntity):
    """Diagnostic sensor that restores a per_entity attrs dict on startup.

    Used by `manual_override_end_time` to repopulate the manual-override
    manager after HA reboots. Subclasses must implement `_restore_from_attributes`.
    """

    async def async_added_to_hass(self) -> None:
        """Restore prior per_entity expiry dict, if any."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None:
            return
        per_entity = (last.attributes or {}).get("per_entity") or {}
        self._restore_from_attributes(per_entity)

    def _restore_from_attributes(self, per_entity: Mapping[str, str]) -> None:
        """Override in subclass to consume the restored per_entity dict."""


class _ManualOverrideEndSensor(_ACPRestorableDiagnosticSensor):
    """Concrete: rehydrate manual-override manager from per_entity expiry dict."""

    def _restore_from_attributes(self, per_entity: Mapping[str, str]) -> None:
        """Push prior per-entity expiry timestamps back into the manager.

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
            started_at = started_at_for_expiry(expiry, manager.reset_duration)
            manager.manual_control[eid] = True
            manager.manual_control_time[eid] = started_at
            manager._record_event(  # noqa: SLF001
                eid,
                "restored",
                our_state=None,
                new_position=None,
                reason="restored from RestoreEntity after reboot",
            )
            restored_any = True

        if restored_any:
            self.async_write_ha_state()


# ---------------------------------------------------------------------------
# native_value / extra_state_attributes bodies
# ---------------------------------------------------------------------------


def _cover_position_value(s: _ACPSensor) -> Any:
    held = s.data.states.get("held_position")
    if held is not None:
        return held
    return s.data.states["state"]


def _compute_distance_attrs(
    coordinator: AdaptiveDataUpdateCoordinator,
    snapshot,
    target_position: Any,
) -> dict[str, Any] | None:
    """Build target_distance / actual_distances / distance_unit, or None to skip.

    Translates the published position percentage into a physical distance using
    the policy's lift-axis travel range. Inverse-agnostic: 100% always maps to
    the full configured dimension regardless of inverse_state, since the value
    is literal arithmetic on what the sensor publishes.
    """
    options = coordinator.config_entry.options
    dim_m = coordinator._policy.lift_travel_metres(  # noqa: SLF001
        coordinator._config_service, options  # noqa: SLF001
    )
    if dim_m is None or dim_m <= 0 or target_position is None:
        return None
    try:
        target_pct = float(target_position)
    except (TypeError, ValueError):
        return None
    hass = coordinator.hass
    attrs: dict[str, Any] = {
        "target_distance": round(
            to_display_length(dim_m * target_pct / 100.0, hass), 2
        ),
        "distance_unit": length_display_unit(hass),
    }
    if snapshot and snapshot.cover_positions:
        attrs["actual_distances"] = {
            eid: (
                None
                if pos is None
                else round(to_display_length(dim_m * pos / 100.0, hass), 2)
            )
            for eid, pos in snapshot.cover_positions.items()
        }
    return attrs


def _cover_position_attrs(s: _ACPSensor) -> Mapping[str, Any] | None:
    attrs = dict(s.data.attributes) if s.data.attributes else {}
    attrs["control_method"] = s.data.states.get("control")
    pipeline_result = s.coordinator._pipeline_result  # noqa: SLF001
    if pipeline_result is not None:
        attrs["reason"] = _localized_reason(
            s.coordinator, pipeline_result.reason_payload, pipeline_result.reason
        )
    diagnostics = s.coordinator.data.diagnostics if s.coordinator.data else None
    if diagnostics:
        position_explanation = diagnostics.get("position_explanation")
        if position_explanation is not None:
            attrs["position_explanation"] = position_explanation
        attrs["raw_calculated_position"] = diagnostics.get("calculated_position")
        # Pre-interpolation logical target (issue #911); the companion card shows
        # this as the primary position, demoting the interpolated `state`.
        attrs["linear_position"] = diagnostics.get("linear_position")
        calc_details = diagnostics.get("calculation_details")
        if calc_details:
            attrs["edge_case_detected"] = calc_details.get("edge_case_detected")
            attrs["safety_margin"] = calc_details.get("safety_margin")
            # The trace key is suffixed (effective_distance_m), but the companion
            # card depends on the legacy `effective_distance` attribute name on the
            # cover_position sensor — map it back so the card stays byte-identical.
            attrs["effective_distance"] = calc_details.get("effective_distance_m")

    # Synthetic opening/closing indicator for no-feedback covers. Present only
    # while a cover is mid-transit (gated on ``waiting`` inside transit_states),
    # so the companion card can render "Opening…/Closing…" on covers that report
    # neither position nor an opening/closing state. Emitted only when non-empty
    # to keep the attribute set lean.
    transit = s.coordinator._cmd_svc.transit_states()  # noqa: SLF001
    if transit:
        attrs["transit_states"] = transit

    snapshot = s.coordinator._snapshot  # noqa: SLF001
    if snapshot and snapshot.cover_positions:
        actual_positions = dict(snapshot.cover_positions)
        attrs["actual_positions"] = actual_positions

        # all_at_target: True when every cover with a known position is within
        # tolerance of the coordinator's current target position.
        target = s.data.states.get("state")
        tolerance = s.coordinator._cmd_svc._position_tolerance  # noqa: SLF001
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

    target_pos = s.data.states.get("held_position")
    if target_pos is None:
        target_pos = s.data.states.get("state")
    distance_attrs = _compute_distance_attrs(s.coordinator, snapshot, target_pos)
    if distance_attrs is not None:
        attrs.update(distance_attrs)

    return attrs


def _cover_tilt_value(s: _ACPSensor) -> int | None:
    pr = s.coordinator._pipeline_result  # noqa: SLF001
    return None if pr is None else pr.tilt


def _time_value(key: str) -> Callable[[_ACPSensor], Any]:
    def _v(s: _ACPSensor) -> Any:
        return s.data.states[key]

    return _v


def _time_attrs(key: str) -> Callable[[_ACPSensor], Mapping[str, float] | None]:
    def _a(s: _ACPSensor) -> Mapping[str, float] | None:
        pos = s.data.states.get(f"{key}_position")
        if pos is None:
            return None
        return {
            "azimuth": round(float(pos["azimuth"]), 1),
            "elevation": round(float(pos["elevation"]), 1),
        }

    return _a


def _sun_position_value(s: _ACPDiagnosticSensor) -> float | None:
    if s.data.diagnostics is None:
        return None
    return s.data.diagnostics.get("sun_azimuth")


def _sun_position_attrs(s: _ACPDiagnosticSensor) -> Mapping[str, Any] | None:
    if s.data.diagnostics is None:
        return None
    diagnostics = s.data.diagnostics
    config = diagnostics.get("configuration", {})
    attrs: dict[str, Any] = {}

    elevation = diagnostics.get("sun_elevation")
    if elevation is not None:
        attrs["elevation"] = round(elevation, 1)

    min_elev = config.get("min_elevation")
    max_elev = config.get("max_elevation")
    if min_elev is not None:
        attrs["min_elevation"] = min_elev
    if max_elev is not None:
        attrs["max_elevation"] = max_elev

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
        azi_min = (window_azi - fov_left + DEGREES_IN_CIRCLE) % DEGREES_IN_CIRCLE
        azi_max = (window_azi + fov_right + DEGREES_IN_CIRCLE) % DEGREES_IN_CIRCLE
        attrs["azimuth_min"] = azi_min
        attrs["azimuth_max"] = azi_max
        sun_azimuth = diagnostics.get("sun_azimuth")
        if sun_azimuth is not None:
            if azi_min <= azi_max:
                attrs["in_fov"] = azi_min <= sun_azimuth <= azi_max
            else:
                attrs["in_fov"] = sun_azimuth >= azi_min or sun_azimuth <= azi_max

    if config.get("enable_blind_spot", False) and fov_left is not None:
        # One [right_edge, left_edge] pair per active slot (issue #701). Slot 1
        # reuses the legacy unsuffixed keys. ``blind_spot_range`` keeps emitting
        # only slot 1 for Lovelace-card back-compat; ``blind_spot_ranges`` lists
        # every active slot.
        ranges: list[list[float]] = []
        for keys in BLIND_SPOT_SLOTS.values():
            # Signed-gamma keys are the primary source (issue #247): the wedge is
            # -right_gamma..left_gamma, so the emitted [right, left] pair is
            # [-right_gamma, left_gamma] — byte-identical to the legacy formula.
            lg = config.get(keys["left_gamma"])
            rg = config.get(keys["right_gamma"])
            if lg is not None and rg is not None:
                ranges.append([-rg, lg])
                continue
            bs_left = config.get(keys["left"])
            bs_right = config.get(keys["right"])
            if bs_left is None or bs_right is None:
                continue
            ranges.append([fov_left - bs_right, fov_left - bs_left])
        if ranges:
            attrs["blind_spot_range"] = ranges[0]
            attrs["blind_spot_ranges"] = ranges

    return attrs or None


def _control_status_value(s: _ACPDiagnosticSensor) -> str | None:
    if s.data.diagnostics is None:
        return None
    return s.data.diagnostics.get("control_status")


def _iso_or_none(value: dt.datetime | None) -> str | None:
    """Return a tz-aware ISO-8601 string for a naive-local datetime, or None."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = dt_util.as_local(value)
    return value.isoformat()


def _control_status_attrs(s: _ACPDiagnosticSensor) -> Mapping[str, Any] | None:
    if s.data.diagnostics is None:
        return None
    diagnostics = s.data.diagnostics
    attrs: dict[str, Any] = {
        "reason": diagnostics.get("control_state_reason"),
        "automatic_control_enabled": s.coordinator.automatic_control,
        "cover_type": s._cover_type,  # noqa: SLF001 — consumed by Lovelace card to flip cover-fill polarity for awnings
        # Additive self-discovery descriptor (issue #725): full axis list +
        # labels + ranges + per-axis supported flags. cover_type above is left
        # untouched so older card versions are unaffected.
        "cover_discovery": asdict(s.coordinator.build_axis_discovery()),
    }

    time_window = diagnostics.get("time_window", {})
    attrs["time_window_status"] = (
        "Active" if time_window.get("check_adaptive_time") else "Outside Window"
    )
    attrs["after_start_time"] = time_window.get("after_start_time")
    attrs["before_end_time"] = time_window.get("before_end_time")
    attrs["schedule_start"] = _iso_or_none(time_window.get("start_time"))
    attrs["schedule_end"] = _iso_or_none(time_window.get("end_time"))

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

    if diagnostics.get("control_status") == "manual_override":
        attrs["manual_covers"] = s.data.states.get("manual_list", [])

    attrs["delta_position_threshold"] = diagnostics.get("delta_position_threshold")
    attrs["delta_time_threshold_minutes"] = diagnostics.get(
        "delta_time_threshold_minutes"
    )
    if "position_delta_from_last_action" in diagnostics:
        attrs["position_delta_from_last_action"] = diagnostics[
            "position_delta_from_last_action"
        ]
    if "seconds_since_last_action" in diagnostics:
        attrs["seconds_since_last_action"] = diagnostics["seconds_since_last_action"]

    attrs["last_updated"] = diagnostics.get("last_updated")
    return attrs


def _last_action_value(s: _ACPDiagnosticSensor) -> str | None:
    if not s.data or not s.data.diagnostics:
        return None
    action = s.data.diagnostics.get("last_cover_action")
    if not action or not action.get("entity_id"):
        return "No action recorded"

    service = action.get("service", "unknown")
    entity = action.get("entity_id", "unknown")
    timestamp_str = action.get("timestamp", "")

    if timestamp_str:
        try:
            ts = dt_util.parse_datetime(timestamp_str)
            if ts:
                time_str = dt_util.as_local(ts).strftime("%H:%M:%S")
                return f"{service} → {entity.split('.')[-1]} at {time_str}"
        except (ValueError, AttributeError):
            pass
    return f"{service} → {entity.split('.')[-1]}"


def _last_action_attrs(s: _ACPDiagnosticSensor) -> Mapping[str, Any] | None:
    if not s.data or not s.data.diagnostics:
        return None
    action = s.data.diagnostics.get("last_cover_action")
    if not action or not action.get("entity_id"):
        return None

    attrs: dict[str, Any] = {
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


def _manual_override_end_value(s: _ManualOverrideEndSensor) -> dt.datetime | None:
    times = s.coordinator.manager.manual_control_time
    if not times:
        return None
    duration = s.coordinator.manager.reset_duration
    return max(expiry_for_started_at(t, duration) for t in times.values())


def _manual_override_end_attrs(
    s: _ManualOverrideEndSensor,
) -> Mapping[str, Any] | None:
    times = s.coordinator.manager.manual_control_time
    if not times:
        return None
    duration = s.coordinator.manager.reset_duration
    return {
        "per_entity": {
            entity_id: expiry_for_started_at(t, duration).isoformat()
            for entity_id, t in times.items()
        }
    }


def _position_verification_value(s: _ACPDiagnosticSensor) -> int:
    entities = s.coordinator.entities
    if not entities:
        return 0
    return max(
        s.coordinator._cmd_svc.get_diagnostics(e)["retry_count"]  # noqa: SLF001
        for e in entities
    )


def _position_verification_attrs(s: _ACPDiagnosticSensor) -> Mapping[str, Any] | None:
    entities = s.coordinator.entities
    if not entities:
        return {}
    cmd_svc = s.coordinator._cmd_svc  # noqa: SLF001
    per_entity = {e: cmd_svc.get_diagnostics(e) for e in entities}
    recon_times = [
        d["last_reconcile_time"]
        for d in per_entity.values()
        if d["last_reconcile_time"] is not None
    ]
    attrs: dict[str, Any] = {
        "max_retries": cmd_svc._max_retries,  # noqa: SLF001
        "per_entity": per_entity,
    }
    if recon_times:
        attrs["last_reconcile_time"] = max(recon_times)
    return attrs


def _motion_status_value(s: _ACPDiagnosticSensor) -> str:
    if not motion_entities(s.config_entry.options):
        return "not_configured"
    mgr = s.coordinator._motion_mgr  # noqa: SLF001
    if mgr.is_motion_timeout_active:
        pr = getattr(s.coordinator, "_pipeline_result", None)
        if pr is not None and pr.skip_command and pr.control_method.value == "motion":
            return "holding"
        return "no_motion"
    if mgr.last_motion_time is None:
        return "waiting_for_data"
    if s.coordinator.is_motion_detected:
        return "motion_detected"
    if mgr.has_pending_timeout:
        return "timeout_pending"
    return "waiting_for_data"


def _motion_status_attrs(s: _ACPDiagnosticSensor) -> Mapping[str, Any] | None:
    if not motion_entities(s.config_entry.options):
        return None
    mgr = s.coordinator._motion_mgr  # noqa: SLF001
    attrs: dict[str, Any] = {
        "motion_timeout_seconds": mgr._timeout_seconds
    }  # noqa: SLF001

    if mgr.last_motion_time is not None:
        if mgr.has_pending_timeout or mgr.is_motion_timeout_active:
            end_ts = mgr.last_motion_time + mgr._timeout_seconds  # noqa: SLF001
            attrs["motion_timeout_end_time"] = dt_util.utc_from_timestamp(
                end_ts
            ).isoformat()
        attrs["last_motion_time"] = dt_util.utc_from_timestamp(
            mgr.last_motion_time
        ).isoformat()
    return attrs


def _climate_status_value(s: _ACPDiagnosticSensor) -> str | None:
    from .helpers import climate_mode_from_diagnostics

    return climate_mode_from_diagnostics(s.data.diagnostics)


def _round_threshold(value: float | str | None) -> float | None:
    """Round a threshold value to 1 decimal place at the presentation boundary.

    Accepts numeric strings stored by TemplateSelector since #577 (e.g. ``"22"``,
    ``"22.5"``) as well as plain numbers. Comma-decimal strings (``"22,5"``)
    are normalised to a decimal point. Jinja2 template strings are returned as
    ``None`` — their rendered value is only available inside the coordinator
    update cycle via ``TemplateResolver``.
    """
    if value is None:
        return None
    if isinstance(value, str):
        from .templates import is_template_string

        if is_template_string(value):
            return None  # unresolved template — no numeric value at attr time
        try:
            value = float(value.replace(",", "."))
        except (ValueError, TypeError):
            return None
    return round(value, 1)


def _climate_status_attrs(s: _ACPDiagnosticSensor) -> Mapping[str, Any]:
    """Return extra_state_attributes for the climate_status sensor.

    Always returns a non-None dict so that threshold setpoints and
    inactive_reason are visible even in standby (when diagnostics is None).
    """
    from .pipeline.handlers.climate import inactive_reason_from_result

    opts = s.config_entry.options

    # --- Threshold setpoints: always present, even in standby ---
    # Read from config_entry.options — the canonical live source.
    # Rounded to 1 decimal place at the sensor boundary (Display-Rounding rule).
    attrs: dict[str, Any] = {
        "temp_low": _round_threshold(opts.get(CONF_TEMP_LOW)),
        "temp_high": _round_threshold(opts.get(CONF_TEMP_HIGH)),
        "temp_summer_outside": _round_threshold(opts.get(CONF_OUTSIDE_THRESHOLD)),
    }

    # --- inactive_reason: always present ---
    result = s.coordinator._pipeline_result  # noqa: SLF001
    attrs["inactive_reason"] = inactive_reason_from_result(result)

    # --- Active-state attributes (only when diagnostics are populated) ---
    diagnostics = s.data.diagnostics
    if diagnostics is None:
        return attrs

    active_temp = diagnostics.get("active_temperature")
    if active_temp is not None:
        attrs["active_temperature"] = active_temp
        # ``active_temperature`` is reported in the configured sensor's unit,
        # not HA's locale unit (the integration never converts sensor reads).
        # Surface the SENSOR's unit so the value's meaning is unambiguous;
        # fall back to HA's locale only when no sensor is configured.
        from .const import CONF_TEMP_ENTITY
        from .unit_system import sensor_unit_label

        ha_unit = str(s.hass.config.units.temperature_unit)
        sensor_uom = sensor_unit_label(
            s.hass, s.config_entry.options.get(CONF_TEMP_ENTITY), ha_unit
        )
        attrs["temperature_unit"] = sensor_uom
        attrs["ha_temperature_unit"] = ha_unit

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

    return attrs


def _solar_calculation_details(s: _ACPDiagnosticSensor) -> Mapping[str, Any] | None:
    """Read the raw solar-calculation trace from diagnostics (single source).

    The trace is the same ``calculation_details`` dict that the diagnostics
    download surfaces — the sensor and the download therefore read one source.
    """
    diagnostics = s.data.diagnostics if s.data else None
    if not diagnostics:
        return None
    return diagnostics.get("calculation_details") or None


def _solar_calculation_value(s: _ACPDiagnosticSensor) -> int | None:
    """State = raw geometric position percentage (pre-interpolation, pre-inverse).

    For venetian this is the lift/position axis (top-level ``position_pct``); the
    tilt axis rides in the attributes under the nested ``tilt`` sub-key.
    """
    details = _solar_calculation_details(s)
    if details is None:
        return None
    return details.get("position_pct")


def _solar_calculation_attrs(s: _ACPDiagnosticSensor) -> Mapping[str, Any] | None:
    """Attributes = the full raw calculation_details trace dict."""
    return _solar_calculation_details(s)


def _decision_trace_value(s: _ACPDiagnosticSensor) -> str:
    result = s.coordinator._pipeline_result  # noqa: SLF001
    if result is None:
        return "unknown"
    return result.control_method.value


def _configured_handlers(opts: Mapping[str, Any]) -> list[str]:
    """Pipeline handlers the user has configured (card-normalized names)."""
    enabled: list[str] = ["manual", "default"]
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
        custom_position_slot_configured(opts, slot_keys)
        for slot_keys in CUSTOM_POSITION_SLOTS.values()
    ):
        enabled.append("custom_position")
    if motion_entities(opts):
        enabled.append("motion")
    if opts.get(CONF_CLOUD_SUPPRESSION) and any(
        opts.get(k)
        for k in (
            CONF_IS_SUNNY_SENSOR,
            CONF_LUX_ENTITY,
            CONF_IRRADIANCE_ENTITY,
            CONF_CLOUD_COVERAGE_ENTITY,
        )
    ):
        enabled.append("cloud")
    if opts.get(CONF_CLIMATE_MODE):
        enabled.append("climate")
    if opts.get(CONF_ENABLE_GLARE_ZONES):
        enabled.append("glare_zone")
    if opts.get(CONF_ENABLE_SUN_TRACKING, True):
        enabled.append("solar")
    return enabled


def _position_forecast_value(s: _ACPDiagnosticSensor) -> dt.datetime | None:
    """Return the timestamp of the next forecast event (sunrise, FOV enter, ...).

    Reads from ``coordinator.data.position_forecast``, which the coordinator
    refreshes on a slow background cadence via the executor (issue #437).
    None when the forecast has not been computed yet or no upcoming events
    are scheduled.
    """
    forecast = getattr(s.coordinator.data, "position_forecast", None)
    if forecast is None:
        return None
    now = dt_util.now()
    upcoming = [e for e in forecast.events if e.t >= now]
    return upcoming[0].t if upcoming else None


def _position_forecast_attrs(
    s: _ACPDiagnosticSensor,
) -> Mapping[str, Any] | None:
    """Return the serialised forecast samples + events for the companion card.

    Reads from ``coordinator.data.position_forecast`` — never recomputes.
    The coordinator owns the refresh cadence (issue #437).
    """
    forecast = getattr(s.coordinator.data, "position_forecast", None)
    if forecast is None:
        return None
    return forecast.to_attrs()


def _build_custom_position_slots_snapshot(
    options: Mapping[str, Any], hass: Any
) -> list[dict[str, Any]]:
    """Build a per-slot snapshot for the companion card's slot UI.

    Always returns one row per slot (1-5) so the consumer can render a stable
    grid. Rows for unconfigured slots have ``enabled=False`` with the other
    fields nulled out — the card uses ``sensor is not None`` to tell
    "configured but disabled" apart from "never configured".

    ``sensor`` stays populated with the first trigger sensor for card
    back-compat; ``sensors`` carries the full multi-sensor list and
    ``template``/``template_mode`` describe the optional condition template
    (issue #563).
    """
    snapshot: list[dict[str, Any]] = []
    for slot, slot_keys in CUSTOM_POSITION_SLOTS.items():
        sensors = custom_position_slot_sensors(options, slot_keys)
        position = options.get(slot_keys["position"])
        has_template = is_template_string(options.get(slot_keys["template"]))
        configured = custom_position_slot_configured(options, slot_keys)
        sensor = sensors[0] if sensors else None
        sensor_name: str | None = None
        if configured and sensor:
            state = hass.states.get(sensor)
            if state is not None:
                sensor_name = state.attributes.get(ATTR_FRIENDLY_NAME)
        snapshot.append(
            {
                "slot": slot,
                "enabled": (
                    bool(
                        options.get(
                            slot_keys["enabled"], DEFAULT_CUSTOM_POSITION_ENABLED
                        )
                    )
                    if configured
                    else False
                ),
                "sensor": sensor if configured else None,
                "sensors": sensors if configured else [],
                "template": has_template if configured else False,
                "template_mode": (
                    options.get(slot_keys["template_mode"])
                    or DEFAULT_TEMPLATE_COMBINE_MODE
                    if configured and has_template
                    else None
                ),
                "sensor_name": sensor_name,
                # User-configured slot label (issue #867); overrides sensor_name
                # in the reason/decision_trace/card when set. None = unset.
                "custom_name": custom_position_slot_name(options, slot_keys),
                "position": int(position) if configured else None,
                "priority": (
                    int(
                        options.get(slot_keys["priority"])
                        or DEFAULT_CUSTOM_POSITION_PRIORITY
                    )
                    if configured
                    else None
                ),
                "min_mode": (
                    bool(options.get(slot_keys["min_mode"], False))
                    if configured
                    else None
                ),
            }
        )
    return snapshot


def _decision_trace_attrs(s: _ACPDiagnosticSensor) -> Mapping[str, Any] | None:
    attrs: dict[str, Any] = {}
    result = s.coordinator._pipeline_result  # noqa: SLF001
    if result:
        attrs["trace"] = [
            {
                "handler": step.handler,
                "matched": step.matched,
                # Localized to the instance language (issue #882); falls back to
                # the step's English ``reason`` for legacy payload-less steps.
                "reason": _localized_reason(
                    s.coordinator, step.reason_payload, step.reason
                ),
                "position": step.position,
                **({"tilt": step.tilt} if step.tilt is not None else {}),
                **(
                    {"held_position": step.held_position}
                    if step.held_position is not None
                    else {}
                ),
                # Additive card payload (issue #882): the stable code + params so
                # the companion card can localize with its own templates. Omitted
                # on legacy steps that carry only an English ``reason`` string.
                **(
                    _reason_to_dict_attrs(step.reason_payload)
                    if step.reason_payload is not None
                    else {}
                ),
            }
            for step in result.decision_trace
        ]
        attrs["reason"] = _localized_reason(
            s.coordinator, result.reason_payload, result.reason
        )
        if result.reason_payload is not None:
            attrs.update(_reason_to_dict_attrs(result.reason_payload))
        attrs["bypass_auto_control"] = result.bypass_auto_control
        attrs["default_position"] = result.default_position
        attrs["is_sunset_active"] = result.is_sunset_active
        attrs["configured_default"] = result.configured_default
        attrs["configured_sunset_pos"] = result.configured_sunset_pos
        if result.tilt is not None:
            attrs["tilt"] = result.tilt
        if result.custom_position_active_slot is not None:
            attrs["custom_position_active_slot"] = result.custom_position_active_slot
        if result.custom_position_minimum_mode is not None:
            attrs["custom_position_minimum_mode"] = result.custom_position_minimum_mode
        if result.custom_position_active_slot_name is not None:
            attrs["custom_position_active_slot_name"] = (
                result.custom_position_active_slot_name
            )
        if result.control_method == ControlMethod.WEATHER:
            weather_mgr = s.coordinator._weather_mgr  # noqa: SLF001
            attrs["weather_active_conditions"] = weather_mgr.active_conditions
            attrs["weather_in_clear_delay"] = weather_mgr.in_clear_delay

    attrs["in_time_window"] = s.coordinator.check_adaptive_time
    attrs["enabled_handlers"] = _configured_handlers(s.config_entry.options)
    attrs["custom_position_slots"] = _build_custom_position_slots_snapshot(
        s.config_entry.options, s.coordinator.hass
    )

    diagnostics = s.coordinator.data.diagnostics if s.coordinator.data else {}
    if diagnostics:
        attrs["sun_azimuth"] = diagnostics.get("sun_azimuth")
        attrs["sun_elevation"] = diagnostics.get("sun_elevation")
        attrs["gamma"] = diagnostics.get("gamma")
        sun_validity = diagnostics.get("sun_validity", {})
        if sun_validity:
            attrs["in_field_of_view"] = sun_validity.get("valid")
            attrs["elevation_valid"] = sun_validity.get("valid_elevation")
            attrs["in_blind_spot"] = sun_validity.get("in_blind_spot")
            attrs["sunset_window_active"] = sun_validity.get("sunset_window_active")
            # Promote the #552/#553 sun_state derivation onto the wire so the
            # companion card reads it as the authoritative sky-compass dot state.
            attrs["sun_state"] = sun_validity.get("sun_state")
        if s.coordinator._cover_data is not None:  # noqa: SLF001
            attrs["direct_sun_valid"] = (
                s.coordinator._cover_data.direct_sun_valid
            )  # noqa: SLF001

    return attrs or None


def _last_skipped_value(s: _ACPDiagnosticSensor) -> str | None:
    if not s.data or not s.data.diagnostics:
        return None
    action = s.data.diagnostics.get("last_skipped_action")
    if not action or not action.get("entity_id"):
        return "No action skipped"
    return action.get("reason")


def _last_skipped_attrs(s: _ACPDiagnosticSensor) -> Mapping[str, Any] | None:
    if not s.data or not s.data.diagnostics:
        return None
    action = s.data.diagnostics.get("last_skipped_action")
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


# ---------------------------------------------------------------------------
# Specs — declarative inventory of every sensor
# ---------------------------------------------------------------------------


# Standard (non-diagnostic) sensors — Cover_Position is the headline state,
# Start/End Sun are user-facing time fields.
_STANDARD_SPECS: tuple[_SensorSpec, ...] = (
    _SensorSpec(
        suffix="Cover_Position",
        display_name="Target Position",
        icon="mdi:sun-compass",
        state_class=SensorStateClass.MEASUREMENT,
        unit=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=_cover_position_value,
        attrs_fn=_cover_position_attrs,
        diagnostic=False,
        unrecorded_attributes=frozenset(
            {"actual_positions", "actual_distances", "position_explanation"}
        ),
    ),
    _SensorSpec(
        suffix="Cover_Tilt",
        display_name="Target Tilt",
        icon="mdi:angle-acute",
        state_class=SensorStateClass.MEASUREMENT,
        unit=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=_cover_tilt_value,
        diagnostic=False,
        enabled_when=_exposes_dual_axis_sensor,
    ),
    _SensorSpec(
        suffix="Start Sun",
        display_name="Start Sun",
        icon="mdi:sun-clock-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_time_value("start"),
        attrs_fn=_time_attrs("start"),
        diagnostic=False,
    ),
    _SensorSpec(
        suffix="End Sun",
        display_name="End Sun",
        icon="mdi:sun-clock",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_time_value("end"),
        attrs_fn=_time_attrs("end"),
        diagnostic=False,
    ),
)


_DIAGNOSTIC_SPECS: tuple[_SensorSpec, ...] = (
    _SensorSpec(
        suffix="sun_position",
        display_name="Sun Position",
        icon="mdi:compass-outline",
        state_class=SensorStateClass.MEASUREMENT,
        unit="°",
        suggested_display_precision=1,
        value_fn=_sun_position_value,
        attrs_fn=_sun_position_attrs,
    ),
    _SensorSpec(
        suffix="solar_calculation",
        display_name="Solar Calculation",
        icon="mdi:sun-angle-outline",
        translation_key="solar_calculation",
        state_class=SensorStateClass.MEASUREMENT,
        unit=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=_solar_calculation_value,
        attrs_fn=_solar_calculation_attrs,
        # The raw trace can be large and changes every cycle — keep all attributes
        # out of the recorder DB while the small numeric state still records.
        unrecorded_attributes=frozenset({MATCH_ALL}),
    ),
    _SensorSpec(
        suffix="control_status",
        display_name="Control Status",
        icon="mdi:information-outline",
        translation_key="control_status",
        value_fn=_control_status_value,
        attrs_fn=_control_status_attrs,
        unrecorded_attributes=frozenset({"manual_covers"}),
    ),
    _SensorSpec(
        suffix="decision_trace",
        display_name="Decision Trace",
        icon="mdi:list-status",
        translation_key="decision_trace",
        device_class=SensorDeviceClass.ENUM,
        options=tuple(m.value for m in ControlMethod) + ("unknown",),
        value_fn=_decision_trace_value,
        attrs_fn=_decision_trace_attrs,
        unrecorded_attributes=frozenset(
            {"trace", "custom_position_slots", "enabled_handlers"}
        ),
    ),
    _SensorSpec(
        suffix="position_forecast",
        display_name="Position Forecast",
        icon="mdi:chart-line",
        translation_key="position_forecast",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_position_forecast_value,
        attrs_fn=_position_forecast_attrs,
        unrecorded_attributes=frozenset({"forecast", "events"}),
    ),
    _SensorSpec(
        suffix="last_skipped_action",
        display_name="Last Skipped Action",
        icon="mdi:debug-step-over",
        value_fn=_last_skipped_value,
        attrs_fn=_last_skipped_attrs,
    ),
    _SensorSpec(
        suffix="last_cover_action",
        display_name="Last Cover Action",
        icon="mdi:history",
        value_fn=_last_action_value,
        attrs_fn=_last_action_attrs,
    ),
    _SensorSpec(
        suffix="manual_override_end_time",
        display_name="Manual Override End Time",
        icon="mdi:timer-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        should_poll=False,
        value_fn=_manual_override_end_value,
        attrs_fn=_manual_override_end_attrs,
    ),
    _SensorSpec(
        suffix="position_verification",
        display_name="Position Verification",
        icon="mdi:refresh",
        state_class=SensorStateClass.MEASUREMENT,
        unit="retries",
        should_poll=False,
        value_fn=_position_verification_value,
        attrs_fn=_position_verification_attrs,
        unrecorded_attributes=frozenset({"per_entity"}),
    ),
    _SensorSpec(
        suffix="motion_status",
        display_name="Occupancy Status",
        icon="mdi:motion-sensor",
        translation_key="motion_status",
        device_class=SensorDeviceClass.ENUM,
        options=(
            "not_configured",
            "motion_detected",
            "timeout_pending",
            "no_motion",
            "waiting_for_data",
        ),
        should_poll=False,
        value_fn=_motion_status_value,
        attrs_fn=_motion_status_attrs,
    ),
    _SensorSpec(
        suffix="climate_status",
        display_name="Climate Status",
        icon="mdi:weather-partly-cloudy",
        translation_key="climate_status",
        device_class=SensorDeviceClass.ENUM,
        options=("summer_mode", "winter_mode", "intermediate"),
        value_fn=_climate_status_value,
        attrs_fn=_climate_status_attrs,
        enabled_when=lambda e: bool(e.options.get(CONF_CLIMATE_MODE, False)),
    ),
)


# Specs that need a non-default class (RestoreEntity hooks etc.).
_SPEC_OVERRIDES: dict[str, type[_ACPDiagnosticSensor]] = {
    "manual_override_end_time": _ManualOverrideEndSensor,
}


def _resolve_cls(default_base: type, spec: _SensorSpec) -> type:
    """Pick the concrete class for ``spec``.

    _SPEC_OVERRIDES wins for the base (RestoreEntity etc.); _unrecorded_attributes,
    when set, is layered on via a one-shot subclass — HA reads that attribute at
    class init, so it must live on a class, not an instance.
    """
    base = _SPEC_OVERRIDES.get(spec.suffix, default_base)
    if not spec.unrecorded_attributes:
        return base
    return type(
        f"_ACPSensor_{spec.suffix}",
        (base,),
        {"_unrecorded_attributes": spec.unrecorded_attributes},
    )


_STANDARD_CLASSES: dict[str, type] = {
    s.suffix: _resolve_cls(_ACPSensor, s) for s in _STANDARD_SPECS
}
_DIAGNOSTIC_CLASSES: dict[str, type] = {
    s.suffix: _resolve_cls(_ACPDiagnosticSensor, s) for s in _DIAGNOSTIC_SPECS
}


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: AdaptiveConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Initialize Adaptive Cover Pro config entry."""
    coordinator: AdaptiveDataUpdateCoordinator = config_entry.runtime_data

    # Cover groups expose their own aggregate sensors, never the cover set.
    from .cover_types import get_policy

    if get_policy(config_entry.data.get(CONF_SENSOR_TYPE)).is_orchestrator:
        from .group_entities import build_group_sensors

        async_add_entities(
            build_group_sensors(config_entry.entry_id, hass, config_entry, coordinator)
        )
        return

    entities: list[SensorEntity] = []

    for spec in _STANDARD_SPECS:
        if not spec.enabled_when(config_entry):
            continue
        cls = _STANDARD_CLASSES[spec.suffix]
        entities.append(
            cls(config_entry.entry_id, hass, config_entry, coordinator, spec)
        )

    for spec in _DIAGNOSTIC_SPECS:
        if not spec.enabled_when(config_entry):
            continue
        cls = _DIAGNOSTIC_CLASSES[spec.suffix]
        entities.append(
            cls(config_entry.entry_id, hass, config_entry, coordinator, spec)
        )

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Backward-compat class names
# ---------------------------------------------------------------------------
# Tests construct sensors by their original class names with the old
# `(entry_id_or_unique_id, hass, config_entry, name, coordinator [, …])`
# signature. The new architecture uses one generic class per base, so each
# legacy name is a thin subclass that:
#   1) accepts every historical kwarg / positional shape the tests use, and
#   2) forwards to the spec-driven base.
# These aliases exist purely for test compatibility — production setup goes
# through `async_setup_entry` and never touches them. Keep them at module
# bottom so they don't pollute the spec inventory above.

_SPEC_BY_SUFFIX: dict[str, _SensorSpec] = {
    spec.suffix: spec for spec in _STANDARD_SPECS + _DIAGNOSTIC_SPECS
}


def _normalize_legacy_args(
    args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[str, HomeAssistant, ConfigEntry, AdaptiveDataUpdateCoordinator]:
    """Map legacy positional/kwarg signatures to the new four-arg form.

    Historical signature was `(entry_id, hass, config_entry, name, coordinator)`,
    plus extras like `sensor_name`, `key`, `icon`, `hass_ref` on a few classes.
    Drops the unused legacy kwargs — those are now resolved through the spec.
    """
    for legacy_key in ("name", "sensor_name", "key", "icon", "hass_ref"):
        kwargs.pop(legacy_key, None)
    eid = (
        kwargs.pop("unique_id", None)
        or kwargs.pop("config_entry_id", None)
        or kwargs.pop("entry_id", None)
    )
    args_list = list(args)
    # Old 5-arg positional shape: (eid, hass, config_entry, name, coordinator)
    # Drop the name slot at index 3 if present.
    if len(args_list) >= 5:
        args_list = args_list[:3] + args_list[4:]
    if eid is None and args_list:
        eid = args_list.pop(0)
    hass = kwargs.pop("hass", None) or (args_list.pop(0) if args_list else None)
    config_entry = kwargs.pop("config_entry", None) or (
        args_list.pop(0) if args_list else None
    )
    coordinator = kwargs.pop("coordinator", None) or (
        args_list.pop(0) if args_list else None
    )
    return eid, hass, config_entry, coordinator


def _spec_class_attrs(spec: _SensorSpec) -> dict[str, Any]:
    """Mirror spec fields onto class-level `_attr_*` for tests that introspect.

    Tests like `test_target_position_sensor_precision_is_zero` use
    `object.__new__(Cls)` (bypassing __init__) and read `cls._attr_*` via the
    HA SensorEntity property getters. The spec-driven generic class only sets
    these as instance attrs; the legacy aliases need them at class level too.
    """
    attrs: dict[str, Any] = {}
    if spec.translation_key is not None:
        attrs["_attr_translation_key"] = spec.translation_key
    if spec.state_class is not None:
        attrs["_attr_state_class"] = spec.state_class
    if spec.device_class is not None:
        attrs["_attr_device_class"] = spec.device_class
    if spec.unit is not None:
        attrs["_attr_native_unit_of_measurement"] = spec.unit
    if spec.suggested_display_precision is not None:
        attrs["_attr_suggested_display_precision"] = spec.suggested_display_precision
    if spec.options is not None:
        attrs["_attr_options"] = list(spec.options)
    if not spec.should_poll:
        attrs["_attr_should_poll"] = False
    return attrs


def _make_legacy_alias(name: str, suffix: str, base_cls: type) -> type:
    """Build a backward-compat class bound to a single spec by suffix.

    The class name is the legacy public name (`AdaptiveCoverSunPositionSensor`
    etc.). Class-level `_attr_*` are mirrored from the spec so introspection
    via `object.__new__(Cls)` keeps working. The `__init__` accepts the
    historical positional+kwarg shapes (see `_normalize_legacy_args`).
    """
    spec = _SPEC_BY_SUFFIX[suffix]
    cls_attrs: dict[str, Any] = _spec_class_attrs(spec)
    # Store spec at class level so tests that use __new__() (bypass __init__)
    # can still resolve `self._spec` via class lookup.
    cls_attrs["_spec"] = spec

    def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
        eid, hass, config_entry, coordinator = _normalize_legacy_args(args, kwargs)
        base_cls.__init__(self, eid, hass, config_entry, coordinator, spec)
        # Climate-only: legacy `_temp_unit` instance attr so tests reading
        # `sensor._temp_unit` still pass. Computed-on-read elsewhere.
        if suffix == "climate_status" and hass is not None:
            self._temp_unit = hass.config.units.temperature_unit

    cls_attrs["__init__"] = __init__
    return type(name, (base_cls,), cls_attrs)


class AdaptiveCoverTimeSensorEntity(_ACPSensor):
    """Legacy alias: dispatches Start/End by `key=` kwarg."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Accept `key="start"` or `key="end"` to select the spec."""
        key = kwargs.get("key")
        suffix = "End Sun" if key == "end" else "Start Sun"
        eid, hass, config_entry, coordinator = _normalize_legacy_args(args, kwargs)
        super().__init__(eid, hass, config_entry, coordinator, _SPEC_BY_SUFFIX[suffix])


AdaptiveCoverSensorEntity = _make_legacy_alias(
    "AdaptiveCoverSensorEntity", "Cover_Position", _ACPSensor
)
AdaptiveCoverSunPositionSensor = _make_legacy_alias(
    "AdaptiveCoverSunPositionSensor", "sun_position", _ACPDiagnosticSensor
)
AdaptiveCoverControlStatusSensor = _make_legacy_alias(
    "AdaptiveCoverControlStatusSensor", "control_status", _ACPDiagnosticSensor
)
AdaptiveCoverDecisionTraceSensor = _make_legacy_alias(
    "AdaptiveCoverDecisionTraceSensor", "decision_trace", _ACPDiagnosticSensor
)
AdaptiveCoverLastSkippedActionSensor = _make_legacy_alias(
    "AdaptiveCoverLastSkippedActionSensor", "last_skipped_action", _ACPDiagnosticSensor
)
AdaptiveCoverLastActionSensor = _make_legacy_alias(
    "AdaptiveCoverLastActionSensor", "last_cover_action", _ACPDiagnosticSensor
)
AdaptiveCoverManualOverrideEndSensor = _make_legacy_alias(
    "AdaptiveCoverManualOverrideEndSensor",
    "manual_override_end_time",
    _ManualOverrideEndSensor,
)
AdaptiveCoverPositionVerificationSensor = _make_legacy_alias(
    "AdaptiveCoverPositionVerificationSensor",
    "position_verification",
    _ACPDiagnosticSensor,
)
AdaptiveCoverMotionStatusSensor = _make_legacy_alias(
    "AdaptiveCoverMotionStatusSensor", "motion_status", _ACPDiagnosticSensor
)
AdaptiveCoverClimateStatusSensor = _make_legacy_alias(
    "AdaptiveCoverClimateStatusSensor", "climate_status", _ACPDiagnosticSensor
)
