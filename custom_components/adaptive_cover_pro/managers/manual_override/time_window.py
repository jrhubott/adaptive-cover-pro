"""Time-window manual-override detector — a pure time-based alternative.

During the window after an ACP command, every change is ignored. Once the
window expires, the next position change away from the commanded state is a
manual override. No delta threshold and no origin/context inspection — purely
time-gated. Opt-in; ``PositionDeltaDetector`` remains the default.

The window length reuses ``CONF_TRANSIT_TIMEOUT`` (default 45 s) — the option
that already represents "how long after a command the cover may still be
settling." The engine stamps the last-command time and supplies
``seconds_since_command`` on the context.
"""

from __future__ import annotations

from typing import ClassVar

from ...const import DEFAULT_TRANSIT_TIMEOUT_SECONDS
from .detector import (
    DetectionContext,
    DetectorConfig,
    OverrideDecision,
    OverrideDetector,
    position_unavailable_decision,
)


class TimeWindowDetector(OverrideDetector):
    """Treat any movement outside the post-command window as a manual override."""

    strategy_id: ClassVar[str] = "time_window"

    def __init__(
        self, window_seconds: float = float(DEFAULT_TRANSIT_TIMEOUT_SECONDS)
    ) -> None:
        """Initialise with the post-command settle window in seconds."""
        self._window_seconds = window_seconds

    @classmethod
    def from_config(cls, config: DetectorConfig) -> TimeWindowDetector:
        """Build from config, taking the window from ``command_window_seconds``."""
        return cls(window_seconds=config.command_window_seconds)

    def update_config(self, config: DetectorConfig) -> None:
        """Apply a window change without a reload."""
        self._window_seconds = config.command_window_seconds

    def detect(self, context: DetectionContext) -> OverrideDecision:
        """Decide manual override purely from time since the last ACP command."""
        if context.new_position is None:
            return position_unavailable_decision(context)

        elapsed = context.seconds_since_command
        if elapsed is not None and elapsed < self._window_seconds:
            return OverrideDecision(
                event_name="manual_override_rejected_command_window",
                event_kwargs={
                    "our_state": context.our_state,
                    "new_position": context.new_position,
                    "reason": (
                        f"within {self._window_seconds:.0f}s command window "
                        f"({elapsed:.1f}s elapsed)"
                    ),
                },
            )

        if context.new_position == context.our_state:
            return OverrideDecision()

        return OverrideDecision(
            mark_manual=True,
            event_name="manual_override_set",
            event_kwargs={
                "our_state": context.our_state,
                "new_position": context.new_position,
                "reason": "movement after command window",
            },
        )
