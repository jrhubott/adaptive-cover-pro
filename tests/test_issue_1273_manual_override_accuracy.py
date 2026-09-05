"""Manual-override accuracy defects found by code review (issue #1273).

Three things are pinned here, each of which the shipped suite let through:

* **Threshold boundary parity.** The primary axis rejects at
  ``delta <= threshold``; the secondary axis marked manual at
  ``delta >= threshold``. At ``delta == threshold`` the two axes returned
  opposite verdicts for the same user option. ``>`` is the correct operator on
  both: ``CoverCommandService._position_matches`` treats
  ``abs(actual - target) <= tolerance`` as ARRIVED, so a delta equal to the
  floored threshold must not simultaneously read as a manual touch.
* **Reason-string honesty.** Three ``PositionDeltaDetector`` reason strings
  described a comparison their own branch did not perform. These strings reach
  the diagnostics event timeline, ``last_skipped_action`` and the Lovelace card,
  so they are the first thing read when triaging a false override.
* **Suppression-count decay.** The Issue-#33 24 h window was pruned on write
  only, so ``primary_axis_suppression_counts()`` froze after the last
  suppression instead of decaying to zero.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.managers.manual_override import (
    AdaptiveCoverManager,
    DetectionContext,
    OverrideState,
    PositionDeltaDetector,
    SecondaryAxisCheck,
    effective_manual_threshold,
)
from custom_components.adaptive_cover_pro.managers.manual_override.expiry import (
    expiry_for_started_at,
)

_LOGGER = logging.getLogger(__name__)

THRESHOLD = 5
assert (
    effective_manual_threshold(THRESHOLD) == THRESHOLD
), "test relies on THRESHOLD sitting above the POSITION_TOLERANCE_PERCENT floor"


def _ctx(
    *,
    our_state: int,
    new_position: int | None,
    old_position: int | None = None,
    has_recorded_target: bool = True,
) -> DetectionContext:
    """Minimal DetectionContext for pure primary-axis threshold tests."""
    policy = MagicMock()
    policy.primary_axis_suppression.return_value = False
    new_state = MagicMock()
    new_state.state = "open"
    return DetectionContext(
        entity_id="cover.x",
        our_state=our_state,
        new_state=new_state,
        old_state=None,
        new_position=new_position,
        old_position=old_position,
        caps=MagicMock(),
        policy=policy,
        manual_threshold=THRESHOLD,
        has_recorded_target=has_recorded_target,
        allow_reset=True,
        is_acp_context=False,
        context_user_id=None,
        context_id=None,
        seconds_since_command=None,
        secondary_axis_check=None,
        is_waiting=lambda _e: False,
        is_in_command_grace=lambda _e: False,
        is_in_transit=lambda _e: False,
        now=dt.datetime.now(dt.UTC),
    )


def _secondary(*, expected: int) -> SecondaryAxisCheck:
    return SecondaryAxisCheck(
        expected=expected,
        attribute="current_tilt_position",
        label="tilt",
    )


def _state(value: int):
    s = MagicMock()
    s.attributes = {"current_tilt_position": value}
    return s


# ---------------------------------------------------------------------------
# A2 — the two axes must agree at delta == threshold
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestThresholdBoundaryParity:
    """``delta == threshold`` is NOT a manual override, on either axis.

    The floored threshold doubles as the arrival tolerance
    (``effective_manual_threshold`` floors at ``POSITION_TOLERANCE_PERCENT``,
    the same constant ``_position_matches`` compares with ``<=``). A delta that
    counts as "arrived" must never also count as "the user moved it".
    """

    def test_primary_axis_at_threshold_is_not_manual(self):
        decision = PositionDeltaDetector().detect(
            _ctx(our_state=50, new_position=50 + THRESHOLD)
        )
        assert decision.mark_manual is False
        assert decision.event_name == "manual_override_rejected_within_threshold"

    def test_secondary_axis_at_threshold_is_not_manual(self):
        res = _secondary(expected=70).evaluate(
            "cover.x", _state(70 + THRESHOLD), manual_threshold=THRESHOLD
        )
        assert res.is_manual is False
        assert (
            res.consumed is False
        ), "a below-threshold secondary delta falls through to the position axis"
        assert res.event_name is None

    def test_primary_axis_one_past_threshold_is_manual(self):
        decision = PositionDeltaDetector().detect(
            _ctx(our_state=50, new_position=50 + THRESHOLD + 1)
        )
        assert decision.mark_manual is True
        assert decision.event_name == "manual_override_set"

    def test_secondary_axis_one_past_threshold_is_manual(self):
        res = _secondary(expected=70).evaluate(
            "cover.x", _state(70 + THRESHOLD + 1), manual_threshold=THRESHOLD
        )
        assert res.is_manual is True
        assert res.consumed is True
        assert res.event_name == "manual_override_set"

    @pytest.mark.parametrize("delta", [0, 1, THRESHOLD - 1, THRESHOLD])
    def test_axes_agree_below_and_at_threshold(self, delta: int):
        """Neither axis fires anywhere in ``[0, threshold]``."""
        primary = PositionDeltaDetector().detect(
            _ctx(our_state=50, new_position=50 + delta)
        )
        secondary = _secondary(expected=70).evaluate(
            "cover.x", _state(70 + delta), manual_threshold=THRESHOLD
        )
        assert primary.mark_manual is False
        assert secondary.is_manual is False

    @pytest.mark.parametrize("delta", [THRESHOLD + 1, THRESHOLD + 20, 40])
    def test_axes_agree_above_threshold(self, delta: int):
        """Both axes fire everywhere above the threshold."""
        primary = PositionDeltaDetector().detect(
            _ctx(our_state=50, new_position=50 + delta)
        )
        secondary = _secondary(expected=40).evaluate(
            "cover.x", _state(40 + delta), manual_threshold=THRESHOLD
        )
        assert primary.mark_manual is True
        assert secondary.is_manual is True


# ---------------------------------------------------------------------------
# A1 — a reason string must describe the branch that produced it
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReasonStringsMatchTheirBranch:
    """Every rendered comparison operator must be the one actually evaluated.

    Asserted on the operator token rather than the whole sentence so the prose
    can be reworded without pinning it, but a flipped operator still fails.
    """

    def test_within_threshold_reason_uses_the_inclusive_operator(self):
        """The reject branch fires on ``<=``, so it must not claim ``<``."""
        decision = PositionDeltaDetector().detect(
            _ctx(our_state=50, new_position=50 + THRESHOLD)
        )
        reason = decision.event_kwargs["reason"]
        assert "<=" in reason, reason
        # The bare "<" claim was false at exactly the threshold.
        assert "% < threshold" not in reason, reason

    def test_manual_reason_uses_the_strict_operator(self):
        """The manual branch fires on ``>``, so it must not claim ``>=``."""
        decision = PositionDeltaDetector().detect(
            _ctx(our_state=50, new_position=50 + THRESHOLD + 1)
        )
        reason = decision.event_kwargs["reason"]
        assert ">" in reason
        assert ">=" not in reason, reason

    def test_no_recorded_target_reason_uses_the_strict_operator(self):
        """The context-less-move branch also fires on ``>`` (issue #654)."""
        decision = PositionDeltaDetector().detect(
            _ctx(
                our_state=50,
                new_position=80,
                old_position=80 - (THRESHOLD + 1),
                has_recorded_target=False,
            )
        )
        assert decision.mark_manual is True
        reason = decision.event_kwargs["reason"]
        assert ">" in reason
        assert ">=" not in reason, reason

    def test_no_recorded_target_boundary_matches_the_primary_axis(self):
        """A context-less move of exactly the threshold is not a move (#654)."""
        decision = PositionDeltaDetector().detect(
            _ctx(
                our_state=50,
                new_position=80,
                old_position=80 - THRESHOLD,
                has_recorded_target=False,
            )
        )
        assert decision.mark_manual is False
        assert decision.event_name == "manual_override_rejected_no_command_target"


# ---------------------------------------------------------------------------
# A3 — the 24 h suppression window must decay when read, not only when written
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSuppressionCountDecays:
    """``primary_axis_suppression_counts`` is published as a *last 24 h* figure.

    Pruning only on write meant the number froze at the last suppression, so a
    diagnostics file pulled days later still told the user to raise
    ``venetian_backrotate_publish_lag`` for an actuator that had settled.
    """

    def _manager(self) -> AdaptiveCoverManager:
        return AdaptiveCoverManager(
            MagicMock(), {"hours": 2}, _LOGGER, event_buffer=None
        )

    def test_fresh_suppression_is_counted(self):
        mgr = self._manager()
        mgr._record_primary_axis_suppression("cover.x", delta=90.0)
        assert mgr.primary_axis_suppression_counts() == {"cover.x": 1}

    def test_count_decays_to_empty_once_the_window_passes(self):
        mgr = self._manager()
        mgr._record_primary_axis_suppression("cover.x", delta=90.0)

        # Back-date the recorded event past the 24 h window without recording a
        # new one — the reader is the only thing that can notice.
        stale = dt.datetime.now(dt.UTC) - dt.timedelta(hours=25)
        mgr._primary_axis_suppression_counts["cover.x"][0] = stale

        assert mgr.primary_axis_suppression_counts() == {}

    def test_reader_keeps_in_window_events_and_drops_stale_ones(self):
        mgr = self._manager()
        for _ in range(3):
            mgr._record_primary_axis_suppression("cover.x", delta=90.0)

        deque = mgr._primary_axis_suppression_counts["cover.x"]
        assert len(deque) == 3
        deque[0] = dt.datetime.now(dt.UTC) - dt.timedelta(hours=30)
        deque[1] = dt.datetime.now(dt.UTC) - dt.timedelta(hours=25)

        assert mgr.primary_axis_suppression_counts() == {"cover.x": 1}


# ---------------------------------------------------------------------------
# A4 — one definition of "this override is live"
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLivenessHasOneDefinition:
    """``expiry_for`` promises ``None`` when the cover is not held.

    It gated only on ``manual_control_time`` and never consulted
    ``manual_control``, so the end-time sensor (which iterates the former) and
    the diagnostics block (which reads the latter) could disagree. Since #1274
    there is no second store to consult.
    """

    def _manager(self) -> AdaptiveCoverManager:
        mgr = AdaptiveCoverManager(
            MagicMock(), {"hours": 2}, _LOGGER, event_buffer=None
        )
        mgr.add_covers({"cover.x"})
        return mgr

    def test_expiry_for_is_none_when_not_held(self):
        mgr = self._manager()
        assert mgr.expiry_for("cover.x") is None

    def test_expiry_for_is_set_when_held(self):
        mgr = self._manager()
        mgr.mark_user_command("cover.x", reason="test")
        assert mgr.is_cover_manual("cover.x") is True
        assert mgr.expiry_for("cover.x") is not None

    def test_expiry_for_is_none_after_reset(self):
        mgr = self._manager()
        mgr.mark_user_command("cover.x", reason="test")
        mgr.reset("cover.x")
        assert mgr.expiry_for("cover.x") is None

    def test_the_flag_store_no_longer_exists(self):
        """#1274: presence in the single store IS the armed flag.

        Retires ``test_expiry_for_honours_the_manual_control_flag``, which
        manufactured "start time live, flag False" — a split the one-store
        model cannot express and, as that test's own docstring conceded,
        nothing in production could produce.
        """
        mgr = self._manager()
        assert not hasattr(mgr, "manual_control")

        mgr.mark_user_command("cover.x", reason="test")
        assert mgr.override_for("cover.x") is not None
        assert mgr.is_cover_manual("cover.x") is True

        mgr.reset("cover.x")
        assert mgr.override_for("cover.x") is None
        assert mgr.is_cover_manual("cover.x") is False
        assert mgr.expiry_for("cover.x") is None

    def test_override_state_is_immutable(self):
        """Re-arming replaces the stored state; it never mutates one in place."""
        mgr = self._manager()
        mgr.mark_user_command("cover.x", reason="test")

        state = mgr.override_for("cover.x")
        assert isinstance(state, OverrideState)
        with pytest.raises(dataclasses.FrozenInstanceError):
            state.started_at = dt.datetime.now(dt.UTC)

        assert state.expiry is None
        assert mgr.expiry_for("cover.x") == expiry_for_started_at(
            state.started_at, mgr.reset_duration
        )

    def test_active_entities_matches_expiry_for(self):
        """The accessor both consumers should share agrees with ``expiry_for``."""
        mgr = self._manager()
        mgr.add_covers({"cover.y"})
        mgr.mark_user_command("cover.x", reason="test")

        assert set(mgr.active_entities()) == {"cover.x"}
        assert all(mgr.expiry_for(eid) is not None for eid in mgr.active_entities())


@pytest.mark.unit
class TestArmIsTheOnlyWriter:
    """``_arm`` is the only path that puts a cover into the override store.

    The armed flag used to be a separate dict written by each arming caller, so
    ``set_last_updated`` could record a start time on a cover the flag said was
    not overridden — a convention rather than a guarantee (#1273). Since #1274
    there is one store and one writer: being in it IS being overridden, so the
    two can no longer disagree.
    """

    def _manager(self) -> AdaptiveCoverManager:
        mgr = AdaptiveCoverManager(
            MagicMock(), {"hours": 2}, _LOGGER, event_buffer=None
        )
        mgr.add_covers({"cover.x"})
        return mgr

    def test_set_last_updated_alone_arms_the_flag(self):
        mgr = self._manager()
        state = MagicMock(last_updated=dt.datetime.now(dt.UTC))

        mgr.set_last_updated("cover.x", state, allow_reset=True)

        assert mgr.is_cover_manual("cover.x") is True
        assert mgr.expiry_for("cover.x") is not None
        assert mgr.active_entities() == ["cover.x"]

    def test_setdefault_branch_also_arms_the_flag(self):
        """``overwrite=False`` is the do-not-extend path, not a do-not-arm path."""
        mgr = self._manager()

        mgr._arm("cover.x", timestamp=dt.datetime.now(dt.UTC), overwrite=False)

        assert mgr.is_cover_manual("cover.x") is True
        assert mgr.active_entities() == ["cover.x"]

    def test_reset_removes_the_override_state(self):
        mgr = self._manager()
        mgr.mark_user_command("cover.x", reason="test")

        mgr.reset("cover.x")

        assert mgr.is_cover_manual("cover.x") is False
        assert mgr.override_for("cover.x") is None
        assert mgr.active_entities() == []
        assert mgr.expiry_for("cover.x") is None
