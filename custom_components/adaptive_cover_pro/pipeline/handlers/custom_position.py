"""Custom position handler — sensor-driven fixed cover positions."""

from __future__ import annotations

from ...const import ControlMethod
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
    :class:`CustomPositionSensorState` entries).  If the sensor is
    ``is_on=True`` it claims the position; otherwise it passes through.
    """

    def __init__(
        self,
        slot: int,
        entity_id: str,
        position: int,
        priority: int,
        tilt: int | None = None,
    ) -> None:
        """Create a handler for one custom position slot.

        Args:
            slot:      1-based slot number (1–4).  Used to build ``name``.
            entity_id: Binary sensor entity ID that activates this position.
            position:  Cover position (0–100 %) to apply when the sensor is on.
            priority:  Pipeline evaluation priority (1–99).  Higher = evaluated first.
            tilt:      Explicit tilt (0–100 %) for venetian covers. None = solar tilt.

        """
        self._slot = slot
        self._entity_id = entity_id
        self._position = position
        self._tilt = tilt
        self.priority = priority  # instance attribute overrides any class-level default
        # min_mode is read from the snapshot at evaluate() time, not stored here,
        # since snapshot is the single source of truth for per-cycle config.

    @property
    def name(self) -> str:  # type: ignore[override]
        """Handler name includes the slot number for clear decision-trace output."""
        return f"custom_position_{self._slot}"

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return the configured position when this slot's sensor is active.

        In ``min_mode`` (and not on the ``use_my`` path), the handler defers
        by returning ``None``. The registry then composes the configured
        position as a post-decision floor clamp on whichever lower-priority
        handler wins (issue #463).
        """
        # Find our sensor in the snapshot's sensor list by entity_id.
        for state in snapshot.custom_position_sensors:
            if state.entity_id == self._entity_id:
                if state.is_on:
                    # Floor mode (without use_my) defers to the floor-clamp
                    # composition pass — see pipeline/floors.py.
                    if state.min_mode and not state.use_my:
                        return None
                    raw = compute_raw_calculated_position(snapshot)
                    # "Use My" path: route through the cover's hardware-stored My preset.
                    # my_position_value acts as both the target and the reason annotation.
                    # min_mode is ignored — My is hardware-pinned; floor semantics don't apply.
                    if state.use_my and snapshot.my_position_value is not None:
                        pos = snapshot.my_position_value
                        return PipelineResult(
                            position=pos,
                            tilt=self._tilt,
                            use_my_position=True,
                            bypass_auto_control=True,
                            control_method=ControlMethod.CUSTOM_POSITION,
                            reason=(
                                f"custom position #{self._slot} active ({self._entity_id})"
                                f" — use My position ({pos}%)"
                                " [bypasses automatic control]"
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
                        bypass_auto_control=True,
                        control_method=ControlMethod.CUSTOM_POSITION,
                        reason=(
                            f"custom position #{self._slot} active ({self._entity_id})"
                            f" — position {pos}%"
                            " [bypasses automatic control]"
                        ),
                        raw_calculated_position=raw,
                        custom_position_active_slot=self._slot,
                        custom_position_minimum_mode=None,
                        custom_position_active_slot_name=state.sensor_name,
                    )
                # Sensor found but not active — pass through
                return None

        # Sensor not found in snapshot — configuration mismatch or not yet loaded
        return None

    def describe_skip(self, snapshot: PipelineSnapshot) -> str:  # noqa: ARG002
        """Reason when this slot's sensor is not active."""
        return f"custom position #{self._slot} sensor not active ({self._entity_id})"
