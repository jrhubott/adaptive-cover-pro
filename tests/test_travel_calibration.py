"""Measuring how long a cover takes to move, and refusing to guess when it can't.

``TravelTimeCalibrator`` sweeps each configured cover stop-to-stop twice, times
both legs, and stores the result so later moves can be rendered as a ramp. Three
things make it more than a stopwatch:

* a cover HA can only *assume* the state of reports arrival instantly, so timing
  it would measure a service call rather than a traverse — those are excluded;
* physically coupled covers (day/night Model C rails) cannot sweep with their
  partner in the way, so the policy names where the partner must be parked first;
* the run owns the covers while it holds them, and has to give them back on
  every exit path — success, timeout, exception, or cancel.

Timings are synthetic without being fake: the stand-in cover publishes its
states with explicit ``timestamp`` values, which HA carries through to
``time_fired`` — the very field the calibrator measures. So a "20 second"
traverse is exactly 20 seconds to the code under test while taking microseconds
to run. Freezing the clock instead would deadlock every ``asyncio`` timeout in
the module, since freezegun stops ``time.monotonic`` and the event loop is
driven by it.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.adaptive_cover_pro.const import (
    CONF_DAY_NIGHT_CONTROL_MODEL,
    CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY,
    CONF_INVERSE_STATE,
    CONF_TRAVEL_TIME_CALIBRATION,
    DAY_NIGHT_MODEL_DUAL_ENTITY,
    TRAVEL_CALIBRATION_SOURCE_MANUAL,
    TRAVEL_CALIBRATION_SOURCE_MEASURED,
    TRAVEL_CALIBRATION_STATE_CANCELLED,
    TRAVEL_CALIBRATION_STATE_COMPLETE,
    TRAVEL_CALIBRATION_STATE_FAILED,
    TRAVEL_CALIBRATION_STATE_RUNNING,
    TRAVEL_CALIBRATION_STATUS_OK,
    TRAVEL_CALIBRATION_STATUS_TIMEOUT,
    TRAVEL_CALIBRATION_STATUS_UNAVAILABLE,
    TRAVEL_CALIBRATION_STATUS_UNOBSERVABLE,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
)
from custom_components.adaptive_cover_pro.managers.travel_calibration import (
    REASON_ASSUMED_STATE,
    REASON_NO_ENTITIES,
    REASON_NO_MOTION_OBSERVED,
    TravelTimeCalibrator,
)

pytestmark = pytest.mark.integration

# HA CoverEntityFeature masks: OPEN(1) CLOSE(2) SET_POSITION(4) STOP(8).
_POSITION_FEATURES = 1 | 2 | 4 | 8
_OPEN_CLOSE_FEATURES = 1 | 2 | 8

# Real-clock budgets, deliberately tiny: only the tests that exercise a stall or
# a cancel ever reach them, and they should not add seconds to the suite.
_TEST_MOVE_TIMEOUT = 0.25
_TEST_PROBE = 0.05

# The synthetic timestamps are exact, but ``sent_at`` is taken from the real
# clock a few microseconds earlier, so the delay split lands just off the nose.
_DELAY_TOLERANCE = 0.5


class _FakeCover:
    """A cover that answers commands by publishing timestamped states.

    Each command restarts from the current wall clock and lays its states out at
    explicit offsets, so every leg is self-consistent and ``sent_at`` (which the
    calibrator reads from the real clock) lines up with offset zero.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        *,
        position: int = 30,
        features: int = _POSITION_FEATURES,
        open_travel: float = 20.0,
        close_travel: float = 20.0,
        delay: float = 2.0,
        publish_transit: bool = True,
        assumed: bool = False,
        stalled: bool = False,
        stall_after: int | None = None,
        never_arrive: bool = False,
    ) -> None:
        self.hass = hass
        self.entity_id = entity_id
        self.position = position
        self.features = features
        self.open_travel = open_travel
        self.close_travel = close_travel
        self.delay = delay
        self.publish_transit = publish_transit
        self.assumed = assumed
        self.stalled = stalled
        # Stop responding once this many commands have been accepted — lets a
        # test stall a specific leg rather than the whole run.
        self.stall_after = stall_after
        # Report motion but never reach the target: a cover that starts and
        # then jams, which is a different failure from one that never moves.
        self.never_arrive = never_arrive
        self.commands: list[int] = []
        self._publish(time.time())

    @property
    def _position_capable(self) -> bool:
        return bool(self.features & 4)

    def _attrs(self) -> dict:
        attrs: dict = {"supported_features": self.features}
        if self._position_capable:
            attrs["current_position"] = self.position
        if self.assumed:
            attrs["assumed_state"] = True
        return attrs

    def _publish(self, stamp: float, state: str | None = None) -> None:
        if state is None:
            state = "closed" if self.position == 0 else "open"
        self.hass.states.async_set(
            self.entity_id, state, self._attrs(), timestamp=stamp
        )

    async def command(self, entity_id: str, target: int, **_kw) -> None:
        """Stand in for CoverCommandService._execute_command."""
        self.commands.append(target)
        if self.stalled or (
            self.stall_after is not None and len(self.commands) > self.stall_after
        ):
            return
        base = time.time()
        rising = target > self.position
        if self.publish_transit:
            self._publish(base + self.delay, "opening" if rising else "closing")
            await self.hass.async_block_till_done()
        if self.never_arrive:
            return
        travel = self.open_travel if rising else self.close_travel
        self.position = target
        self._publish(base + self.delay + travel)
        await self.hass.async_block_till_done()


