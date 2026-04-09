"""Integration tests for coordinator orchestration methods.

Tests coordinator-level methods that wire together the pipeline, managers,
and CoverCommandService.  Uses the mock-coordinator pattern: a minimal mock
is built with the required attributes, and the unbound coordinator method is
called on it.

Covers:
- async_handle_state_change: solar vs default, safety bypass (force=True)
- async_handle_first_refresh: startup commands sent for all entities
- async_handle_cover_state_change: manual override detection, grace period skip
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pipeline_result(
    *,
    position: int = 50,
    control_method: ControlMethod = ControlMethod.SOLAR,
    bypass_auto_control: bool = False,
) -> PipelineResult:
    return PipelineResult(
        position=position,
        control_method=control_method,
        reason="test",
        bypass_auto_control=bypass_auto_control,
    )


def _make_coordinator(
    *,
    entities: list[str] | None = None,
    automatic_control: bool = True,
    pipeline_result: PipelineResult | None = None,
    manual_toggle: bool = True,
    in_startup_grace_period: bool = False,
    state_change_data_entity: str = "cover.test",
    state_change_data_position: int = 50,
):
    """Build a minimal mock coordinator for state-change handler tests."""
    coordinator = MagicMock()
    coordinator.entities = entities if entities is not None else ["cover.test"]
    coordinator.automatic_control = automatic_control
    coordinator.manual_toggle = manual_toggle
    coordinator.logger = MagicMock()
    coordinator.state_change = True
    coordinator.cover_state_change = True

    if pipeline_result is None:
        pipeline_result = _make_pipeline_result()
    coordinator._pipeline_result = pipeline_result
    coordinator._pipeline_bypasses_auto_control = pipeline_result.bypass_auto_control

    coordinator._check_sun_validity_transition = MagicMock(return_value=False)
    coordinator._build_position_context = MagicMock(return_value=MagicMock())
    coordinator._cmd_svc = MagicMock()
    coordinator._cmd_svc.apply_position = AsyncMock(
        return_value=("sent", "set_cover_position")
    )

    coordinator._is_in_startup_grace_period = MagicMock(
        return_value=in_startup_grace_period
    )

    state_data = MagicMock()
    state_data.entity_id = state_change_data_entity
    coordinator.state_change_data = state_data
    coordinator._pending_cover_events = [state_data]
    coordinator._target_just_reached = set()
    coordinator.target_call = {}
    coordinator._cover_type = "cover_blind"
    coordinator.manual_reset = False
    coordinator.manual_threshold = None
    coordinator.wait_for_target = {}
    coordinator.manager = MagicMock()

    return coordinator


# ---------------------------------------------------------------------------
# Step 1: Full update cycle with solar tracking
# ---------------------------------------------------------------------------


class TestStateChangeWithSolarTracking:
    """async_handle_state_change calls apply_position with solar position."""

    @pytest.mark.asyncio
    async def test_solar_position_sent_to_all_entities(self):
        """When pipeline returns SOLAR, apply_position is called for each entity."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        result = _make_pipeline_result(position=65, control_method=ControlMethod.SOLAR)
        coordinator = _make_coordinator(
            entities=["cover.blind_1", "cover.blind_2"],
            pipeline_result=result,
        )

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator, state=65, options={}
        )

        assert coordinator._cmd_svc.apply_position.call_count == 2
        called_entities = [
            call.args[0]
            for call in coordinator._cmd_svc.apply_position.call_args_list
        ]
        assert "cover.blind_1" in called_entities
        assert "cover.blind_2" in called_entities

    @pytest.mark.asyncio
    async def test_solar_uses_non_force_context(self):
        """Solar handler result does NOT use force=True in position context."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        result = _make_pipeline_result(
            position=55, control_method=ControlMethod.SOLAR, bypass_auto_control=False
        )
        coordinator = _make_coordinator(
            entities=["cover.test"],
            pipeline_result=result,
        )

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator, state=55, options={}
        )

        # _build_position_context must be called WITHOUT force=True
        coordinator._build_position_context.assert_called_once_with(
            "cover.test",
            {},
            force=False,
            sun_just_appeared=coordinator._check_sun_validity_transition.return_value,
        )

    @pytest.mark.asyncio
    async def test_state_change_flag_cleared_after_handling(self):
        """state_change flag must be cleared after async_handle_state_change."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = _make_coordinator()
        coordinator.state_change = True

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator, state=50, options={}
        )

        assert coordinator.state_change is False


# ---------------------------------------------------------------------------
# Step 2: Full update cycle with sun outside FOV
# ---------------------------------------------------------------------------


