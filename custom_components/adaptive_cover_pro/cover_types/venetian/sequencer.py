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
import dataclasses
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
    DEFAULT_VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS,
    DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS,
    DEFAULT_VENETIAN_POST_SETTLE_MODE,
    POSITION_CLOSED,
    POSITION_OPEN,
    VENETIAN_BACKROTATE_MAX_DELTA_PERCENT,
    VENETIAN_DRIFT_RETRY_DELAY_SECONDS,
    VENETIAN_POSITION_SETTLE_NO_CHANGE_SAMPLES,
    VENETIAN_POSITION_SETTLE_POLL_SECONDS,
    VENETIAN_POSITION_SETTLE_STARTUP_GRACE_SECONDS,
    VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS,
    VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS,
    VENETIAN_POST_SETTLE_MODE_ENTITY_STATE,
    VENETIAN_POST_TILT_REBASE_DELAY_SECONDS,
    VENETIAN_REBASE_MAX_DRIFT_PERCENT,
    VENETIAN_TILT_RESET_CLOSE,
    VENETIAN_TILT_RESET_OPEN,
    VENETIAN_TILT_SUPPRESSION_SECONDS,
    VENETIAN_TILT_VERIFY_MAX_SAMPLES,
    VENETIAN_TILT_VERIFY_POLL_SECONDS,
    VENETIAN_TILT_VERIFY_TOLERANCE,
)
from ...managers.cover_command.gates import (
    check_position_delta,
    filter_endpoint_specials,
)
from ...managers.cover_command.transit import is_state_in_transit
from ...managers.manual_override import inverse_state

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ...diagnostics.event_buffer import EventBuffer

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

# Special tilt positions that always bypass the min-delta gate (issue #629).
# A tilt command to fully-open (100) or fully-closed (0) is explicit user
# intent and must never be silently swallowed by drift suppression. This
# mirrors the position-axis behaviour in ``managers/cover_command/routing.py``.
_TILT_SPECIAL_POSITIONS: list[int] = [POSITION_CLOSED, POSITION_OPEN]