class _Rig:
    """A calibrator wired to one or more fake covers."""

    def __init__(
        self,
        hass: HomeAssistant,
        covers: dict[str, _FakeCover],
        *,
        cover_type: str = "cover_blind",
        options: dict | None = None,
        move_timeout: float = _TEST_MOVE_TIMEOUT,
    ) -> None:
        self.hass = hass
        self.covers = covers
        self.options = dict(options or {})
        self.persisted: dict | None = None
        # A real CoverCommandService so capability reads, position reads,
        # availability and the _at_target tolerance are the production ones.
        self.cmd_svc = CoverCommandService(
            hass=hass,
            logger=MagicMock(),
            cover_type=cover_type,
            grace_mgr=MagicMock(),
            policy=get_policy(cover_type),
        )
        self.cmd_svc._execute_command = AsyncMock(side_effect=self._dispatch)

        async def _persist(table):
            self.persisted = table

        self.calibrator = TravelTimeCalibrator(
            hass,
            MagicMock(),
            cmd_svc=self.cmd_svc,
            policy=get_policy(cover_type),
            get_entities=lambda: list(covers),
            get_options=lambda: self.options,
            persist=_persist,
            move_timeout_seconds=move_timeout,
            probe_seconds=_TEST_PROBE,
        )

    async def _dispatch(self, entity_id: str, target: int, **kw) -> None:
        await self.covers[entity_id].command(entity_id, target, **kw)

    def commands(self, entity_id: str) -> list[int]:
        return self.covers[entity_id].commands


def _rig(hass, *, move_timeout: float = _TEST_MOVE_TIMEOUT, **cover_kwargs) -> _Rig:
    """One position-capable cover, the common case."""
    return _Rig(
        hass,
        {"cover.a": _FakeCover(hass, "cover.a", **cover_kwargs)},
        move_timeout=move_timeout,
    )


async def _park_in_wait(rig: _Rig) -> asyncio.Task:
    """Start a run and let it settle into its first arrival wait."""
    task = asyncio.ensure_future(rig.calibrator.async_run())
    for _ in range(20):
        await asyncio.sleep(0)
        if rig.commands("cover.a"):
            break
    assert rig.calibrator.is_running
    return task


async def _partial_move(rig: _Rig, entity_id: str, position: int) -> None:
    """Move a stalled cover part-way, without it ever reaching its target.

    Gives a cancel something real to undo: a cover still sitting exactly where
    the run found it has nothing to restore, so a restore pass over it is
    correctly a no-op and proves nothing.
    """
    cover = rig.covers[entity_id]
    cover.position = position
    cover._publish(time.time())
    await rig.hass.async_block_till_done()


# ------------------------------------------------------------------ #
# The measurement itself
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_sweeps_each_endpoint_in_order(hass: HomeAssistant) -> None:
    """Seed to closed, time the traverse open, time it back, then go home."""
    rig = _rig(hass)
    await rig.calibrator.async_run()

    assert rig.commands("cover.a") == [0, 100, 0, 30]


