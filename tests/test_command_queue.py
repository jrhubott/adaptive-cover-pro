"""Issue #1189 — named cover-command dispatch queue.

Covers sharing a queue name never transmit at the same moment: the queue holds
the slot for a configurable gap after every send, so one-way radio backends
(Somfy RTS, RFXtrx) stop losing colliding frames.

Test classes mirror the implementation steps:

* ``TestNormalization`` / ``TestRegistry`` — the pure name folding and the
  ``hass.data`` registry with attach/detach refcounting.
* ``TestQueueMechanics`` — the ``CommandQueue`` state machine (serialization,
  head-of-line, supersede, bounded budget) with no HA involved.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    COMMAND_QUEUE_REGISTRY_KEY,
    CONF_COMMAND_QUEUE,
    CONF_COMMAND_QUEUE_GAP,
    CONF_ENTITIES,
    CONF_SENSOR_TYPE,
    DEFAULT_COMMAND_QUEUE_GAP,
    DOMAIN,
    CoverType,
)
from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler
from custom_components.adaptive_cover_pro.cover_types import (
    POLICY_REGISTRY,
    get_policy,
)
from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
    PositionContext,
    ServiceCallPlan,
)
from custom_components.adaptive_cover_pro.managers.cover_command.queue import (
    CommandQueue,
    QueueGrant,
    get_command_queue,
    normalize_queue_name,
)

# A gap short enough to keep the suite fast but long enough to be observable.
_GAP = 0.05
# Generous budget — the bounded-wait tests set their own tiny one.
_BUDGET = 5.0


def _hass() -> SimpleNamespace:
    """Minimal hass stand-in: the queue registry only ever touches ``.data``."""
    return SimpleNamespace(data={})


# ---------------------------------------------------------------------------
# Step 1 — normalization
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNormalization:
    """``normalize_queue_name`` folds the invisible-typo classes."""

    def test_folds_case(self):
        assert normalize_queue_name("Facade South") == normalize_queue_name(
            "facade south"
        )
        assert normalize_queue_name("FACADE SOUTH") == "facade south"

    def test_collapses_internal_whitespace(self):
        assert normalize_queue_name("Facade  South") == normalize_queue_name(
            "Facade South"
        )
        assert normalize_queue_name("Facade\tSouth") == "facade south"

    def test_strips_surrounding_whitespace(self):
        assert normalize_queue_name("  Facade South  ") == "facade south"

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n", None])
    def test_blank_is_empty(self, blank):
        assert normalize_queue_name(blank) == ""

    def test_casefold_not_lower(self):
        """Casefold, so locale-quirky pairs still collapse (German sharp s)."""
        assert normalize_queue_name("STRASSE") == normalize_queue_name("straße")


# ---------------------------------------------------------------------------
# Step 1 — registry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegistry:
    """``get_command_queue`` is the single shared-object accessor."""

    def test_same_object_for_normalized_equal_names(self):
        hass = _hass()
        a = get_command_queue(hass, normalize_queue_name("Facade South"))
        b = get_command_queue(hass, normalize_queue_name("  facade   south "))
        assert a is b

    def test_distinct_objects_for_distinct_names(self):
        hass = _hass()
        a = get_command_queue(hass, normalize_queue_name("Facade South"))
        b = get_command_queue(hass, normalize_queue_name("Facade North"))
        assert a is not b

    def test_refcount_keeps_queue_until_last_detach(self):
        hass = _hass()
        queue = get_command_queue(hass, "facade south")
        queue.attach()
        queue.attach()
        queue.detach()
        assert hass.data[COMMAND_QUEUE_REGISTRY_KEY].get("facade south") is queue
        queue.detach()
        assert "facade south" not in hass.data[COMMAND_QUEUE_REGISTRY_KEY]

    def test_default_gap_is_the_constant(self):
        hass = _hass()
        queue = get_command_queue(hass, "facade south")
        assert queue.gap_seconds == DEFAULT_COMMAND_QUEUE_GAP


# ---------------------------------------------------------------------------
# Step 2 — queue mechanics (pure asyncio, no HA)
# ---------------------------------------------------------------------------


def _queue(gap: float = _GAP) -> CommandQueue:
    q = CommandQueue("facade south")
    q.set_gap(gap)
    return q


@pytest.mark.unit
class TestQueueMechanics:
    """The ``CommandQueue`` state machine."""

    @pytest.mark.asyncio
    async def test_first_send_is_immediate(self):
        """The gap applies AFTER a send, never before the first one."""
        queue = _queue()
        loop = asyncio.get_running_loop()
        started = loop.time()
        grant = await queue.acquire("cover.a", head_of_line=False, budget=_BUDGET)
        assert isinstance(grant, QueueGrant)
        assert grant.waited_seconds == 0.0
        assert grant.budget_expired is False
        assert grant.depth_at_enqueue == 0
        assert loop.time() - started < _GAP

    @pytest.mark.asyncio
    async def test_two_acquires_serialize_with_the_gap(self):
        queue = _queue()
        loop = asyncio.get_running_loop()
        order: list[tuple[str, float]] = []

        async def run(entity: str) -> None:
            grant = await queue.acquire(entity, head_of_line=False, budget=_BUDGET)
            assert grant is not None
            order.append((entity, loop.time()))
            queue.release(grant, transmitted=True)

        await run("cover.a")
        await run("cover.b")
        assert [e for e, _ in order] == ["cover.a", "cover.b"]
        assert order[1][1] - order[0][1] >= _GAP

    @pytest.mark.asyncio
    async def test_second_waiter_blocks_until_the_holder_releases(self):
        queue = _queue()
        first = await queue.acquire("cover.a", head_of_line=False, budget=_BUDGET)
        assert first is not None

        task = asyncio.create_task(
            queue.acquire("cover.b", head_of_line=False, budget=_BUDGET)
        )
        await asyncio.sleep(0)
        assert not task.done()

        queue.release(first, transmitted=True)
        second = await task
        assert second is not None
        assert second.waited_seconds > 0.0
        assert second.depth_at_enqueue == 0

    @pytest.mark.asyncio
    async def test_release_untransmitted_grants_next_with_no_spacing(self):
        queue = _queue(gap=10.0)
        first = await queue.acquire("cover.a", head_of_line=False, budget=_BUDGET)
        assert first is not None
        task = asyncio.create_task(
            queue.acquire("cover.b", head_of_line=False, budget=_BUDGET)
        )
        await asyncio.sleep(0)
        queue.release(first, transmitted=False)
        second = await asyncio.wait_for(task, timeout=1.0)
        assert second is not None
        assert second.budget_expired is False

    @pytest.mark.asyncio
    async def test_head_of_line_jumps_ahead_of_routine_waiters(self):
        queue = _queue()
        holder = await queue.acquire("cover.h", head_of_line=False, budget=_BUDGET)
        assert holder is not None
        granted: list[str] = []

        async def run(entity: str, *, head: bool) -> None:
            grant = await queue.acquire(entity, head_of_line=head, budget=_BUDGET)
            assert grant is not None
            granted.append(entity)
            queue.release(grant, transmitted=False)

        routine_1 = asyncio.create_task(run("cover.r1", head=False))
        await asyncio.sleep(0)
        routine_2 = asyncio.create_task(run("cover.r2", head=False))
        await asyncio.sleep(0)
        safety = asyncio.create_task(run("cover.s", head=True))
        await asyncio.sleep(0)

        queue.release(holder, transmitted=False)
        await asyncio.wait_for(
            asyncio.gather(routine_1, routine_2, safety), timeout=2.0
        )
        # Safety first, then FIFO within the routine tier.
        assert granted == ["cover.s", "cover.r1", "cover.r2"]

    @pytest.mark.asyncio
    async def test_safety_still_takes_its_spacing(self):
        """Head-of-line is not bypass — a safety command still waits the gap."""
        queue = _queue()
        loop = asyncio.get_running_loop()
        first = await queue.acquire("cover.a", head_of_line=False, budget=_BUDGET)
        assert first is not None
        queue.release(first, transmitted=True)
        started = loop.time()
        safety = await queue.acquire("cover.s", head_of_line=True, budget=_BUDGET)
        assert safety is not None
        assert loop.time() - started >= _GAP

    @pytest.mark.asyncio
    async def test_waiting_reservation_is_superseded_by_a_newer_one(self):
        queue = _queue()
        holder = await queue.acquire("cover.a", head_of_line=False, budget=_BUDGET)
        assert holder is not None

        stale = asyncio.create_task(
            queue.acquire("cover.b", head_of_line=False, budget=_BUDGET)
        )
        await asyncio.sleep(0)
        fresh = asyncio.create_task(
            queue.acquire("cover.b", head_of_line=False, budget=_BUDGET)
        )
        await asyncio.sleep(0)

        assert await asyncio.wait_for(stale, timeout=1.0) is None

        queue.release(holder, transmitted=False)
        assert await asyncio.wait_for(fresh, timeout=1.0) is not None

    @pytest.mark.asyncio
    async def test_current_holder_is_never_superseded(self):
        queue = _queue()
        holder = await queue.acquire("cover.a", head_of_line=False, budget=_BUDGET)
        assert holder is not None
        waiting = asyncio.create_task(
            queue.acquire("cover.a", head_of_line=False, budget=_BUDGET)
        )
        await asyncio.sleep(0)
        # The holder keeps its slot; the newcomer merely queues behind it.
        assert not waiting.done()
        queue.release(holder, transmitted=False)
        assert await asyncio.wait_for(waiting, timeout=1.0) is not None

    @pytest.mark.asyncio
    async def test_budget_expiry_transmits_anyway_and_still_spaces(self):
        queue = _queue(gap=10.0)
        holder = await queue.acquire("cover.a", head_of_line=False, budget=_BUDGET)
        assert holder is not None

        grant = await queue.acquire("cover.b", head_of_line=False, budget=0.02)
        assert grant is not None
        assert grant.budget_expired is True

        # A budget-expired grant's release must still advance busy_until, and
        # must not hand the stuck holder's slot to anyone else.
        queue.release(grant, transmitted=True)
        assert queue.busy_for_seconds() > 0.0

    @pytest.mark.asyncio
    async def test_budget_expiry_during_spacing_still_returns_a_grant(self):
        queue = _queue(gap=10.0)
        first = await queue.acquire("cover.a", head_of_line=False, budget=_BUDGET)
        assert first is not None
        queue.release(first, transmitted=True)
        grant = await queue.acquire("cover.b", head_of_line=False, budget=0.02)
        assert grant is not None
        assert grant.budget_expired is True

    @pytest.mark.asyncio
    async def test_set_gap_none_restores_the_default(self):
        queue = _queue(gap=1.5)
        assert queue.gap_seconds == 1.5
        queue.set_gap(None)
        assert queue.gap_seconds == DEFAULT_COMMAND_QUEUE_GAP

    @pytest.mark.asyncio
    async def test_mark_external_transmit_advances_busy_until(self):
        queue = _queue(gap=1.5)
        assert queue.busy_for_seconds() == 0.0
        queue.mark_external_transmit()
        assert queue.busy_for_seconds() > 0.0

    @pytest.mark.asyncio
    async def test_depth_reports_waiting_reservations(self):
        queue = _queue()
        first = await queue.acquire("cover.a", head_of_line=False, budget=_BUDGET)
        assert first is not None
        assert queue.depth == 0
        task = asyncio.create_task(
            queue.acquire("cover.b", head_of_line=False, budget=_BUDGET)
        )
        await asyncio.sleep(0)
        assert queue.depth == 1
        queue.release(first, transmitted=False)
        second = await asyncio.wait_for(task, timeout=1.0)
        assert second is not None
        assert queue.depth == 0
        queue.release(second, transmitted=False)

    @pytest.mark.asyncio
    async def test_expired_grant_never_frees_the_same_entitys_live_slot(self):
        """Two grants, one entity: only the one that HOLDS the slot may free it.

        Same-entity concurrency is routine — ``apply_position`` can re-book an
        entity a reconciliation resend is already transmitting, and at a
        configured gap above the 30 s budget every wait ends this way. Keying
        the release on the entity id makes the identity check vacuous: the
        grant that gave up waiting frees the one that is still on the air, and
        the next member keys on top of it.
        """
        queue = _queue()
        holder = await queue.acquire("cover.a", head_of_line=False, budget=_BUDGET)
        assert holder is not None
        assert holder.holds_slot is True

        # A second dispatch for the SAME entity gives up waiting.
        expired = await queue.acquire("cover.a", head_of_line=False, budget=0.02)
        assert expired is not None
        assert expired.budget_expired is True
        assert expired.holds_slot is False

        waiter = asyncio.create_task(
            queue.acquire("cover.c", head_of_line=False, budget=_BUDGET)
        )
        await asyncio.sleep(0)

        queue.release(expired, transmitted=True)
        await asyncio.sleep(0)
        # cover.a is still mid-transmit, so the slot is still cover.a's.
        assert queue._owner == "cover.a"
        assert not waiter.done()

        queue.release(holder, transmitted=False)
        assert await asyncio.wait_for(waiter, timeout=1.0) is not None

    def test_grant_is_a_frozen_slotted_dataclass(self):
        grant = QueueGrant(waited_seconds=0.0, budget_expired=False, depth_at_enqueue=0)
        with pytest.raises(Exception):
            grant.waited_seconds = 1.0  # type: ignore[misc]
        assert not hasattr(grant, "__dict__")


# ---------------------------------------------------------------------------
# Step 2b — unwinding an interrupted acquisition
#
# A dispatch task really does get torn down mid-acquire: the #1138 external
# interlock cancels the prior task for a follower when a second external
# command lands, and unload cancels every one of them. A slot stranded by that
# teardown never comes back on its own — every other member then enqueues,
# burns the whole 30 s budget and transmits ``budget_expired``, unspaced and
# simultaneous, which is the exact collision this feature exists to prevent.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInterruptedAcquire:
    """Cancellation must never leave the slot held."""

    @pytest.mark.asyncio
    async def test_cancel_during_the_spacing_sleep_releases_the_slot(self):
        queue = _queue(gap=0.2)
        first = await queue.acquire("cover.a", head_of_line=False, budget=_BUDGET)
        assert first is not None
        queue.release(first, transmitted=True)  # spacing now owed

        task = asyncio.create_task(
            queue.acquire("cover.b", head_of_line=False, budget=_BUDGET)
        )
        await asyncio.sleep(0)
        # cover.b took the slot and is sleeping out the previous send's gap.
        assert queue._owner == "cover.b"

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert queue._owner is None
        # Granted on the remaining spacing, not after the whole budget — the
        # tell that the slot was really free rather than stranded.
        grant = await queue.acquire("cover.c", head_of_line=False, budget=0.5)
        assert grant is not None
        assert grant.budget_expired is False

    @pytest.mark.asyncio
    async def test_cancel_after_the_grant_lands_releases_the_slot(self):
        """``_grant_next`` records the owner and resolves the future together.

        Between those two and the waiting task actually resuming there is a
        whole scheduler turn, and a cancellation delivered in it used to clean
        the tiers while leaving ownership behind.
        """
        queue = _queue(gap=0.0)
        first = await queue.acquire("cover.a", head_of_line=False, budget=_BUDGET)
        assert first is not None
        task = asyncio.create_task(
            queue.acquire("cover.b", head_of_line=False, budget=_BUDGET)
        )
        await asyncio.sleep(0)

        queue.release(first, transmitted=False)
        assert queue._owner == "cover.b"  # granted, but cover.b has not resumed
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert queue._owner is None
        grant = await queue.acquire("cover.c", head_of_line=False, budget=0.5)
        assert grant is not None
        assert grant.budget_expired is False

    @pytest.mark.asyncio
    async def test_supersede_landing_with_the_budget_expiry_is_not_a_grant(self):
        """A ``False`` resolved in the expiry iteration is a skip, not a grant.

        The two events can land in one scheduler batch: the supersede resolves
        the future first, then the deadline callback finds it already done and
        falls back to ``_must_cancel``, so the wait raises ``TimeoutError`` over
        a future that says "superseded". Reading only "was it granted" turns
        that into a budget-expired GRANT, and the caller transmits a target a
        newer decision has already replaced.
        """
        queue = _queue(gap=0.0)
        loop = asyncio.get_running_loop()
        holder = await queue.acquire("cover.a", head_of_line=False, budget=_BUDGET)
        assert holder is not None

        task = asyncio.create_task(
            queue.acquire("cover.b", head_of_line=False, budget=0.05)
        )
        await asyncio.sleep(0)
        # Block the loop past the deadline so the supersede and the deadline
        # handle are picked up in the SAME batch, supersede first.
        time.sleep(0.06)
        loop.call_soon(lambda: queue._enqueue("cover.b", head_of_line=False))

        assert await asyncio.wait_for(task, timeout=1.0) is None

    @pytest.mark.asyncio
    async def test_a_cancelled_dispatch_hands_the_slot_back(self):
        """The same teardown, seen from ``apply_position``."""
        hass = _svc_hass()
        queue = _queue(gap=0.0)
        svc = _svc(hass, command_queue=queue)
        gate = asyncio.Event()

        async def _blocked(*_a, **_kw):
            await gate.wait()

        hass.services.async_call = AsyncMock(side_effect=_blocked)

        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
        ):
            task = asyncio.create_task(
                svc.apply_position("cover.a", 70, "solar", _ctx())
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert queue._owner == "cover.a"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert queue._owner is None

    @pytest.mark.asyncio
    async def test_an_error_before_the_send_hands_the_slot_back(self):
        """Nothing between the grant and the send may strand the slot."""
        hass = _svc_hass()
        queue = _queue(gap=0.0)
        svc = _svc(hass, command_queue=queue)

        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
            patch.object(
                svc, "_prepare_service_call", side_effect=RuntimeError("boom")
            ),
            pytest.raises(RuntimeError),
        ):
            await svc.apply_position("cover.a", 70, "solar", _ctx())

        assert queue._owner is None
        # Nothing went out, so nobody owes the air anything.
        assert queue.busy_for_seconds() == 0.0


# ---------------------------------------------------------------------------
# Step 3 — the gate inside apply_position
# ---------------------------------------------------------------------------


_CAPS = {
    "has_set_position": True,
    "has_set_tilt_position": False,
    "has_open": True,
    "has_close": True,
    "has_stop": True,
}


def _svc_hass() -> MagicMock:
    h = MagicMock()
    h.data = {}
    h.services.async_call = AsyncMock()
    return h


def _svc(hass, *, command_queue=None) -> CoverCommandService:
    s = CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=MagicMock(),
        open_close_threshold=50,
        command_queue=command_queue,
    )
    s._enabled = True
    return s


def _ctx(*, is_safety=False, user_command=False, policy=None):
    return PositionContext(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=1,
        time_threshold=0,
        special_positions=[0, 100],
        force=True,
        is_safety=is_safety,
        user_command=user_command,
        policy=policy,
    )


def _patch_caps(caps=None):
    return patch(
        "custom_components.adaptive_cover_pro.managers.cover_command"
        ".check_cover_features",
        return_value=dict(caps or _CAPS),
    )


@pytest.mark.unit
class TestDispatchGate:
    """``apply_position``'s queue gate — placement, staleness, release."""

    @pytest.mark.asyncio
    async def test_unqueued_cover_dispatch_is_untouched(self):
        """No queue configured → not one queue call, not one registry key."""
        hass = _svc_hass()
        svc = _svc(hass)

        async def _explode(*_a, **_kw):
            raise AssertionError("the queue must not be consulted when unqueued")

        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
            patch.object(CommandQueue, "acquire", _explode),
        ):
            outcome, _ = await svc.apply_position("cover.a", 70, "solar", _ctx())

        assert outcome == "sent"
        assert COMMAND_QUEUE_REGISTRY_KEY not in hass.data

    @pytest.mark.asyncio
    async def test_queued_second_cover_waits_out_the_gap(self):
        hass = _svc_hass()
        queue = _queue()
        svc = _svc(hass, command_queue=queue)
        loop = asyncio.get_running_loop()
        stamps: list[float] = []
        hass.services.async_call = AsyncMock(
            side_effect=lambda *a, **kw: stamps.append(loop.time())
        )

        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
        ):
            await svc.apply_position("cover.a", 70, "solar", _ctx())
            await svc.apply_position("cover.b", 70, "solar", _ctx())

        assert len(stamps) == 2
        assert stamps[1] - stamps[0] >= _GAP

    @pytest.mark.asyncio
    async def test_superseded_reservation_skips_and_books_nothing(self):
        hass = _svc_hass()
        queue = _queue(gap=0.0)
        svc = _svc(hass, command_queue=queue)
        # Somebody else holds the slot so both attempts have to wait.
        blocker = await queue.acquire("cover.blocker", head_of_line=False, budget=5.0)
        assert blocker is not None

        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
        ):
            stale = asyncio.create_task(
                svc.apply_position("cover.a", 70, "solar", _ctx())
            )
            await asyncio.sleep(0)
            fresh = asyncio.create_task(
                svc.apply_position("cover.a", 40, "solar", _ctx())
            )
            await asyncio.sleep(0)

            assert await asyncio.wait_for(stale, timeout=2.0) == (
                "skipped",
                "superseded_in_queue",
            )
            # Nothing booked by the superseded attempt.
            assert svc.state("cover.a").sent_at is None
            assert svc.get_target("cover.a") is None
            svc._grace_mgr.start_command_grace_period.assert_not_called()

            queue.release(blocker, transmitted=False)
            assert await asyncio.wait_for(fresh, timeout=2.0) == (
                "sent",
                "set_cover_position",
            )

    @pytest.mark.asyncio
    async def test_entity_gone_during_the_wait_is_a_clean_skip(self):
        hass = _svc_hass()
        queue = _queue(gap=0.0)
        svc = _svc(hass, command_queue=queue)

        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
            patch.object(svc, "is_entity_unavailable", side_effect=[False, True]),
        ):
            outcome, detail = await svc.apply_position("cover.a", 70, "solar", _ctx())

        assert (outcome, detail) == ("skipped", "cover_unavailable")
        hass.services.async_call.assert_not_awaited()
        assert svc.state("cover.a").sent_at is None
        assert svc.get_target("cover.a") is None
        svc._grace_mgr.start_command_grace_period.assert_not_called()
        # The slot went back untransmitted — no gap owed, no owner left behind.
        assert queue.busy_for_seconds() == 0.0
        assert queue._owner is None

    @pytest.mark.asyncio
    async def test_plan_is_recomputed_after_the_queue_wait(self):
        """Capabilities can change across the wait — the stale plan must not ship."""
        hass = _svc_hass()
        queue = _queue(gap=0.0)
        svc = _svc(hass, command_queue=queue)
        before = ServiceCallPlan(
            service="set_cover_position",
            service_data={"entity_id": "cover.a", "position": 70},
            supports_position=True,
            routed_target=70,
        )
        after = ServiceCallPlan(
            service="open_cover",
            service_data={"entity_id": "cover.a"},
            supports_position=False,
            routed_target=100,
        )
        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
            patch(
                "custom_components.adaptive_cover_pro.managers.cover_command"
                ".route_service_call",
                side_effect=[before, after],
            ) as routed,
        ):
            outcome, detail = await svc.apply_position("cover.a", 70, "solar", _ctx())

        assert routed.call_count == 2
        assert (outcome, detail) == ("sent", "open_cover")

    @pytest.mark.asyncio
    async def test_dry_run_never_touches_the_queue(self):
        hass = _svc_hass()
        queue = _queue()
        svc = _svc(hass, command_queue=queue)
        svc._dry_run = True

        async def _explode(*_a, **_kw):
            raise AssertionError("a dry run must not consult the queue")

        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
            patch.object(CommandQueue, "acquire", _explode),
        ):
            outcome, detail = await svc.apply_position("cover.a", 70, "solar", _ctx())

        assert (outcome, detail) == ("skipped", "dry_run")
        assert queue._owner is None

    @pytest.mark.asyncio
    async def test_slot_is_released_even_when_the_service_call_raises(self):
        hass = _svc_hass()
        queue = _queue()
        svc = _svc(hass, command_queue=queue)
        hass.services.async_call = AsyncMock(side_effect=HomeAssistantError("boom"))

        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
        ):
            outcome, detail = await svc.apply_position("cover.a", 70, "solar", _ctx())

        assert (outcome, detail) == ("skipped", "service_call_failed")
        assert queue._owner is None

    @pytest.mark.asyncio
    async def test_no_capable_service_releases_untransmitted(self):
        hass = _svc_hass()
        queue = _queue()
        svc = _svc(hass, command_queue=queue)
        caps = dict(_CAPS)
        caps.update(has_set_position=False, has_open=False, has_close=False)

        with (
            _patch_caps(caps),
            patch.object(svc, "_get_current_position", return_value=10),
        ):
            outcome, detail = await svc.apply_position("cover.a", 70, "solar", _ctx())

        assert (outcome, detail) == ("skipped", "no_capable_service")
        assert queue._owner is None
        assert queue.busy_for_seconds() == 0.0


