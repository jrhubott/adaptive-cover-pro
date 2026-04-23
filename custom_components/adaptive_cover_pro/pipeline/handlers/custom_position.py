"""Custom position handler — sensor-driven fixed cover positions."""

from __future__ import annotations

from ...enums import ControlMethod
from ..handler import OverrideHandler
from ..helpers import compute_raw_calculated_position
from ..types import PipelineResult, PipelineSnapshot


class CustomPositionHandler(OverrideHandler):
    """Return a configured position when this slot's binary sensor is active.

    One instance is created per configured custom position slot (up to 4).
    Each instance carries its own sensor entity_id, target position, and
    pipeline priority so the PipelineRegistry can sort them correctly relative
    to all other handlers.

    Priority is configurable (1–99, default 77) so users can choose where in
    the decision chain each custom position activates:
    - Priority > 80  → overrides manual override too
    - Priority 77    → default: between manual override (80) and motion timeout (75)
    - Priority < 40  → evaluated after solar tracking

    The handler matches by looking up its sensor entity_id in
    ``snapshot.custom_position_sensors`` (a list of
    ``(entity_id, is_on, position, priority)`` tuples).  If the sensor is
    ``is_on=True`` it claims the position; otherwise it passes through.
    """

    def __init__(self, slot: int, entity_id: str, position: int, priority: int) -> None:
        """Create a handler for one custom position slot.

        Args:
            slot:      1-based slot number (1–4).  Used to build ``name``.
            entity_id: Binary sensor entity ID that activates this position.
            position:  Cover position (0–100 %) to apply when the sensor is on.
            priority:  Pipeline evaluation priority (1–99).  Higher = evaluated first.

        """
        self._slot = slot
        self._entity_id = entity_id
        self._position = position
        self.priority = priority  # instance attribute overrides any class-level default
        # min_mode is read from the snapshot tuple at evaluate() time, not stored here,
        # since snapshot is the single source of truth for per-cycle config.

    @property
    def name(self) -> str:  # type: ignore[override]
        """Handler name includes the slot number for clear decision-trace output."""
        return f"custom_position_{self._slot}"

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return the configured position when this slot's sensor is active."""
        # Find our sensor in the snapshot's sensor list by entity_id.
        for (
            entity_id,
            is_on,
            _position,
            _priority,
            min_mode,
            use_my,
        ) in snapshot.custom_position_sensors:
            if entity_id == self._entity_id:
                if is_on:
                    raw = compute_raw_calculated_position(snapshot)
                    # "Use My" path: route through the cover's hardware-stored My preset.
                    # my_position_value acts as both the target and the reason annotation.
                    # min_mode is ignored — My is hardware-pinned; floor semantics don't apply.
                    if use_my and snapshot.my_position_value is not None:
                        pos = snapshot.my_position_value
                        return PipelineResult(
                            position=pos,
                            use_my_position=True,
                            bypass_auto_control=True,
                            control_method=ControlMethod.CUSTOM_POSITION,
                            reason=(
                                f"custom position #{self._slot} active ({self._entity_id})"
                                f" — use My position ({pos}%)"
                                " [bypasses automatic control]"
                            ),
                            raw_calculated_position=raw,
                        )
                    if min_mode:
                        pos = max(self._position, raw)
                        mode_note = f" [minimum mode — floor {self._position}%, calculated {raw}%]"
                    else:
                        pos = self._position
                        mode_note = ""
                    return PipelineResult(
                        position=pos,
                        bypass_auto_control=True,
                        control_method=ControlMethod.CUSTOM_POSITION,
                        reason=(
                            f"custom position #{self._slot} active ({self._entity_id})"
                            f" — position {pos}%{mode_note}"
                            " [bypasses automatic control]"
                        ),
                        raw_calculated_position=raw,
                    )
                # Sensor found but not active — pass through
                return None

        # Sensor not found in snapshot — configuration mismatch or not yet loaded
        return None

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:  # noqa: ARG002
        """Reason when this slot's sensor is not active."""
        return f"custom position #{self._slot} sensor not active ({self._entity_id})"
