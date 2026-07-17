"""Tilt-axis overlay composition (issue #514).

A per-slot *tilt-only* custom-position contribution fixes the slat angle
(tilt) without claiming the position axis: solar — or whatever wins the
position pipeline — drives the carriage, while the active tilt-only slot
overlays its configured slat angle onto the winner.

**This module is now an adapter.** Issue #943 folded the tilt-axis pass into
the unified model in :mod:`pipeline.axis_constraints`: a tilt-only slot is a
tilt-axis constraint of ``kind=FIXED``, and "highest priority wins" is the
resolution rule that kind has always used. The functions here delegate and
re-shape the result into ``TiltAxisContribution``.

The overlay stays fill-when-unset (decision Q1b) — a position-winner that
already set an explicit tilt keeps it. That application rule lives in the
registry; the *bounded* tilt constraints #943 added (tilt min/max) deliberately
clamp-when-set instead, which is why kind, not axis, is what selects the rule.

The pass is cover-type-agnostic — it reads ``state.tilt_mode`` and never asks
"is this venetian". The venetian-specific behaviour (suppressing the global
tilt-only carriage-close) lives in ``VenetianPolicy.post_pipeline_resolve``,
gated on ``PipelineResult.tilt_only_contribution_active``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..const import AxisConstraintMode
from ..cover_types.base import AXIS_NAME_TILT
from .axis_constraints import AxisConstraint, gather_axis_constraints, resolve_fixed
from .types import PipelineSnapshot


@dataclass(frozen=True, slots=True)
class TiltAxisContribution:
    """One active tilt-only contribution selected by the tilt-axis pass.

    A tilt-axis view of a ``FIXED``
    :class:`~pipeline.axis_constraints.AxisConstraint`.

    Attributes:
        source: Stable identifier used as the ``handler`` field in the
                decision trace — e.g. ``"custom_position_1"``.
        label:  Human-readable name used in the trace reason — the bound
                sensor's friendly name, or its entity_id when unnamed.
        tilt:   The slat angle (0–100) to overlay onto the position winner.
        slot:   1-based slot number of the contributing custom-position slot,
                surfaced in the Control Status string when applied (#667).

    """

    source: str
    label: str
    tilt: int
    slot: int


def _as_contribution(constraint: AxisConstraint) -> TiltAxisContribution:
    """Project a FIXED tilt constraint onto the contribution shape."""
    assert constraint.value is not None  # noqa: S101 — FIXED always carries a value
    return TiltAxisContribution(
        source=constraint.source,
        label=constraint.label,
        tilt=constraint.value,
        slot=constraint.slot,
    )


def _fixed_tilt_constraints(snapshot: PipelineSnapshot) -> list[AxisConstraint]:
    """Every active FIXED tilt claim in the snapshot, in snapshot order."""
    return [
        c
        for c in gather_axis_constraints(snapshot)
        if c.axis == AXIS_NAME_TILT and c.kind is AxisConstraintMode.FIXED
    ]


def gather_tilt_only_contributions(
    snapshot: PipelineSnapshot,
) -> list[TiltAxisContribution]:
    """Collect every active tilt-only contribution from the snapshot.

    A contribution is active when its sensor is ``is_on``, the slot's derived
    ``tilt_mode`` is ``FIXED`` (``tilt_only`` in the stored config), and the
    slot has a configured ``tilt`` value — a tilt-only slot with no tilt
    contributes nothing. Slots are returned in their snapshot order, matching
    ``_build_pipeline`` registration order.
    """
    return [_as_contribution(c) for c in _fixed_tilt_constraints(snapshot)]


def resolve_tilt_axis_from(
    constraints: Iterable[AxisConstraint],
) -> TiltAxisContribution | None:
    """Resolve the tilt-only winner from already-gathered *constraints*.

    The registry gathers once and composes both axes from that one list, so it
    calls this rather than :func:`resolve_tilt_axis` — re-walking the snapshot
    to rediscover the same constraints would be a second source of truth for
    which slots are active.
    """
    winner = resolve_fixed(constraints, AXIS_NAME_TILT)
    return None if winner is None else _as_contribution(winner)


def resolve_tilt_axis(snapshot: PipelineSnapshot) -> TiltAxisContribution | None:
    """Return the highest-priority active tilt-only contribution, or None.

    Priority comes from the slot's ``priority`` field (the same value the
    PipelineRegistry sorts handlers by). When multiple tilt-only slots are
    active, the highest priority wins; ties resolve to the first in snapshot
    order. Returns ``None`` when no tilt-only slot is active.
    """
    return resolve_tilt_axis_from(gather_axis_constraints(snapshot))
