"""Tests for AdaptiveCoverMyPositionButton (button platform, issue #409)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.managers.cover_command import PositionContext
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult
from custom_components.adaptive_cover_pro.const import ControlMethod

# ---------------------------------------------------------------------------
# Step 8 — My Position button created when entities configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_my_position_button_created_when_entities_configured():
    """async_setup_entry must yield exactly one AdaptiveCoverMyPositionButton."""
    from custom_components.adaptive_cover_pro.button import (
        AdaptiveCoverMyPositionButton,
        async_setup_entry,
    )
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENABLE_MY_POSITION_ENTITIES,
        CONF_ENTITIES,
        DOMAIN,
    )

    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.options = {
        CONF_ENTITIES: ["cover.test1"],
        CONF_ENABLE_MY_POSITION_ENTITIES: True,
    }
    config_entry.data = {"name": "Test Cover", "sensor_type": "cover_blind"}

    coordinator = MagicMock()
    hass.data = {DOMAIN: {"test_entry": coordinator}}

    added = []

    def capture(entities, **kwargs):
        added.extend(entities)

    await async_setup_entry(hass, config_entry, capture)

    my_pos_buttons = [e for e in added if isinstance(e, AdaptiveCoverMyPositionButton)]
    assert len(my_pos_buttons) == 1


# ---------------------------------------------------------------------------
# Step 9 — async_press calls async_apply_user_position for each entity
# ---------------------------------------------------------------------------


def _make_my_position_button(*, options=None, entities=None):
    """Return a minimal AdaptiveCoverMyPositionButton without HA infrastructure."""
    from custom_components.adaptive_cover_pro.button import (
        AdaptiveCoverMyPositionButton,
    )
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENTITIES,
        CONF_MY_POSITION_VALUE,
    )

    if entities is None:
        entities = ["cover.test1", "cover.test2"]
    if options is None:
        options = {CONF_MY_POSITION_VALUE: 55, CONF_ENTITIES: entities}

    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.options = options
    config_entry.data = {"name": "Test Cover", "sensor_type": "cover_blind"}

    coordinator = MagicMock()
    coordinator.async_apply_user_position = AsyncMock(
        return_value=("sent", "set_cover_position")
    )

    button = AdaptiveCoverMyPositionButton.__new__(AdaptiveCoverMyPositionButton)
    button.coordinator = coordinator
    button.config_entry = config_entry
    button._entities = entities
    return button


@pytest.mark.asyncio
async def test_press_calls_async_apply_user_position():
    """async_press must call async_apply_user_position for each entity."""
    button = _make_my_position_button()

    await button.async_press()

    assert button.coordinator.async_apply_user_position.call_count == 2
    for call in button.coordinator.async_apply_user_position.call_args_list:
        assert call.args[1] == 55
        assert call.kwargs.get("trigger") == "my_position_recall"
        assert call.kwargs.get("force") is False


# ---------------------------------------------------------------------------
# Step 10 — Warn-and-skip when my_position_value not configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_press_warn_and_skip_when_my_position_not_configured():
    """async_press must skip all covers when my_position_value is not set."""
    from custom_components.adaptive_cover_pro.const import CONF_ENTITIES

    options = {CONF_ENTITIES: ["cover.test1", "cover.test2"]}
    button = _make_my_position_button(
        options=options, entities=["cover.test1", "cover.test2"]
    )

    await button.async_press()

    button.coordinator.async_apply_user_position.assert_not_called()


# ---------------------------------------------------------------------------
# Step 11 — Preempted-skip return does not raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_press_records_preempted_skip_when_force_override_active():
    """async_press must not raise when coordinator returns preempted_by_force_override."""
    button = _make_my_position_button()
    button.coordinator.async_apply_user_position = AsyncMock(
        return_value=("skipped", "preempted_by_force_override")
    )

    # Must not raise
    await button.async_press()


# ---------------------------------------------------------------------------
# Issue #430 regression tests — My Position button must bypass auto_control gate
# ---------------------------------------------------------------------------


def _make_my_position_coord():
    """Return a coordinator-shaped object with a real _build_position_context.

    Uses the same _make_coord pattern as test_coordinator_apply_user_position.py
    but spies on _build_position_context to inspect which kwargs land there.
    """
    from custom_components.adaptive_cover_pro.pipeline.types import DecisionStep

    coord = MagicMock(spec=AdaptiveDataUpdateCoordinator)
    coord.config_entry = MagicMock()
    coord.config_entry.options = {}
    # After fix #643, async_apply_user_position falls back to _resolved_options.
    coord._resolved_options = {}
    coord._snapshot_builder = MagicMock()
    coord._snapshot_builder.read_custom_position_sensors.return_value = []
    # Floor composition reads from a real PipelineSnapshot (#463).
    from tests.test_pipeline.conftest import make_snapshot  # noqa: PLC0415

    coord._snapshot_builder.build = MagicMock(return_value=make_snapshot())
    coord._cover_data = MagicMock()
    coord._cover_type = "cover_blind"
    coord._weather_readings = None
    coord._cloud_mgr = MagicMock()

    pipeline_result = PipelineResult(
        position=50,
        control_method=ControlMethod.SOLAR,
        reason="solar",
        decision_trace=[
            DecisionStep(handler="solar", matched=True, reason="solar", position=50)
        ],
    )
    coord._pipeline = MagicMock()
    coord._pipeline.evaluate.return_value = pipeline_result

    solar_handler = MagicMock()
    solar_handler.priority = 40
    coord._handler_by_name = {"solar": solar_handler}

    # _cmd_svc: apply_position returns "skipped"/"auto_control_off" unless
    # bypass_auto_control is True on the context.  We use a real PositionContext
    # to drive the gate, inspecting the context the coordinator passes.
    captured_contexts: list[PositionContext] = []

    async def _fake_apply(entity_id, position, trigger, ctx):
        captured_contexts.append(ctx)
        if not ctx.is_safety and not ctx.bypass_auto_control and not ctx.auto_control:
            return ("skipped", "auto_control_off")
        return ("sent", "set_cover_position")

    cmd_svc = MagicMock()
    cmd_svc.apply_position = _fake_apply
    cmd_svc.record_preempted_skip = MagicMock()
    coord._cmd_svc = cmd_svc
    coord._captured_contexts = captured_contexts

    # Real _build_position_context that passes bypass_auto_control into PositionContext.
    coord.automatic_control = False
    coord._pipeline_bypasses_auto_control = False
    coord._pipeline_result = PipelineResult(
        position=50, control_method=ControlMethod.SOLAR, reason="solar"
    )
    coord._inverse_state = False
    coord.min_change = 2
    coord.time_threshold = 0
    coord._policy = MagicMock()
    coord._policy.position_context_overrides.return_value = {}

    manager = MagicMock()
    manager.is_cover_manual.return_value = False
    coord.manager = manager

    coord._build_position_context = (
        AdaptiveDataUpdateCoordinator._build_position_context.__get__(coord)
    )
    coord.async_apply_user_position = (
        AdaptiveDataUpdateCoordinator.async_apply_user_position.__get__(coord)
    )

    return coord


@pytest.mark.asyncio
async def test_my_position_button_bypasses_auto_control():
    """My Position button must send the command even when auto_control is off.

    Regression test for issue #430. ``async_apply_user_position`` bypasses the
    auto_control_off gate intrinsically for every user-initiated command, so a
    My Position recall sends even with automatic control off — no per-caller
    bypass_auto_control flag is needed.
    """
    coord = _make_my_position_coord()

    outcome = await coord.async_apply_user_position(
        "cover.test",
        50,
        trigger="my_position_recall",
        force=False,
        use_my_position=True,
    )

    assert (
        outcome[0] == "sent"
    ), f"Expected command to be sent even with auto_control off, but got: {outcome}"
    # The dispatched context bypassed the gate without being classified safety.
    ctx = coord._captured_contexts[-1]
    assert ctx.bypass_auto_control is True
    assert ctx.is_safety is False


@pytest.mark.asyncio
async def test_my_position_button_passes_bypass_kwargs():
    """async_press must forward use_my_position=True (force=False).

    The auto_control_off bypass is intrinsic to async_apply_user_position now,
    so the button no longer passes bypass_auto_control — it only flags the
    My Position recall via use_my_position.

    Drives the button's real async_press with the real coordinator method bound
    so we can assert the kwargs land on _build_position_context.
    """
    from custom_components.adaptive_cover_pro.button import (
        AdaptiveCoverMyPositionButton,
    )
    from custom_components.adaptive_cover_pro.const import (
        CONF_ENTITIES,
        CONF_MY_POSITION_VALUE,
    )

    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.options = {CONF_MY_POSITION_VALUE: 50, CONF_ENTITIES: ["cover.test"]}
    config_entry.data = {"name": "Test Cover", "sensor_type": "cover_blind"}

    coordinator = MagicMock()
    coordinator.async_apply_user_position = AsyncMock(
        return_value=("sent", "set_cover_position")
    )

    button = AdaptiveCoverMyPositionButton.__new__(AdaptiveCoverMyPositionButton)
    button.coordinator = coordinator
    button.config_entry = config_entry
    button._entities = ["cover.test"]

    await button.async_press()

    coordinator.async_apply_user_position.assert_awaited_once()
    _, kwargs = coordinator.async_apply_user_position.call_args
    assert (
        kwargs.get("use_my_position") is True
    ), "async_press must pass use_my_position=True to async_apply_user_position"
    assert (
        "bypass_auto_control" not in kwargs
    ), "bypass is intrinsic now — button must not pass bypass_auto_control"