# ---------------------------------------------------------------------------
# Step 4 — ordering + grace-window regression spine
# ---------------------------------------------------------------------------


class _RecordingPolicy:
    """A policy stub that records the order of the dispatch-path hooks."""

    def __init__(self, log: list[str], *, tail: float = 0.0) -> None:
        self._log = log
        self._tail = tail

    def capture_dispatch_token(self, _entity_id):
        self._log.append("capture_dispatch_token")
        return "token"

    async def await_dispatch_clearance(self, _entity_id, **_kw):
        self._log.append("await_dispatch_clearance")
        return True

    async def before_position_command(self, *_a, **_kw):
        return None

    async def after_position_command(self, *_a, **_kw):
        self._log.append("after_position_command")
        if self._tail:
            await asyncio.sleep(self._tail)

    async def maybe_update_tilt_only(self, *_a, **_kw):
        return None


@pytest.mark.unit
class TestOrderingAndGraceWindow:
    """The placement is load-bearing in both directions (#1115, #1139, #853)."""

    @pytest.mark.asyncio
    async def test_queue_acquire_runs_after_dispatch_clearance(self):
        """Before the clearance gate the Model C middle rail would deadlock."""
        hass = _svc_hass()
        queue = _queue(gap=0.0)
        svc = _svc(hass, command_queue=queue)
        log: list[str] = []
        policy = _RecordingPolicy(log)

        real_acquire = CommandQueue.acquire

        async def _tracked(self, *a, **kw):
            log.append("queue_acquire")
            return await real_acquire(self, *a, **kw)

        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
            patch.object(CommandQueue, "acquire", _tracked),
        ):
            await svc.apply_position("cover.a", 70, "solar", _ctx(policy=policy))

        assert log[:3] == [
            "capture_dispatch_token",
            "await_dispatch_clearance",
            "queue_acquire",
        ]

    @pytest.mark.asyncio
    async def test_command_grace_period_starts_after_queue_wait(self):
        """#1139: a 20 s wait must not burn the 5 s grace window before the send."""
        hass = _svc_hass()
        queue = _queue()
        svc = _svc(hass, command_queue=queue)
        loop = asyncio.get_running_loop()
        grace_at: list[float] = []
        svc._grace_mgr.start_command_grace_period = MagicMock(
            side_effect=lambda _e: grace_at.append(loop.time())
        )

        # Hold the queue so the dispatch below has to wait out a real gap.
        held = await queue.acquire("cover.other", head_of_line=False, budget=5.0)
        assert held is not None
        queue.release(held, transmitted=True)
        started = loop.time()

        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
        ):
            await svc.apply_position("cover.a", 70, "solar", _ctx())

        assert held is not None
        assert grace_at, "the grace window must be started by a real dispatch"
        assert grace_at[0] - started >= _GAP
        # sent_at is booked at transmit time too, not at decision time (#853).
        assert svc.state("cover.a").sent_at is not None

    @pytest.mark.asyncio
    async def test_venetian_tail_runs_outside_the_queue_slot(self):
        """The settle+tilt tail must not starve the queue for its whole duration."""
        hass = _svc_hass()
        queue = _queue(gap=0.0)
        svc = _svc(hass, command_queue=queue)
        log: list[str] = []
        policy = _RecordingPolicy(log, tail=0.2)

        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
        ):
            first = asyncio.create_task(
                svc.apply_position("cover.a", 70, "solar", _ctx(policy=policy))
            )
            await asyncio.sleep(0.05)
            # The tail is still running; a second cover must not be blocked by it.
            second = await asyncio.wait_for(
                queue.acquire("cover.b", head_of_line=False, budget=0.05),
                timeout=0.5,
            )
            assert second is not None
            assert second.budget_expired is False
            queue.release(second, transmitted=False)
            await asyncio.wait_for(first, timeout=2.0)

        assert "after_position_command" in log


