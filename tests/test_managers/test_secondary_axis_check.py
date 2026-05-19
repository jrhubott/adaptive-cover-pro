"""Unit tests for ``SecondaryAxisCheck.evaluate``.

The value object encapsulates the per-axis manual-override decision so
``AdaptiveCoverManager.handle_state_change`` can stay generic. These tests
pin the four decision branches: no-op, suppressed, manual, below-threshold.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import POSITION_TOLERANCE_PERCENT
from custom_components.adaptive_cover_pro.managers.manual_override import (
    SecondaryAxisCheck,
    effective_manual_threshold,
)


@pytest.mark.unit
class TestEffectiveManualThreshold:
    """Single-source-of-truth for the manual-override threshold floor.

    Two callers (``handle_state_change`` and ``SecondaryAxisCheck.evaluate``)
    delegate here; pinning the contract prevents the formula from drifting
    across the two sites the next time the floor changes.
    """

    def test_none_returns_floor(self):
        assert effective_manual_threshold(None) == POSITION_TOLERANCE_PERCENT

    def test_zero_returns_floor(self):
        assert effective_manual_threshold(0) == POSITION_TOLERANCE_PERCENT

    def test_below_floor_returns_floor(self):
        assert (
            effective_manual_threshold(POSITION_TOLERANCE_PERCENT - 1)
            == POSITION_TOLERANCE_PERCENT
        )

    def test_at_floor_returns_floor(self):
        assert (
            effective_manual_threshold(POSITION_TOLERANCE_PERCENT)
            == POSITION_TOLERANCE_PERCENT
        )

    def test_above_floor_returns_user_value(self):
        assert (
            effective_manual_threshold(POSITION_TOLERANCE_PERCENT + 7)
            == POSITION_TOLERANCE_PERCENT + 7
        )


def _state(attrs: dict):
    s = MagicMock()
    s.attributes = attrs
    return s


def _check(*, expected: int = 70, suppressed: bool = False) -> SecondaryAxisCheck:
    return SecondaryAxisCheck(
        expected=expected,
        attribute="current_tilt_position",
        label="tilt",
        suppression=(
            (lambda _eid, _delta: suppressed) if suppressed is not None else None
        ),
    )


@pytest.mark.unit
class TestNoOpPaths:
    """Inputs where the secondary-axis check produces no record and no manual."""

    def test_attribute_missing_is_noop(self):
        res = _check().evaluate("cover.x", _state({}), manual_threshold=5)
        assert res.consumed is False
        assert res.is_manual is False
        assert res.event_name is None

    def test_axis_on_target_is_noop(self):
        res = _check(expected=70).evaluate(
            "cover.x", _state({"current_tilt_position": 70}), manual_threshold=5
        )
        assert res.consumed is False
        assert res.is_manual is False
        assert res.event_name is None

    def test_below_threshold_is_silent_passthrough(self):
        res = _check(expected=70, suppressed=False).evaluate(
            "cover.x", _state({"current_tilt_position": 72}), manual_threshold=5
        )
        # Delta of 2 is below the effective threshold (max(5, POSITION_TOLERANCE_PERCENT))
        assert res.consumed is False
        assert res.is_manual is False
        assert res.event_name is None


@pytest.mark.unit
class TestSuppressed:
    """Suppression predicate decides whether back-rotate drift is consumed.

    The predicate is the venetian-side seam — it knows both the back-rotate
    window and the delta cap. Small deltas inside the window return True
    (consumed). Large deltas during the window return False (fall through to
    the numeric path which records the user's move).
    """

    def test_suppressed_consumes_both_axes(self):
        # Predicate returns True (small delta inside window) — back-drive is
        # suppressed; both tilt AND position checks are short-circuited.
        res = _check(expected=70, suppressed=True).evaluate(
            "cover.x", _state({"current_tilt_position": 68}), manual_threshold=5
        )
        assert res.consumed is True  # blocks position-axis fall-through
        assert res.is_manual is False
        assert res.event_name == "manual_override_rejected_tilt_suppression"
        assert res.event_kwargs["our_state"] == 70
        assert res.event_kwargs["new_position"] == 68

    def test_predicate_rejects_falls_through_to_numeric_path(self):
        # Predicate returns False (delta cap exceeded inside window). The common
        # manager must continue past the suppression branch and the existing
        # numeric path records the user's move as `manual_override_set`.
        res = _check(expected=70, suppressed=False).evaluate(
            "cover.x", _state({"current_tilt_position": 20}), manual_threshold=5
        )
        assert res.consumed is True
        assert res.is_manual is True
        assert res.event_name == "manual_override_set"
        assert res.event_kwargs["our_state"] == 70
        assert res.event_kwargs["new_position"] == 20
        # The numeric path's reason text wins — no "back-rotate" wording.
        assert res.event_kwargs["reason"].startswith("tilt delta 50.0% >= threshold")

    def test_predicate_called_with_entity_and_delta(self):
        # Verify the widened signature is actually used.
        seen: list[tuple[str, float]] = []

        def _predicate(entity_id: str, delta: float) -> bool:
            seen.append((entity_id, delta))
            return False

        check = SecondaryAxisCheck(
            expected=70,
            attribute="current_tilt_position",
            label="tilt",
            suppression=_predicate,
        )
        check.evaluate(
            "cover.kitchen", _state({"current_tilt_position": 30}), manual_threshold=5
        )
        assert seen == [("cover.kitchen", 40.0)]


@pytest.mark.unit
class TestOnTargetWithSuppression:
    """On-target tilt inside the suppression window must still consume the check.

    Regression for issue #33: motor back-drives the position axis while tilt is
    settling. If evaluate() short-circuits on `new_value == expected` without
    consulting suppression, the position-axis fall-through trips a false manual
    override on motor back-drift (e.g. commanded 34%, motor settles at 37%).
    """

    def test_on_target_inside_suppression_consumes_position_check(self):
        res = _check(expected=70, suppressed=True).evaluate(
            "cover.x", _state({"current_tilt_position": 70}), manual_threshold=5
        )
        assert res.consumed is True
        assert res.is_manual is False
        assert res.event_name == "manual_override_rejected_tilt_suppression"

    def test_on_target_outside_suppression_is_noop(self):
        res = _check(expected=70, suppressed=False).evaluate(
            "cover.x", _state({"current_tilt_position": 70}), manual_threshold=5
        )
        assert res.consumed is False
        assert res.is_manual is False
        assert res.event_name is None


@pytest.mark.unit
class TestManual:
    """Above-threshold drift outside the suppression window flips manual."""

    def test_above_threshold_outside_suppression_is_manual(self):
        res = _check(expected=70, suppressed=False).evaluate(
            "cover.x", _state({"current_tilt_position": 20}), manual_threshold=5
        )
        assert res.consumed is True
        assert res.is_manual is True
        assert res.event_name == "manual_override_set"
        assert res.event_kwargs["our_state"] == 70
        assert res.event_kwargs["new_position"] == 20

    def test_threshold_floor_uses_position_tolerance(self):
        # When the user manual_threshold is below POSITION_TOLERANCE_PERCENT,
        # the floor (POSITION_TOLERANCE_PERCENT) wins.
        from custom_components.adaptive_cover_pro.const import (
            POSITION_TOLERANCE_PERCENT,
        )

        res = _check(expected=70).evaluate(
            "cover.x",
            _state({"current_tilt_position": 70 - POSITION_TOLERANCE_PERCENT - 1}),
            manual_threshold=1,  # would say "manual" naively
        )
        assert res.is_manual is True
