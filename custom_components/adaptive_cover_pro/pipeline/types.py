"""Pipeline data types."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..enums import ControlMethod


@dataclass(frozen=True)
class PipelineContext:
    """Input to the override pipeline."""

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
