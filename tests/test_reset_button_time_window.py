"""Tests for issue #193: Reset Manual Override button must respect time window.

When the user presses Reset Manual Override outside the active-hours window,
the override flag must still be cleared but the cover must NOT be repositioned.
The next normal update cycle (when the window opens) sends the correct position.

These tests also verify that _async_send_after_override_clear is the single
shared gate for both the auto-expiry path and the button path.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(*, check_adaptive_time=True, automatic_control=True):
    """Minimal coordinator mock for testing _async_send_after_override_clear."""
    from custom_components.adaptive_cover_pro.managers.cover_command import (
        PositionContext,
    )

    coordinator = MagicMock()
    coordinator.check_adaptive_time = check_adaptive_time
    coordinator.automatic_control = automatic_control
    coordinator.entities = ["cover.test"]
    coordinator.logger = MagicMock()
    coordinator._check_sun_validity_transition.return_value = False
    coordinator._build_position_context.return_value = PositionContext(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=2,
        time_threshold=2,
        special_positions=[0, 100],
        inverse_state=False,
        force=True,
    )
    coordinator._cmd_svc.apply_position = AsyncMock(
        return_value=("sent", "set_cover_position")
    )
    return coordinator


def _make_button(coordinator, entities):
    """AdaptiveCoverButton without HA infrastructure."""
    from custom_components.adaptive_cover_pro.button import AdaptiveCoverButton

    button = AdaptiveCoverButton.__new__(AdaptiveCoverButton)
    button.coordinator = coordinator
    button._entities = entities
    return button


# ---------------------------------------------------------------------------
# _async_send_after_override_clear — direct method tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_after_override_clear_skips_when_outside_time_window():
    """apply_position must not be called when check_adaptive_time is False."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator(check_adaptive_time=False)

    result = await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
        coordinator, 75, {}
    )

    coordinator._cmd_svc.apply_position.assert_not_called()
    assert result == set()


@pytest.mark.asyncio
async def test_send_after_override_clear_skips_when_auto_control_off():
    """apply_position must not be called when automatic_control is False."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator(automatic_control=False)

    result = await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
        coordinator, 75, {}
    )

    coordinator._cmd_svc.apply_position.assert_not_called()
    assert result == set()


@pytest.mark.asyncio
async def test_send_after_override_clear_sends_to_specified_entities_only():
    """Entities kwarg must restrict the send to those covers, not self.entities."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator()
    coordinator.entities = ["cover.all_a", "cover.all_b"]

    result = await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
        coordinator, 60, {}, entities=["cover.only_this"]
    )

    assert coordinator._cmd_svc.apply_position.call_count == 1
    sent_entity = coordinator._cmd_svc.apply_position.call_args[0][0]
    assert sent_entity == "cover.only_this"
    assert result == {"cover.only_this"}


@pytest.mark.asyncio
async def test_send_after_override_clear_defaults_to_all_entities():
    """When entities kwarg is omitted, all self.entities must be targeted."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator()
    coordinator.entities = ["cover.a", "cover.b"]

    result = await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
        coordinator, 50, {}
    )

    assert coordinator._cmd_svc.apply_position.call_count == 2
    assert result == {"cover.a", "cover.b"}


@pytest.mark.asyncio
async def test_send_after_override_clear_uses_custom_trigger():
    """Trigger kwarg must be forwarded to apply_position as the reason string."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator()
    coordinator.entities = ["cover.test"]

    await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
        coordinator, 50, {}, trigger="manual_reset"
    )

    reason = coordinator._cmd_svc.apply_position.call_args[0][2]
    assert reason == "manual_reset"


@pytest.mark.asyncio
async def test_send_after_override_clear_default_trigger_is_manual_override_cleared():
    """Default trigger must remain 'manual_override_cleared' for backward compat."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator()
    coordinator.entities = ["cover.test"]

    await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
        coordinator, 50, {}
    )

    reason = coordinator._cmd_svc.apply_position.call_args[0][2]
    assert reason == "manual_override_cleared"


@pytest.mark.asyncio
async def test_send_after_override_clear_returns_only_sent_entities():
    """Entities whose apply_position returns anything other than 'sent' must be excluded."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = _make_coordinator()
    coordinator.entities = ["cover.capable", "cover.not_capable"]

    async def side_effect(entity, *args, **kwargs):
        if entity == "cover.capable":
            return ("sent", "set_cover_position")
        return ("skipped", "no_capable_service")

    coordinator._cmd_svc.apply_position = AsyncMock(side_effect=side_effect)

    result = await AdaptiveDataUpdateCoordinator._async_send_after_override_clear(
        coordinator, 50, {}
    )

    assert result == {"cover.capable"}


