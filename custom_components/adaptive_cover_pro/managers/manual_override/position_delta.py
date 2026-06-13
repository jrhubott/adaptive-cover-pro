"""Position-delta manual-override detector — the default strategy.

Holds the original detection logic: a state change is a manual override when
the cover's reported primary-axis position differs from the commanded position
by more than the effective threshold, after guarding against the absence of a
recorded command target (Issue #546), transient states, in-transit reports,
and slow-bus publish-lag (Issue #33). The engine handles the upstream gates
(tracked / wait-for-target / command-grace / secondary axis) and resolves
``new_position`` before calling :meth:`detect`.
"""

from __future__ import annotations

from typing import ClassVar

from .detector import DetectionContext, OverrideDecision, OverrideDetector
from .secondary_axis import effective_manual_threshold


class PositionDeltaDetector(OverrideDetector):
    """Threshold-on-position-delta detection (the historical default)."""

    strategy_id: ClassVar[str] = "position_delta"

    def detect(self, context: DetectionContext) -> OverrideDecision:
        """Decide manual override from the primary-axis position delta."""
        entity_id = context.entity_id
        our_state = context.our_state
        new_position = context.new_position

        # Issue #546: no command was ever sent for this entity, so ``our_state``
        # is the pipeline's theoretical default rather than a commanded
        # position. Comparing the cover's real resting position against it is
        # meaningless and produced a false override when the cover sat at its
        # sunset position while the pipeline default diverged. The legitimate
        # user-move signals (HA-context fast-path, stop-to-My) never route
        # through ``detect``, so suppressing here cannot mask a real user touch.
        if not context.has_recorded_target:
            return OverrideDecision(
                event_name="manual_override_rejected_no_command_target",
                event_kwargs={
                    "our_state": our_state,
                    "new_position": new_position,
                    "reason": "no recorded command target (our_state is pipeline default)",
                },
            )

        # Position still unavailable (entity in transient state like "opening")
        # — nothing to compare against, skip override detection.
        if new_position is None:
            return OverrideDecision(
                event_name="manual_override_rejected_position_unavailable",
                event_kwargs={
                    "our_state": our_state,
                    "new_position": None,
                    "reason": "position unavailable (transient state)",
                },
            )

        # Cover's own state attribute says it's still in transit. The
        # current_position it just reported can lag the actual physical
        # position — Zigbee covers that emit a single end-of-move report
        # look like a stale-position event with state=closing/opening.
        # Wait for the next event when the cover stops; that event runs
        # the full position-math path.
        if context.is_in_transit(entity_id):
            new_state_str = getattr(context.new_state, "state", "unknown")
            return OverrideDecision(
                event_name="manual_override_rejected_in_transit",
                event_kwargs={
                    "our_state": our_state,
                    "new_position": new_position,
                    "reason": f"cover state '{new_state_str}' indicates in-transit",
                },
            )

        # Issue #33 Phase 5: cross-axis publish-lag guard. Slow-bus
        # actuators (Somfy IO via Tahoma, slow KNX, Fibaro/Shelly republish)
        # publish a late ``current_position`` tens of seconds after the
        # cover has physically stopped. Without this guard the threshold
        # check below would treat the stale publish as a 100 % user touch.
        # ``CoverTypePolicy.primary_axis_suppression`` defaults to False on
        # non-venetian cover types — they don't share the back-rotate
        # signature and so opt out automatically.
        delta = abs(our_state - new_position)
        if context.policy.primary_axis_suppression(entity_id, float(delta)):
            return OverrideDecision(
                event_name="manual_override_rejected_primary_axis_suppression",
                event_kwargs={
                    "our_state": our_state,
                    "new_position": new_position,
                    "effective_threshold": None,
                    "reason": (
                        f"primary-axis publish-lag suppression for {entity_id} "
                        f"(delta {delta:.1f}%)"
                    ),
                },
                record_primary_axis_suppression=True,
                suppression_delta=float(delta),
            )

        if new_position == our_state:
            return OverrideDecision()

        # Floor the threshold at POSITION_TOLERANCE_PERCENT so motor rounding
        # / position-reporting imprecision can't trip false positives even
        # when the user leaves manual_threshold unset. See
        # ``effective_manual_threshold`` for the single-source-of-truth.
        effective_threshold = effective_manual_threshold(context.manual_threshold)
        if abs(our_state - new_position) <= effective_threshold:
            return OverrideDecision(
                event_name="manual_override_rejected_within_threshold",
                event_kwargs={
                    "our_state": our_state,
                    "new_position": new_position,
                    "effective_threshold": effective_threshold,
                    "reason": (
                        f"delta {abs(our_state - new_position):.1f}% "
                        f"< threshold {effective_threshold}%"
                    ),
                },
            )

        return OverrideDecision(
            mark_manual=True,
            event_name="manual_override_set",
            event_kwargs={
                "our_state": our_state,
                "new_position": new_position,
                "effective_threshold": effective_threshold,
                "reason": (
                    f"delta {abs(our_state - new_position):.1f}% "
                    f">= threshold {effective_threshold}%"
                ),
            },
        )
