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

import dataclasses
import datetime as dt
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.venetian import (
    DualAxisSequencer,
)
from custom_components.adaptive_cover_pro.cover_types.venetian.policy import (
    VenetianPolicy,
)
from custom_components.adaptive_cover_pro.const import (
    VENETIAN_TILT_RESET_CLOSE,
    ControlMethod,
)
from custom_components.adaptive_cover_pro.managers.grace_period import (
    GracePeriodManager,
)
from custom_components.adaptive_cover_pro.managers.manual_override import (
    AdaptiveCoverManager,
    SecondaryAxisCheck,
)
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

# Zero the real-motor sleep delays — these tests drive run_sequence/attach()
# against the real sequencer, which otherwise waits on the post-tilt rebase delay.
pytestmark = pytest.mark.usefixtures("neutralize_venetian_delays")


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
    *,
    entity_id: str,
    state: str,
    stamp_age_seconds: float = 0.0,
    settled_now: bool = False,
    settled_age_seconds: float = 0.0,
) -> Callable[[str, float], bool]:
    """Build a real ``DualAxisSequencer`` and return its bound delta-cap gate.

    Closes the integration gap the lambda-stub helpers leave open (issue #33
    follow-on): wires ``stamp_position_command`` and the ``_get_state``
    callback together so the cap behaves exactly as ``VenetianPolicy.is_in_tilt_suppression``
    does in production. ``state`` should be ``"opening"``/``"closing"`` to
    model an in-transit cycle, or ``"stopped"`` to model a settled cycle.

    ``stamp_age_seconds`` backdates the suppression stamp so callers can land
    outside the post-settle cap-grace tail while still inside the overall
    suppression window.

    ``settled_now`` calls the sequencer's ``_stamp_settled`` writer to
    deterministically anchor the publish-lag window to "now" — modelling the
    state immediately after ``run_sequence`` observes the carriage transition
    to settled (issue #33 Track A). ``settled_age_seconds`` then backdates
    that anchor so a test can land outside the publish-lag window while
    keeping the legacy cap path intact.
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
    if stamp_age_seconds > 0:
        seq._suppression_at[entity_id] -= dt.timedelta(seconds=stamp_age_seconds)
    if settled_now:
        seq._stamp_settled(entity_id)
        if settled_age_seconds > 0:
            seq._settled_at[entity_id] -= dt.timedelta(seconds=settled_age_seconds)
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


def test_tilt_drift_inside_post_settle_grace_is_ignored() -> None:
    """Within the 5s grace tail after settle, even a large delta is motor drift.

    KNX/Shelly actuators publish their tilt-walk burst AFTER ``cover.state``
    has already settled to ``open``/``closed``. The post-settle cap grace
    keeps suppression on for this brief tail so the burst isn't misread as a
    user grab (issue #33).
    """
    entity_id = "cover.venetian_post_settle_grace"
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

    assert not mgr.is_cover_manual(entity_id)


def test_tilt_drift_after_settle_grace_with_large_delta_trips_override() -> None:
    """Once both the 5s cap-grace AND 45s publish-lag windows elapse, the cap reasserts.

    A delta > 30% with state=``stopped`` and both ``_suppression_at`` and
    ``_settled_at`` aged past their respective windows is a user grabbing
    the slats, not motor drift, and must still trip manual override even
    inside the 90s overall suppression window.

    The publish-lag window (issue #33 Track A) extends the original
    cap-grace behaviour: pre-Track-A this test backdated only the
    suppression stamp by 10 s; post-Track-A the settled anchor must also
    be backdated past 45 s.
    """
    from custom_components.adaptive_cover_pro.const import (
        VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS,
    )

    entity_id = "cover.venetian_post_settle_user_move"
    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    suppression = _make_sequencer_suppression(
        entity_id=entity_id,
        state="stopped",
        stamp_age_seconds=VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS + 5.0,
        settled_now=True,
        settled_age_seconds=VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS + 1.0,
    )

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


# ---------------------------------------------------------------------------
# Issue #33 Track A: publish-lag window anchored to moving → settled
# ---------------------------------------------------------------------------


def test_late_publish_burst_after_real_settle_does_not_trip_override() -> None:
    """Somfy IO publish-lag scenario end-to-end (issue #33 Track A).

    Reproduces the user's diagnostic: position command stamped, motor
    physically settles, sequencer observes moving→settled (``_stamp_settled``
    fires), 20-40 s later the actuator republishes the back-rotate tilt
    burst with a delta > 30%. The new publish-lag window must suppress the
    burst even though the legacy cap-grace (5 s) has long expired.
    """
    from custom_components.adaptive_cover_pro.const import (
        VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS,
    )

    entity_id = "cover.venetian_publish_lag"
    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    suppression = _make_sequencer_suppression(
        entity_id=entity_id,
        state="stopped",
        stamp_age_seconds=VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS + 1.0,
        settled_now=True,
    )

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=100, tilt=5),
        our_state=50,
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
    events = mgr.get_event_buffer()
    assert any(
        evt.get("event") == "manual_override_rejected_tilt_suppression"
        for evt in events
    ), f"expected rejected_tilt_suppression event, got {events}"


def test_real_user_twist_after_publish_lag_still_trips_override() -> None:
    """After the publish-lag window expires, a large tilt delta is a user touch.

    Counterpart to ``test_late_publish_burst_after_real_settle_does_not_trip_override``:
    same setup, but ``_settled_at`` is backdated past the publish-lag window
    so the cap reasserts. A delta=95 is a real wand-twist and must trip
    manual override.
    """
    from custom_components.adaptive_cover_pro.const import (
        VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS,
        VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS,
    )

    entity_id = "cover.venetian_real_user_twist"
    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    suppression = _make_sequencer_suppression(
        entity_id=entity_id,
        state="stopped",
        stamp_age_seconds=VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS + 1.0,
        settled_now=True,
        settled_age_seconds=VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS + 1.0,
    )

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=100, tilt=5),
        our_state=50,
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

    assert mgr.is_cover_manual(entity_id)
    events = mgr.get_event_buffer()
    assert any(
        evt.get("event") == "manual_override_set" for evt in events
    ), f"expected manual_override_set event, got {events}"


async def test_premature_stall_does_not_start_publish_lag_clock_early() -> None:
    """End-to-end Track B + Track A: stall declaration is anchored to real settle.

    A slow-starting actuator publishes the same position for the first few
    samples (motor hasn't begun travel), then ramps down. Without the
    Track B startup-grace fix the settle loop would declare stall on the
    third pre-motion sample and ``run_sequence`` would stamp
    ``_settled_at`` 20-30 s before the cover actually stops — starting
    the publish-lag clock at the wrong moment.

    This integration test drives ``run_sequence`` against that hardware
    profile and asserts the stamp lands AFTER the startup-grace window
    has elapsed.
    """
    import datetime as _dt

    from custom_components.adaptive_cover_pro.cover_types.venetian.sequencer import (
        DualAxisSequencer,
    )

    # Slow-start profile: 100 % for 5 samples (pre-motion), then ramps to 9.
    pos_seq = iter([100, 100, 100, 100, 100, 80, 60, 9, 9, 9])
    state_seq = iter(["open"] * 5 + ["closing"] * 3 + ["open"] * 2)

    from unittest.mock import AsyncMock

    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    seq = DualAxisSequencer(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=lambda _eid: next(pos_seq, 9),
        set_commanded_position=lambda *_: None,
        position_tolerance=5,
        is_dry_run=lambda: False,
        get_state=lambda _eid: next(state_seq, "open"),
        post_settle_hold_seconds=0,
    )

    # Drive the settle loop fast and force a short startup grace so the test
    # completes quickly, while still proving the grace gates the stall.
    import custom_components.adaptive_cover_pro.cover_types.venetian.sequencer as seq_mod

    orig_poll = seq_mod.VENETIAN_POSITION_SETTLE_POLL_SECONDS
    orig_grace = seq_mod.VENETIAN_POSITION_SETTLE_STARTUP_GRACE_SECONDS
    seq_mod.VENETIAN_POSITION_SETTLE_POLL_SECONDS = 0.01
    seq_mod.VENETIAN_POSITION_SETTLE_STARTUP_GRACE_SECONDS = 0.05
    try:
        t0 = _dt.datetime.now(_dt.UTC)
        await seq.run_sequence(
            "cover.x", position_target=9, tilt_target=60, reason="solar"
        )
    finally:
        seq_mod.VENETIAN_POSITION_SETTLE_POLL_SECONDS = orig_poll
        seq_mod.VENETIAN_POSITION_SETTLE_STARTUP_GRACE_SECONDS = orig_grace

    assert "cover.x" in seq._settled_at
    elapsed = (seq._settled_at["cover.x"] - t0).total_seconds()
    # Must land at or after the startup-grace boundary (0.05 s here). The
    # pre-fix code would stamp within microseconds of t0.
    assert elapsed >= 0.05, f"settled_at stamped too early: {elapsed:.4f} s"


# ---------------------------------------------------------------------------
# Issue #33 follow-on: command-grace guard for tilt-axis manual-override
# ---------------------------------------------------------------------------


def _make_policy_with_grace(
    entity_id: str, *, venetian_mode: str = "tilt_only"
) -> tuple[VenetianPolicy, GracePeriodManager]:
    """Build a VenetianPolicy attached with a real GracePeriodManager in active grace.

    Stamps ``entity_id`` in the grace manager so
    ``grace_mgr.is_in_command_grace_period(entity_id)`` returns True.
    The sequencer is wired for real via attach() so the suppression path
    runs exactly as it does in production.
    """
    import datetime as _dt

    grace_mgr = GracePeriodManager(logger=MagicMock())
    # Stamp the entity directly — avoids asyncio.create_task() in unit-test context.
    grace_mgr._command_timestamps[entity_id] = _dt.datetime.now().timestamp()

    policy = VenetianPolicy()
    policy.attach(
        hass=MagicMock(),
        logger=MagicMock(),
        grace_mgr=grace_mgr,
        get_current_position=lambda _: None,
        set_commanded_position=lambda *_: None,
        position_tolerance=5,
        is_dry_run=lambda: False,
        venetian_mode=venetian_mode,
    )
    return policy, grace_mgr


def _make_pipeline_result(*, tilt: int = 60) -> PipelineResult:
    """Build a minimal PipelineResult with a resolved tilt."""
    return PipelineResult(
        position=50,
        control_method=ControlMethod.SOLAR,
        reason="solar",
        tilt=tilt,
    )


def test_tilt_axis_change_inside_command_grace_is_not_override() -> None:
    """Tilt state change inside the command-grace window must NOT trip manual override.

    User1's diagnostic: ``tilt_command_sent`` → 4.5 s → ``tilt_command_drift``
    → ``manual_override_set``, all inside the 5 s command-grace tail.

    In tilt_only mode ``update_tilt_only`` never stamps ``_suppression_at``, so
    ``is_in_tilt_suppression`` returns False. Without the new grace gate the
    delta=17 tilt change trips a false override.
    """
    entity_id = "cover.venetian_grace_tilt_only"
    mgr = _make_manager(entity_id)
    policy, grace_mgr = _make_policy_with_grace(entity_id, venetian_mode="tilt_only")

    result = _make_pipeline_result(tilt=60)
    check = policy.secondary_axis_check(result, MagicMock())
    # delta = |60 - 43| = 17, threshold = 3 → would normally trip override
    assert check is not None

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=None, tilt=43),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=3,
        secondary_axis_check=check,
    )

    assert not mgr.is_cover_manual(
        entity_id
    ), "Tilt change inside command grace should NOT trigger manual override"


def test_tilt_only_update_inside_grace_position_and_tilt_mode_not_override() -> None:
    """Same grace guard applies in position_and_tilt mode.

    User2's path: update_tilt_only fires in position_and_tilt mode, similarly
    never stamps _suppression_at. A tilt state-change inside grace must be
    suppressed regardless of mode.
    """
    entity_id = "cover.venetian_grace_pos_and_tilt"
    mgr = _make_manager(entity_id)
    policy, grace_mgr = _make_policy_with_grace(
        entity_id, venetian_mode="position_and_tilt"
    )

    result = _make_pipeline_result(tilt=60)
    check = policy.secondary_axis_check(result, MagicMock())
    # delta = |60 - 43| = 17, threshold = 3 → would normally trip override
    assert check is not None

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=None, tilt=43),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=3,
        secondary_axis_check=check,
    )

    assert not mgr.is_cover_manual(entity_id), (
        "Tilt change inside command grace (position_and_tilt mode) "
        "should NOT trigger manual override"
    )


# ---------------------------------------------------------------------------
# Issue #33 Phase 5: cross-axis publish-lag suppression for the primary
# (position) axis. The tilt axis already has the three-tier suppression
# (mid-travel / post-stamp cap-grace / post-settle publish-lag); the user's
# 2026-05-26 diagnostic shows the same defect exists on the position axis
# but has no equivalent guard. The tests below pin the new behaviour.
# ---------------------------------------------------------------------------


def _make_policy_with_publish_lag(
    entity_id: str,
    *,
    settled_age_seconds: float = 0.0,
    backrotate_publish_lag_seconds: float | None = None,
) -> tuple[VenetianPolicy, object]:
    """Build a VenetianPolicy whose sequencer is anchored just past settle.

    The grace manager is constructed but NOT stamped — the suppression I
    want to exercise is the publish-lag window (anchored to the
    moving→settled transition), not the command-grace tail.

    ``settled_age_seconds`` backdates the sequencer's ``_settled_at`` stamp so
    a single test can land inside or outside the publish-lag window without
    sleeping. The suppression stamp is similarly aged past the
    ``VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS`` boundary so the 5 s cap-grace
    tail is closed — only the publish-lag window itself protects the test.

    ``backrotate_publish_lag_seconds`` overrides the default so the
    configurable-option tests can prove the value actually drives the window.
    """
    from custom_components.adaptive_cover_pro.const import (
        VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS,
    )

    grace_mgr = GracePeriodManager(logger=MagicMock())
    policy = VenetianPolicy()
    attach_kwargs: dict = {
        "hass": MagicMock(),
        "logger": MagicMock(),
        "grace_mgr": grace_mgr,
        "get_current_position": lambda _: None,
        "set_commanded_position": lambda *_: None,
        "position_tolerance": 5,
        "is_dry_run": lambda: False,
        "get_state": lambda _eid: "stopped",
    }
    if backrotate_publish_lag_seconds is not None:
        attach_kwargs["backrotate_publish_lag_seconds"] = backrotate_publish_lag_seconds
    policy.attach(**attach_kwargs)

    seq = policy.sequencer
    assert seq is not None
    seq.stamp_position_command(entity_id)
    # Push the suppression stamp past the cap-grace tail so only publish-lag
    # remains as the suppressing predicate.
    seq._suppression_at[entity_id] -= dt.timedelta(
        seconds=VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS + 1.0
    )
    seq._stamp_settled(entity_id)
    if settled_age_seconds > 0:
        seq._settled_at[entity_id] -= dt.timedelta(seconds=settled_age_seconds)
    return policy, grace_mgr


def test_position_drift_inside_publish_lag_after_settle_is_ignored() -> None:
    """Issue #33: late position publish inside the publish-lag window must NOT trip override.

    Reproduces the user's 2026-05-26 diagnostic on
    ``cover.wohnzimmerjalousie_links``: ``set_cover_position(100)`` at T+0,
    cover state transitions ``opening`` → settled by T+8 s, then at T+64 s a
    stale ``current_position=0`` is published by the slow Somfy IO bus while
    HA cover state has reverted to ``open``. Pre-fix the position-axis
    branch had no equivalent guard to the tilt axis's
    ``is_in_suppression_with_cap`` and fired ``manual_override_set`` with
    delta=100 against effective_threshold=3.

    Post-fix, the new ``CoverTypePolicy.primary_axis_suppression`` hook
    delegates to the same publish-lag predicate the tilt axis already uses,
    so this late publish is recorded as
    ``manual_override_rejected_primary_axis_suppression`` and the cover
    stays automatic.
    """
    entity_id = "cover.wohnzimmerjalousie_links"
    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    policy, _grace_mgr = _make_policy_with_publish_lag(
        entity_id, settled_age_seconds=20.0
    )

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=0, tilt=100),
        our_state=100,
        policy=policy,
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=None,
        is_in_command_grace=lambda _eid: False,
        is_in_transit=lambda _eid: False,
    )

    assert not mgr.is_cover_manual(
        entity_id
    ), "Late position publish inside the publish-lag window must NOT trip override"
    events = mgr.get_event_buffer()
    assert any(
        evt.get("event") == "manual_override_rejected_primary_axis_suppression"
        for evt in events
    ), f"expected primary-axis suppression event, got {events}"
    counts = mgr.primary_axis_suppression_counts()
    assert (
        counts.get(entity_id, 0) == 1
    ), "expected counter to record one primary-axis suppression"


def test_position_drift_after_publish_lag_still_trips_override() -> None:
    """Once the publish-lag window has elapsed, a 100% position delta IS a user touch.

    Same setup as
    ``test_position_drift_inside_publish_lag_after_settle_is_ignored`` but
    ``_settled_at`` is aged past
    ``VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS`` so the new predicate
    returns False and the legacy threshold path runs — exactly what should
    happen when the user really did grab the cover an hour after a
    commanded move.

    NOTE: This test passes pre-fix for the WRONG reason — without the
    publish-lag guard, the position-axis path always trips for delta=100%.
    Post-fix it passes for the RIGHT reason: the guard correctly opts out
    when settled_age > publish-lag window.
    """
    from custom_components.adaptive_cover_pro.const import (
        VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS,
    )

    entity_id = "cover.wohnzimmerjalousie_links_real_user"
    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    policy, _grace_mgr = _make_policy_with_publish_lag(
        entity_id,
        settled_age_seconds=VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS + 1.0,
    )

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=0, tilt=100),
        our_state=100,
        policy=policy,
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=None,
        is_in_command_grace=lambda _eid: False,
        is_in_transit=lambda _eid: False,
    )

    assert mgr.is_cover_manual(
        entity_id
    ), "100% delta after publish-lag window must trip manual override"
    events = mgr.get_event_buffer()
    assert any(
        evt.get("event") == "manual_override_set" for evt in events
    ), f"expected manual_override_set, got {events}"
    counts = mgr.primary_axis_suppression_counts()
    assert (
        counts.get(entity_id, 0) == 0
    ), "counter must NOT increment when suppression is bypassed"


def test_configurable_publish_lag_extends_suppression_window() -> None:
    """A user-bumped publish-lag value must suppress drift inside the bigger window.

    With ``backrotate_publish_lag_seconds=90.0`` and
    ``settled_age_seconds=60.0`` we land inside the 90 s window but outside
    the default 45 s window — proving the configurable option actually
    changes the predicate's outcome. Targets the slow-KNX/Fibaro/Shelly
    user the diagnostic surfaced.
    """
    entity_id = "cover.venetian_slow_actuator"
    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    policy, _grace_mgr = _make_policy_with_publish_lag(
        entity_id,
        settled_age_seconds=60.0,
        backrotate_publish_lag_seconds=90.0,
    )

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=0, tilt=100),
        our_state=100,
        policy=policy,
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=None,
        is_in_command_grace=lambda _eid: False,
        is_in_transit=lambda _eid: False,
    )

    assert not mgr.is_cover_manual(
        entity_id
    ), "60 s late publish must be suppressed when publish-lag is bumped to 90 s"


def test_configurable_publish_lag_shrinks_suppression_window() -> None:
    """A user-tightened publish-lag value must let drift through above the smaller window.

    With ``backrotate_publish_lag_seconds=20.0`` and
    ``settled_age_seconds=30.0`` we land outside the tighter window — even
    though we'd be inside the 45 s default. Proves the option drives both
    directions, not just the bump-up case.
    """
    entity_id = "cover.venetian_fast_actuator"
    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    policy, _grace_mgr = _make_policy_with_publish_lag(
        entity_id,
        settled_age_seconds=30.0,
        backrotate_publish_lag_seconds=20.0,
    )

    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=0, tilt=100),
        our_state=100,
        policy=policy,
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=None,
        is_in_command_grace=lambda _eid: False,
        is_in_transit=lambda _eid: False,
    )

    assert mgr.is_cover_manual(
        entity_id
    ), "30 s late publish must trip override when publish-lag is dropped to 20 s"


def test_warn_log_fires_on_first_suppression_per_entity(caplog) -> None:
    """WARN log fires on the first suppression per entity per process, then ≤1 per hour.

    The log message guides the user toward the new configurable option when
    they see the suppression firing repeatedly on the same actuator. Two
    suppressions within a minute → one WARN line. A third suppression more
    than an hour later → a second WARN line.
    """
    import logging

    entity_id = "cover.warn_test"
    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    policy, _grace_mgr = _make_policy_with_publish_lag(
        entity_id, settled_age_seconds=20.0
    )

    def _drive_one_suppression() -> None:
        mgr.handle_state_change(
            states_data=_make_event(entity_id, position=0, tilt=100),
            our_state=100,
            policy=policy,
            allow_reset=True,
            is_waiting=lambda _eid: False,
            manual_threshold=None,
            is_in_command_grace=lambda _eid: False,
            is_in_transit=lambda _eid: False,
        )

    with caplog.at_level(
        logging.WARNING,
        logger="custom_components.adaptive_cover_pro.managers.manual_override",
    ):
        _drive_one_suppression()
        # Re-stamp the sequencer's settle to drive a second suppression
        # (running handle_state_change again with no fresh stamp would still
        # land in the same window; that's fine — we want the throttle path).
        policy.sequencer._stamp_settled(entity_id)
        policy.sequencer._settled_at[entity_id] -= dt.timedelta(seconds=20.0)
        _drive_one_suppression()

    suppression_warns = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING and "publish-lag" in rec.getMessage()
    ]
    assert len(suppression_warns) == 1, (
        f"expected exactly 1 WARN inside throttle window, got {len(suppression_warns)}: "
        f"{[r.getMessage() for r in suppression_warns]}"
    )

    # Advance the throttle clock past 1 h and drive a third suppression.
    last_warn = mgr._last_suppression_warn_at[entity_id]
    mgr._last_suppression_warn_at[entity_id] = last_warn - dt.timedelta(
        hours=1, seconds=1
    )
    with caplog.at_level(
        logging.WARNING,
        logger="custom_components.adaptive_cover_pro.managers.manual_override",
    ):
        caplog.clear()
        policy.sequencer._stamp_settled(entity_id)
        policy.sequencer._settled_at[entity_id] -= dt.timedelta(seconds=20.0)
        _drive_one_suppression()

    suppression_warns_after_throttle = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING and "publish-lag" in rec.getMessage()
    ]
    assert (
        len(suppression_warns_after_throttle) == 1
    ), "expected the per-hour throttle to release a second WARN line"


# ---------------------------------------------------------------------------
# Issue #927: a drift-reset endpoint publish that arrives AFTER the 5 s command
# grace expires must not be misread as a manual override. The reset drives the
# slats to a mechanical endpoint (tilt→0 for direction=close) and back; on slow
# Somfy IO/Overkiz actuators the endpoint's ``current_tilt_position=0`` publishes
# ~6-7 s later — past the command grace. A VALUE-based, tilt-axis-only
# ``excursion_match`` predicate (wired on the tilt ``SecondaryAxisCheck``, NOT
# folded into the shared position-axis suppression) recognises that stale
# endpoint publish as ACP's own excursion, while a genuine user move still trips.
# ---------------------------------------------------------------------------


async def _drive_drift_reset(
    entity_id: str,
    *,
    target: int,
    anchor: int = 0,
    reset_threshold: int = 1,
    grace_active: bool = False,
) -> tuple[DualAxisSequencer, VenetianPolicy]:
    """Run a real close-direction drift reset and return ``(seq, policy)``.

    Builds a real ``DualAxisSequencer`` wired for a close-direction reset that
    fires on essentially every send (``threshold=1``), seeds the tilt anchor so
    the accumulator crosses the threshold, then drives one tilt-only update so
    the two-step endpoint excursion (tilt→0 then back to ``target``) runs
    through the public path. Wraps the sequencer in a ``VenetianPolicy`` whose
    command grace is expired by default (``grace_active=False``) — the #927
    timeline where the endpoint publish lands ~0.6 s after grace closes. Set
    ``grace_active=True`` to model the endpoint publish landing WHILE the policy
    command-grace window is still open (finding #3).

    ``get_current_tilt_position`` is left unwired so the verify step is a no-op
    (no phantom drift pop of ``_tilt_targets``) and the drift accumulator
    anchors on the seeded stored target — the same pattern
    ``test_venetian_drift_reset.py`` uses.
    """
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    seq = DualAxisSequencer(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=lambda _eid: None,
        set_commanded_position=lambda *_: None,
        position_tolerance=5,
        is_dry_run=lambda: False,
        get_tilt_reset_threshold=lambda: reset_threshold,
        get_tilt_reset_direction=lambda: VENETIAN_TILT_RESET_CLOSE,
    )
    seq._tilt_targets[entity_id] = anchor  # seed accumulator anchor
    await seq.update_tilt_only(
        entity_id, tilt_target=target, current_position=50, reason="solar"
    )

    grace_mgr = MagicMock()
    grace_mgr.is_in_command_grace_period = lambda _eid: grace_active
    policy = VenetianPolicy()
    policy._sequencer = seq
    policy._grace_mgr = grace_mgr
    return seq, policy


def _secondary_check(policy: VenetianPolicy, expected: int) -> SecondaryAxisCheck:
    """Build the tilt ``SecondaryAxisCheck`` via the production policy path.

    Going through ``policy.secondary_axis_check`` (rather than constructing the
    check by hand) exercises the real wiring — ``suppression`` AND the #927
    ``excursion_match`` — so these tests break if either is mis-wired.
    """
    return policy.secondary_axis_check(SimpleNamespace(tilt=expected), None)


async def test_drift_reset_endpoint_publish_after_grace_is_suppressed() -> None:
    """The reporter's #927 timeline: stale tilt→0 endpoint publish must NOT trip.

    A close-direction reset drives the slats to tilt 0 and restores 79; the
    endpoint's ``current_tilt_position=0`` publishes ~7 s later, past the 5 s
    command grace. Pre-fix (no excursion mechanism) ``evaluate`` sees delta=79%
    and fires ``manual_override_set``. Post-fix ``excursion_match`` recognises
    the value-matched endpoint publish inside the excursion window and
    suppresses it.
    """
    entity_id = "cover.og_studio_tur"
    seq, policy = await _drive_drift_reset(entity_id, target=79)

    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=50, tilt=0),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=2,
        secondary_axis_check=_secondary_check(policy, 79),
    )

    assert not mgr.is_cover_manual(
        entity_id
    ), "stale drift-reset endpoint publish after grace must NOT trip manual override"


async def test_user_tilt_move_to_endpoint_without_reset_trips_override() -> None:
    """A genuine user move to tilt 0 with NO drift reset must still trip override.

    Same wiring as the repro but ``threshold=0`` so no reset fires and no
    excursion is stamped. The tilt=0 publish is then a real user grab and must
    set manual override — proving the suppression is scoped to ACP's own
    excursion, not a blanket widening.
    """
    entity_id = "cover.og_studio_tur_user"
    seq, policy = await _drive_drift_reset(entity_id, target=79, reset_threshold=0)
    assert entity_id not in seq._reset_excursion

    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=50, tilt=0),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=2,
        secondary_axis_check=_secondary_check(policy, 79),
    )

    assert mgr.is_cover_manual(
        entity_id
    ), "a real user move to the endpoint with no reset must trip manual override"


async def test_user_tilt_move_to_mirror_value_during_window_trips_override() -> None:
    """Finding #1: a user move to the MIRROR value during the window must trip.

    Close reset from anchor 0 to target 35 stamps an endpoint-0 excursion. The
    old delta-based predicate matched any publish whose ``|expected − value|``
    equalled the excursion delta (35), so a genuine user move to the mirror
    value ``2·35 − 0 = 70`` (delta 35) was wrongly swallowed and burned the
    one-shot. The value-based predicate matches only value≈0, so the move to
    tilt 70 trips override.
    """
    entity_id = "cover.og_studio_tur_mirror"
    seq, policy = await _drive_drift_reset(entity_id, target=35)

    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=50, tilt=70),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=2,
        secondary_axis_check=_secondary_check(policy, 35),
    )

    assert mgr.is_cover_manual(
        entity_id
    ), "a user tilt move to the mirror value must trip, not be swallowed (finding #1)"


async def test_position_axis_move_during_window_trips_override() -> None:
    """Finding #2: the POSITION axis must not consult the tilt excursion.

    After a close reset (endpoint-0 excursion, delta 35), the tilt reports
    on-target (35) so the secondary axis falls through, and a genuine POSITION
    move (our_state 50 → current_position 15, delta 35) must trip. The old
    design OR'd the excursion into the shared ``primary_axis_suppression`` that
    the position axis probes, so this position move — coincidentally matching
    the excursion delta — was suppressed and the tilt one-shot burned.
    """
    entity_id = "cover.og_studio_tur_pos"
    seq, policy = await _drive_drift_reset(entity_id, target=35)

    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    # Pass the WIRED policy so the position axis consults the same
    # ``primary_axis_suppression`` the sequencer backs — this is the path
    # finding #2 lives on (the old design folded the excursion into it).
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=15, tilt=35),
        our_state=50,
        policy=policy,
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=2,
        secondary_axis_check=_secondary_check(policy, 35),
    )

    assert mgr.is_cover_manual(entity_id), (
        "a genuine position-axis move must trip; the position axis must not "
        "consult the tilt excursion (finding #2)"
    )


async def test_endpoint_publish_inside_grace_marks_record_then_target_return_consumes() -> (
    None
):
    """#930 finding 3: an endpoint publish inside grace MARKS; target return consumes.

    Mirrors production: the coordinator wires ``is_in_command_grace`` to the
    SAME ``GracePeriodManager`` the policy's excursion window uses. On a fast
    actuator the endpoint publish lands WHILE that command-grace window is still
    open, so ``handle_state_change`` returns early in the grace-reject branch
    BEFORE ``secondary_axis_check.evaluate`` ever runs. The grace-reject branch
    still advances the excursion trajectory via the generic
    ``SecondaryAxisCheck.consume_excursion``: the endpoint publish marks the
    record (it survives the return leg), and a later publish back on the target
    consumes it. A genuine user move to the endpoint after the whole trajectory
    then trips.
    """
    entity_id = "cover.og_studio_tur_grace"
    seq, policy = await _drive_drift_reset(entity_id, target=79, grace_active=True)
    assert entity_id in seq._reset_excursion

    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    # In-grace endpoint publish: command grace is active (as production wires it
    # off the same GracePeriodManager), so the grace-reject branch fires first
    # and marks (not consumes) the excursion record.
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=50, tilt=0),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=2,
        secondary_axis_check=_secondary_check(policy, 79),
        is_in_command_grace=lambda _eid: True,
    )
    assert not mgr.is_cover_manual(entity_id)
    assert entity_id in seq._reset_excursion
    assert seq._reset_excursion[entity_id][0].endpoint_seen is True

    # In-grace target-return publish (tilt back to 79) consumes the record.
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=50, tilt=79),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=2,
        secondary_axis_check=_secondary_check(policy, 79),
        is_in_command_grace=lambda _eid: True,
    )
    assert not mgr.is_cover_manual(entity_id)
    assert entity_id not in seq._reset_excursion

    # After grace expires, a genuine user tilt-to-endpoint move (no stamp left)
    # must still trip override — proving the trajectory tracking did not disable
    # detection. Flip the policy's command grace off too (the suppression
    # callback consults it), mirroring the window having closed.
    policy._grace_mgr.is_in_command_grace_period = lambda _eid: False
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=50, tilt=0),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=2,
        secondary_axis_check=_secondary_check(policy, 79),
        is_in_command_grace=lambda _eid: False,
    )
    assert mgr.is_cover_manual(
        entity_id
    ), "a genuine user move to the endpoint after grace must still trip override"


async def test_endpoint_publish_during_wait_for_target_marks_record_then_target_return_consumes() -> (
    None
):
    """Case 2 (#930): an endpoint publish inside wait_for_target marks; target return consumes.

    The ``is_waiting`` gate returns early — like the command-grace gate — BEFORE
    ``secondary_axis_check.evaluate`` runs. When a position command is still
    awaiting its target as the drift-reset endpoint publish lands, the
    grace-reject branch advances the excursion trajectory: the endpoint publish
    marks the record (retained through the return leg), and a later publish back
    on the target consumes it. Symmetric with
    ``test_endpoint_publish_inside_grace_marks_record_then_target_return_consumes``.
    """
    entity_id = "cover.og_studio_tur_wait"
    seq, policy = await _drive_drift_reset(entity_id, target=79)
    assert entity_id in seq._reset_excursion

    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    # In-flight position command: wait_for_target active — the is_waiting branch
    # fires first and marks (not consumes) the excursion record.
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=50, tilt=0),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: True,
        manual_threshold=2,
        secondary_axis_check=_secondary_check(policy, 79),
    )
    assert not mgr.is_cover_manual(entity_id)
    assert entity_id in seq._reset_excursion
    assert seq._reset_excursion[entity_id][0].endpoint_seen is True

    # Target-return publish while still waiting consumes the record.
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=50, tilt=79),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: True,
        manual_threshold=2,
        secondary_axis_check=_secondary_check(policy, 79),
    )
    assert not mgr.is_cover_manual(entity_id)
    assert entity_id not in seq._reset_excursion

    # Once the command settles (is_waiting False, no stamp left), a genuine user
    # tilt-to-endpoint move must still trip override.
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=50, tilt=0),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=2,
        secondary_axis_check=_secondary_check(policy, 79),
    )
    assert mgr.is_cover_manual(
        entity_id
    ), "a genuine user move to the endpoint after wait_for_target must still trip"


async def test_endpoint_publish_after_excursion_window_trips_override() -> None:
    """A matching endpoint publish arriving AFTER the excursion window trips.

    The reset stamps the excursion, but the late publish only lands after the
    publish-lag-sized window elapses. Backdating the excursion stamp past the
    window closes the one-shot suppression, so the tilt=0 event is treated as a
    user move.
    """
    entity_id = "cover.og_studio_tur_late"
    seq, policy = await _drive_drift_reset(entity_id, target=79)
    # Backdate the excursion stamp past the publish-lag-sized window.
    seq._reset_excursion[entity_id] = [
        dataclasses.replace(
            r,
            at=r.at - dt.timedelta(seconds=seq._backrotate_publish_lag_seconds + 1.0),
        )
        for r in seq._reset_excursion[entity_id]
    ]

    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=50, tilt=0),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=2,
        secondary_axis_check=_secondary_check(policy, 79),
    )

    assert mgr.is_cover_manual(
        entity_id
    ), "endpoint publish after the excursion window must trip manual override"


async def test_drift_reset_intermediate_publish_does_not_trip_override() -> None:
    """Case 1 (#930): a mid-excursion INTERMEDIATE tilt publish must NOT trip.

    A close reset to target 41 drives the slats 41→0→41. On a slow actuator an
    intermediate ``current_tilt_position=9`` publishes mid-travel — delta 32
    from the 41 target, well over the threshold. The traversal-band suppression
    recognises it as ACP's own excursion. The endpoint publish then marks the
    record (still retained, not burned); only a publish landing back on the
    target (41) consumes it.
    """
    entity_id = "cover.og_studio_tur_intermediate"
    seq, policy = await _drive_drift_reset(entity_id, target=41)

    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=50, tilt=9),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=2,
        secondary_axis_check=_secondary_check(policy, 41),
    )
    assert not mgr.is_cover_manual(
        entity_id
    ), "an intermediate mid-excursion tilt publish must NOT trip manual override"

    # The endpoint publish marks the record; it is RETAINED, not consumed, so it
    # still protects the return leg (#930).
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=50, tilt=0),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=2,
        secondary_axis_check=_secondary_check(policy, 41),
    )
    assert not mgr.is_cover_manual(entity_id)
    assert entity_id in seq._reset_excursion

    # The target-return publish (tilt back to 41) consumes the record.
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=50, tilt=41),
        our_state=50,
        policy=get_policy("cover_venetian"),
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=2,
        secondary_axis_check=_secondary_check(policy, 41),
    )
    assert not mgr.is_cover_manual(entity_id)
    assert entity_id not in seq._reset_excursion


async def test_return_leg_intermediate_after_endpoint_publish_does_not_trip_override() -> (
    None
):
    """#930 finding 1: an intermediate on the RETURN leg must NOT trip override.

    A close reset to target 35 drives the slats 35→0→35. The endpoint publish
    (tilt 0) marks the record; then on the return leg an intermediate tilt-28
    publish lands (delta 7 from the 35 target, over the threshold). Because the
    record survives until the target-return consumes it, the return-leg
    intermediate is recognised as ACP's own move. Pre-#930 the endpoint publish
    burned the one-shot, so this return-leg intermediate tripped a false
    override.
    """
    entity_id = "cover.og_studio_tur_return"
    seq, policy = await _drive_drift_reset(entity_id, target=35)

    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)

    def _drive(tilt: int) -> None:
        mgr.handle_state_change(
            states_data=_make_event(entity_id, position=50, tilt=tilt),
            our_state=50,
            policy=get_policy("cover_venetian"),
            allow_reset=True,
            is_waiting=lambda _eid: False,
            manual_threshold=2,
            secondary_axis_check=_secondary_check(policy, 35),
        )

    # Endpoint publish marks the record.
    _drive(0)
    assert not mgr.is_cover_manual(entity_id)
    # Return-leg intermediate — must NOT trip (pre-#930 it did).
    _drive(28)
    assert not mgr.is_cover_manual(entity_id)
    # Target return consumes the record.
    _drive(35)
    assert not mgr.is_cover_manual(entity_id)
    assert entity_id not in seq._reset_excursion

    # A genuine user move to the endpoint after the trajectory completes trips.
    _drive(0)
    assert mgr.is_cover_manual(entity_id)


async def test_near_target_tilt_publish_position_axis_still_evaluated() -> None:
    """#930 finding 2: a near-target tilt publish keeps the position axis observable.

    After a close reset to target 35, a tilt publish of 34 is within tolerance
    of the target, so the excursion predicate falls through (rather than
    swallowing both axes as an in-band intermediate). With the tilt delta below
    threshold, a simultaneous genuine POSITION move (our_state 50 →
    current_position 15) must still trip manual override. Pre-#930 the near-
    target value was swallowed by the band rule and the position move was lost.
    """
    entity_id = "cover.og_studio_tur_near_target"
    seq, policy = await _drive_drift_reset(entity_id, target=35)

    mgr = _make_manager(entity_id)
    mgr.hass.states.get = MagicMock(return_value=None)
    # Pass the WIRED policy so the position axis consults the sequencer-backed
    # ``primary_axis_suppression`` (which must NOT swallow this move).
    mgr.handle_state_change(
        states_data=_make_event(entity_id, position=15, tilt=34),
        our_state=50,
        policy=policy,
        allow_reset=True,
        is_waiting=lambda _eid: False,
        manual_threshold=2,
        secondary_axis_check=_secondary_check(policy, 35),
    )

    assert mgr.is_cover_manual(entity_id), (
        "a near-target tilt publish must fall through so a genuine position "
        "move still trips (finding #2)"
    )
