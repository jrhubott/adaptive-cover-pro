"""Override pipeline for Adaptive Cover Pro.

The pipeline evaluates a chain of handlers in priority order to determine
the final cover position and control method for each update cycle.
"""

from .handler import OverrideHandler
from .registry import PipelineRegistry
from .types import DecisionStep, PipelineResult

__all__ = [
    "DecisionStep",
    "OverrideHandler",
    "PipelineRegistry",
    "PipelineResult",
]