@pytest.mark.asyncio
async def test_asymmetric_legs_are_recorded_separately(hass: HomeAssistant) -> None:
    """A cover that drops faster than it rises must not be flattened to one figure."""
    rig = _rig(hass, open_travel=40.0, close_travel=20.0)
    await rig.calibrator.async_run()

    row = rig.persisted["cover.a"]
    assert row["open_seconds"] == pytest.approx(40.0, abs=_DELAY_TOLERANCE)
    assert row["close_seconds"] == pytest.approx(20.0, abs=_DELAY_TOLERANCE)
    assert row["full_travel_seconds"] == pytest.approx(30.0, abs=_DELAY_TOLERANCE)
    assert row["source"] == TRAVEL_CALIBRATION_SOURCE_MEASURED


@pytest.mark.asyncio
async def test_start_delay_is_split_out_of_travel(hass: HomeAssistant) -> None:
    """Actuator dead time is measured, and excluded from the travel figure.

    Booking the lag as travel would make every ramp derived from it start early
    and finish late, so the two are recorded as separate numbers.
    """
    rig = _rig(hass, delay=4.0, open_travel=20.0, close_travel=20.0)
    await rig.calibrator.async_run()

    row = rig.persisted["cover.a"]
    assert row["start_delay_seconds"] == pytest.approx(4.0, abs=_DELAY_TOLERANCE)
    assert row["full_travel_seconds"] == pytest.approx(20.0, abs=_DELAY_TOLERANCE)


@pytest.mark.asyncio
async def test_position_only_cover_is_timed_without_transit_states(
    hass: HomeAssistant,
) -> None:
    """A cover that reports only its final position still calibrates.

    Nothing here distinguishes dead time from travel — the single publish that
    says "arrived" is the only evidence there is. The whole elapsed span books
    as travel and the delay as zero, which is the conservative half of that
    trade: a ramp that runs a little slow, rather than one that shows the cover
    parked while it is in fact already moving.
    """
    rig = _rig(
        hass, publish_transit=False, delay=2.0, open_travel=25.0, close_travel=25.0
    )
    await rig.calibrator.async_run()

    row = rig.persisted["cover.a"]
    assert row["full_travel_seconds"] == pytest.approx(27.0, abs=_DELAY_TOLERANCE)
    assert row["start_delay_seconds"] == pytest.approx(0.0, abs=_DELAY_TOLERANCE)


@pytest.mark.asyncio
async def test_results_are_persisted_and_reported_ok(hass: HomeAssistant) -> None:
    rig = _rig(hass)
    await rig.calibrator.async_run()

    assert rig.calibrator.state == TRAVEL_CALIBRATION_STATE_COMPLETE
    assert rig.calibrator.active_entity is None
    assert (
        rig.calibrator.diagnostics()["results"]["cover.a"]["status"]
        == TRAVEL_CALIBRATION_STATUS_OK
    )


# ------------------------------------------------------------------ #
# Observability — covers that cannot be measured
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_assumed_state_cover_is_never_commanded(hass: HomeAssistant) -> None:
    """The gate fires before dispatch: an unmeasurable cover is not moved at all.

    Sweeping it would put the covers through a pointless round trip to arrive at
    a number that means nothing.
    """
    rig = _rig(hass, features=_OPEN_CLOSE_FEATURES, assumed=True)
    await rig.calibrator.async_run()

    assert rig.commands("cover.a") == []
    result = rig.calibrator.diagnostics()["results"]["cover.a"]
    assert result["status"] == TRAVEL_CALIBRATION_STATUS_UNOBSERVABLE
    assert result["reason"] == REASON_ASSUMED_STATE