@dataclasses.dataclass(frozen=True, slots=True)
class _ResetExcursion:
    """A drift-reset endpoint excursion awaiting its late state publish (#927).

    When ``_maybe_drift_reset`` drives the slats to a mechanical endpoint and
    back, a slow actuator (Somfy IO/Overkiz) may publish the endpoint's
    ``current_tilt_position`` several seconds later — after the command grace
    window has closed. ``endpoint`` is the LOGICAL endpoint the reset drove to
    (``POSITION_OPEN``/``POSITION_CLOSED``) — matched against the published wire
    value via :meth:`DualAxisSequencer._to_wire`; ``at`` is the UTC instant the
    endpoint command was sent, bounding the publish-lag window in
    :meth:`DualAxisSequencer.is_reset_excursion_publish`.
    """

    endpoint: int
    at: dt.datetime


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
        get_enforce_delta_at_endpoints: Callable[[], bool] | None = None,
        get_tilt_reset_threshold: Callable[[], int] | None = None,
        get_tilt_reset_direction: Callable[[], str] | None = None,
        post_settle_hold_seconds: float = DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS,
        post_settle_mode: str = DEFAULT_VENETIAN_POST_SETTLE_MODE,
        backrotate_publish_lag_seconds: float = (
            DEFAULT_VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS
        ),
    ) -> None:
        """Bind HA + cmd_svc dependencies; per-entity timestamps start empty.

        ``backrotate_publish_lag_seconds`` (issue #33 Phase 5) is user-
        configurable via ``CONF_VENETIAN_BACKROTATE_PUBLISH_LAG`` and feeds
        :meth:`is_in_suppression_with_cap`'s post-settle publish-lag tier.
        Bigger values absorb longer republish lags (slow KNX bus, Somfy IO
        via Tahoma); smaller values tighten false-touch detection on fast
        actuators.

        ``post_settle_mode`` (issue #801) selects how ``run_sequence`` waits
        between position settle and the tilt command: the default
        ``fixed_delay`` always sleeps ``post_settle_hold_seconds``;
        ``entity_state`` instead polls :meth:`_wait_for_stationary`, which
        proceeds the moment ``cover.state`` is no longer opening/closing and
        falls back to the fixed sleep on timeout or when state is
        unavailable.
        """
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
        self._get_enforce_delta_at_endpoints = get_enforce_delta_at_endpoints
        # Live accessor for the accumulated commanded tilt-% drift-reset
        # threshold (issue #663). 0 disables. A live lambda (like
        # ``get_min_change``) so an options change applies without a reload.
        self._get_tilt_reset_threshold = get_tilt_reset_threshold or (lambda: 0)
        # Live accessor for the drift-reset direction (issue #686). Resolves the
        # mechanical endpoint the reset drives to before re-sending the target —
        # ``VENETIAN_TILT_RESET_OPEN`` (default, back-compat) or ``_CLOSE``. A
        # live lambda so an options change applies without a reload.
        self._get_tilt_reset_direction = get_tilt_reset_direction or (
            lambda: VENETIAN_TILT_RESET_OPEN
        )
        self._post_settle_hold_seconds = post_settle_hold_seconds
        self._post_settle_mode = post_settle_mode
        self._backrotate_publish_lag_seconds = backrotate_publish_lag_seconds
        # Per-entity timestamps. Keep these on the sequencer (rather than on
        # CoverCommandService.PerEntityState) so non-venetian covers carry no
        # dual-axis state at all.
        self._suppression_at: dict[str, dt.datetime] = {}
        # Per-entity ``moving → settled`` transition timestamp. Anchors the
        # publish-lag window (issue #33 Track A) to the actual settle event
        # observed by the sequencer, not to ``stamp_position_command``.
        # Written by ``run_sequence`` after ``_wait_for_position_settle``
        # returns, and lazy-written from ``is_in_suppression_with_cap`` when
        # the cap query observes a settled state with no stamp yet.
        self._settled_at: dict[str, dt.datetime] = {}
        self._tilt_targets: dict[str, int] = {}
        # Entities whose last stored target has been verified against the
        # actuator. Dedup at _send_tilt_command only fires when the target
        # matches AND the entity is in this set — a verify=False fire-and-
        # forget send stores the target but does not mark verified, so the
        # subsequent verifying send still runs (issue #33).
        self._tilt_targets_verified: set[str] = set()
        self._tilt_sent_at: dict[str, dt.datetime] = {}
        # Per-entity accumulated commanded tilt-% change (issue #663). Each real
        # (non-deduped, non-dry-run, non-gated) tilt send adds
        # ``abs(new_target - prior_anchor)``; crossing
        # ``_get_tilt_reset_threshold()`` triggers a two-step drift reset.
        self._accumulated_tilt: dict[str, float] = {}
        # Entities currently inside a drift-reset's two-step send. A dedicated
        # guard (not reason-string sniffing): the reset's own open + return
        # sends must neither re-accumulate nor re-trigger another reset.
        self._reset_in_progress: set[str] = set()
        # Tilt-only updates deferred because the back-rotate suppression window
        # was still open (issue #756). Keyed by entity_id; cleared the moment a
        # tilt actually sends via ``update_tilt_only``. Drives
        # ``has_pending_tilt`` so the coordinator keeps re-attempting dispatch.
        self._pending_tilt: dict[str, dict] = {}
        # Per-entity list of drift-reset endpoint excursions awaiting their
        # late state publish (issue #927). A reset drives the slats to a
        # mechanical endpoint and back; on slow actuators the endpoint's stale
        # ``current_tilt_position`` publishes after the command grace closes.
        # ``is_reset_excursion_publish`` consumes one one-shot record per
        # matching publish (value-matched, time-boxed) so it isn't misread as a
        # manual move. A list (not a single slot) so two resets firing inside
        # the same window each keep their own record instead of clobbering.
        self._reset_excursion: dict[str, list[_ResetExcursion]] = {}

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

    def _target_already_satisfied(self, entity_id: str, tilt_target: int) -> bool:
        """Return whether the tilt dedup may safely skip a re-send.

        The stored-target dedup alone is wrong for mechanically coupled
        venetians (issue #679): a ``set_cover_position`` command back-drives
        the tilt actuator, so the stored target (last verified send) no longer
        reflects reality. Before treating a target as "unchanged", confirm the
        *live* actuator tilt is still within ``VENETIAN_TILT_VERIFY_TOLERANCE``
        of the stored target.

        Returns ``True`` only when the stored target equals ``tilt_target``
        AND (the live tilt could not be read OR it still matches within
        tolerance). Returns ``False`` when the stored target differs, or when
        the live actuator reading has drifted out of tolerance — forcing a
        re-send so the coupled cover recovers its slat angle. Reuses
        :meth:`_resolve_tilt_anchor` for the live read (the same actual-aware
        path the min-delta gate uses) — no second live-read path.
        """
        stored = self._tilt_targets.get(entity_id)
        if stored is None or stored != tilt_target:
            return False
        anchor, source = self._resolve_tilt_anchor(entity_id)
        if (
            source == _ANCHOR_SOURCE_ACTUAL
            and anchor is not None
            and abs(anchor - stored) > VENETIAN_TILT_VERIFY_TOLERANCE
        ):
            return False
        return True

    # -- suppression window ------------------------------------------------ #

    def stamp_position_command(self, entity_id: str) -> None:
        """Record that a ``set_cover_position`` was just emitted.

        Also clears any prior ``moving → settled`` stamp for this entity so
        the publish-lag window starts fresh for the new cycle. Without this
        reset an old settle stamp from the previous cycle would leak its
        publish-lag tail into a new command, letting a user touch on the
        next cycle get swallowed as motor drift.
        """
        self._suppression_at[entity_id] = dt.datetime.now(dt.UTC)
        self._settled_at.pop(entity_id, None)

    def _stamp_settled(self, entity_id: str) -> None:
        """Record that the sequencer observed ``moving → settled`` for this entity.

        Single dict-access site shared by the deterministic write in
        ``run_sequence`` (after ``_wait_for_position_settle`` returns) and
        the opportunistic lazy-write in ``is_in_suppression_with_cap``. Keep
        callers from poking ``_settled_at`` directly so the publish-lag
        anchor stays a single source of truth.
        """
        self._settled_at[entity_id] = dt.datetime.now(dt.UTC)

    def is_in_suppression(self, entity_id: str) -> bool:
        """Return whether the back-rotate window is still open for this cover."""
        ts = self._suppression_at.get(entity_id)
        if ts is None:
            return False
        return self._seconds_since(ts) < VENETIAN_TILT_SUPPRESSION_SECONDS

    def is_carriage_moving(self, entity_id: str) -> bool:
        """Return whether the carriage is physically mid-travel.

        Reads ``cover.state`` via the injected ``get_state`` callback and
        reports True only while the motor is actively ``opening``/``closing``.
        Single source of truth for the "carriage still settling" check, shared
        by ``is_in_suppression_with_cap`` tier (a) and the forced-transition
        tilt bypass (issue #770).
        """
        if self._get_state is None:
            return False
        return is_state_in_transit(self._get_state(entity_id))

    def suppression_remaining_seconds(self, entity_id: str) -> float | None:
        """Seconds until the back-rotate suppression window closes (issue #756).

        Returns ``None`` when no position command has been stamped for this
        entity (no window open), otherwise the remaining seconds clamped to
        ``>= 0`` so a just-expired window schedules an immediate wake.
        """
        ts = self._suppression_at.get(entity_id)
        if ts is None:
            return None
        remaining = VENETIAN_TILT_SUPPRESSION_SECONDS - self._seconds_since(ts)
        return remaining if remaining > 0 else 0.0

    # -- deferred tilt (issue #756) --------------------------------------- #

    def record_pending_tilt(
        self,
        entity_id: str,
        *,
        tilt_target: int | None,
        current_position: int | None,
        reason: str,
    ) -> None:
        """Queue a tilt-only update that was deferred by the suppression window.

        Issue #756: when the override resolves with the carriage already at
        target but the slat tilt differs, ``maybe_update_tilt_only`` cannot
        send while the prior sequence's back-rotate window is open. Recording
        the intent lets ``has_pending_tilt`` keep the coordinator re-attempting
        dispatch until the window closes.
        """
        self._pending_tilt[entity_id] = {
            "tilt_target": tilt_target,
            "current_position": current_position,
            "reason": reason,
        }

    def has_pending_tilt(self, entity_id: str) -> bool:
        """Return whether a deferred tilt-only update is queued (issue #756)."""
        return entity_id in self._pending_tilt

    def is_in_suppression_with_cap(self, entity_id: str, delta: float) -> bool:
        """Suppress back-rotate drift only when the delta is plausibly motor drift.

        Three-tier suppression on the tilt axis after a position command:

        (a) **Carriage still mid-travel** (``cover.state`` in
            ``opening``/``closing``): any delta is motor drift while the
            motor runs. The cap is fully bypassed.

        (b) **Post-stamp command-grace tail**
            (``VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS``, 5 s): for a brief
            tail after ``stamp_position_command``, even a large delta
            (>cap) is still motor drift. Catches fast actuators (KNX,
            Shelly 2PM) whose back-rotate burst lands microseconds after
            ``cover.state`` reports settled.

        (c) **Post-settle publish-lag window**
            (``VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS``, 45 s): anchored
            to the ``moving → settled`` transition the sequencer observed
            in ``_wait_for_position_settle`` (or lazy-stamped here on the
            first non-moving query). Catches slow-bus republish on Somfy
            IO via Tahoma and KNX/Z2M where the back-rotate tilt value
            lands tens of seconds after the cover physically stops.

        Outside all three tiers the legacy slat-geometry cap applies: a
        delta above ``VENETIAN_BACKROTATE_MAX_DELTA_PERCENT`` is a user
        move and the manual-override path runs.
        """
        if not self.is_in_suppression(entity_id):
            return False
        if self.is_carriage_moving(entity_id):
            return True
        # (b) Command-grace tail anchored to stamp_position_command.
        stamp = self._suppression_at.get(entity_id)
        if stamp is not None and (
            self._seconds_since(stamp) < VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS
        ):
            return True
        # (c) Publish-lag window anchored to moving → settled.
        # Lazy-write: if the cap query observes a non-moving state and a
        # live suppression stamp but no settle stamp yet, treat this query
        # itself as the first non-moving observation and stamp now.
        if entity_id not in self._settled_at and stamp is not None:
            self._stamp_settled(entity_id)
        settled_at = self._settled_at.get(entity_id)
        if settled_at is not None and (
            self._seconds_since(settled_at) < self._backrotate_publish_lag_seconds
        ):
            return True
        return delta <= VENETIAN_BACKROTATE_MAX_DELTA_PERCENT

    def is_reset_excursion_publish(self, entity_id: str, new_value: float) -> bool:
        """Suppress the late state publish of a drift-reset endpoint excursion (#927).

        A drift reset (``_maybe_drift_reset``) drives the slats to a mechanical
        endpoint (``tilt→0`` for ``direction=close``) and restores the target
        ~1.5 s later. On a slow Somfy IO/Overkiz actuator the endpoint's
        ``current_tilt_position`` publishes ~6-7 s after it was sent — after
        the 5 s command grace has closed — and the tilt-only path never opened
        the back-rotate suppression window, so nothing else guards it.
        ``SecondaryAxisCheck.evaluate`` then reads it as a large-delta manual
        move and fires a false ``manual_override_set``.

        Matches on the PUBLISHED WIRE VALUE, not on a reconstructed delta: the
        stale publish's ``new_value`` is the wire endpoint reading, and a match
        holds when it lands within ``VENETIAN_TILT_VERIFY_TOLERANCE`` of the
        recorded LOGICAL endpoint mapped through :meth:`_to_wire` (so inversion
        is handled once). Value-matching — rather than the old
        ``abs(delta - expected_delta)`` reconstruction — means a genuine user
        tilt move to the *mirror* value ``2·target − endpoint`` (which produces
        the same delta) is NOT swallowed, and it never reads ``_tilt_targets``,
        so a diverged stored target (e.g. tilt-skip-above open mode) can't make
        the guard silently inert.

        One record per matching publish is consumed (first match popped, order
        preserved); non-matching intermediate events leave every record intact
        so the real endpoint publish still matches. Expired records (older than
        the configured ``backrotate_publish_lag_seconds`` window, default 45 s)
        are dropped first, so a genuine user move to the same value seconds
        later — once the window has lapsed — still trips.
        """
        records = self._reset_excursion.get(entity_id)
        if not records:
            return False
        # Drop expired records before matching so a stale record can't outlive
        # its publish-lag window and swallow a genuine later move.
        live = [
            record
            for record in records
            if self._seconds_since(record.at) < self._backrotate_publish_lag_seconds
        ]
        matched = False
        remaining: list[_ResetExcursion] = []
        for record in live:
            if not matched and (
                abs(new_value - self._to_wire(record.endpoint))
                <= VENETIAN_TILT_VERIFY_TOLERANCE
            ):
                matched = True  # one-shot: consume only the first matching record
                continue
            remaining.append(record)
        if remaining:
            self._reset_excursion[entity_id] = remaining
        else:
            self._reset_excursion.pop(entity_id, None)
        return matched

    # -- tilt sequence ----------------------------------------------------- #

    def last_tilt_target(self, entity_id: str) -> int | None:
        """Return the last tilt target sent (for diagnostics / tests)."""
        return self._tilt_targets.get(entity_id)

    def clear_tilt_targets(self) -> None:
        """Forget every stored tilt target — anchor falls back to live actuator reads.

        Defense-in-depth hook for Auto Control off→on transitions (issue #33).
        Suppression timestamps are intentionally untouched — the back-rotate
        window is a time-based safeguard, independent of the stored-target
        cache. The ``moving → settled`` stamps used by the publish-lag
        window are also cleared so a stale prior-cycle settle can't leak its
        suppression tail into the next command after Auto Control is
        re-enabled.
        """
        self._tilt_targets.clear()
        self._tilt_targets_verified.clear()
        self._settled_at.clear()
        # Drift-reset accumulator + recursion guard (issue #663) are per-entity
        # caches like the above and must reset on Auto Control off→on too.
        self._accumulated_tilt.clear()
        self._reset_in_progress.clear()

    async def run_sequence(
        self,
        entity_id: str,
        *,
        position_target: int,
        tilt_target: int,
        reason: str,
        drift_reset_eligible: bool = True,
    ) -> None:
        """Wait for vertical motion to settle, then send the tilt command.

        ``drift_reset_eligible`` (issue #808) is threaded to
        ``_send_tilt_command``; the owning policy sets it False to keep a
        non-solar tilt from accumulating drift when scope is ``sun_tracking_only``.
        Defaults to True so existing callers preserve today's behaviour.
        """
        settled, _last = await self._wait_for_position_settle(
            entity_id, position_target
        )
        if self._post_settle_mode == VENETIAN_POST_SETTLE_MODE_ENTITY_STATE:
            await self._wait_for_stationary(entity_id)
        else:
            await asyncio.sleep(self._post_settle_hold_seconds)
        # The window protects the position-axis settle + tilt-induced back-drive.
        # Only the position-sequence path owns this stamp; tilt-only sends from
        # update_tilt_only must not extend it (issue #33 follow-on).
        self.stamp_position_command(entity_id)
        # Anchor the publish-lag window to the moving → settled transition
        # we just observed (issue #33 Track A). Must come AFTER
        # stamp_position_command because that call clears ``_settled_at`` for
        # a fresh cycle — we want the publish-lag clock to start now, not
        # leak from a prior cycle. ``stamp_position_command`` pops; this
        # write puts the fresh settle stamp back.
        self._stamp_settled(entity_id)
        # A position move can mechanically back-rotate the slats on real
        # motors (Somfy IO via Tahoma, KNX): the carriage bottoming out at /
        # near POSITION_CLOSED drags the tilt shut regardless of the angle we
        # last commanded. Any prior verification of the stored tilt target is
        # therefore stale the moment the carriage has travelled. Drop the
        # verified flag so the post-settle send below re-reads the actuator
        # and re-asserts tilt on drift, instead of short-circuiting on the
        # target-unchanged + already-verified dedup in ``_send_tilt_command``
        # and leaving the slats wherever the motor parked them.
        #
        # Opening transitions already force tilt through
        # ``before_position_command`` (and that force-send discards the flag);
        # closing transitions had no equivalent, so a close to 0 left the
        # slats shut until some later open happened to clear the flag — an
        # intermittent, state-dependent blackout. This closes that gap on the
        # closing path.
        self._tilt_targets_verified.discard(entity_id)
        await self._send_tilt_command(
            entity_id,
            tilt_target=tilt_target,
            position_target=position_target,
            reason=reason,
            position_settled=settled,
            drift_reset_eligible=drift_reset_eligible,
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
        drift_reset_eligible: bool = True,
        _retry_depth: int = 0,
    ) -> bool:
        """Emit ``set_cover_tilt_position`` and rebase the commanded position.

        Returns ``True`` only when the ``set_cover_tilt_position`` service call
        actually dispatched. Every early-out — target-unchanged dedup,
        min-delta gate, dry-run, and a ``HomeAssistantError`` from the service
        call — returns ``False``. The drift-reset endpoint stamp (issue #927)
        relies on this so it records only after the endpoint move really went
        out; a failed send leaves no stale record to swallow a later user move.

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

        ``_retry_depth`` is an internal recursion guard for the issue #500
        drift-retry path: ``_verify_and_record_tilt`` calls back into this
        method with ``_retry_depth=1`` after a drift event so the gates
        (dedup, dry-run, grace) are reused per the no-duplication rule. The
        depth flag is threaded straight into the verify step so a still-
        drifting retry does not spawn another retry.
        """
        if not force and self._target_already_satisfied(entity_id, tilt_target):
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
                await self._verify_and_record_tilt(
                    entity_id, tilt_target, _retry_depth=_retry_depth
                )
            return False

        if not force and self._get_min_change is not None:
            anchor, anchor_source = self._resolve_tilt_anchor(entity_id)
            # When endpoint-delta enforcement is enabled (issue #679), drop the
            # 0/100 special bypass so the gate applies to the full endpoints too.
            # Delegates to filter_endpoint_specials (gates.py) — the same helper
            # used by build_special_positions on the position axis.
            enforced = bool(
                self._get_enforce_delta_at_endpoints
                and self._get_enforce_delta_at_endpoints()
            )
            tilt_specials = filter_endpoint_specials(_TILT_SPECIAL_POSITIONS, enforced)
            if anchor is not None and not check_position_delta(
                entity_id,
                tilt_target,
                self._get_min_change(),
                tilt_specials,
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
                return False

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
            return False

        # Capture the pre-send anchor BEFORE overwriting _tilt_targets so the
        # drift accumulator (issue #663) measures real commanded travel. Uses
        # the same actual-aware source as the min-delta gate; a cold-start None
        # accumulates 0 (the freshly-stored target seeds the next send). Only
        # resolved when the feature is enabled (threshold > 0) and the send is
        # not part of a reset already — resolving does a live actuator read, so
        # skipping it when disabled keeps the read sequence identical to the
        # pre-#663 behaviour for the dominant disabled case.
        drift_reset_enabled = (
            drift_reset_eligible
            and entity_id not in self._reset_in_progress
            and self._get_tilt_reset_threshold() > 0
        )
        pre_send_anchor: int | None = None
        if drift_reset_enabled:
            pre_send_anchor, _ = self._resolve_tilt_anchor(entity_id)

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
            return False

        self._record_event(
            "tilt_command_sent",
            entity_id=entity_id,
            tilt_position=tilt_target,
            position_target=position_target,
            trigger=reason,
        )

        # Accumulate real commanded travel and, when the threshold is crossed,
        # run a two-step mechanical drift reset (issue #663). Placed after the
        # successful async_call so deduped / dry-run / min-delta-gated sends do
        # not count. ``drift_reset_enabled`` already folds in the recursion
        # guard and the disabled (threshold 0) case, so the reset's own two
        # sends neither re-accumulate nor re-trigger.
        if drift_reset_enabled:
            await self._maybe_drift_reset(
                entity_id,
                original_target=tilt_target,
                position_target=position_target,
                pre_send_anchor=pre_send_anchor,
            )

        if not verify:
            return True

        # Wait for the motor's mechanical back-drive on the vertical axis to
        # settle before reading current_position for the rebase. Without this
        # delay the read races the asynchronous back-drive and captures the
        # pre-settle value, causing the rebase to see zero drift and skip.
        await asyncio.sleep(VENETIAN_POST_TILT_REBASE_DELAY_SECONDS)

        # Verify the tilt actually landed. On slow/racing hardware the motor
        # may back-rotate the slats during position movement, leaving the cover
        # at tilt=0 even though we sent tilt=N. If we detect drift, clear the
        # recorded target so the next update_tilt_only cycle retries.
        await self._verify_and_record_tilt(
            entity_id, tilt_target, _retry_depth=_retry_depth
        )

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
        return True

    async def _maybe_drift_reset(
        self,
        entity_id: str,
        *,
        original_target: int,
        position_target: int,
        pre_send_anchor: int | None,
    ) -> None:
        """Accumulate commanded tilt travel; reset to flush drift on threshold.

        Issue #663. Adds ``abs(original_target - pre_send_anchor)`` to the
        per-entity accumulator (0 when the anchor is unresolved at cold start —
        the freshly-stored target seeds the next send). When a positive
        threshold is crossed, runs a two-step re-zero through
        :meth:`_send_tilt_command` (reusing inverse / grace / dedup gates per
        the no-duplication rule):

        1. Record a per-entity endpoint-excursion stamp (issue #927) so a stale
           endpoint state publish arriving after the command grace closes is
           later recognised by :meth:`is_reset_excursion_publish` as ACP's own
           move rather than a manual override, then drive the slats to the
           mechanical endpoint chosen by ``get_tilt_reset_direction`` (issue
           #686) — logical ``POSITION_OPEN`` (default) or ``POSITION_CLOSED``
           (``force=True``, ``verify=False``). The literal logical value is
           passed so ``_to_wire`` applies inversion exactly once; it is NOT
           clamped to ``CONF_MAX_TILT`` — the hardware endpoint is the correct
           re-zero anchor.
        2. Settle (``VENETIAN_POST_TILT_REBASE_DELAY_SECONDS``).
        3. Re-send the original target (``force=True``, ``verify=True``) so the
           dedup gate doesn't swallow the unchanged value.

        Then zeroes the accumulator. A dedicated ``_reset_in_progress`` guard
        wraps the two sends so they neither re-accumulate nor re-trigger.
        """
        if entity_id in self._reset_in_progress:
            return

        delta = (
            abs(original_target - pre_send_anchor)
            if pre_send_anchor is not None
            else 0.0
        )
        accumulated = self._accumulated_tilt.get(entity_id, 0.0) + delta
        self._accumulated_tilt[entity_id] = accumulated

        threshold = self._get_tilt_reset_threshold()
        if not (threshold > 0 and accumulated >= threshold):
            return

        # Resolve the drive-to endpoint once (issue #686). ``close`` drives the
        # slats fully closed; anything else (default ``open``) drives fully open.
        direction = self._get_tilt_reset_direction()
        reset_endpoint = (
            POSITION_CLOSED if direction == VENETIAN_TILT_RESET_CLOSE else POSITION_OPEN
        )
        self._record_event(
            "tilt_reset_triggered",
            entity_id=entity_id,
            accumulated_tilt=accumulated,
            threshold=threshold,
            target=original_target,
            direction=direction,
        )
        self._reset_in_progress.add(entity_id)
        try:
            # Event NAME stays ``tilt_reset_open`` for Lovelace-card / test
            # stability; its ``tilt_position`` reflects the chosen endpoint.
            self._record_event(
                "tilt_reset_open",
                entity_id=entity_id,
                tilt_position=reset_endpoint,
            )
            sent = await self._send_tilt_command(
                entity_id,
                tilt_target=reset_endpoint,
                position_target=position_target,
                reason="tilt_reset_open",
                force=True,
                verify=False,
            )
            # Record the endpoint excursion so a stale endpoint state publish
            # that lands after the command grace closes is recognised as ACP's
            # own move, not a manual override (issue #927). The LOGICAL endpoint
            # is stored; is_reset_excursion_publish applies _to_wire on match.
            # Only stamp when the endpoint command ACTUALLY dispatched: in
            # dry-run the send is skipped, and a HomeAssistantError leaves the
            # slats put — either way a stamp would linger with no matching
            # publish and could later swallow a genuine move. Append — a second
            # reset inside the window keeps its own record.
            if sent:
                self._reset_excursion.setdefault(entity_id, []).append(
                    _ResetExcursion(endpoint=reset_endpoint, at=dt.datetime.now(dt.UTC))
                )
            await asyncio.sleep(VENETIAN_POST_TILT_REBASE_DELAY_SECONDS)
            self._record_event(
                "tilt_reset_return",
                entity_id=entity_id,
                tilt_position=original_target,
            )
            await self._send_tilt_command(
                entity_id,
                tilt_target=original_target,
                position_target=position_target,
                reason="tilt_reset_return",
                force=True,
                verify=True,
            )
        finally:
            self._reset_in_progress.discard(entity_id)
        self._accumulated_tilt[entity_id] = 0.0

    async def update_tilt_only(
        self,
        entity_id: str,
        *,
        tilt_target: int,
        current_position: int | None,
        reason: str,
        force: bool = False,
        drift_reset_eligible: bool = True,
    ) -> None:
        """Emit a tilt command without a position settle wait or suppression stamp.

        Used by VenetianPolicy when the position axis won't fire this cycle
        (cover is already at the commanded position) so tilt can still track
        the sun continuously.

        ``force=True`` bypasses the target-unchanged dedup here AND threads the
        same flag into ``_send_tilt_command`` so it skips the dedup/min-delta
        gates. Used by the user-tilt path (issue #684): a user explicitly
        re-requesting the current tilt is not a no-op.
        """
        # Any tilt-only send for this entity satisfies (or supersedes) a
        # previously deferred tilt, so clear the pending marker (issue #756).
        self._pending_tilt.pop(entity_id, None)
        if not force and self._target_already_satisfied(entity_id, tilt_target):
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
            force=force,
            drift_reset_eligible=drift_reset_eligible,
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

    async def _wait_for_stationary(self, entity_id: str) -> None:
        """Poll ``cover.state`` until the carriage is no longer opening/closing.

        Backs the ``entity_state`` post-settle mode (issue #801): returns as
        soon as the *first* poll observes a non-transit state — no
        consecutive-poll debounce, by explicit design decision — instead of
        always sleeping the fixed ``post_settle_hold_seconds`` hold. Reuses
        :data:`VENETIAN_POSITION_SETTLE_POLL_SECONDS` as the poll interval and
        ``post_settle_hold_seconds`` as the timeout budget, the same knobs
        ``_wait_for_position_settle`` already exposes to the user.

        Falls back to sleeping the full fixed hold — not the remaining
        budget, so the fallback is exactly the ``fixed_delay`` behaviour —
        when ``get_state`` was never wired up, or when the budget elapses
        with the carriage still in transit (or state unreadable).
        """
        if self._get_state is None:
            await asyncio.sleep(self._post_settle_hold_seconds)
            return
        deadline = dt.datetime.now(dt.UTC) + dt.timedelta(
            seconds=self._post_settle_hold_seconds
        )
        while dt.datetime.now(dt.UTC) < deadline:
            if not is_state_in_transit(self._get_state(entity_id)):
                return
            await asyncio.sleep(VENETIAN_POSITION_SETTLE_POLL_SECONDS)
        self._logger.debug(
            "Venetian post-settle entity_state wait: %s still in transit after "
            "%.1fs budget, falling back to fixed hold",
            entity_id,
            self._post_settle_hold_seconds,
        )
        await asyncio.sleep(self._post_settle_hold_seconds)

    async def _wait_for_position_settle(
        self, entity_id: str, target: int
    ) -> tuple[bool, int | None]:
        """Poll ``current_position`` until settle, no-progress, or timeout.

        When a ``get_state`` callable is provided, the no-progress stall counter
        is reset while ``cover.state`` reports ``opening`` or ``closing``.  This
        prevents a Shelly 2PM (or similar hardware) that publishes position at
        ~1 s intervals from triggering a false stall while the motor is still
        mid-travel.

        Startup grace (``VENETIAN_POSITION_SETTLE_STARTUP_GRACE_SECONDS``):
        some actuators (Somfy IO via Tahoma in issue #33) take 3-5 s to begin
        physical travel after the service call. During that pre-motion window
        ``cover.state`` still reads ``open``/``closed`` and ``current_position``
        is unchanged, which would otherwise trip the 3-sample stall counter
        and declare settle 20-30 s before the cover actually stops moving.
        The startup grace blocks stall declaration until either the cover has
        been observed in a moving state at least once, or the wall-clock
        grace window has elapsed since the loop began.
        """
        loop_started = dt.datetime.now(dt.UTC)
        deadline = loop_started + dt.timedelta(
            seconds=VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS
        )
        last_position: int | None = None
        unchanged_samples = 0
        motion_observed = False

        while dt.datetime.now(dt.UTC) < deadline:
            current = self._get_current_position(entity_id)
            if current is None:
                return False, last_position

            # Read state once per iteration so both the in-tolerance gate and
            # the no-progress stall counter use the same snapshot.
            state = self._get_state(entity_id) if self._get_state else None
            is_moving = is_state_in_transit(state)
            if is_moving:
                motion_observed = True

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
                    startup_grace_elapsed = (
                        self._seconds_since(loop_started)
                        >= VENETIAN_POSITION_SETTLE_STARTUP_GRACE_SECONDS
                    )
                    if (
                        unchanged_samples >= VENETIAN_POSITION_SETTLE_NO_CHANGE_SAMPLES
                        and (motion_observed or startup_grace_elapsed)
                    ):
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

    @staticmethod
    def _seconds_since(stamp: dt.datetime) -> float:
        """Return wall-clock seconds since ``stamp`` (UTC).

        Single source of truth for elapsed-since-timestamp arithmetic.
        Used by both the settle-loop startup grace and the suppression
        cap-grace / publish-lag checks so the formula isn't duplicated
        across the file.
        """
        return (dt.datetime.now(dt.UTC) - stamp).total_seconds()

    # -- diagnostics helpers ----------------------------------------------- #

    def _record_event(self, event_name: str, **fields) -> None:
        """Append a tilt diagnostic event to the shared event buffer."""
        if self._event_buffer is None:
            return
        self._event_buffer.record(
            {"ts": dt.datetime.now(dt.UTC).isoformat(), "event": event_name, **fields}
        )

    async def _verify_and_record_tilt(
        self, entity_id: str, tilt_target: int, *, _retry_depth: int = 0
    ) -> None:
        """Poll actual tilt up to N samples; accept on the first in-tolerance read.

        Attempt 0 reads immediately (the caller has already slept
        ``VENETIAN_POST_TILT_REBASE_DELAY_SECONDS``); attempts 1..N-1 sleep
        ``VENETIAN_TILT_VERIFY_POLL_SECONDS`` before reading. Only when every
        sample is out of tolerance do we emit ``tilt_command_drift`` and
        clear the recorded target. Real-actuator publish lag (KNX, Shelly)
        can land the slats correctly but report the pre-update value for
        1–3 s afterwards — a single-shot read misreads that lag as drift
        and triggers a phantom retry next cycle (issue #33).

        On drift, when ``_retry_depth == 0``, schedules a single bounded
        re-send through ``_send_tilt_command`` after
        ``VENETIAN_DRIFT_RETRY_DELAY_SECONDS`` so all gates (dedup, dry-run,
        grace) are reused per the no-duplication rule (issue #500). The
        retry passes ``_retry_depth=1`` to block further recursion: a still-
        drifting second attempt drops out and the next coordinator cycle
        owns ultimate recovery.
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

        # Issue #500: don't wait for the next coordinator cycle (minutes away
        # with delta_position=5). Re-send once through _send_tilt_command —
        # reuses every gate (dedup, dry-run, grace) per the no-duplication
        # rule. _retry_depth blocks recursion: the retry call passes
        # _retry_depth=1, and this branch only fires when _retry_depth == 0.
        if _retry_depth == 0:
            self._record_event(
                "tilt_command_drift_retry",
                entity_id=entity_id,
                tilt_target=tilt_target,
                actual_tilt_position=actual,
                delta=delta,
                retry_delay_seconds=VENETIAN_DRIFT_RETRY_DELAY_SECONDS,
            )
            await asyncio.sleep(VENETIAN_DRIFT_RETRY_DELAY_SECONDS)
            await self._send_tilt_command(
                entity_id,
                tilt_target=tilt_target,
                position_target=self._get_current_position(entity_id) or 0,
                reason="drift_retry",
                force=True,
                verify=True,
                _retry_depth=1,
            )
