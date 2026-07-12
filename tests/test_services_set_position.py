"""Tests for the set_position service.

Strict red-green-refactor: each section corresponds to a TDD plan step.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol

from custom_components.adaptive_cover_pro.const import (
    CONF_SENSOR_TYPE,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
    DOMAIN,
    CoverType,
)
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
)
from tests.ha_helpers import (
    VERTICAL_OPTIONS,
    _patch_coordinator_refresh,
)

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup(
    hass,
    entry_id: str = "sp_01",
    options: dict | None = None,
    name: str = "SP Cover",
):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    opts = dict(VERTICAL_OPTIONS) if options is None else options
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": name, CONF_SENSOR_TYPE: CoverType.BLIND},
        options=opts,
        entry_id=entry_id,
        title=name,
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _make_coord(
    *,
    entities: list[str] | None = None,
    options: dict | None = None,
    custom_states: list[CustomPositionSensorState] | None = None,
    is_manual: bool = False,
    apply_position_result=("sent", "set_cover_position"),
):
    """Build a minimal mock coordinator for unit-level tests."""
    coord = MagicMock()
    coord.entities = entities or ["cover.test_blind"]
    coord.config_entry = MagicMock()
    coord.config_entry.options = options or {}

    # Snapshot builder — async_apply_user_position routes its custom-position
    # read through this collaborator after Phase D.  Floor composition now
    # consumes the full PipelineSnapshot (issue #463), so we hand back a real
    # snapshot pre-populated with the requested custom-position states.
    from tests.test_pipeline.conftest import make_snapshot  # noqa: PLC0415

    coord._snapshot_builder = MagicMock()
    coord._snapshot_builder.read_custom_position_sensors.return_value = (
        custom_states or []
    )
    snapshot = make_snapshot(custom_position_sensors=custom_states or [])
    coord._snapshot_builder.build = MagicMock(return_value=snapshot)

    # _build_position_context
    ctx = MagicMock()
    coord._build_position_context.return_value = ctx

    # manager
    coord.manager = MagicMock()
    coord.manager.is_cover_manual.return_value = is_manual

    # _cmd_svc.apply_position
    coord._cmd_svc = MagicMock()
    coord._cmd_svc.apply_position = AsyncMock(return_value=apply_position_result)

    # Bind the real coordinator helper so the service exercises it.
    from custom_components.adaptive_cover_pro.coordinator import (  # noqa: PLC0415
        AdaptiveDataUpdateCoordinator,
    )

    coord.async_apply_user_position = (
        AdaptiveDataUpdateCoordinator.async_apply_user_position.__get__(coord)
    )
    # set_position now routes through the axis collapse point (issue #725); bind
    # the real dispatcher so behavior tests exercise the same path production does.
    coord.async_apply_user_tilt = AsyncMock(return_value=("sent", "tilt"))
    coord.async_apply_user_axis = (
        AdaptiveDataUpdateCoordinator.async_apply_user_axis.__get__(coord)
    )

    return coord


# ---------------------------------------------------------------------------
# Step 1 (Red → Green): Service registration
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_set_position_service_registered_after_setup(hass) -> None:
    """set_position service is registered after async_setup_services."""
    await _setup(hass, entry_id="sp_reg_01")
    assert hass.services.has_service(
        DOMAIN, "set_position"
    ), "set_position service should be registered after setup"


@pytest.mark.integration
async def test_set_position_service_removed_after_all_entries_unloaded(hass) -> None:
    """set_position service is removed when the last entry is unloaded."""
    entry = await _setup(hass, entry_id="sp_unload_01")
    assert hass.services.has_service(DOMAIN, "set_position")

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.services.has_service(
        DOMAIN, "set_position"
    ), "set_position service should be removed when last entry is unloaded"


@pytest.mark.integration
async def test_set_tilt_service_registered_after_setup(hass) -> None:
    """set_tilt service is registered after async_setup_services."""
    await _setup(hass, entry_id="st_reg_01")
    assert hass.services.has_service(
        DOMAIN, "set_tilt"
    ), "set_tilt service should be registered after setup"


@pytest.mark.integration
async def test_set_tilt_service_removed_after_all_entries_unloaded(hass) -> None:
    """set_tilt service is removed when the last entry is unloaded."""
    entry = await _setup(hass, entry_id="st_unload_01")
    assert hass.services.has_service(DOMAIN, "set_tilt")

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.services.has_service(
        DOMAIN, "set_tilt"
    ), "set_tilt service should be removed when last entry is unloaded"


# ---------------------------------------------------------------------------
# Wrapper coverage: thin _resolve_targets re-export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_position_routes_through_axis_dispatch() -> None:
    """The set_position handler dispatches on the POSITION axis constant (#725)."""
    from custom_components.adaptive_cover_pro.cover_types.base import (
        AXIS_NAME_POSITION,
    )
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    coord = MagicMock()
    coord.entities = ["cover.blind_a"]
    coord.async_apply_user_axis = AsyncMock(return_value=("sent", "position"))
    call = MagicMock()
    call.data = {"position": 55}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)

    coord.async_apply_user_axis.assert_awaited_once_with(
        "cover.blind_a",
        AXIS_NAME_POSITION,
        55,
        trigger="set_position",
        force=False,
    )


