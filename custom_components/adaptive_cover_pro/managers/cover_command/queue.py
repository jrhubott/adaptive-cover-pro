"""Named cover-command dispatch queue (issue #1189).

One-way radio backends (Somfy RTS, RFXtrx) have no acknowledgement: two frames
keyed at the same moment collide and are simply lost. A reporter running 16 RTS
covers as 16 independent ACP config entries hits this every time the sun moves
enough for several facades to re-position at once — nothing in the integration
arbitrates between entries, because each one owns its own coordinator and its
own :class:`CoverCommandService`.

This module is that arbiter. A cover names the queue it belongs to; every cover
naming the same queue transmits one at a time, the queue staying busy for
``gap_seconds`` after each send. Blank name = no queue = bit-identical to the
behaviour before this module existed.

Three properties the mechanism carries, each load-bearing:

**Bounded wait.** Every acquisition takes a budget. On expiry the caller gets a
grant flagged ``budget_expired`` and transmits ANYWAY — a command is never
withheld indefinitely. An unbounded wait would be issue #756 reintroduced
outright: a queue wait is a long-blocking dispatch inside the coordinator's
update cycle, which is precisely the shape #756 was about.

**Head of line, not bypass.** Safety and explicit user commands jump ahead of
routine waiters but still take their spacing. Bypass would be the wrong answer:
a storm retraction firing on 16 instances at once is the MOST collision-prone
moment there is, so safety needs the spacing more than tracking does — it just
must never wait behind tracking.

**Supersede, don't backlog.** At most one pending reservation per entity; a
newer acquisition for the same entity cancels the waiting one, which returns
``None`` so the caller can record a clean skip. This bounds queue depth to the
entity count and guarantees the frame that finally goes out carries the most
recent decision rather than a 20-second-stale target — the same last-writer-wins
doctrine ``coordinator._external_interlock_tasks`` already encodes.

The queue holds no HA references and imports nothing from Home Assistant: it is
cross-instance manager state, per CODING_GUIDELINES' manager/policy split, and
nothing in it is cover-type-specific.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from ...const import COMMAND_QUEUE_REGISTRY_KEY, DEFAULT_COMMAND_QUEUE_GAP

__all__ = [
    "CommandQueue",
    "QueueGrant",
    "get_command_queue",
    "normalize_queue_name",
]


def normalize_queue_name(name: Any) -> str:
    """Fold a queue name to its match key. Empty string means "no queue".

    Free text carries a silent failure class: ``"facade south"`` and
    ``"Facade South"`` are two queues that both look right, and the symptom —
    covers that quietly do not serialize — is indistinguishable from the bug
    this feature exists to fix. So matching folds the two invisible-typo
    classes: case (``casefold``, not ``lower``, so locale-quirky pairs like
    ``STRASSE``/``straße`` collapse too) and whitespace (``"Facade  South"`` is
    the same name typed with an extra space).

    Normalization applies at match and registry time ONLY. Every display
    surface — the dropdown, the config summary, the queue entry's own title —
    keeps the user's original casing.
    """
    if not name:
        return ""
    return " ".join(str(name).split()).casefold()


def _resolution(future: asyncio.Future) -> bool | None:
    """Return the real outcome a waiter's future carries, or ``None`` if it has none.

    ``asyncio.wait_for`` cancels the future it was waiting on when the timeout
    fires, so ``done()`` alone is not enough — the future may be done BECAUSE it
    was cancelled. It may also have been resolved in the very loop iteration the
    timeout landed in, and that resolution is real in BOTH directions: a ``True``
    means the slot is ours, and a ``False`` means a newer reservation for this
    entity replaced us. Collapsing the second into a budget-expired grant would
    put the older decision on the air.
    """
    if not future.done() or future.cancelled():
        return None
    return future.result() is True


@dataclass(frozen=True, slots=True)
class QueueGrant:
    """The outcome of one successful :meth:`CommandQueue.acquire`.

    ``waited_seconds`` is exactly ``0.0`` when the slot was free and unspaced —
    the "immediate" case the diagnostics report — and the real elapsed wait
    otherwise. ``budget_expired`` says the bounded-wait ceiling fired and the
    caller is transmitting outside its turn; it is a diagnostic, never a
    refusal. ``depth_at_enqueue`` is how many reservations were already waiting
    when this one asked.

    ``holds_slot`` is the release token, and it is NOT the inverse of
    ``budget_expired``: a grant that took the slot and then overran its budget
    waiting out the spacing holds it, while a grant that gave up waiting for
    the slot entirely does not. It exists because the entity id cannot answer
    the question — two acquisitions for the SAME entity can be live at once
    (``apply_position`` re-booking an entity a reconciliation resend is already
    transmitting), and an entity-keyed release would then let the one that
    never held the slot free the one that does, mid-transmission.
    """

    waited_seconds: float
    budget_expired: bool
    depth_at_enqueue: int
    entity_id: str = ""
    holds_slot: bool = True


@dataclass(slots=True)
class _Waiter:
    """One pending reservation. At most one per entity, by construction."""

    entity_id: str
    future: asyncio.Future
    head_of_line: bool


@dataclass(slots=True)
class CommandQueue:
    """Serializes cover commands across every member naming this queue.

    Owns a single logical slot plus a monotonic ``busy_until`` deadline. A
    member takes the slot, transmits, and releases; the release starts the gap,
    and the next waiter is granted the slot but does not transmit until the gap
    has elapsed. The gap therefore applies AFTER a send and never before the
    first one.
    """

    name: str
    #: The registry dict this queue was created in, so the last ``detach`` can
    #: drop itself. ``None`` for the standalone queues unit tests construct.
    _registry: dict[str, CommandQueue] | None = None

    _gap_override: float | None = None
    _busy_until: float = 0.0
    _owner: str | None = None
    _attached: int = 0
    _head_waiters: deque[_Waiter] = field(default_factory=deque)
    _routine_waiters: deque[_Waiter] = field(default_factory=deque)
    _pending: dict[str, _Waiter] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Configuration
    # ------------------------------------------------------------------ #

    @property
    def gap_seconds(self) -> float:
        """Seconds the queue stays busy after a member transmits.

        The default applies whenever no Command Queue config entry owns this
        name — naming a queue must never require creating one.
        """
        if self._gap_override is None:
            return DEFAULT_COMMAND_QUEUE_GAP
        return self._gap_override

    @property
    def gap_source(self) -> str:
        """Where ``gap_seconds`` came from: ``"entry"`` or ``"default"``.

        The field that actually answers a support question. A queue named by
        covers but owned by no Command Queue entry reports ``default``, which
        is the difference between "your configured gap is not being applied"
        and "you never created the entry that holds it".
        """
        return "default" if self._gap_override is None else "entry"

    def set_gap(self, gap: float | None) -> None:
        """Bind the owning entry's gap, or ``None`` to revert to the default.

        Read live at release time rather than copied into each member, so
        editing the queue entry takes effect without reloading any member.
        """
        self._gap_override = None if gap is None else float(gap)

    # ------------------------------------------------------------------ #
    # Membership refcount
    # ------------------------------------------------------------------ #

    @property
    def attached(self) -> int:
        """How many config entries currently reference this queue."""
        return self._attached

    def attach(self) -> None:
        """Register one more member (a cover entry, or the queue's own entry)."""
        self._attached += 1

    def detach(self) -> None:
        """Drop one member; the queue leaves the registry when the last one goes.

        Reloading a single member is attach-after-detach on the SAME object as
        long as another member (or the queue entry) still holds a reference —
        which is why the object lives in ``hass.data`` and not on any entry.
        """
        self._attached = max(0, self._attached - 1)
        if self._attached == 0 and self._registry is not None:
            self._registry.pop(self.name, None)

    # ------------------------------------------------------------------ #
    # Observation (diagnostics)
    # ------------------------------------------------------------------ #

    @property
    def depth(self) -> int:
        """Reservations waiting for the slot right now (the holder excluded)."""
        return len(self._head_waiters) + len(self._routine_waiters)

    def busy_for_seconds(self) -> float:
        """Remaining spacing owed before the next member may transmit."""
        return max(0.0, self._busy_until - monotonic())

    def projected_wait_seconds(self, *, head_of_line: bool = False) -> float:
        """Seconds a reservation asking RIGHT NOW would wait before transmitting.

        Zero means the slot is free, unqueued and the air owes nothing — the
        only state in which a command goes out with no delay at all.

        The estimate is the sum of three things a caller cannot see separately:
        the spacing still owed by the air, one further gap if somebody holds the
        slot (a holder has yet to transmit and start its own gap), and one gap
        per reservation ahead of this one in the tiers it cannot jump.

        This exists so a caller can decide whether a wait is AFFORDABLE, which
        is a different question from whether it is zero. Folding the two — "not
        free this instant" therefore "stand down" — is what made a reconciliation
        pass emit at most one frame: the very spacing the pass's own first
        resend started disqualified every remaining entity on the queue.
        Spacing owed is bounded by ``gap_seconds`` and is largely the caller's
        own doing; waiting it out is the entire point of the queue.
        """
        ahead = len(self._head_waiters)
        if not head_of_line:
            ahead += len(self._routine_waiters)
        wait = self.busy_for_seconds()
        if self._owner is not None:
            wait += self.gap_seconds
        return wait + ahead * self.gap_seconds

    # ------------------------------------------------------------------ #
    # The slot
    # ------------------------------------------------------------------ #

    async def acquire(
        self,
        entity_id: str,
        *,
        head_of_line: bool,
        budget: float,
    ) -> QueueGrant | None:
        """Take the slot and wait out any spacing still owed.

        Returns ``None`` when this reservation was superseded by a newer one
        for the same entity while it waited. Every other outcome is a grant the
        caller must transmit on and then :meth:`release`, including the
        budget-expired one.

        ``budget`` is the caller's whole allowance for this one acquisition. A
        caller that cannot afford to suspend for long — the reconciliation
        resend, whose pass IS the retry loop and which HA re-fires on an
        interval it re-arms before dispatching — passes a small one and asks
        :meth:`resend_stands_down` first, so it only ever enters a wait it can
        pay for.
        """
        started = monotonic()
        depth_at_enqueue = self.depth
        budget_expired = False
        awaited = False
        holds_slot = False

        try:
            if self._owner is not None or self.depth:
                waiter = self._enqueue(entity_id, head_of_line=head_of_line)
                awaited = True
                try:
                    granted = await asyncio.wait_for(waiter.future, budget)
                except TimeoutError:
                    # ``wait_for`` cancels the future on timeout, but it may
                    # already have been resolved in the same loop iteration —
                    # honour that, in BOTH directions. A ``False`` resolved in
                    # the expiry iteration is a supersede, and a supersede is
                    # never upgradeable into a grant: transmitting on it would
                    # put a target a newer decision has already replaced on the
                    # air, which is the one thing budget expiry must not cost.
                    resolution = _resolution(waiter.future)
                    granted = resolution is True
                    if not granted:
                        self._drop(waiter)
                        if resolution is False:
                            return None
                        budget_expired = True
                except BaseException:
                    # A genuine outer cancellation (shutdown, a superseding task
                    # being torn down). ``_grant_next`` records the owner and
                    # resolves the future in one step, so the slot may ALREADY
                    # be ours even though this coroutine never got to resume —
                    # claim it here so the unwind below gives it back rather
                    # than stranding it.
                    if _resolution(waiter.future) is True:
                        holds_slot = True
                    self._drop(waiter)
                    raise
                if not budget_expired and not granted:
                    return None  # superseded by a newer reservation for this entity

            if not budget_expired:
                self._owner = entity_id
                holds_slot = True

            # Spacing owed by the air, charged against whatever is left of the
            # budget. Expiring here still yields a grant: the caller transmits
            # anyway, because withholding is the failure mode.
            #
            # RE-READ each time round. ``busy_until`` is not a constant across
            # this sleep — three transmissions advance it from outside the slot
            # and every one of them lands, by construction, while the next
            # member is ALREADY here: ``release`` hands the slot on in the same
            # call that starts the gap, so a waiter is spacing before the
            # venetian tail reaches ``mark_air_busy`` (the tail waits out a
            # physical rail first), before a stop is keyed, and before a
            # gave-up member transmits out of turn. A sleep sized once from a
            # single reading cannot see any of them, which would make the
            # reports inert in exactly the contended case they exist for.
            #
            # Bounded regardless: the loop sleeps the REMAINING interval each
            # pass (never spins) and stops the moment the budget is gone, so no
            # stream of out-of-turn frames can withhold a command indefinitely.
            while True:
                remaining = self._busy_until - monotonic()
                if remaining <= 0:
                    break
                left = budget - (monotonic() - started)
                if left <= 0:
                    budget_expired = True
                    break
                awaited = True
                await asyncio.sleep(min(remaining, left))
        except BaseException:
            # Nothing may leave this method holding the slot except a returned
            # grant. Both awaits above are cancellation points a coordinator
            # really does fire — the #1138 interlock task is cancelled when a
            # second external command supersedes it, and unload cancels every
            # one of them — and a slot stranded that way never comes back on
            # its own: every other member enqueues, burns its whole budget and
            # transmits ``budget_expired``, unspaced and simultaneous.
            if holds_slot:
                self._hand_off()
            raise

        return QueueGrant(
            waited_seconds=(monotonic() - started) if awaited else 0.0,
            budget_expired=budget_expired,
            depth_at_enqueue=depth_at_enqueue,
            entity_id=entity_id,
            holds_slot=holds_slot,
        )

    def release(self, grant: QueueGrant, *, transmitted: bool) -> None:
        """Give the slot back, starting the gap when something was transmitted.

        Tolerates a release from a grant that never held the slot — that is
        exactly what a gave-up-waiting grant is. Such a release still advances
        ``busy_until`` (a frame did go out, so the air is busy) but must not
        hand the real holder's slot to the next waiter.

        Keyed on ``grant.holds_slot``, never on the entity id. The entity id
        cannot answer this: two acquisitions for the SAME entity can be live at
        once, and ``self._owner != entity_id`` is then vacuously False — the
        grant that gave up waiting would free the one still on the air, and the
        next member would key on top of a transmission in progress.
        """
        if transmitted:
            self._busy_until = monotonic() + self.gap_seconds
        if not grant.holds_slot:
            return
        self._hand_off()

    def mark_external_transmit(self) -> None:
        """Record a frame this queue did not gate.

        Two transmissions deliberately run outside a slot, for opposite reasons:

        A **stop** is interactive and latency-critical — queueing it would make
        the user wait out someone else's gap for a button press.

        A **dual-axis tilt tail** waits on a physical rail before it keys (up to
        ``VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS``), so holding the slot across
        it would starve every other member, and issue #1115's day/night guard
        depends on the tail not holding it.

        Both still occupy the air, so both tell the queue and the NEXT queued
        member spaces itself accordingly instead of assuming the air was free.
        """
        self._busy_until = monotonic() + self.gap_seconds

    # ------------------------------------------------------------------ #
    # Waiter bookkeeping — one place, shared by every grant path
    # ------------------------------------------------------------------ #

    def defers_to_pending(self, entity_id: str) -> bool:
        """Whether a RESEND must stand down for this entity's pending reservation.

        Supersede has a direction. A live dispatch carries the newest decision,
        so it displaces whatever this entity had waiting — that is the whole
        point of the one-reservation-per-entity rule. A reconciliation resend
        re-books a target that was already decided, so displacing a waiting live
        dispatch would put the OLDER number on the air and drop the newer one,
        inverting this module's own guarantee that the frame which finally goes
        out carries the most recent decision.

        Only the WAITING reservation counts. An entity that currently holds the
        slot is mid-transmit and has no pending reservation, so a resend queues
        behind it exactly as it did before.
        """
        return entity_id in self._pending

    def resend_stands_down(self, entity_id: str, *, budget: float) -> bool:
        """Whether a reconciliation resend must yield its whole turn.

        Two independent reasons, and the split between them is the point:

        * :meth:`defers_to_pending` — a live dispatch for this entity is
          already waiting and carries the newer decision. Unbounded from this
          pass's perspective and never this pass's to wait out.
        * The turn would cost more than ``budget``, the pass's REMAINING
          allowance. Spacing owed and reservations ahead are both bounded and
          largely the pass's own doing, so it waits them out when it can afford
          to and stands down only when it cannot.

        The earlier form asked "is the slot free this instant", which folded the
        spacing the pass's own previous resend had just started into the
        stand-down: the first entity to resend disqualified every remaining one,
        so a pass emitted at most one frame no matter how idle the queue was.

        Advisory, not authoritative. The pass asks it to avoid entering a wait
        it cannot pay for; whether the resend actually went out is answered by
        the acquisition itself, so nothing downstream depends on this reading
        still being true when the wait ends.
        """
        return (
            self.defers_to_pending(entity_id) or self.projected_wait_seconds() > budget
        )

    def _enqueue(self, entity_id: str, *, head_of_line: bool) -> _Waiter:
        """Queue a reservation, superseding any this entity already had waiting."""
        previous = self._pending.pop(entity_id, None)
        if previous is not None:
            self._remove(previous)
            if not previous.future.done():
                previous.future.set_result(False)
        waiter = _Waiter(
            entity_id=entity_id,
            future=asyncio.get_running_loop().create_future(),
            head_of_line=head_of_line,
        )
        self._pending[entity_id] = waiter
        tier = self._head_waiters if head_of_line else self._routine_waiters
        tier.append(waiter)
        return waiter

    def _drop(self, waiter: _Waiter) -> None:
        """Forget a reservation that gave up (budget expiry, cancellation)."""
        self._remove(waiter)
        if self._pending.get(waiter.entity_id) is waiter:
            del self._pending[waiter.entity_id]

    def _remove(self, waiter: _Waiter) -> None:
        for tier in (self._head_waiters, self._routine_waiters):
            try:
                tier.remove(waiter)
            except ValueError:
                continue
            return

    def _hand_off(self) -> None:
        """Drop ownership and pass the free slot on.

        The one place ownership is given up, shared by the normal release and
        by the unwind an interrupted :meth:`acquire` performs — mirroring the
        two would be how one of them ends up leaving the slot held.
        """
        self._owner = None
        self._grant_next()

    def _grant_next(self) -> None:
        """Hand the free slot to the next waiter: head tier first, then FIFO."""
        if self._owner is not None:
            return
        for tier in (self._head_waiters, self._routine_waiters):
            while tier:
                waiter = tier.popleft()
                if self._pending.get(waiter.entity_id) is waiter:
                    del self._pending[waiter.entity_id]
                if waiter.future.done():
                    continue
                self._owner = waiter.entity_id
                waiter.future.set_result(True)
                return


def get_command_queue(hass, name: str) -> CommandQueue:
    """Return the shared queue for a NORMALIZED name, creating it on first ask.

    The single accessor: the config-flow dropdown, the coordinator's attach and
    the queue entry's own setup all come through here, so two callers can never
    end up holding different objects for the same name.

    Deliberately not a walk of ``hass.config_entries.async_entries(DOMAIN)`` —
    that walk is the right way to enumerate names for the dropdown, but runtime
    serialization needs a shared OBJECT, not an enumeration.
    """
    registry: dict[str, CommandQueue] = hass.data.setdefault(
        COMMAND_QUEUE_REGISTRY_KEY, {}
    )
    queue = registry.get(name)
    if queue is None:
        queue = CommandQueue(name=name, _registry=registry)
        registry[name] = queue
    return queue