class TestStateChangeWithDefaultPosition:
    """When pipeline returns DEFAULT, apply_position is called with default position."""

    @pytest.mark.asyncio
    async def test_default_position_sent(self):
        """DEFAULT control method sends the pipeline position (default h_def)."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        result = _make_pipeline_result(
            position=30, control_method=ControlMethod.DEFAULT
        )
        coordinator = _make_coordinator(
            entities=["cover.blind"],
            pipeline_result=result,
        )

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator, state=30, options={}
        )

        coordinator._cmd_svc.apply_position.assert_called_once_with(
            "cover.blind",
            30,
            "solar",  # reason is always "solar" for non-safety handlers
            context=coordinator._build_position_context.return_value,
        )

    @pytest.mark.asyncio
    async def test_default_also_uses_non_force_context(self):
        """DEFAULT handler result also does NOT use force=True."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        result = _make_pipeline_result(
            position=0, control_method=ControlMethod.DEFAULT, bypass_auto_control=False
        )
        coordinator = _make_coordinator(
            entities=["cover.blind"],
            pipeline_result=result,
        )

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator, state=0, options={}
        )

        coordinator._build_position_context.assert_called_once_with(
            "cover.blind",
            {},
            force=False,
            sun_just_appeared=coordinator._check_sun_validity_transition.return_value,
        )


# ---------------------------------------------------------------------------
# Step 6: First refresh sends startup commands
# ---------------------------------------------------------------------------


class TestFirstRefreshSendsStartupCommands:
    """async_handle_first_refresh sends startup commands to all entities."""

    @pytest.mark.asyncio
    async def test_startup_commands_sent_to_all_entities(self):
        """All configured cover entities receive apply_position on first refresh."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = _make_coordinator(
            entities=["cover.blind_a", "cover.blind_b", "cover.blind_c"],
        )
        coordinator.first_refresh = True

        await AdaptiveDataUpdateCoordinator.async_handle_first_refresh(
            coordinator, state=50, options={}
        )

        assert coordinator._cmd_svc.apply_position.call_count == 3
        called_entities = {
            call.args[0]
            for call in coordinator._cmd_svc.apply_position.call_args_list
        }
        assert called_entities == {"cover.blind_a", "cover.blind_b", "cover.blind_c"}

    @pytest.mark.asyncio
    async def test_startup_uses_startup_reason(self):
        """apply_position is called with reason='startup' on first refresh."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = _make_coordinator(entities=["cover.test"])
        coordinator.first_refresh = True

        await AdaptiveDataUpdateCoordinator.async_handle_first_refresh(
            coordinator, state=40, options={}
        )

        call = coordinator._cmd_svc.apply_position.call_args
        assert call.args[2] == "startup"

    @pytest.mark.asyncio
    async def test_first_refresh_flag_cleared(self):
        """first_refresh flag must be cleared after async_handle_first_refresh."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = _make_coordinator(entities=["cover.test"])
        coordinator.first_refresh = True

        await AdaptiveDataUpdateCoordinator.async_handle_first_refresh(
            coordinator, state=50, options={}
        )

        assert coordinator.first_refresh is False

    @pytest.mark.asyncio
    async def test_startup_commands_with_empty_entity_list(self):
        """No apply_position calls when no entities are configured."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = _make_coordinator(entities=[])
        coordinator.first_refresh = True

        await AdaptiveDataUpdateCoordinator.async_handle_first_refresh(
            coordinator, state=50, options={}
        )

        coordinator._cmd_svc.apply_position.assert_not_called()
        assert coordinator.first_refresh is False


# ---------------------------------------------------------------------------
# Step 7: State change with safety handler bypasses gates
# ---------------------------------------------------------------------------


