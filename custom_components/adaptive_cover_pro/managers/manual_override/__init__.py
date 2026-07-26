"""Manual-override subsystem: the engine plus its pluggable detectors.

Public surface is re-exported here so existing imports
(``from ...managers.manual_override import AdaptiveCoverManager``) keep working
unchanged, and so new detection patterns are reachable from one place.
"""

from __future__ import annotations

from .detector import (
    DetectionContext,
    DetectorConfig,
    OverrideDecision,
    OverrideDetector,
    StopToMy,
    UserContextChange,
    default_stop_to_my_decision,
    default_user_context_decision,
)
from .manager import AdaptiveCoverManager, inverse_state, to_logical
from .position_delta import PositionDeltaDetector
from .registry import DEFAULT_DETECTOR, DETECTOR_REGISTRY, get_detector
from .secondary_axis import (
    SecondaryAxisCheck,
    SecondaryAxisResult,
    effective_manual_threshold,
    resolve_dispatched_secondary_expected,
)
from .time_window import TimeWindowDetector

__all__ = [
    "DEFAULT_DETECTOR",
    "DETECTOR_REGISTRY",
    "AdaptiveCoverManager",
    "DetectionContext",
    "DetectorConfig",
    "OverrideDecision",
    "OverrideDetector",
    "PositionDeltaDetector",
    "SecondaryAxisCheck",
    "SecondaryAxisResult",
    "StopToMy",
    "TimeWindowDetector",
    "UserContextChange",
    "default_stop_to_my_decision",
    "default_user_context_decision",
    "effective_manual_threshold",
    "get_detector",
    "inverse_state",
    "resolve_dispatched_secondary_expected",
    "to_logical",
]
