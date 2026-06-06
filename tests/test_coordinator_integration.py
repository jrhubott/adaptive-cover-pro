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

from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pipeline_result(
    *,
    position: int = 50,
    control_method: ControlMethod = ControlMethod.SOLAR,
    bypass_auto_control: bool = False,
    floor_clamp_applied: bool = False,
) -> PipelineResult:
    return PipelineResult(
        position=position,
        control_method=control_method,
        reason="test",
        bypass_auto_control=bypass_auto_control,
        floor_clamp_applied=floor_clamp_applied,
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
    coordinator.manual_ignore_external = False
    coordinator.logger = MagicMock()
    coordinator.state_change = True
    coordinator.cover_state_change = True

    if pipeline_result is None:
        pipeline_result = _make_pipeline_result()
    coordinator._pipeline_result = pipeline_result
    coordinator._pipeline_bypasses_auto_control = pipeline_result.bypass_auto_control
    coordinator._pipeline_is_safety_handler = pipeline_result.control_method in (
        ControlMethod.FORCE,
        ControlMethod.WEATHER,
    )

    coordinator._check_sun_validity_transition = MagicMock(return_value=False)
    coordinator._is_custom_position_sensor_trigger = MagicMock(return_value=False)
    coordinator._build_position_context = MagicMock(return_value=MagicMock())
    coordinator._cmd_svc = MagicMock()
    coordinator._cmd_svc.apply_position = AsyncMock(
        return_value=("sent", "set_cover_position")
    )

    # _dispatch_to_cover wraps apply_position; mock it to delegate so tests that
    # assert on apply_position continue to work unchanged.
    async def _dispatch_side_effect(cover, state, reason, ctx):
        return await coordinator._cmd_svc.apply_position(
            cover, state, reason, context=ctx
        )

    coordinator._dispatch_to_cover = AsyncMock(side_effect=_dispatch_side_effect)
    # Default: cold-start (not a reload).  Tests that want reload behaviour
    # must set coordinator._is_reload = True explicitly.
    coordinator._is_reload = False

    coordinator._is_in_startup_grace_period = MagicMock(
        return_value=in_startup_grace_period
    )

    state_data = MagicMock()
    state_data.entity_id = state_change_data_entity
    coordinator.state_change_data = state_data
    coordinator._pending_cover_events = [state_data]
    coordinator._target_just_reached = set()
    coordinator._cover_type = "cover_blind"
    coordinator.manual_reset = False
    coordinator.manual_threshold = None
    coordinator.manager = MagicMock()
    coordinator.manager.is_cover_manual.return_value = False

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
            call.args[0] for call in coordinator._cmd_svc.apply_position.call_args_list
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

        # _build_position_context must be called WITHOUT force=True or is_safety
        coordinator._build_position_context.assert_called_once_with(
            "cover.test",
            {},
            force=False,
            is_safety=False,
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
            is_safety=False,
            sun_just_appeared=coordinator._check_sun_validity_transition.return_value,
        )


# ---------------------------------------------------------------------------
# Issue #290: Custom position must NOT use force=True context
# ---------------------------------------------------------------------------


class TestCustomPositionNoRedundantCommands:
    """Custom position handler must not trigger force=True in position context.

    bypass_auto_control=True on a CustomPositionHandler result exists only to
    defeat the auto_control_off gate.  It must NOT cascade to force=True, which
    would bypass the same-position short-circuit and resend set_cover_position
    on every sun.sun update (every few seconds), causing audible relay clicks.
    """

    @pytest.mark.asyncio
    async def test_custom_position_does_not_use_force_context(self):
        """Custom position pipeline result must call _build_position_context with force=False."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        result = _make_pipeline_result(
            position=60,
            control_method=ControlMethod.CUSTOM_POSITION,
            bypass_auto_control=True,
        )
        coordinator = _make_coordinator(
            entities=["cover.blind"],
            pipeline_result=result,
        )

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator, state=60, options={}
        )

        coordinator._build_position_context.assert_called_once_with(
            "cover.blind",
            {},
            force=False,
            is_safety=False,
            sun_just_appeared=coordinator._check_sun_validity_transition.return_value,
        )


# ---------------------------------------------------------------------------
# Step 5: Custom-position sensor edge-trigger bypasses time-delta gate
# ---------------------------------------------------------------------------


class TestCustomPositionSensorEdgeTriggerBypassesGate:
    """Sensor toggle triggers force=True; solar refresh keeps force=False.

    Issue #348: a custom-position sensor toggling within delta_time of the last
    cover move must NOT be throttled.  The same-position short-circuit (PR #300)
    still prevents redundant re-sends when the sensor stays active across solar
    refreshes.
    """

    @pytest.mark.asyncio
    async def test_custom_position_sensor_edge_trigger_uses_force_context(self):
        """Sensor-triggered custom position must call _build_position_context with force=True."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        result = _make_pipeline_result(
            position=60,
            control_method=ControlMethod.CUSTOM_POSITION,
            bypass_auto_control=True,
        )
        coordinator = _make_coordinator(
            entities=["cover.blind"],
            pipeline_result=result,
        )
        coordinator._is_custom_position_sensor_trigger = MagicMock(return_value=True)

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator, state=60, options={}
        )

        coordinator._build_position_context.assert_called_once_with(
            "cover.blind",
            {},
            force=True,
            is_safety=False,
            sun_just_appeared=coordinator._check_sun_validity_transition.return_value,
        )

    @pytest.mark.asyncio
    async def test_custom_position_sun_driven_refresh_does_not_use_force_context(self):
        """Solar-cycle refresh with active custom position keeps force=False (PR #300 invariant)."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        result = _make_pipeline_result(
            position=60,
            control_method=ControlMethod.CUSTOM_POSITION,
            bypass_auto_control=True,
        )
        coordinator = _make_coordinator(
            entities=["cover.blind"],
            pipeline_result=result,
        )
        coordinator._is_custom_position_sensor_trigger = MagicMock(return_value=False)

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator, state=60, options={}
        )

        coordinator._build_position_context.assert_called_once_with(
            "cover.blind",
            {},
            force=False,
            is_safety=False,
            sun_just_appeared=coordinator._check_sun_validity_transition.return_value,
        )

    @pytest.mark.asyncio
    async def test_trigger_outside_window_pre_start_no_command(self):
        """Custom-position sensor trigger outside time window (before start_time) must NOT send a command.

        Issue #383: custom_position_sensor_triggered must not bypass the time-window gate.
        A sensor toggle before the user's start_time should be suppressed — the same
        rule that applies to solar / climate / default handlers.
        """
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        result = _make_pipeline_result(
            position=60,
            control_method=ControlMethod.CUSTOM_POSITION,
            bypass_auto_control=True,
        )
        coordinator = _make_coordinator(
            entities=["cover.blind"],
            pipeline_result=result,
        )
        coordinator.check_adaptive_time = (
            False  # outside time window — before start_time
        )
        coordinator._is_custom_position_sensor_trigger = MagicMock(return_value=True)

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator, state=60, options={}
        )

        coordinator._cmd_svc.apply_position.assert_not_called()


class TestFloorClampUnderManualOverride:
    """A floor-clamp under an armed manual override must dispatch and not snap to default (#534)."""

    @pytest.mark.asyncio
    async def test_floor_clamp_forces_dispatch_under_manual_override(self):
        """A floor-clamped position under manual override calls context with force=True."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        result = _make_pipeline_result(
            position=80,
            control_method=ControlMethod.MANUAL,
            floor_clamp_applied=True,
        )
        coordinator = _make_coordinator(
            entities=["cover.blind"],
            pipeline_result=result,
        )
        coordinator.manager.is_cover_manual.return_value = True

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator, state=80, options={}
        )

        coordinator._build_position_context.assert_called_once_with(
            "cover.blind",
            {},
            force=True,
            is_safety=False,
            sun_just_appeared=coordinator._check_sun_validity_transition.return_value,
        )

    @pytest.mark.asyncio
    async def test_floor_release_under_armed_override_does_not_force_to_default(self):
        """Floor sensor off while override armed: stay at floor, no force to default (#534).

        When the floor sensor releases and manual override is still the winner,
        the manual-hold winner re-emits its theoretical default (90).  The
        coordinator must NOT take the custom_position_released force path —
        otherwise it would drive the cover to that default.  force stays False.
        """
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        result = _make_pipeline_result(
            position=90,
            control_method=ControlMethod.MANUAL,
            floor_clamp_applied=False,
        )
        coordinator = _make_coordinator(
            entities=["cover.blind"],
            pipeline_result=result,
        )
        coordinator.manager.is_cover_manual.return_value = True
        coordinator._last_state_change_entity = "binary_sensor.cp1"

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator,
            state=90,
            options={},
            custom_position_released_entities={"binary_sensor.cp1"},
        )

        coordinator._build_position_context.assert_called_once_with(
            "cover.blind",
            {},
            force=False,
            is_safety=False,
            sun_just_appeared=coordinator._check_sun_validity_transition.return_value,
        )


class TestCustomPositionSensorReleaseEdgeBypassesGate:
    """Custom-position sensor release-edge mirrors force-override release (#365).

    When a custom-position sensor toggles off, the CustomPositionHandler returns
    None and a lower-priority handler (SOLAR / DEFAULT) wins, so the pipeline's
    control_method is no longer CUSTOM_POSITION.  Without explicit release
    handling, force=True is never threaded, and CoverCommandService drops the
    return-to-solar command on the time-delta gate.

    The fix mirrors the existing _prev_force_override_active pattern: the
    coordinator captures last cycle's per-sensor active state and computes a
    set of sensors that flipped from on to off this cycle.  When the released
    sensor IS the entity that triggered this refresh, force=True is passed.
    """

    def _make_release_coordinator(
        self,
        *,
        last_state_change_entity: str = "binary_sensor.movie_time",
    ):
        """Coordinator where a custom-position sensor just transitioned on → off.

        Pipeline result is SOLAR because the sensor is off this cycle —
        CustomPositionHandler returned None at custom_position.py:92.
        """
        result = _make_pipeline_result(
            position=55,
            control_method=ControlMethod.SOLAR,
            bypass_auto_control=False,
        )
        coordinator = _make_coordinator(entities=["cover.test"], pipeline_result=result)
        coordinator.check_adaptive_time = True
        coordinator.is_force_override_active = False
        coordinator._last_state_change_entity = last_state_change_entity
        return coordinator

    @pytest.mark.asyncio
    async def test_custom_position_release_passes_force_true(self):
        """When the triggering sensor flipped on → off, force=True bypasses gates."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = self._make_release_coordinator()

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator,
            state=55,
            options={},
            prev_force_override=False,
            custom_position_released_entities={"binary_sensor.movie_time"},
        )

        coordinator._build_position_context.assert_called_once_with(
            "cover.test",
            {},
            force=True,
            is_safety=False,
            sun_just_appeared=coordinator._check_sun_validity_transition.return_value,
        )

    @pytest.mark.asyncio
    async def test_custom_position_release_uses_released_reason(self):
        """Reason string on release is 'custom_position_released'."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = self._make_release_coordinator()

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator,
            state=55,
            options={},
            prev_force_override=False,
            custom_position_released_entities={"binary_sensor.movie_time"},
        )

        call = coordinator._cmd_svc.apply_position.call_args
        reason = call.args[2]
        assert reason == "custom_position_released"

    @pytest.mark.asyncio
    async def test_no_release_with_empty_released_set(self):
        """Empty released set: no release detected, force stays False."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = self._make_release_coordinator()

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator,
            state=55,
            options={},
            prev_force_override=False,
            custom_position_released_entities=set(),
        )

        coordinator._build_position_context.assert_called_once_with(
            "cover.test",
            {},
            force=False,
            is_safety=False,
            sun_just_appeared=coordinator._check_sun_validity_transition.return_value,
        )

    @pytest.mark.asyncio
    async def test_release_only_when_triggering_entity_matches(self):
        """A sensor in the released set but NOT the trigger entity does not force."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = self._make_release_coordinator(
            last_state_change_entity="cover.test"
        )

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator,
            state=55,
            options={},
            prev_force_override=False,
            custom_position_released_entities={"binary_sensor.movie_time"},
        )

        coordinator._build_position_context.assert_called_once_with(
            "cover.test",
            {},
            force=False,
            is_safety=False,
            sun_just_appeared=coordinator._check_sun_validity_transition.return_value,
        )

    @pytest.mark.asyncio
    async def test_release_outside_window_after_end_time_no_command(self):
        """Custom-position release outside time window (after end_time) must NOT send a command.

        Issue #383: custom_position_released must not bypass the time-window gate.
        The time-window gate applies uniformly outside the configured window — before
        start_time and after end_time alike. Only force-override and weather (safety
        handlers) are permitted to move covers outside the window. A sensor release
        outside the window leaves the cover at its current position; the sunset
        dispatch path (_check_sunset_window_transition) owns any sunset_pos movement.
        """
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = self._make_release_coordinator()
        coordinator.check_adaptive_time = False  # outside time window — after end_time

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator,
            state=55,
            options={},
            prev_force_override=False,
            custom_position_released_entities={"binary_sensor.movie_time"},
        )

        coordinator._cmd_svc.apply_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_release_outside_window_pre_start_no_command(self):
        """Custom-position release outside time window (before start_time) must NOT send a command.

        Issue #383: custom_position_released must not bypass the time-window gate.
        The release edge happens before the user's start_time, so no cover command
        should be issued — the same rule that applies to solar / climate / default.
        """
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = self._make_release_coordinator()
        coordinator.check_adaptive_time = (
            False  # outside time window — before start_time
        )
        coordinator._last_state_change_entity = "binary_sensor.movie_time"

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator,
            state=55,
            options={},
            prev_force_override=False,
            custom_position_released_entities={"binary_sensor.movie_time"},
        )

        coordinator._cmd_svc.apply_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_release_outside_window_sunset_active_no_command(self):
        """Custom-position release outside time window during sunset window must NOT send a command.

        Issue #383: the time-window gate applies even when the sunset window is active.
        _check_sunset_window_transition owns the sunset_pos dispatch — async_handle_state_change
        must not send any command when check_adaptive_time is False, regardless of whether
        the sunset window is currently open.
        """
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = self._make_release_coordinator()
        coordinator.check_adaptive_time = False  # outside time window — sunset active
        coordinator._last_state_change_entity = "binary_sensor.movie_time"

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator,
            state=40,
            options={},
            prev_force_override=False,
            custom_position_released_entities={"binary_sensor.movie_time"},
        )

        coordinator._cmd_svc.apply_position.assert_not_called()


class TestCustomPositionTriggerEntityRecording:
    """async_check_entity_state_change records the triggering entity_id."""

    @pytest.mark.asyncio
    async def test_async_check_entity_state_change_records_trigger_entity(self):
        """Trigger entity_id is stored as _last_state_change_entity for use in async_handle_state_change."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = MagicMock()
        coordinator.async_refresh = AsyncMock()
        coordinator.state_change = False
        coordinator._last_state_change_entity = None
        coordinator.logger = MagicMock()

        event = MagicMock()
        event.data = {
            "entity_id": "binary_sensor.movie_time",
            "old_state": MagicMock(state="off"),
            "new_state": MagicMock(state="on"),
        }

        await AdaptiveDataUpdateCoordinator.async_check_entity_state_change(
            coordinator, event
        )

        assert coordinator._last_state_change_entity == "binary_sensor.movie_time"


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
            call.args[0] for call in coordinator._cmd_svc.apply_position.call_args_list
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

    @pytest.mark.asyncio
    async def test_first_refresh_skips_unloaded_cover_entity(self):
        """Issue #342: first refresh must not call set_cover_position on unloaded covers.

        On HA restart, the cover platform may not finish loading before the
        integration's first refresh runs. The cover_unavailable gate in
        apply_position must short-circuit so HA never sees the service call
        (which would otherwise emit a "missing or not currently available"
        warning and, on platforms that queue commands, replay it later).
        """
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )
        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
        )

        coordinator = _make_coordinator(entities=["cover.unloaded"])
        coordinator.first_refresh = True

        real_hass = MagicMock()
        real_hass.states.get.return_value = None
        real_hass.services.async_call = AsyncMock()

        real_svc = CoverCommandService(
            hass=real_hass,
            logger=MagicMock(),
            cover_type="cover_blind",
            grace_mgr=MagicMock(),
            open_close_threshold=50,
        )
        coordinator._cmd_svc = real_svc

        async def _delegate(cover, state, reason, ctx):
            return await real_svc.apply_position(cover, state, reason, ctx)

        coordinator._dispatch_to_cover = AsyncMock(side_effect=_delegate)

        await AdaptiveDataUpdateCoordinator.async_handle_first_refresh(
            coordinator, state=100, options={}
        )

        real_hass.services.async_call.assert_not_called()
        assert real_svc.last_skipped_action["reason"] == "cover_unavailable"
        assert real_svc.last_skipped_action["entity_id"] == "cover.unloaded"

    @pytest.mark.asyncio
    async def test_reload_during_time_window_does_not_move_covers(self):
        """On config-entry reload, first-refresh must NOT move non-safety covers.

        Issue #187: reloading the integration during the day (inside the time
        window) triggered a move to default position (100%) because the
        first-refresh dispatch path was not distinguished from a cold HA boot.
        """
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        result = _make_pipeline_result(
            position=100,
            control_method=ControlMethod.DEFAULT,
            bypass_auto_control=False,
        )
        coordinator = _make_coordinator(
            entities=["cover.blind_1", "cover.blind_2"],
            pipeline_result=result,
        )
        coordinator.first_refresh = True
        coordinator._is_reload = True  # simulate daytime options-flow reload
        coordinator.check_adaptive_time = True  # inside time window

        await AdaptiveDataUpdateCoordinator.async_handle_first_refresh(
            coordinator, state=100, options={}
        )

        coordinator._cmd_svc.apply_position.assert_not_called()
        assert coordinator.first_refresh is False
        assert coordinator._is_reload is False

    @pytest.mark.asyncio
    async def test_reload_still_dispatches_on_active_safety_override(self):
        """Safety overrides (force/weather) must still fire on config-entry reload.

        Even when _is_reload=True, a force-override or weather handler that sets
        bypass_auto_control=True must move the cover immediately.
        """
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        result = _make_pipeline_result(
            position=0,
            control_method=ControlMethod.FORCE,
            bypass_auto_control=True,
        )
        coordinator = _make_coordinator(
            entities=["cover.blind_1"],
            pipeline_result=result,
        )
        coordinator.first_refresh = True
        coordinator._is_reload = True  # simulate reload
        coordinator.check_adaptive_time = True

        await AdaptiveDataUpdateCoordinator.async_handle_first_refresh(
            coordinator, state=0, options={}
        )

        coordinator._cmd_svc.apply_position.assert_called_once()
        assert coordinator.first_refresh is False

    @pytest.mark.asyncio
    async def test_cold_start_during_time_window_still_dispatches(self):
        """Cold HA boot inside the time window must still send startup commands.

        Regression guard: _is_reload=False (cold start) must preserve the
        existing first-refresh dispatch so covers track the sun from boot.
        """
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        result = _make_pipeline_result(
            position=65,
            control_method=ControlMethod.SOLAR,
            bypass_auto_control=False,
        )
        coordinator = _make_coordinator(
            entities=["cover.blind_1", "cover.blind_2"],
            pipeline_result=result,
        )
        coordinator.first_refresh = True
        coordinator._is_reload = False  # cold start
        coordinator.check_adaptive_time = True

        await AdaptiveDataUpdateCoordinator.async_handle_first_refresh(
            coordinator, state=65, options={}
        )

        assert coordinator._cmd_svc.apply_position.call_count == 2
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
            is_safety=True,  # ← safety target classification
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
# Step 7b: Force override release bypasses time/position delta gates (#177)
# ---------------------------------------------------------------------------


