"""Dual-axis cover-command sequencer for venetian blinds.

Real-motor venetian blinds (KNX, Somfy IO, Shelly 2PM) back-rotate the
slats while moving vertically: firing ``set_cover_position`` and
``set_cover_tilt_position`` simultaneously leaves tilt drifting. The
sequencer runs the position command first, polls ``current_position``
until the cover settles (or a timeout / no-progress sample budget fires),
then sends the tilt command — overriding the motor back-rotate exactly
once, after vertical motion has finished.

Owned by ``VenetianPolicy``; constructed when the policy is attached to
the coordinator. Other cover-type policies have no sequencer at all.

Co-located with ``policy.py`` under ``cover_types/venetian/`` so the
venetian-only state (back-rotate window, tilt targets, verify cache)
lives alongside the policy that owns it. Per CODING_GUIDELINES.md
"Managers Hold State, Policies Hold Behavior", cover-type-bound
machinery belongs next to its policy, not in the cover-type-agnostic
``managers/`` package.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.components.cover.const import DOMAIN as COVER_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_SET_COVER_TILT_POSITION,
)
from homeassistant.exceptions import HomeAssistantError

from ...const import (
    ATTR_TILT_POSITION,
    DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS,
    VENETIAN_BACKROTATE_MAX_DELTA_PERCENT,
    VENETIAN_POSITION_SETTLE_NO_CHANGE_SAMPLES,
    VENETIAN_POSITION_SETTLE_POLL_SECONDS,
    VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS,
    VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS,
    VENETIAN_POST_TILT_REBASE_DELAY_SECONDS,
    VENETIAN_REBASE_MAX_DRIFT_PERCENT,
    VENETIAN_TILT_SUPPRESSION_SECONDS,
    VENETIAN_TILT_VERIFY_MAX_SAMPLES,
    VENETIAN_TILT_VERIFY_POLL_SECONDS,
    VENETIAN_TILT_VERIFY_TOLERANCE,
)
from ...managers.cover_command.gates import check_position_delta
from ...managers.manual_override import inverse_state

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ...diagnostics.event_buffer import EventBuffer

# HA cover states that indicate the motor is still mid-travel.
_COVER_MOVING_STATES = frozenset({"opening", "closing"})

# Reason codes for tilt_command_skipped events.
_TILT_SKIP_DRY_RUN = "dry_run"
_TILT_SKIP_TARGET_UNCHANGED = "target_unchanged"
_TILT_SKIP_SERVICE_FAILED = "service_call_failed"
_TILT_SKIP_DELTA_TOO_SMALL = "delta_too_small"

# Reason codes for rebase_skipped events.
_REBASE_SKIP_SETTLE_FAILED = "settle_failed"

# Anchor sources for the tilt min-delta gate (issue #33). The gate compares
# the new target against either the live actuator reading (preferred) or the
# previously-stored target (fallback when the actuator can't be read).
_ANCHOR_SOURCE_ACTUAL = "actual"
_ANCHOR_SOURCE_TARGET_FALLBACK = "target_fallback"


class DualAxisSequencer:
    """Position→settle→tilt sequencer + tilt-axis suppression window."""

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        logger,
        grace_mgr,
        get_current_position: Callable[[str], int | None],
        set_commanded_position: Callable[[str, int], None],
        position_tolerance: int,
        is_dry_run: Callable[[], bool],
        get_state: Callable[[str], str | None] | None = None,
        get_current_tilt_position: Callable[[str], int | None] | None = None,
        event_buffer: EventBuffer | None = None,
        invert_tilt: Callable[[], bool] | None = None,
        get_min_change: Callable[[], int] | None = None,
        post_settle_hold_seconds: float = DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS,
    ) -> None:
        """Bind HA + cmd_svc dependencies; per-entity timestamps start empty."""
        self._hass = hass
        self._logger = logger
        self._grace_mgr = grace_mgr
        self._get_current_position = get_current_position
        self._set_commanded_position = set_commanded_position
        self._position_tolerance = position_tolerance
        self._is_dry_run = is_dry_run
        self._get_state = get_state
        self._get_current_tilt_position = get_current_tilt_position
        self._event_buffer = event_buffer
        self._invert_tilt = invert_tilt
        self._get_min_change = get_min_change
        self._post_settle_hold_seconds = post_settle_hold_seconds
        # Per-entity timestamps. Keep these on the sequencer (rather than on
        # CoverCommandService.PerEntityState) so non-venetian covers carry no
        # dual-axis state at all.
        self._suppression_at: dict[str, dt.datetime] = {}
        self._tilt_targets: dict[str, int] = {}
        # Entities whose last stored target has been verified against the
        # actuator. Dedup at _send_tilt_command only fires when the target
        # matches AND the entity is in this set — a verify=False fire-and-
        # forget send stores the target but does not mark verified, so the
        # subsequent verifying send still runs (issue #33).
        self._tilt_targets_verified: set[str] = set()
        self._tilt_sent_at: dict[str, dt.datetime] = {}

    # -- tilt inversion ---------------------------------------------------- #

    def _to_wire(self, tilt: int) -> int:
        """Convert logical tilt to wire value, applying inversion if configured.

        Symmetric: applied to a logical value yields wire; applied to a wire
        value yields logical. Both directions go through the same inversion
        check, so callers reading the actuator can use this to compare
        against a logical target.
        """
        if self._invert_tilt is not None and self._invert_tilt():
            return inverse_state(tilt)
        return tilt

    def _resolve_tilt_anchor(self, entity_id: str) -> tuple[int | None, str]:
        """Return ``(anchor, source)`` for the tilt min-delta gate.

        Issue #33: the gate must anchor on the actuator's live tilt to avoid
        comparing against a stale stored target (the motor auto-tilts on
        close, leaving ``_tilt_targets`` out of sync with reality).

        Returns
        -------
        ``(value, source)`` where:
          * ``value`` is a logical tilt position (``0..100``) or ``None`` if
            neither the actuator nor a stored target is available.
          * ``source`` is :data:`_ANCHOR_SOURCE_ACTUAL` when the live read
            succeeded, or :data:`_ANCHOR_SOURCE_TARGET_FALLBACK` when we fell
            back to the stored target.

        """
        if self._get_current_tilt_position is not None:
            wire = self._get_current_tilt_position(entity_id)
            if wire is not None:
                return self._to_wire(wire), _ANCHOR_SOURCE_ACTUAL
        return self._tilt_targets.get(entity_id), _ANCHOR_SOURCE_TARGET_FALLBACK

    # -- suppression window ------------------------------------------------ #

    def stamp_position_command(self, entity_id: str) -> None:
        """Record that a ``set_cover_position`` was just emitted."""
        self._suppression_at[entity_id] = dt.datetime.now(dt.UTC)

    def is_in_suppression(self, entity_id: str) -> bool:
        """Return whether the back-rotate window is still open for this cover."""
        ts = self._suppression_at.get(entity_id)
        if ts is None:
            return False
        elapsed = (dt.datetime.now(dt.UTC) - ts).total_seconds()
        return elapsed < VENETIAN_TILT_SUPPRESSION_SECONDS

    def is_in_suppression_with_cap(self, entity_id: str, delta: float) -> bool:
        """Suppress back-rotate drift only when the delta is plausibly motor drift.

        Slat geometry bounds back-rotation magnitude post-settle; a large delta
        once the carriage has stopped is a user move, not motor drift, so the
        manual-override path runs and the user's command is recorded (issue #33
        follow-on).

        While the carriage is still mid-travel (HA cover state ``opening`` or
        ``closing``) the cap is bypassed: real-world venetian actuators (KNX,
        Shelly 2PM, FGR223) back-retract the slats by well over the cap during
        carriage motion, so an arbitrarily large delta is still motor drift
        until ``cover.state`` settles. The cap reasserts once state leaves the
        moving set.

        A post-settle grace tail (``VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS``)
        also bypasses the cap: KNX/Shelly actuators publish their tilt-walk
        burst after ``cover.state`` already reads ``open``/``closed``, so the
        cap would reject legitimate motor drift as a user move without this
        window (issue #33).
        """
        if not self.is_in_suppression(entity_id):
            return False
        if self._get_state is not None:
            state = self._get_state(entity_id)
            if state in _COVER_MOVING_STATES:
                return True
        stamp = self._suppression_at.get(entity_id)
        if stamp is not None:
            elapsed = (dt.datetime.now(dt.UTC) - stamp).total_seconds()
            if elapsed < VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS:
                return True
        return delta <= VENETIAN_BACKROTATE_MAX_DELTA_PERCENT

    # -- tilt sequence ----------------------------------------------------- #

    def last_tilt_target(self, entity_id: str) -> int | None:
        """Return the last tilt target sent (for diagnostics / tests)."""
        return self._tilt_targets.get(entity_id)

    def clear_tilt_targets(self) -> None:
        """Forget every stored tilt target — anchor falls back to live actuator reads.

        Defense-in-depth hook for Auto Control off→on transitions (issue #33).
        Suppression timestamps are intentionally untouched — the back-rotate
        window is a time-based safeguard, independent of the stored-target
        cache.
        """
        self._tilt_targets.clear()
        self._tilt_targets_verified.clear()

    async def run_sequence(
        self,
        entity_id: str,
        *,
        position_target: int,
        tilt_target: int,
        reason: str,
    ) -> None:
        """Wait for vertical motion to settle, then send the tilt command."""
        settled, _last = await self._wait_for_position_settle(
            entity_id, position_target
        )
        await asyncio.sleep(self._post_settle_hold_seconds)
        # The window protects the position-axis settle + tilt-induced back-drive.
        # Only the position-sequence path owns this stamp; tilt-only sends from
        # update_tilt_only must not extend it (issue #33 follow-on).
        self.stamp_position_command(entity_id)
        await self._send_tilt_command(
            entity_id,
            tilt_target=tilt_target,
            position_target=position_target,
            reason=reason,
            position_settled=settled,
        )

    async def _send_tilt_command(
        self,
        entity_id: str,
        *,
        tilt_target: int,
        position_target: int,
        reason: str,
        force: bool = False,
        position_settled: bool = True,
        verify: bool = True,
    ) -> None:
        """Emit ``set_cover_tilt_position`` and rebase the commanded position.

        Shared by ``run_sequence`` (post-settle chase) and ``update_tilt_only``
        (tilt-only update when position hasn't changed).

        The min-delta gate is anchored on the live actuator reading (issue
        #33) with fallback to the stored target when current tilt is
        unavailable — without this, a stale stored target (e.g. set before
        the motor auto-tilted on close) skips legitimate moves.

        A target-unchanged dedup runs first: if the stored target already
        matches and the caller didn't pass ``force=True``, no service call
        fires. That keeps ``run_sequence``'s post-settle tilt from re-sending
        a tilt that ``before_position_command`` already sent for the same
        opening transition — total service-call count for an opening
        transition remains 2 (tilt + position).

        But the dedup branch still runs the verify step when the stored
        target hasn't yet been confirmed against the actuator (issue #33
        tilt-first path). Otherwise a misbehaving actuator that lands at
        the wrong tilt during the carriage travel is never noticed: no
        drift event, no ``_tilt_targets`` pop, no retry on the next cycle.
        ``_tilt_targets_verified`` tracks which stored targets have already
        been confirmed so the verify only fires once per target.

        ``verify=False`` is the fire-and-forget mode used by
        ``before_position_command``: the service call dispatches and the
        target is recorded, but the post-tilt sleep, verify, and rebase are
        all skipped. Verifying the pre-position tilt is pointless because
        the actuator hasn't published yet AND the position command is about
        to move the carriage; verification would race both signals.
        """
        if not force and tilt_target == self._tilt_targets.get(entity_id):
            self._record_event(
                "tilt_command_skipped",
                reason=_TILT_SKIP_TARGET_UNCHANGED,
                entity_id=entity_id,
                tilt_position=tilt_target,
                position_target=position_target,
                trigger=reason,
            )
            if verify and entity_id not in self._tilt_targets_verified:
                await asyncio.sleep(VENETIAN_POST_TILT_REBASE_DELAY_SECONDS)
                await self._verify_and_record_tilt(entity_id, tilt_target)
            return

        if not force and self._get_min_change is not None:
            anchor, anchor_source = self._resolve_tilt_anchor(entity_id)
            if anchor is not None and not check_position_delta(
                entity_id,
                tilt_target,
                self._get_min_change(),
                None,
                position=anchor,
                logger=self._logger,
                axis_label="tilt",
            ):
                self._record_event(
                    "tilt_command_skipped",
                    reason=_TILT_SKIP_DELTA_TOO_SMALL,
                    entity_id=entity_id,
                    tilt_position=tilt_target,
                    position_target=position_target,
                    trigger=reason,
                    prior_tilt_target=self._tilt_targets.get(entity_id),
                    anchor_value=anchor,
                    anchor_source=anchor_source,
                    min_delta_required=self._get_min_change(),
                )
                return

        if self._is_dry_run():
            self._logger.info(
                "[dry_run] would send cover.set_cover_tilt_position %s → %s%%",
                entity_id,
                tilt_target,
            )
            self._record_event(
                "tilt_command_skipped",
                reason=_TILT_SKIP_DRY_RUN,
                entity_id=entity_id,
                tilt_position=tilt_target,
                position_target=position_target,
                trigger=reason,
            )
            return

        self._tilt_targets[entity_id] = tilt_target  # store logical value
        # A freshly-sent target is unverified until _verify_and_record_tilt
        # confirms it lands on the actuator (issue #33). Discard preemptively
        # in case a prior cycle marked the entity verified.
        self._tilt_targets_verified.discard(entity_id)
        self._tilt_sent_at[entity_id] = dt.datetime.now(dt.UTC)
        # Restart the grace window so the tilt-axis change isn't read as a
        # user touch by manual_override detection.
        self._grace_mgr.start_command_grace_period(entity_id)

        wire_target = self._to_wire(tilt_target)
        self._logger.info(
            "[%s] Tilt %s → %s%% (wire: %s%%) (paired with position %s%%)",
            reason,
            entity_id,
            tilt_target,
            wire_target,
            position_target,
        )

        try:
            await self._hass.services.async_call(
                COVER_DOMAIN,
                SERVICE_SET_COVER_TILT_POSITION,
                {ATTR_ENTITY_ID: entity_id, ATTR_TILT_POSITION: wire_target},
            )
        except HomeAssistantError as err:
            self._logger.warning(
                "Service call %s.%s failed for %s: %s",
                COVER_DOMAIN,
                SERVICE_SET_COVER_TILT_POSITION,
                entity_id,
                err,
            )
            self._record_event(
                "tilt_command_skipped",
                reason=_TILT_SKIP_SERVICE_FAILED,
                entity_id=entity_id,
                tilt_position=tilt_target,
                position_target=position_target,
                trigger=reason,
            )
            return

        self._record_event(
            "tilt_command_sent",
            entity_id=entity_id,
            tilt_position=tilt_target,
            position_target=position_target,
            trigger=reason,
        )

        if not verify:
            return

        # Wait for the motor's mechanical back-drive on the vertical axis to
        # settle before reading current_position for the rebase. Without this
        # delay the read races the asynchronous back-drive and captures the
        # pre-settle value, causing the rebase to see zero drift and skip.
        await asyncio.sleep(VENETIAN_POST_TILT_REBASE_DELAY_SECONDS)

        # Verify the tilt actually landed. On slow/racing hardware the motor
        # may back-rotate the slats during position movement, leaving the cover
        # at tilt=0 even though we sent tilt=N. If we detect drift, clear the
        # recorded target so the next update_tilt_only cycle retries.
        await self._verify_and_record_tilt(entity_id, tilt_target)

        if position_settled:
            self._rebase_commanded_position(entity_id, position_target)
        else:
            self._record_event(
                "rebase_skipped",
                reason=_REBASE_SKIP_SETTLE_FAILED,
                entity_id=entity_id,
                position_target=position_target,
                trigger=reason,
            )

    async def update_tilt_only(
        self,
        entity_id: str,
        *,
        tilt_target: int,
        current_position: int | None,
        reason: str,
    ) -> None:
        """Emit a tilt command without a position settle wait or suppression stamp.

        Used by VenetianPolicy when the position axis won't fire this cycle
        (cover is already at the commanded position) so tilt can still track
        the sun continuously.
        """
        if tilt_target == self._tilt_targets.get(entity_id):
            self._record_event(
                "tilt_command_skipped",
                reason=_TILT_SKIP_TARGET_UNCHANGED,
                entity_id=entity_id,
                tilt_position=tilt_target,
                current_position=current_position,
                trigger=reason,
            )
            return
        await self._send_tilt_command(
            entity_id,
            tilt_target=tilt_target,
            position_target=current_position if current_position is not None else 0,
            reason=reason,
        )

    def _rebase_commanded_position(self, entity_id: str, position_target: int) -> None:
        """Reset the cmd_svc target to the actual post-tilt position.

        After set_cover_tilt_position returns, the motor has finished its
        mechanical back-drive of the vertical axis. Reading current_position now
        and pushing that value into set_commanded_position() makes the next
        reconciliation pass compute zero delta — closing the loop where
        reconciliation re-issued set_cover_position, which re-fired the
        sequencer, which back-drove the cover again.
        """
        actual = self._get_current_position(entity_id)
        if actual is None:
            return
        drift = abs(actual - position_target)
        if drift <= self._position_tolerance:
            return
        if drift > VENETIAN_REBASE_MAX_DRIFT_PERCENT:
            self._logger.warning(
                "Venetian rebase refused for %s: drift %s%% exceeds max %s%% "
                "(commanded %s%%, actual %s%%)",
                entity_id,
                drift,
                VENETIAN_REBASE_MAX_DRIFT_PERCENT,
                position_target,
                actual,
            )
            self._record_event(
                "rebase_refused_drift_too_large",
                entity_id=entity_id,
                position_target=position_target,
                actual_position=actual,
                drift=drift,
                max_drift=VENETIAN_REBASE_MAX_DRIFT_PERCENT,
            )
            return
        self._logger.debug(
            "Venetian post-tilt rebase: %s commanded %s%% → actual %s%% "
            "(absorbing motor back-drive)",
            entity_id,
            position_target,
            actual,
        )
        self._set_commanded_position(entity_id, actual)

    async def _wait_for_position_settle(
        self, entity_id: str, target: int
    ) -> tuple[bool, int | None]:
        """Poll ``current_position`` until settle, no-progress, or timeout.

        When a ``get_state`` callable is provided, the no-progress stall counter
        is reset while ``cover.state`` reports ``opening`` or ``closing``.  This
        prevents a Shelly 2PM (or similar hardware) that publishes position at
        ~1 s intervals from triggering a false stall while the motor is still
        mid-travel.
        """
        deadline = dt.datetime.now(dt.UTC) + dt.timedelta(
            seconds=VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS
        )
        last_position: int | None = None
        unchanged_samples = 0

        while dt.datetime.now(dt.UTC) < deadline:
            current = self._get_current_position(entity_id)
            if current is None:
                return False, last_position

            # Read state once per iteration so both the in-tolerance gate and
            # the no-progress stall counter use the same snapshot.
            state = self._get_state(entity_id) if self._get_state else None
            is_moving = state in _COVER_MOVING_STATES

            if abs(current - target) <= self._position_tolerance:
                # When a get_state callback is provided, also require that
                # the cover has actually stopped before declaring settle —
                # some actuators briefly transit through the target position
                # while still in a "closing"/"opening" state.
                if self._get_state is None or not is_moving:
                    return True, current

            if last_position is not None and current == last_position:
                if is_moving:
                    # Motor is still traveling — don't count this as a stall sample.
                    unchanged_samples = 0
                else:
                    unchanged_samples += 1
                    if unchanged_samples >= VENETIAN_POSITION_SETTLE_NO_CHANGE_SAMPLES:
                        self._logger.debug(
                            "Venetian settle: %s stalled at %s%% (target %s%%) "
                            "after %d unchanged samples",
                            entity_id,
                            current,
                            target,
                            unchanged_samples,
                        )
                        return False, current
            else:
                unchanged_samples = 0

            last_position = current
            await asyncio.sleep(VENETIAN_POSITION_SETTLE_POLL_SECONDS)

        self._logger.debug(
            "Venetian settle: %s timed out at %s%% (target %s%%) after %.0fs",
            entity_id,
            last_position,
            target,
            VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS,
        )
        return False, last_position

    # -- diagnostics helpers ----------------------------------------------- #

    def _record_event(self, event_name: str, **fields) -> None:
        """Append a tilt diagnostic event to the shared event buffer."""
        if self._event_buffer is None:
            return
        self._event_buffer.record(
            {"ts": dt.datetime.now(dt.UTC).isoformat(), "event": event_name, **fields}
        )

    async def _verify_and_record_tilt(self, entity_id: str, tilt_target: int) -> None:
        """Poll actual tilt up to N samples; accept on the first in-tolerance read.

        Attempt 0 reads immediately (the caller has already slept
        ``VENETIAN_POST_TILT_REBASE_DELAY_SECONDS``); attempts 1..N-1 sleep
        ``VENETIAN_TILT_VERIFY_POLL_SECONDS`` before reading. Only when every
        sample is out of tolerance do we emit ``tilt_command_drift`` and
        clear the recorded target. Real-actuator publish lag (KNX, Shelly)
        can land the slats correctly but report the pre-update value for
        1–3 s afterwards — a single-shot read misreads that lag as drift
        and triggers a phantom retry next cycle (issue #33).
        """
        if self._get_current_tilt_position is None:
            return
        actual: int | None = None
        delta: int | None = None
        for attempt in range(VENETIAN_TILT_VERIFY_MAX_SAMPLES):
            if attempt > 0:
                await asyncio.sleep(VENETIAN_TILT_VERIFY_POLL_SECONDS)
            actual_wire = self._get_current_tilt_position(entity_id)
            if actual_wire is None:
                return
            actual = self._to_wire(actual_wire)
            delta = abs(actual - tilt_target)
            if delta <= VENETIAN_TILT_VERIFY_TOLERANCE:
                # Stored target now matches the actuator — mark verified so
                # the dedup gate in _send_tilt_command can safely skip a
                # subsequent send for the same target (issue #33).
                self._tilt_targets_verified.add(entity_id)
                self._record_event(
                    "tilt_command_verified",
                    entity_id=entity_id,
                    tilt_target=tilt_target,
                    actual_tilt_position=actual,
                    delta=delta,
                    tolerance=VENETIAN_TILT_VERIFY_TOLERANCE,
                )
                return
        self._logger.warning(
            "Venetian tilt drift detected for %s after %d samples: "
            "sent %s%% but actual is %s%% (delta=%s%% > tolerance=%s%%) "
            "— clearing recorded target for retry",
            entity_id,
            VENETIAN_TILT_VERIFY_MAX_SAMPLES,
            tilt_target,
            actual,
            delta,
            VENETIAN_TILT_VERIFY_TOLERANCE,
        )
        self._record_event(
            "tilt_command_drift",
            entity_id=entity_id,
            tilt_target=tilt_target,
            actual_tilt_position=actual,
            delta=delta,
            tolerance=VENETIAN_TILT_VERIFY_TOLERANCE,
        )
        self._tilt_targets.pop(entity_id, None)
        self._tilt_targets_verified.discard(entity_id)
