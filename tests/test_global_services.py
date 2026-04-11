"""Tests for the global ACP services (integration_enable/disable/emergency_stop).

Covers Part B of the issue #186 follow-up:
  - Three services accept HA target: block (entity_id / device_id / area_id)
  - No target → all coordinators
  - entity_id → only the owning coordinator, with entity-level filter for emergency_stop
  - device_id → the coordinator whose config_entry is associated with the device
  - Unmanaged entity_id → silently skipped
  - integration_enable: sets enabled_toggle=True, sends no commands
  - integration_disable: calls stop_in_flight, cancels timers, clears state, disables
  - emergency_stop: calls stop_all (blanket), then same cleanup as disable
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.services import (
    _resolve_targets,
    async_setup_services,
    async_unload_services,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(entities: list[str]) -> MagicMock:
    coord = MagicMock()
    coord.entities = entities
    coord.enabled_toggle = True
    coord.logger = MagicMock()
    coord._cmd_svc = MagicMock()
    coord._cmd_svc.stop_in_flight = AsyncMock(return_value=[])
    coord._cmd_svc.stop_all = AsyncMock(return_value=[])
    coord._cmd_svc.clear_non_safety_targets = MagicMock()
    coord._cmd_svc._safety_targets = set()
    coord._cancel_motion_timeout = MagicMock()
    coord._cancel_weather_timeout = MagicMock()
    return coord


def _make_hass(coordinators: dict) -> MagicMock:
    """Create a mock hass with hass.data[DOMAIN] = coordinators."""
    hass = MagicMock()
    hass.data = {"adaptive_cover_pro": coordinators}
    hass.services = MagicMock()
    hass.services.has_service = MagicMock(return_value=False)
    hass.services.async_register = MagicMock()
    hass.services.async_remove = MagicMock()
    return hass


def _make_call(entity_id=None, device_id=None, area_id=None) -> MagicMock:
    call = MagicMock()
    call.data = {}
    if entity_id:
        call.data["entity_id"] = (
            entity_id if isinstance(entity_id, list) else [entity_id]
        )
    if device_id:
        call.data["device_id"] = (
            device_id if isinstance(device_id, list) else [device_id]
        )
    if area_id:
        call.data["area_id"] = area_id if isinstance(area_id, list) else [area_id]
    return call


# ---------------------------------------------------------------------------
# _resolve_targets
# ---------------------------------------------------------------------------


def test_resolve_no_target_returns_all_coordinators():
    """No target → all coordinators, None filter."""
    coord_a = _make_coordinator(["cover.a"])
    coord_b = _make_coordinator(["cover.b"])
    hass = _make_hass({"entry_a": coord_a, "entry_b": coord_b})
    call = _make_call()

    result = _resolve_targets(hass, call)

    assert set(result.keys()) == {coord_a, coord_b}
    assert result[coord_a] is None
    assert result[coord_b] is None


def test_resolve_entity_id_maps_to_owning_coordinator():
    """entity_id target → only the coordinator that owns that entity."""
    coord_a = _make_coordinator(["cover.living"])
    coord_b = _make_coordinator(["cover.bedroom"])
    hass = _make_hass({"entry_a": coord_a, "entry_b": coord_b})
    call = _make_call(entity_id="cover.living")

    result = _resolve_targets(hass, call)

    assert coord_a in result
    assert coord_b not in result
    assert result[coord_a] == {"cover.living"}


def test_resolve_unmanaged_entity_skipped():
    """entity_id not owned by any coordinator → silently excluded from result."""
    coord_a = _make_coordinator(["cover.living"])
    hass = _make_hass({"entry_a": coord_a})
    call = _make_call(entity_id="cover.unmanaged")

    result = _resolve_targets(hass, call)

    assert result == {}


def test_resolve_device_id_maps_to_coordinator():
    """device_id target → coordinator whose config_entry is associated with the device."""
    coord_a = _make_coordinator(["cover.a"])
    hass = _make_hass({"entry_abc": coord_a})

    fake_device = MagicMock()
    fake_device.config_entries = ["entry_abc"]
    fake_device.area_id = None

    dev_reg_mock = MagicMock()
    dev_reg_mock.async_get = MagicMock(return_value=fake_device)

    call = _make_call(device_id="device_xyz")

    with patch(
        "custom_components.adaptive_cover_pro.services.dr.async_get",
        return_value=dev_reg_mock,
    ):
        result = _resolve_targets(hass, call)

    assert coord_a in result
    assert result[coord_a] is None


def test_resolve_entity_id_within_device_coordinator_not_narrowed():
    """When a coordinator is already targeted by device_id, entity_id does not narrow it."""
    coord_a = _make_coordinator(["cover.a", "cover.b"])
    hass = _make_hass({"entry_abc": coord_a})

    fake_device = MagicMock()
    fake_device.config_entries = ["entry_abc"]
    fake_device.area_id = None

    dev_reg_mock = MagicMock()
    dev_reg_mock.async_get = MagicMock(return_value=fake_device)
    dev_reg_mock.devices = {}  # no area expansion needed

    call = MagicMock()
    call.data = {
        "device_id": ["device_xyz"],
        "entity_id": ["cover.a"],
    }

    with patch(
        "custom_components.adaptive_cover_pro.services.dr.async_get",
        return_value=dev_reg_mock,
    ):
        result = _resolve_targets(hass, call)

    # device_id set None (all covers) — entity_id should not narrow it
    assert result[coord_a] is None


# ---------------------------------------------------------------------------
# integration_enable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_enable_no_target_enables_all():
    """integration_enable with no target enables all coordinators."""
    coord_a = _make_coordinator(["cover.a"])
    coord_b = _make_coordinator(["cover.b"])
    hass = _make_hass({"entry_a": coord_a, "entry_b": coord_b})

    await async_setup_services(hass)
    handler = hass.services.async_register.call_args_list[-3][0][
        2
    ]  # 3rd-from-last = enable

    call = _make_call()
    await handler(call)

    assert coord_a.enabled_toggle is True
    assert coord_b.enabled_toggle is True


@pytest.mark.asyncio
async def test_integration_enable_sends_no_commands():
    """integration_enable must not send any cover commands."""
    coord_a = _make_coordinator(["cover.a"])
    hass = _make_hass({"entry_a": coord_a})

    await async_setup_services(hass)
    handler = hass.services.async_register.call_args_list[-3][0][2]

    call = _make_call()
    await handler(call)

    coord_a._cmd_svc.stop_in_flight.assert_not_called()
    coord_a._cmd_svc.stop_all.assert_not_called()


# ---------------------------------------------------------------------------
# integration_disable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_disable_calls_stop_in_flight():
    """integration_disable calls stop_in_flight (not stop_all)."""
    coord_a = _make_coordinator(["cover.a"])
    hass = _make_hass({"entry_a": coord_a})

    await async_setup_services(hass)
    handler = hass.services.async_register.call_args_list[-2][0][
        2
    ]  # 2nd-from-last = disable

    call = _make_call()
    await handler(call)

    coord_a._cmd_svc.stop_in_flight.assert_called_once()
    coord_a._cmd_svc.stop_all.assert_not_called()


@pytest.mark.asyncio
async def test_integration_disable_sets_enabled_false():
    """integration_disable sets enabled_toggle=False."""
    coord_a = _make_coordinator(["cover.a"])
    hass = _make_hass({"entry_a": coord_a})

    await async_setup_services(hass)
    handler = hass.services.async_register.call_args_list[-2][0][2]

    call = _make_call()
    await handler(call)

    assert coord_a.enabled_toggle is False


@pytest.mark.asyncio
async def test_integration_disable_cancels_timers_and_clears_state():
    """integration_disable cancels motion/weather timers and clears reconciliation state."""
    coord_a = _make_coordinator(["cover.a"])
    hass = _make_hass({"entry_a": coord_a})

    await async_setup_services(hass)
    handler = hass.services.async_register.call_args_list[-2][0][2]

    call = _make_call()
    await handler(call)

    coord_a._cancel_motion_timeout.assert_called_once()
    coord_a._cancel_weather_timeout.assert_called_once()
    coord_a._cmd_svc.clear_non_safety_targets.assert_called_once()


# ---------------------------------------------------------------------------
# emergency_stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emergency_stop_calls_stop_all():
    """emergency_stop calls stop_all (blanket, regardless of wait_for_target)."""
    coord_a = _make_coordinator(["cover.a", "cover.b"])
    hass = _make_hass({"entry_a": coord_a})

    await async_setup_services(hass)
    handler = hass.services.async_register.call_args_list[-1][0][
        2
    ]  # last = emergency_stop

    call = _make_call()
    await handler(call)

    coord_a._cmd_svc.stop_all.assert_called_once_with(coord_a.entities)
    coord_a._cmd_svc.stop_in_flight.assert_not_called()


@pytest.mark.asyncio
async def test_emergency_stop_also_disables_integration():
    """emergency_stop disables the integration after stopping."""
    coord_a = _make_coordinator(["cover.a"])
    hass = _make_hass({"entry_a": coord_a})

    await async_setup_services(hass)
    handler = hass.services.async_register.call_args_list[-1][0][2]

    call = _make_call()
    await handler(call)

    assert coord_a.enabled_toggle is False


@pytest.mark.asyncio
async def test_emergency_stop_with_entity_filter_narrows_stop():
    """emergency_stop with entity_id target stops only that cover."""
    coord_a = _make_coordinator(["cover.a", "cover.b"])
    hass = _make_hass({"entry_a": coord_a})

    await async_setup_services(hass)
    handler = hass.services.async_register.call_args_list[-1][0][2]

    call = _make_call(entity_id="cover.a")
    await handler(call)

    coord_a._cmd_svc.stop_all.assert_called_once_with(["cover.a"])


@pytest.mark.asyncio
async def test_emergency_stop_no_target_hits_all_instances():
    """emergency_stop with no target acts on all ACP coordinators."""
    coord_a = _make_coordinator(["cover.a"])
    coord_b = _make_coordinator(["cover.b"])
    hass = _make_hass({"entry_a": coord_a, "entry_b": coord_b})

    await async_setup_services(hass)
    handler = hass.services.async_register.call_args_list[-1][0][2]

    call = _make_call()
    await handler(call)

    coord_a._cmd_svc.stop_all.assert_called_once()
    coord_b._cmd_svc.stop_all.assert_called_once()
    assert coord_a.enabled_toggle is False
    assert coord_b.enabled_toggle is False


# ---------------------------------------------------------------------------
# async_unload_services
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unload_services_removes_all_three():
    """async_unload_services removes all three new services when last entry unloads."""
    hass = _make_hass({})  # empty → last entry gone

    await async_unload_services(hass)

    removed = {c.args[1] for c in hass.services.async_remove.call_args_list}
    assert "integration_enable" in removed
    assert "integration_disable" in removed
    assert "emergency_stop" in removed