def test_resolve_targets_wrapper_delegates_to_services_module() -> None:
    """The local _resolve_targets wrapper forwards args to services._resolve_targets."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        _resolve_targets as wrapper,
    )

    sentinel_hass = MagicMock(name="hass")
    sentinel_call = MagicMock(name="call")
    expected = {"coord_x": None}

    with patch(
        "custom_components.adaptive_cover_pro.services._resolve_targets",
        return_value=expected,
    ) as real:
        result = wrapper(sentinel_hass, sentinel_call)

    real.assert_called_once_with(sentinel_hass, sentinel_call)
    assert result is expected


# ---------------------------------------------------------------------------
# Steps 3 & 14 (Red → Green): Schema validation
# ---------------------------------------------------------------------------


def test_schema_rejects_missing_position() -> None:
    """Schema raises when position is absent."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        SET_POSITION_SCHEMA,
    )

    with pytest.raises(vol.Invalid):
        SET_POSITION_SCHEMA({})


def test_schema_rejects_position_out_of_range() -> None:
    """Schema raises when position=150 (> 100)."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        SET_POSITION_SCHEMA,
    )

    with pytest.raises(vol.Invalid):
        SET_POSITION_SCHEMA({"position": 150})


def test_schema_rejects_negative_position() -> None:
    """Schema raises when position=-1 (< 0)."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        SET_POSITION_SCHEMA,
    )

    with pytest.raises(vol.Invalid):
        SET_POSITION_SCHEMA({"position": -1})


def test_schema_accepts_boundary_values() -> None:
    """Schema accepts position=0 and position=100."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        SET_POSITION_SCHEMA,
    )

    assert (
        SET_POSITION_SCHEMA({"position": 0, "entity_id": ["cover.test"]})["position"]
        == 0
    )
    assert (
        SET_POSITION_SCHEMA({"position": 100, "entity_id": ["cover.test"]})["position"]
        == 100
    )


def test_schema_rejects_extra_key_tilt() -> None:
    """Schema rejects genuinely-unknown keys (e.g. 'tilt' is not a valid field)."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        SET_POSITION_SCHEMA,
    )

    with pytest.raises(vol.Invalid):
        SET_POSITION_SCHEMA({"position": 50, "tilt": 30})


def test_schema_coerces_string_to_int() -> None:
    """Schema coerces string '40' to int 40."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        SET_POSITION_SCHEMA,
    )

    result = SET_POSITION_SCHEMA({"position": "40", "entity_id": ["cover.test"]})
    assert result["position"] == 40
    assert isinstance(result["position"], int)


# ---------------------------------------------------------------------------
# Step 5 (Red → Green): No floor active — position passes through unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_floor_active_position_passes_through() -> None:
    """No active min_mode slots → apply_position called with requested position."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    coord = _make_coord(custom_states=[])
    call = MagicMock()
    call.data = {"position": 40}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test_blind",
        40,
        "set_position",
        coord._build_position_context.return_value,
    )


