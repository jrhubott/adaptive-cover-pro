"""Axis-target dispatch model.

A cover instance resolves, each update cycle, to a list of ``AxisTarget`` —
one per (entity, axis) the instance actuates. This single representation
subsumes every dispatch shape the integration supports:

- single-axis covers → one ``(entity, position, value)`` target per entity
  (the historic broadcast-to-all-entities loop)
- venetian → ``(entity, position)`` + ``(entity, tilt, SEQUENCE_AFTER_PRIMARY)``
  on the *same* entity (the second axis is a second service, sequenced after
  the carriage settles)
- dual-/split-panel → one ``(entity, position)`` target per *separate* entity,
  each carrying its own value

The coordinator asks the active ``CoverTypePolicy`` for the target list and
dispatches each — it never branches on cover type. See
``CoverTypePolicy.resolve_axis_targets``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import CoverAxis


class DispatchStrategy(Enum):
    """How an ``AxisTarget`` is sent to the cover."""

    INDEPENDENT = auto()
    """Dispatch immediately via the axis's service (its own entity)."""

    SEQUENCE_AFTER_PRIMARY = auto()
    """Wait for this entity's primary (position) axis to settle, then send.

    Used by venetian's tilt axis: the slats are driven only after the carriage
    has stopped, with the back-rotation/settle handling owned by the
    ``DualAxisSequencer``.
    """


@dataclass(frozen=True, slots=True)
class AxisTarget:
    """One axis value to dispatch to one entity this cycle."""

    entity_id: str
    axis: CoverAxis
    value: int
    strategy: DispatchStrategy = DispatchStrategy.INDEPENDENT
    role: str | None = None
    """Optional panel role ("front"/"back"/"top"/"bottom") for diagnostics
    and per-role target sensors. ``None`` for single-entity cover types."""
