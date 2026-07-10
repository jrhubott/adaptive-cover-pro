"""Floor-mode composition helpers (issue #463).

A "floor" is a minimum-position clamp contributed by an *active* override —
custom-position slot with ``min_mode`` or weather override with ``min_mode``
— that must raise whichever handler ultimately wins the pipeline, regardless
of priority.

These helpers are pure: they read a :class:`PipelineSnapshot` and return
plain data. The registry composes the active floors after picking a winner;
the coordinator's user-move clamp consumes the same helpers so the
``max(active_floors)`` arithmetic exists in exactly one place.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..const import custom_position_handler_name
from .types import PipelineSnapshot


@dataclass(frozen=True, slots=True)
class FloorClampInfo:
    """One active floor contributed to the pipeline composition pass.

    Attributes:
        source: Stable identifier used as the ``handler`` field in the
                decision trace — e.g. ``"custom_position_1"``, ``"weather"``.
        label:  Human-readable name used in the clamp reason string —
                the bound sensor's friendly name, or a fixed string for
                the weather override.
        position: The floor position (0–100) in pre-inversion canonical
                space (0 = closed, 100 = open).

    """

    source: str
    label: str
    position: int


def gather_active_floors(snapshot: PipelineSnapshot) -> list[FloorClampInfo]:
    """Collect every active min-mode floor contributed by the snapshot.

    Order of returned floors:
      1. Custom-position slots, in the order they appear in
         ``snapshot.custom_position_sensors`` (matches ``_build_pipeline``
         registration order).
      2. The weather override floor, if any.

    A custom-position floor is active when its trigger is ``is_on`` and
    ``min_mode`` is True. ``use_my`` floors are excluded — the "Use My"
    path is hardware-pinned and never participates in floor semantics.
    """
    floors: list[FloorClampInfo] = []
    for state in snapshot.custom_position_sensors:
        if state.is_on and state.min_mode and not state.use_my:
            floors.append(
                FloorClampInfo(
                    source=custom_position_handler_name(state.slot),
                    label=state.display_label,
                    position=state.position,
                )
            )
    if snapshot.weather_override_active and snapshot.weather_override_min_mode:
        floors.append(
            FloorClampInfo(
                source="weather",
                label="weather override",
                position=snapshot.weather_override_position,
            )
        )
    return floors


def effective_floor(
    floors: Iterable[FloorClampInfo],
) -> tuple[int, FloorClampInfo | None]:
    """Return the highest active floor and the FloorClampInfo it came from.

    When ``floors`` is empty, returns ``(0, None)`` — the coordinator and
    registry both treat 0 as "no clamp applied".
    """
    winner: FloorClampInfo | None = None
    for info in floors:
        if winner is None or info.position > winner.position:
            winner = info
    if winner is None:
        return 0, None
    return winner.position, winner
