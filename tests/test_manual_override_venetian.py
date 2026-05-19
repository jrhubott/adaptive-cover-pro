"""Tilt-axis manual-override tests for the cover_venetian sensor type.

Issue #33: real-motor venetians (KNX, Somfy IO, Shelly 2PM) back-rotate the
slats while moving vertically. AdaptiveCoverManager must therefore ignore
tilt-axis drift inside the venetian tilt-suppression window, but still flag
genuine "user grabbed the wand" tilt deltas outside that window. Position-
axis drift continues to behave exactly as it does for any other cover type.

Wired through ``SecondaryAxisCheck`` — a per-cover-type plug supplied by
``CoverTypePolicy.secondary_axis_check`` (``VenetianPolicy`` for these tests).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.venetian import (
    DualAxisSequencer,
)
from custom_components.adaptive_cover_pro.managers.manual_override import (
    AdaptiveCoverManager,
    SecondaryAxisCheck,
)


def _make_event(entity_id: str, *, position: int | None, tilt: int | None):
    """Build a fake StateChangedData reporting both axes."""
    attrs: dict = {}
    if position is not None:
        attrs["current_position"] = position
    if tilt is not None:
        attrs["current_tilt_position"] = tilt
    event = MagicMock()
    event.entity_id = entity_id
    event.new_state = MagicMock()
    event.new_state.state = "stopped"
    event.new_state.attributes = attrs
    event.new_state.last_updated = dt.datetime.now(dt.UTC)
    return event


def _make_manager(entity_id: str) -> AdaptiveCoverManager:
    mgr = AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration={"hours": 2},
        logger=MagicMock(),
    )
    mgr.add_covers([entity_id])
    return mgr


def _tilt_check(*, expected: int = 70, suppressed: bool) -> SecondaryAxisCheck:
    return SecondaryAxisCheck(
        expected=expected,
        attribute="current_tilt_position",
        label="tilt",
        suppression=lambda _eid, _delta: suppressed,
    )


def _make_sequencer_suppression(
    *, entity_id: str, state: str
) -> Callable[[str, float], bool]:
    """Build a real ``DualAxisSequencer`` and return its bound delta-cap gate.

    Closes the integration gap the lambda-stub helpers leave open (issue #33
    follow-on): wires ``stamp_position_command`` and the ``_get_state``
    callback together so the cap behaves exactly as ``VenetianPolicy.is_in_tilt_suppression``
    does in production. ``state`` should be ``"opening"``/``"closing"`` to
    model an in-transit cycle, or ``"stopped"`` to model a settled cycle.
    """
    hass = MagicMock()
    seq = DualAxisSequencer(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=lambda _eid: None,
        set_commanded_position=lambda *_: None,
        position_tolerance=5,
        is_dry_run=lambda: False,
        get_state=lambda _eid: state,
    )
    seq.stamp_position_command(entity_id)
    return seq.is_in_suppression_with_cap


def test_tilt_drift_inside_suppression_window_is_ignored() -> None:
    """Tilt drift right after a position command is the motor back-rotate.

    `suppression(entity_id) -> True` makes the tilt-axis evaluation log the
    rejection and fall through to the position-axis check, leaving the cover
    not-manual when the position axis is on target.
    """
    entity_id = "cover.venetian_kitchen"
    mgr = _make_manager(entity_id)

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=50, tilt=20),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
        secondary_axis_check=_tilt_check(suppressed=True),
    )

    assert not mgr.is_cover_manual(entity_id)


def test_tilt_drift_outside_suppression_trips_override() -> None:
    """Once the suppression window has elapsed, tilt drift is a user touch."""
    entity_id = "cover.venetian_office"
    mgr = _make_manager(entity_id)

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=50, tilt=20),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
        secondary_axis_check=_tilt_check(suppressed=False),
    )

    assert mgr.is_cover_manual(entity_id)


def test_tilt_drift_within_threshold_is_ignored_even_outside_window() -> None:
    """Tilt deltas under the threshold floor are ignored regardless of suppression."""
    entity_id = "cover.venetian_lounge"
    mgr = _make_manager(entity_id)

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=50, tilt=72),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
        secondary_axis_check=_tilt_check(suppressed=False),
    )

    assert not mgr.is_cover_manual(entity_id)


def test_position_drift_inside_tilt_suppression_window_is_ignored() -> None:
    """Position drift caused by the motor's back-drive must not trip override.

    During the venetian back-rotate window the motor physically moves the cover
    position axis as a side-effect of the tilt command. That drift is not a user
    touch — both axes must be suppressed while the window is open.
    """
    entity_id = "cover.venetian_master"
    mgr = _make_manager(entity_id)

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=58, tilt=20),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
        secondary_axis_check=_tilt_check(suppressed=True),
    )

    assert not mgr.is_cover_manual(entity_id)


def test_position_drift_inside_window_with_tilt_on_target_is_ignored() -> None:
    """Tilt on-target + position drifted by motor back-drive must not trip override.

    Regression for issue #33: when tilt arrives exactly at the expected value,
    the old code short-circuited to consumed=False without consulting the
    suppression callback. The position-axis check then saw |34-37|=3 (= threshold
    floor of 3), which is not strictly less than 3, and set manual override.
    """
    entity_id = "cover.venetian_kitchen"
    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=37, tilt=70),
        our_state=34,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=3,
        secondary_axis_check=_tilt_check(expected=70, suppressed=True),
    )

    assert not mgr.is_cover_manual(entity_id)


def test_position_drift_outside_window_with_tilt_on_target_is_ignored() -> None:
    """Tilt on-target + position drifted after suppression window expires must not trip override.

    Field bug from issue #33 beta.4: motor back-drive on the position axis can
    outlast the 90s suppression window. When the next state event arrives with
    tilt exactly at the expected value, the old code returned consumed=False,
    letting the position-axis check see |34-37|=3 >= POSITION_TOLERANCE_PERCENT
    and trip manual override on residual motor drift.
    """
    entity_id = "cover.venetian_bedroom"
    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=37, tilt=70),
        our_state=34,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=3,
        secondary_axis_check=_tilt_check(expected=70, suppressed=False),
    )

    assert not mgr.is_cover_manual(entity_id)


def test_position_drift_outside_tilt_suppression_trips_override() -> None:
    """Once the suppression window has closed, position drift is a user touch."""
    entity_id = "cover.venetian_master2"
    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=80, tilt=70),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
        secondary_axis_check=_tilt_check(suppressed=False),
    )

    assert mgr.is_cover_manual(entity_id)


def test_tilt_drift_during_in_transit_close_is_ignored_regardless_of_delta() -> None:
    """Issue #33: motor back-rotate during a closing carriage can exceed the cap.

    Report 1 timeline: ``set_cover_position(86)`` stamps suppression at T+0;
    while ``cover.state == "closing"`` the actuator reports
    ``current_tilt_position=0`` against ``our_state=100`` — a 100% delta that
    blows past the 30% ``VENETIAN_BACKROTATE_MAX_DELTA_PERCENT`` cap. The cap
    must NOT defeat suppression while the carriage is still mid-travel; this
    is real motor drift, not a user move.
    """
    entity_id = "cover.venetian_kitchen_close"
    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    suppression = _make_sequencer_suppression(entity_id=entity_id, state="closing")

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=86, tilt=0),
        our_state=100,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
        secondary_axis_check=SecondaryAxisCheck(
            expected=100,
            attribute="current_tilt_position",
            label="tilt",
            suppression=suppression,
        ),
    )

    assert not mgr.is_cover_manual(entity_id)


def test_tilt_drift_during_in_transit_open_is_ignored_regardless_of_delta() -> None:
    """Issue #33: same fault on the opening side (Report 2, fnep).

    Diagnostic timeline: at T+0 ``set_cover_position(17)`` stamps suppression;
    while ``cover.state == "opening"`` the actuator reports
    ``current_tilt_position=100`` against ``our_state=60`` — a 40% delta past
    the 30% cap. Suppression must hold; the 60→100 mismatch is the actuator
    landing wrong during travel, not a user touch.
    """
    entity_id = "cover.venetian_office_open"
    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    suppression = _make_sequencer_suppression(entity_id=entity_id, state="opening")

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=17, tilt=100),
        our_state=60,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
        secondary_axis_check=SecondaryAxisCheck(
            expected=60,
            attribute="current_tilt_position",
            label="tilt",
            suppression=suppression,
        ),
    )

    assert not mgr.is_cover_manual(entity_id)


def test_tilt_drift_after_settle_with_large_delta_still_trips_override() -> None:
    """The in-transit cap bypass must NOT swallow a genuine post-settle user move.

    Once ``cover.state`` leaves the moving set, the geometry-bounded cap
    reasserts: a delta > 30% with state=``stopped`` is a user grabbing the
    slats, not motor drift, and must still trip manual override even inside
    the 90s suppression window.
    """
    entity_id = "cover.venetian_post_settle_user_move"
    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    suppression = _make_sequencer_suppression(entity_id=entity_id, state="stopped")

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=50, tilt=0),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
        secondary_axis_check=SecondaryAxisCheck(
            expected=80,
            attribute="current_tilt_position",
            label="tilt",
            suppression=suppression,
        ),
    )

    assert mgr.is_cover_manual(entity_id)


def test_non_venetian_cover_with_no_check_runs_position_axis_only() -> None:
    """Without a SecondaryAxisCheck the manager runs the legacy position path."""
    entity_id = "cover.blind"
    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=50, tilt=10),
        our_state=50,
        policy=get_policy("cover_blind"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=5,
        secondary_axis_check=None,
    )

    assert not mgr.is_cover_manual(entity_id)
