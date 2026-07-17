"""Unified per-axis constraint composition (issue #943).

A *constraint* is one active override's claim on one axis of the cover. The
handler that owns the claim defers (returns ``None`` from ``evaluate``); the
pipeline resolves normally and this module composes the claims onto whatever
won. That is priority-independent by construction — a constraint clamps the
winner regardless of who the winner is.

**This module is the generalization of two single-purpose passes.** Before
#943 the same shape was written twice:

* ``floors.py``    — position-min only: max-of-values, always-clamp,
  ``held_position``-aware (issues #463 / #496 / #534 / #809).
* ``tilt_axis.py`` — tilt-fixed only: highest-priority-wins, fill-when-unset
  (issue #514).

Those two rules read like an axis difference but are really a **kind**
difference. A position floor is ``kind=MIN``; a tilt-only slot is
``kind=FIXED``. Once that is named, one gather + one compose serves both axes,
and the new bounds (#943's position-max and tilt-min/max) fall out of the
existing rules rather than needing new ones:

============  =========================================  ===================
kind          resolution                                 application
============  =========================================  ===================
``FIXED``     highest priority wins (ties → first)       fill-when-unset
``MIN``       max of the lows        (#496)              always-clamp
``MAX``       min of the highs       (the #496 mirror)   always-clamp
``RANGE``     both of the above                          always-clamp
============  =========================================  ===================

No existing behavior changes, because a pre-#943 config can only ever produce
position-``MIN`` and tilt-``FIXED`` constraints — exactly the two cells the old
modules implemented. ``floors.py`` and ``tilt_axis.py`` survive as thin
adapters over this module so the coordinator's user-move clamp (#472/#416/#372)
and the registry keep sharing one implementation of the arithmetic.

Everything here is pure: it reads a :class:`PipelineSnapshot` and returns plain
data. Cover-type-agnostic by construction — axes are keyed by the shared
``AXIS_NAME_*`` vocabulary and the pass never asks "is this venetian".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..const import AxisConstraintMode, ReasonCode, custom_position_handler_name
from ..cover_types.base import AXIS_NAME_POSITION, AXIS_NAME_TILT
from ..reason_i18n import Reason
from .types import DecisionStep, PipelineSnapshot


@dataclass(frozen=True, slots=True)
class AxisConstraint:
    """One active override's claim on one axis.

    Replaces ``FloorClampInfo`` (position-min) and ``TiltAxisContribution``
    (tilt-fixed) with a single type covering both.

    Attributes:
        axis:  Which axis this constrains — one of the ``AXIS_NAME_*``
               constants from ``cover_types.base``. A *value*, never a
               cover-type branch: code outside ``cover_types/`` must not ask
               what kind of cover it is looking at.
        kind:  How the claim resolves and applies. See the table in the module
               docstring.
        low:   Floor value (0–100, pre-inversion canonical space), or None.
               For ``FIXED`` this equals ``high`` — the exact value.
        high:  Ceiling value, or None. For ``FIXED`` this equals ``low``.
        source: Stable identifier used as the ``handler`` field in the decision
               trace — e.g. ``"custom_position_1"``, ``"weather"``.
        label: Human-readable name for the trace reason — the bound sensor's
               friendly name, or a fixed string for the weather override.
        priority: The contributing override's pipeline priority. ``FIXED``
               resolution sorts on this; the user-move clamp gates on it so a
               floor only clamps a manual command when it outranks manual
               override (#472). The pipeline-winner clamp ignores it — auto-rule
               composition stays unconditional (#463).
        slot:  1-based custom-position slot number, or 0 for non-slot sources
               (the weather floor). Surfaced in the Control Status string
               (#667).

    """

    axis: str
    kind: AxisConstraintMode
    low: int | None
    high: int | None
    source: str
    label: str
    priority: int
    slot: int

    @property
    def value(self) -> int | None:
        """The exact value of a ``FIXED`` claim (``low`` and ``high`` agree)."""
        return self.low


def clamp_to_bounds(value: int, low: int | None, high: int | None) -> int:
    """Clamp *value* into ``[low, high]``; either bound may be absent.

    **The one clamp formula.** Every caller — the registry's position pass, its
    tilt pass, the venetian engine-tilt clamp, and ``floors.effective_floor``'s
    consumers — goes through here, so the arithmetic exists exactly once
    (CODING_GUIDELINES § Single-Source-of-Truth Helpers).

    Order matters: the ceiling applies first and the floor last, so when a
    caller hands in conflicting bounds (``low > high``) **the floor wins**.
    That is the deliberate conflict rule — a floor is a protection commitment
    (keep at least this much cover), and honoring it over a ceiling fails safe.
    It also keeps the position pass's ``final > effective_winner_pos`` predicate
    exactly equivalent to the pre-#943 ``floor_raised``.

    Bounds are tested with ``is None``, never truthiness: 0 is a real bound.
    """
    if high is not None:
        value = min(value, high)
    if low is not None:
        value = max(value, low)
    return value


def compose_bounds(
    constraints: Iterable[AxisConstraint], axis: str
) -> tuple[int | None, int | None]:
    """Compose every bounded constraint on *axis* into one ``(low, high)``.

    ``low`` is the **max of the lows** — issue #496's max-of-floors rule, now
    stated once for every axis. ``high`` is the **min of the highs**, its single
    mirror. Both mean "the most restrictive claim wins", which is what makes
    composition order-independent.

    ``FIXED`` constraints are skipped: they resolve by priority
    (:func:`resolve_fixed`), not by composition. Returns ``(None, None)`` when
    nothing bounds the axis.
    """
    low: int | None = None
    high: int | None = None
    for c in constraints:
        if c.axis != axis or c.kind is AxisConstraintMode.FIXED:
            continue
        if c.low is not None and (low is None or c.low > low):
            low = c.low
        if c.high is not None and (high is None or c.high < high):
            high = c.high
    return low, high


def bounding_constraint(
    constraints: Iterable[AxisConstraint],
    axis: str,
    value: int,
    *,
    low: bool,
) -> AxisConstraint | None:
    """Return the single constraint whose bound actually bound this cycle.

    ``compose_bounds`` collapses many claims into one ``(low, high)`` pair, so
    a clamp knows the value it applied but not *which* claim produced it. This
    walks back: the binding floor is the first ``MIN``/``RANGE`` whose ``low``
    equals ``value`` (``low=True``); the binding ceiling the first
    ``MAX``/``RANGE`` whose ``high`` equals ``value`` (``low=False``). "First"
    matches the tie rule the composition already uses (max-of-mins keeps the
    earliest slot on a tie), so the trace credits exactly one slot — never the
    join of every active bound (audit finding 4a). Returns None when nothing
    matched (an inert axis).
    """
    for c in constraints:
        if c.axis != axis or c.kind is AxisConstraintMode.FIXED:
            continue
        edge = c.low if low else c.high
        if edge is not None and edge == value:
            return c
    return None


def resolve_fixed(
    constraints: Iterable[AxisConstraint], axis: str
) -> AxisConstraint | None:
    """Return the highest-priority ``FIXED`` claim on *axis*, or None.

    Ties resolve to the first in iteration order (snapshot order, which matches
    ``_build_pipeline`` registration order) — byte-identical to the rule
    ``tilt_axis.resolve_tilt_axis`` has used since #514.
    """
    winner: AxisConstraint | None = None
    for c in constraints:
        if c.axis != axis or c.kind is not AxisConstraintMode.FIXED:
            continue
        if winner is None or c.priority > winner.priority:
            winner = c
    return winner


def _bounded(
    axis: str,
    mode: AxisConstraintMode,
    low: int | None,
    high: int | None,
    *,
    source: str,
    label: str,
    priority: int,
    slot: int,
) -> AxisConstraint | None:
    """Build a bounded constraint, or None when the axis makes no claim."""
    if mode in (AxisConstraintMode.NONE, AxisConstraintMode.FIXED):
        return None
    return AxisConstraint(
        axis=axis,
        kind=mode,
        low=low,
        high=high,
        source=source,
        label=label,
        priority=priority,
        slot=slot,
    )


def gather_axis_constraints(snapshot: PipelineSnapshot) -> list[AxisConstraint]:
    """Collect every active axis constraint the snapshot contributes.

    One pass over the snapshot emits, in this order (which the trace relies on):

      1. Per custom-position slot, in snapshot order (matching
         ``_build_pipeline`` registration order): its position claim then its
         tilt claim. A slot may constrain both axes.
      2. The weather override's min-mode position floor, if any.

    Slot modes are read straight off ``CustomPositionSensorState`` — the
    snapshot builder already derived them at the single normalization site, so
    no boolean precedence is re-litigated here.

    ``use_my`` position claims are excluded: the My path is hardware-pinned and
    never participates in constraint semantics (pre-#943 behavior, preserved).
    """
    # Local import: ``pipeline.handlers`` pulls in cover-type policies, so a
    # module-level import here would form a circular import chain. The class is
    # still the single source of truth for the weather priority — never inline
    # the magic number.
    from .handlers.weather import WeatherOverrideHandler

    constraints: list[AxisConstraint] = []
    for state in snapshot.custom_position_sensors:
        if not state.is_on:
            continue
        source = custom_position_handler_name(state.slot)
        shared = {
            "source": source,
            "label": state.display_label,
            "priority": state.priority,
            "slot": state.slot,
        }

        # --- Position axis ---
        if not state.use_my:
            position_low = (
                state.position
                if state.position_mode
                in (AxisConstraintMode.MIN, AxisConstraintMode.RANGE)
                else None
            )
            pos = _bounded(
                AXIS_NAME_POSITION,
                state.position_mode,
                position_low,
                state.position_max,
                **shared,
            )
            if pos is not None:
                constraints.append(pos)

        # --- Tilt axis ---
        if state.tilt_mode is AxisConstraintMode.FIXED and state.tilt is not None:
            constraints.append(
                AxisConstraint(
                    axis=AXIS_NAME_TILT,
                    kind=AxisConstraintMode.FIXED,
                    low=state.tilt,
                    high=state.tilt,
                    **shared,
                )
            )
        else:
            tilt = _bounded(
                AXIS_NAME_TILT,
                state.tilt_mode,
                state.tilt_min,
                state.tilt_max,
                **shared,
            )
            if tilt is not None:
                constraints.append(tilt)

    if snapshot.weather_override_active and snapshot.weather_override_min_mode:
        constraints.append(
            AxisConstraint(
                axis=AXIS_NAME_POSITION,
                kind=AxisConstraintMode.MIN,
                low=snapshot.weather_override_position,
                high=None,
                source="weather",
                label="weather override",
                priority=WeatherOverrideHandler.priority,
                slot=0,
            )
        )
    return constraints


def bound_label(value: int | None) -> str:
    """Render one side of a bound for a trace reason ('—' when unbounded)."""
    return "—" if value is None else f"{value}%"


def tilt_clamp_step(
    *, from_tilt: int, to_tilt: int, label: str, source: str
) -> DecisionStep:
    """Build the trace step for a tilt clamp.

    Shared by the registry (clamping a tilt the winner already set) and
    ``VenetianPolicy.post_pipeline_resolve`` (clamping the tilt its engine
    resolves after the pipeline). Both clamps are the same event and must read
    the same way in the trace, so the step is built in one place.
    """
    return DecisionStep(
        handler=source,
        matched=True,
        reason_payload=Reason(
            ReasonCode.REGISTRY_TILT_CLAMPED,
            {"from_tilt": from_tilt, "to_tilt": to_tilt, "label": label},
        ),
        position=None,
        tilt=to_tilt,
    )


def constraint_label(constraints: Iterable[AxisConstraint], axis: str) -> str:
    """Name the bounded constraints on *axis* for a trace reason.

    One constraint renders its own label; several render a joined list, so a
    clamp always says which slot(s) produced it.
    """
    labels = [
        c.label
        for c in constraints
        if c.axis == axis and c.kind is not AxisConstraintMode.FIXED
    ]
    # dict.fromkeys de-dupes while preserving order (two bounds, one slot).
    return ", ".join(dict.fromkeys(labels)) or "constraint"