# ---------------------------------------------------------------------------
# Step 5 — the other wire sites
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOtherWireSites:
    """Reconciliation queues like any dispatch; a stop reports without queueing."""

    @pytest.mark.asyncio
    async def test_reconciliation_resend_takes_a_routine_turn(self):
        hass = _svc_hass()
        queue = _queue(gap=0.0)
        svc = _svc(hass, command_queue=queue)
        calls: list[bool] = []
        real_acquire = CommandQueue.acquire

        async def _tracked(self, entity_id, *, head_of_line, budget):
            calls.append(head_of_line)
            return await real_acquire(
                self, entity_id, head_of_line=head_of_line, budget=budget
            )

        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
            patch.object(CommandQueue, "acquire", _tracked),
        ):
            await svc._execute_command("cover.a", 70)

        assert calls == [False]
        hass.services.async_call.assert_awaited_once()
        assert queue._owner is None
        assert queue.busy_for_seconds() == 0.0

    @pytest.mark.asyncio
    async def test_reconciliation_resend_waits_behind_a_holder(self):
        hass = _svc_hass()
        queue = _queue(gap=0.0)
        svc = _svc(hass, command_queue=queue)
        holder = await queue.acquire("cover.other", head_of_line=False, budget=5.0)
        assert holder is not None

        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
        ):
            task = asyncio.create_task(svc._execute_command("cover.a", 70))
            await asyncio.sleep(0)
            hass.services.async_call.assert_not_awaited()
            queue.release(holder, transmitted=False)
            await asyncio.wait_for(task, timeout=2.0)

        hass.services.async_call.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconciliation_resend_yields_to_a_waiting_live_dispatch(self):
        """Supersede has a DIRECTION: a resend never displaces a live decision.

        Both seams acquire under the same one-reservation-per-entity rule, so a
        resend enqueuing behind a WAITING ``apply_position`` for the same entity
        used to cancel it. The fresh dispatch then returned
        ``superseded_in_queue`` having booked nothing, and what went on the air
        was the previously booked target — the module's own guarantee that the
        frame that finally goes out carries the most recent decision, inverted.
        """
        hass = _svc_hass()
        queue = _queue(gap=0.0)
        svc = _svc(hass, command_queue=queue)
        # Somebody else holds the slot so the live dispatch has to wait.
        blocker = await queue.acquire("cover.blocker", head_of_line=False, budget=5.0)
        assert blocker is not None

        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
        ):
            live = asyncio.create_task(
                svc.apply_position("cover.a", 70, "solar", _ctx())
            )
            await asyncio.sleep(0)
            assert queue.depth == 1

            # The reconciliation pass re-books the OLD target behind it.
            resend = asyncio.create_task(svc._execute_command("cover.a", 40))
            await asyncio.sleep(0)

            # It stood down at once instead of taking the live reservation's
            # place — no reservation of its own, and the live one untouched.
            assert resend.done()
            assert not live.done()
            assert queue.depth == 1
            hass.services.async_call.assert_not_awaited()

            queue.release(blocker, transmitted=False)
            assert await asyncio.wait_for(live, timeout=2.0) == (
                "sent",
                "set_cover_position",
            )
            await asyncio.wait_for(resend, timeout=1.0)

        # One frame, carrying the FRESH target.
        assert hass.services.async_call.await_count == 1
        assert hass.services.async_call.await_args[0][2]["position"] == 70

    @pytest.mark.asyncio
    async def test_stop_transmits_immediately_and_reports_the_air_as_busy(self):
        """Stops skip the queue but still report the air as busy.

        A button press must not wait out someone else's gap — but it does
        key the radio, so the queue is told.
        """
        hass = _svc_hass()
        queue = _queue()
        svc = _svc(hass, command_queue=queue)
        marked: list[str] = []
        real_mark = CommandQueue.mark_external_transmit

        def _tracked(self):
            marked.append(self.name)
            real_mark(self)

        with patch.object(CommandQueue, "mark_external_transmit", _tracked):
            await svc._stop_tracker.call_stop_cover("cover.a")

        hass.services.async_call.assert_awaited_once()
        assert marked == [queue.name]
        assert queue.busy_for_seconds() > 0.0

    @pytest.mark.asyncio
    async def test_stop_on_an_unqueued_cover_is_untouched(self):
        hass = _svc_hass()
        svc = _svc(hass)
        await svc._stop_tracker.call_stop_cover("cover.a")
        hass.services.async_call.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_queue_external_transmit_is_a_noop_when_unqueued(self):
        hass = _svc_hass()
        svc = _svc(hass)
        svc.mark_queue_external_transmit()  # must not raise
        assert COMMAND_QUEUE_REGISTRY_KEY not in hass.data

    @pytest.mark.asyncio
    async def test_mark_queue_external_transmit_advances_the_spacing(self):
        hass = _svc_hass()
        queue = _queue()
        svc = _svc(hass, command_queue=queue)
        assert queue.busy_for_seconds() == 0.0
        svc.mark_queue_external_transmit()
        assert queue.busy_for_seconds() > 0.0


