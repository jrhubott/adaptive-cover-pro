"""Diagnostics builder for Adaptive Cover Pro.

Extracts all diagnostic data assembly from the coordinator into a
standalone, testable class.  The builder operates on a ``DiagnosticContext``
dataclass that bundles every piece of coordinator state it needs, so it
never accesses the coordinator directly.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from ..const import ControlStatus
from ..const import (
    ClimateStrategy,
    ControlMethod,
    FORECAST_STEP_MINUTES,
    ReasonCode,
    SunState,
)
from ..reason_i18n import Reason, render

# Sensor state classifications (issue #693, Q3).
_SENSOR_STATE_NOT_CONFIGURED = "not_configured"
_SENSOR_STATE_UNAVAILABLE = "unavailable"
_SENSOR_STATE_AVAILABLE = "available"
# HA state strings that count as "no real value" for a configured entity.
_UNAVAILABLE_HA_STATES = ("unavailable", "unknown")


@dataclass(frozen=True, slots=True)
class SensorSource:
    """One shared sensor key's provenance and live availability (issue #693, Q3).

    ``source`` is "local" (configured on the cover) or "profile" (inherited
    from a linked Building Profile). ``state`` is one of
    ``not_configured`` / ``unavailable`` / ``available``. ``entity_id`` holds
    the raw option value — a string for most keys, a list for the multi-entity
    keys (gate/severe sensors), or ``None`` when unset.
    """

    key: str
    entity_id: Any  # str | list | None — the raw option value
    source: str
    state: str


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

    # Solar-tracking-only forecast for the rest of today (issue #437 cache).
    # Optional — None when the background recompute hasn't produced one yet.
    position_forecast: Any = None  # Forecast | None

    # Configuration snapshot
    config_options: dict = field(default_factory=dict)

    # Per-cycle options after template resolution — same keys as config_options
    # but with TEMPLATABLE_KEYS rendered to numbers (issue #577). Used to show
    # raw template alongside its resolved value in diagnostics.
    resolved_options: dict = field(default_factory=dict)

    # HA core handle — the sole boundary the builder uses to resolve live entity
    # states and to read a linked Building Profile's options for the sensor
    # source/state subsections (issue #693, Q3). Optional: when absent the
    # profile lookup is skipped and configured entities classify as unavailable.
    hass: Any = None

    # Motion manager state
    motion_detected: bool = True
    motion_timeout_active: bool = False
    motion_hold_active: bool = False
    # Occupancy template's current rendered result (issue #577 follow-up).
    motion_template_active: bool = False

    # Debug & diagnostics (optional — only populated when debug_mode is on or buffer has entries)
    event_timeline: list[dict] | None = None
    manual_override_events: list[dict] | None = (
        None  # deprecated alias; use event_timeline
    )
    cover_command_state: dict[str, dict] | None = None
    debug_config: dict | None = None

    # Meta — integration identity and coordinator health
    integration_version: str | None = None
    cover_type: str | None = None
    last_update_success: bool = True
    last_exception_repr: str | None = None
    last_update_success_time_iso: str | None = None
    update_interval_seconds: float | None = None

    # Live cover entity state (positions + capabilities)
    covers: dict[str, dict] = field(default_factory=dict)

    # Manual override live state (per-entity map)
    manual_override_state: dict | None = None

    # Manual override detection toggles
    manual_toggle: bool = True
    enabled_toggle: bool = True

    # Issue #33 Phase 5: per-entity counts of cross-axis publish-lag
    # suppressions in the last 24 h. Threaded in from
    # ``ManualOverrideManager.primary_axis_suppression_counts()`` so a
    # user looking at a diagnostic file can immediately see whether the
    # new guard is firing — and how often — for their actuator. Empty
    # dict (default) → key omitted from diagnostics output.
    primary_axis_suppression_counts: dict[str, int] = field(default_factory=dict)

    # issue #625: True when the end-of-window position is the live effective
    # default this cycle (window clock-closed AND end_of_window_position set).
    # Populated by the coordinator from the same window_is_closed/eow_pos it
    # computes in _compute_current_effective_default. Surfaced in the
    # default_position diagnostics block to disambiguate sunset-vs-eow.
    end_of_window_active: bool = False

    # issue #786: the effective indoor temperature sensor and its provenance,
    # threaded from the cycle's ClimateReadings. ``source`` is "explicit" /
    # "area" / "none"; ``area_id`` is set only for the "area" source. Surfaced
    # in the climate block so a user can see why a cover reacts to a sensor
    # they never configured on it.
    temp_sensor_entity_id: str | None = None
    temp_sensor_source: str = "none"
    temp_sensor_area_id: str | None = None

    # issue #547: provenance of the outdoor temperature actually used —
    # "live", "forecast_max", "max_of_live_and_forecast", or "live_fallback".
    # Threaded from the cycle's ClimateReadings; surfaced in temperature_details
    # so a user can see whether climate mode reacted to the forecast or the
    # live reading.
    outside_temp_source: str = "live"

    # issue #882: the instance-language reason-template overlay, primed once at
    # setup by the coordinator (``coordinator._reason_labels``). ``None`` → the
    # English defaults, so ``render(...)`` output is byte-identical on an EN
    # install. Kept HA-free (a plain mapping) so the builder stays unit-testable.
    reason_labels: Mapping[str, str] | None = None


# ---------------------------------------------------------------------------
# Strategy label map (moved from coordinator class attribute)
# ---------------------------------------------------------------------------

_CLIMATE_STRATEGY_LABELS: dict[ClimateStrategy, str] = {
    ClimateStrategy.EXTREME_HEAT: "Extreme Heat",
    ClimateStrategy.WINTER_HEATING: "Winter Heating",
    ClimateStrategy.SUMMER_COOLING: "Summer Cooling",
    ClimateStrategy.LOW_LIGHT: "Low Light",
    ClimateStrategy.GLARE_CONTROL: "Glare Control",
}


# ---------------------------------------------------------------------------
# ControlMethod → ControlStatus mapping
# ---------------------------------------------------------------------------

_METHOD_TO_STATUS: dict[ControlMethod, str] = {
    ControlMethod.WEATHER: ControlStatus.WEATHER_OVERRIDE_ACTIVE,
    ControlMethod.MOTION: ControlStatus.MOTION_TIMEOUT,
    ControlMethod.MANUAL: ControlStatus.MANUAL_OVERRIDE,
    # All other methods → pipeline is running normally
    ControlMethod.CLOUD: ControlStatus.ACTIVE,
    ControlMethod.EXTREME_HEAT: ControlStatus.ACTIVE,
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
        diagnostics.update(self._build_meta(ctx))
        diagnostics.update(self._build_solar(ctx))
        diagnostics.update(self._build_position(ctx))
        diagnostics.update(self._build_decision_trace(ctx))
        diagnostics.update(self._build_handler_priorities(ctx))
        diagnostics.update(self._build_time_window(ctx))
        diagnostics.update(self._build_sun_validity(ctx))
        diagnostics.update(self._build_climate(ctx))
        diagnostics.update(self._build_last_action(ctx))
        diagnostics.update(self._build_covers(ctx))
        diagnostics.update(self._build_forecast(ctx))
        diagnostics.update(self._build_manual_override_state(ctx))
        diagnostics.update(self._build_configuration(ctx))
        diagnostics.update(self._build_sensor_sources(ctx))
        diagnostics.update(self._build_debug_info(ctx))

        explanation = diagnostics.get("position_explanation", "")
        return diagnostics, explanation

    # -- private helpers ----------------------------------------------------

    @staticmethod
    def _build_solar(ctx: DiagnosticContext) -> dict:
        """Build solar position diagnostics.

        Sun angles are rounded to 1 decimal place here — the single rounding
        point for all consumers (sensors, Decision Trace, REST API).  Full
        float precision is kept inside the calculation engine and pipeline.
        """
        diagnostics: dict = {}
        sun_azimuth, sun_elevation = ctx.pos_sun
        diagnostics["sun_azimuth"] = (
            round(sun_azimuth, 1) if sun_azimuth is not None else None
        )
        diagnostics["sun_elevation"] = (
            round(sun_elevation, 1) if sun_elevation is not None else None
        )

        if ctx.cover and hasattr(ctx.cover, "gamma"):
            diagnostics["gamma"] = round(ctx.cover.gamma, 1)

        return diagnostics

    @staticmethod
    def _get_control_state_reason(ctx: DiagnosticContext) -> str:
        """Get the current control state reason from pipeline result or cover geometry.

        Renders through the instance-language ``ctx.reason_labels`` overlay
        (issue #882); ``None`` → English defaults, byte-identical to the legacy
        prose. The builder-owned control-state labels emit ``Reason(builder.*)``
        codes; the cover-geometry branch prefers the engine's stable
        ``control_state_reason_code`` (rendered with labels) and falls back to
        the cover's English ``control_state_reason`` prose when no code is
        exposed (e.g. legacy test doubles).
        """
        labels = ctx.reason_labels
        result = ctx.pipeline_result
        if result is not None and result.control_method == ControlMethod.MOTION:
            reason = render(
                Reason(ReasonCode.BUILDER_CONTROL_OCCUPANCY_TIMEOUT), labels
            )
        elif result is not None and result.control_method == ControlMethod.MANUAL:
            reason = render(Reason(ReasonCode.BUILDER_CONTROL_MANUAL_OVERRIDE), labels)
        elif (
            result is not None
            and result.climate_strategy == ClimateStrategy.TRACKING_SEASON_GATE
        ):
            reason = render(
                Reason(ReasonCode.BUILDER_CONTROL_TRACKING_OFF_SEASON), labels
            )
        elif ctx.cover:
            code = getattr(ctx.cover, "control_state_reason_code", None)
            reason = (
                render(Reason(code), labels)
                if code is not None
                else ctx.cover.control_state_reason
            )
        else:
            reason = render(Reason(ReasonCode.BUILDER_UNKNOWN), labels)

        # An applied tilt-only custom slot is otherwise invisible on the tracker
        # entity because the position winner owns the status (#667).
        if result is not None and result.tilt_only_slot is not None:
            reason = render(
                Reason(
                    ReasonCode.BUILDER_CONTROL_TILT_FIXED,
                    {"reason": reason, "slot": result.tilt_only_slot},
                ),
                labels,
            )
        return reason

    @staticmethod
    def _build_position_explanation(ctx: DiagnosticContext) -> str:
        """Build a human-readable explanation of the full position decision chain.

        Derives the explanation from the pipeline result's ``reason`` string
        so there is a single source of truth.  Post-processing transforms
        (interpolation, inverse state) are appended when they changed the value.
        When manual override is active and the cover's physical position diverges
        from the solar calculation, the divergence is surfaced explicitly.
        """
        labels = ctx.reason_labels
        result = ctx.pipeline_result
        if result is None:
            return render(Reason(ReasonCode.BUILDER_UNKNOWN), labels)

        # Outside time window — pipeline ran but commands are gated
        if not ctx.check_adaptive_time:
            pos = result.default_position
            pos_label = Reason(
                ReasonCode.FRAGMENT_SUNSET_POSITION
                if result.is_sunset_active
                else ReasonCode.FRAGMENT_DEFAULT_POSITION
            )
            return render(
                Reason(
                    ReasonCode.BUILDER_OUTSIDE_WINDOW,
                    {"pos_label": pos_label, "pos": pos},
                ),
                labels,
            )

        # Base explanation is the pipeline reason (already human-readable). Prefer
        # the structured payload so the base localizes; fall back to the legacy
        # English ``reason`` string when a result carries no payload.
        base = (
            render(result.reason_payload, labels)
            if result.reason_payload is not None
            else result.reason
        )
        parts: list[str] = [base]

        # Surface the divergence between the physical held position and the solar calc
        # only when they differ — avoids noise when the cover happens to be at the
        # solar position already.
        if (
            result.control_method == ControlMethod.MANUAL
            and result.held_position is not None
            and result.held_position != result.raw_calculated_position
        ):
            parts.append(
                render(
                    Reason(
                        ReasonCode.BUILDER_MANUAL_DIVERGENCE,
                        {
                            "held": result.held_position,
                            "raw": result.raw_calculated_position,
                        },
                    ),
                    labels,
                )
            )

        # Surface an applied tilt-only custom slot alongside the position winner
        # so it is visible in the Control Status string (#667).
        if result.tilt_only_slot is not None:
            parts.append(
                render(
                    Reason(
                        ReasonCode.BUILDER_TILT_FIXED,
                        {"tilt": result.tilt, "slot": result.tilt_only_slot},
                    ),
                    labels,
                )
            )

        # Append post-processing transforms if they changed the value
        final = ctx.final_state
        if ctx.use_interpolation:
            parts.append(
                render(
                    Reason(ReasonCode.BUILDER_INTERPOLATED, {"final": final}), labels
                )
            )
        elif ctx.inverse_state and final != result.position:
            parts.append(
                render(Reason(ReasonCode.BUILDER_INVERSED, {"final": final}), labels)
            )

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
        """Build position diagnostics by composing the smaller per-section helpers."""
        diagnostics: dict = {}
        diagnostics.update(cls._build_position_base(ctx))
        diagnostics.update(cls._build_position_delta_time(ctx))
        diagnostics.update(cls._build_position_calc_details(ctx))
        diagnostics["last_updated"] = dt.datetime.now(dt.UTC).isoformat()
        return diagnostics

    @classmethod
    def _build_position_base(cls, ctx: DiagnosticContext) -> dict:
        """Build calculated position, control status/reason, optional flags, and explanation."""
        diagnostics: dict = {}
        result = ctx.pipeline_result
        raw_pos = result.raw_calculated_position if result is not None else 0
        diagnostics["calculated_position"] = raw_pos

        if result is not None and result.climate_state is not None:
            diagnostics["calculated_position_climate"] = result.climate_state

        diagnostics["control_status"] = cls._determine_control_status(ctx)
        diagnostics["control_state_reason"] = cls._get_control_state_reason(ctx)
        if result is not None and result.bypass_auto_control:
            diagnostics["bypass_auto_control"] = True
        if result is not None and result.use_my_position:
            diagnostics["use_my_position"] = True
        if result is not None and result.tilt is not None:
            diagnostics["tilt"] = result.tilt

        diagnostics["position_explanation"] = cls._build_position_explanation(ctx)
        return diagnostics

    @staticmethod
    def _build_position_delta_time(ctx: DiagnosticContext) -> dict:
        """Threshold values plus delta-from-last-action and time-since-last-action."""
        diagnostics: dict = {
            "delta_position_threshold": ctx.min_change,
            "delta_time_threshold_minutes": ctx.time_threshold,
        }
        result = ctx.pipeline_result
        raw_pos = result.raw_calculated_position if result is not None else 0
        last_action = ctx.last_cover_action

        if last_action.get("position") is not None:
            diagnostics["position_delta_from_last_action"] = abs(
                raw_pos - last_action["position"]
            )

        if last_action.get("timestamp"):
            try:
                last_ts = dt.datetime.fromisoformat(last_action["timestamp"])
                if last_ts.tzinfo is None:
                    # ISO timestamps without offset are stored as UTC by the
                    # coordinator; treat them that way for the elapsed-time math.
                    last_ts = last_ts.replace(tzinfo=dt.UTC)
                now_utc = dt.datetime.now(dt.UTC)
                elapsed = (now_utc - last_ts).total_seconds()
                diagnostics["seconds_since_last_action"] = round(elapsed)
            except (ValueError, AttributeError):
                pass

        return diagnostics

    @staticmethod
    def _round_trace_value(key: str, value: Any) -> Any:
        """Round one raw trace leaf at the presentation boundary (issue #682).

        Convention (issue #140 §Display Rounding): degrees/ratios that aren't
        positions → 1 decimal for angles, ~3 decimals for unit-less ratios;
        distances (``_m``) → 3 decimals; positions/percent → integer; everything
        else (bools, strings, lists, None) passes through untouched. Rounding
        happens here — never inside the calc engines.
        """
        if not isinstance(value, int | float) or isinstance(value, bool):
            return value
        if key.endswith("_pct") or key.endswith("_position") or key == "max_degrees":
            return int(round(value))
        if key.endswith("_deg"):
            return round(value, 1)
        if key.endswith("_m"):
            return round(value, 3)
        # Unit-less ratios (cos_gamma, safety_margin, discriminant, beta_rad, …).
        return round(value, 3)

    @classmethod
    def _round_trace(cls, trace: dict) -> dict:
        """Return a rounded copy of a trace dict, recursing into nested sub-traces.

        Copies rather than mutating so the engine's ``_last_calc_details`` keeps
        full precision for any other reader.
        """
        rounded: dict = {}
        for key, value in trace.items():
            if isinstance(value, dict):
                rounded[key] = cls._round_trace(value)
            else:
                rounded[key] = cls._round_trace_value(key, value)
        return rounded

    @classmethod
    def _build_position_calc_details(cls, ctx: DiagnosticContext) -> dict:
        """Surface the cover's per-cycle calc trace.

        Stamps the ``cover_type`` discriminator from ``ctx.cover_type`` (config
        data — engines never branch on their own type string), the
        ``status`` reason string, and rounds every numeric leaf at this
        presentation boundary.

        When no engine trace was recorded this cycle — the sun is outside the
        window (sunset/elevation/FOV/blind-spot), so the solar handler never
        ran — a minimal fallback trace carrying just the sun inputs is emitted
        with ``position_pct`` None. This keeps the live ``solar_calculation``
        sensor's attributes populated (explaining the geometry and the reason)
        even though its state stays None — there is no solar target to report
        (issue #682 follow-up).
        """
        if not ctx.cover:
            return {}
        calc_details = getattr(ctx.cover, "_last_calc_details", None)
        if calc_details is None:
            calc_details = {
                "sol_elev_deg": getattr(ctx.cover, "sol_elev", None),
                "gamma_deg": getattr(ctx.cover, "gamma", None),
                "position_pct": None,
            }
        details = cls._round_trace(calc_details)
        details["cover_type"] = ctx.cover_type
        details["status"] = getattr(ctx.cover, "control_state_reason", None)
        return {"calculation_details": details}

    @staticmethod
    def _build_time_window(ctx: DiagnosticContext) -> dict:
        """Build time window diagnostics."""
        from ..const import CONF_END_OF_WINDOW_POS

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
                "is_sunset_active": (
                    result.is_sunset_active if result is not None else False
                ),
                "configured_default": (
                    result.configured_default if result is not None else 0
                ),
                "configured_sunset_pos": (
                    result.configured_sunset_pos if result is not None else None
                ),
                # issue #625: the configured end-of-window position (None when
                # disabled) plus whether it is the live effective default this
                # cycle. ``end_of_window_active`` disambiguates "sunset position
                # is active" from "end-of-window position is active" — both set
                # ``is_sunset_active=True``.
                "configured_end_of_window_pos": ctx.config_options.get(
                    CONF_END_OF_WINDOW_POS
                ),
                "end_of_window_active": ctx.end_of_window_active,
                "configured_cloudy_pos": (
                    result.configured_cloudy_pos if result is not None else None
                ),
            },
        }

    @staticmethod
    def _build_sun_validity(ctx: DiagnosticContext) -> dict:
        """Build sun validity diagnostics."""
        if not ctx.cover:
            return {}
        cover = ctx.cover
        in_fov = getattr(cover, "in_fov", None)
        direct_sv = getattr(cover, "direct_sun_valid", None)
        # Derive sun_state from primitives (not from control_state_reason string)
        if direct_sv:
            sun_state = SunState.HITTING
        elif in_fov:
            sun_state = SunState.IN_FOV_NOT_VALID
        else:
            sun_state = SunState.OUTSIDE_FOV
        return {
            "sun_validity": {
                "valid": cover.valid,
                "valid_elevation": cover.valid_elevation,
                "in_blind_spot": getattr(cover, "is_sun_in_blind_spot", None),
                # True when current time is within the astronomical sunset window
                # (after sunset+offset or before sunrise+offset). When True, the
                # solar handler is suppressed (direct_sun_valid is False) even if
                # the sun is geometrically in front of the window.
                "sunset_window_active": getattr(cover, "sunset_valid", None),
                "in_fov": in_fov,
                "direct_sun_valid": direct_sv,
                "sun_state": sun_state.value,
            }
        }

    @staticmethod
    def _build_climate(ctx: DiagnosticContext) -> dict:
        """Build climate mode diagnostics."""
        diagnostics: dict = {}

        # Effective indoor temp sensor + provenance (issue #786). Surfaced
        # whenever a sensor resolved (explicit or auto-resolved from the area)
        # so a user can trace why a cover reacts to a sensor they never
        # configured on it. Absent when nothing resolved (source "none").
        if ctx.temp_sensor_source and ctx.temp_sensor_source != "none":
            diagnostics["temp_sensor"] = {
                "entity_id": ctx.temp_sensor_entity_id,
                "source": ctx.temp_sensor_source,
                "area_id": ctx.temp_sensor_area_id,
            }

        result = ctx.pipeline_result
        if ctx.climate_mode and result is not None and result.climate_data is not None:
            climate_data = result.climate_data
            diagnostics["climate_control_method"] = result.control_method

            # Round temperatures to 1 decimal — presentation boundary.
            raw_temp = climate_data.get_current_temperature
            diagnostics["active_temperature"] = (
                round(raw_temp, 1) if isinstance(raw_temp, int | float) else raw_temp
            )

            def _round_temp(val: object) -> object:
                """Round a temperature value to 1 decimal if numeric."""
                try:
                    return round(float(val), 1)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    return val

            diagnostics["temperature_details"] = {
                "inside_temperature": _round_temp(climate_data.inside_temperature),
                "outside_temperature": _round_temp(climate_data.outside_temperature),
                "outside_temperature_source": ctx.outside_temp_source,
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
                "cloud_coverage_above_threshold": climate_data.cloud_coverage_above_threshold,
                "tracking_seasons": sorted(climate_data.tracking_seasons),
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
    def _compute_data_window(timeline) -> dict:
        """Summarize the time span and capture moment of an event timeline.

        ``start``/``end`` are the earliest/latest ``ts`` in the timeline (None
        when empty); ``captured_at`` is the UTC ISO timestamp the snapshot was
        taken. Shared by ``_build_debug_info`` and the diagnostics export
        null-case marker so both describe their window identically (#656).
        """
        stamps = [e["ts"] for e in (timeline or []) if e.get("ts")]
        return {
            "start": min(stamps) if stamps else None,
            "end": max(stamps) if stamps else None,
            "captured_at": dt.datetime.now(dt.UTC).isoformat(),
        }

    @staticmethod
    def _build_debug_info(ctx: DiagnosticContext) -> dict:
        """Build debug & diagnostics section."""
        diagnostics: dict = {}

        if ctx.debug_config is not None:
            diagnostics["debug_config"] = ctx.debug_config

        # Unified event timeline from the shared ring buffer
        timeline = ctx.event_timeline or ctx.manual_override_events
        # Always record the data window so a downloaded snapshot is
        # self-describing — even when the timeline is empty (#656).
        diagnostics["data_window"] = DiagnosticsBuilder._compute_data_window(timeline)
        if timeline:
            diagnostics["event_timeline"] = timeline
            # Backward-compat filtered alias for consumers that read manual_override_history
            mo_events = [
                e for e in timeline if e.get("event", "").startswith("manual_override_")
            ]
            if mo_events:
                diagnostics["manual_override_history"] = mo_events

        # Always emit cover_commands (empty dict when nothing active)
        diagnostics["cover_commands"] = ctx.cover_command_state or {}

        # Issue #33 Phase 5 cross-axis publish-lag suppression counts.
        # Surfaced only when non-empty so the field stays out of the way
        # for users whose actuator publishes inside the default window.
        if ctx.primary_axis_suppression_counts:
            diagnostics["primary_axis_suppression_last_24h"] = dict(
                ctx.primary_axis_suppression_counts
            )

        return diagnostics

    @staticmethod
    def _build_meta(ctx: DiagnosticContext) -> dict:
        """Build integration identity and coordinator health section."""
        return {
            "meta": {
                "integration_version": ctx.integration_version,
                "cover_type": ctx.cover_type,
                "coordinator_update": {
                    "last_update_success": ctx.last_update_success,
                    "last_exception": ctx.last_exception_repr,
                    "last_update_success_time": ctx.last_update_success_time_iso,
                    "update_interval_seconds": ctx.update_interval_seconds,
                },
            }
        }

    @staticmethod
    def _build_decision_trace(ctx: DiagnosticContext) -> dict:
        """Build per-handler decision trace from pipeline result.

        Note: the trace may include a synthetic ``floor_clamp`` step that is
        not backed by a registered handler — it represents the post-decision
        floor-composition pass in :class:`PipelineRegistry` (issue #463).
        """
        result = ctx.pipeline_result
        if result is None or not result.decision_trace:
            return {"decision_trace": []}
        return {
            "decision_trace": [
                {
                    "handler": step.handler,
                    "matched": step.matched,
                    "reason": step.reason,
                    "position": step.position,
                    **(
                        {"priority": step.priority} if step.priority is not None else {}
                    ),
                    **(
                        {"held_position": step.held_position}
                        if step.held_position is not None
                        else {}
                    ),
                }
                for step in result.decision_trace
            ]
        }

    @staticmethod
    def _build_handler_priorities(ctx: DiagnosticContext) -> dict:
        """Build the configurable built-in handler priority section.

        Shows each handler's effective priority, its class default, and whether
        the user overrode it — visible even for handlers that did not appear in
        this cycle's decision_trace (e.g. a suppressed handler). Ordered by
        effective priority, highest first, to mirror evaluation order.
        """
        from ..pipeline.handlers import (
            HANDLER_PRIORITY_CONF,
            HANDLER_PRIORITY_DEFAULTS,
            resolve_handler_priority,
        )

        options = ctx.config_options or {}
        rows = {
            name: {
                "priority": resolve_handler_priority(options, name),
                "default": HANDLER_PRIORITY_DEFAULTS[name],
                "overridden": options.get(HANDLER_PRIORITY_CONF[name]) is not None,
            }
            for name in HANDLER_PRIORITY_CONF
        }
        ordered = dict(
            sorted(rows.items(), key=lambda kv: kv[1]["priority"], reverse=True)
        )
        return {"handler_priorities": ordered}

    @staticmethod
    def _build_covers(ctx: DiagnosticContext) -> dict:
        """Build live cover entity state section."""
        if not ctx.covers:
            return {"covers": {}}
        return {"covers": ctx.covers}

    @staticmethod
    def _build_forecast(ctx: DiagnosticContext) -> dict:
        """Build the rest-of-day position forecast section.

        The forecast is a **solar-tracking-only** projection: it holds the
        window geometry constant and walks the sun forward through the rest of
        today. It deliberately ignores every real-time handler (manual
        override, motion, weather safety, climate, custom positions), so it is
        useful for validating sun/FOV geometry and timing — *not* for
        explaining why a cover did or did not move at a given instant (the
        ``decision_trace`` section above answers that). The ``description``
        field is emitted into the dump so a reader never mistakes the
        projection for the live decision.
        """
        forecast = ctx.position_forecast
        if forecast is None:
            return {}
        # Reuse the sensor's wire serialization ("forecast" samples + "events",
        # ISO-8601 times) so the dump and the card share one format.
        return {
            "position_forecast": {
                "description": (
                    "Solar-tracking-only projection for the rest of today. "
                    "Holds window geometry constant and walks the sun forward; "
                    "does NOT model manual override, occupancy, weather safety, "
                    "climate, or custom-position handlers. Use it to validate "
                    "sun/FOV geometry and timing, not to explain a specific "
                    "command — see decision_trace for that."
                ),
                "step_minutes": FORECAST_STEP_MINUTES,
                **forecast.to_attrs(),
            }
        }

    @staticmethod
    def _build_manual_override_state(ctx: DiagnosticContext) -> dict:
        """Build per-entity manual override live state section."""
        if ctx.manual_override_state is None:
            return {}
        return {"manual_override_state": ctx.manual_override_state}

    @staticmethod
    def _build_configuration(ctx: DiagnosticContext) -> dict:
        """Build configuration diagnostics."""
        from ..const import (
            BLIND_SPOT_SLOTS,
            CONF_AZIMUTH,
            CONF_CLOUD_SUPPRESSION,
            CONF_CLOUDY_POSITION,
            CONF_ENABLE_BLIND_SPOT,
            CONF_ENABLE_MAX_POSITION,
            CONF_ENABLE_MIN_POSITION,
            CONF_ENABLE_POSITION_MATCHING,
            CONF_END_OF_WINDOW_POS,
            CONF_FOV_LEFT,
            CONF_FOV_RIGHT,
            CONF_INTERP,
            CONF_INVERSE_STATE,
            CONF_IS_SUNNY_SENSOR,
            CONF_IS_SUNNY_TEMPLATE,
            CONF_MAX_ELEVATION,
            CONF_MAX_POSITION,
            CONF_MANUAL_IGNORE_EXTERNAL,
            CONF_MIN_ELEVATION,
            CONF_MIN_POSITION,
            CONF_MIN_POSITION_SUN_TRACKING,
            CONF_MOTION_SENSORS,
            CONF_MOTION_TEMPLATE,
            CONF_MOTION_TEMPLATE_MODE,
            CONF_MOTION_TIMEOUT,
            CONF_POSITION_TOLERANCE,
            DEFAULT_MOTION_TEMPLATE_MODE,
            DEFAULT_MOTION_TIMEOUT,
        )

        from ..templates import is_template_string

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
                # Per-slot blind-spot keys (issue #701). Slot 1 reuses the
                # legacy unsuffixed keys; slots 2/3 are suffixed.
                **{
                    keys[sub]: options.get(keys[sub])
                    for keys in BLIND_SPOT_SLOTS.values()
                    for sub in ("left", "right", "elevation", "elevation_mode")
                },
                "min_position": options.get(CONF_MIN_POSITION),
                "min_position_sun_tracking": options.get(
                    CONF_MIN_POSITION_SUN_TRACKING
                ),
                "max_position": options.get(CONF_MAX_POSITION),
                "enable_min_position": options.get(CONF_ENABLE_MIN_POSITION, False),
                "enable_max_position": options.get(CONF_ENABLE_MAX_POSITION, False),
                "position_tolerance": options.get(CONF_POSITION_TOLERANCE),
                "enable_position_matching": options.get(
                    CONF_ENABLE_POSITION_MATCHING, False
                ),
                "inverse_state": options.get(CONF_INVERSE_STATE, False),
                "interpolation": options.get(CONF_INTERP, False),
                # Kept one release for the companion card (issue #563): True
                # when a safety-priority custom position (the merged force
                # override) is the active pipeline winner.
                "force_override_active": (
                    result is not None
                    and result.is_safety
                    and result.control_method == ControlMethod.CUSTOM_POSITION
                ),
                "motion_sensors": options.get(CONF_MOTION_SENSORS, []),
                "motion_template": options.get(CONF_MOTION_TEMPLATE),
                "motion_template_active": ctx.motion_template_active,
                "motion_template_mode": options.get(
                    CONF_MOTION_TEMPLATE_MODE, DEFAULT_MOTION_TEMPLATE_MODE
                ),
                "motion_timeout": options.get(
                    CONF_MOTION_TIMEOUT, DEFAULT_MOTION_TIMEOUT
                ),
                "motion_detected": ctx.motion_detected,
                "motion_timeout_active": ctx.motion_timeout_active,
                "motion_hold_active": ctx.motion_hold_active,
                "manual_toggle": ctx.manual_toggle,
                "manual_ignore_external": options.get(
                    CONF_MANUAL_IGNORE_EXTERNAL, False
                ),
                "enabled_toggle": ctx.enabled_toggle,
                "cloud_suppression_enabled": options.get(CONF_CLOUD_SUPPRESSION, False),
                "cloudy_position": options.get(CONF_CLOUDY_POSITION),
                # issue #625: raw config value (None when disabled).
                "end_of_window_position": options.get(CONF_END_OF_WINDOW_POS),
                "is_sunny_source": (
                    options.get(CONF_IS_SUNNY_SENSOR)
                    or (
                        "[template]"
                        if is_template_string(options.get(CONF_IS_SUNNY_TEMPLATE))
                        else "weather_state"
                    )
                ),
                "templated_thresholds": DiagnosticsBuilder._templated_thresholds(ctx),
            }
        }

    @classmethod
    def _build_sensor_sources(cls, ctx: DiagnosticContext) -> dict:
        """Build the two shared-sensor source/state subsections (issue #693, Q3).

        Every key in ``BUILDING_PROFILE_SENSOR_KEYS`` is classified per cover via
        the three-way ``classify_profile_sensor_source``: ``"profile"`` (inherited
        from the profile), ``"override"`` (profile defines it but the cover
        overrides it locally), or ``"local"`` (profile leaves it blank, cover keeps
        its own). ``profile`` entries go in the building-profile block; ``override``
        and ``local`` entries go in the local block, distinguished by ``source`` —
        so a diagnostics download shows exactly which sensors a cover has overridden.

        Emits a top-level ``local_sensors`` list always, plus a
        ``building_profile_sensors`` list only when the cover is linked
        (``CONF_BUILDING_PROFILE_ID`` set). The profile block is omitted
        entirely for an unlinked cover. Both lists are additive — the existing
        flat ``configuration`` dict is untouched so current consumers keep
        working.
        """
        from ..const import BUILDING_PROFILE_SENSOR_KEYS, CONF_BUILDING_PROFILE_ID
        from ..profile_link import classify_profile_sensor_source

        options = ctx.config_options or {}
        profile_id = options.get(CONF_BUILDING_PROFILE_ID)
        linked = bool(profile_id)
        profile_options = cls._linked_profile_options(ctx, profile_id)

        profile_block: list[dict] = []
        local_block: list[dict] = []
        for key in sorted(BUILDING_PROFILE_SENSOR_KEYS):
            source, entity_id = classify_profile_sensor_source(
                key, options, profile_options
            )
            bucket = profile_block if source == "profile" else local_block
            descriptor = SensorSource(
                key=key,
                entity_id=entity_id,
                source=source,
                state=cls._classify_sensor_state(ctx.hass, entity_id),
            )
            bucket.append(asdict(descriptor))

        diagnostics: dict = {"local_sensors": local_block}
        if linked:
            diagnostics["building_profile_sensors"] = profile_block
        return diagnostics

    @staticmethod
    def _linked_profile_options(ctx: DiagnosticContext, profile_id: Any) -> dict:
        """Return the linked Building Profile entry's options (empty when none).

        Looks the profile up by ``entry_id`` among the integration's config
        entries — the same surface ``profile_link`` uses — without importing
        the cover-type discriminator (any ACP entry with the id is the profile).
        """
        from ..const import DOMAIN

        if not profile_id or ctx.hass is None:
            return {}
        try:
            entries = ctx.hass.config_entries.async_entries(DOMAIN)
        except AttributeError:
            return {}
        for entry in entries:
            if entry.entry_id == profile_id:
                return entry.options or {}
        return {}

    @staticmethod
    def _classify_sensor_state(hass: Any, entity_id: Any) -> str:
        """Classify a sensor entity's live availability.

        Falsy/empty value → ``not_configured``. A configured entity whose HA
        state is missing/``unavailable``/``unknown`` → ``unavailable``; one that
        resolves to a real state → ``available``. List-valued keys (gate/severe
        sensors) resolve to ``available`` when at least one member resolves.
        """
        if not entity_id:
            return _SENSOR_STATE_NOT_CONFIGURED
        entity_ids = entity_id if isinstance(entity_id, list) else [entity_id]
        for eid in entity_ids:
            state = hass.states.get(eid) if hass is not None else None
            if state is not None and state.state not in _UNAVAILABLE_HA_STATES:
                return _SENSOR_STATE_AVAILABLE
        return _SENSOR_STATE_UNAVAILABLE

    @staticmethod
    def _templated_thresholds(ctx: DiagnosticContext) -> dict:
        """Map each threshold configured as a template to its raw + resolved value.

        Only keys whose raw value is an actual Jinja2 template appear, so a plain
        numeric config yields an empty dict (issue #577).
        """
        from ..config_fields import TEMPLATABLE_KEYS
        from ..templates import is_template_string

        raw = ctx.config_options
        resolved = ctx.resolved_options
        return {
            key: {"template": raw[key], "resolved": resolved.get(key)}
            for key in TEMPLATABLE_KEYS
            if is_template_string(raw.get(key))
        }
