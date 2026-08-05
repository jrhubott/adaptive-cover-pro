"""Day/Night dual-fabric shade cover-type package (#993, Model A).

Re-exports the public surface so ``from cover_types.day_night_shade import
DayNightShadePolicy`` resolves. The policy drives a single HA entity on two
axes — the vertical carriage (position) and the sheer-vs-blackout fabric blend
(riding the tilt axis) — reusing the venetian ``DualAxisSequencer`` by
composition rather than owning its own.
"""

from __future__ import annotations

from .policy import GEOMETRY_DAY_NIGHT_SHADE_SCHEMA, DayNightShadePolicy

__all__ = [
    "GEOMETRY_DAY_NIGHT_SHADE_SCHEMA",
    "DayNightShadePolicy",
]
