"""Tests for the pluggable manual-override detector subsystem.

Covers the detector ABC contract + registry, the two shipped detectors as pure
units, the default channel decisions, and the engine wiring (edge callbacks,
command-timing clock, runtime config).
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.managers.manual_override import (
    DETECTOR_REGISTRY,
    AdaptiveCoverManager,
    DetectionContext,
    DetectorConfig,
    OverrideDecision,
    OverrideDetector,
    PositionDeltaDetector,
    StopToMy,
    TimeWindowDetector,
    UserContextChange,
    default_stop_to_my_decision,
    default_user_context_decision,
    get_detector,
)

from .stub_detector import StubDetector


def _ctx(
    *,
    our_state: int = 50,
    new_position: int | None = 50,
    old_position: int | None = None,
    manual_threshold: int | None = 5,
    is_in_transit: bool = False,
    primary_suppress: bool = False,
    seconds_since_command: float | None = None,
    new_state_str: str = "open",
    has_recorded_target: bool = True,
) -> DetectionContext:
    """Build a DetectionContext for pure-unit detector tests."""
    policy = MagicMock()
    policy.primary_axis_suppression.return_value = primary_suppress
    new_state = MagicMock()
    new_state.state = new_state_str
    return DetectionContext(
        entity_id="cover.x",
        our_state=our_state,
        new_state=new_state,
        old_state=None,
        new_position=new_position,
        old_position=old_position,
        caps=MagicMock(),
        policy=policy,
        manual_threshold=manual_threshold,
        has_recorded_target=has_recorded_target,
        allow_reset=True,
        is_acp_context=False,
        context_user_id=None,
        context_id=None,
        seconds_since_command=seconds_since_command,
        secondary_axis_check=None,
        is_waiting=lambda _e: False,
        is_in_command_grace=lambda _e: False,
        is_in_transit=lambda _e: is_in_transit,
        now=dt.datetime.now(dt.UTC),
    )


def _config(*, command_window_seconds: float = 45.0) -> DetectorConfig:
    return DetectorConfig(
        manual_threshold=5,
        command_window_seconds=command_window_seconds,
        reset=False,
        duration={"hours": 2},
        ignore_external=False,
    )


# ---------------------------------------------------------------------------
# Registry + ABC contract (parametrized over every registered detector + stub)
# ---------------------------------------------------------------------------

ALL_DETECTORS = [*DETECTOR_REGISTRY.values(), StubDetector]


def test_registry_is_non_empty_and_well_formed():
    assert DETECTOR_REGISTRY
    for strategy_id, cls in DETECTOR_REGISTRY.items():
        assert issubclass(cls, OverrideDetector)
        assert cls.strategy_id == strategy_id
        assert isinstance(strategy_id, str)


@pytest.mark.parametrize("cls", ALL_DETECTORS, ids=lambda c: c.strategy_id)
def test_detect_returns_decision_and_never_raises_on_none_position(cls):
    detector = cls()
    decision = detector.detect(_ctx(new_position=None))
    assert isinstance(decision, OverrideDecision)


def test_get_detector_selects_by_id():
    cfg = _config()
    assert isinstance(get_detector("position_delta", cfg), PositionDeltaDetector)
    assert isinstance(get_detector("time_window", cfg), TimeWindowDetector)


def test_get_detector_falls_back_to_default_for_missing_or_unknown():
    cfg = _config()
    assert isinstance(get_detector(None, cfg), PositionDeltaDetector)
    assert isinstance(get_detector("does-not-exist", cfg), PositionDeltaDetector)


def test_time_window_from_config_takes_window_from_command_window_seconds():
    detector = get_detector("time_window", _config(command_window_seconds=90.0))
    assert isinstance(detector, TimeWindowDetector)
    # within a 90s window, a movement is rejected (not manual)
    decision = detector.detect(
        _ctx(our_state=100, new_position=0, seconds_since_command=30.0)
    )
    assert decision.mark_manual is False
    assert decision.event_name == "manual_override_rejected_command_window"


# ---------------------------------------------------------------------------
# PositionDeltaDetector — pure unit per gate
# ---------------------------------------------------------------------------


def test_position_delta_unavailable():
    d = PositionDeltaDetector().detect(_ctx(new_position=None))
    assert d.mark_manual is False
    assert d.event_name == "manual_override_rejected_position_unavailable"


def test_position_delta_in_transit():
    d = PositionDeltaDetector().detect(
        _ctx(our_state=50, new_position=90, is_in_transit=True, new_state_str="opening")
    )
    assert d.mark_manual is False
    assert d.event_name == "manual_override_rejected_in_transit"
    assert "opening" in d.event_kwargs["reason"]


def test_position_delta_primary_axis_suppression():
    d = PositionDeltaDetector().detect(
        _ctx(our_state=50, new_position=90, primary_suppress=True)
    )
    assert d.mark_manual is False
    assert d.event_name == "manual_override_rejected_primary_axis_suppression"
    assert d.record_primary_axis_suppression is True
    assert d.suppression_delta == 40.0


def test_position_delta_within_threshold():
    d = PositionDeltaDetector().detect(
        _ctx(our_state=50, new_position=52, manual_threshold=5)
    )
    assert d.mark_manual is False
    assert d.event_name == "manual_override_rejected_within_threshold"


def test_position_delta_over_threshold_marks():
    d = PositionDeltaDetector().detect(
        _ctx(our_state=50, new_position=80, manual_threshold=5)
    )
    assert d.mark_manual is True
    assert d.event_name == "manual_override_set"


def test_position_delta_equal_is_noop():
    d = PositionDeltaDetector().detect(_ctx(our_state=50, new_position=50))
    assert d == OverrideDecision()


def test_position_delta_threshold_floored_at_tolerance():
    # user threshold 0 → floored at POSITION_TOLERANCE_PERCENT (3); delta 3 within
    d = PositionDeltaDetector().detect(
        _ctx(our_state=37, new_position=34, manual_threshold=0)
    )
    assert d.mark_manual is False
    assert d.event_name == "manual_override_rejected_within_threshold"


def test_position_delta_no_recorded_target_resting_position_still_suppressed():
    """No recorded target + cover resting (old≈new) → still suppressed (#546).

    Issue #546: when ACP never sent a command, ``our_state`` is the pipeline's
    theoretical default, not a commanded position. A cover simply republishing
    its resting position (old == new) must NOT mark manual override, even though
    that position differs wildly from the meaningless default.
    """
    d = PositionDeltaDetector().detect(
        _ctx(
            our_state=100,
            old_position=25,
            new_position=25,
            manual_threshold=3,
            has_recorded_target=False,
        )
    )
    assert d.mark_manual is False
    assert d.event_name == "manual_override_rejected_no_command_target"


def test_position_delta_no_recorded_target_real_move_marks_override():
    """No recorded target + a real move (old≠new) → marks override (#654).

    A context-less physical-remote move shows the position changing between
    old_state and new_state. Even without a recorded command target, that
    change itself is the user touch and must engage the override, recording
    new_position (where the user left it) as the manual position.
    """
    d = PositionDeltaDetector().detect(
        _ctx(
            our_state=100,
            old_position=25,
            new_position=80,
            manual_threshold=3,
            has_recorded_target=False,
        )
    )
    assert d.mark_manual is True
    assert d.event_name == "manual_override_set"
    assert d.event_kwargs["new_position"] == 80


def test_position_delta_no_recorded_target_old_position_none_suppressed():
    """No recorded target + unknown old position → cannot prove a move, suppress."""
    d = PositionDeltaDetector().detect(
        _ctx(
            our_state=100,
            old_position=None,
            new_position=80,
            manual_threshold=3,
            has_recorded_target=False,
        )
    )
    assert d.mark_manual is False
    assert d.event_name == "manual_override_rejected_no_command_target"


def test_position_delta_no_recorded_target_subthreshold_move_suppressed():
    """No recorded target + tiny move (2% < tolerance floor 3%) → suppressed.

    A sub-tolerance drift between old and new is motor imprecision, not a user
    touch, so it stays suppressed even with no recorded command target.
    """
    d = PositionDeltaDetector().detect(
        _ctx(
            our_state=100,
            old_position=25,
            new_position=27,
            manual_threshold=0,
            has_recorded_target=False,
        )
    )
    assert d.mark_manual is False
    assert d.event_name == "manual_override_rejected_no_command_target"


def test_position_delta_with_recorded_target_still_marks_large_delta():
    """A recorded target with a large delta still marks manual override (regression)."""
    d = PositionDeltaDetector().detect(
        _ctx(
            our_state=100,
            new_position=25,
            manual_threshold=3,
            has_recorded_target=True,
        )
    )
    assert d.mark_manual is True
    assert d.event_name == "manual_override_set"


# ---------------------------------------------------------------------------
# TimeWindowDetector — pure unit
# ---------------------------------------------------------------------------


def test_time_window_within_window_rejects():
    d = TimeWindowDetector(window_seconds=60).detect(
        _ctx(our_state=100, new_position=0, seconds_since_command=10.0)
    )
    assert d.mark_manual is False
    assert d.event_name == "manual_override_rejected_command_window"


def test_time_window_after_window_marks_movement():
    d = TimeWindowDetector(window_seconds=60).detect(
        _ctx(our_state=100, new_position=0, seconds_since_command=120.0)
    )
    assert d.mark_manual is True
    assert d.event_name == "manual_override_set"


def test_time_window_no_command_recorded_marks_movement():
    d = TimeWindowDetector(window_seconds=60).detect(
        _ctx(our_state=100, new_position=0, seconds_since_command=None)
    )
    assert d.mark_manual is True


def test_time_window_equal_position_is_noop():
    d = TimeWindowDetector(window_seconds=60).detect(
        _ctx(our_state=50, new_position=50, seconds_since_command=120.0)
    )
    assert d == OverrideDecision()


def test_time_window_unavailable():
    d = TimeWindowDetector(window_seconds=60).detect(_ctx(new_position=None))
    assert d.event_name == "manual_override_rejected_position_unavailable"


# ---------------------------------------------------------------------------
# Default channel decisions
# ---------------------------------------------------------------------------


def test_default_user_context_decision_marks():
    d = default_user_context_decision(
        UserContextChange(
            entity_id="cover.x",
            new_state=MagicMock(),
            allow_reset=True,
            context_user_id="holly",
            context_id="ctx-1",
        )
    )
    assert d.mark_manual is True
    assert d.event_name == "manual_override_set"
    assert "holly" in d.event_kwargs["reason"]


def test_default_stop_to_my_decision_marks_when_not_waiting():
    d = default_stop_to_my_decision(
        StopToMy(entity_id="cover.x", my_position_value=40, is_waiting=lambda _e: False)
    )
    assert d is not None
    assert d.mark_manual is True
    assert d.event_kwargs["our_state"] == 40


def test_default_stop_to_my_decision_declines_when_waiting():
    d = default_stop_to_my_decision(
        StopToMy(entity_id="cover.x", my_position_value=40, is_waiting=lambda _e: True)
    )
    assert d is None


# ---------------------------------------------------------------------------
# Engine wiring: edge callbacks, command clock, runtime config
# ---------------------------------------------------------------------------


def _engine(*, detector, on_engaged=None, on_cleared=None) -> AdaptiveCoverManager:
    mgr = AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration={"hours": 2},
        logger=MagicMock(),
        detector=detector,
        on_engaged=on_engaged,
        on_cleared=on_cleared,
    )
    mgr.add_covers(["cover.a"])
    return mgr


def _drive(mgr, *, our_state=100, new_position=0):
    policy = MagicMock()
    policy.read_axis_value.return_value = new_position
    policy.primary_axis_suppression.return_value = False
    event = MagicMock()
    event.entity_id = "cover.a"
    event.old_state = MagicMock()
    event.new_state = MagicMock()
    event.new_state.state = "open"
    event.new_state.attributes = {}
    event.new_state.context = None
    event.new_state.last_updated = dt.datetime.now(dt.UTC)
    mgr.handle_state_change(
        event,
        our_state,
        policy,
        False,
        lambda _e: False,
        5,
        is_in_command_grace=lambda _e: False,
        is_in_transit=lambda _e: False,
    )


def test_on_engaged_fires_once_on_edge_only():
    engaged: list[str] = []
    mgr = _engine(detector=StubDetector(force_mark=True), on_engaged=engaged.append)
    _drive(mgr)
    assert mgr.is_cover_manual("cover.a")
    assert engaged == ["cover.a"]
    # second detection on an already-manual cover does not re-fire the edge
    _drive(mgr)
    assert engaged == ["cover.a"]


def test_on_engaged_not_fired_by_mark_user_command():
    engaged: list[str] = []
    mgr = _engine(detector=StubDetector(), on_engaged=engaged.append)
    mgr.mark_user_command("cover.a", reason="set_position")
    assert mgr.is_cover_manual("cover.a")
    assert engaged == []


def test_on_cleared_fires_on_reset():
    cleared: list[list[str]] = []
    detector = StubDetector(force_mark=True)
    mgr = _engine(detector=detector, on_cleared=cleared.append)
    _drive(mgr)
    mgr.reset("cover.a")
    assert cleared == [["cover.a"]]
    assert detector.resets == ["cover.a"]


def test_note_command_sent_feeds_seconds_since_command():
    # Without a recorded command, a movement is manual (no window).
    mgr1 = _engine(detector=TimeWindowDetector(window_seconds=60))
    _drive(mgr1)
    assert mgr1.is_cover_manual("cover.a")

    # After note_command_sent, the same movement is within the window → ignored.
    mgr2 = _engine(detector=TimeWindowDetector(window_seconds=60))
    mgr2.note_command_sent("cover.a")
    _drive(mgr2)
    assert not mgr2.is_cover_manual("cover.a")


def test_update_config_reapplies_reset_duration():
    mgr = _engine(detector=StubDetector())
    assert mgr.reset_duration == dt.timedelta(hours=2)
    mgr.update_config(
        DetectorConfig(
            manual_threshold=5,
            command_window_seconds=45.0,
            reset=False,
            duration={"seconds": 1},
            ignore_external=False,
        )
    )
    assert mgr.reset_duration == dt.timedelta(seconds=1)


def test_add_covers_notifies_detector():
    detector = StubDetector()
    AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration={"hours": 2},
        logger=MagicMock(),
        detector=detector,
    ).add_covers(["cover.a", "cover.b"])
    assert detector.added == [["cover.a", "cover.b"]]