class TestForceOverrideRelease:
    """When force override releases, covers must return to calculated position
    immediately — the force override's own move must not count against the
    time delta threshold.  Regression tests for issue #177.
    """

    def _make_release_coordinator(
        self,
        *,
        is_force_override_active: bool = False,
        check_adaptive_time: bool = True,
    ):
        """Coordinator where force override just transitioned from on → off."""
        result = _make_pipeline_result(
            position=55,
            control_method=ControlMethod.SOLAR,
            bypass_auto_control=False,
        )
        coordinator = _make_coordinator(entities=["cover.test"], pipeline_result=result)
        coordinator.check_adaptive_time = check_adaptive_time
        coordinator.is_force_override_active = is_force_override_active
        return coordinator

    @pytest.mark.asyncio
    async def test_force_override_release_passes_force_true(self):
        """When force override just released, _build_position_context gets force=True.

        Previously the pipeline result had bypass_auto_control=False (solar won),
        so force=False was passed and the time delta gate could block the move.
        This test verifies the fix: prev_force_override=True causes force=True.
        """
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = self._make_release_coordinator(is_force_override_active=False)

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator,
            state=55,
            options={},
            prev_force_override=True,  # was active last cycle
        )

        coordinator._build_position_context.assert_called_once_with(
            "cover.test",
            {},
            force=True,  # ← must bypass time/position delta gates
            is_safety=False,  # ← force override release is NOT a safety target
            sun_just_appeared=coordinator._check_sun_validity_transition.return_value,
        )

    @pytest.mark.asyncio
    async def test_force_override_release_uses_cleared_reason(self):
        """Reason string must be 'force_override_cleared' on release."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = self._make_release_coordinator(is_force_override_active=False)

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator,
            state=55,
            options={},
            prev_force_override=True,
        )

        call = coordinator._cmd_svc.apply_position.call_args
        reason = call.args[2]
        assert reason == "force_override_cleared"

    @pytest.mark.asyncio
    async def test_no_release_without_prior_force_override(self):
        """When prev_force_override=False, normal solar tracking uses force=False."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = self._make_release_coordinator(is_force_override_active=False)

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator,
            state=55,
            options={},
            prev_force_override=False,  # no prior force override
        )

        coordinator._build_position_context.assert_called_once_with(
            "cover.test",
            {},
            force=False,  # ← normal solar tracking respects gates
            is_safety=False,
            sun_just_appeared=coordinator._check_sun_validity_transition.return_value,
        )

    @pytest.mark.asyncio
    async def test_force_override_still_active_uses_bypass_auto_control(self):
        """While force override is still active, bypass_auto_control drives force=True."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        result = _make_pipeline_result(
            position=0,
            control_method=ControlMethod.FORCE,
            bypass_auto_control=True,
        )
        coordinator = _make_coordinator(entities=["cover.test"], pipeline_result=result)
        coordinator.check_adaptive_time = True
        coordinator.is_force_override_active = True

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator,
            state=0,
            options={},
            prev_force_override=True,  # was also active last cycle — still on
        )

        coordinator._build_position_context.assert_called_once_with(
            "cover.test",
            {},
            force=True,  # ← safety bypass from bypass_auto_control
            is_safety=True,  # ← force override active = safety target
            sun_just_appeared=coordinator._check_sun_validity_transition.return_value,
        )

    @pytest.mark.asyncio
    async def test_force_override_release_outside_time_window_still_sends(self):
        """Force override release must move covers even outside the active time window.

        The time-window guard skips non-safety state changes, but a force override
        release is a special transition: the cover must return to its calculated
        position regardless of the time window.
        """
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = self._make_release_coordinator(
            is_force_override_active=False,
            check_adaptive_time=False,  # outside time window
        )

        await AdaptiveDataUpdateCoordinator.async_handle_state_change(
            coordinator,
            state=55,
            options={},
            prev_force_override=True,
        )

        # Must send even though we're outside the time window
        coordinator._cmd_svc.apply_position.assert_called_once()


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
    async def test_manual_override_detection_runs_when_auto_control_off(self):
        """Issue #293: handle_state_change IS called even when automatic_control=False.

        Observation is not action — recording manual overrides when the user
        toggles auto control off lets reconciliation back off and surfaces the
        user's intent in diagnostics.  Only manual_toggle=False short-circuits.
        """
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

        coordinator.manager.handle_state_change.assert_called_once()

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


# ---------------------------------------------------------------------------
# Step 8: Cover-online transition retriggers refresh (issue #342)
# ---------------------------------------------------------------------------


class TestCoverOnlineTransitionRetrigger:
    """When a tracked cover transitions from unavailable to a real state, the
    coordinator must run another refresh so the correct position is recomputed
    and dispatched (the original startup pass was skipped via cover_unavailable).
    """

    def _make_event(self, entity_id: str, new_state_value):
        event = MagicMock()
        new_state = (
            MagicMock(state=new_state_value) if new_state_value is not None else None
        )
        event.data = {
            "entity_id": entity_id,
            "old_state": None,
            "new_state": new_state,
        }
        return event

    @pytest.mark.asyncio
    async def test_cover_online_transition_triggers_refresh(self):
        """old_state=None + new_state has a real value → schedule a refresh."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = _make_coordinator(entities=["cover.late"])
        coordinator.async_request_refresh = AsyncMock()

        await AdaptiveDataUpdateCoordinator.async_check_cover_state_change(
            coordinator, self._make_event("cover.late", "open")
        )

        coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cover_online_to_unavailable_does_not_retrigger(self):
        """old_state=None + new_state.state=='unavailable' must NOT refresh.

        Cover platform registered the entity but it's still not reachable —
        another refresh now would just re-skip with cover_unavailable.
        """
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = _make_coordinator(entities=["cover.late"])
        coordinator.async_request_refresh = AsyncMock()

        await AdaptiveDataUpdateCoordinator.async_check_cover_state_change(
            coordinator, self._make_event("cover.late", "unavailable")
        )

        coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cover_online_to_unknown_does_not_retrigger(self):
        """old_state=None + new_state.state=='unknown' must NOT refresh."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coordinator = _make_coordinator(entities=["cover.late"])
        coordinator.async_request_refresh = AsyncMock()

        await AdaptiveDataUpdateCoordinator.async_check_cover_state_change(
            coordinator, self._make_event("cover.late", "unknown")
        )

        coordinator.async_request_refresh.assert_not_awaited()
