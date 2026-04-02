"""Pipeline data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..enums import ClimateStrategy, ControlMethod

if TYPE_CHECKING:
    from ..config_types import CoverConfig, GlareZonesConfig
    from ..engine.covers.base import AdaptiveGeneralCover
    from ..state.climate_provider import ClimateReadings


# ---------------------------------------------------------------------------
# Legacy context — kept for backward compatibility during migration.
# Remove after all handlers are migrated to PipelineSnapshot (Task 15).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineContext:
    """Input to the override pipeline. DEPRECATED: use PipelineSnapshot."""

    calculated_position: int
    climate_position: int | None
    default_position: int
    raw_calculated_position: int
    in_time_window: bool
    direct_sun_valid: bool
    force_override_active: bool
    force_override_position: int
    motion_timeout_active: bool
    manual_override_active: bool
    climate_mode_enabled: bool
    climate_is_summer: bool
    climate_is_winter: bool

    wind_active: bool = False
    wind_retract_position: int = 100
    cloud_suppression_active: bool = False


# ---------------------------------------------------------------------------
# New snapshot — raw state for self-contained plugin handlers
# ---------------------------------------------------------------------------


@dataclass
class ClimateOptions:
    """Climate configuration thresholds for the ClimateHandler."""

    temp_low: float | None
    temp_high: float | None
    temp_switch: bool  # True = use outside temp; False = use inside temp
    transparent_blind: bool
    temp_summer_outside: float | None
    cloud_suppression_enabled: bool


@dataclass
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
    default_position: int  # h_def or sunset_pos (no solar calculation)

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

    # Glare zones (vertical covers only — None for awning/tilt)
    glare_zones: GlareZonesConfig | None
    active_zone_names: set[str]


# ---------------------------------------------------------------------------
# Output types (shared by both PipelineContext and PipelineSnapshot flows)
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

    # Optional climate diagnostics set by ClimateHandler
    climate_state: int | None = None
    climate_strategy: ClimateStrategy | None = None
