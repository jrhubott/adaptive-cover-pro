"""Custom position handler — sensor/template-driven fixed cover positions."""

from __future__ import annotations

from ...const import (
    CUSTOM_POSITION_SAFETY_PRIORITY,
    AxisConstraintMode,
    ControlMethod,
    ReasonCode,
    custom_position_handler_name,
)
from ...reason_i18n import Reason
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
        position: int | None,
        priority: int,
        tilt: int | None = None,
    ) -> None:
        """Create a handler for one custom position slot.

        Args:
            slot:      1-based slot number (1–10).  Used to build ``name``.
            position:  Cover position (0–100 %) to apply when the trigger is on,
                       or None for a constraint-only slot that names no position.
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
    def _trigger_param(state: CustomPositionSensorState) -> object:
        """Describe what activated the slot, as a reason-template param.

        Active sensor entity ids stay scalar strings (never translated); a
        template activation contributes a ``trigger_template`` fragment. When
        both are present the value is a tuple rendered joined by ``", "`` (the
        old force-override reason format). A slot with neither falls back to the
        ``trigger_fallback`` fragment.
        """
        parts: list[object] = list(state.active_entity_ids)
        if state.template_active:
            parts.append(Reason(ReasonCode.FRAGMENT_TRIGGER_TEMPLATE))
        if parts:
            return tuple(parts)
        return Reason(ReasonCode.FRAGMENT_TRIGGER_FALLBACK)

    def _reason_head(self, state: CustomPositionSensorState) -> Reason:
        """Return the leading-clause fragment of the reason (issue #867).

        A configured slot name renders ``"<name> active"`` (the name is a raw
        scalar, never translated); otherwise falls back to today's exact
        ``"custom position #N active (trigger)"`` form. Single source of truth
        for both PipelineResult reason branches below.
        """
        if state.custom_name:
            return Reason(ReasonCode.CUSTOM_HEAD_NAMED, {"name": state.custom_name})
        return Reason(
            ReasonCode.CUSTOM_HEAD_SLOT,
            {"slot": self._slot, "trigger": self._trigger_param(state)},
        )

    def evaluate(self, snapshot: PipelineSnapshot) -> PipelineResult | None:
        """Return the configured position when this slot's trigger is active.

        The handler only claims the position axis when the slot names an
        *exact* position (``position_mode`` is ``FIXED``). Every other mode —
        a floor (``min_mode``, issue #463), a ceiling or range, or no position
        claim at all (issue #943) — defers by returning ``None`` so the
        registry can compose the constraint onto whichever handler actually
        wins. That is what makes a constraint priority-independent.

        The ``use_my`` path is the exception: it is hardware-pinned, ignores
        constraint semantics entirely, and always claims.
        """
        # Find our slot in the snapshot's sensor list.
        for state in snapshot.custom_position_sensors:
            if state.slot == self._slot:
                if state.is_on:
                    # Defer to the axis-constraint composition pass — see
                    # pipeline/axis_constraints.py. Covers today's tilt-only
                    # (#514) and floor (#463) deferrals plus the ceiling /
                    # range / no-claim modes, with identical outcomes for
                    # every pre-#943 configuration.
                    if (
                        state.position_mode is not AxisConstraintMode.FIXED
                        and not state.use_my
                    ):
                        return None
                    raw = compute_raw_calculated_position(snapshot)
                    reason_head = self._reason_head(state)
                    # Issue #767: only the priority-100 safety slot bypasses the
                    # Automatic-Control-OFF gate. Ordinary slots respect the switch.
                    bypass_auto_control = self._is_safety
                    bypass_note: Reason | str = (
                        Reason(ReasonCode.FRAGMENT_BYPASS_NOTE)
                        if bypass_auto_control
                        else ""
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
                            reason_payload=Reason(
                                ReasonCode.CUSTOM_USE_MY,
                                {
                                    "head": reason_head,
                                    "position": pos,
                                    "bypass_note": bypass_note,
                                },
                            ),
                            raw_calculated_position=raw,
                            custom_position_active_slot=self._slot,
                            custom_position_minimum_mode=None,
                            custom_position_active_slot_name=state.slot_name,
                        )
                    # Exact-position branch (state.min_mode is False here —
                    # floor mode defers above). A ``use_my`` slot reaches here
                    # even with no position of its own; when its My value is
                    # also unavailable there is nothing to send, so defer rather
                    # than close the cover with a phantom 0 (audit finding 3).
                    pos = self._position
                    if pos is None:
                        return None
                    return PipelineResult(
                        position=pos,
                        tilt=self._tilt,
                        bypass_auto_control=bypass_auto_control,
                        is_safety=self._is_safety,
                        control_method=ControlMethod.CUSTOM_POSITION,
                        reason_payload=Reason(
                            ReasonCode.CUSTOM_POSITION,
                            {
                                "head": reason_head,
                                "position": pos,
                                "bypass_note": bypass_note,
                            },
                        ),
                        raw_calculated_position=raw,
                        custom_position_active_slot=self._slot,
                        custom_position_minimum_mode=None,
                        custom_position_active_slot_name=state.slot_name,
                    )
                # Slot found but not active — pass through
                return None

        # Slot not found in snapshot — configuration mismatch or not yet loaded
        return None

    def describe_skip(self, snapshot: PipelineSnapshot) -> Reason:  # noqa: ARG002
        """Reason when this slot's trigger is not active."""
        return Reason(ReasonCode.SKIP_CUSTOM_NOT_ACTIVE, {"slot": self._slot})
