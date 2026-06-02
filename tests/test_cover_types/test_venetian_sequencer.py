"""Unit tests for ``DualAxisSequencer``.

The sequencer owns:
- the venetian tilt-axis suppression window (``stamp_position_command`` /
  ``is_in_suppression``), and
- the post-position settle loop + ``set_cover_tilt_position`` call
  (``run_sequence``).

These tests exercise it in isolation — the integration with
``CoverCommandService.apply_position`` is covered in
``tests/test_cover_command_venetian.py``.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS,
    VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS,
    VENETIAN_TILT_SUPPRESSION_SECONDS,
)
from custom_components.adaptive_cover_pro.diagnostics.event_buffer import EventBuffer
from custom_components.adaptive_cover_pro.cover_types.venetian.sequencer import (
    DualAxisSequencer,
)


@pytest.fixture(autouse=True)
def _zero_post_tilt_delay(monkeypatch):
    """Skip real-motor delays in unit tests.

    Zeroes the post-tilt rebase delay (1.5 s) and the verify-retry poll
    interval (1.0 s) so the test suite doesn't spend real time waiting on
    asyncio.sleep. The post-settle hold is a per-instance parameter
    (``post_settle_hold_seconds``) defaulting to 0 in ``_build_sequencer``
    — no monkeypatching needed for that delay. The retry sample COUNT
    (``VENETIAN_TILT_VERIFY_MAX_SAMPLES``) is left at its production value
    because it is the behaviour under test.
    """
    monkeypatch.setattr(
        "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer."
        "VENETIAN_POST_TILT_REBASE_DELAY_SECONDS",
        0,
    )
    monkeypatch.setattr(
        "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer."
        "VENETIAN_TILT_VERIFY_POLL_SECONDS",
        0,
    )


def _build_sequencer(
    *,
    current_positions=None,
    dry_run=False,
    set_commanded_position=None,
    get_state=None,
    get_current_tilt_position=None,
    event_buffer=None,
    invert_tilt=None,
    get_min_change=None,
    post_settle_hold_seconds: float = 0,
):
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    if current_positions is None:
        current_positions = []
    iter_positions = iter(current_positions)
    if set_commanded_position is None:
        set_commanded_position = lambda *_: None  # noqa: E731
    return (
        hass,
        DualAxisSequencer(
            hass=hass,
            logger=MagicMock(),
            grace_mgr=MagicMock(),
            get_current_position=lambda _eid: next(iter_positions, None),
            set_commanded_position=set_commanded_position,
            position_tolerance=5,
            is_dry_run=lambda: dry_run,
            get_state=get_state,
            get_current_tilt_position=get_current_tilt_position,
            event_buffer=event_buffer,
            invert_tilt=invert_tilt,
            get_min_change=get_min_change,
            post_settle_hold_seconds=post_settle_hold_seconds,
        ),
    )


@pytest.mark.unit
class TestSuppressionWindow:
    """``stamp_position_command`` and ``is_in_suppression`` mediate the back-rotate window."""

    def test_no_stamp_means_not_suppressed(self):
        _, seq = _build_sequencer()
        assert seq.is_in_suppression("cover.x") is False

    def test_fresh_stamp_is_suppressed(self):
        _, seq = _build_sequencer()
        seq.stamp_position_command("cover.x")
        assert seq.is_in_suppression("cover.x") is True

    def test_stale_stamp_expires(self):
        _, seq = _build_sequencer()
        seq._suppression_at["cover.x"] = dt.datetime.now(dt.UTC) - dt.timedelta(
            seconds=VENETIAN_TILT_SUPPRESSION_SECONDS + 1
        )
        assert seq.is_in_suppression("cover.x") is False


@pytest.mark.unit
class TestSuppressionDeltaCap:
    """``is_in_suppression_with_cap`` adds a delta gate on top of the window.

    The cap is the venetian-side policy that protects against user moves
    inside the back-rotate window from being silently swallowed as motor drift
    (issue #33 follow-on).
    """

    def test_suppressed_small_delta_returns_true(self):
        _, seq = _build_sequencer()
        seq.stamp_position_command("cover.x")
        assert seq.is_in_suppression_with_cap("cover.x", delta=10.0) is True

    def test_suppressed_large_delta_past_grace_returns_false(self):
        """Past both the cap-grace and publish-lag windows, the cap reasserts.

        The publish-lag window (issue #33 Track A) was added in front of the
        legacy cap path. To reach the cap-reasserts behavior the test
        backdates both ``_suppression_at`` past the cap grace and
        ``_settled_at`` past the publish lag.
        """
        from custom_components.adaptive_cover_pro.const import (
            VENETIAN_BACKROTATE_MAX_DELTA_PERCENT,
        )

        _, seq = _build_sequencer()
        seq.stamp_position_command("cover.x")
        seq._suppression_at["cover.x"] = dt.datetime.now(dt.UTC) - dt.timedelta(
            seconds=VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS + 1.0
        )
        seq._settled_at["cover.x"] = dt.datetime.now(dt.UTC) - dt.timedelta(
            seconds=VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS + 1.0
        )
        big = VENETIAN_BACKROTATE_MAX_DELTA_PERCENT + 1
        assert seq.is_in_suppression_with_cap("cover.x", delta=float(big)) is False

    def test_suppressed_delta_at_cap_boundary_returns_true(self):
        from custom_components.adaptive_cover_pro.const import (
            VENETIAN_BACKROTATE_MAX_DELTA_PERCENT,
        )

        _, seq = _build_sequencer()
        seq.stamp_position_command("cover.x")
        # Cap is inclusive — delta == cap is still motor back-drive.
        assert (
            seq.is_in_suppression_with_cap(
                "cover.x", delta=float(VENETIAN_BACKROTATE_MAX_DELTA_PERCENT)
            )
            is True
        )

    def test_no_stamp_means_not_suppressed_regardless_of_delta(self):
        _, seq = _build_sequencer()
        assert seq.is_in_suppression_with_cap("cover.x", delta=1.0) is False

    def test_stale_stamp_expires_regardless_of_delta(self):
        _, seq = _build_sequencer()
        seq._suppression_at["cover.x"] = dt.datetime.now(dt.UTC) - dt.timedelta(
            seconds=VENETIAN_TILT_SUPPRESSION_SECONDS + 1
        )
        assert seq.is_in_suppression_with_cap("cover.x", delta=1.0) is False

    def test_suppressed_large_delta_with_settled_state_inside_grace_returns_true(
        self,
    ) -> None:
        """Large delta inside the post-settle grace window should suppress.

        Regression for issue #33: real KNX/Shelly actuators publish tilt-walk
        bursts AFTER ``cover.state`` has already settled to "open". Without a
        grace tail, the cap rejects deltas >30 even microseconds after the
        carriage settles, latching false manual override.
        """
        _, seq = _build_sequencer(get_state=lambda _eid: "open")
        seq.stamp_position_command("cover.x")
        assert seq.is_in_suppression_with_cap("cover.x", delta=100.0) is True

    def test_suppressed_large_delta_with_settled_state_past_grace_returns_false(
        self,
    ) -> None:
        """Once both the cap-grace and publish-lag windows expire, the cap reasserts.

        The publish-lag window (issue #33 Track A) was added in front of the
        legacy cap path. Both ``_suppression_at`` (cap-grace anchor) and
        ``_settled_at`` (publish-lag anchor) must be backdated for the cap
        path to run.
        """
        _, seq = _build_sequencer(get_state=lambda _eid: "open")
        seq.stamp_position_command("cover.x")
        seq._suppression_at["cover.x"] = dt.datetime.now(dt.UTC) - dt.timedelta(
            seconds=VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS + 1.0
        )
        seq._settled_at["cover.x"] = dt.datetime.now(dt.UTC) - dt.timedelta(
            seconds=VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS + 1.0
        )
        assert seq.is_in_suppression_with_cap("cover.x", delta=100.0) is False

    def test_suppressed_large_delta_while_cover_moving_returns_true(self) -> None:
        """In-motion bypass: any delta is motor drift while cover.state is moving."""
        _, seq = _build_sequencer(get_state=lambda _eid: "closing")
        seq.stamp_position_command("cover.x")
        assert seq.is_in_suppression_with_cap("cover.x", delta=100.0) is True

    def test_suppressed_small_delta_settled_state_past_grace_returns_true(self) -> None:
        """Cap path still suppresses sub-cap deltas after grace expires."""
        _, seq = _build_sequencer(get_state=lambda _eid: "open")
        seq.stamp_position_command("cover.x")
        seq._suppression_at["cover.x"] = dt.datetime.now(dt.UTC) - dt.timedelta(
            seconds=VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS + 1.0
        )
        assert seq.is_in_suppression_with_cap("cover.x", delta=10.0) is True

    def test_large_delta_inside_publish_lag_after_settled_returns_true(self) -> None:
        """Inside the post-settle publish-lag window, any delta is motor drift.

        Track A in issue #33: Somfy IO actuators republish their back-rotate
        tilt burst tens of seconds after ``cover.state`` reports settled —
        well past the small (5 s) post-settle cap grace. Anchoring a longer
        publish-lag window to the ``moving → settled`` transition the
        sequencer observed in its settle loop bypasses the back-rotate cap
        for that full window, so the late burst doesn't latch false manual
        override.
        """
        _, seq = _build_sequencer(get_state=lambda _eid: "open")
        seq.stamp_position_command("cover.x")
        # Backdate the stamp past the existing cap-grace so only the new
        # publish-lag window can save us.
        seq._suppression_at["cover.x"] = dt.datetime.now(dt.UTC) - dt.timedelta(
            seconds=VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS + 1.0
        )
        # The sequencer just observed moving→settled.
        seq._settled_at["cover.x"] = dt.datetime.now(dt.UTC)
        assert seq.is_in_suppression_with_cap("cover.x", delta=95.0) is True

    def test_large_delta_past_publish_lag_after_settled_returns_false(self) -> None:
        """Once the publish-lag window elapses, the cap reasserts.

        Counterpart to ``test_large_delta_inside_publish_lag_after_settled_returns_true``:
        backdating ``_settled_at`` past the publish-lag window puts a large
        delta firmly in user-touch territory. A sub-cap delta still passes
        the legacy cap path.
        """
        _, seq = _build_sequencer(get_state=lambda _eid: "open")
        seq.stamp_position_command("cover.x")
        seq._suppression_at["cover.x"] = dt.datetime.now(dt.UTC) - dt.timedelta(
            seconds=VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS + 1.0
        )
        seq._settled_at["cover.x"] = dt.datetime.now(dt.UTC) - dt.timedelta(
            seconds=VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS + 1.0
        )
        assert seq.is_in_suppression_with_cap("cover.x", delta=95.0) is False
        # Sub-cap delta still suppressed via the cap path.
        assert seq.is_in_suppression_with_cap("cover.x", delta=10.0) is True

    def test_publish_lag_clears_on_next_position_command(self) -> None:
        """``stamp_position_command`` clears ``_settled_at`` for a fresh cycle.

        Without the clear, an old settle stamp from a previous cycle would
        leak its publish-lag window into a new command — letting a true
        user touch on the next cycle get swallowed as motor drift.
        """
        _, seq = _build_sequencer(get_state=lambda _eid: "open")
        seq.stamp_position_command("cover.x")
        seq._suppression_at["cover.x"] = dt.datetime.now(dt.UTC) - dt.timedelta(
            seconds=VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS + 1.0
        )
        # Lazy-write path: the cap query stamps _settled_at opportunistically.
        seq.is_in_suppression_with_cap("cover.x", delta=10.0)
        assert "cover.x" in seq._settled_at
        # A new position command starts a fresh cycle; the prior settle
        # stamp must be forgotten.
        seq.stamp_position_command("cover.x")
        assert "cover.x" not in seq._settled_at


@pytest.mark.asyncio
class TestSettleAndTilt:
    """Settle-loop and tilt-service-call branches of ``run_sequence``."""

    async def test_settle_returns_when_target_reached(self):
        # Sequence: 80 (off-target), 50 (within 5%-tolerance) → reached.
        _, seq = _build_sequencer(current_positions=[80, 50])
        reached, last = await seq._wait_for_position_settle("cover.x", target=50)
        assert reached is True
        assert last == 50

    async def test_settle_bails_on_unavailable(self):
        _, seq = _build_sequencer(current_positions=[None])
        reached, last = await seq._wait_for_position_settle("cover.x", target=50)
        assert reached is False
        assert last is None

    async def test_run_sequence_emits_tilt_after_settle(self):
        hass, seq = _build_sequencer(current_positions=[60])
        seq._wait_for_position_settle = AsyncMock(return_value=(True, 60))
        await seq.run_sequence(
            "cover.x", position_target=60, tilt_target=80, reason="solar"
        )
        assert hass.services.async_call.call_count == 1
        called = hass.services.async_call.call_args.args
        assert called[1] == "set_cover_tilt_position"
        assert called[2]["tilt_position"] == 80

    async def test_run_sequence_records_last_tilt_target(self):
        _, seq = _build_sequencer()
        seq._wait_for_position_settle = AsyncMock(return_value=(True, 60))
        await seq.run_sequence(
            "cover.x", position_target=60, tilt_target=80, reason="solar"
        )
        assert seq.last_tilt_target("cover.x") == 80

    async def test_dry_run_skips_service_call(self):
        hass, seq = _build_sequencer(dry_run=True)
        seq._wait_for_position_settle = AsyncMock(return_value=(True, 60))
        await seq.run_sequence(
            "cover.x", position_target=60, tilt_target=80, reason="solar"
        )
        assert hass.services.async_call.call_count == 0

    async def test_run_sequence_stamps_settled_at_after_wait_returns(self):
        """``run_sequence`` deterministically stamps the moving→settled anchor.

        After ``_wait_for_position_settle`` returns, the sequencer must
        stamp ``_settled_at`` to anchor the publish-lag window (issue #33
        Track A). Without this deterministic stamp the window would rely
        solely on the lazy-write in ``is_in_suppression_with_cap``, which
        only fires if/when a manual-override query happens to land at the
        right moment.
        """
        _, seq = _build_sequencer()
        seq._wait_for_position_settle = AsyncMock(return_value=(True, 60))
        assert "cover.x" not in seq._settled_at
        before = dt.datetime.now(dt.UTC)
        await seq.run_sequence(
            "cover.x", position_target=60, tilt_target=80, reason="solar"
        )
        after = dt.datetime.now(dt.UTC)
        assert "cover.x" in seq._settled_at
        assert before <= seq._settled_at["cover.x"] <= after

    async def test_settle_does_not_stall_during_startup_grace_when_state_never_moves(
        self, monkeypatch
    ):
        """Startup grace must block stall declaration before motor begins to travel.

        Somfy IO motors take 3-5 s to begin physical travel after the service
        call. During that window cover.state still reads "open" and
        current_position is unchanged. Without the startup grace, the
        no-progress counter trips after 3 samples and the loop declares stall
        20-30 s before the cover actually stops moving — starting the
        publish-lag clock far too early (issue #33 Track B).
        """
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"
            ".VENETIAN_POSITION_SETTLE_POLL_SECONDS",
            0,
        )
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"
            ".VENETIAN_POSITION_SETTLE_STARTUP_GRACE_SECONDS",
            0.05,
        )
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"
            ".VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS",
            0.2,
        )
        _, seq = _build_sequencer(get_state=lambda _eid: "open")
        seq._get_current_position = MagicMock(return_value=100)

        reached, last = await seq._wait_for_position_settle("cover.x", target=9)

        assert reached is False
        # Pre-fix returns after 4 samples (poll 1 sets last=100, polls 2-4
        # each tick unchanged_samples up to 3 → stall). With the startup
        # grace the loop must keep polling past the unchanged-sample
        # threshold until the wall-clock grace elapses (0.05 s) or the
        # timeout fires (0.2 s). Either way that's strictly more than 4
        # samples.
        assert seq._get_current_position.call_count > 4

    async def test_settle_does_not_stall_after_motion_observed(self, monkeypatch):
        """Once motion is observed, the unchanged-sample stall counter is active again.

        State cycles ``open → closing → closing → open → open → open``; the
        gate must let the post-motion 3-unchanged-sample stall fire. Without
        a motion-observed flag, the same gate that protects pre-motion startup
        would suppress all post-motion stalls and the loop would only ever
        bail on the 60 s timeout.
        """
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"
            ".VENETIAN_POSITION_SETTLE_POLL_SECONDS",
            0,
        )
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"
            ".VENETIAN_POSITION_SETTLE_STARTUP_GRACE_SECONDS",
            0,
        )
        state_seq = iter(["open", "closing", "closing", "open", "open", "open"])
        _, seq = _build_sequencer(
            get_state=lambda _eid: next(state_seq, "open"),
        )
        seq._get_current_position = MagicMock(side_effect=[100, 100, 95, 95, 95, 95])

        reached, last = await seq._wait_for_position_settle("cover.x", target=9)

        assert reached is False
        assert last == 95
        # poll 1 (open, 100, last=None reset), poll 2 (closing, 100, moving
        # reset), poll 3 (closing, 95, moving reset), poll 4 (open, 95,
        # unchanged=1), poll 5 (open, 95, unchanged=2), poll 6 (open, 95,
        # unchanged=3 → stall).
        assert seq._get_current_position.call_count >= 6

    async def test_settle_does_not_stall_during_startup_grace_no_state_callback(
        self, monkeypatch
    ):
        """No ``get_state`` callback: startup grace still gates on wall-clock elapsed.

        Backwards-compat path used by tests and non-venetian callers that
        construct DualAxisSequencer without a state callback. The startup
        grace must still apply, anchored purely on wall-clock time since the
        loop began — otherwise a slow-starting motor's pre-motion samples
        would prematurely declare stall.
        """
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"
            ".VENETIAN_POSITION_SETTLE_POLL_SECONDS",
            0,
        )
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"
            ".VENETIAN_POSITION_SETTLE_STARTUP_GRACE_SECONDS",
            0.05,
        )
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"
            ".VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS",
            0.2,
        )
        _, seq = _build_sequencer()  # no get_state
        seq._get_current_position = MagicMock(return_value=60)

        reached, last = await seq._wait_for_position_settle("cover.x", target=9)

        assert reached is False
        assert seq._get_current_position.call_count > 4


@pytest.mark.asyncio
class TestPostTiltRebase:
    """After a successful tilt command, the commanded position is rebased to the
    actual post-tilt position so reconciliation sees zero drift.
    """

    async def test_rebases_commanded_position_to_actual_post_tilt(self):
        """After tilt, set_commanded_position is called with the actual position."""
        set_cmd_pos = MagicMock()
        # position_target=50, post-tilt actual=56 → |delta|=6 > tolerance(5).
        _, seq = _build_sequencer(set_commanded_position=set_cmd_pos)
        seq._get_current_position = lambda _eid: 56
        seq._wait_for_position_settle = AsyncMock(return_value=(True, 50))
        await seq.run_sequence(
            "cover.x", position_target=50, tilt_target=80, reason="solar"
        )
        set_cmd_pos.assert_called_once_with("cover.x", 56)

    async def test_does_not_rebase_when_post_tilt_position_none(self):
        """If current_position is unavailable after tilt, skip the rebase."""
        set_cmd_pos = MagicMock()
        _, seq = _build_sequencer(set_commanded_position=set_cmd_pos)
        seq._get_current_position = lambda _eid: None
        seq._wait_for_position_settle = AsyncMock(return_value=(True, 50))
        await seq.run_sequence(
            "cover.x", position_target=50, tilt_target=80, reason="solar"
        )
        set_cmd_pos.assert_not_called()

    async def test_does_not_rebase_when_drift_within_tolerance(self):
        """Drift of 2% (≤ tolerance of 5%) should not trigger a rebase."""
        set_cmd_pos = MagicMock()
        _, seq = _build_sequencer(set_commanded_position=set_cmd_pos)
        # position_target=50, actual=52 → |delta|=2 ≤ 5
        seq._get_current_position = lambda _eid: 52
        seq._wait_for_position_settle = AsyncMock(return_value=(True, 50))
        await seq.run_sequence(
            "cover.x", position_target=50, tilt_target=80, reason="solar"
        )
        set_cmd_pos.assert_not_called()

    async def test_rebase_reads_position_after_post_tilt_delay(self, monkeypatch):
        """A delay must occur between the tilt service call and the position rebase.

        Without this delay the rebase reads current_position immediately after
        set_cover_tilt_position returns. For async motors (Shelly/KNX/Somfy) the
        mechanical back-drive happens AFTER the service call returns, so the
        immediate read sees the pre-back-drive value and the rebase is skipped.
        The fix is asyncio.sleep(VENETIAN_POST_TILT_REBASE_DELAY_SECONDS) between
        the tilt call and the rebase so the motor has time to settle first.
        """
        sleep_calls: list[float] = []

        async def _capture_sleep(delay):
            sleep_calls.append(delay)

        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer.asyncio.sleep",
            _capture_sleep,
        )

        set_cmd_pos = MagicMock()
        _, seq = _build_sequencer(set_commanded_position=set_cmd_pos)
        seq._get_current_position = lambda _eid: 56
        seq._wait_for_position_settle = AsyncMock(return_value=(True, 50))

        await seq.run_sequence(
            "cover.x", position_target=50, tilt_target=80, reason="solar"
        )

        assert sleep_calls, (
            "asyncio.sleep was not called after the tilt service call — "
            "post-tilt rebase delay is missing"
        )

    async def test_post_settle_hold_delay_observed_before_tilt_command(
        self, monkeypatch
    ):
        """A post-settle hold sleep must occur BEFORE the tilt service call in run_sequence.

        Without this hold, the tilt command races mechanical vibration left over
        from the position motor on real hardware (Somfy IO, KNX, Shelly 2PM).
        The test verifies that asyncio.sleep is called with the hold duration
        before the first tilt service call, NOT only after it.
        """
        sleep_calls: list[float] = []
        service_call_count = [0]

        async def _capture_sleep(delay):
            sleep_calls.append(delay)

        async def _record_service_call(*args, **kwargs):
            service_call_count[0] += 1

        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer.asyncio.sleep",
            _capture_sleep,
        )

        set_cmd_pos = MagicMock()
        # post_settle_hold_seconds=0.5 is injected via the per-instance kwarg — the
        # autouse fixture no longer needs to monkeypatch a module constant.
        hass, seq = _build_sequencer(
            set_commanded_position=set_cmd_pos, post_settle_hold_seconds=0.5
        )
        hass.services.async_call = AsyncMock(side_effect=_record_service_call)
        seq._get_current_position = lambda _eid: 60
        seq._wait_for_position_settle = AsyncMock(return_value=(True, 60))

        await seq.run_sequence(
            "cover.x", position_target=60, tilt_target=80, reason="solar"
        )

        # The 0.5 s post-settle hold must appear in sleep_calls BEFORE the first
        # tilt service call. We check this by verifying 0.5 is present and that
        # the service call was actually made (i.e. this isn't a dry-run test).
        assert service_call_count[0] == 1, "tilt service call was never made"
        assert (
            0.5 in sleep_calls
        ), "asyncio.sleep(0.5) was not called — post-settle hold is missing from run_sequence"
        # The 0.5 s hold must come BEFORE the first service call, so it must be
        # the first element in sleep_calls (the post-tilt rebase delay is zeroed
        # by the autouse fixture, leaving only the 0.5 hold).
        assert (
            sleep_calls[0] == 0.5
        ), f"Expected post-settle hold (0.5 s) to be first sleep, got {sleep_calls}"

    async def test_settle_timeout_with_user_open_does_not_rebase(self, monkeypatch):
        """Issue #33 follow-on regression seal: settle timeout + user-open does not strand the target.

        Reproduces the diagnostic timeline from
        ``/tmp/issue33-last-diag.json`` in miniature: pipeline wants
        position=0, but the cover is at 100 because the user opened it during
        the back-rotate suppression window. The real settle loop hits the
        stall budget without progress, and the rebase MUST refuse to absorb
        the 100-pt drift.
        """
        from custom_components.adaptive_cover_pro.const import (
            VENETIAN_REBASE_MAX_DRIFT_PERCENT,
        )

        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer."
            "VENETIAN_POSITION_SETTLE_POLL_SECONDS",
            0,
        )

        set_cmd_pos = MagicMock()
        hass, seq = _build_sequencer(
            set_commanded_position=set_cmd_pos,
            get_state=lambda _eid: "open",  # not moving → stall path fires
        )
        seq._get_current_position = lambda _eid: 100

        await seq.run_sequence(
            "cover.x", position_target=0, tilt_target=80, reason="solar"
        )

        # The seal: target_call NOT mutated to the user's 100.
        set_cmd_pos.assert_not_called()
        # Belt-and-braces: even if the settle gate slipped, the drift cap
        # would catch this drift.
        assert abs(100 - 0) > VENETIAN_REBASE_MAX_DRIFT_PERCENT
        # Tilt is still attempted on settle failure — the fix scope is rebase,
        # not tilt delivery. Real motors that stall briefly still get tilt.
        assert hass.services.async_call.call_count == 1

    async def test_does_not_rebase_when_tilt_service_fails(self):
        """If the tilt service call raises, rebase must not run."""
        from homeassistant.exceptions import HomeAssistantError

        set_cmd_pos = MagicMock()
        hass, seq = _build_sequencer(set_commanded_position=set_cmd_pos)
        hass.services.async_call = AsyncMock(
            side_effect=HomeAssistantError("tilt fail")
        )
        seq._wait_for_position_settle = AsyncMock(return_value=(True, 50))
        await seq.run_sequence(
            "cover.x", position_target=50, tilt_target=80, reason="solar"
        )
        set_cmd_pos.assert_not_called()

    async def test_does_not_rebase_when_drift_exceeds_max_cap(self):
        """Drift > VENETIAN_REBASE_MAX_DRIFT_PERCENT must NOT be absorbed.

        Belt-and-braces guard: even if settle reports success (intentionally
        mocked True here), the cap stands on its own. Real motor back-drive is
        single-digit percent; a 100 % delta is the user opening the blind.
        """
        from custom_components.adaptive_cover_pro.const import (
            VENETIAN_REBASE_MAX_DRIFT_PERCENT,
        )

        set_cmd_pos = MagicMock()
        _, seq = _build_sequencer(set_commanded_position=set_cmd_pos)
        seq._get_current_position = lambda _eid: 100
        seq._wait_for_position_settle = AsyncMock(return_value=(True, 100))
        await seq.run_sequence(
            "cover.x", position_target=0, tilt_target=80, reason="solar"
        )
        set_cmd_pos.assert_not_called()
        # The cap MUST be smaller than the drift we just refused to absorb.
        assert abs(100 - 0) > VENETIAN_REBASE_MAX_DRIFT_PERCENT

    async def test_rebases_when_drift_between_tolerance_and_cap(self):
        """Drift in the small-back-drive band (tolerance < d ≤ cap) still rebases."""
        set_cmd_pos = MagicMock()
        _, seq = _build_sequencer(set_commanded_position=set_cmd_pos)
        # target=50, actual=60 → drift=10 > tolerance(5) and ≤ cap(15).
        seq._get_current_position = lambda _eid: 60
        seq._wait_for_position_settle = AsyncMock(return_value=(True, 50))
        await seq.run_sequence(
            "cover.x", position_target=50, tilt_target=80, reason="solar"
        )
        set_cmd_pos.assert_called_once_with("cover.x", 60)

    async def test_run_sequence_stamps_suppression_window(self):
        """The position-axis path must still stamp — the window protects
        post-position back-drive state events.
        """
        set_cmd_pos = MagicMock()
        _, seq = _build_sequencer(set_commanded_position=set_cmd_pos)
        seq._get_current_position = lambda _eid: 60
        seq._wait_for_position_settle = AsyncMock(return_value=(True, 60))
        assert seq.is_in_suppression("cover.x") is False
        await seq.run_sequence(
            "cover.x", position_target=60, tilt_target=80, reason="solar"
        )
        assert seq.is_in_suppression("cover.x") is True

    async def test_does_not_rebase_when_settle_returns_false(self):
        """Settle timeout / stall must NOT rebase the commanded position.

        Reproduces the issue-33 trail: pipeline wants position=0 but the user
        has opened the blind to 100. The settle loop times out without progress
        and the unbounded rebase silently absorbs 100 as the new target,
        stranding the cover.
        """
        set_cmd_pos = MagicMock()
        hass, seq = _build_sequencer(set_commanded_position=set_cmd_pos)
        seq._get_current_position = lambda _eid: 100
        seq._wait_for_position_settle = AsyncMock(return_value=(False, 100))
        await seq.run_sequence(
            "cover.x", position_target=0, tilt_target=80, reason="solar"
        )
        set_cmd_pos.assert_not_called()
        # Tilt is still attempted — fix scope is rebase only.
        assert seq.last_tilt_target("cover.x") == 80
        assert hass.services.async_call.call_count == 1


@pytest.mark.asyncio
class TestSendTiltCommand:
    """``_send_tilt_command`` is the shared tilt-emission body used by both
    ``run_sequence`` and ``update_tilt_only``.
    """

    async def test_emits_tilt_service_call(self):
        hass, seq = _build_sequencer()
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        assert hass.services.async_call.call_count == 1
        call = hass.services.async_call.call_args.args
        assert call[1] == "set_cover_tilt_position"
        assert call[2]["tilt_position"] == 80

    async def test_records_last_tilt_target(self):
        _, seq = _build_sequencer()
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        assert seq.last_tilt_target("cover.x") == 80

    async def test_dry_run_skips_service_call(self):
        hass, seq = _build_sequencer(dry_run=True)
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        assert hass.services.async_call.call_count == 0


@pytest.mark.asyncio
class TestUpdateTiltOnly:
    """``update_tilt_only`` emits tilt without a settle wait."""

    async def test_emits_tilt_without_settle_wait(self):
        hass, seq = _build_sequencer()
        seq._wait_for_position_settle = AsyncMock()
        await seq.update_tilt_only(
            "cover.x", tilt_target=70, current_position=40, reason="solar"
        )
        seq._wait_for_position_settle.assert_not_awaited()
        assert hass.services.async_call.call_count == 1
        assert hass.services.async_call.call_args.args[1] == "set_cover_tilt_position"

    async def test_does_not_stamp_suppression_after_send(self):
        """Tilt-only sends must NOT (re)stamp the back-rotate window.

        The window covers a position command's mechanical back-drive. A tilt-only
        update from ``maybe_update_tilt_only`` / ``auto_control_on`` doesn't move
        the carriage and shouldn't reset the timer (issue #33 follow-on: the
        re-stamp kept the suppression window open long enough to silently
        consume the user's manual open).
        """
        _, seq = _build_sequencer()
        await seq.update_tilt_only(
            "cover.x", tilt_target=70, current_position=40, reason="solar"
        )
        assert seq.is_in_suppression("cover.x") is False

    async def test_update_tilt_only_does_not_extend_existing_suppression_window(self):
        """A tilt-only send must NOT push the existing stamp forward.

        Reproduces the diagnostic: a position command from 60s ago has 30s of
        window remaining. A tilt-only update at this point must preserve the
        original stamp, not refresh it to "now" and grant another 90 seconds.
        """
        _, seq = _build_sequencer()
        past = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=60)
        seq._suppression_at["cover.x"] = past
        await seq.update_tilt_only(
            "cover.x", tilt_target=70, current_position=40, reason="solar"
        )
        assert seq._suppression_at["cover.x"] == past

    async def test_short_circuits_when_target_unchanged(self):
        hass, seq = _build_sequencer()
        await seq.update_tilt_only(
            "cover.x", tilt_target=70, current_position=40, reason="solar"
        )
        assert hass.services.async_call.call_count == 1
        # Same target — must not fire again.
        await seq.update_tilt_only(
            "cover.x", tilt_target=70, current_position=42, reason="solar"
        )
        assert hass.services.async_call.call_count == 1

    async def test_emits_when_target_changes(self):
        hass, seq = _build_sequencer()
        await seq.update_tilt_only(
            "cover.x", tilt_target=70, current_position=40, reason="solar"
        )
        await seq.update_tilt_only(
            "cover.x", tilt_target=85, current_position=40, reason="solar"
        )
        assert hass.services.async_call.call_count == 2


@pytest.mark.asyncio
class TestSettleStateAware:
    """_wait_for_position_settle must not declare stall while cover.state is moving."""

    async def test_settle_does_not_fire_while_state_is_closing(self, monkeypatch):
        """Stall counter must stay at zero while state=closing, regardless of position."""
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"
            ".VENETIAN_POSITION_SETTLE_POLL_SECONDS",
            0,
        )
        # State: closing for 5 polls, then open for 3 → stall fires on poll 8.
        state_seq = iter(
            [
                "closing",
                "closing",
                "closing",
                "closing",
                "closing",
                "open",
                "open",
                "open",
            ]
        )
        calls = [0]

        def get_pos(_eid):
            calls[0] += 1
            return 40

        _, seq = _build_sequencer(get_state=lambda _eid: next(state_seq, "open"))
        seq._get_current_position = get_pos

        reached, last = await seq._wait_for_position_settle("cover.x", target=10)

        assert reached is False
        assert last == 40
        # Pre-fix bug returns after 4 polls; fix must poll at least 6.
        assert calls[0] >= 6

    async def test_settle_does_not_fire_while_state_is_opening(self, monkeypatch):
        """Same as closing test — opening state must also suppress the stall counter."""
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"
            ".VENETIAN_POSITION_SETTLE_POLL_SECONDS",
            0,
        )
        state_seq = iter(
            [
                "opening",
                "opening",
                "opening",
                "opening",
                "opening",
                "open",
                "open",
                "open",
            ]
        )
        calls = [0]

        def get_pos(_eid):
            calls[0] += 1
            return 40

        _, seq = _build_sequencer(get_state=lambda _eid: next(state_seq, "open"))
        seq._get_current_position = get_pos

        reached, last = await seq._wait_for_position_settle("cover.x", target=10)

        assert reached is False
        assert calls[0] >= 6

    async def test_settle_resets_unchanged_counter_when_motion_resumes(
        self, monkeypatch
    ):
        """Stall counter must reset if state becomes moving mid-sequence."""
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"
            ".VENETIAN_POSITION_SETTLE_POLL_SECONDS",
            0,
        )
        # open→open→closing→closing→open→open→open: counter resets on polls 3-4.
        state_seq = iter(["open", "open", "closing", "closing", "open", "open", "open"])
        calls = [0]

        def get_pos(_eid):
            calls[0] += 1
            return 40

        _, seq = _build_sequencer(get_state=lambda _eid: next(state_seq, "open"))
        seq._get_current_position = get_pos

        reached, last = await seq._wait_for_position_settle("cover.x", target=10)

        assert reached is False
        # Without fix: returns after poll 4 (3 unchanged). With fix: 7 polls.
        assert calls[0] >= 6

    async def test_settle_unchanged_samples_only_count_when_stationary(
        self, monkeypatch
    ):
        """When state is always open AND startup grace has elapsed, the 3-sample stall fires.

        The startup grace (Track B fix in issue #33) is intentionally
        bypassed here so we exercise the post-grace stall path. The
        production grace prevents a slow-starting motor's pre-motion samples
        from being misclassified as stall; once grace expires, the legacy
        3-unchanged-sample counter still terminates the wait.
        """
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"
            ".VENETIAN_POSITION_SETTLE_POLL_SECONDS",
            0,
        )
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"
            ".VENETIAN_POSITION_SETTLE_STARTUP_GRACE_SECONDS",
            0,
        )
        state_seq = iter(["open", "open", "open", "open", "open"])
        calls = [0]

        def get_pos(_eid):
            calls[0] += 1
            return 40

        _, seq = _build_sequencer(get_state=lambda _eid: next(state_seq, "open"))
        seq._get_current_position = get_pos

        reached, last = await seq._wait_for_position_settle("cover.x", target=10)

        assert reached is False
        # poll 1: last=None → reset; poll 2-4: unchanged 1-3 → stall at poll 4.
        assert calls[0] == 4

    async def test_settle_waits_when_in_tolerance_but_state_is_closing(
        self, monkeypatch
    ):
        """Settle must NOT short-circuit on position alone while cover.state is moving.

        The cover reports position 52, which is within tolerance of target 50.
        But cover.state is still "closing" for the first two polls and only
        transitions to "closed" on poll three. The loop must keep iterating
        until the state is no longer moving, then return True.
        """
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"
            ".VENETIAN_POSITION_SETTLE_POLL_SECONDS",
            0,
        )
        # All positions are in-tolerance (target=50, value=52, tol=5).
        # State: closing × 2, then closed.
        pos_calls = [0]

        def get_pos(_eid):
            pos_calls[0] += 1
            return 52

        state_seq = iter(["closing", "closing", "closed"])
        _, seq = _build_sequencer(get_state=lambda _eid: next(state_seq, "closed"))
        seq._get_current_position = get_pos

        reached, last = await seq._wait_for_position_settle("cover.x", target=50)

        assert reached is True
        # The loop must NOT have short-circuited on poll 1 (position in-tolerance but
        # still closing). It must have polled at least 3 times.
        assert pos_calls[0] >= 3

    async def test_settle_returns_true_when_state_becomes_closed_while_in_tolerance(
        self, monkeypatch
    ):
        """Settle returns True once state leaves moving, even when position was always in-tolerance.

        Positions are all within tolerance (target=50, value=52, tol=5). The
        state transitions closing→closing→closed. The loop must continue past
        polls 1-2 (state=closing) and return True on poll 3 (state=closed),
        with last position == 52.
        """
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"
            ".VENETIAN_POSITION_SETTLE_POLL_SECONDS",
            0,
        )
        state_seq = iter(["closing", "closing", "closed"])
        _, seq = _build_sequencer(get_state=lambda _eid: next(state_seq, "closed"))
        seq._get_current_position = lambda _eid: 52

        reached, last = await seq._wait_for_position_settle("cover.x", target=50)

        assert reached is True
        assert last == 52

    async def test_settle_returns_true_on_position_alone_when_no_get_state(
        self, monkeypatch
    ):
        """When no get_state callback is provided, position tolerance is sufficient to return True.

        This is the backwards-compat path for non-venetian callers and tests
        that construct DualAxisSequencer without a get_state argument. Positions
        [80, 52] with target=50 (tol=5): poll 1 is out-of-tolerance, poll 2 is
        in-tolerance → the loop returns True immediately on poll 2, without
        waiting for any state transition.
        """
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"
            ".VENETIAN_POSITION_SETTLE_POLL_SECONDS",
            0,
        )
        pos_calls = [0]
        pos_seq = iter([80, 52])

        def get_pos(_eid):
            pos_calls[0] += 1
            return next(pos_seq, 52)

        _, seq = _build_sequencer()  # no get_state
        seq._get_current_position = get_pos

        reached, last = await seq._wait_for_position_settle("cover.x", target=50)

        assert reached is True
        assert last == 52
        assert pos_calls[0] == 2

    async def test_settle_falls_back_when_no_get_state(self, monkeypatch):
        """No get_state injected + startup grace bypassed → stalls at 4 polls.

        Equivalent of the pre-Track-B fall-back path: with no state callback
        and the startup grace patched to zero, the legacy 3-unchanged-sample
        counter terminates the loop at poll 4 (poll 1 sets last_position,
        polls 2-4 each increment unchanged_samples to 3).
        """
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"
            ".VENETIAN_POSITION_SETTLE_POLL_SECONDS",
            0,
        )
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer"
            ".VENETIAN_POSITION_SETTLE_STARTUP_GRACE_SECONDS",
            0,
        )
        calls = [0]

        def get_pos(_eid):
            calls[0] += 1
            return 40

        _, seq = _build_sequencer()  # no get_state
        seq._get_current_position = get_pos

        reached, last = await seq._wait_for_position_settle("cover.x", target=10)

        assert reached is False
        assert calls[0] == 4


@pytest.mark.asyncio
class TestTiltVerification:
    """After _send_tilt_command, the recorded target is cleared if tilt didn't land."""

    async def test_clears_tilt_target_when_actual_drifts_beyond_tolerance(self):
        """Tilt sent to 80 but cover reads back 0: target must be cleared."""
        _, seq = _build_sequencer(get_current_tilt_position=lambda _eid: 0)
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        # |0 - 80| = 80 > VENETIAN_TILT_VERIFY_TOLERANCE → cleared
        assert seq.last_tilt_target("cover.x") is None

    async def test_keeps_tilt_target_when_actual_within_tolerance(self):
        """Tilt sent to 80, reads back 78: within 5% tolerance → keep target."""
        _, seq = _build_sequencer(get_current_tilt_position=lambda _eid: 78)
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        # |78 - 80| = 2 <= 5 → keep
        assert seq.last_tilt_target("cover.x") == 80

    async def test_keeps_tilt_target_when_tilt_position_unknown(self):
        """Cannot read actual tilt (None) → fail-open: keep target to avoid retry storms."""
        _, seq = _build_sequencer(get_current_tilt_position=lambda _eid: None)
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        assert seq.last_tilt_target("cover.x") == 80

    async def test_update_tilt_only_retries_after_drift_clears_target(self):
        """update_tilt_only must resend when the recorded target was cleared by drift.

        Issue #500: each send through ``_send_tilt_command`` with verify=True
        produces two service calls when the actuator drifts on every read —
        the initial send and one bounded drift retry. The retry's
        ``_retry_depth=1`` blocks further recursion.
        """
        hass, seq = _build_sequencer(get_current_tilt_position=lambda _eid: 0)
        # First send: drift triggers a bounded retry (2 calls); both still
        # drift, target stays cleared.
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        assert seq.last_tilt_target("cover.x") is None
        assert hass.services.async_call.call_count == 2
        # Same target via update_tilt_only: short-circuit compares against None
        # → resends; that resend also drifts and retries, +2 more calls.
        await seq.update_tilt_only(
            "cover.x", tilt_target=80, current_position=60, reason="solar"
        )
        assert hass.services.async_call.call_count == 4


@pytest.mark.asyncio
class TestTiltDriftImmediateRetry:
    """Issue #500: drift triggers a single bounded immediate re-send.

    Previously the sequencer detected drift, popped the stored target, and
    waited for the next coordinator cycle (potentially minutes away with
    ``delta_position=5``) to retry. For this user's KNX/Shelly actuator the
    back-rotate landed at 100% tilt during a carriage open, the integration
    saw drift on the verify path, and the user stared at the wrong tilt
    until the next cycle. The fix re-sends once through ``_send_tilt_command``
    after a short delay so all gates (dedup, dry-run, grace) are reused per
    the no-duplication rule, with a ``_retry_depth`` kwarg that blocks
    recursion past one retry.
    """

    async def test_drift_triggers_immediate_resend_via_send_tilt_command(
        self, monkeypatch
    ):
        """Initial send drifts; the bounded retry verifies in-tolerance."""
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer."
            "VENETIAN_DRIFT_RETRY_DELAY_SECONDS",
            0,
        )
        # Stateful actuator: drift on every verify sample of the first send
        # (4 reads at 0%), then in-tolerance for the retry's verify samples.
        readings = {"count": 0}

        def stateful_tilt(_eid):
            readings["count"] += 1
            # First send's verify loop reads VENETIAN_TILT_VERIFY_MAX_SAMPLES
            # (4) times at 0; retry verify loop returns 80.
            return 0 if readings["count"] <= 4 else 80

        buf = EventBuffer(maxlen=32)
        hass, seq = _build_sequencer(
            get_current_tilt_position=stateful_tilt,
            event_buffer=buf,
        )
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        # Two service calls: the initial send and the bounded drift retry.
        assert hass.services.async_call.call_count == 2
        events = buf.snapshot()
        drift = [e for e in events if e["event"] == "tilt_command_drift"]
        retries = [e for e in events if e["event"] == "tilt_command_drift_retry"]
        verified = [e for e in events if e["event"] == "tilt_command_verified"]
        assert len(drift) == 1
        assert len(retries) == 1
        assert len(verified) == 1
        # After the retry verifies, the stored target reflects the
        # successfully landed tilt.
        assert seq.last_tilt_target("cover.x") == 80

    async def test_drift_retry_does_not_recurse_when_still_drifting(self, monkeypatch):
        """A still-drifting retry must not trigger a third send (``_retry_depth`` guard)."""
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer."
            "VENETIAN_DRIFT_RETRY_DELAY_SECONDS",
            0,
        )
        buf = EventBuffer(maxlen=32)
        hass, seq = _build_sequencer(
            get_current_tilt_position=lambda _eid: 0,  # always drifts
            event_buffer=buf,
        )
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        # Exactly two: initial send + one bounded retry, no third.
        assert hass.services.async_call.call_count == 2
        events = buf.snapshot()
        drift = [e for e in events if e["event"] == "tilt_command_drift"]
        retries = [e for e in events if e["event"] == "tilt_command_drift_retry"]
        # Both sends drift; only the first emits a drift_retry.
        assert len(drift) == 2
        assert len(retries) == 1
        # Target was cleared by the second drift and not restored.
        assert seq.last_tilt_target("cover.x") is None

    async def test_drift_retry_completes_within_bounded_time(self, monkeypatch):
        """The retry must complete in a bounded time (no unbounded sleep loop)."""
        import asyncio as _asyncio

        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer."
            "VENETIAN_DRIFT_RETRY_DELAY_SECONDS",
            0,
        )
        _, seq = _build_sequencer(
            get_current_tilt_position=lambda _eid: 0,
        )
        await _asyncio.wait_for(
            seq._send_tilt_command(
                "cover.x", tilt_target=80, position_target=60, reason="solar"
            ),
            timeout=5.0,
        )

    async def test_no_retry_when_initial_send_verifies_in_tolerance(self):
        """A healthy initial send must not trigger a retry."""
        buf = EventBuffer(maxlen=32)
        hass, seq = _build_sequencer(
            get_current_tilt_position=lambda _eid: 78,  # within tolerance of 80
            event_buffer=buf,
        )
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        assert hass.services.async_call.call_count == 1
        events = buf.snapshot()
        retries = [e for e in events if e["event"] == "tilt_command_drift_retry"]
        assert retries == []

    async def test_retry_depth_blocks_recursion_at_send_level(self, monkeypatch):
        """A caller-supplied ``_retry_depth=1`` must short-circuit the retry path."""
        monkeypatch.setattr(
            "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer."
            "VENETIAN_DRIFT_RETRY_DELAY_SECONDS",
            0,
        )
        buf = EventBuffer(maxlen=32)
        hass, seq = _build_sequencer(
            get_current_tilt_position=lambda _eid: 0,  # always drifts
            event_buffer=buf,
        )
        await seq._send_tilt_command(
            "cover.x",
            tilt_target=80,
            position_target=60,
            reason="solar",
            _retry_depth=1,
        )
        assert hass.services.async_call.call_count == 1
        events = buf.snapshot()
        retries = [e for e in events if e["event"] == "tilt_command_drift_retry"]
        assert retries == []


@pytest.mark.asyncio
class TestSendTiltCommandVerifyFlag:
    """``verify=False`` lets ``before_position_command`` fire-and-forget.

    Before sending a position command on an opening transition (issue #33),
    the policy pre-sends the tilt so slats are at the target angle before
    the carriage moves. Verifying that pre-tilt is wrong: the actuator
    hasn't published the new tilt yet, and the position command is about to
    move the carriage anyway. With ``verify=False``, the service call fires,
    the target is recorded into ``_tilt_targets`` (so the post-settle resend
    dedups), and no sleep/verify/rebase runs.
    """

    async def test_verify_false_skips_drift_event_and_preserves_target(self):
        """No drift event is emitted; stored target survives even when actuator reads stale."""
        buf = EventBuffer(maxlen=16)
        _, seq = _build_sequencer(
            get_current_tilt_position=lambda _eid: 0,  # would normally drift
            event_buffer=buf,
        )
        await seq._send_tilt_command(
            "cover.x",
            tilt_target=80,
            position_target=60,
            reason="solar",
            verify=False,
        )
        assert seq.last_tilt_target("cover.x") == 80
        verify_events = [
            e
            for e in buf.snapshot()
            if e["event"] in ("tilt_command_verified", "tilt_command_drift")
        ]
        assert verify_events == []

    async def test_verify_false_does_not_rebase_position(self):
        """``_rebase_commanded_position`` must not fire when verify=False."""
        recorded: list[tuple[str, int]] = []

        def _record(eid: str, pos: int) -> None:
            recorded.append((eid, pos))

        _, seq = _build_sequencer(
            current_positions=[55],  # would rebase 60 → 55 under default verify=True
            set_commanded_position=_record,
            get_current_tilt_position=lambda _eid: 80,  # in-tolerance: would verify ok
        )
        await seq._send_tilt_command(
            "cover.x",
            tilt_target=80,
            position_target=60,
            reason="solar",
            verify=False,
        )
        assert recorded == []

    async def test_verify_true_default_still_runs_verify(self):
        """Default behaviour (verify=True) must remain unchanged."""
        buf = EventBuffer(maxlen=16)
        _, seq = _build_sequencer(
            get_current_tilt_position=lambda _eid: 78,
            event_buffer=buf,
        )
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        verified = [e for e in buf.snapshot() if e["event"] == "tilt_command_verified"]
        assert len(verified) == 1


@pytest.mark.asyncio
class TestSendTiltCommandDedup:
    """``_send_tilt_command`` short-circuits when the stored target already matches.

    Issue #33 follow-on: when ``before_position_command`` sends tilt-first on
    an opening transition, the subsequent ``run_sequence`` reaches its own
    ``_send_tilt_command`` step with the same target already stored.  Without
    a top-level dedup, the second send fires a redundant
    ``set_cover_tilt_position`` service call. The dedup keeps the total
    venetian-open service-call count at 2 (position + tilt) instead of 3.
    """

    async def test_second_send_with_same_target_skips_service_call(self):
        hass, seq = _build_sequencer()
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        assert hass.services.async_call.call_count == 1
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        assert hass.services.async_call.call_count == 1

    async def test_second_send_with_same_target_records_skipped_event(self):
        buf = EventBuffer(maxlen=16)
        _, seq = _build_sequencer(event_buffer=buf)
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        buf2 = EventBuffer(maxlen=16)
        seq._event_buffer = buf2
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        skipped = [e for e in buf2.snapshot() if e["event"] == "tilt_command_skipped"]
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "target_unchanged"

    async def test_force_bypasses_dedup(self):
        hass, seq = _build_sequencer()
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        await seq._send_tilt_command(
            "cover.x",
            tilt_target=80,
            position_target=60,
            reason="solar",
            force=True,
        )
        assert hass.services.async_call.call_count == 2

    async def test_different_target_does_not_dedup(self):
        hass, seq = _build_sequencer()
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        await seq._send_tilt_command(
            "cover.x", tilt_target=40, position_target=60, reason="solar"
        )
        assert hass.services.async_call.call_count == 2

    async def test_dedup_after_unverified_send_still_runs_verify(self):
        """Issue #33: dedup must not silence drift detection on an unverified target.

        ``before_position_command`` sends tilt pre-position with ``verify=False,
        force=True``; the post-settle ``run_sequence`` reaches the dedup gate
        with the same target stored. The service-call dedup is preserved
        (count stays at 1), but verify still runs against the actuator —
        otherwise a wrong landing is never noticed and ``_tilt_targets``
        is never popped to re-arm the next cycle's retry.
        """
        buf = EventBuffer(maxlen=16)
        hass, seq = _build_sequencer(
            get_current_tilt_position=lambda _eid: 0, event_buffer=buf
        )
        await seq._send_tilt_command(
            "cover.x",
            tilt_target=60,
            position_target=17,
            reason="auto_control_on",
            force=True,
            verify=False,
        )
        assert hass.services.async_call.call_count == 1
        await seq._send_tilt_command(
            "cover.x", tilt_target=60, position_target=17, reason="solar"
        )
        # Second send dedups (preserves the tilt-first opening service-call
        # count) but the verify path runs because the prior send was
        # unverified. The verify sees drift, fires one bounded retry through
        # _send_tilt_command — that retry adds one service call and emits a
        # second drift event before _retry_depth=1 stops recursion (#500).
        assert hass.services.async_call.call_count == 2
        drift = [e for e in buf.snapshot() if e["event"] == "tilt_command_drift"]
        assert len(drift) == 2
        # Drift clears the stored target so the next update_tilt_only retries.
        assert seq.last_tilt_target("cover.x") is None


@pytest.mark.asyncio
class TestTiltFirstThenSequenceVerify:
    """Integration-shaped guard for the tilt-first opening path (issue #33).

    Report 2 timeline: ``before_position_command`` fires tilt=60 pre-position
    (``verify=False, force=True``); the carriage opens; ``run_sequence`` waits
    for settle, then re-enters ``_send_tilt_command(verify=True)`` with the
    same target. Service-call dedup is preserved (cycle stays at 2 tilt+pos
    calls) but verify MUST run on the deduped branch — otherwise a wrong
    landing is never noticed and the cover sits at the wrong tilt forever.
    """

    async def test_run_sequence_after_tilt_first_runs_verify_on_dedup(self):
        """End-to-end: tilt-first then run_sequence with divergent actuator.

        Issue #500: the verify on the dedup branch sees the wrong landing
        and triggers one bounded drift retry, which adds a second service
        call and a second drift event before ``_retry_depth=1`` stops
        recursion.
        """
        buf = EventBuffer(maxlen=32)
        # Actuator reports the wrong tilt (0) for every verify sample — the
        # fix's verify path should observe this and emit tilt_command_drift.
        hass, seq = _build_sequencer(
            get_current_tilt_position=lambda _eid: 0,
            event_buffer=buf,
        )
        # Stub the settle loop so run_sequence completes synchronously.
        seq._wait_for_position_settle = AsyncMock(return_value=(True, 17))

        # Step 1: before_position_command fires tilt pre-position.
        await seq._send_tilt_command(
            "cover.x",
            tilt_target=60,
            position_target=17,
            reason="auto_control_on",
            force=True,
            verify=False,
        )
        # Step 2: position command would fire here; we skip it (separate path).
        # Step 3: after_position_command runs run_sequence which re-enters
        # _send_tilt_command with the default verify=True.
        await seq.run_sequence(
            "cover.x",
            position_target=17,
            tilt_target=60,
            reason="solar",
        )

        # Pre-tilt call (1) + drift-retry call from the dedup verify (1) = 2.
        assert hass.services.async_call.call_count == 2

        events = buf.snapshot()
        # The dedup branch DID run verify, and verify saw the wrong landing.
        # Both the verify and the still-drifting retry emit drift events.
        drift = [e for e in events if e["event"] == "tilt_command_drift"]
        assert len(drift) == 2
        # Dedup itself was recorded.
        skipped = [
            e
            for e in events
            if e["event"] == "tilt_command_skipped"
            and e.get("reason") == "target_unchanged"
        ]
        assert len(skipped) == 1
        # Drift cleared the stored target so update_tilt_only re-fires next cycle.
        assert seq.last_tilt_target("cover.x") is None


@pytest.mark.asyncio
class TestTiltVerifyWithRetry:
    """Issue #33: verify must tolerate actuator publish lag.

    KNX/Shelly venetian actuators publish ``current_tilt_position`` via state
    updates that can lag the service call by 1–3 s past the existing
    ``VENETIAN_POST_TILT_REBASE_DELAY_SECONDS`` (1.5 s) wait. A single-shot
    verify reads the pre-update value, declares drift, and clears
    ``_tilt_targets`` — which then re-arms the next ``update_tilt_only`` cycle
    to fire a second (and third) tilt command for the same logical target.
    The verify must poll up to ``VENETIAN_TILT_VERIFY_MAX_SAMPLES`` times,
    ``VENETIAN_TILT_VERIFY_POLL_SECONDS`` apart, and only declare drift when
    every sample is out of tolerance.
    """

    async def test_verify_accepts_on_late_sample_and_keeps_target(self):
        """Stale read first, then a matching read → target preserved, no drift event."""
        tilt_readings = iter([0, 0, 80])
        buf = EventBuffer(maxlen=16)
        _, seq = _build_sequencer(
            get_current_tilt_position=lambda _eid: next(tilt_readings, 80),
            event_buffer=buf,
        )
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        assert seq.last_tilt_target("cover.x") == 80
        drift_events = [e for e in buf.snapshot() if e["event"] == "tilt_command_drift"]
        verified_events = [
            e for e in buf.snapshot() if e["event"] == "tilt_command_verified"
        ]
        assert drift_events == []
        assert len(verified_events) == 1

    async def test_verify_clears_target_when_all_samples_drift(self):
        """Constantly stale read → target cleared, drift event records final sample.

        Issue #500: the verify path now schedules one bounded drift retry,
        which itself drifts and emits a second drift event before
        ``_retry_depth=1`` stops recursion. Both events record the same
        post-drift sample.
        """
        buf = EventBuffer(maxlen=16)
        _, seq = _build_sequencer(
            get_current_tilt_position=lambda _eid: 0,
            event_buffer=buf,
        )
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        assert seq.last_tilt_target("cover.x") is None
        drift_events = [e for e in buf.snapshot() if e["event"] == "tilt_command_drift"]
        assert len(drift_events) == 2
        for evt in drift_events:
            assert evt["actual_tilt_position"] == 0
            assert evt["delta"] == 80

    async def test_verify_stops_polling_after_first_in_tolerance_sample(self):
        """In-tolerance first read must short-circuit — no extra polls."""
        tilt_mock = MagicMock(side_effect=[78])
        _, seq = _build_sequencer(get_current_tilt_position=tilt_mock)
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        assert tilt_mock.call_count == 1
        assert seq.last_tilt_target("cover.x") == 80

    async def test_verify_skipped_when_get_current_tilt_position_unwired(self):
        """No tilt-read callback → preserve target, emit no verify/drift events."""
        buf = EventBuffer(maxlen=16)
        _, seq = _build_sequencer(
            get_current_tilt_position=None,
            event_buffer=buf,
        )
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        assert seq.last_tilt_target("cover.x") == 80
        verify_events = [
            e
            for e in buf.snapshot()
            if e["event"] in ("tilt_command_verified", "tilt_command_drift")
        ]
        assert verify_events == []


@pytest.mark.asyncio
class TestTiltDiagnosticEvents:
    """DualAxisSequencer emits EventBuffer entries for every tilt command outcome."""

    async def test_tilt_command_sent_event_recorded(self):
        buf = EventBuffer(maxlen=16)
        _, seq = _build_sequencer(event_buffer=buf)
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        sent = [e for e in buf.snapshot() if e["event"] == "tilt_command_sent"]
        assert len(sent) == 1
        ev = sent[0]
        assert ev["entity_id"] == "cover.x"
        assert ev["tilt_position"] == 80
        assert ev["position_target"] == 60
        assert ev["trigger"] == "solar"
        assert "ts" in ev

    async def test_tilt_command_skipped_on_dry_run(self):
        buf = EventBuffer(maxlen=16)
        _, seq = _build_sequencer(dry_run=True, event_buffer=buf)
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        skipped = [e for e in buf.snapshot() if e["event"] == "tilt_command_skipped"]
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "dry_run"
        assert skipped[0]["entity_id"] == "cover.x"
        assert skipped[0]["tilt_position"] == 80
        assert "ts" in skipped[0]

    async def test_tilt_command_skipped_on_short_circuit(self):
        buf = EventBuffer(maxlen=16)
        hass, seq = _build_sequencer(event_buffer=buf)
        # First call actually sends.
        await seq.update_tilt_only(
            "cover.x", tilt_target=70, current_position=40, reason="solar"
        )
        assert hass.services.async_call.call_count == 1
        # Replace buffer to isolate the second call's events.
        buf2 = EventBuffer(maxlen=16)
        seq._event_buffer = buf2
        # Second call with same target → short-circuit.
        await seq.update_tilt_only(
            "cover.x", tilt_target=70, current_position=42, reason="solar"
        )
        assert hass.services.async_call.call_count == 1
        skipped = [e for e in buf2.snapshot() if e["event"] == "tilt_command_skipped"]
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "target_unchanged"
        assert skipped[0]["tilt_position"] == 70
        assert skipped[0]["current_position"] == 42
        assert "ts" in skipped[0]

    async def test_tilt_command_verified_event(self):
        buf = EventBuffer(maxlen=16)
        _, seq = _build_sequencer(
            get_current_tilt_position=lambda _eid: 78,
            event_buffer=buf,
        )
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        verified = [e for e in buf.snapshot() if e["event"] == "tilt_command_verified"]
        assert len(verified) == 1
        ev = verified[0]
        assert ev["entity_id"] == "cover.x"
        assert ev["tilt_target"] == 80
        assert ev["actual_tilt_position"] == 78
        assert ev["delta"] == 2
        assert "ts" in ev

    async def test_tilt_command_drift_event(self):
        """Schedule a bounded drift retry on the verify path (issue #500).

        The retry itself drifts on the all-drift actuator → two
        ``tilt_command_drift`` events per send.
        """
        buf = EventBuffer(maxlen=16)
        _, seq = _build_sequencer(
            get_current_tilt_position=lambda _eid: 0,
            event_buffer=buf,
        )
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        drift = [e for e in buf.snapshot() if e["event"] == "tilt_command_drift"]
        assert len(drift) == 2
        for ev in drift:
            assert ev["entity_id"] == "cover.x"
            assert ev["tilt_target"] == 80
            assert ev["actual_tilt_position"] == 0
            assert ev["delta"] == 80
            assert "ts" in ev

    async def test_no_verify_event_when_tilt_position_unknown(self):
        buf = EventBuffer(maxlen=16)
        _, seq = _build_sequencer(
            get_current_tilt_position=lambda _eid: None,
            event_buffer=buf,
        )
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        verify_events = [
            e
            for e in buf.snapshot()
            if e["event"] in ("tilt_command_verified", "tilt_command_drift")
        ]
        assert len(verify_events) == 0


@pytest.mark.asyncio
class TestTiltInversion:
    """_send_tilt_command applies optional tilt-axis inversion before sending."""

    async def test_inverts_wire_value_when_invert_tilt_is_true(self):
        """With invert_tilt=True, wire value sent must be 100 - tilt_target."""
        hass, seq = _build_sequencer(invert_tilt=lambda: True)
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        wire = hass.services.async_call.call_args.args[2]["tilt_position"]
        assert wire == 20  # 100 - 80

    async def test_passes_target_through_when_invert_tilt_is_false(self):
        """With invert_tilt=False, wire value must equal tilt_target unchanged."""
        hass, seq = _build_sequencer(invert_tilt=lambda: False)
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        wire = hass.services.async_call.call_args.args[2]["tilt_position"]
        assert wire == 80

    async def test_recorded_tilt_target_stays_logical(self):
        """last_tilt_target must store the logical (user-facing) value, not the wire value."""
        _, seq = _build_sequencer(invert_tilt=lambda: True)
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        assert seq.last_tilt_target("cover.x") == 80

    async def test_invert_tilt_callable_evaluated_per_call(self):
        """Callable must be evaluated on each send so runtime option changes take effect.

        ``force=True`` bypasses the target-unchanged dedup so both sends
        actually fire the service call (the dedup is unrelated to inversion).
        """
        inverted = [True]
        hass, seq = _build_sequencer(invert_tilt=lambda: inverted[0])
        await seq._send_tilt_command(
            "cover.x",
            tilt_target=80,
            position_target=60,
            reason="solar",
            force=True,
        )
        first_wire = hass.services.async_call.call_args_list[-1].args[2][
            "tilt_position"
        ]
        assert first_wire == 20

        inverted[0] = False
        await seq._send_tilt_command(
            "cover.x",
            tilt_target=80,
            position_target=60,
            reason="solar",
            force=True,
        )
        second_wire = hass.services.async_call.call_args_list[-1].args[2][
            "tilt_position"
        ]
        assert second_wire == 80

    async def test_verify_keeps_target_when_wire_actual_matches_inverted_target(self):
        """Verification must compare in logical space: wire=20 → logical=80, matches tilt_target=80."""
        _, seq = _build_sequencer(
            invert_tilt=lambda: True,
            get_current_tilt_position=lambda _eid: 20,
        )
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        assert seq.last_tilt_target("cover.x") == 80

    async def test_verify_detects_drift_in_logical_space_when_inverted(self):
        """Wire=80 → logical=20 when inverted; delta against tilt_target=80 is 60 → drift."""
        _, seq = _build_sequencer(
            invert_tilt=lambda: True,
            get_current_tilt_position=lambda _eid: 80,
        )
        await seq._send_tilt_command(
            "cover.x", tilt_target=80, position_target=60, reason="solar"
        )
        assert seq.last_tilt_target("cover.x") is None


@pytest.mark.asyncio
class TestTiltDeltaGate:
    """Tilt commands must respect the configured min-change threshold."""

    async def test_below_min_change_skips_service_call(self):
        """When tilt delta is below min_change, no service call is made and a skip event is emitted.

        Stored target and actuator agree at 50 — gate skips because delta < min_change
        regardless of anchor source (issue #33).
        """
        from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
            EventBuffer,
        )

        buf = EventBuffer(maxlen=20)
        hass, seq = _build_sequencer(
            get_min_change=lambda: 8,
            event_buffer=buf,
            get_current_tilt_position=lambda _eid: 50,
        )
        seq._tilt_targets["cover.x"] = 50

        await seq._send_tilt_command(
            "cover.x", tilt_target=53, position_target=60, reason="solar"
        )

        assert hass.services.async_call.call_count == 0
        events = buf.snapshot()
        assert len(events) == 1
        assert events[0]["event"] == "tilt_command_skipped"
        assert events[0]["reason"] == "delta_too_small"

    async def test_at_or_above_min_change_emits_service_call(self):
        """When tilt delta meets min_change, the tilt service call fires.

        Stored target and actuator agree at 50 — gate fires because delta >= min_change
        regardless of anchor source (issue #33). Actuator continues to read
        50 after the send so verify sees drift and triggers the issue #500
        bounded retry, which adds one additional service call.
        """
        hass, seq = _build_sequencer(
            get_min_change=lambda: 8,
            get_current_tilt_position=lambda _eid: 50,
        )
        seq._tilt_targets["cover.x"] = 50

        await seq._send_tilt_command(
            "cover.x", tilt_target=58, position_target=60, reason="solar"
        )

        # Initial send (delta gate passes) + one bounded drift retry (#500).
        assert hass.services.async_call.call_count == 2

    async def test_first_cycle_bypasses_gate(self):
        """With no prior tilt target, the gate is bypassed (first-cycle send)."""
        hass, seq = _build_sequencer(get_min_change=lambda: 50)
        # No seed in _tilt_targets — simulates first cycle

        await seq._send_tilt_command(
            "cover.x", tilt_target=10, position_target=60, reason="solar"
        )

        assert hass.services.async_call.call_count == 1

    async def test_force_kwarg_bypasses_gate(self):
        """force=True bypasses the delta gate regardless of delta size."""
        hass, seq = _build_sequencer(get_min_change=lambda: 50)
        seq._tilt_targets["cover.x"] = 50

        await seq._send_tilt_command(
            "cover.x", tilt_target=51, position_target=60, reason="solar", force=True
        )

        assert hass.services.async_call.call_count == 1

    async def test_default_min_change_one_is_permissive(self):
        """Without get_min_change, any delta ≥ 1 sends — gate is permissive by default.

        Stored target and actuator agree at 50 — gate is permissive regardless of
        anchor source (issue #33).
        """
        hass, seq = _build_sequencer(
            get_current_tilt_position=lambda _eid: 50,
        )  # no get_min_change
        seq._tilt_targets["cover.x"] = 50

        await seq._send_tilt_command(
            "cover.x", tilt_target=51, position_target=60, reason="solar"
        )

        assert hass.services.async_call.call_count == 1


class TestResolveTiltAnchor:
    """``_resolve_tilt_anchor`` returns the right (value, source) for each branch.

    Issue #33: covers the three call paths into the helper — live actuator read,
    actuator-returns-None fallback, and no-callback fallback.
    """

    def test_returns_actual_when_callback_provided_and_returns_value(self):
        """Live actuator read short-circuits the stored target."""
        _, seq = _build_sequencer(get_current_tilt_position=lambda _eid: 72)
        seq._tilt_targets["cover.x"] = 99  # stored target is stale
        value, source = seq._resolve_tilt_anchor("cover.x")
        assert value == 72
        assert source == "actual"

    def test_falls_back_to_stored_when_actuator_returns_none(self):
        """When the callback returns None, fall back to stored target."""
        _, seq = _build_sequencer(get_current_tilt_position=lambda _eid: None)
        seq._tilt_targets["cover.x"] = 50
        value, source = seq._resolve_tilt_anchor("cover.x")
        assert value == 50
        assert source == "target_fallback"

    def test_falls_back_to_stored_when_callback_not_wired(self):
        """When no callback is configured at all, fall back to stored target."""
        _, seq = _build_sequencer()  # no get_current_tilt_position
        seq._tilt_targets["cover.x"] = 40
        value, source = seq._resolve_tilt_anchor("cover.x")
        assert value == 40
        assert source == "target_fallback"

    def test_actual_inverted_when_invert_tilt_set(self):
        """Wire reading is converted to logical space when inversion is configured."""
        _, seq = _build_sequencer(
            get_current_tilt_position=lambda _eid: 80,
            invert_tilt=lambda: True,
        )
        value, source = seq._resolve_tilt_anchor("cover.x")
        # inverse_state(80) == 20
        assert value == 20
        assert source == "actual"

    def test_returns_none_value_when_actuator_none_and_no_stored_target(self):
        """No actuator + no stored target → (None, target_fallback)."""
        _, seq = _build_sequencer(get_current_tilt_position=lambda _eid: None)
        value, source = seq._resolve_tilt_anchor("cover.x")
        assert value is None
        assert source == "target_fallback"


@pytest.mark.asyncio
class TestTiltDeltaAnchorIsActualTilt:
    """Issue #33: the tilt min-delta gate must anchor on live actuator state.

    The stored ``_tilt_targets`` value can drift from reality whenever the
    motor auto-tilts mechanically (e.g. on close, on slat back-rotate) — comparing
    a new target against the *stored target* skips legitimate motion while the
    cover is far from where ACP thinks it is. The fix anchors on the actuator's
    actual tilt (with fallback to the stored target when unavailable).
    """

    async def test_stale_anchor_after_close_does_not_skip_legitimate_move(self):
        """Stored=74 (fiction), actual=100 (closed), target=72 → command must fire.

        Reproduces issue #33 comment 81 failure mode 1: motor auto-tilted to 100
        on close, but our stored target is the pre-close 74. New target 72 has
        |72−74|=2 against stored (would skip), but |72−100|=28 against actual
        (must fire). The actuator continues to read 100 after the send, so
        verify sees drift and triggers the issue #500 bounded retry (+1 call).
        """
        hass, seq = _build_sequencer(
            get_min_change=lambda: 8,
            get_current_tilt_position=lambda _eid: 100,
        )
        seq._tilt_targets["cover.x"] = 74

        await seq._send_tilt_command(
            "cover.x", tilt_target=72, position_target=60, reason="solar"
        )

        # Initial send + one bounded drift retry (#500).
        assert hass.services.async_call.call_count == 2

    async def test_slow_drift_past_stale_anchor_fires_each_cycle(self):
        """Stored=74, actual=100. Targets 70, 69, 68 each delta against stored
        is 4-6 (would skip), but against actual is 30-32 (must fire).

        Reproduces issue #33 comment 81 failure mode 2: solar drift across
        multiple cycles below the legacy stored-target min_change but well
        above the gate threshold relative to actual tilt. Each of the three
        sends drifts on verify and triggers the issue #500 bounded retry, so
        the service-call count doubles: 3 sends × 2 calls each = 6.
        """
        hass, seq = _build_sequencer(
            get_min_change=lambda: 8,
            get_current_tilt_position=lambda _eid: 100,
        )
        seq._tilt_targets["cover.x"] = 74

        for target in (70, 69, 68):
            await seq._send_tilt_command(
                "cover.x", tilt_target=target, position_target=60, reason="solar"
            )

        assert hass.services.async_call.call_count == 6

    async def test_anchor_falls_back_to_stored_target_when_actuator_returns_none(self):
        """When ``get_current_tilt_position`` returns None, anchor falls back
        to the stored target and the gate still skips when delta is small.

        The diagnostic event must record ``anchor_source='target_fallback'``
        and ``anchor_value=<stored>`` so operators can see why a skip happened.
        """
        buf = EventBuffer(maxlen=20)
        hass, seq = _build_sequencer(
            get_min_change=lambda: 8,
            get_current_tilt_position=lambda _eid: None,
            event_buffer=buf,
        )
        seq._tilt_targets["cover.x"] = 50

        await seq._send_tilt_command(
            "cover.x", tilt_target=53, position_target=60, reason="solar"
        )

        assert hass.services.async_call.call_count == 0
        events = buf.snapshot()
        assert len(events) == 1
        assert events[0]["event"] == "tilt_command_skipped"
        assert events[0]["reason"] == "delta_too_small"
        assert events[0]["anchor_source"] == "target_fallback"
        assert events[0]["anchor_value"] == 50

    async def test_skip_event_records_actual_anchor_when_available(self):
        """When actuator reports a current tilt, skip events record it as the anchor.

        Stored=99 (stale), actual=72, target=74, min_change=8: delta vs stored
        is 25 (would fire under old logic), delta vs actual is 2 → skip with
        ``anchor_source='actual'`` and ``anchor_value=72``.
        """
        buf = EventBuffer(maxlen=20)
        hass, seq = _build_sequencer(
            get_min_change=lambda: 8,
            get_current_tilt_position=lambda _eid: 72,
            event_buffer=buf,
        )
        seq._tilt_targets["cover.x"] = 99

        await seq._send_tilt_command(
            "cover.x", tilt_target=74, position_target=60, reason="solar"
        )

        assert hass.services.async_call.call_count == 0
        events = buf.snapshot()
        assert len(events) == 1
        assert events[0]["event"] == "tilt_command_skipped"
        assert events[0]["reason"] == "delta_too_small"
        assert events[0]["anchor_source"] == "actual"
        assert events[0]["anchor_value"] == 72


@pytest.mark.asyncio
async def test_run_sequence_uses_configured_post_settle_hold() -> None:
    """DualAxisSequencer built with post_settle_hold_seconds=5.0 sleeps 5.0 s, not 2.0."""
    sleep_calls: list[float] = []

    async def _capture_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    import unittest.mock

    with unittest.mock.patch(
        "custom_components.adaptive_cover_pro.cover_types.venetian.sequencer.asyncio.sleep",
        side_effect=_capture_sleep,
    ):
        _, seq = _build_sequencer(post_settle_hold_seconds=5.0)
        seq._wait_for_position_settle = AsyncMock(return_value=(True, 60))
        await seq.run_sequence(
            "cover.x", position_target=60, tilt_target=80, reason="solar"
        )

    assert 5.0 in sleep_calls, f"Expected 5.0 in sleep_calls, got {sleep_calls}"
    assert (
        2.0 not in sleep_calls
    ), f"2.0 (module default) must not appear, got {sleep_calls}"


class TestClearTiltTargets:
    """``clear_tilt_targets`` invalidates the stored-target cache (issue #33).

    Defense-in-depth hook called from Auto Control off→on transitions, so the
    very next cycle resolves the anchor from the live actuator instead of an
    arbitrarily old stored target.
    """

    def test_clears_all_stored_tilt_targets(self):
        _, seq = _build_sequencer()
        seq._tilt_targets["cover.a"] = 50
        seq._tilt_targets["cover.b"] = 75

        seq.clear_tilt_targets()

        assert seq._tilt_targets == {}

    def test_last_tilt_target_returns_none_after_clear(self):
        _, seq = _build_sequencer()
        seq._tilt_targets["cover.a"] = 50

        seq.clear_tilt_targets()

        assert seq.last_tilt_target("cover.a") is None

    def test_does_not_touch_suppression_timestamps(self):
        """Back-rotate suppression is a time-based safeguard, independent of the
        stored-target cache — clearing tilt targets must not reopen the suppression
        window early.
        """
        _, seq = _build_sequencer()
        seq.stamp_position_command("cover.a")
        before = dict(seq._suppression_at)
        assert before  # sanity: there's something to preserve

        seq.clear_tilt_targets()

        assert seq._suppression_at == before
