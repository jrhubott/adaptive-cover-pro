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

    # Weather override state (from WeatherManager)
    weather_override_active: bool
    weather_override_position: int

    # Glare zones (vertical covers only — None for awning/tilt)
    glare_zones: GlareZonesConfig | None
    active_zone_names: frozenset[str]


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

    # Optional climate diagnostics set by ClimateHandler
    climate_state: int | None = None
    climate_strategy: ClimateStrategy | None = None
