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