@pytest.mark.asyncio
async def test_snapping_cover_without_transit_states_is_unobservable(
    hass: HomeAssistant,
) -> None:
    """A position-less cover that never reports motion cannot be timed.

    It reports the destination state immediately, which from the outside is
    indistinguishable from teleporting. The probe catches it rather than
    crediting it with an instantaneous traverse.
    """
    rig = _rig(
        hass,
        features=_OPEN_CLOSE_FEATURES,
        publish_transit=False,
        open_travel=0.0,
        close_travel=0.0,
        delay=0.0,
    )
    await rig.calibrator.async_run()

    result = rig.calibrator.diagnostics()["results"]["cover.a"]
    assert result["status"] == TRAVEL_CALIBRATION_STATUS_UNOBSERVABLE
    assert result["reason"] == REASON_NO_MOTION_OBSERVED
    assert rig.persisted == {}


@pytest.mark.asyncio
async def test_open_close_cover_with_real_transit_states_calibrates(
    hass: HomeAssistant,
) -> None:
    """No position, but honest opening/closing → fully supported.

    This is the case the whole feature exists for: a cover that cannot report
    where it is, but can be timed, and therefore can be animated.
    """
    rig = _rig(
        hass,
        features=_OPEN_CLOSE_FEATURES,
        position=0,
        open_travel=18.0,
        close_travel=18.0,
    )
    await rig.calibrator.async_run()

    assert rig.persisted["cover.a"]["full_travel_seconds"] == pytest.approx(
        18.0, abs=_DELAY_TOLERANCE
    )


@pytest.mark.asyncio
async def test_unavailable_cover_is_skipped(hass: HomeAssistant) -> None:
    rig = _rig(hass)
    hass.states.async_set("cover.a", "unavailable", {})
    await hass.async_block_till_done()
    await rig.calibrator.async_run()

    assert rig.commands("cover.a") == []
    assert (
        rig.calibrator.diagnostics()["results"]["cover.a"]["status"]
        == TRAVEL_CALIBRATION_STATUS_UNAVAILABLE
    )


@pytest.mark.asyncio
async def test_a_measured_run_leaves_a_manual_row_alone(hass: HomeAssistant) -> None:
    """The covers that need manual entry are exactly the ones a run skips.

    If a run wiped the rows it could not replace, calibrating an install with one
    RTS blind in it would silently un-configure that blind every time.
    """
    manual = {
        "full_travel_seconds": 27.0,
        "open_seconds": None,
        "close_seconds": None,
        "start_delay_seconds": 0.0,
        "calibrated_at": "2026-08-01T00:00:00+00:00",
        "source": TRAVEL_CALIBRATION_SOURCE_MANUAL,
    }
    covers = {
        "cover.rts": _FakeCover(
            hass, "cover.rts", features=_OPEN_CLOSE_FEATURES, assumed=True
        ),
        "cover.good": _FakeCover(hass, "cover.good"),
    }
    rig = _Rig(
        hass, covers, options={CONF_TRAVEL_TIME_CALIBRATION: {"cover.rts": manual}}
    )
    await rig.calibrator.async_run()

    assert rig.persisted["cover.rts"] == manual
    # …and the sibling still got measured. One skip must not void the batch.
    assert rig.persisted["cover.good"]["source"] == TRAVEL_CALIBRATION_SOURCE_MEASURED


# ------------------------------------------------------------------ #
# Failure handling
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_a_stalled_cover_times_out_without_voiding_the_batch(
    hass: HomeAssistant,
) -> None:
    covers = {
        "cover.stuck": _FakeCover(hass, "cover.stuck", stalled=True),
        "cover.good": _FakeCover(hass, "cover.good"),
    }
    rig = _Rig(hass, covers)
    await rig.calibrator.async_run()

    results = rig.calibrator.diagnostics()["results"]
    assert results["cover.stuck"]["status"] == TRAVEL_CALIBRATION_STATUS_TIMEOUT
    assert results["cover.good"]["status"] == TRAVEL_CALIBRATION_STATUS_OK
    assert "cover.stuck" not in rig.persisted
    assert "cover.good" in rig.persisted


@pytest.mark.asyncio
async def test_implausible_measurement_is_discarded(hass: HomeAssistant) -> None:
    """A sub-second 'traverse' is a device echoing its target, not a fast cover."""
    rig = _rig(hass, open_travel=0.2, close_travel=0.2, delay=0.0)
    await rig.calibrator.async_run()

    assert rig.persisted == {}
    assert (
        rig.calibrator.diagnostics()["results"]["cover.a"]["status"]
        == TRAVEL_CALIBRATION_STATUS_TIMEOUT
    )


