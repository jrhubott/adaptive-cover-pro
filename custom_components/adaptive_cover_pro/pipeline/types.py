"""Pipeline data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..enums import ClimateStrategy, ControlMethod

if TYPE_CHECKING:
    from ..config_types import CoverConfig, GlareZonesConfig
    from ..engine.covers.base import AdaptiveGeneralCover
    from ..state.climate_provider import ClimateReadings


# ---------------------------------------------------------------------------
# New snapshot — raw state for self-contained plugin handlers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClimateOptions:
    """Climate configuration thresholds for the ClimateHandler."""

    temp_low: float | None
    temp_high: float | None
    temp_switch: bool  # True = use outside temp; False = use inside temp
    transparent_blind: bool
    temp_summer_outside: float | None
    cloud_suppression_enabled: bool
    winter_close_insulation: bool


@dataclass(frozen=True)
class PipelineSnapshot:
    """Raw state passed to all pipeline handlers.

    Handlers read from this snapshot, compute their own conditions, and
    compute their own positions. No pre-computed decisions live here.
    """

    # Shared calculation engine (sun geometry + cover position math)
    cover: AdaptiveGeneralCover

    # Cover configuration
    config: CoverConfig
    cover_type: str  # "cover_blind" / "cover_awning" / "cover_tilt"

    # Effective default position — the single source of truth for all handlers.
    # Computed by compute_effective_default() before the pipeline runs:
    #   - equals sunset_pos when current time is in the astronomical sunset window
    #   - equals h_def at all other times
    # Handlers MUST use this field; accessing snapshot.cover.default is incorrect
    # and will raise AttributeError (the property has been intentionally removed).
    #
    # NOTE: The raw config values (h_def, sunset_pos) are intentionally NOT
    # exposed on this snapshot.  There is no way for a handler to reconstruct
    # a different default without going through compute_effective_default().
    # The raw values are only available on PipelineResult (written by the
    # coordinator *after* evaluation) so they appear in diagnostics without
    # being visible to handler logic.
    default_position: int

    # True when default_position == sunset_pos (astronomical sunset window active).
    # Handlers may read this to label reason strings; they must not use it to
    # derive a different position.
    is_sunset_active: bool

    # Climate readings (raw sensor values — None if not configured)
    climate_readings: ClimateReadings | None
    climate_mode_enabled: bool
    climate_options: ClimateOptions | None

    # Force override sensor states (entity_id -> is "on")
    force_override_sensors: dict[str, bool]
    force_override_position: int

    # Manager states (inherently stateful; managers track across update cycles)
    manual_override_active: bool
    motion_timeout_active: bool

    # Weather override state (from WeatherManager)
    weather_override_active: bool
    weather_override_position: int

    # Glare zones (vertical covers only — None for awning/tilt)
    glare_zones: GlareZonesConfig | None
    active_zone_names: frozenset[str]

    # When True (default), weather override sends commands even if automatic_control is OFF.
    # Users can disable this if they want weather override to respect the auto-control toggle.
    weather_bypass_auto_control: bool = True

    # Minimum position mode: when True, the configured position acts as a floor —
    # the handler returns max(configured, raw_calculated) instead of always returning configured.
    force_override_min_mode: bool = False
    weather_override_min_mode: bool = False

    # True when current time is within the configured start/end operational window.
    # Handlers that should only run during the active window (e.g. SolarHandler,
    # GlareZoneHandler) check this field and return None when it is False.
    # Defaults to True so that handlers which don't check it are unaffected and
    # existing tests that construct PipelineSnapshot without this field continue
    # to pass.
    in_time_window: bool = True

    # True when the Motion Control switch is enabled.  MotionTimeoutHandler
    # checks this field and passes through (returns None) when it is False,
    # allowing lower-priority handlers to run as if motion timeout is inactive.
    # Defaults to True for backward compatibility.
    motion_control_enabled: bool = True

    # Custom position sensor states: list of (entity_id, is_on, position, priority, min_mode, use_my).
    # Each entry corresponds to one configured custom position slot.  The pipeline
    # creates a separate CustomPositionHandler instance per slot, each with its own
    # priority, so the PipelineRegistry sorts them correctly relative to all other
    # handlers.  The handler reads its own sensor's is_on state from this list by
    # matching entity_id.
    # min_mode: when True, the position acts as a floor (max of configured vs calculated).
    # use_my: when True and my_position_value is set, trigger the cover's "My" hardware
    #   preset via cover.stop_cover instead of the slot's numeric position.
    # Defaults to empty list (feature disabled / not configured).
    custom_position_sensors: list[tuple[str, bool, int, int, bool, bool]] = field(default_factory=list)

    # Somfy "My" position support.
    # my_position_value: the position (1–99 %) the user programmed on the motor remote.
    #   None = feature disabled for this cover.
    # sunset_use_my: when True, the sunset/end_time return path triggers My instead of
    #   the normal open/close threshold fallback (for non-position-capable covers).
    my_position_value: int | None = None
    sunset_use_my: bool = False


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionStep:
    """Record of one handler's evaluation."""

    handler: str
    matched: bool
    reason: str
    position: int | None


@dataclass(frozen=True)
class PipelineResult:
    """Output of the override pipeline."""

    position: int
    control_method: ControlMethod
    reason: str
    decision_trace: list[DecisionStep] = field(default_factory=list)
    tilt: int | None = None

    # Raw geometric position before post-processing (interpolation/inverse_state).
    # Set by SolarHandler when direct sun is valid, otherwise equals the effective
    # default position.  Used by diagnostics to show the pure calculation result.
    raw_calculated_position: int = 0

    # Sunset context — written by the coordinator via dataclasses.replace() after
    # pipeline evaluation, NOT sourced from the handler snapshot.  This keeps
    # the raw config values out of handler logic while still surfacing them in
    # diagnostics and the Decision Trace sensor.
    default_position: int = 0
    is_sunset_active: bool = False
    configured_default: int = 0           # raw h_def from user config
    configured_sunset_pos: int | None = None  # raw sunset_pos (None = not configured)

    # Optional climate diagnostics set by ClimateHandler
    climate_state: int | None = None
    climate_strategy: ClimateStrategy | None = None
    climate_data: Any = None  # ClimateCoverData | None — avoids circular import

    # When True, this result is applied even when automatic_control is OFF.
    # Set by safety handlers (ForceOverrideHandler, WeatherOverrideHandler) so
    # that wind/rain/force protection still works when the user has paused
    # normal sun-tracking automation.
    bypass_auto_control: bool = False

    # When True, the coordinator should route this command through
    # CoverCommandService.send_my_position() on non-position-capable covers
    # (cover.stop_cover while stationary → triggers the Somfy "My" hardware preset).
    # Position-capable covers gracefully fall through to set_cover_position(position).
    use_my_position: bool = False
