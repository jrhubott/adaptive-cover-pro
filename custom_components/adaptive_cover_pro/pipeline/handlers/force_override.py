"""Force override handler — highest priority safety override."""

from __future__ import annotations

from ...enums import ControlMethod
from ..handler import OverrideHandler
from ..types import PipelineResult, PipelineSnapshot


class ForceOverrideHandler(OverrideHandler):
    """Return the force-override position when any safety sensor is active.

    Priority 100 — evaluated before all other handlers.
    Evaluates the raw sensor states from snapshot.force_override_sensors
    directly; any sensor in the "on" state activates the override.
    """

    name = "force_override"
    priority = 100

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return override position when any force override sensor is on."""
        if not snapshot.force_override_sensors:
            return None
        if not any(snapshot.force_override_sensors.values()):
            return None
        active = [e for e, on in snapshot.force_override_sensors.items() if on]
        pos = snapshot.force_override_position
        return PipelineResult(
            position=pos,
            control_method=ControlMethod.FORCE,
            reason=f"force override active ({', '.join(active)}) — position {pos}% [bypasses automatic control]",
            bypass_auto_control=True,
        )

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:  # noqa: ARG002
        """Reason when force override is not active."""
        return "force override not active"