class TestStateChangeWithSafetyHandlerBypass:
    """Safety handlers (force/weather) pass force=True to position context."""

    @pytest.mark.asyncio
    async def test_force_override_uses_force_true_context(self):
        """ForceOverrideHandler result triggers force=True in position context."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        result = _make_pipeline_result(
            position=75,
            control_method=ControlMethod.FORCE,
            bypass_auto_control=True,
        )
        coordinator = _make_coordinator(
            entities=["cover.test"],
            pipeline_result=result,
        )

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator, state=75, options={"test": True}
        )

        coordinator._build_position_context.assert_called_once_with(
            "cover.test",
            {"test": True},
            force=True,  # ← safety bypass
            sun_just_appeared=coordinator._check_sun_validity_transition.return_value,
        )

    @pytest.mark.asyncio
    async def test_safety_handler_uses_control_method_as_reason(self):
        """Safety handlers use the control_method value as the reason string."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        result = _make_pipeline_result(
            position=0,
            control_method=ControlMethod.WEATHER,
            bypass_auto_control=True,
        )
        coordinator = _make_coordinator(
            entities=["cover.test"],
            pipeline_result=result,
        )

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator, state=0, options={}
        )

        call = coordinator._cmd_svc.apply_position.call_args
        reason = call.args[2]
        assert reason == ControlMethod.WEATHER.value

    @pytest.mark.asyncio
    async def test_non_safety_handler_uses_solar_as_reason(self):
        """Non-safety handlers (solar, default, manual) use 'solar' as reason string."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        result = _make_pipeline_result(
            position=50,
            control_method=ControlMethod.SOLAR,
            bypass_auto_control=False,
        )
        coordinator = _make_coordinator(
            entities=["cover.test"],
            pipeline_result=result,
        )

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator, state=50, options={}
        )

        call = coordinator._cmd_svc.apply_position.call_args
        reason = call.args[2]
        assert reason == "solar"


# ---------------------------------------------------------------------------
# Step 8: Cover state change triggers manual override detection
# ---------------------------------------------------------------------------


class TestCoverStateChangeTriggerManualOverride:
    """async_handle_cover_state_change triggers manual override detection."""

    @pytest.mark.asyncio
    async def test_manual_override_detection_called_when_conditions_met(self):
        """handle_state_change is called when manual_toggle and auto_control are on."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = _make_coordinator(
            manual_toggle=True,
            in_startup_grace_period=False,
        )
        coordinator.automatic_control = True
        coordinator._target_just_reached = set()
        coordinator.cover_state_change = True

        await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(
            coordinator, state=50
        )

        coordinator.manager.handle_state_change.assert_called_once()

    @pytest.mark.asyncio
    async def test_manual_override_detection_skipped_when_manual_toggle_off(self):
        """handle_state_change NOT called when manual_toggle is False."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = _make_coordinator(
            manual_toggle=False,
            in_startup_grace_period=False,
        )
        coordinator.automatic_control = True
        coordinator.cover_state_change = True

        await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(
            coordinator, state=50
        )

        coordinator.manager.handle_state_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_manual_override_detection_skipped_when_auto_control_off(self):
        """handle_state_change NOT called when automatic_control is False."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = _make_coordinator(
            manual_toggle=True,
            in_startup_grace_period=False,
        )
        coordinator.automatic_control = False
        coordinator.cover_state_change = True

        await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(
            coordinator, state=50
        )

        coordinator.manager.handle_state_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_cover_state_change_flag_cleared(self):
        """cover_state_change flag is cleared regardless of detection result."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = _make_coordinator(manual_toggle=False)
        coordinator.automatic_control = True
        coordinator.cover_state_change = True

        await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(
            coordinator, state=50
        )

        assert coordinator.cover_state_change is False


# ---------------------------------------------------------------------------
# Step 9: Cover state change during startup grace period is ignored
# ---------------------------------------------------------------------------


class TestCoverStateChangeDuringGracePeriod:
    """Position changes during startup grace period do not trigger override detection."""

    @pytest.mark.asyncio
    async def test_grace_period_skips_manual_override_detection(self):
        """Grace period returns early — handle_state_change not called."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = _make_coordinator(
            manual_toggle=True,
            in_startup_grace_period=True,  # ← in grace period
        )
        coordinator.automatic_control = True
        coordinator.cover_state_change = True

        await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(
            coordinator, state=50
        )

        coordinator.manager.handle_state_change.assert_not_called()
        assert coordinator.cover_state_change is False

    @pytest.mark.asyncio
    async def test_grace_period_logs_debug_message(self):
        """A debug message is logged when cover position change is ignored in grace period."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = _make_coordinator(
            manual_toggle=True,
            in_startup_grace_period=True,
        )
        coordinator.automatic_control = True
        coordinator.cover_state_change = True

        await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(
            coordinator, state=50
        )

        coordinator.logger.debug.assert_called()
        log_args = [call[0][0] for call in coordinator.logger.debug.call_args_list]
        assert any("grace period" in msg.lower() for msg in log_args)


# ---------------------------------------------------------------------------
# Step 10: Target-just-reached skips manual override (complementary tests)
# ---------------------------------------------------------------------------


class TestTargetJustReachedSkipsManualOverride:
    """_target_just_reached prevents false manual override on automation settle."""

    @pytest.mark.asyncio
    async def test_target_just_reached_entity_removed_from_set(self):
        """The entity is removed from _target_just_reached after being processed."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        entity_id = "cover.venetian"
        coordinator = _make_coordinator(
            manual_toggle=True,
            state_change_data_entity=entity_id,
        )
        coordinator.automatic_control = True
        coordinator._target_just_reached = {entity_id}
        coordinator.cover_state_change = True

        await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(
            coordinator, state=50
        )

        # Entity must be consumed — not in set anymore
        assert entity_id not in coordinator._target_just_reached
        # And manual override detection must have been skipped
        coordinator.manager.handle_state_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_other_entities_not_affected_by_target_just_reached(self):
        """_target_just_reached only skips the specific entity, not others."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        # State change event is for "cover.other" (NOT in _target_just_reached)
        coordinator = _make_coordinator(
            manual_toggle=True,
            state_change_data_entity="cover.other",
        )
        coordinator.automatic_control = True
        coordinator._target_just_reached = {"cover.different"}  # different entity
        coordinator.cover_state_change = True

        await AdaptiveDataUpdateCoordinator.async_handle_cover_state_change(
            coordinator, state=50
        )

        # "cover.other" is NOT in _target_just_reached → detection runs normally
        coordinator.manager.handle_state_change.assert_called_once()
