"""Solar handler — sun-based position when direct sun is valid."""

from __future__ import annotations

from ...const import ControlMethod, ReasonCode
from ...reason_i18n import Reason
from ..handler import OverrideHandler
from ..helpers import anticipated_solar_position
from ..types import PipelineResult, PipelineSnapshot


class SolarHandler(OverrideHandler):
    """Return the sun-calculated position when direct sun is in the FOV.

    Priority 40 — lower than climate, higher than default.
    Activates when the sun is within the cover's field of view and within
    configured elevation limits. Computes position from the calculation
    engine and applies configured position limits.
    """

    name = "solar"
    priority = 40

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return calculated position when direct sun is valid."""
        if not snapshot.in_time_window:
            return None
        # Sun tracking off — the static toggle, the sun-tracking gate, or both
        # (issue #1167). They fold into this one field at snapshot-build time, so
        # there is exactly one place that means "tracking is not live right now"
        # and exactly one place that reads it. Declining here (rather than
        # omitting the handler from the pipeline) is what lets the chain fall
        # through with a decision-trace entry explaining why.
        if not snapshot.enable_sun_tracking:
            return None
        if not snapshot.cover.direct_sun_valid:
            return None

        position = anticipated_solar_position(snapshot)
        suffix: Reason | str = ""
        if getattr(snapshot, "minimize_movements", False):
            steps = getattr(snapshot, "max_coverage_steps", 1)
            suffix = Reason(ReasonCode.FRAGMENT_COVERAGE_STEP, {"steps": steps})
        return PipelineResult(
            position=position,
            control_method=ControlMethod.SOLAR,
            reason_payload=Reason(
                ReasonCode.SOLAR_TRACKING, {"position": position, "suffix": suffix}
            ),
            raw_calculated_position=position,
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> Reason:
        """Reason when solar handler does not match.

        Ordered to match ``evaluate``: the first gate that actually stopped it is
        the one reported, so "outside the window" outranks "tracking is not
        live", which in turn outranks "the sun is not on this face".

        Tracking-not-live splits by cause. Reporting a closed gate to a user who
        simply switched sun tracking off — and never configured a gate — would
        send them looking for a setting that does not exist, so the gate reason
        is used only when a gate is what actually closed it.
        """
        if not snapshot.in_time_window:
            return Reason(ReasonCode.SKIP_OUTSIDE_WINDOW)
        if not snapshot.enable_sun_tracking:
            return Reason(
                ReasonCode.SKIP_SUN_TRACKING_GATE
                if snapshot.sun_tracking_gate_closed
                else ReasonCode.SKIP_SUN_TRACKING_OFF
            )
        return Reason(ReasonCode.SKIP_SUN_OUTSIDE)