# ---------------------------------------------------------------------------
# Step 5b — the venetian tail's own frame
#
# The settle+tilt tail runs OUTSIDE the slot on purpose (holding it across a
# settle capped at 60 s would starve the queue, and #1115's day/night guard
# depends on the tail not holding it). That makes the tilt frame a transmission
# the queue never gated, exactly like a stop — so, exactly like a stop, it has
# to tell the queue, or the next member keys on top of it.
# ---------------------------------------------------------------------------


def _tilt_sequencer(queue, *, position: int = 70):
    """Build a ``DualAxisSequencer`` wired to *queue*, settling instantly."""
    from custom_components.adaptive_cover_pro.cover_types.venetian.sequencer import (
        DualAxisSequencer,
    )

    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    return hass, DualAxisSequencer(
        hass=hass,
        logger=MagicMock(),
        grace_mgr=MagicMock(),
        get_current_position=lambda _eid: position,
        set_commanded_position=lambda *_a: None,
        position_tolerance=5,
        is_dry_run=lambda: False,
        post_settle_hold_seconds=0,
        mark_air_busy=None if queue is None else queue.mark_external_transmit,
    )


@pytest.mark.unit
@pytest.mark.usefixtures("neutralize_venetian_delays")
class TestVenetianTailReportsItsFrame:
    """The tail transmits outside the slot, so it reports the air as busy."""

    @pytest.mark.asyncio
    async def test_the_tail_reports_its_tilt_frame_to_the_queue(self):
        queue = _queue()
        hass, seq = _tilt_sequencer(queue)
        assert queue.busy_for_seconds() == 0.0

        await seq.run_sequence(
            "cover.a", position_target=70, tilt_target=40, reason="solar"
        )

        hass.services.async_call.assert_awaited()
        assert queue.busy_for_seconds() > 0.0

    @pytest.mark.asyncio
    async def test_a_deduped_tail_reports_nothing(self):
        """No frame, no spacing — the tail only owes the air what it used."""
        queue = _queue()
        _hass_obj, seq = _tilt_sequencer(queue)
        seq._tilt_targets["cover.a"] = 40
        seq._tilt_targets_verified.add("cover.a")

        await seq.run_sequence(
            "cover.a", position_target=70, tilt_target=40, reason="solar"
        )

        assert queue.busy_for_seconds() == 0.0

    @pytest.mark.asyncio
    async def test_an_unwired_sequencer_is_untouched(self):
        _hass_obj, seq = _tilt_sequencer(None)
        await seq.run_sequence(
            "cover.a", position_target=70, tilt_target=40, reason="solar"
        )

    def test_the_policy_wires_the_queue_reporter_onto_its_sequencer(self):
        """``attach`` is where the tail meets the command service."""
        from custom_components.adaptive_cover_pro.cover_types.venetian.policy import (
            VenetianPolicy,
        )

        svc = _svc(_svc_hass(), command_queue=_queue())
        policy = VenetianPolicy()
        policy.attach(
            hass=MagicMock(),
            logger=MagicMock(),
            grace_mgr=MagicMock(),
            get_current_position=lambda _eid: 0,
            set_commanded_position=lambda *_a: None,
            position_tolerance=5,
            is_dry_run=lambda: False,
            mark_air_busy=svc.mark_queue_external_transmit,
        )
        assert policy.sequencer._mark_air_busy == svc.mark_queue_external_transmit


