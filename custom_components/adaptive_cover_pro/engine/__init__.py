"""Engine package — pure calculation logic with no Home Assistant dependency."""

from .sun_geometry import SunGeometry

__all__ = ["SunGeometry"]
