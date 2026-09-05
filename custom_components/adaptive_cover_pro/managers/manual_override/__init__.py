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
    OverrideState,
    StopToMy,
    UserContextChange,
    default_stop_to_my_decision,
    default_user_context_decision,
    position_unavailable_decision,
)
from .expiry import STARTED_AT_SOURCE_DERIVED, STARTED_AT_SOURCE_ENGAGED
from .manager import AdaptiveCoverManager
from .position_delta import PositionDeltaDetector
from .registry import DEFAULT_DETECTOR, DETECTOR_REGISTRY, get_detector
from .secondary_axis import (
    SecondaryAxisCheck,
    SecondaryAxisResult,
    effective_manual_threshold,
    resolve_dispatched_secondary_expected,
    resolve_single_axis_suppression,
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
    "OverrideState",
    "STARTED_AT_SOURCE_DERIVED",
    "STARTED_AT_SOURCE_ENGAGED",
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
    "position_unavailable_decision",
    "resolve_dispatched_secondary_expected",
    "resolve_single_axis_suppression",
]