# ---------------------------------------------------------------------------
# Step 6 — the Command Queue entry type
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEntryType:
    """A virtual entry type, discriminated by capability and never by string."""

    def test_cover_type_member_and_display_name(self):
        assert CoverType.COMMAND_QUEUE == "cover_command_queue"
        assert CoverType.COMMAND_QUEUE.display_name == "Command Queue"

    def test_policy_capabilities(self):
        policy = get_policy(CoverType.COMMAND_QUEUE)
        assert policy.controls_cover is False
        assert policy.is_command_queue is True
        assert policy.axes == ()

    def test_every_other_policy_is_not_a_command_queue(self):
        for cover_type in POLICY_REGISTRY:
            if cover_type == CoverType.COMMAND_QUEUE:
                continue
            assert get_policy(cover_type).is_command_queue is False

    def test_entry_filters_exclude_and_include_the_right_entries(self):
        from custom_components.adaptive_cover_pro.profile_link import (
            _building_profile_entries,
            _command_queue_entries,
            _cover_entries,
        )

        entries = [
            SimpleNamespace(
                entry_id="cover", data={CONF_SENSOR_TYPE: CoverType.BLIND}, options={}
            ),
            SimpleNamespace(
                entry_id="profile",
                data={CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
                options={},
            ),
            SimpleNamespace(
                entry_id="queue",
                data={CONF_SENSOR_TYPE: CoverType.COMMAND_QUEUE},
                options={},
            ),
        ]
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = entries

        assert [e.entry_id for e in _cover_entries(hass)] == ["cover"]
        assert [e.entry_id for e in _building_profile_entries(hass)] == ["profile"]
        assert [e.entry_id for e in _command_queue_entries(hass)] == ["queue"]


# ---------------------------------------------------------------------------
# Step 7 — queue-entry lifecycle (real config entries)
# ---------------------------------------------------------------------------


async def _add_queue_entry(hass, *, name="Facade South", gap=2.5, entry_id="queue_1"):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": name, CONF_SENSOR_TYPE: CoverType.COMMAND_QUEUE},
        options={CONF_COMMAND_QUEUE_GAP: gap},
        entry_id=entry_id,
        title=f"Command Queue {name}",
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _add_cover_entry(hass, *, entry_id, queue_name, entity="cover.test_blind"):
    from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

    options = dict(VERTICAL_OPTIONS)
    options[CONF_ENTITIES] = [entity]
    if queue_name is not None:
        options[CONF_COMMAND_QUEUE] = queue_name
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": entry_id, CONF_SENSOR_TYPE: CoverType.BLIND},
        options=options,
        entry_id=entry_id,
        title=entry_id,
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _registry(hass) -> dict:
    return hass.data.get(COMMAND_QUEUE_REGISTRY_KEY, {})


@pytest.mark.integration
class TestQueueEntryLifecycle:
    """The queue entry owns the gap; the covers own the membership."""

    async def test_queue_entry_binds_its_gap(self, hass):
        await _add_queue_entry(hass, gap=2.5)
        queue = _registry(hass)["facade south"]
        assert queue.gap_seconds == 2.5
        assert queue.attached == 1

    async def test_a_name_with_no_entry_still_serializes_at_the_default(self, hass):
        await _add_cover_entry(hass, entry_id="cov_a", queue_name="Unowned Queue")
        queue = _registry(hass)["unowned queue"]
        assert queue.gap_seconds == DEFAULT_COMMAND_QUEUE_GAP
        assert queue.attached == 1

    async def test_cover_joins_the_queue_by_normalized_name(self, hass):
        await _add_queue_entry(hass, name="Facade South", gap=2.5)
        await _add_cover_entry(hass, entry_id="cov_a", queue_name="  FACADE   south ")
        assert list(_registry(hass)) == ["facade south"]
        queue = _registry(hass)["facade south"]
        assert queue.attached == 2
        assert queue.gap_seconds == 2.5

    async def test_options_update_propagates_without_reloading_members(self, hass):
        entry = await _add_queue_entry(hass, gap=2.5)
        cover = await _add_cover_entry(
            hass, entry_id="cov_a", queue_name="Facade South"
        )
        queue = _registry(hass)["facade south"]
        coordinator_before = cover.runtime_data

        hass.config_entries.async_update_entry(
            entry, options={CONF_COMMAND_QUEUE_GAP: 7.5}
        )
        await hass.async_block_till_done()

        assert queue.gap_seconds == 7.5
        # Same live object, same live coordinator — nothing was torn down.
        assert _registry(hass)["facade south"] is queue
        assert cover.runtime_data is coordinator_before

    async def test_unloading_the_entry_reverts_the_gap_but_keeps_members(self, hass):
        entry = await _add_queue_entry(hass, gap=2.5)
        await _add_cover_entry(hass, entry_id="cov_a", queue_name="Facade South")
        queue = _registry(hass)["facade south"]

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert _registry(hass)["facade south"] is queue
        assert queue.gap_seconds == DEFAULT_COMMAND_QUEUE_GAP
        assert queue.attached == 1

    async def test_member_reload_keeps_the_same_queue_object(self, hass):
        await _add_queue_entry(hass, gap=2.5)
        cover = await _add_cover_entry(
            hass, entry_id="cov_a", queue_name="Facade South"
        )
        queue = _registry(hass)["facade south"]

        from tests.ha_helpers import _patch_coordinator_refresh

        with _patch_coordinator_refresh():
            await hass.config_entries.async_reload(cover.entry_id)
            await hass.async_block_till_done()

        assert _registry(hass)["facade south"] is queue
        assert queue.attached == 2

    async def test_registry_key_drops_when_the_last_member_leaves(self, hass):
        entry = await _add_queue_entry(hass, gap=2.5)
        cover = await _add_cover_entry(
            hass, entry_id="cov_a", queue_name="Facade South"
        )

        await hass.config_entries.async_unload(cover.entry_id)
        await hass.async_block_till_done()
        assert "facade south" in _registry(hass)

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert "facade south" not in _registry(hass)

    async def test_removing_the_queue_entry_leaves_cover_options_untouched(self, hass):
        entry = await _add_queue_entry(hass, gap=2.5)
        cover = await _add_cover_entry(
            hass, entry_id="cov_a", queue_name="Facade South"
        )

        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

        # Dangling names are deliberate and functional: the cover keeps its
        # assignment and reverts to the default gap.
        assert cover.options[CONF_COMMAND_QUEUE] == "Facade South"

    async def test_no_crosstalk_with_building_profiles(self, hass):
        """A queue entry must never reach the profile propagation listener."""
        from custom_components.adaptive_cover_pro import _async_profile_propagate

        entry = await _add_queue_entry(hass, gap=2.5)
        cover = await _add_cover_entry(
            hass, entry_id="cov_a", queue_name="Facade South"
        )
        before = dict(cover.options)

        await _async_profile_propagate(hass, entry)
        await hass.async_block_till_done()

        assert dict(cover.options) == before


# ---------------------------------------------------------------------------
# Step 8 — two live entries serialize
# ---------------------------------------------------------------------------


def _seed_cover_state(hass, entity_id: str, position: int = 10) -> None:
    """Seed a position-capable cover: OPEN|CLOSE|SET_POSITION|STOP."""
    hass.states.async_set(
        entity_id,
        "open",
        {"current_position": position, "supported_features": 15},
    )


def _timed_cover_calls(order: list[tuple[str, float]]):
    """Patch ``ServiceRegistry.async_call`` to stamp every cover command.

    Patched on the CLASS: ``ServiceRegistry`` uses slots, so the instance
    attribute is read-only. One helper rather than three copies — the three
    tests below differ in what they assert about the order, not in how they
    observe it.
    """
    from homeassistant.core import ServiceRegistry

    original = ServiceRegistry.async_call
    loop = asyncio.get_running_loop()

    async def _timed(self, domain, service, service_data=None, *args, **kwargs):
        if domain == "cover":
            entity = (service_data or {}).get("entity_id")
            order.append((entity, loop.time()))
        return await original(self, domain, service, service_data, *args, **kwargs)

    return patch.object(ServiceRegistry, "async_call", _timed)


async def _dispatch(entry, entity_id: str, position: int, *, is_safety=False):
    svc = entry.runtime_data._cmd_svc
    return await svc.apply_position(
        entity_id,
        position,
        "solar",
        PositionContext(
            auto_control=True,
            manual_override=False,
            sun_just_appeared=False,
            min_change=1,
            time_threshold=0,
            special_positions=[0, 100],
            force=True,
            is_safety=is_safety,
        ),
    )


@pytest.mark.integration
class TestCrossEntrySerialization:
    """The whole point: two independent config entries take turns."""

    async def test_entries_sharing_a_name_serialize_across_casing(self, hass):
        _seed_cover_state(hass, "cover.a")
        _seed_cover_state(hass, "cover.b")
        await _add_queue_entry(hass, name="Facade South", gap=_GAP)
        a = await _add_cover_entry(
            hass, entry_id="cov_a", queue_name="Facade South", entity="cover.a"
        )
        b = await _add_cover_entry(
            hass, entry_id="cov_b", queue_name="  facade   SOUTH ", entity="cover.b"
        )
        assert list(_registry(hass)) == ["facade south"]

        order: list[tuple[str, float]] = []
        with _timed_cover_calls(order):
            await asyncio.gather(
                _dispatch(a, "cover.a", 70),
                _dispatch(b, "cover.b", 70),
            )

        assert {eid for eid, _ in order} == {"cover.a", "cover.b"}
        assert order[1][1] - order[0][1] >= _GAP

    async def test_different_names_do_not_influence_each_other(self, hass):
        _seed_cover_state(hass, "cover.a")
        _seed_cover_state(hass, "cover.b")
        a = await _add_cover_entry(
            hass, entry_id="cov_a", queue_name="North", entity="cover.a"
        )
        b = await _add_cover_entry(
            hass, entry_id="cov_b", queue_name="South", entity="cover.b"
        )
        assert sorted(_registry(hass)) == ["north", "south"]

        order: list[tuple[str, float]] = []
        loop = asyncio.get_running_loop()
        started = loop.time()
        with _timed_cover_calls(order):
            await asyncio.gather(
                _dispatch(a, "cover.a", 70),
                _dispatch(b, "cover.b", 70),
            )

        assert {eid for eid, _ in order} == {"cover.a", "cover.b"}
        # Both go out promptly — well inside one default gap.
        assert max(ts for _, ts in order) - started < DEFAULT_COMMAND_QUEUE_GAP

    async def test_safety_goes_first_but_still_waits_its_spacing(self, hass):
        _seed_cover_state(hass, "cover.a")
        _seed_cover_state(hass, "cover.b")
        await _add_queue_entry(hass, name="Facade South", gap=_GAP)
        a = await _add_cover_entry(
            hass, entry_id="cov_a", queue_name="Facade South", entity="cover.a"
        )
        b = await _add_cover_entry(
            hass, entry_id="cov_b", queue_name="Facade South", entity="cover.b"
        )
        queue = _registry(hass)["facade south"]

        # A third member is mid-transmission, so both dispatches must queue.
        holder = await queue.acquire("cover.z", head_of_line=False, budget=5.0)
        assert holder is not None

        loop = asyncio.get_running_loop()
        order: list[tuple[str, float]] = []

        with _timed_cover_calls(order):
            routine = asyncio.create_task(_dispatch(a, "cover.a", 70))
            await asyncio.sleep(0)
            safety = asyncio.create_task(_dispatch(b, "cover.b", 0, is_safety=True))
            await asyncio.sleep(0)
            queue.release(holder, transmitted=True)
            released_at = loop.time()
            await asyncio.wait_for(asyncio.gather(routine, safety), timeout=5.0)

        assert [eid for eid, _ in order] == ["cover.b", "cover.a"]
        # Head of line, NOT bypass: the safety command still took its spacing.
        assert order[0][1] - released_at >= _GAP


# ---------------------------------------------------------------------------
# Step 9 — config flow: create + options
# ---------------------------------------------------------------------------


def _schema_keys(schema) -> set[str]:
    return {str(marker.schema) for marker in schema.schema}


def _selector_for(schema, key):
    for marker, sel in schema.schema.items():
        if str(marker.schema) == key:
            return sel
    raise AssertionError(f"{key} not in schema")


@pytest.mark.integration
class TestCommandQueueConfigFlow:
    """Creating a queue, editing it, and assigning covers to it."""

    async def test_create_menu_offers_the_queue(self, hass):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] == "menu"
        assert "create_command_queue" in result["menu_options"]

    async def test_create_step_round_trips_name_and_gap(self, hass):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_command_queue"}
        )
        assert result["type"] == "form"
        assert result["step_id"] == "create_command_queue"
        assert _schema_keys(result["data_schema"]) == {"name", CONF_COMMAND_QUEUE_GAP}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"name": "Facade South", CONF_COMMAND_QUEUE_GAP: 7.5},
        )
        assert result["type"] == "create_entry"
        entry = result["result"]
        assert entry.data[CONF_SENSOR_TYPE] == CoverType.COMMAND_QUEUE
        assert entry.data["name"] == "Facade South"
        assert entry.options[CONF_COMMAND_QUEUE_GAP] == 7.5

    async def test_a_second_queue_with_the_same_name_is_refused(self, hass):
        """The NAME is the identity here — two entries owning it corrupt the gap.

        Unlike a Building Profile, which links by ``entry_id``, a Command Queue
        entry binds itself to the shared queue by normalized name. Two entries
        on one name both ``attach()`` and both ``set_gap()``, so the bound gap
        is whichever loaded last — and unloading or deleting EITHER runs
        ``set_gap(None)``, silently reverting the queue to the default while
        the surviving entry's UI still shows its configured value.
        """
        await _add_queue_entry(hass, name="Facade South", gap=7.5)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_command_queue"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            # Normalized-equal, not byte-equal: the invisible-typo classes the
            # matcher folds everywhere else must fold here too.
            {"name": "  facade   SOUTH ", CONF_COMMAND_QUEUE_GAP: 2.0},
        )
        assert result["type"] == "abort"
        assert result["reason"] == "already_configured"

        queue_entries = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_SENSOR_TYPE) == CoverType.COMMAND_QUEUE
        ]
        assert len(queue_entries) == 1
        assert queue_entries[0].options[CONF_COMMAND_QUEUE_GAP] == 7.5

    async def test_a_distinct_queue_name_still_creates(self, hass):
        await _add_queue_entry(hass, name="Facade South", gap=7.5)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "create_command_queue"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"name": "Facade North", CONF_COMMAND_QUEUE_GAP: 2.0},
        )
        assert result["type"] == "create_entry"
        assert result["result"].data["name"] == "Facade North"

    async def test_create_step_defaults_the_gap_to_the_constant(self, hass):
        from custom_components.adaptive_cover_pro.config_flow import (
            COMMAND_QUEUE_CREATE_SCHEMA,
        )

        validated = COMMAND_QUEUE_CREATE_SCHEMA({"name": "Q"})
        assert validated[CONF_COMMAND_QUEUE_GAP] == DEFAULT_COMMAND_QUEUE_GAP

    async def test_queue_options_menu(self, hass):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={"name": "Facade South", CONF_SENSOR_TYPE: CoverType.COMMAND_QUEUE},
            options={CONF_COMMAND_QUEUE_GAP: 2.5},
            entry_id="queue_1",
            title="Command Queue Facade South",
        )
        entry.add_to_hass(hass)
        flow = OptionsFlowHandler(entry)
        flow.hass = hass

        result = await flow.async_step_init()
        assert result["type"] == "menu"
        assert result["menu_options"] == [
            "queue_settings",
            "queue_overview",
            "done",
        ]

    async def test_queue_settings_round_trips_the_gap(self, hass):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={"name": "Facade South", CONF_SENSOR_TYPE: CoverType.COMMAND_QUEUE},
            options={CONF_COMMAND_QUEUE_GAP: 2.5},
            entry_id="queue_1",
            title="Command Queue Facade South",
        )
        entry.add_to_hass(hass)
        flow = OptionsFlowHandler(entry)
        flow.hass = hass

        result = await flow.async_step_queue_settings()
        assert result["type"] == "form"
        assert _schema_keys(result["data_schema"]) == {CONF_COMMAND_QUEUE_GAP}

        await flow.async_step_queue_settings({CONF_COMMAND_QUEUE_GAP: 9.0})
        assert flow.options[CONF_COMMAND_QUEUE_GAP] == 9.0

    async def test_queue_gap_range_is_single_sourced(self):
        from custom_components.adaptive_cover_pro.const import OPTION_RANGES

        assert OPTION_RANGES[CONF_COMMAND_QUEUE_GAP] == (0.0, 60.0)

    async def test_queue_overview_lists_matching_covers_only(self, hass):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={"name": "Facade South", CONF_SENSOR_TYPE: CoverType.COMMAND_QUEUE},
            options={CONF_COMMAND_QUEUE_GAP: 2.5},
            entry_id="queue_1",
            title="Command Queue Facade South",
        )
        entry.add_to_hass(hass)
        for entry_id, title, queue_name in (
            ("cov_a", "Kitchen", "  FACADE   south "),
            ("cov_b", "Study", "Facade North"),
            ("cov_c", "Hall", None),
        ):
            options = {} if queue_name is None else {CONF_COMMAND_QUEUE: queue_name}
            member = MockConfigEntry(
                domain=DOMAIN,
                data={"name": title, CONF_SENSOR_TYPE: CoverType.BLIND},
                options=options,
                entry_id=entry_id,
                title=title,
            )
            member.add_to_hass(hass)

        flow = OptionsFlowHandler(entry)
        flow.hass = hass
        result = await flow.async_step_queue_overview()
        overview = result["description_placeholders"]["overview"]
        assert "Kitchen" in overview
        assert "Study" not in overview
        assert "Hall" not in overview

    async def test_automation_step_dropdown_lists_the_deduped_union(self, hass):
        queue_entry = MockConfigEntry(
            domain=DOMAIN,
            data={"name": "Roof Terrace", CONF_SENSOR_TYPE: CoverType.COMMAND_QUEUE},
            options={},
            entry_id="queue_1",
            title="Command Queue Roof Terrace",
        )
        queue_entry.add_to_hass(hass)
        first = MockConfigEntry(
            domain=DOMAIN,
            data={"name": "A", CONF_SENSOR_TYPE: CoverType.BLIND},
            options={CONF_COMMAND_QUEUE: "Facade South"},
            entry_id="cov_a",
            title="A",
        )
        first.add_to_hass(hass)
        second = MockConfigEntry(
            domain=DOMAIN,
            data={"name": "B", CONF_SENSOR_TYPE: CoverType.BLIND},
            options={CONF_COMMAND_QUEUE: "facade  south"},
            entry_id="cov_b",
            title="B",
        )
        second.add_to_hass(hass)

        flow = OptionsFlowHandler(first)
        flow.hass = hass
        flow.handler = first.entry_id
        result = await flow.async_step_automation()
        selector_obj = _selector_for(result["data_schema"], CONF_COMMAND_QUEUE)
        labels = [o["label"] for o in selector_obj.config["options"]]
        # Deduped by normalized name, displayed in first-seen original casing.
        assert labels == ["Facade South", "Roof Terrace"]
        assert selector_obj.config["custom_value"] is True

    async def test_free_text_is_stored_exactly_as_typed(self, hass):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={"name": "A", CONF_SENSOR_TYPE: CoverType.BLIND},
            options={},
            entry_id="cov_a",
            title="A",
        )
        entry.add_to_hass(hass)
        flow = OptionsFlowHandler(entry)
        flow.hass = hass
        flow.handler = entry.entry_id

        await flow.async_step_automation({CONF_COMMAND_QUEUE: "Facade South"})
        assert flow.options[CONF_COMMAND_QUEUE] == "Facade South"

    async def test_blank_submission_pops_the_key(self, hass):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={"name": "A", CONF_SENSOR_TYPE: CoverType.BLIND},
            options={CONF_COMMAND_QUEUE: "Facade South"},
            entry_id="cov_a",
            title="A",
        )
        entry.add_to_hass(hass)
        flow = OptionsFlowHandler(entry)
        flow.hass = hass
        flow.handler = entry.entry_id

        await flow.async_step_automation({CONF_COMMAND_QUEUE: ""})
        assert CONF_COMMAND_QUEUE not in flow.options

    async def test_absent_submission_also_pops_the_key(self, hass):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={"name": "A", CONF_SENSOR_TYPE: CoverType.BLIND},
            options={CONF_COMMAND_QUEUE: "Facade South"},
            entry_id="cov_a",
            title="A",
        )
        entry.add_to_hass(hass)
        flow = OptionsFlowHandler(entry)
        flow.hass = hass
        flow.handler = entry.entry_id

        await flow.async_step_automation({})
        assert CONF_COMMAND_QUEUE not in flow.options

    def test_queue_is_in_the_automation_sync_category(self):
        from custom_components.adaptive_cover_pro.config_flow import SYNC_CATEGORIES

        assert CONF_COMMAND_QUEUE in SYNC_CATEGORIES["automation"]


