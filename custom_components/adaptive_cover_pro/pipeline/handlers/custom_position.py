"""Custom position handler — sensor/template-driven fixed cover positions."""

from __future__ import annotations

from ...const import (
    CUSTOM_POSITION_SAFETY_PRIORITY,
    ControlMethod,
    custom_position_handler_name,
)
from ..handler import OverrideHandler
from ..helpers import compute_raw_calculated_position
from ..types import CustomPositionSensorState, PipelineResult, PipelineSnapshot


class CustomPositionHandler(OverrideHandler):
    """Return a configured position when this slot's trigger is active.

    One instance is created per configured custom position slot (up to 10).
    Each instance carries its own target position and pipeline priority so
    the PipelineRegistry can sort them correctly relative to all other
    handlers.

    A slot's trigger is the OR of its bound binary sensors, optionally folded
    with a condition template (issue #563); the snapshot builder evaluates
    that into ``CustomPositionSensorState.is_on`` so this handler stays pure.

    Priority is configurable (1–100, default 77) so users can choose where in
    the decision chain each custom position activates:
    - Priority 100   → safety: full force-override semantics (acts outside the
                       time window, bypasses delta gates)
    - Priority > 80  → overrides manual override too
    - Priority 77    → default: between manual override (80) and motion timeout (75)
    - Priority < 40  → evaluated after solar tracking

    The handler matches by looking up its slot number in
    ``snapshot.custom_position_sensors`` (a list of
    :class:`CustomPositionSensorState` entries).  If the slot is
    ``is_on=True`` it claims the position; otherwise it passes through.
    """

    def __init__(
        self,
        slot: int,
        position: int,
        priority: int,
        tilt: int | None = None,
    ) -> None:
        """Create a handler for one custom position slot.

        Args:
            slot:      1-based slot number (1–10).  Used to build ``name``.
            position:  Cover position (0–100 %) to apply when the trigger is on.
            priority:  Pipeline evaluation priority (1–100).  Higher = evaluated first.
            tilt:      Explicit tilt (0–100 %) for venetian covers. None = solar tilt.

        """
        self._slot = slot
        self._position = position
        self._tilt = tilt
        self.priority = priority  # instance attribute overrides any class-level default
        # min_mode is read from the snapshot at evaluate() time, not stored here,
        # since snapshot is the single source of truth for per-cycle config.

    @property
    def name(self) -> str:  # type: ignore[override]
        """Handler name includes the slot number for clear decision-trace output."""
        return custom_position_handler_name(self._slot)

    @property
    def _is_safety(self) -> bool:
        """True when this slot inherits force-override safety semantics."""
        return self.priority >= CUSTOM_POSITION_SAFETY_PRIORITY

    @staticmethod
    def _trigger_label(state: CustomPositionSensorState) -> str:
        """Describe what activated the slot, for reason strings.

        Active sensors are joined like the old force-override reason; a
        template-only activation reads ``template``.
        """
        parts = list(state.active_entity_ids)
        if state.template_active:
            parts.append("template")
        return ", ".join(parts) if parts else "trigger"

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return the configured position when this slot's trigger is active.

        In ``min_mode`` (and not on the ``use_my`` path), the handler defers
        by returning ``None``. The registry then composes the configured
        position as a post-decision floor clamp on whichever lower-priority
        handler wins (issue #463).
        """
        # Find our slot in the snapshot's sensor list.
        for state in snapshot.custom_position_sensors:
            if state.slot == self._slot:
                if state.is_on:
                    # Tilt-only mode defers to the tilt-axis overlay pass — the
                    # slot fixes the slat angle but never claims position
                    # (issue #514). See pipeline/tilt_axis.py.
                    if state.tilt_only:
                        return None
                    # Floor mode (without use_my) defers to the floor-clamp
                    # composition pass — see pipeline/floors.py.
                    if state.min_mode and not state.use_my:
                        return None
                    raw = compute_raw_calculated_position(snapshot)
                    trigger = self._trigger_label(state)
                    # Issue #767: only the priority-100 safety slot bypasses the
                    # Automatic-Control-OFF gate. Ordinary slots respect the switch.
                    bypass_auto_control = self._is_safety
                    bypass_note = (
                        " [bypasses automatic control]" if bypass_auto_control else ""
                    )
                    # "Use My" path: route through the cover's hardware-stored My preset.
                    # my_position_value acts as both the target and the reason annotation.
                    # min_mode is ignored — My is hardware-pinned; floor semantics don't apply.
                    if state.use_my and snapshot.my_position_value is not None:
                        pos = snapshot.my_position_value
                        return PipelineResult(
                            position=pos,
                            tilt=self._tilt,
                            use_my_position=True,
                            bypass_auto_control=bypass_auto_control,
                            is_safety=self._is_safety,
                            control_method=ControlMethod.CUSTOM_POSITION,
                            reason=(
                                f"custom position #{self._slot} active ({trigger})"
                                f" — use My position ({pos}%)"
                                f"{bypass_note}"
                            ),
                            raw_calculated_position=raw,
                            custom_position_active_slot=self._slot,
                            custom_position_minimum_mode=None,
                            custom_position_active_slot_name=state.sensor_name,
                        )
                    # Exact-position branch (state.min_mode is False here —
                    # floor mode defers above).
                    pos = self._position
                    return PipelineResult(
                        position=pos,
                        tilt=self._tilt,
                        bypass_auto_control=bypass_auto_control,
                        is_safety=self._is_safety,
                        control_method=ControlMethod.CUSTOM_POSITION,
                        reason=(
                            f"custom position #{self._slot} active ({trigger})"
                            f" — position {pos}%"
                            f"{bypass_note}"
                        ),
                        raw_calculated_position=raw,
                        custom_position_active_slot=self._slot,
                        custom_position_minimum_mode=None,
                        custom_position_active_slot_name=state.sensor_name,
                    )
                # Slot found but not active — pass through
                return None

        # Slot not found in snapshot — configuration mismatch or not yet loaded
        return None

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:  # noqa: ARG002
        """Reason when this slot's trigger is not active."""
        return f"custom position #{self._slot} not active"
