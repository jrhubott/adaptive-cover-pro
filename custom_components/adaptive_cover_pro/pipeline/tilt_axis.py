"""Tilt-axis overlay composition (issue #514).

A per-slot *tilt-only* custom-position contribution fixes the slat angle
(tilt) without claiming the position axis: solar — or whatever wins the
position pipeline — drives the carriage, while the active tilt-only slot
overlays its configured slat angle onto the winner.

This mirrors :mod:`pipeline.floors`: pure helpers that read a
:class:`PipelineSnapshot` and return plain data. The registry composes the
winning tilt-only contribution after picking a position winner; the overlay
fills the winner's tilt only when it is unset (fill-when-unset, decision Q1b)
so a position-winner that already set an explicit tilt keeps it.

The pass is cover-type-agnostic — it reads ``state.tilt_only`` and never asks
"is this venetian". The venetian-specific behaviour (suppressing the global
tilt-only carriage-close) lives in ``VenetianPolicy.post_pipeline_resolve``,
gated on ``PipelineResult.tilt_only_contribution_active``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..const import custom_position_handler_name
from .types import PipelineSnapshot


@dataclass(frozen=True, slots=True)
class TiltAxisContribution:
    """One active tilt-only contribution selected by the tilt-axis pass.

    Attributes:
        source: Stable identifier used as the ``handler`` field in the
                decision trace — e.g. ``"custom_position_1"``.
        label:  Human-readable name used in the trace reason — the bound
                sensor's friendly name, or its entity_id when unnamed.
        tilt:   The slat angle (0–100) to overlay onto the position winner.

    """

    source: str
    label: str
    tilt: int


def gather_tilt_only_contributions(
    snapshot: PipelineSnapshot,
) -> list[TiltAxisContribution]:
    """Collect every active tilt-only contribution from the snapshot.

    A contribution is active when its sensor is ``is_on``, ``tilt_only`` is
    True, and the slot has a configured ``tilt`` value (a tilt-only slot with
    no tilt contributes nothing). Slots are returned in their snapshot order,
    matching ``_build_pipeline`` registration order.
    """
    contributions: list[TiltAxisContribution] = []
    for state in snapshot.custom_position_sensors:
        if state.is_on and state.tilt_only and state.tilt is not None:
            label = state.sensor_name or (
                state.entity_ids[0] if state.entity_ids else "template"
            )
            contributions.append(
                TiltAxisContribution(
                    source=custom_position_handler_name(state.slot),
                    label=label,
                    tilt=state.tilt,
                )
            )
    return contributions


def resolve_tilt_axis(snapshot: PipelineSnapshot) -> TiltAxisContribution | None:
    """Return the highest-priority active tilt-only contribution, or None.

    Priority comes from the slot's ``priority`` field (the same value the
    PipelineRegistry sorts handlers by). When multiple tilt-only slots are
    active, the highest priority wins; ties resolve to the first in snapshot
    order. Returns ``None`` when no tilt-only slot is active.
    """
    winner_state = None
    for state in snapshot.custom_position_sensors:
        if not (state.is_on and state.tilt_only and state.tilt is not None):
            continue
        if winner_state is None or state.priority > winner_state.priority:
            winner_state = state
    if winner_state is None:
        return None
    label = winner_state.sensor_name or (
        winner_state.entity_ids[0] if winner_state.entity_ids else "template"
    )
    return TiltAxisContribution(
        source=custom_position_handler_name(winner_state.slot),
        label=label,
        tilt=winner_state.tilt,
    )
