"""Floor-mode composition helpers (issue #463).

A "floor" is a minimum-position clamp contributed by an *active* override —
custom-position slot with ``min_mode`` or weather override with ``min_mode``
— that must raise whichever handler ultimately wins the pipeline, regardless
of priority.

**This module is now an adapter.** Issue #943 generalized floors into the
2-axis × {fixed, min, max, range} model in :mod:`pipeline.axis_constraints`; a
floor is simply a position-axis constraint of ``kind=MIN``. The functions here
delegate to that model and re-shape its output into the ``FloorClampInfo`` the
coordinator's user-move clamp (issues #472/#416/#372) still consumes, so the
``max(active_floors)`` arithmetic exists in exactly one place and the manual
path keeps its byte-identical behavior.

These helpers stay pure: they read a :class:`PipelineSnapshot` and return
plain data.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..const import AxisConstraintMode
from ..cover_types.base import AXIS_NAME_POSITION
from .axis_constraints import AxisConstraint, compose_bounds, gather_axis_constraints
from .types import PipelineSnapshot


@dataclass(frozen=True, slots=True)
class FloorClampInfo:
    """One active floor contributed to the pipeline composition pass.

    A position-axis view of an :class:`~pipeline.axis_constraints.AxisConstraint`
    with a low bound. Kept as the public shape for the coordinator's user-move
    clamp.

    Attributes:
        source: Stable identifier used as the ``handler`` field in the
                decision trace — e.g. ``"custom_position_1"``, ``"weather"``.
        label:  Human-readable name used in the clamp reason string —
                the bound sensor's friendly name, or a fixed string for
                the weather override.
        position: The floor position (0–100) in pre-inversion canonical
                space (0 = closed, 100 = open).
        priority: The contributing override's pipeline priority — the custom
                slot's configured priority, or the weather handler's declared
                priority for the weather floor. The user-move clamp gates on
                this so a floor only clamps a manual command when it outranks
                manual override (issue #472); the pipeline-winner clamp ignores
                it (auto-rule composition stays unconditional, issue #463).

    """

    source: str
    label: str
    position: int
    priority: int


def _as_floor(constraint: AxisConstraint) -> FloorClampInfo:
    """Project a low-bounded position constraint onto the floor shape."""
    assert constraint.low is not None  # noqa: S101 — guarded by the caller's filter
    return FloorClampInfo(
        source=constraint.source,
        label=constraint.label,
        position=constraint.low,
        priority=constraint.priority,
    )


def gather_active_floors(snapshot: PipelineSnapshot) -> list[FloorClampInfo]:
    """Collect every active min-mode floor contributed by the snapshot.

    Order of returned floors:
      1. Custom-position slots, in the order they appear in
         ``snapshot.custom_position_sensors`` (matches ``_build_pipeline``
         registration order).
      2. The weather override floor, if any.

    A custom-position floor is active when its trigger is ``is_on`` and the
    slot's derived ``position_mode`` carries a low bound (``MIN`` / ``RANGE``
    — ``min_mode`` in the stored config). ``use_my`` floors are excluded — the
    "Use My" path is hardware-pinned and never participates in floor semantics.
    Both facts are enforced by :func:`gather_axis_constraints`; this filters its
    output down to the position lows.
    """
    return [
        _as_floor(c)
        for c in gather_axis_constraints(snapshot)
        if c.axis == AXIS_NAME_POSITION
        and c.kind is not AxisConstraintMode.FIXED
        and c.low is not None
    ]


def effective_floor(
    floors: Iterable[FloorClampInfo],
) -> tuple[int, FloorClampInfo | None]:
    """Return the highest active floor and the FloorClampInfo it came from.

    The max-of-floors rule (issue #496) lives in
    :func:`~pipeline.axis_constraints.compose_bounds`; this resolves the
    composed low back to the floor that produced it so callers keep the
    ``label`` / ``priority`` they need for tracing and gating. Ties resolve to
    the first floor in iteration order, as before.

    When ``floors`` is empty, returns ``(0, None)`` — the coordinator and
    registry both treat 0 as "no clamp applied".
    """
    floors = list(floors)
    composed, _ = compose_bounds(
        [
            AxisConstraint(
                axis=AXIS_NAME_POSITION,
                kind=AxisConstraintMode.MIN,
                low=f.position,
                high=None,
                source=f.source,
                label=f.label,
                priority=f.priority,
                slot=0,
            )
            for f in floors
        ],
        AXIS_NAME_POSITION,
    )
    if composed is None:
        return 0, None
    winner = next(f for f in floors if f.position == composed)
    return composed, winner
