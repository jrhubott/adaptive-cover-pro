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

    # Sun position
    pos_sun: list  # [azimuth, elevation]

    # Cover engine object (AdaptiveGeneralCover) — provides sun geometry, gamma, etc.
    cover: Any  # AdaptiveGeneralCover | None

    # Full pipeline result — single source of truth for position, control method,
    # overrides, raw calculated position, and climate data.
    pipeline_result: Any  # PipelineResult | None

    # Climate mode toggle (switch state)
    climate_mode: bool

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
# ControlMethod → ControlStatus mapping
# ---------------------------------------------------------------------------

_METHOD_TO_STATUS: dict[ControlMethod, str] = {
    ControlMethod.FORCE: ControlStatus.FORCE_OVERRIDE_ACTIVE,
    ControlMethod.WEATHER: ControlStatus.WEATHER_OVERRIDE_ACTIVE,
    ControlMethod.MOTION: ControlStatus.MOTION_TIMEOUT,
    ControlMethod.MANUAL: ControlStatus.MANUAL_OVERRIDE,
    # All other methods → pipeline is running normally
    ControlMethod.CLOUD: ControlStatus.ACTIVE,
    ControlMethod.SUMMER: ControlStatus.ACTIVE,
    ControlMethod.WINTER: ControlStatus.ACTIVE,
    ControlMethod.SOLAR: ControlStatus.ACTIVE,
    ControlMethod.DEFAULT: ControlStatus.ACTIVE,
    ControlMethod.GLARE_ZONE: ControlStatus.ACTIVE,
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

        if ctx.cover and hasattr(ctx.cover, "gamma"):
            diagnostics["gamma"] = ctx.cover.gamma

        return diagnostics

    @staticmethod
    def _get_control_state_reason(ctx: DiagnosticContext) -> str:
        """Get the current control state reason from pipeline result or cover geometry."""
        if ctx.pipeline_result is not None:
            method = ctx.pipeline_result.control_method
            if method == ControlMethod.FORCE:
                return "Force Override"
            if method == ControlMethod.MOTION:
                return "Motion Timeout"
            if method == ControlMethod.MANUAL:
                return "Manual Override"
        if ctx.cover:
            return ctx.cover.control_state_reason
        return "Unknown"

    @staticmethod
    def _build_position_explanation(ctx: DiagnosticContext) -> str:
        """Build a human-readable explanation of the full position decision chain.

        Derives the explanation from the pipeline result's ``reason`` string
        so there is a single source of truth.  Post-processing transforms
        (interpolation, inverse state) are appended when they changed the value.
        """
        result = ctx.pipeline_result
        if result is None:
            return "Unknown"

        # Outside time window — pipeline ran but commands are gated
        if not ctx.check_adaptive_time:
            pos = result.default_position
            pos_label = "sunset position" if result.is_sunset_active else "default position"
            return f"Outside time window → {pos_label} {pos}% (commands paused)"

        # Base explanation is the pipeline reason (already human-readable)
        parts: list[str] = [result.reason]

        # Append post-processing transforms if they changed the value
        final = ctx.final_state
        if ctx.use_interpolation:
            parts.append(f"interpolated → {final}%")
        elif ctx.inverse_state and final != result.position:
            parts.append(f"inversed → {final}%")

        return " → ".join(parts)

    @staticmethod
    def _determine_control_status(ctx: DiagnosticContext) -> str:
        """Determine current control status from pipeline result."""
        if not ctx.automatic_control:
            return ControlStatus.AUTOMATIC_CONTROL_OFF

        result = ctx.pipeline_result
        if result is not None:
            status = _METHOD_TO_STATUS.get(result.control_method, ControlStatus.ACTIVE)
            if status != ControlStatus.ACTIVE:
                return status

        if not ctx.check_adaptive_time:
            return ControlStatus.OUTSIDE_TIME_WINDOW

        if ctx.cover and not ctx.cover.valid:
            return ControlStatus.SUN_NOT_VISIBLE

        return ControlStatus.ACTIVE

    @classmethod
    def _build_position(cls, ctx: DiagnosticContext) -> dict:
        """Build position diagnostics."""
        diagnostics: dict = {}

        result = ctx.pipeline_result
        raw_pos = result.raw_calculated_position if result is not None else 0
        diagnostics["calculated_position"] = raw_pos

        if result is not None and result.climate_state is not None:
            diagnostics["calculated_position_climate"] = result.climate_state

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
                raw_pos - last_action["position"]
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
        if ctx.cover:
            calc_details = getattr(ctx.cover, "_last_calc_details", None)
            if calc_details is not None:
                diagnostics["calculation_details"] = calc_details

        diagnostics["last_updated"] = dt.datetime.now(dt.UTC).isoformat()

        return diagnostics

    @staticmethod
    def _build_time_window(ctx: DiagnosticContext) -> dict:
        """Build time window diagnostics."""
        result = ctx.pipeline_result
        return {
            "time_window": {
                "check_adaptive_time": ctx.check_adaptive_time,
                "after_start_time": ctx.after_start_time,
                "before_end_time": ctx.before_end_time,
                "start_time": ctx.start_time,
                "end_time": ctx.end_time,
            },
            "default_position": {
                # The effective default used this cycle by all pipeline handlers.
                # equals configured_sunset_pos when is_sunset_active=True,
                # equals configured_default otherwise.
                "effective": result.default_position if result is not None else 0,
                "is_sunset_active": result.is_sunset_active if result is not None else False,
                "configured_default": result.configured_default if result is not None else 0,
                "configured_sunset_pos": result.configured_sunset_pos if result is not None else None,
            },
        }

    @staticmethod
    def _build_sun_validity(ctx: DiagnosticContext) -> dict:
        """Build sun validity diagnostics."""
        diagnostics: dict = {}
        if ctx.cover:
            diagnostics["sun_validity"] = {
                "valid": ctx.cover.valid,
                "valid_elevation": ctx.cover.valid_elevation,
                "in_blind_spot": getattr(ctx.cover, "is_sun_in_blind_spot", None),
                # True when current time is within the astronomical sunset window
                # (after sunset+offset or before sunrise+offset). When True, the
                # solar handler is suppressed (direct_sun_valid is False) even if
                # the sun is geometrically in front of the window.
                "sunset_window_active": getattr(ctx.cover, "sunset_valid", None),
            }
        return diagnostics

    @staticmethod
    def _build_climate(ctx: DiagnosticContext) -> dict:
        """Build climate mode diagnostics."""
        diagnostics: dict = {}
        result = ctx.pipeline_result
        if ctx.climate_mode and result is not None and result.climate_data is not None:
            climate_data = result.climate_data
            diagnostics["climate_control_method"] = result.control_method

            diagnostics["active_temperature"] = climate_data.get_current_temperature
            diagnostics["temperature_details"] = {
                "inside_temperature": climate_data.inside_temperature,
                "outside_temperature": climate_data.outside_temperature,
                "temp_switch": climate_data.temp_switch,
            }

            if result.climate_strategy is not None:
                diagnostics["climate_strategy"] = result.climate_strategy.value

            diagnostics["climate_conditions"] = {
                "is_summer": climate_data.is_summer,
                "is_winter": climate_data.is_winter,
                "is_presence": climate_data.is_presence,
                "is_sunny": climate_data.is_sunny,
                "lux_below_threshold": climate_data.lux_below_threshold,
                "irradiance_below_threshold": climate_data.irradiance_below_threshold,
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
        result = ctx.pipeline_result
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
                "force_override_active": (
                    result is not None
                    and result.control_method == ControlMethod.FORCE
                ),
                "motion_sensors": options.get(CONF_MOTION_SENSORS, []),
                "motion_timeout": options.get(
                    CONF_MOTION_TIMEOUT, DEFAULT_MOTION_TIMEOUT
                ),
                "motion_detected": ctx.motion_detected,
                "motion_timeout_active": ctx.motion_timeout_active,
            }
        }