# ---------------------------------------------------------------------------
# Step 6 (Red → Green): min_mode slot off — no clamping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_min_mode_slot_off_no_clamping() -> None:
    """Slot with min_mode=True but is_on=False does NOT act as a floor."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    slot = CustomPositionSensorState(
        entity_ids=("binary_sensor.slot1",),
        is_on=False,
        position=60,
        priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
        min_mode=True,
        use_my=False,
    )
    coord = _make_coord(custom_states=[slot])
    call = MagicMock()
    call.data = {"position": 30}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test_blind",
        30,
        "set_position",
        coord._build_position_context.return_value,
    )


# ---------------------------------------------------------------------------
# Step 7 (Red → Green): min_mode slot on — clamps up
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_min_mode_slot_on_clamps_up() -> None:
    """Slot with min_mode=True, is_on=True, position=50, request 20 → clamped to 50."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    # Re-anchored for #472: a floor must be > ManualOverrideHandler.priority (80)
    # to clamp a user move.
    slot = CustomPositionSensorState(
        entity_ids=("binary_sensor.slot1",),
        is_on=True,
        position=50,
        priority=82,
        min_mode=True,
        use_my=False,
    )
    coord = _make_coord(custom_states=[slot])
    call = MagicMock()
    call.data = {"position": 20}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test_blind",
        50,
        "set_position",
        coord._build_position_context.return_value,
    )


# ---------------------------------------------------------------------------
# Step 8 (Red → Green): at floor and above floor — no clamping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_equals_floor_no_extra_clamping() -> None:
    """Request exactly at floor (50) → apply_position called with 50."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    slot = CustomPositionSensorState(
        entity_ids=("binary_sensor.slot1",),
        is_on=True,
        position=50,
        priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
        min_mode=True,
        use_my=False,
    )
    coord = _make_coord(custom_states=[slot])
    call = MagicMock()
    call.data = {"position": 50}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test_blind",
        50,
        "set_position",
        coord._build_position_context.return_value,
    )


@pytest.mark.asyncio
async def test_request_above_floor_no_clamping() -> None:
    """Request (70) above floor (50) → apply_position called with 70."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    slot = CustomPositionSensorState(
        entity_ids=("binary_sensor.slot1",),
        is_on=True,
        position=50,
        priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
        min_mode=True,
        use_my=False,
    )
    coord = _make_coord(custom_states=[slot])
    call = MagicMock()
    call.data = {"position": 70}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test_blind",
        70,
        "set_position",
        coord._build_position_context.return_value,
    )


# ---------------------------------------------------------------------------
# Steps 10–11 (Red → Green): Multiple min_mode floors — highest wins
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_floors_request_below_highest_clamped() -> None:
    """Two active min_mode slots (40, 65); request 50 → clamped to 65."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    # Re-anchored for #472: a floor must be > ManualOverrideHandler.priority (80)
    # to clamp a user move.
    slot_a = CustomPositionSensorState(
        entity_ids=("binary_sensor.slot1",),
        is_on=True,
        position=40,
        priority=82,
        min_mode=True,
        use_my=False,
    )
    slot_b = CustomPositionSensorState(
        entity_ids=("binary_sensor.slot2",),
        is_on=True,
        position=65,
        priority=82,
        min_mode=True,
        use_my=False,
    )
    coord = _make_coord(custom_states=[slot_a, slot_b])
    call = MagicMock()
    call.data = {"position": 50}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test_blind",
        65,
        "set_position",
        coord._build_position_context.return_value,
    )


@pytest.mark.asyncio
async def test_two_floors_request_above_highest_not_clamped() -> None:
    """Two active min_mode slots (40, 65); request 70 → not clamped."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    slot_a = CustomPositionSensorState(
        entity_ids=("binary_sensor.slot1",),
        is_on=True,
        position=40,
        priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
        min_mode=True,
        use_my=False,
    )
    slot_b = CustomPositionSensorState(
        entity_ids=("binary_sensor.slot2",),
        is_on=True,
        position=65,
        priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
        min_mode=True,
        use_my=False,
    )
    coord = _make_coord(custom_states=[slot_a, slot_b])
    call = MagicMock()
    call.data = {"position": 70}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test_blind",
        70,
        "set_position",
        coord._build_position_context.return_value,
    )


