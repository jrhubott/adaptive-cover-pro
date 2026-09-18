"""Cloud-suppression smoothing state (issue #864).

The cloud-suppression *decision* (activate / deactivate) is cross-cycle state:
a Schmitt latch per numeric trigger (hysteresis) plus an aggregate hold-timer
(debounce). Per CODING_GUIDELINES "Managers Hold State, Policies Hold Behavior"
that state cannot live in the pure ``CloudSuppressionHandler`` or the frozen
``ClimateReadings``. It lives here, modelled on ``WeatherManager``: the manager
resolves a single boolean and the coordinator threads it into the snapshot; the
handler gates on that boolean and keeps only position selection + the FOV /
time-window guards.

The manager is cover-type-agnostic — it consumes provider booleans and never
reads HA or branches on cover type. Absent config (hold-time 0, blank release
thresholds) reproduces today's instantaneous single-crossing behaviour exactly.

The Schmitt latch and the hold-time debounce are the shared
``managers/common/smoothing`` primitives (issue #917) — the same code
``ClimateSmoothingManager`` delegates to.

It also owns the *escalation clock* (issue #175): how long the resolved bool
has been continuously True, and therefore whether the handler should still
answer with the cloudy position or give up and open the cover fully. Same
division of labour as the decision itself — the manager holds the cross-cycle
state and publishes a phase, the pure handler reads a snapshot field and is the
only thing that ever commands a position.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..const import DEFAULT_CLOUD_SUPPRESSION_HOLD_TIME, CloudSuppressionPhase
from .common import (
    EventRecorder,
    HoldDebouncer,
    advance_schmitt_latch,
    expiry_for_started_at,
    started_at_for_expiry,
)

if TYPE_CHECKING:
    from ..state.climate_provider import ClimateReadings


class CloudSuppressionManager:
    """Hold hysteresis latches + a hold-timer for cloud suppression.

    Each numeric trigger (lux, irradiance, cloud coverage) is a Schmitt latch:
    it engages when the activate edge is met and drops only when the value
    clears the release edge — in between it holds its prior state. ``is_sunny``
    is boolean, so it contributes directly with no hysteresis.

    The instantaneous condition is the OR of ``not is_sunny`` and the three
    latches. A hold-time debounce wraps that aggregate: a change must persist for
    the configured hold-time before the resolved bool flips. Reverting before
    expiry cancels the pending transition (true debounce). ``hold_time == 0``
    flips immediately.

    On top of that decision sits the escalation clock (issue #175). The engage
    edge records ``_suppression_started_at``; the deadline is DERIVED from it on
    every read rather than stored beside it, so editing the delay mid-hold takes
    effect at once with no recompute path to write and nothing to keep in sync.
    The disengage edge and :meth:`_reset` clear it, which is what makes a brief
    sunny spell restart the clock — deliberate, and the reason the option's own
    description points at the smoothing hold-time as the debounce for riding out
    short gaps rather than growing a third timer here.
    """

    def __init__(self, logger, *, event_buffer=None) -> None:
        """Initialize the manager.

        Args:
            logger: Logger for debug/info output.
            event_buffer: Shared diagnostic ring buffer (optional).

        """
        self._logger = logger
        self._events = EventRecorder(event_buffer)

        # Config (updated via update_config).
        self._enabled: bool = False
        self._hold_time: int = DEFAULT_CLOUD_SUPPRESSION_HOLD_TIME
        # Escalation delay in seconds, or None for "hold as long as the cloud
        # lasts" (issue #175). None in BOTH this placeholder and the
        # ``update_config`` parameter: there is no ``DEFAULT_CLOUD_ESCALATION_-
        # DELAY`` for the two to drift apart from, because absent-means-off is
        # what lets the option ship without a config-entry migration.
        self._escalation_delay: int | None = None
        # When the current continuous suppression run began (UTC), or None when
        # nothing is holding. The clock's ONLY stored state — see the class
        # docstring for why the deadline is derived rather than kept here.
        self._suppression_started_at: dt.datetime | None = None
        # A deadline handed back by the restore path that could not be inverted
        # yet because the delay had not been read from options at that point.
        # See :meth:`_adopt_restored_deadline`.
        self._restored_deadline: dt.datetime | None = None

        # Per-trigger Schmitt latches (hysteresis memory).
        self._lux_latched: bool = False
        self._irr_latched: bool = False
        self._cloud_latched: bool = False

        # Aggregate resolved bool + hold-time debounce (shared primitive).
        self._debouncer = HoldDebouncer(
            logger, label="cloud-suppression hold", on_commit=self._on_commit
        )
        # seeded=False: the first reading after construction/reload is the world
        # as found, not a change, so it must not be debounced (issue #1238).
        # Without it a restart in cloudy weather would leave the resolved bool
        # False — and the climate LOW_LIGHT branch, which now gates on it, would
        # sun-track for the whole hold-time before the cover settled.
        self._debouncer.reset(False, seeded=False)

    # --- Configuration ---

    def update_config(
        self,
        *,
        enabled: bool,
        hold_time_seconds: int = DEFAULT_CLOUD_SUPPRESSION_HOLD_TIME,
        escalation_delay_seconds: int | None = None,
    ) -> None:
        """Update configuration from the coordinator.

        ``hold_time_seconds`` defaults to ``DEFAULT_CLOUD_SUPPRESSION_HOLD_TIME``
        (0 = instantaneous) so a caller predating the smoothing feature gets the
        historical single-crossing behaviour. ``escalation_delay_seconds``
        defaults to ``None`` for the same reason at the #175 boundary: no
        escalation at all, which is what every install had before it existed.

        Assigning the delay is the whole update — the deadline follows from it
        on the next read, so a mid-hold change lands immediately and an
        already-running hold keeps its honest start instant.
        """
        self._enabled = enabled
        self._hold_time = hold_time_seconds
        self._escalation_delay = escalation_delay_seconds
        if not enabled:
            self._reset()
            return
        self._adopt_restored_deadline()

    # --- Properties ---

    @property
    def is_suppression_active(self) -> bool:
        """Return the resolved suppression bool (False when disabled)."""
        return self._enabled and self._debouncer.resolved

    @property
    def is_timeout_running(self) -> bool:
        """Return True when a hold-time debounce timer is pending."""
        return self._debouncer.is_timeout_running

    # --- Escalation clock (issue #175) ---

    @property
    def escalation_delay_seconds(self) -> int | None:
        """Configured escalation delay in seconds, or None when unset."""
        return self._escalation_delay

    @property
    def suppression_started_at(self) -> dt.datetime | None:
        """When the current continuous suppression run began (UTC), or None."""
        return self._suppression_started_at

    @property
    def escalation_deadline(self) -> dt.datetime | None:
        """Absolute UTC instant the hold escalates at, or None.

        Derived, never stored: ``None`` whenever nothing is holding or no delay
        is configured, so a caller can treat "no deadline" and "no escalation
        possible" as one question.
        """
        started = self._suppression_started_at
        delay = self._escalation_delay
        if started is None or delay is None:
            return None
        return expiry_for_started_at(started, dt.timedelta(seconds=delay))

    @property
    def phase(self) -> CloudSuppressionPhase:
        """How far along the current hold is (issue #175).

        Polled rather than event-driven, mirroring
        ``AdaptiveCoverManager.reset_if_needed``: ``sun.sun`` is tracked
        unconditionally so there is already a heartbeat, and the coordinator
        additionally arms one wake at the exact deadline for precision.
        """
        if not self.is_suppression_active:
            return CloudSuppressionPhase.IDLE
        deadline = self.escalation_deadline
        if deadline is not None and dt.datetime.now(dt.UTC) >= deadline:
            return CloudSuppressionPhase.ESCALATED
        return CloudSuppressionPhase.HOLDING

    @property
    def is_escalation_active(self) -> bool:
        """Return True once the hold has run past its escalation deadline."""
        return self.phase is CloudSuppressionPhase.ESCALATED

    def seconds_until_escalation(self) -> float | None:
        """Seconds left before the hold escalates, or None when there is nothing to wait for.

        ``None`` covers all three "no wake needed" cases at once — nothing is
        holding, no delay is configured, or the deadline has already passed —
        which is exactly the contract ``coordinator._schedule_optional_wake``
        takes for "no wake this cycle".
        """
        deadline = self.escalation_deadline
        if deadline is None or not self.is_suppression_active:
            return None
        remaining = (deadline - dt.datetime.now(dt.UTC)).total_seconds()
        return remaining if remaining > 0 else None

    def restore_escalation_deadline(self, deadline: dt.datetime) -> None:
        """Rehydrate a persisted escalation deadline after a restart (issue #175).

        Called from the ``cloud_escalation_end_time`` sensor's ``RestoreEntity``
        hook. Cloud suppression deliberately re-engages instantly on reload
        (#1238's ``seeded=False``), so without this a daily-restarting install
        would be handed a fresh full hold every day and the escalation would
        never fire.

        The caller is responsible for rejecting an unusable value (unparseable,
        naive, already past); everything that arrives here is a trusted absolute
        instant in the future.
        """
        self._restored_deadline = deadline
        self._adopt_restored_deadline()

    # --- Evaluation ---

    def evaluate(self, readings: ClimateReadings | None) -> str | None:
        """Fold this cycle's readings into the latches + hold-time debounce.

        Returns ``"should_start_timeout"`` when a transition is pending, the
        hold-time is non-zero, and no timer is already counting toward it — the
        coordinator owns timer creation because it holds the refresh callback.
        Returns ``None`` otherwise. When ``hold_time == 0`` the resolved bool is
        committed in-line and ``None`` is returned.
        """
        if not self._enabled or readings is None:
            self._reset()
            return None

        self._update_latches(readings)
        instantaneous = (
            (not readings.is_sunny)
            or self._lux_latched
            or self._irr_latched
            or self._cloud_latched
        )
        return self._debouncer.evaluate(instantaneous, self._hold_time)

    def _update_latches(self, readings: ClimateReadings) -> None:
        """Advance each per-trigger Schmitt latch for this cycle."""
        self._lux_latched = advance_schmitt_latch(
            self._lux_latched,
            readings.lux_below_threshold,
            readings.lux_release_cleared,
        )
        self._irr_latched = advance_schmitt_latch(
            self._irr_latched,
            readings.irradiance_below_threshold,
            readings.irradiance_release_cleared,
        )
        self._cloud_latched = advance_schmitt_latch(
            self._cloud_latched,
            readings.cloud_coverage_above_threshold,
            readings.cloud_coverage_release_cleared,
        )

    # --- Hold-time debounce timer ---

    def start_hold_timeout(self, refresh_callback: Callable) -> None:
        """Start the hold-time debounce timer.

        Called by the coordinator when :meth:`evaluate` signals a pending
        transition. When the timer expires (and the transition has not been
        reverted), the resolved bool commits and ``refresh_callback`` runs.
        """
        self._logger.info(
            "Cloud-suppression change pending — holding %s seconds before it takes "
            "effect",
            self._hold_time,
        )
        self._debouncer.start_hold_timeout(self._hold_time, refresh_callback)

    async def _on_hold_timeout_expired(self, refresh_callback: Callable) -> None:
        """Commit the pending transition once the hold-time has elapsed."""
        await self._debouncer._on_hold_timeout_expired(refresh_callback)

    def cancel_hold_timeout(self) -> None:
        """Cancel the running hold-time timer, if any."""
        self._debouncer.cancel()

    # --- Internal helpers ---

    def _on_commit(self, previous: bool, target: bool) -> None:
        """Record the resolved-bool transition and move the escalation clock."""
        self._events.record(
            "cloud_suppression_changed",
            entity_id="",
            previous=previous,
            current=target,
        )
        if not target:
            # Disengage edge: the run is over, so the next cloud starts a fresh
            # clock. A restored deadline that outlived its own hold goes with it.
            self._suppression_started_at = None
            self._restored_deadline = None
            return
        if self._suppression_started_at is None:
            # Engage edge. Guarded on None rather than assigned unconditionally
            # so a deadline already restored from the previous run wins over
            # "now" — restore-then-arm short-circuits here, arm-then-restore is
            # overwritten by :meth:`_adopt_restored_deadline`, and both orders
            # converge on the same (earlier, truer) instant.
            self._suppression_started_at = dt.datetime.now(dt.UTC)

    def _adopt_restored_deadline(self) -> None:
        """Invert a pending restored deadline into a start instant (issue #175).

        The one definition of that inversion, called from the two points where
        the (deadline, delay) pair can become complete. It needs both, and they
        arrive in either order: the sensor's restore hook runs during platform
        forwarding — before the first update cycle, so before ``update_config``
        has read the delay out of options — while a reload with the delay
        already known completes the pair the other way round. Whichever lands
        second finishes the job; neither alone does anything.
        """
        deadline = self._restored_deadline
        delay = self._escalation_delay
        if deadline is None or delay is None:
            return
        self._restored_deadline = None
        self._suppression_started_at = started_at_for_expiry(
            deadline, dt.timedelta(seconds=delay)
        )

    def _reset(self) -> None:
        """Drop all latch/resolved/pending state and cancel any timer.

        Runs on every cycle with nothing to fold in — not just a config reload
        that disables the feature, but also a **transient readings outage**
        (``readings is None`` in :meth:`evaluate`).

        ``seeded=False`` is deliberate on that outage path too. The reset itself
        is what ends an interrupted hold: it cancels the timer, drops the
        pending target, and forces ``resolved`` back to False. Re-seeding only
        decides what the *next* valid reading costs. Seeded, that reading would
        re-arm a fresh full hold — a one-cycle blip would buy another
        ``hold_time`` of the wrong state, which is the restart bug issue #1238
        fixed. Unseeded, it is treated as the world as found and commits
        in-line; the seed is one-shot, so the transition after it debounces
        normally again.
        """
        self._lux_latched = False
        self._irr_latched = False
        self._cloud_latched = False
        self._debouncer.reset(False, seeded=False)
        # ``reset`` deliberately fires no commit callback, so the escalation
        # clock has to be cleared here rather than riding the disengage edge in
        # :meth:`_on_commit` (issue #175).
        self._suppression_started_at = None
        self._restored_deadline = None
