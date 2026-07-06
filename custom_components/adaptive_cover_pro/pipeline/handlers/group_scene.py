"""Group scene handler — a cover-group's scene intent claims the position.

Issue #790 Phase 2. Priority 85 (``GROUP_SCENE_PRIORITY``): above manual
override (80) so "put the room in Privacy" wins over a stale per-cover
manual state, below weather (90) so wind/rain safety on an outdoor member
still overrides a group scene. Not user-overridable.
"""

from __future__ import annotations

from ...const import GROUP_SCENE_PRIORITY, ControlMethod, GroupIntentKind
from ...cover_types import get_policy
from ..handler import OverrideHandler
from ..helpers import compute_raw_calculated_position
from ..types import PipelineResult, PipelineSnapshot


class GroupSceneHandler(OverrideHandler):
    """Apply a live cover-group scene intent, resolved per member policy.

    ``snapshot.group_intent`` is ``None`` for non-members — absence of an
    intent IS non-membership, so no roster lookup happens here. The scene is
    an intent, not an absolute position: the member's own policy maps it
    (``position_for_scene``), which is what makes mixed groups work.
    """

    name = "group_scene"
    priority = GROUP_SCENE_PRIORITY

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return the policy-resolved scene position when a scene intent is live."""
        intent = snapshot.group_intent
        if intent is None or intent.kind is not GroupIntentKind.SCENE:
            return None
        policy = snapshot.policy or get_policy(snapshot.cover_type)
        position = policy.position_for_scene(intent.scene)
        return PipelineResult(
            position=position,
            control_method=ControlMethod.GROUP_SCENE,
            reason=(
                f"group scene '{intent.scene}' from group {intent.group_id}"
                f" → {position}%"
            ),
            raw_calculated_position=compute_raw_calculated_position(snapshot),
            # Explicit user intent: runs even with automatic control off —
            # same semantics as custom-position slots. NOT a safety result.
            bypass_auto_control=True,
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:
        """Reason when no scene intent is live."""
        if snapshot.group_intent is not None:
            return "group intent is a lock, not a scene"
        return "no group scene intent"