# ---------------------------------------------------------------------------
# Step 12 (Red → Green): Manual override — force=True bypasses gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_override_active_force_bypasses_gate() -> None:
    """is_cover_manual=True but force=True → apply_position still invoked."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    coord = _make_coord(custom_states=[], is_manual=True)
    call = MagicMock()
    call.data = {"position": 40}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)

    # _build_position_context should be called with force=True
    coord._build_position_context.assert_called_once()
    call_kwargs = coord._build_position_context.call_args
    assert call_kwargs.kwargs.get("force") is True or (
        len(call_kwargs.args) >= 3 and call_kwargs.args[2] is True
    ), f"force=True expected in _build_position_context call, got {call_kwargs}"

    # apply_position must still be called (not blocked)
    coord._cmd_svc.apply_position.assert_awaited_once()


# ---------------------------------------------------------------------------
# Step 13 (Red → Green): Unknown entity_id — silently skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_entity_id_silently_skipped() -> None:
    """entity_id not owned by any ACP coord → apply_position never called, no exception."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    call = MagicMock()
    call.data = {"entity_id": ["cover.unknown"], "position": 40}

    # _resolve_targets returns empty dict → no coordinator to act on
    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={},
    ):
        # Must not raise
        await async_handle_set_position(call)


# ---------------------------------------------------------------------------
# Steps 16–17 (Red → Green): INFO log when clamped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clamp_emits_info_log(caplog) -> None:
    """Clamping 20→50 emits an INFO log containing 'clamped' and '50'."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    # Re-anchored for #472: a floor must be > ManualOverrideHandler.priority (80)
    # to clamp a user move.
    slot = CustomPositionSensorState(
        entity_ids=("binary_sensor.slot1",),
        is_on=True,
        position=50,
        priority=82,
        min_mode=True,
        use_my=False,
    )
    coord = _make_coord(custom_states=[slot])
    call = MagicMock()
    call.data = {"position": 20}

    with (
        patch(
            "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
            return_value={coord: None},
        ),
        caplog.at_level(logging.INFO, logger="custom_components.adaptive_cover_pro"),
    ):
        await async_handle_set_position(call)

    log_text = caplog.text
    assert "clamped" in log_text.lower(), f"Expected 'clamped' in log, got: {log_text}"
    assert "50" in log_text, f"Expected '50' in log, got: {log_text}"


@pytest.mark.asyncio
async def test_no_clamp_emits_debug_log(caplog) -> None:
    """No clamping → no INFO log (DEBUG-only)."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    coord = _make_coord(custom_states=[])
    call = MagicMock()
    call.data = {"position": 40}

    with (
        patch(
            "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
            return_value={coord: None},
        ),
        caplog.at_level(logging.INFO, logger="custom_components.adaptive_cover_pro"),
    ):
        await async_handle_set_position(call)

    # INFO log should NOT contain "clamped"
    info_records = [
        r
        for r in caplog.records
        if r.levelno == logging.INFO and "clamped" in r.getMessage().lower()
    ]
    assert not info_records, f"Unexpected INFO clamp log: {info_records}"


# ---------------------------------------------------------------------------
# Entity filter: only targeted entities within coordinator are commanded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entity_filter_limits_commands() -> None:
    """When entity_filter is a set, only those entities get commanded."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    coord = _make_coord(
        entities=["cover.blind_a", "cover.blind_b"],
        custom_states=[],
    )
    call = MagicMock()
    call.data = {"position": 60}

    # Simulate _resolve_targets returning a filter of only blind_a
    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: {"cover.blind_a"}},
    ):
        await async_handle_set_position(call)

    # Only blind_a should be commanded
    calls = coord._cmd_svc.apply_position.await_args_list
    entity_ids_commanded = [c.args[0] for c in calls]
    assert entity_ids_commanded == ["cover.blind_a"]


@pytest.mark.asyncio
async def test_no_filter_commands_all_entities() -> None:
    """When entity_filter is None, all coordinator entities are commanded."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    coord = _make_coord(
        entities=["cover.blind_a", "cover.blind_b"],
        custom_states=[],
    )
    call = MagicMock()
    call.data = {"position": 60}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)

    calls = coord._cmd_svc.apply_position.await_args_list
    entity_ids_commanded = sorted(c.args[0] for c in calls)
    assert entity_ids_commanded == ["cover.blind_a", "cover.blind_b"]


