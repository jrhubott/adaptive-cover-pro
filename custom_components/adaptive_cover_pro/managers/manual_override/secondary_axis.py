"""Secondary-axis manual-override check and the shared threshold formula.

Co-located so the single-source-of-truth threshold helper sits next to both
of its consumers — the position-delta detector
(:mod:`.position_delta`) and the secondary-axis check below — without
creating an import cycle through the manager.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from ...const import POSITION_TOLERANCE_PERCENT


def effective_manual_threshold(user_threshold: int | None) -> int:
    """Resolve the effective manual-override threshold for a delta comparison.

    Floored at ``POSITION_TOLERANCE_PERCENT`` so motor rounding and reporting
    imprecision can't trip false positives even when the user configures
    ``manual_threshold = 0`` or leaves it unset. Both the primary-axis check
    in ``PositionDeltaDetector.detect`` and the secondary-axis check in
    ``SecondaryAxisCheck.evaluate`` delegate here; keeping the two in sync
    via a single helper prevents the formula from drifting (e.g. the day
    ``POSITION_TOLERANCE_PERCENT`` changes).
    """
    return max(
        user_threshold if user_threshold is not None else 0, POSITION_TOLERANCE_PERCENT
    )


@dataclasses.dataclass(frozen=True, slots=True)
class SecondaryAxisResult:
    """Outcome of evaluating a non-primary axis for manual-override drift.

    ``consumed`` short-circuits the position-axis check (caller returns
    immediately). ``is_manual`` triggers ``mark_manual_control`` +
    ``set_last_updated``. ``event_name`` (with ``event_kwargs``) appends a
    record to the diagnostics ring buffer.
    """

    consumed: bool = False
    is_manual: bool = False
    event_name: str | None = None
    event_kwargs: dict[str, Any] | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class SecondaryAxisCheck:
    """Per-cover-type plug for manual-override evaluation on a secondary axis.

    Built once per cover-state-change cycle by ``CoverTypePolicy.secondary_axis_check``.
    Encapsulates the expected value (e.g. tilt resolved by the engine), the
    HA state attribute to read, an optional suppression callback (e.g.
    venetian's motor back-rotate window), and a label that flavours the
    diagnostic event names. The engine calls ``evaluate`` once and dispatches
    on the returned ``SecondaryAxisResult`` — it stays ignorant of which axis
    is being checked.

    ``excursion_match`` (issue #927) is an optional axis-agnostic callback that
    receives the raw PUBLISHED value (not a delta) and reports whether it
    matches an integration-issued excursion the axis owner recorded (e.g.
    venetian's drift-reset endpoint publish). It is consulted BEFORE
    ``suppression`` so a matching publish is recognised and consumed whether or
    not a time-based grace window happens to be open at the same instant.
    """

    expected: int
    attribute: str  # e.g. "current_tilt_position"
    label: str  # e.g. "tilt" — flavours the rejection-reason text
    suppression: Callable[[str, float], bool] | None = None
    # Value-based (not delta-based) predicate over the raw published value;
    # matches an integration-issued excursion so its stale late publish isn't
    # misread as a manual move (issue #927).
    excursion_match: Callable[[str, float], bool] | None = None

    def consume_excursion(self, entity_id: str, new_state) -> None:
        """Consume a matching one-shot excursion stamp under another gate.

        When another gate (e.g. command grace) already suppressed this update,
        pop any matching one-shot excursion stamp anyway so it does not linger
        and later swallow a genuine move to the same value (issue #927).
        """
        if self.excursion_match is None:
            return
        new_value = new_state.attributes.get(self.attribute)
        if new_value is None:
            return
        self.excursion_match(entity_id, new_value)  # one-shot pop on match

    def evaluate(
        self,
        entity_id: str,
        new_state,
        manual_threshold: int | None,
    ) -> SecondaryAxisResult:
        """Decide what (if anything) the secondary axis tells the manager to do."""
        new_value = new_state.attributes.get(self.attribute)
        if new_value is None:
            return SecondaryAxisResult()

        effective_threshold = effective_manual_threshold(manual_threshold)

        # Issue #927: consult the value-based excursion predicate FIRST, before
        # the delta-based suppression grace check. A drift-reset endpoint
        # publish must be recognised and consume its one-shot record even when
        # the command-grace window is simultaneously open — otherwise the grace
        # term short-circuits, the record lingers the full publish-lag window,
        # and a genuine later move to the endpoint value gets swallowed. This
        # matches on the raw published value, so a user move to the mirror value
        # (same delta, different value) is not swallowed.
        if self.excursion_match is not None and self.excursion_match(
            entity_id, new_value
        ):
            return SecondaryAxisResult(
                consumed=True,
                event_name="manual_override_rejected_tilt_suppression",
                event_kwargs={
                    "our_state": self.expected,
                    "new_position": new_value,
                    "effective_threshold": effective_threshold,
                    "reason": (
                        f"{self.label} value {new_value:.0f}% matches an ACP "
                        "drift-reset endpoint excursion; suppressing both tilt "
                        "and position checks"
                    ),
                },
            )

        delta = abs(self.expected - new_value)

        # Check suppression BEFORE the on-target short-circuit. When the motor
        # back-drives the position axis during tilt settling, tilt may arrive
        # exactly on target while the position axis still shows back-drive drift.
        # Returning consumed=False here would let the position-axis check run and
        # falsely trip manual override on that drift.
        if self.suppression is not None and self.suppression(entity_id, delta):
            return SecondaryAxisResult(
                consumed=True,
                event_name="manual_override_rejected_tilt_suppression",
                event_kwargs={
                    "our_state": self.expected,
                    "new_position": new_value,
                    "effective_threshold": effective_threshold,
                    "reason": (
                        f"{self.label} delta {delta:.1f}% within venetian "
                        "back-rotate window; suppressing both tilt and position checks"
                    ),
                },
            )

        if new_value == self.expected:
            return SecondaryAxisResult()

        if delta >= effective_threshold:
            return SecondaryAxisResult(
                consumed=True,
                is_manual=True,
                event_name="manual_override_set",
                event_kwargs={
                    "our_state": self.expected,
                    "new_position": new_value,
                    "effective_threshold": effective_threshold,
                    "reason": (
                        f"{self.label} delta {delta:.1f}% >= threshold {effective_threshold}%"
                    ),
                },
            )

        # Below threshold and not suppressed — preserve the legacy "silent
        # fall-through" behavior so the position-axis check still runs.
        return SecondaryAxisResult()
