"""Custom position handler — sensor/template-driven fixed cover positions."""

from __future__ import annotations

from ...const import (
    CUSTOM_POSITION_SAFETY_PRIORITY,
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

    def _slot_state(
        self, snapshot: PipelineSnapshot
    ) -> CustomPositionSensorState | None:
        """Return this slot's entry in the snapshot, or None when it has none.

        The snapshot's sensor list is compacted (gaps skipped), so the list
        index does not recover the slot — every consumer has to match on
        ``state.slot``. One lookup shared by ``evaluate`` and ``describe_skip``
        so the two can never disagree about which state they are judging.
        """
        for state in snapshot.custom_position_sensors:
            if state.slot == self._slot:
                return state
        return None

    def _scoped_out_of_window(
        self, state: CustomPositionSensorState, snapshot: PipelineSnapshot
    ) -> bool:
        """Whether the user scoped this slot to a window that is currently shut.

        **The one statement of the #1318 gate.** ``evaluate`` and
        ``describe_skip`` both consume it, so the guard and the reason it
        produces cannot drift apart.

        Absent the opt-in this is always False, which is #895's behaviour
        verbatim: that issue is the exact inverse request — a sleep-mode custom
        position that must SURVIVE the end-of-window transition — and its fix
        (``coordinator._pipeline_has_active_override``) is what makes the
        end-of-window default yield to a still-winning slot. Both outcomes have
        to stay reachable, so this is a per-slot choice and never a new default.

        Scoped to the FIXED claim paths (``has_fixed_claim`` — an exact
        position, or ``use_my``). A slot's BOUNDED claims are governed by a
        different key entirely, ``custom_position_outside_window_N`` (#943 item
        B), read by ``axis_constraints._window_eligible``. That is why this sits
        AFTER the constraint-mode deferral rather than before it, departing from
        ``weather._scoped_out_of_window``: weather's min-mode floor drops from
        the same field its handler reads, so gating first is truthful there;
        here a constraint-only slot answers to another setting and must never be
        told a scope it does not obey is what stopped it.

        The priority-100 safety slot is unconditionally exempt (#563). Safety
        semantics ARE "acts outside the time window"; letting a scheduling
        checkbox disarm storm or forced protection would undo the priority.

        Reads ``clock_window_open``, never ``in_time_window``: the latter folds
        in the sun-tracking daytime gate, which is dark mid-clock on a
        gate-configured install (#656/#632). Gating on it would silence the slot
        inside the very window the user scoped it to.
        """
        if self._is_safety:
            return False
        return (
            state.has_fixed_claim
            and state.scope_to_window
            and not snapshot.clock_window_open
        )

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
        wins. That is what makes a constraint priority-independent — against a
        computed winner. Against one *holding* a physical position the slot's
        priority decides whether it may move the cover at all (#1170).

        The ``use_my`` path is the exception: it is hardware-pinned, ignores
        constraint semantics entirely, and always claims. Both paths together
        are :attr:`CustomPositionSensorState.has_fixed_claim`.
        """
        state = self._slot_state(snapshot)
        # No entry for this slot (configuration mismatch or not yet loaded), or
        # the trigger is quiet — pass through either way.
        if state is None or not state.is_on:
            return None
        # Defer to the axis-constraint composition pass — see
        # pipeline/axis_constraints.py. Covers today's tilt-only (#514) and
        # floor (#463) deferrals plus the ceiling / range / no-claim modes,
        # with identical outcomes for every pre-#943 configuration.
        if not state.has_fixed_claim:
            return None
        # Issue #1318: the user scoped this slot's exact-position claim to the
        # clock window and the window is shut. Standing down here is the whole
        # fix — DefaultHandler then wins, so
        # ``coordinator._pipeline_has_active_override`` reads False on its own
        # and the end-of-window position is sent.
        if self._scoped_out_of_window(state, snapshot):
            return None
        raw = compute_raw_calculated_position(snapshot)
        reason_head = self._reason_head(state)
        # Issue #767: only the priority-100 safety slot bypasses the
        # Automatic-Control-OFF gate. Ordinary slots respect the switch.
        bypass_auto_control = self._is_safety
        bypass_note: Reason | str = (
            Reason(ReasonCode.FRAGMENT_BYPASS_NOTE) if bypass_auto_control else ""
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
        # Exact-position branch (state.min_mode is False here — floor mode
        # defers above). A ``use_my`` slot reaches here even with no position
        # of its own; when its My value is also unavailable there is nothing to
        # send, so defer rather than close the cover with a phantom 0 (audit
        # finding 3).
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

    def describe_skip(self, snapshot: PipelineSnapshot) -> Reason:
        """Reason when this slot did not claim the position axis.

        Ordered to match ``evaluate``, so the trace names the gate that
        actually stopped it: the window scope is reported only once the
        trigger check has passed, which keeps a user whose sensor is simply
        off reading "not active" instead of being pointed at a scope that is
        not what stopped the slot (``weather.describe_skip``'s principle).

        A constraint-only slot never reaches the scope branch — the predicate
        answers False for it — so its trace keeps today's wording and its user
        is not pointed at a setting that does not govern their bounds.
        """
        state = self._slot_state(snapshot)
        if (
            state is not None
            and state.is_on
            and self._scoped_out_of_window(state, snapshot)
        ):
            return Reason(ReasonCode.SKIP_OUTSIDE_WINDOW)
        return Reason(ReasonCode.SKIP_CUSTOM_NOT_ACTIVE, {"slot": self._slot})