# ---------------------------------------------------------------------------
# Non-min_mode slot (is_on=True) does NOT act as floor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_min_mode_slot_on_does_not_clamp() -> None:
    """Slot is_on=True but min_mode=False — no floor effect."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    slot = CustomPositionSensorState(
        entity_ids=("binary_sensor.slot1",),
        is_on=True,
        position=80,
        priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
        min_mode=False,
        use_my=False,
    )
    coord = _make_coord(custom_states=[slot])
    call = MagicMock()
    call.data = {"position": 30}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test_blind",
        30,
        "set_position",
        coord._build_position_context.return_value,
    )


# ---------------------------------------------------------------------------
# New contract: ``force`` parameter and pipeline preemption
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_default_engages_manual_override() -> None:
    """Calling the service without ``force`` engages manual override."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    coord = _make_coord(custom_states=[])
    call = MagicMock()
    call.data = {"position": 50}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)

    # mark_user_command must have been called (via the bound real method).
    coord.manager.mark_user_command.assert_called_once_with(
        "cover.test_blind", reason="set_position"
    )


@pytest.mark.asyncio
async def test_service_force_true_bypasses_pipeline() -> None:
    """With ``force=True`` even an active weather override does not block.

    Default pipeline mock auto-yields no winner step (iterating a MagicMock
    returns empty), so the legacy bypass path is exercised directly: the
    command dispatches and manual override is NOT engaged.
    """
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    coord = _make_coord(custom_states=[])
    call = MagicMock()
    call.data = {"position": 50, "force": True}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)

    coord._cmd_svc.apply_position.assert_awaited_once()
    coord.manager.mark_user_command.assert_not_called()


@pytest.mark.asyncio
async def test_service_force_default_preempted_by_safety_custom_position() -> None:
    """With force=False (default) and a priority-100 custom slot winning, the call is rejected."""
    from custom_components.adaptive_cover_pro.pipeline.types import (
        DecisionStep,
        PipelineResult,
    )
    from custom_components.adaptive_cover_pro.const import ControlMethod
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    coord = _make_coord(custom_states=[])
    # Wire up the pipeline mock and handler lookup explicitly so the
    # preemption branch resolves a real priority value.
    coord._pipeline.evaluate.return_value = PipelineResult(
        position=10,
        control_method=ControlMethod.CUSTOM_POSITION,
        reason="custom_position",
        decision_trace=[
            DecisionStep(
                handler="custom_position_5",
                matched=True,
                reason="custom_position",
                position=10,
            )
        ],
        is_safety=True,
    )
    handler = MagicMock()
    handler.priority = 100
    coord._handler_by_name = {"custom_position_5": handler}
    coord._cmd_svc.record_preempted_skip = MagicMock()

    call = MagicMock()
    call.data = {"position": 50}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)

    coord._cmd_svc.apply_position.assert_not_awaited()
    coord.manager.mark_user_command.assert_not_called()
    coord._cmd_svc.record_preempted_skip.assert_called_once_with(
        "cover.test_blind",
        50,
        trigger="set_position",
        winner_name="custom_position_5",
    )


def test_schema_accepts_force_parameter() -> None:
    """SET_POSITION_SCHEMA accepts the optional ``force`` field."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        SET_POSITION_SCHEMA,
    )

    result = SET_POSITION_SCHEMA(
        {"position": 50, "force": True, "entity_id": ["cover.test"]}
    )
    assert result["position"] == 50
    assert result["force"] is True


def test_schema_defaults_force_to_false() -> None:
    """SET_POSITION_SCHEMA defaults ``force`` to False when omitted."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        SET_POSITION_SCHEMA,
    )

    result = SET_POSITION_SCHEMA({"position": 50, "entity_id": ["cover.test"]})
    assert result.get("force") is False


