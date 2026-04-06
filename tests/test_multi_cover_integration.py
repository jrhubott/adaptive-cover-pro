"""Integration tests for multi-entity cover coordination.

Tests scenarios where the coordinator manages multiple cover entities and
verifies that the correct pipeline position, override state, and gate logic
are applied independently per entity.

Covers:
- Step 32: Different covers get same pipeline position
- Step 33: Manual override on one cover doesn't affect others
- Step 34: Grace period per-entity
- Step 35: Reconciliation targets per-entity
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
    PositionContext,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def hass():
    h = MagicMock()
    h.services.async_call = AsyncMock()
    return h


@pytest.fixture
def grace_mgr():
    return MagicMock()


def _make_svc(hass, grace_mgr) -> CoverCommandService:
    return CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=grace_mgr,
        open_close_threshold=50,
        check_interval_minutes=1,
        position_tolerance=3,
        max_retries=3,
    )


def _ctx(**overrides) -> PositionContext:
    """Return a PositionContext with all gates passing by default."""
    defaults = dict(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=2,
        time_threshold=0,
        special_positions=[0, 100, 50],
        inverse_state=False,
        force=False,
    )
    defaults.update(overrides)
    return PositionContext(**defaults)


def _patch_caps():
    return patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
        return_value={
            "has_set_position": True,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
        },
    )


def _patch_position(svc, positions: dict):
    """Patch _get_current_position with a dict mapping entity → position."""
    def _get(entity):
        return positions.get(entity, None)
    svc._get_current_position = MagicMock(side_effect=_get)


def _patch_time_no_throttle():
    """Patch get_last_updated to return None (bypasses time delta gate)."""
    return patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
        return_value=None,
    )


# ---------------------------------------------------------------------------
# Step 32: Different covers get same pipeline position
# ---------------------------------------------------------------------------


class TestAllCoversReceiveSamePipelinePosition:
    """All configured entities receive the same pipeline position each cycle."""

    @pytest.mark.asyncio
    async def test_two_covers_same_position_sent(self, hass, grace_mgr):
        """Both covers receive the same pipeline position (solar tracking result)."""
        svc = _make_svc(hass, grace_mgr)
        _patch_position(svc, {"cover.blind_1": 20, "cover.blind_2": 20})

        with _patch_caps(), _patch_time_no_throttle():
            for entity in ["cover.blind_1", "cover.blind_2"]:
                await svc.apply_position(entity, 65, "solar", context=_ctx())

        assert hass.services.async_call.call_count == 2
        assert svc.target_call["cover.blind_1"] == 65
        assert svc.target_call["cover.blind_2"] == 65

    @pytest.mark.asyncio
    async def test_three_covers_all_tracked_independently(self, hass, grace_mgr):
        """Three covers all tracked with independent target_call entries."""
        svc = _make_svc(hass, grace_mgr)
        entities = ["cover.a", "cover.b", "cover.c"]
        _patch_position(svc, {e: 30 for e in entities})

        with _patch_caps(), _patch_time_no_throttle():
            for entity in entities:
                await svc.apply_position(entity, 70, "solar", context=_ctx())

        assert len(svc.target_call) == 3
        for entity in entities:
            assert svc.target_call[entity] == 70

    @pytest.mark.asyncio
    async def test_all_covers_set_wait_for_target(self, hass, grace_mgr):
        """After a successful command, all entities have wait_for_target=True."""
        svc = _make_svc(hass, grace_mgr)
        entities = ["cover.left", "cover.right"]
        _patch_position(svc, {e: 10 for e in entities})

        with _patch_caps(), _patch_time_no_throttle():
            for entity in entities:
                await svc.apply_position(entity, 55, "solar", context=_ctx())

        for entity in entities:
            assert svc.wait_for_target[entity] is True


# ---------------------------------------------------------------------------
# Step 33: Manual override on one cover doesn't affect others
# ---------------------------------------------------------------------------


class TestManualOverridePerEntity:
    """Manual override on one entity does not prevent commands to other entities."""

    @pytest.mark.asyncio
    async def test_manual_override_only_skips_affected_entity(self, hass, grace_mgr):
        """Entity A in manual override is skipped; entity B still receives command."""
        svc = _make_svc(hass, grace_mgr)
        _patch_position(svc, {"cover.a": 30, "cover.b": 30})

        with _patch_caps(), _patch_time_no_throttle():
            # cover.a: manual override active → should be skipped
            outcome_a, reason_a = await svc.apply_position(
                "cover.a", 60, "solar", context=_ctx(manual_override=True)
            )
            # cover.b: no override → should be sent
            outcome_b, _ = await svc.apply_position(
                "cover.b", 60, "solar", context=_ctx(manual_override=False)
            )

        assert outcome_a == "skipped"
        assert reason_a == "manual_override"
        assert outcome_b == "sent"
        assert "cover.a" not in svc.target_call
        assert svc.target_call["cover.b"] == 60

    @pytest.mark.asyncio
    async def test_reconciliation_skips_manual_entity_only(self, hass, grace_mgr):
        """Reconciliation skips the manually-overridden entity but retries others."""
        svc = _make_svc(hass, grace_mgr)
        svc.target_call["cover.manual"] = 85
        svc.target_call["cover.auto"] = 70
        svc.wait_for_target["cover.manual"] = False
        svc.wait_for_target["cover.auto"] = False

        # Both off their targets
        _patch_position(svc, {"cover.manual": 50, "cover.auto": 50})
        svc.manual_override_entities = {"cover.manual"}

        with _patch_caps():
            await svc._reconcile(dt.datetime.now(dt.UTC))

        # Only cover.auto should have been retried
        assert hass.services.async_call.call_count == 1
        call_data = hass.services.async_call.call_args[0][2]
        assert call_data.get("entity_id") == "cover.auto"

    @pytest.mark.asyncio
    async def test_clearing_override_restores_commands(self, hass, grace_mgr):
        """Once manual override is cleared for entity A, it starts receiving commands again."""
        svc = _make_svc(hass, grace_mgr)
        _patch_position(svc, {"cover.a": 30})

        # First: skip due to manual override
        with _patch_caps(), _patch_time_no_throttle():
            outcome_skip, _ = await svc.apply_position(
                "cover.a", 60, "solar", context=_ctx(manual_override=True)
            )

        # Override cleared: should send
        with _patch_caps(), _patch_time_no_throttle():
            outcome_send, _ = await svc.apply_position(
                "cover.a", 60, "solar", context=_ctx(manual_override=False)
            )

        assert outcome_skip == "skipped"
        assert outcome_send == "sent"


# ---------------------------------------------------------------------------
# Step 34: Grace period per-entity
# ---------------------------------------------------------------------------


class TestGracePeriodPerEntity:
    """Grace period is tracked per-entity so one cover's grace doesn't block others."""

    @pytest.mark.asyncio
    async def test_grace_period_checked_per_entity(self, hass, grace_mgr):
        """Grace period manager is called per-entity, not globally.

        When entity A is in command grace period (move in progress), the grace
        manager signals that. Entity B can still receive commands independently.
        """
        svc = _make_svc(hass, grace_mgr)

        # Simulate: cover.a just received a command (in grace period)
        # cover.b has no grace period active
        called_entities = []

        original_start = grace_mgr.start_command_grace_period

        def track_grace(entity_id):
            called_entities.append(entity_id)

        grace_mgr.start_command_grace_period = MagicMock(side_effect=track_grace)

        _patch_position(svc, {"cover.a": 30, "cover.b": 30})

        with _patch_caps(), _patch_time_no_throttle():
            await svc.apply_position("cover.a", 60, "solar", context=_ctx())
            await svc.apply_position("cover.b", 60, "solar", context=_ctx())

        # Grace period is started for BOTH entities independently
        assert "cover.a" in called_entities
        assert "cover.b" in called_entities
        assert called_entities.count("cover.a") == 1
        assert called_entities.count("cover.b") == 1

    @pytest.mark.asyncio
    async def test_sent_at_tracked_per_entity(self, hass, grace_mgr):
        """_sent_at is recorded independently per entity after successful command."""
        svc = _make_svc(hass, grace_mgr)
        _patch_position(svc, {"cover.blind_1": 20, "cover.blind_2": 20})

        with _patch_caps(), _patch_time_no_throttle():
            await svc.apply_position("cover.blind_1", 50, "solar", context=_ctx())
            await svc.apply_position("cover.blind_2", 80, "solar", context=_ctx())

        assert "cover.blind_1" in svc._sent_at
        assert "cover.blind_2" in svc._sent_at
        # Timestamps are independent (could be same sub-second, but keys exist)
        assert svc._sent_at["cover.blind_1"] is not None
        assert svc._sent_at["cover.blind_2"] is not None


# ---------------------------------------------------------------------------
# Step 35: Reconciliation targets per-entity
# ---------------------------------------------------------------------------


class TestReconciliationTargetsPerEntity:
    """Reconciliation manages target tracking independently for each entity."""

    @pytest.mark.asyncio
    async def test_reconcile_retries_per_entity_independently(self, hass, grace_mgr):
        """Retry counts are tracked independently per entity."""
        svc = _make_svc(hass, grace_mgr)
        svc.target_call["cover.a"] = 60
        svc.target_call["cover.b"] = 70
        svc.wait_for_target["cover.a"] = False
        svc.wait_for_target["cover.b"] = False

        # cover.a: at target (delta=2 ≤ 3); cover.b: off target (delta=20 > 3)
        _patch_position(svc, {"cover.a": 58, "cover.b": 50})

        with _patch_caps():
            await svc._reconcile(dt.datetime.now(dt.UTC))

        # Only cover.b should have been retried
        assert svc._retry_counts.get("cover.a", 0) == 0
        assert svc._retry_counts.get("cover.b", 0) == 1

    @pytest.mark.asyncio
    async def test_target_call_updated_per_entity(self, hass, grace_mgr):
        """target_call records the last sent position per entity independently."""
        svc = _make_svc(hass, grace_mgr)
        _patch_position(svc, {"cover.north": 20, "cover.south": 20})

        with _patch_caps(), _patch_time_no_throttle():
            await svc.apply_position("cover.north", 40, "solar", context=_ctx())
            await svc.apply_position("cover.south", 80, "solar", context=_ctx())

        assert svc.target_call["cover.north"] == 40
        assert svc.target_call["cover.south"] == 80

    @pytest.mark.asyncio
    async def test_check_target_reached_per_entity(self, hass, grace_mgr):
        """check_target_reached is independent: clearing one doesn't affect the other."""
        svc = _make_svc(hass, grace_mgr)
        svc.target_call["cover.a"] = 50
        svc.target_call["cover.b"] = 70
        svc.wait_for_target["cover.a"] = True
        svc.wait_for_target["cover.b"] = True

        # cover.a reaches its target; cover.b does not
        reached_a = svc.check_target_reached("cover.a", 50)  # exact match
        reached_b = svc.check_target_reached("cover.b", 50)  # delta=20 > tolerance=3

        assert reached_a is True
        assert svc.wait_for_target["cover.a"] is False  # cleared

        assert reached_b is False
        assert svc.wait_for_target["cover.b"] is True  # still waiting

    @pytest.mark.asyncio
    async def test_max_retries_per_entity_independent(self, hass, grace_mgr):
        """When one entity hits max retries, others still get retried."""
        svc = _make_svc(hass, grace_mgr)
        # cover.a at max retries — should be skipped
        svc.target_call["cover.a"] = 60
        svc.target_call["cover.b"] = 70
        svc.wait_for_target["cover.a"] = False
        svc.wait_for_target["cover.b"] = False
        svc._retry_counts["cover.a"] = 3  # at max_retries=3

        # Both off target
        _patch_position(svc, {"cover.a": 40, "cover.b": 40})

        with _patch_caps():
            await svc._reconcile(dt.datetime.now(dt.UTC))

        # cover.b retried; cover.a skipped (at max)
        assert svc._retry_counts.get("cover.b", 0) == 1
        # cover.a retry count stays at 3 (not incremented further)
        assert svc._retry_counts["cover.a"] == 3
