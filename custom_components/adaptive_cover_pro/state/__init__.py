"""State providers — read Home Assistant state into pure data."""

from .climate_provider import ClimateProvider, ClimateReadings
from .cover_provider import CoverProvider
from .snapshot import CoverCapabilities, CoverStateSnapshot, SunSnapshot
from .sun_provider import SunProvider
from .window_transition_tracker import WindowTransitionTracker

__all__ = [
    "ClimateProvider",
    "ClimateReadings",
    "CoverCapabilities",
    "CoverProvider",
    "CoverStateSnapshot",
    "SunProvider",
    "SunSnapshot",
    "WindowTransitionTracker",
]
