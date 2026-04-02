"""Wind override handler — safety retraction when wind exceeds threshold (stub)."""

from __future__ import annotations

from ..handler import OverrideHandler
from ..types import PipelineResult, PipelineSnapshot


class WindOverrideHandler(OverrideHandler):
    """Retracts covers when wind speed exceeds threshold.

    Priority 90: between force_override (100) and motion_timeout (80).
    Stub — will be fully implemented when wind sensor configuration is
    added (Issue #28). Add wind_speed and wind_threshold to PipelineSnapshot
    then implement evaluate() here.
    """

    name = "wind"
    priority = 90

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:  # noqa: ARG002
        """Return None until wind configuration is implemented (Issue #28)."""
        return None

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:  # noqa: ARG002
        """Reason when wind override is not active."""
        return "wind override not configured (Issue #28)"