@pytest.mark.asyncio
async def test_no_entities_reports_failure(hass: HomeAssistant) -> None:
    rig = _Rig(hass, {})
    await rig.calibrator.async_run()

    assert rig.calibrator.state == TRAVEL_CALIBRATION_STATE_FAILED
    assert rig.calibrator.diagnostics()["last_error"] == REASON_NO_ENTITIES


@pytest.mark.asyncio
async def test_stale_rows_are_pruned_at_run_start(hass: HomeAssistant) -> None:
    """A cover swapped out in the options flow must not linger in the table."""
    rig = _rig(hass)
    rig.options[CONF_TRAVEL_TIME_CALIBRATION] = {
        "cover.gone": {"full_travel_seconds": 12.0}
    }
    await rig.calibrator.async_run()

    assert "cover.gone" not in rig.persisted


# ------------------------------------------------------------------ #
# Owning the covers, and giving them back
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_automation_is_suspended_and_handed_back(hass: HomeAssistant) -> None:
    rig = _rig(hass)
    seen: list[bool] = []
    original = rig.covers["cover.a"].command

    async def _spy(entity_id, target, **kw):
        seen.append(rig.cmd_svc.calibrating)
        await original(entity_id, target, **kw)

    rig.covers["cover.a"].command = _spy
    await rig.calibrator.async_run()

    assert seen and all(seen), "automation must be suspended for every leg"
    assert rig.cmd_svc.calibrating is False


@pytest.mark.asyncio
async def test_automation_is_handed_back_even_when_the_run_raises(
    hass: HomeAssistant,
) -> None:
    """A crash mid-run must not leave the integration silently switched off."""
    rig = _rig(hass)
    rig.cmd_svc._execute_command = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await rig.calibrator.async_run()

    assert rig.cmd_svc.calibrating is False


@pytest.mark.asyncio
async def test_covers_are_restored_to_where_the_run_found_them(
    hass: HomeAssistant,
) -> None:
    rig = _rig(hass, position=42)
    await rig.calibrator.async_run()

    assert rig.commands("cover.a")[-1] == 42


@pytest.mark.asyncio
async def test_a_second_trigger_while_running_is_a_no_op(hass: HomeAssistant) -> None:
    rig = _rig(hass)
    rig.calibrator._state = TRAVEL_CALIBRATION_STATE_RUNNING
    await rig.calibrator.async_run()

    assert rig.commands("cover.a") == []


# ------------------------------------------------------------------ #
# Cancellation
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_cancel_with_restore_puts_the_covers_back(hass: HomeAssistant) -> None:
    rig = _rig(hass, position=42, stalled=True, move_timeout=5)
    task = await _park_in_wait(rig)
    await _partial_move(rig, "cover.a", 10)

    rig.calibrator.async_cancel(restore=True)
    await task

    assert rig.calibrator.state == TRAVEL_CALIBRATION_STATE_CANCELLED
    assert rig.cmd_svc.calibrating is False
    assert rig.commands("cover.a")[-1] == 42


@pytest.mark.asyncio
async def test_cancel_without_restore_issues_no_further_movement(
    hass: HomeAssistant,
) -> None:
    """What the panic stop needs: stand down, do not move anything else."""
    rig = _rig(hass, position=42, stalled=True, move_timeout=5)
    task = await _park_in_wait(rig)
    await _partial_move(rig, "cover.a", 10)
    before = list(rig.commands("cover.a"))

    rig.calibrator.async_cancel(restore=False)
    await task

    assert rig.commands("cover.a") == before
    assert rig.calibrator.state == TRAVEL_CALIBRATION_STATE_CANCELLED
    assert rig.cmd_svc.calibrating is False


@pytest.mark.asyncio
async def test_cancel_when_idle_reports_nothing_to_cancel(hass: HomeAssistant) -> None:
    rig = _rig(hass)
    assert rig.calibrator.async_cancel(restore=True) is False


# ------------------------------------------------------------------ #
# Day/night Model C — coupled rails
# ------------------------------------------------------------------ #


