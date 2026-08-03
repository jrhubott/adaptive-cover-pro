"""Measure how long each cover takes to traverse its full range.

Drives every configured cover to one mechanical stop, times it to the other and
back, and averages the two legs into a ``full_travel_seconds`` figure stored per
entity in ``CONF_TRAVEL_TIME_CALIBRATION``. Once that number exists, every
subsequent dispatch can carry a ``TravelPlan`` — a position-vs-time ramp a
dashboard card can animate — for covers that report position too rarely to
animate from, or not at all.

**Cover-type-agnostic.** Endpoints come from the policy's own axis descriptor,
dispatch order from ``order_for_dispatch``, and the "move this other entity out
of the way first" question from ``travel_calibration_clearance``. Nothing here
knows what a rail is. Per CODING_GUIDELINES.md § "Managers Hold State, Policies
Hold Behavior": this manager owns the run's state and side effects, the policy
owns every decision that depends on what kind of cover it is.

**Reuses, never re-derives.** Commands go out through
``CoverCommandService._execute_command`` (the existing bypass-the-gates seam),
"has it arrived?" is ``CoverCommandService._at_target`` (the same tolerance
reconciliation uses), "is it moving?" is ``transit.is_state_in_transit``, and
"can HA even see this cover?" is ``helpers.is_assumed_state``. A second copy of
any of those would drift.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from homeassistant.const import STATE_CLOSED, STATE_OPEN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from ..const import (
    CONF_TRAVEL_TIME_CALIBRATION,
    DEFAULT_TRAVEL_CALIBRATION_TIMEOUT_SECONDS,
    TRAVEL_CALIBRATION_SOURCE_MEASURED,
    TRAVEL_CALIBRATION_STATE_CANCELLED,
    TRAVEL_CALIBRATION_STATE_COMPLETE,
    TRAVEL_CALIBRATION_STATE_FAILED,
    TRAVEL_CALIBRATION_STATE_IDLE,
    TRAVEL_CALIBRATION_STATE_RUNNING,
    TRAVEL_CALIBRATION_STATUS_OK,
    TRAVEL_CALIBRATION_STATUS_TIMEOUT,
    TRAVEL_CALIBRATION_STATUS_UNAVAILABLE,
    TRAVEL_CALIBRATION_STATUS_UNOBSERVABLE,
    REASON_ASSUMED_STATE,
    REASON_CLEARANCE_FAILED,
    REASON_IMPLAUSIBLE_RESULT,
    REASON_NO_ENTITIES,
    REASON_NO_MOTION_OBSERVED,
    REASON_RESTORE,
    TRAVEL_CALIBRATION_TRANSIT_PROBE_SECONDS,
    TRAVEL_TIME_MAX_SECONDS,
    TRAVEL_TIME_MIN_SECONDS,
)
from ..cover_types.base import caps_get
from ..helpers import is_assumed_state
from .cover_command.transit import is_state_in_transit


@dataclasses.dataclass(frozen=True, slots=True)
class _Arrival:
    """Outcome of waiting for one commanded move to land.

    ``first_motion_at`` is when the cover was first *observed* moving — a
    transit state, or a change in the axis value. It is the boundary between
    actuator dead time and actual travel, which the two must be split at: a
    figure that silently includes 4 s of Somfy IO start-up lag makes every
    ramp derived from it start early and finish late.
    """

    arrived_at: dt.datetime | None = None
    first_motion_at: dt.datetime | None = None
    unobservable: bool = False

    @property
    def ok(self) -> bool:
        """Whether the cover actually reached the commanded endpoint."""
        return self.arrived_at is not None


@dataclasses.dataclass(frozen=True, slots=True)
class _Leg:
    """One timed traverse, split into dead time and travel."""

    travel_seconds: float
    start_delay_seconds: float

    @classmethod
    def from_arrival(cls, arrival: _Arrival, sent_at: dt.datetime) -> _Leg:
        """Split a landed move into its dead-time and travel halves.

        With no observed motion the whole elapsed time is booked as travel and
        the delay as zero. That is the conservative split: over-reporting travel
        makes a ramp run slightly slow, whereas over-reporting the delay would
        make the cover appear stationary for a stretch it was actually moving.

        The same fallback catches a motion stamp at or after the arrival stamp,
        which an out-of-order publish can produce. Trusting it would yield a
        zero or negative traverse, and a negative one propagates into a ramp
        that runs backwards.
        """
        started = arrival.first_motion_at or sent_at
        if arrival.arrived_at is not None and started >= arrival.arrived_at:
            started = sent_at
        return cls(
            travel_seconds=(arrival.arrived_at - started).total_seconds(),
            start_delay_seconds=max(0.0, (started - sent_at).total_seconds()),
        )


class TravelTimeCalibrator:
    """Runs, and reports on, a travel-time calibration pass."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger,
        *,
        cmd_svc,
        policy,
        get_entities: Callable[[], Sequence[str]],
        get_options: Callable[[], Mapping[str, Any]],
        persist: Callable[[dict[str, dict[str, Any]]], Any],
        on_progress: Callable[[], None] | None = None,
        move_timeout_seconds: int = DEFAULT_TRAVEL_CALIBRATION_TIMEOUT_SECONDS,
        probe_seconds: int = TRAVEL_CALIBRATION_TRANSIT_PROBE_SECONDS,
    ) -> None:
        """Wire the calibrator to its collaborators.

        ``get_entities`` / ``get_options`` are callables rather than values so a
        run started long after construction still sees the current cover list
        and config. ``persist`` is the coroutine that writes the finished table
        back to ``config_entry.options``.
        """
        self._hass = hass
        self._logger = logger
        self._cmd_svc = cmd_svc
        self._policy = policy
        self._get_entities = get_entities
        self._get_options = get_options
        self._persist = persist
        self._on_progress = on_progress
        self._move_timeout = float(move_timeout_seconds)
        self._probe_seconds = float(probe_seconds)

        self._state: str = TRAVEL_CALIBRATION_STATE_IDLE
        self._results: dict[str, dict[str, Any]] = {}
        self._active: str | None = None
        self._last_error: str | None = None
        # Created per run. Set by ``async_cancel`` and raced against every
        # arrival wait, so a cancel takes effect at the next observation rather
        # than sitting behind a 180 s timeout.
        self._abort: asyncio.Event | None = None
        # A second, stronger signal. The restore pass deliberately ignores
        # ``_abort`` — it runs after a cancel has already been raised and still
        # has to wait for each cover in turn — so without this it would be
        # uncancellable, and an unload would sit through a full per-move timeout
        # per cover. Set by an unload/panic cancel (``restore=False``), never by
        # the options-flow Cancel, which is asking for the restore to happen.
        self._hard_abort: asyncio.Event | None = None
        self._restore_on_cancel: bool = True

    # ------------------------------------------------------------------ #
    # Public surface
    # ------------------------------------------------------------------ #

    @property
    def state(self) -> str:
        """Lifecycle state of the most recent (or current) run."""
        return self._state

    @property
    def is_running(self) -> bool:
        """Whether a run currently owns the covers."""
        return self._state == TRAVEL_CALIBRATION_STATE_RUNNING

    @property
    def active_entity(self) -> str | None:
        """The cover currently being swept, or None."""
        return self._active

    def diagnostics(self) -> dict[str, Any]:
        """Snapshot for the diagnostic sensor and the downloadable diagnostics."""
        return {
            "state": self._state,
            "active_entity": self._active,
            "last_error": self._last_error,
            "results": {eid: dict(row) for eid, row in self._results.items()},
        }

    @callback
    def async_cancel(self, *, restore: bool = True) -> bool:
        """Stop an in-flight run. Returns whether there was one to stop.

        ``restore=True`` (the options-flow Cancel button) still puts the covers
        back where the run found them. ``restore=False`` is for the panic stop
        service, where more movement is precisely what the user is asking to
        avoid — the run stands down and the covers stay put.
        """
        if not self.is_running or self._abort is None:
            return False
        self._restore_on_cancel = restore
        self._abort.set()
        if not restore and self._hard_abort is not None:
            # "Stop moving" means stop moving, including a restore already under
            # way — the panic-stop and unload callers want no further commands.
            self._hard_abort.set()
        return True

    async def async_run(self) -> None:
        """Calibrate every configured cover, then restore their positions.

        Sequential by design. Concurrent sweeps would be faster, but a cover
        type with physically coupled entities cannot have two of them travelling
        at once, and the per-entity clearance parking (§ policy hook) only
        guarantees a clear track for ONE subject at a time.
        """
        if self.is_running:
            self._logger.debug("Travel-time calibration already running — ignoring")
            return

        entities = list(self._get_entities() or [])
        if not entities:
            self._state = TRAVEL_CALIBRATION_STATE_FAILED
            self._last_error = REASON_NO_ENTITIES
            self._notify()
            return

        # Refresh the policy's option-derived state before anything asks it a
        # question. A run can be started from the options flow at any time, and
        # the clearance gate the restore leans on reads role state the
        # coordinator normally primes per cycle — cold, it would report every
        # coupled entity free to move.
        self._policy.sync_runtime_options(dict(self._get_options()))

        self._abort = asyncio.Event()
        self._hard_abort = asyncio.Event()
        self._restore_on_cancel = True
        self._results = {}
        self._last_error = None
        self._state = TRAVEL_CALIBRATION_STATE_RUNNING
        # Hand the covers over: automation and reconciliation both stand down
        # until the finally below gives them back.
        self._cmd_svc.calibrating = True
        self._notify()

        # Only what THIS run measured. Deliberately not a copy of the stored
        # table: the run is long, and anything else that writes to the table
        # while it is going (a hand-entered time saved from the options flow)
        # would be erased when a start-of-run snapshot was written back at the
        # end. The merge against live options happens at persist time instead.
        measured: dict[str, dict[str, Any]] = {}
        # Captured before anything moves, so the restore pass can put every
        # cover back exactly where the user left it.
        origins = {eid: self._cmd_svc.get_current_position(eid) for eid in entities}
        failed = False

        try:
            for subject in self._policy.order_for_dispatch(entities):
                if self._aborted:
                    break
                self._active = subject
                self._notify()
                row = await self._calibrate_entity(subject, entities)
                if row is not None:
                    measured[subject] = row
        except Exception:
            # Reported as failed rather than swallowed — the exception still
            # propagates, but the sensor must not claim the run completed.
            failed = True
            raise
        finally:
            self._active = None
            try:
                if not self._aborted or self._restore_on_cancel:
                    await self._restore(origins)
            except Exception:  # noqa: BLE001 — see below
                # Swallowed deliberately, and this is the one place in the module
                # where that is right. Everything after this point is what makes
                # the run recoverable: clearing ``calibrating``, leaving a
                # terminal state, and persisting the measurements. Letting a
                # failed go-home move escape would skip all three and strand the
                # calibrator reporting ``running`` forever — after which every
                # later run early-returns at the is_running guard and the feature
                # is dead for the life of the config entry.
                self._logger.exception(
                    "Travel-time calibration could not return the covers to "
                    "their original positions"
                )
                failed = True
            finally:
                # Always, on every path: a run that leaves this set has switched
                # automation off indefinitely with no visible cause.
                self._cmd_svc.calibrating = False

            if failed:
                self._state = TRAVEL_CALIBRATION_STATE_FAILED
            elif self._aborted:
                self._state = TRAVEL_CALIBRATION_STATE_CANCELLED
            else:
                self._state = TRAVEL_CALIBRATION_STATE_COMPLETE
            # A hard abort is an unload or a panic stop. Writing options during
            # an unload dispatches the entry's update listeners — via a task HA
            # does NOT scope to the entry — which would run a full refresh on the
            # coordinator that just shut down. Discarding this run's measurements
            # is the cheaper loss by a wide margin, and a panic stop discarding a
            # half-finished sweep is what the button means anyway.
            #
            # An ordinary cancel or a failure DOES persist: those entities really
            # were measured, and dropping them would mean re-sweeping covers that
            # were already done.
            #
            # Expressed as a condition rather than an early return: a ``return``
            # inside a ``finally`` discards whatever exception is in flight, and
            # on the unload path that exception is the CancelledError this branch
            # exists to handle.
            if not self._hard_aborted:
                await self._persist(self._merged_table(entities, measured))
            self._notify()

    # ------------------------------------------------------------------ #
    # Per-entity run
    # ------------------------------------------------------------------ #

    async def _calibrate_entity(
        self, subject: str, entities: Sequence[str]
    ) -> dict[str, Any] | None:
        """Sweep one cover and return its table row, or None if it was skipped."""
        if self._cmd_svc.is_entity_unavailable(subject):
            self._record(subject, TRAVEL_CALIBRATION_STATUS_UNAVAILABLE)
            return None

        # An assumed-state cover reports open/closed the instant the command is
        # accepted, so "arrival" is the round trip of a service call, not a
        # traverse. Timing it would produce a confidently wrong number, which is
        # worse than no number: manual entry is the honest path for these.
        if is_assumed_state(self._hass.states.get(subject)):
            self._record(
                subject,
                TRAVEL_CALIBRATION_STATUS_UNOBSERVABLE,
                reason=REASON_ASSUMED_STATE,
            )
            return None

        caps = self._cmd_svc.get_cover_capabilities(subject)
        axis = self._policy.select_default_axis(caps)

        if not await self._apply_clearance(subject, entities):
            self._record(
                subject,
                TRAVEL_CALIBRATION_STATUS_TIMEOUT,
                reason=REASON_CLEARANCE_FAILED,
            )
            return None

        # Seed — untimed. Only job is to put the cover on a known endpoint so
        # the legs that follow are genuinely full-range sweeps.
        seed = await self._drive_and_wait(subject, axis.value_min, caps=caps)
        if seed.unobservable:
            self._record(
                subject,
                TRAVEL_CALIBRATION_STATUS_UNOBSERVABLE,
                reason=REASON_NO_MOTION_OBSERVED,
            )
            return None
        if not seed.ok:
            return self._timeout(subject)

        legs: list[_Leg] = []
        for target in (axis.value_max, axis.value_min):
            sent_at = self._now()
            arrival = await self._drive_and_wait(subject, target, caps=caps)
            if not arrival.ok:
                return self._timeout(subject)
            legs.append(_Leg.from_arrival(arrival, sent_at))

        opening, closing = legs
        return self._build_row(subject, opening=opening, closing=closing)

    def _build_row(
        self, subject: str, *, opening: _Leg, closing: _Leg
    ) -> dict[str, Any] | None:
        """Turn two timed legs into a persisted row, or reject an absurd result.

        A sweep faster than ``TRAVEL_TIME_MIN_SECONDS`` almost always means the
        cover reported arrival without moving (a device that echoes the target
        back before acting), and one slower than ``TRAVEL_TIME_MAX_SECONDS``
        means something stalled. Both would produce ramps wrong enough to look
        broken, so neither is stored — the same band the manual-entry field is
        bounded by, read from the same constants.
        """
        full = (opening.travel_seconds + closing.travel_seconds) / 2
        if not TRAVEL_TIME_MIN_SECONDS <= full <= TRAVEL_TIME_MAX_SECONDS:
            self._logger.warning(
                "Travel-time calibration for %s produced an implausible %.1fs "
                "full traverse — discarding",
                subject,
                full,
            )
            self._record(
                subject,
                TRAVEL_CALIBRATION_STATUS_TIMEOUT,
                reason=REASON_IMPLAUSIBLE_RESULT,
                full_travel_seconds=round(full, 2),
            )
            return None

        row = {
            "full_travel_seconds": round(full, 2),
            "open_seconds": round(opening.travel_seconds, 2),
            "close_seconds": round(closing.travel_seconds, 2),
            "start_delay_seconds": round(
                (opening.start_delay_seconds + closing.start_delay_seconds) / 2, 2
            ),
            "calibrated_at": self._now().isoformat(),
            "source": TRAVEL_CALIBRATION_SOURCE_MEASURED,
        }
        self._record(subject, TRAVEL_CALIBRATION_STATUS_OK, **row)
        return row

    async def _apply_clearance(self, subject: str, entities: Sequence[str]) -> bool:
        """Park whatever the policy says is standing in ``subject``'s way."""
        plan = self._policy.travel_calibration_clearance(
            subject, entities, self._get_options()
        )
        for partner, park_at in plan.items():
            if partner == subject:
                continue
            caps = self._cmd_svc.get_cover_capabilities(partner)
            arrival = await self._drive_and_wait(partner, park_at, caps=caps)
            if not arrival.ok:
                self._logger.warning(
                    "Travel-time calibration: could not park %s at %s%% to clear "
                    "the way for %s",
                    partner,
                    park_at,
                    subject,
                )
                return False
        return True

    async def _restore(self, origins: Mapping[str, int | None]) -> None:
        """Put every cover back where the run found it, one at a time.

        Ordering matters for the same reason it matters during a sweep: coupled
        entities cannot pass through each other. But this pass cannot derive an
        order for itself. ``order_for_dispatch`` is keyed on the single number a
        seam is fanning out and a restore sends every cover somewhere different;
        sorting by distance instead looks plausible and is wrong — it ties when
        two rails share an origin, and after a cancel the covers are scattered
        mid-travel with no shared starting point to reason from.

        So the order is not computed here at all. Each candidate is offered to
        ``await_dispatch_clearance`` — the policy's own "may this entity move
        there right now?" gate, the same one ``apply_position`` and
        reconciliation consult — and only the ones it clears are dispatched.
        Whatever was blocked is re-offered on the next lap, by which point the
        cover in its way has landed. That is correct in every geometry and both
        frames without this manager knowing what a rail is.

        The loop stops as soon as a lap moves nothing: the remainder is either
        genuinely stuck or its blocker never arrived, and spinning would hold
        the covers hostage. A cover that will not go home is a cosmetic
        annoyance, not a reason to fail a run whose measurements succeeded.
        """
        pending = {eid: pos for eid, pos in origins.items() if pos is not None}
        while pending and not self._hard_aborted:
            moved = False
            for entity_id in list(pending):
                if not await self._policy.await_dispatch_clearance(
                    entity_id,
                    position=pending[entity_id],
                    reason=REASON_RESTORE,
                    wait=False,
                ):
                    continue
                caps = self._cmd_svc.get_cover_capabilities(entity_id)
                await self._drive_and_wait(
                    entity_id, pending.pop(entity_id), caps=caps, ignore_abort=True
                )
                moved = True
                if self._hard_aborted:
                    return
            if not moved:
                self._logger.debug(
                    "Travel-time calibration: %d cover(s) could not be returned "
                    "to their original position — nothing left is clear to move",
                    len(pending),
                )
                return

    # ------------------------------------------------------------------ #
    # Driving and observing one move
    # ------------------------------------------------------------------ #

    async def _drive_and_wait(
        self,
        entity_id: str,
        target: int,
        *,
        caps: Mapping[str, bool] | None,
        ignore_abort: bool = False,
    ) -> _Arrival:
        """Command ``entity_id`` to ``target`` and wait for it to land there.

        Subscribes BEFORE dispatching: a fast cover can publish its whole
        transit between the service call returning and a later subscription
        taking effect, and a missed arrival is indistinguishable from a stall.

        ``ignore_abort`` is for the restore pass, which runs AFTER a cancel has
        already been signalled. Without it every wait there would see the abort
        event already set and return on its first scheduling — turning a
        deliberately one-at-a-time pass into a simultaneous fan-out, on the one
        path where coupled entities have no clearance plan protecting them.
        """
        if self._already_at(entity_id, target, caps):
            # Nothing to observe and nothing to time. Skipping the command also
            # spares the motor a relay click it would gain nothing from (#290).
            return _Arrival(arrived_at=self._now())

        axis = self._policy.select_default_axis(caps or {})
        # Same axis question as _is_arrival — see there for why this is not
        # ``position_axis_supported``. Getting it wrong here also mis-routes the
        # wait into the observability probe.
        position_capable = caps_get(caps, axis.capability_key, default=True)
        origin = self._cmd_svc.get_current_position(entity_id)
        loop = asyncio.get_running_loop()
        arrived: asyncio.Future[dt.datetime] = loop.create_future()
        # Boxed so the callback can write it without a nonlocal declaration
        # across the closure boundary.
        motion: list[dt.datetime | None] = [None]

        @callback
        def _on_change(event) -> None:
            new_state = event.data.get("new_state")
            if new_state is None:
                return
            # The EVENT's timestamp, not ``now()`` at handler entry: a busy loop
            # can defer this callback by tens of milliseconds, and that error
            # would land straight in the measurement.
            stamp = event.time_fired
            if is_state_in_transit(new_state.state):
                if motion[0] is None:
                    motion[0] = stamp
                # Still travelling — it cannot have arrived, whatever the
                # position attribute says while the motor is running.
                return
            landed = self._is_arrival(new_state, target, caps)
            value = new_state.attributes.get(axis.state_attr)
            if (
                motion[0] is None
                and not landed
                and value is not None
                and value != origin
            ):
                # A cover that publishes positions but never an opening/closing
                # state still shows its start this way — but ONLY from an
                # intermediate publish. A cover that reports nothing until it is
                # already home would otherwise mark motion and arrival at the
                # same instant, and the traverse would measure zero.
                motion[0] = stamp
            if landed and not arrived.done():
                arrived.set_result(stamp)

        unsub = async_track_state_change_event(self._hass, [entity_id], _on_change)
        try:
            # Deliberately the reconciliation seam: it already routes through
            # the policy's axis, applies open/close substitution, books the
            # target and stamps the ACP context so the external-command
            # detector does not read our own sweep as a user move.
            await self._cmd_svc._execute_command(entity_id, target)  # noqa: SLF001
            return await self._await_outcome(
                arrived,
                motion,
                probe=not position_capable,
                ignore_abort=ignore_abort,
            )
        finally:
            unsub()

    async def _await_outcome(
        self,
        arrived: asyncio.Future[dt.datetime],
        motion: list[dt.datetime | None],
        *,
        probe: bool,
        ignore_abort: bool = False,
    ) -> _Arrival:
        """Wait for arrival, giving position-less covers an observability probe.

        A cover with no position axis is watched purely through its
        ``opening``/``closing`` states. Some devices never publish those and
        report the destination state immediately — indistinguishable, from the
        outside, from a cover that teleported. The probe catches exactly that:
        no motion seen in the first few seconds means there is nothing here to
        time, and the entity is reported unobservable rather than credited with
        an instantaneous traverse.
        """
        if probe:
            stamp = await self._wait_for(
                arrived, self._probe_seconds, ignore_abort=ignore_abort
            )
            if self._aborted and not ignore_abort:
                return _Arrival(None, motion[0])
            if motion[0] is None:
                # Nothing was seen to move. Note this is checked BEFORE the
                # arrival stamp, because the snapping cover this catches does
                # claim to arrive — instantly, without ever having moved. Taking
                # that claim at face value would book a zero-second traverse.
                return _Arrival(unobservable=True)
            if stamp is not None:
                return _Arrival(stamp, motion[0])
            remaining = max(0.0, self._move_timeout - self._probe_seconds)
        else:
            remaining = self._move_timeout

        stamp = await self._wait_for(arrived, remaining, ignore_abort=ignore_abort)
        return _Arrival(stamp, motion[0])

    async def _wait_for(
        self,
        arrived: asyncio.Future[dt.datetime],
        timeout: float,
        *,
        ignore_abort: bool = False,
    ) -> dt.datetime | None:
        """Arrival stamp, or None if the wait timed out or the run was cancelled.

        Races the abort event alongside the timeout so Cancel lands at the next
        scheduling point instead of after the full per-move budget.

        ``ignore_abort`` drops that race for the restore pass, which by
        definition runs with the abort already set and still has to wait for
        each cover in turn.
        """
        waiters: set[asyncio.Future] = {arrived}
        # ``ignore_abort`` drops only the ordinary cancel. The hard abort is
        # raced either way, so an unload or a panic stop is never left waiting
        # out a per-move timeout.
        signal = self._hard_abort if ignore_abort else self._abort
        abort_task: asyncio.Task | None = None
        if signal is not None:
            abort_task = asyncio.ensure_future(signal.wait())
            waiters.add(abort_task)
        try:
            done, _pending = await asyncio.wait(
                waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            if abort_task is not None:
                abort_task.cancel()
        return arrived.result() if arrived in done else None

    def _is_arrival(self, state_obj, target: int, caps) -> bool:
        """Whether ``state_obj`` shows the cover parked on the commanded target."""
        axis = self._policy.select_default_axis(caps or {})
        # ``axis.capability_key``, NOT ``position_axis_supported`` — the latter
        # always asks about ``axes[0]``, while ``select_default_axis`` returns
        # the TILT axis for a position-primary policy bound to tilt-only
        # hardware. Pairing the two asks whether the cover supports one axis and
        # then reads another: the tilt reading is discarded, arrival never
        # resolves, and the cover burns three full timeouts. This is the same
        # pairing ``CoverTypePolicy.read_axis_value`` makes.
        if caps_get(caps, axis.capability_key, default=True):
            value = state_obj.attributes.get(axis.state_attr)
            if value is None:
                return False
            # The same tolerance predicate check_target_reached and
            # reconciliation use — a second "close enough" rule would let a
            # cover be simultaneously arrived and not arrived.
            return self._cmd_svc._at_target(int(value), target)  # noqa: SLF001
        # Open/close-only: the endpoint states are the only evidence there is.
        # Calibration never targets anything but the two endpoints, so this is
        # total rather than a partial mapping.
        if target <= axis.value_min:
            return state_obj.state == STATE_CLOSED
        if target >= axis.value_max:
            return state_obj.state == STATE_OPEN
        return False

    def _already_at(self, entity_id: str, target: int, caps) -> bool:
        """Whether the cover is parked on ``target`` right now."""
        state_obj = self._hass.states.get(entity_id)
        if state_obj is None or is_state_in_transit(state_obj.state):
            return False
        return self._is_arrival(state_obj, target, caps)

    # ------------------------------------------------------------------ #
    # Bookkeeping
    # ------------------------------------------------------------------ #

    @property
    def _aborted(self) -> bool:
        return self._abort is not None and self._abort.is_set()

    @property
    def _hard_aborted(self) -> bool:
        return self._hard_abort is not None and self._hard_abort.is_set()

    def _now(self) -> dt.datetime:
        return dt.datetime.now(dt.UTC)

    def _timeout(self, subject: str) -> None:
        """Record a leg that never landed, and return None for the row.

        Silent when the run was cancelled: the leg did not fail, it was
        interrupted, and reporting a timeout would blame the cover for the
        user's own Cancel.
        """
        if self._aborted:
            return None
        self._logger.warning(
            "Travel-time calibration timed out for %s — skipping it", subject
        )
        self._record(subject, TRAVEL_CALIBRATION_STATUS_TIMEOUT)
        return None

    def _record(self, entity_id: str, status: str, **extra: Any) -> None:
        self._results[entity_id] = {"status": status, **extra}
        self._notify()

    def _merged_table(
        self, entities: Iterable[str], measured: Mapping[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Rebase this run's measurements onto the table as it stands NOW.

        Read at persist time, not run start. A sweep takes minutes, and the
        options flow can write a hand-entered time for an unmeasurable cover in
        the meantime — that write is the only way such a cover ever gets a time,
        so overwriting it with a stale snapshot would undo the one thing the
        user could do for it. This is the mirror of the flow's own save-time
        merge; between them, neither side can clobber the other.

        Also drops rows for covers this instance no longer controls: swapping a
        cover out would otherwise leave its row behind forever, inflating the
        "N of M calibrated" count and keeping a dead entity in diagnostics.
        """
        stored = self._get_options().get(CONF_TRAVEL_TIME_CALIBRATION) or {}
        keep = set(entities)
        table = {eid: dict(row) for eid, row in stored.items() if eid in keep}
        table.update({eid: dict(row) for eid, row in measured.items() if eid in keep})
        return table

    def _notify(self) -> None:
        if self._on_progress is not None:
            self._on_progress()