# ---------------------------------------------------------------------------
# Button path — issue #193 regression tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_button_skips_send_when_outside_time_window():
    """Pressing Reset outside the active-hours window must clear override but not move the cover.

    Regression test for issue #193: cover was moved to default (100%) even when
    Control Status showed outside_time_window.
    """
    entity_id = "cover.living_room"

    coordinator = _make_coordinator(check_adaptive_time=False)
    coordinator.manager.is_cover_manual.return_value = True
    coordinator.wait_for_target = {entity_id: False}
    coordinator.cover_state_change = False
    coordinator.state = 0
    coordinator.config_entry.options = {}
    coordinator.async_refresh = AsyncMock()
    # The shared method handles the gate; button must delegate to it
    coordinator._async_send_after_override_clear = AsyncMock(return_value=set())

    button = _make_button(coordinator, [entity_id])
    await button.async_press()

    # Override flag must be cleared regardless of time window
    coordinator.manager.reset.assert_called_once_with(entity_id)
    # No direct apply_position call from the button — it delegates
    coordinator._cmd_svc.apply_position.assert_not_called()
    # wait_for_target must be unblocked (entity not in sent set)
    assert coordinator.wait_for_target[entity_id] is False


@pytest.mark.asyncio
async def test_reset_button_skips_send_when_auto_control_off():
    """Pressing Reset with Automatic Control OFF must clear override but not move the cover."""
    entity_id = "cover.bedroom"

    coordinator = _make_coordinator(automatic_control=False)
    coordinator.manager.is_cover_manual.return_value = True
    coordinator.wait_for_target = {entity_id: False}
    coordinator.cover_state_change = False
    coordinator.state = 0
    coordinator.config_entry.options = {}
    coordinator.async_refresh = AsyncMock()
    coordinator._async_send_after_override_clear = AsyncMock(return_value=set())

    button = _make_button(coordinator, [entity_id])
    await button.async_press()

    coordinator.manager.reset.assert_called_once_with(entity_id)
    coordinator._cmd_svc.apply_position.assert_not_called()
    assert coordinator.wait_for_target[entity_id] is False


@pytest.mark.asyncio
async def test_reset_button_delegates_to_shared_method_with_correct_args():
    """Button must call _async_send_after_override_clear with entities and trigger kwargs."""
    entity_id = "cover.kitchen"
    options = {"some_opt": True}

    coordinator = _make_coordinator()
    coordinator.manager.is_cover_manual.return_value = True
    coordinator.wait_for_target = {entity_id: False}
    coordinator.cover_state_change = False
    coordinator.state = 55
    coordinator.config_entry.options = options
    coordinator.async_refresh = AsyncMock()
    coordinator._async_send_after_override_clear = AsyncMock(return_value={entity_id})

    button = _make_button(coordinator, [entity_id])
    await button.async_press()

    coordinator._async_send_after_override_clear.assert_called_once()
    call = coordinator._async_send_after_override_clear.call_args
    assert call[0][0] == 55  # state
    assert call[0][1] == options  # options
    assert call[1].get("entities") == [entity_id]
    assert call[1].get("trigger") == "manual_reset"


@pytest.mark.asyncio
async def test_reset_button_clears_wait_for_target_for_unsent_entities():
    """Entities the shared method did not send to must have wait_for_target cleared."""
    entity_a = "cover.sent"
    entity_b = "cover.not_sent"

    coordinator = _make_coordinator()
    coordinator.manager.is_cover_manual.return_value = True
    coordinator.wait_for_target = {entity_a: True, entity_b: True}
    coordinator.cover_state_change = False
    coordinator.state = 60
    coordinator.config_entry.options = {}
    coordinator.async_refresh = AsyncMock()
    # Simulate: shared method sent to entity_a but not entity_b
    coordinator._async_send_after_override_clear = AsyncMock(return_value={entity_a})

    button = _make_button(coordinator, [entity_a, entity_b])
    await button.async_press()

    # entity_a was sent — wait_for_target stays True (apply_position set it)
    assert coordinator.wait_for_target[entity_a] is True
    # entity_b was not sent — wait_for_target must be cleared
    assert coordinator.wait_for_target[entity_b] is False


@pytest.mark.asyncio
async def test_reset_button_happy_path_inside_window():
    """Inside the window with auto-control on, position must be sent normally."""
    entity_id = "cover.sun_room"

    coordinator = _make_coordinator(check_adaptive_time=True, automatic_control=True)
    coordinator.manager.is_cover_manual.return_value = True
    coordinator.wait_for_target = {entity_id: False}
    coordinator.cover_state_change = False
    coordinator.state = 42
    coordinator.config_entry.options = {}
    coordinator.async_refresh = AsyncMock()
    coordinator._async_send_after_override_clear = AsyncMock(return_value={entity_id})

    button = _make_button(coordinator, [entity_id])
    await button.async_press()

    coordinator.manager.reset.assert_called_once_with(entity_id)
    coordinator.async_refresh.assert_called_once()
    coordinator._async_send_after_override_clear.assert_called_once()
    # entity was sent — wait_for_target remains True
    assert coordinator.wait_for_target[entity_id] is True
