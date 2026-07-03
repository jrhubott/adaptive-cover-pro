"""Group lock handler — a cover-group lock freezes the member in place.

Issue #790 Phase 2. Priority 100 (``CUSTOM_POSITION_SAFETY_PRIORITY``): the
lock outranks everything — including weather — matching the shipped
semantics of a member's own safety slot. On a 100-tie the member's own
custom-position safety slot wins: it is built earlier in
``HANDLER_FACTORIES`` and the registry's priority sort is stable, so
physical safety local to the cover trumps a zone lock.
"""

from __future__ import annotations

from ...const import CUSTOM_POSITION_SAFETY_PRIORITY, ControlMethod, GroupIntentKind
from ..handler import OverrideHandler
from ..helpers import compute_raw_calculated_position
from ..types import PipelineResult, PipelineSnapshot


class GroupLockHandler(OverrideHandler):
    """Freeze the cover while a group lock intent is live.

    Reuses the motion-hold shape: the result wins the pipeline (so nothing
    else can move the cover) and carries ``skip_command=True`` (so nothing is
    sent) — the cover holds whatever position it is in, with no per-entity
    target bookkeeping. Deliberately NOT ``is_safety``: no command is emitted,
    so the safety-target machinery has nothing to track.
    """

    name = "group_lock"
    priority = CUSTOM_POSITION_SAFETY_PRIORITY

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Hold the current position while a lock intent is live."""
        intent = snapshot.group_intent
        if intent is None or intent.kind is not GroupIntentKind.LOCK:
            return None
        held = snapshot.current_cover_position
        position = held if held is not None else snapshot.default_position
        return PipelineResult(
            position=position,
            control_method=ControlMethod.GROUP_LOCK,
            reason=(f"group lock from group {intent.group_id} — holding {position}%"),
            raw_calculated_position=compute_raw_calculated_position(snapshot),
            held_position=held,
            skip_command=True,
            bypass_auto_control=True,
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:
        """Reason when no lock intent is live."""
        if snapshot.group_intent is not None:
            return "group intent is a scene, not a lock"
        return "no group lock intent"
