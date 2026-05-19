"""Direct tests for :class:`StateClassifier`.

The existing manual-override tests (issue #147, #172, #186, #271, #285)
exercise the same body through the coordinator's
:meth:`process_entity_state_change` shim and remain the contract guard
for behavioural correctness.  These tests cover the new public surface:
``CoverCommandService.classify_state_change`` and the classifier's
direct ``classify()`` method, with the smallest cases needed to prove the
relocation is wired correctly.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.diagnostics.event_buffer import EventBuffer
from custom_components.adaptive_cover_pro.managers.cover_command.state_classifier import (
    StateClassifier,
)
from custom_components.adaptive_cover_pro.managers.grace_period import (
    GracePeriodManager,
)


def _make_event(
    entity_id: str, new_pos: int, old_pos: int, new_state="open", old_state="open"
):
    event = MagicMock()
    event.entity_id = entity_id
    event.new_state = MagicMock()
    event.new_state.state = new_state
    event.new_state.attributes = {"current_position": new_pos}
    event.old_state = MagicMock()
    event.old_state.state = old_state
    event.old_state.attributes = {"current_position": old_pos}
    return event


def _make_service(
    *,
    target: int,
    new_pos: int,
    old_pos: int,
    waiting: bool = True,
    transit_timeout: int = 45,
    sent_seconds_ago: float = 10.0,
    last_progress_seconds_ago: float | None = None,
    reached: bool = False,
):
    """Return a MagicMock service exposing the public surface the classifier uses."""
    svc = MagicMock()
    svc._logger = MagicMock()
    svc.is_waiting_for_target = MagicMock(return_value=waiting)
    svc.get_cover_capabilities = MagicMock(return_value={"has_set_position": True})

    def _read_pos(_eid, _caps, state_obj):
        return (
            new_pos
            if state_obj.attributes.get("current_position") == new_pos
            else old_pos
        )

    svc.read_position_with_capabilities = MagicMock(side_effect=_read_pos)
    svc.check_target_reached = MagicMock(return_value=reached)
    svc.get_target = MagicMock(return_value=target)
    svc.record_progress = MagicMock()
    svc.set_waiting = MagicMock()
    svc.waiting_entities = MagicMock(return_value=[])
    svc.transit_timeout_seconds = transit_timeout

    now = dt.datetime.now(dt.UTC)
    ref_age = (
        last_progress_seconds_ago
        if last_progress_seconds_ago is not None
        else sent_seconds_ago
    )

    def _elapsed(_eid, now_arg):
        ref = now - dt.timedelta(seconds=ref_age)
        return (now_arg - ref).total_seconds()

    svc.transit_elapsed_without_progress = MagicMock(side_effect=_elapsed)
    return svc


@pytest.fixture
def classifier_setup():
    buf = EventBuffer(maxlen=20)
    grace = GracePeriodManager(logger=MagicMock(), command_grace_seconds=5.0)
    debug_log = MagicMock()

    def _build(svc):
        classifier = StateClassifier(svc, event_buffer=buf, debug_log=debug_log)
        return classifier, buf, grace, debug_log

    return _build


@pytest.mark.unit
def test_classify_returns_early_when_not_waiting(classifier_setup):
    svc = _make_service(target=0, new_pos=10, old_pos=10, waiting=False)
    classifier, _buf, grace, _debug_log = classifier_setup(svc)
    target_just_reached: set[str] = set()
    classifier.classify(
        _make_event("cover.x", new_pos=10, old_pos=10),
        ignore_intermediate_states=False,
        target_just_reached=target_just_reached,
        grace_mgr=grace,
    )
    svc.set_waiting.assert_not_called()
    assert target_just_reached == set()


@pytest.mark.unit
def test_classify_skips_intermediate_states_when_configured(classifier_setup):
    svc = _make_service(target=0, new_pos=50, old_pos=60)
    classifier, _buf, grace, _debug_log = classifier_setup(svc)
    classifier.classify(
        _make_event("cover.x", new_pos=50, old_pos=60, new_state="opening"),
        ignore_intermediate_states=True,
        target_just_reached=set(),
        grace_mgr=grace,
    )
    svc.is_waiting_for_target.assert_not_called()


@pytest.mark.unit
def test_classify_marks_target_just_reached_within_tolerance(classifier_setup):
    svc = _make_service(target=0, new_pos=1, old_pos=60, reached=True)
    classifier, _buf, grace, _debug_log = classifier_setup(svc)
    target_just_reached: set[str] = set()
    classifier.classify(
        _make_event("cover.x", new_pos=1, old_pos=60),
        ignore_intermediate_states=False,
        target_just_reached=target_just_reached,
        grace_mgr=grace,
    )
    assert "cover.x" in target_just_reached


@pytest.mark.unit
def test_classify_records_forward_progress(classifier_setup):
    svc = _make_service(target=0, new_pos=50, old_pos=60)
    classifier, buf, grace, _debug_log = classifier_setup(svc)
    classifier.classify(
        _make_event("cover.x", new_pos=50, old_pos=60),
        ignore_intermediate_states=False,
        target_just_reached=set(),
        grace_mgr=grace,
    )
    types = [e["event"] for e in buf.snapshot()]
    assert "transit_progress_forward" in types
    svc.record_progress.assert_called_once()
    svc.set_waiting.assert_not_called()


@pytest.mark.unit
def test_classify_clears_wait_after_transit_timeout(classifier_setup):
    svc = _make_service(
        target=0,
        new_pos=80,
        old_pos=80,
        sent_seconds_ago=50.0,
        transit_timeout=45,
    )
    classifier, buf, grace, _debug_log = classifier_setup(svc)
    classifier.classify(
        _make_event("cover.x", new_pos=80, old_pos=80),
        ignore_intermediate_states=False,
        target_just_reached=set(),
        grace_mgr=grace,
    )
    types = [e["event"] for e in buf.snapshot()]
    assert "transit_timeout_cleared" in types
    svc.set_waiting.assert_called_once_with("cover.x", False)


@pytest.mark.unit
def test_classify_restarts_grace_on_step_motor_pause(classifier_setup):
    svc = _make_service(target=10, new_pos=50, old_pos=60)
    classifier, _buf, grace, _debug_log = classifier_setup(svc)
    grace.start_command_grace_period = MagicMock()
    event = _make_event(
        "cover.x", new_pos=50, old_pos=60, new_state="open", old_state="opening"
    )
    classifier.classify(
        event,
        ignore_intermediate_states=False,
        target_just_reached=set(),
        grace_mgr=grace,
    )
    grace.start_command_grace_period.assert_called_once_with("cover.x")
    # Step-motor pause must short-circuit BEFORE the forward-progress block.
    svc.record_progress.assert_not_called()


@pytest.mark.unit
def test_classify_records_startup_delay(classifier_setup):
    svc = _make_service(target=100, new_pos=0, old_pos=0)
    classifier, buf, grace, _debug_log = classifier_setup(svc)
    event = _make_event(
        "cover.x", new_pos=0, old_pos=0, new_state="open", old_state="closed"
    )
    classifier.classify(
        event,
        ignore_intermediate_states=False,
        target_just_reached=set(),
        grace_mgr=grace,
    )
    types = [e["event"] for e in buf.snapshot()]
    assert "transit_startup_delay" in types
    # Startup-delay branch must NOT clear wait_for_target.
    svc.set_waiting.assert_not_called()


@pytest.mark.unit
def test_classify_clears_wait_when_cover_moves_away_from_target(classifier_setup):
    svc = _make_service(target=0, new_pos=70, old_pos=60)
    classifier, buf, grace, _debug_log = classifier_setup(svc)
    event = _make_event(
        "cover.x", new_pos=70, old_pos=60, new_state="open", old_state="open"
    )
    classifier.classify(
        event,
        ignore_intermediate_states=False,
        target_just_reached=set(),
        grace_mgr=grace,
    )
    types = [e["event"] for e in buf.snapshot()]
    assert "transit_cleared" in types
    svc.set_waiting.assert_called_once_with("cover.x", False)
