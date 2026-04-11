"""Override pipeline for Adaptive Cover Pro.

The pipeline evaluates a chain of handlers in priority order to determine
the final cover position and control method for each update cycle.
"""

from .handler import OverrideHandler
from .helpers import (
    apply_snapshot_limits,
    compute_default_position,
    compute_raw_calculated_position,
    compute_solar_position,
)
from .registry import PipelineRegistry
from .types import DecisionStep, PipelineResult

__all__ = [
    "DecisionStep",
    "OverrideHandler",
    "PipelineRegistry",
    "PipelineResult",
    "apply_snapshot_limits",
    "compute_default_position",
    "compute_raw_calculated_position",
    "compute_solar_position",
]