# ---------------------------------------------------------------------------
# Step 12 — diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQueueDiagnostics:
    """Why a cover moved 8 seconds late must be answerable from the data."""

    @pytest.mark.asyncio
    async def test_unqueued_dispatch_records_no_queue_fields(self):
        hass = _svc_hass()
        svc = _svc(hass)
        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
        ):
            await svc.apply_position("cover.a", 70, "solar", _ctx())

        action = svc.last_cover_action
        assert "queue_name" not in action

    @pytest.mark.asyncio
    async def test_immediate_dispatch_records_the_immediate_outcome(self):
        hass = _svc_hass()
        queue = _queue()
        svc = _svc(hass, command_queue=queue)
        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
        ):
            await svc.apply_position("cover.a", 70, "solar", _ctx())

        action = svc.last_cover_action
        assert action["queue_name"] == "facade south"
        assert action["queue_outcome"] == "immediate"
        assert action["queue_wait_seconds"] == 0.0
        assert action["queue_depth_at_enqueue"] == 0

    @pytest.mark.asyncio
    async def test_a_waited_dispatch_records_the_wait(self):
        hass = _svc_hass()
        queue = _queue()
        svc = _svc(hass, command_queue=queue)
        held = await queue.acquire("cover.other", head_of_line=False, budget=5.0)
        assert held is not None
        queue.release(held, transmitted=True)

        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
        ):
            await svc.apply_position("cover.a", 70, "solar", _ctx())

        action = svc.last_cover_action
        assert action["queue_outcome"] == "waited"
        # Strictly positive, not ">= the gap": this field measures THIS
        # dispatch's own wait, and the gap started ticking at the previous
        # transmit — so the remaining spacing it actually sleeps is always a
        # little less than the full gap.
        assert action["queue_wait_seconds"] > 0.0

    @pytest.mark.asyncio
    async def test_a_budget_expired_dispatch_says_so(self):
        hass = _svc_hass()
        queue = _queue(gap=10.0)
        svc = _svc(hass, command_queue=queue)
        holder = await queue.acquire("cover.other", head_of_line=False, budget=5.0)
        assert holder is not None

        with (
            _patch_caps(),
            patch.object(svc, "_get_current_position", return_value=10),
            patch(
                "custom_components.adaptive_cover_pro.managers.cover_command"
                ".COMMAND_QUEUE_MAX_WAIT_SECONDS",
                0.02,
            ),
        ):
            outcome, _ = await svc.apply_position("cover.a", 70, "solar", _ctx())

        # Transmitted anyway — the bounded-wait invariant.
        assert outcome == "sent"
        assert svc.last_cover_action["queue_outcome"] == "budget_expired"

    def test_entry_diagnostics_carry_the_queue_block(self):
        from custom_components.adaptive_cover_pro.diagnostics.builder import (
            DiagnosticsBuilder,
        )

        hass = _hass()
        queue = get_command_queue(hass, "facade south")
        queue.attach()
        queue.attach()
        queue.set_gap(7.5)

        block = DiagnosticsBuilder.build_command_queue_block(queue)
        assert block == {
            "name": "facade south",
            "gap_seconds": 7.5,
            "gap_source": "entry",
            "attached_members": 2,
            "current_depth": 0,
        }

    def test_entry_diagnostics_report_a_defaulted_gap_as_such(self):
        from custom_components.adaptive_cover_pro.diagnostics.builder import (
            DiagnosticsBuilder,
        )

        hass = _hass()
        queue = get_command_queue(hass, "facade south")
        queue.attach()
        block = DiagnosticsBuilder.build_command_queue_block(queue)
        assert block["gap_source"] == "default"
        assert block["gap_seconds"] == DEFAULT_COMMAND_QUEUE_GAP

    def test_entry_diagnostics_omit_the_block_when_unqueued(self):
        from custom_components.adaptive_cover_pro.diagnostics.builder import (
            DiagnosticsBuilder,
        )

        assert DiagnosticsBuilder.build_command_queue_block(None) is None
