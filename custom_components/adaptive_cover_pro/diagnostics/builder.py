"""Diagnostics builder for Adaptive Cover Pro.

Extracts all diagnostic data assembly from the coordinator into a
standalone, testable class.  The builder operates on a ``DiagnosticContext``
dataclass that bundles every piece of coordinator state it needs, so it
never accesses the coordinator directly.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from ..const import ControlStatus
from ..enums import ClimateStrategy, ControlMethod


# ---------------------------------------------------------------------------
# Context dataclass – the coordinator populates this before calling build()
# ---------------------------------------------------------------------------


@dataclass
class DiagnosticContext:
    """Snapshot of coordinator state needed to build diagnostics."""

    # Sun / cover state
    pos_sun: list  # [azimuth, elevation]
    normal_cover_state: Any  # NormalCoverState | None

    # Position
    raw_calculated_position: int
    climate_state: int | None
    climate_data: Any  # ClimateCoverData | None
    climate_strategy: Any  # ClimateStrategy | None
    climate_mode: bool
    control_method: Any  # ControlMethod enum
    pipeline_result: Any  # PipelineResult | None

    # Overrides
    is_force_override_active: bool
    is_weather_override_active: bool
    is_motion_timeout_active: bool
    is_manual_override_active: bool

    # Time window
    check_adaptive_time: bool
    after_start_time: bool
    before_end_time: bool
    start_time: Any
    end_time: Any

    # Automation
    automatic_control: bool
    last_cover_action: dict = field(default_factory=dict)
    last_skipped_action: dict = field(default_factory=dict)
    min_change: int = 1
    time_threshold: int = 2

    # Modes / transforms
    switch_mode: bool = False
    inverse_state: bool = False
    use_interpolation: bool = False
    default_state: int = 0
    final_state: int = 0  # coordinator.state (after interpolation/inverse)

    # Configuration snapshot
    config_options: dict = field(default_factory=dict)

    # Motion manager state
    motion_detected: bool = True
    motion_timeout_active: bool = False

    # Force override config
    force_override_sensors: list = field(default_factory=list)
    force_override_position: int = 0


# ---------------------------------------------------------------------------
# Strategy label map (moved from coordinator class attribute)
# ---------------------------------------------------------------------------

_CLIMATE_STRATEGY_LABELS: dict[ClimateStrategy, str] = {
    ClimateStrategy.WINTER_HEATING: "Winter Heating",
    ClimateStrategy.SUMMER_COOLING: "Summer Cooling",
    ClimateStrategy.LOW_LIGHT: "Low Light",
    ClimateStrategy.GLARE_CONTROL: "Glare Control",
}


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class DiagnosticsBuilder:
    """Assembles diagnostic data from a ``DiagnosticContext``."""

    # -- public API ---------------------------------------------------------

    def build(self, ctx: DiagnosticContext) -> tuple[dict, str]:
        """Build complete diagnostic data.

        Returns:
            A tuple of (diagnostics_dict, position_explanation_string).

        """
        diagnostics: dict = {}
        diagnostics.update(self._build_solar(ctx))
        diagnostics.update(self._build_position(ctx))
        diagnostics.update(self._build_time_window(ctx))
        diagnostics.update(self._build_sun_validity(ctx))
        diagnostics.update(self._build_climate(ctx))
        diagnostics.update(self._build_last_action(ctx))
        diagnostics.update(self._build_configuration(ctx))

        explanation = diagnostics.get("position_explanation", "")
        return diagnostics, explanation

    # -- private helpers ----------------------------------------------------

    @staticmethod
    def _build_solar(ctx: DiagnosticContext) -> dict:
        """Build solar position diagnostics."""
        diagnostics: dict = {}
        sun_azimuth, sun_elevation = ctx.pos_sun
        diagnostics["sun_azimuth"] = sun_azimuth
        diagnostics["sun_elevation"] = sun_elevation

        if ctx.normal_cover_state and hasattr(ctx.normal_cover_state.cover, "gamma"):
            diagnostics["gamma"] = ctx.normal_cover_state.cover.gamma

        return diagnostics

    @staticmethod
    def _get_control_state_reason(ctx: DiagnosticContext) -> str:
        """Get the current control state reason including coordinator-level overrides."""
        if ctx.is_force_override_active:
            return "Force Override"
        if ctx.is_motion_timeout_active:
            return "Motion Timeout"
        if ctx.is_manual_override_active:
            return "Manual Override"
        if ctx.normal_cover_state and ctx.normal_cover_state.cover:
            return ctx.normal_cover_state.cover.control_state_reason
        return "Unknown"

    @staticmethod
    def _build_position_explanation(ctx: DiagnosticContext) -> str:
        """Build a human-readable explanation of the full position decision chain."""
        options = ctx.config_options

        # Priority overrides
        if ctx.is_force_override_active:
            pos = ctx.force_override_position
            return f"Force override active → {pos}%"

        if ctx.is_motion_timeout_active:
            return f"No motion detected → default {ctx.default_state}%"

        if ctx.is_manual_override_active:
            return "Manual override active"

        # Outside time window — pipeline was bypassed; report actual position reason
        if not ctx.check_adaptive_time:
            from ..const import CONF_DEFAULT_HEIGHT, CONF_SUNSET_POS

            sunset_pos = options.get(CONF_SUNSET_POS)
            default_height = options.get(CONF_DEFAULT_HEIGHT, 0)
            cover = ctx.normal_cover_state.cover if ctx.normal_cover_state else None
            past_sunset = cover.sunset_valid if cover else False
            if past_sunset and sunset_pos is not None:
                return f"Outside time window, past sunset → Sunset Position ({sunset_pos}%)"
            return f"Outside time window → Default Position ({default_height}%)"

        # Build the decision chain
        parts: list[str] = []

        # Step 1: Sun position condition and raw calculated value
        if ctx.normal_cover_state and ctx.normal_cover_state.cover:
            cover = ctx.normal_cover_state.cover
            if cover.direct_sun_valid:
                parts.append(f"Sun tracking ({ctx.raw_calculated_position}%)")
            elif cover.sunset_valid and cover.sunset_pos is not None:
                parts.append(f"Sunset Position ({round(cover.sunset_pos)}%)")
            else:
                reason = cover.control_state_reason
                parts.append(f"{reason} → Default Position ({round(cover.default)}%)")

        # Step 2: Position limits on the non-climate path
        if not ctx.switch_mode:
            from ..const import (
                CONF_ENABLE_MAX_POSITION,
                CONF_ENABLE_MIN_POSITION,
                CONF_MAX_POSITION,
                CONF_MIN_POSITION,
            )

            min_pos = options.get(CONF_MIN_POSITION)
            max_pos = options.get(CONF_MAX_POSITION)
            enable_min = options.get(CONF_ENABLE_MIN_POSITION, False)
            enable_max = options.get(CONF_ENABLE_MAX_POSITION, False)
            if min_pos is not None and enable_min and ctx.default_state == min_pos:
                parts.append(f"min limit ({min_pos}%) → {ctx.default_state}%")
            elif max_pos is not None and enable_max and ctx.default_state == max_pos:
                parts.append(f"max limit ({max_pos}%) → {ctx.default_state}%")

        # Step 3: Climate mode override
        if ctx.switch_mode and ctx.climate_state is not None:
            strategy_label = (
                _CLIMATE_STRATEGY_LABELS.get(ctx.climate_strategy, "Active")
                if ctx.climate_strategy
                else "Active"
            )
            parts.append(f"Climate: {strategy_label} → {ctx.climate_state}%")

        # Step 4: Interpolation / inverse state
        final = ctx.final_state
        if ctx.use_interpolation:
            parts.append(f"interpolated → {final}%")
        elif ctx.inverse_state:
            parts.append(f"inversed → {final}%")

        return " → ".join(parts) if parts else "Unknown"

    @staticmethod
    def _determine_control_status(ctx: DiagnosticContext) -> str:
        """Determine current control status."""
        if not ctx.automatic_control:
            return ControlStatus.AUTOMATIC_CONTROL_OFF

        if ctx.is_force_override_active:
            return ControlStatus.FORCE_OVERRIDE_ACTIVE

        if ctx.is_weather_override_active:
            return ControlStatus.WEATHER_OVERRIDE_ACTIVE

        if ctx.is_motion_timeout_active:
            return ControlStatus.MOTION_TIMEOUT

        if ctx.pipeline_result is not None:
            method = ctx.pipeline_result.control_method
            if method == ControlMethod.MANUAL:
                return ControlStatus.MANUAL_OVERRIDE

        if not ctx.check_adaptive_time:
            return ControlStatus.OUTSIDE_TIME_WINDOW

        if ctx.normal_cover_state and not ctx.normal_cover_state.cover.valid:
            return ControlStatus.SUN_NOT_VISIBLE

        return ControlStatus.ACTIVE

    @classmethod
    def _build_position(cls, ctx: DiagnosticContext) -> dict:
        """Build position diagnostics."""
        diagnostics: dict = {}

        diagnostics["calculated_position"] = ctx.raw_calculated_position

        if ctx.climate_state is not None:
            diagnostics["calculated_position_climate"] = ctx.climate_state

        diagnostics["control_status"] = cls._determine_control_status(ctx)
        diagnostics["control_state_reason"] = cls._get_control_state_reason(ctx)

        explanation = cls._build_position_explanation(ctx)
        diagnostics["position_explanation"] = explanation

        # Delta thresholds
        diagnostics["delta_position_threshold"] = ctx.min_change
        diagnostics["delta_time_threshold_minutes"] = ctx.time_threshold

        # Position delta from last action
        last_action = ctx.last_cover_action
        if last_action.get("position") is not None:
            diagnostics["position_delta_from_last_action"] = abs(
                ctx.raw_calculated_position - last_action["position"]
            )

        # Time since last action
        if last_action.get("timestamp"):
            try:
                last_ts = dt.datetime.fromisoformat(last_action["timestamp"])
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=dt.UTC)
                now_utc = dt.datetime.now(dt.UTC)
                elapsed = (now_utc - last_ts).total_seconds()
                diagnostics["seconds_since_last_action"] = round(elapsed)
            except (ValueError, AttributeError):
                pass

        # Vertical cover calculation details
        if ctx.normal_cover_state and ctx.normal_cover_state.cover:
            cover = ctx.normal_cover_state.cover
            calc_details = getattr(cover, "_last_calc_details", None)
            if calc_details is not None:
                diagnostics["calculation_details"] = calc_details

        diagnostics["last_updated"] = dt.datetime.now(dt.UTC).isoformat()

        return diagnostics

    @staticmethod
    def _build_time_window(ctx: DiagnosticContext) -> dict:
        """Build time window diagnostics."""
        return {
            "time_window": {
                "check_adaptive_time": ctx.check_adaptive_time,
                "after_start_time": ctx.after_start_time,
                "before_end_time": ctx.before_end_time,
                "start_time": ctx.start_time,
                "end_time": ctx.end_time,
            }
        }

    @staticmethod
    def _build_sun_validity(ctx: DiagnosticContext) -> dict:
        """Build sun validity diagnostics."""
        diagnostics: dict = {}
        if ctx.normal_cover_state and ctx.normal_cover_state.cover:
            cover = ctx.normal_cover_state.cover
            diagnostics["sun_validity"] = {
                "valid": cover.valid,
                "valid_elevation": cover.valid_elevation,
                "in_blind_spot": getattr(cover, "is_sun_in_blind_spot", None),
            }
        return diagnostics

    @staticmethod
    def _build_climate(ctx: DiagnosticContext) -> dict:
        """Build climate mode diagnostics."""
        diagnostics: dict = {}
        if ctx.climate_mode and ctx.climate_data is not None:
            diagnostics["climate_control_method"] = ctx.control_method

            diagnostics["active_temperature"] = ctx.climate_data.get_current_temperature
            diagnostics["temperature_details"] = {
                "inside_temperature": ctx.climate_data.inside_temperature,
                "outside_temperature": ctx.climate_data.outside_temperature,
                "temp_switch": ctx.climate_data.temp_switch,
            }

            if ctx.climate_strategy is not None:
                diagnostics["climate_strategy"] = ctx.climate_strategy.value

            diagnostics["climate_conditions"] = {
                "is_summer": ctx.climate_data.is_summer,
                "is_winter": ctx.climate_data.is_winter,
                "is_presence": ctx.climate_data.is_presence,
                "is_sunny": ctx.climate_data.is_sunny,
                "lux_below_threshold": ctx.climate_data.lux_below_threshold,
                "irradiance_below_threshold": ctx.climate_data.irradiance_below_threshold,
            }

        return diagnostics

    @staticmethod
    def _build_last_action(ctx: DiagnosticContext) -> dict:
        """Build last action diagnostics."""
        diagnostics: dict = {}
        if ctx.last_cover_action.get("entity_id"):
            diagnostics["last_cover_action"] = ctx.last_cover_action.copy()
        if ctx.last_skipped_action.get("entity_id"):
            diagnostics["last_skipped_action"] = ctx.last_skipped_action.copy()
        return diagnostics

    @staticmethod
    def _build_configuration(ctx: DiagnosticContext) -> dict:
        """Build configuration diagnostics."""
        from ..const import (
            CONF_AZIMUTH,
            CONF_BLIND_SPOT_ELEVATION,
            CONF_BLIND_SPOT_LEFT,
            CONF_BLIND_SPOT_RIGHT,
            CONF_ENABLE_BLIND_SPOT,
            CONF_ENABLE_MAX_POSITION,
            CONF_ENABLE_MIN_POSITION,
            CONF_FOV_LEFT,
            CONF_FOV_RIGHT,
            CONF_FORCE_OVERRIDE_POSITION,
            CONF_FORCE_OVERRIDE_SENSORS,
            CONF_INTERP,
            CONF_INVERSE_STATE,
            CONF_MAX_ELEVATION,
            CONF_MAX_POSITION,
            CONF_MIN_ELEVATION,
            CONF_MIN_POSITION,
            CONF_MOTION_SENSORS,
            CONF_MOTION_TIMEOUT,
            DEFAULT_MOTION_TIMEOUT,
        )

        options = ctx.config_options
        return {
            "configuration": {
                "azimuth": options.get(CONF_AZIMUTH),
                "fov_left": options.get(CONF_FOV_LEFT),
                "fov_right": options.get(CONF_FOV_RIGHT),
                "min_elevation": options.get(CONF_MIN_ELEVATION),
                "max_elevation": options.get(CONF_MAX_ELEVATION),
                "enable_blind_spot": options.get(CONF_ENABLE_BLIND_SPOT, False),
                "blind_spot_elevation": options.get(CONF_BLIND_SPOT_ELEVATION),
                "blind_spot_left": options.get(CONF_BLIND_SPOT_LEFT),
                "blind_spot_right": options.get(CONF_BLIND_SPOT_RIGHT),
                "min_position": options.get(CONF_MIN_POSITION),
                "max_position": options.get(CONF_MAX_POSITION),
                "enable_min_position": options.get(CONF_ENABLE_MIN_POSITION, False),
                "enable_max_position": options.get(CONF_ENABLE_MAX_POSITION, False),
                "inverse_state": options.get(CONF_INVERSE_STATE, False),
                "interpolation": options.get(CONF_INTERP, False),
                "force_override_sensors": options.get(CONF_FORCE_OVERRIDE_SENSORS, []),
                "force_override_position": options.get(CONF_FORCE_OVERRIDE_POSITION, 0),
                "force_override_active": ctx.is_force_override_active,
                "motion_sensors": options.get(CONF_MOTION_SENSORS, []),
                "motion_timeout": options.get(
                    CONF_MOTION_TIMEOUT, DEFAULT_MOTION_TIMEOUT
                ),
                "motion_detected": ctx.motion_detected,
                "motion_timeout_active": ctx.motion_timeout_active,
            }
        }