# ---------------------------------------------------------------------------
# Issue #460: Schema must accept HA-injected target keys
# ---------------------------------------------------------------------------


def test_schema_accepts_ha_injected_entity_id() -> None:
    """Schema accepts entity_id injected by HA target resolution."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        SET_POSITION_SCHEMA,
    )

    result = SET_POSITION_SCHEMA({"position": 50, "entity_id": ["cover.patio"]})
    assert result["position"] == 50


def test_schema_accepts_ha_injected_device_id() -> None:
    """Schema accepts device_id injected by HA target resolution."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        SET_POSITION_SCHEMA,
    )

    result = SET_POSITION_SCHEMA({"position": 30, "device_id": ["abc123"]})
    assert result["position"] == 30


def test_schema_accepts_ha_injected_area_id() -> None:
    """Schema accepts area_id injected by HA target resolution."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        SET_POSITION_SCHEMA,
    )

    result = SET_POSITION_SCHEMA({"position": 75, "area_id": ["living_room"]})
    assert result["position"] == 75


# ---------------------------------------------------------------------------
# Issue #472: the user-move floor clamp is priority-aware. A floor clamps a
# manual command only when its priority strictly exceeds ManualOverrideHandler
# priority (80); a floor at or below 80 yields to the manual move.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sub_80_floor_does_not_clamp_user_move() -> None:
    """A default-priority (77) floor yields to a below-floor user command (#472)."""
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    slot = CustomPositionSensorState(
        entity_ids=("binary_sensor.slot1",),
        is_on=True,
        position=60,
        priority=DEFAULT_CUSTOM_POSITION_PRIORITY,  # 77 < 80 → does not clamp
        min_mode=True,
        use_my=False,
    )
    coord = _make_coord(custom_states=[slot])
    call = MagicMock()
    call.data = {"position": 20}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)

    # Manual wins: the requested value is dispatched unchanged.
    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test_blind",
        20,
        "set_position",
        coord._build_position_context.return_value,
    )


@pytest.mark.asyncio
async def test_above_80_floor_clamps_user_move_before_dispatch() -> None:
    """A >80 floor clamps a below-floor user command up to the floor (#472).

    ``apply_position`` receives the FLOOR value, proving the clamp intercepts
    the move *before* dispatch — the motor never sees the below-floor value.
    """
    from custom_components.adaptive_cover_pro.pipeline.handlers.manual_override import (
        ManualOverrideHandler,
    )
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    slot = CustomPositionSensorState(
        entity_ids=("binary_sensor.slot1",),
        is_on=True,
        position=60,
        priority=ManualOverrideHandler.priority + 2,  # 82 > 80 → clamps
        min_mode=True,
        use_my=False,
    )
    coord = _make_coord(custom_states=[slot])
    call = MagicMock()
    call.data = {"position": 20}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test_blind",
        60,
        "set_position",
        coord._build_position_context.return_value,
    )


@pytest.mark.asyncio
async def test_floor_equal_to_manual_priority_does_not_clamp() -> None:
    """A floor at exactly 80 ties with manual override and loses (strict ``>``)."""
    from custom_components.adaptive_cover_pro.pipeline.handlers.manual_override import (
        ManualOverrideHandler,
    )
    from custom_components.adaptive_cover_pro.services.set_position_service import (
        async_handle_set_position,
    )

    slot = CustomPositionSensorState(
        entity_ids=("binary_sensor.slot1",),
        is_on=True,
        position=60,
        priority=ManualOverrideHandler.priority,  # 80 == 80 → ties → does not clamp
        min_mode=True,
        use_my=False,
    )
    coord = _make_coord(custom_states=[slot])
    call = MagicMock()
    call.data = {"position": 20}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_position_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_position(call)

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test_blind",
        20,
        "set_position",
        coord._build_position_context.return_value,
    )