def _model_c_options(*, inverse: bool = False) -> dict:
    opts = {
        CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY,
        CONF_DAY_NIGHT_MIDDLE_RAIL_ENTITY: "cover.middle",
    }
    if inverse:
        opts[CONF_INVERSE_STATE] = True
    return opts


def _model_c_rig(hass) -> _Rig:
    covers = {
        "cover.bottom": _FakeCover(hass, "cover.bottom", position=30),
        "cover.middle": _FakeCover(hass, "cover.middle", position=60),
    }
    return _Rig(
        hass,
        covers,
        cover_type="cover_day_night_shade",
        options=_model_c_options(),
    )


@pytest.mark.asyncio
async def test_model_c_parks_the_partner_before_each_rail(hass: HomeAssistant) -> None:
    """Each rail's sweep is preceded by its partner vacating the track.

    The bottom rail cannot rise to fully-open unless the middle rail is already
    there; the middle rail cannot drop to fully-closed unless the bottom rail is.
    """
    rig = _model_c_rig(hass)
    await rig.calibrator.async_run()

    bottom = rig.commands("cover.bottom")
    middle = rig.commands("cover.middle")
    # The middle rail is driven fully open before the bottom rail's sweep…
    assert middle[0] == 100
    assert bottom[:3] == [0, 100, 0]
    # …and the middle rail's own sweep follows, the bottom rail already parked
    # at 0 by its own closing leg.
    assert middle[1:4] == [0, 100, 0]


@pytest.mark.asyncio
async def test_model_c_calibrates_both_rails(hass: HomeAssistant) -> None:
    rig = _model_c_rig(hass)
    await rig.calibrator.async_run()

    assert set(rig.persisted) == {"cover.bottom", "cover.middle"}


def test_model_c_parks_flipped_endpoints_on_an_inverse_install() -> None:
    """The parking spots are wire numbers, so inversion has to be applied.

    Parking the middle rail at wire 100 on an inverse install would drive it to
    fully CLOSED — into the bottom rail, which is the collision this frame
    handling exists to prevent (the #993 bug class).
    """
    policy = get_policy("cover_day_night_shade")
    rails = ["cover.bottom", "cover.middle"]

    assert policy.travel_calibration_clearance(
        "cover.bottom", rails, _model_c_options(inverse=True)
    ) == {"cover.middle": 0}
    assert policy.travel_calibration_clearance(
        "cover.bottom", rails, _model_c_options()
    ) == {"cover.middle": 100}
    assert policy.travel_calibration_clearance(
        "cover.middle", rails, _model_c_options()
    ) == {"cover.bottom": 0}


def test_clearance_is_empty_without_a_configured_middle_rail() -> None:
    """An unconfigured rail role has no safe parking spot to name."""
    policy = get_policy("cover_day_night_shade")
    assert (
        policy.travel_calibration_clearance(
            "cover.bottom",
            ["cover.bottom", "cover.middle"],
            {CONF_DAY_NIGHT_CONTROL_MODEL: DAY_NIGHT_MODEL_DUAL_ENTITY},
        )
        == {}
    )


def test_independent_cover_types_need_no_clearance() -> None:
    assert (
        get_policy("cover_blind").travel_calibration_clearance(
            "cover.a", ["cover.a", "cover.b"], {}
        )
        == {}
    )


# ------------------------------------------------------------------ #
# Leg arithmetic edge cases
# ------------------------------------------------------------------ #


def test_a_motion_stamp_at_or_after_arrival_falls_back_to_the_command_time():
    """Out-of-order publishes must not produce a zero or negative traverse.

    A negative one would propagate into a ramp that runs backwards, so the
    split falls back to timing from the command instead.
    """
    from custom_components.adaptive_cover_pro.managers.travel_calibration import (
        _Arrival,
        _Leg,
    )

    sent = dt.datetime(2026, 8, 2, 12, 0, 0, tzinfo=dt.UTC)
    arrived = sent + dt.timedelta(seconds=20)
    leg = _Leg.from_arrival(
        _Arrival(arrived_at=arrived, first_motion_at=arrived + dt.timedelta(seconds=5)),
        sent,
    )

    assert leg.travel_seconds == pytest.approx(20.0)
    assert leg.start_delay_seconds == pytest.approx(0.0)


# ------------------------------------------------------------------ #
# More failure paths
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_a_leg_that_stalls_after_the_seed_is_a_timeout(
    hass: HomeAssistant,
) -> None:
    """The cover reaches its start position, then jams on the timed sweep."""
    rig = _rig(hass, stall_after=1)
    await rig.calibrator.async_run()

    assert (
        rig.calibrator.diagnostics()["results"]["cover.a"]["status"]
        == TRAVEL_CALIBRATION_STATUS_TIMEOUT
    )
    assert rig.persisted == {}


@pytest.mark.asyncio
async def test_a_cover_that_starts_but_never_lands_times_out(
    hass: HomeAssistant,
) -> None:
    """Motion IS observed, so this is not the unobservable case — it is a jam.

    The probe is satisfied by the transit state and the wait falls through to
    the remaining per-move budget.
    """
    rig = _rig(hass, features=_OPEN_CLOSE_FEATURES, never_arrive=True)
    await rig.calibrator.async_run()

    result = rig.calibrator.diagnostics()["results"]["cover.a"]
    assert result["status"] == TRAVEL_CALIBRATION_STATUS_TIMEOUT


@pytest.mark.asyncio
async def test_a_partner_that_will_not_park_blocks_its_subject(
    hass: HomeAssistant,
) -> None:
    """No clearance, no sweep.

    Driving a rail stop-to-stop while its partner sits in the way is how a rail
    gets driven into a rail, so a failed parking move must abandon the subject
    rather than proceed hopefully.
    """
    covers = {
        "cover.bottom": _FakeCover(hass, "cover.bottom", position=30),
        "cover.middle": _FakeCover(hass, "cover.middle", position=60, stalled=True),
    }
    rig = _Rig(
        hass,
        covers,
        cover_type="cover_day_night_shade",
        options=_model_c_options(),
    )
    await rig.calibrator.async_run()

    result = rig.calibrator.diagnostics()["results"]["cover.bottom"]
    assert result["status"] == TRAVEL_CALIBRATION_STATUS_TIMEOUT
    assert result["reason"] == "clearance_failed"
    # Never SWEPT: no command to its far endpoint. It is still driven to 0 later
    # on, but as the middle rail's clearance partner — a parking move, not a
    # timed traverse — which is exactly the distinction being asserted.
    assert 100 not in rig.commands("cover.bottom")


@pytest.mark.asyncio
async def test_cancel_stops_the_batch_before_the_remaining_covers(
    hass: HomeAssistant,
) -> None:
    """A cancel must not merely finish the current cover and carry on."""
    covers = {
        "cover.a": _FakeCover(hass, "cover.a", stalled=True),
        "cover.b": _FakeCover(hass, "cover.b"),
    }
    rig = _Rig(hass, covers, move_timeout=5)
    task = await _park_in_wait(rig)

    rig.calibrator.async_cancel(restore=False)
    await task

    assert rig.commands("cover.b") == []


@pytest.mark.asyncio
async def test_cancel_during_the_observability_probe_is_not_a_verdict(
    hass: HomeAssistant,
) -> None:
    """A cancelled probe must not be recorded as "this cover cannot be measured".

    The probe never completed, so it has no finding to report — writing one
    would tell the user their cover is unmeasurable on the strength of having
    pressed Cancel.
    """
    rig = _rig(hass, features=_OPEN_CLOSE_FEATURES, stalled=True, move_timeout=5)
    rig.calibrator._probe_seconds = 5.0
    task = await _park_in_wait(rig)

    rig.calibrator.async_cancel(restore=False)
    await task

    result = rig.calibrator.diagnostics()["results"].get("cover.a", {})
    assert result.get("status") != TRAVEL_CALIBRATION_STATUS_UNOBSERVABLE


@pytest.mark.asyncio
async def test_progress_callback_fires_as_the_run_advances(
    hass: HomeAssistant,
) -> None:
    """Drives the sensor redraw, which is how a run is watched."""
    rig = _rig(hass)
    seen: list[str] = []
    rig.calibrator._on_progress = lambda: seen.append(rig.calibrator.state)

    await rig.calibrator.async_run()

    assert seen
    assert seen[-1] == TRAVEL_CALIBRATION_STATE_COMPLETE
