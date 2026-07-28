"""Tests for the set_tilt service handler (issue #684 follow-up).

Mirrors ``tests/services/test_set_position_service.py``: the handler resolves
targets via the shared ``_resolve_targets`` shim and delegates each command to
``Coordinator.async_apply_user_tilt`` with the tilt value, ``trigger="set_tilt"``,
and ``force`` propagation. The coordinator method is mocked here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol


def _make_coord(*, entities: list[str] | None = None):
    coord = MagicMock()
    coord.entities = entities or ["cover.venetian"]
    coord.async_apply_user_tilt = AsyncMock(return_value=("sent", ""))
    return coord


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_schema_rejects_missing_tilt() -> None:
    from custom_components.adaptive_cover_pro.services.set_tilt_service import (
        SET_TILT_SCHEMA,
    )

    with pytest.raises(vol.Invalid):
        SET_TILT_SCHEMA({})


def test_schema_rejects_tilt_out_of_range() -> None:
    from custom_components.adaptive_cover_pro.services.set_tilt_service import (
        SET_TILT_SCHEMA,
    )

    with pytest.raises(vol.Invalid):
        SET_TILT_SCHEMA({"tilt": 150})


def test_schema_rejects_negative_tilt() -> None:
    from custom_components.adaptive_cover_pro.services.set_tilt_service import (
        SET_TILT_SCHEMA,
    )

    with pytest.raises(vol.Invalid):
        SET_TILT_SCHEMA({"tilt": -1})


def test_schema_accepts_boundary_values() -> None:
    from custom_components.adaptive_cover_pro.services.set_tilt_service import (
        SET_TILT_SCHEMA,
    )

    assert SET_TILT_SCHEMA({"tilt": 0, "entity_id": ["cover.t"]})["tilt"] == 0
    assert SET_TILT_SCHEMA({"tilt": 100, "entity_id": ["cover.t"]})["tilt"] == 100


def test_schema_coerces_string_to_int() -> None:
    from custom_components.adaptive_cover_pro.services.set_tilt_service import (
        SET_TILT_SCHEMA,
    )

    result = SET_TILT_SCHEMA({"tilt": "40", "entity_id": ["cover.t"]})
    assert result["tilt"] == 40
    assert isinstance(result["tilt"], int)


def test_schema_accepts_force_parameter() -> None:
    from custom_components.adaptive_cover_pro.services.set_tilt_service import (
        SET_TILT_SCHEMA,
    )

    result = SET_TILT_SCHEMA({"tilt": 50, "force": True, "entity_id": ["cover.t"]})
    assert result["tilt"] == 50
    assert result["force"] is True


def test_schema_defaults_force_to_false() -> None:
    from custom_components.adaptive_cover_pro.services.set_tilt_service import (
        SET_TILT_SCHEMA,
    )

    result = SET_TILT_SCHEMA({"tilt": 50, "entity_id": ["cover.t"]})
    assert result.get("force") is False


def test_schema_accepts_ha_injected_target_keys() -> None:
    from custom_components.adaptive_cover_pro.services.set_tilt_service import (
        SET_TILT_SCHEMA,
    )

    assert SET_TILT_SCHEMA({"tilt": 50, "entity_id": ["cover.t"]})["tilt"] == 50
    assert SET_TILT_SCHEMA({"tilt": 30, "device_id": ["abc"]})["tilt"] == 30
    assert SET_TILT_SCHEMA({"tilt": 75, "area_id": ["lr"]})["tilt"] == 75


# ---------------------------------------------------------------------------
# Wrapper coverage: thin _resolve_targets re-export
# ---------------------------------------------------------------------------


def test_resolve_targets_wrapper_delegates_to_services_module() -> None:
    from custom_components.adaptive_cover_pro.services.set_tilt_service import (
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
# Handler delegation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_delegates_to_apply_user_tilt_default_force_false() -> None:
    """Without ``force``, the handler delegates with force=False."""
    from custom_components.adaptive_cover_pro.services.set_tilt_service import (
        async_handle_set_tilt,
    )

    coord = _make_coord()
    call = MagicMock()
    call.data = {"tilt": 40}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_tilt_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_tilt(call)

    coord.async_apply_user_tilt.assert_awaited_once_with(
        "cover.venetian", 40, trigger="set_tilt", force=False
    )


@pytest.mark.asyncio
async def test_handler_force_true_propagates() -> None:
    """force=True propagates through to async_apply_user_tilt."""
    from custom_components.adaptive_cover_pro.services.set_tilt_service import (
        async_handle_set_tilt,
    )

    coord = _make_coord()
    call = MagicMock()
    call.data = {"tilt": 70, "force": True}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_tilt_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_tilt(call)

    coord.async_apply_user_tilt.assert_awaited_once_with(
        "cover.venetian", 70, trigger="set_tilt", force=True
    )


@pytest.mark.asyncio
async def test_entity_filter_limits_commands() -> None:
    """When entity_filter is a set, only those entities get commanded."""
    from custom_components.adaptive_cover_pro.services.set_tilt_service import (
        async_handle_set_tilt,
    )

    coord = _make_coord(entities=["cover.a", "cover.b"])
    call = MagicMock()
    call.data = {"tilt": 60}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_tilt_service._resolve_targets",
        return_value={coord: {"cover.a"}},
    ):
        await async_handle_set_tilt(call)

    commanded = [c.args[0] for c in coord.async_apply_user_tilt.await_args_list]
    assert commanded == ["cover.a"]


@pytest.mark.asyncio
async def test_no_filter_commands_all_entities() -> None:
    """When entity_filter is None, all coordinator entities are commanded."""
    from custom_components.adaptive_cover_pro.services.set_tilt_service import (
        async_handle_set_tilt,
    )

    coord = _make_coord(entities=["cover.a", "cover.b"])
    call = MagicMock()
    call.data = {"tilt": 60}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_tilt_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_tilt(call)

    commanded = sorted(c.args[0] for c in coord.async_apply_user_tilt.await_args_list)
    assert commanded == ["cover.a", "cover.b"]


@pytest.mark.asyncio
async def test_unknown_entity_id_silently_skipped() -> None:
    """No resolved coordinators → nothing commanded, no exception."""
    from custom_components.adaptive_cover_pro.services.set_tilt_service import (
        async_handle_set_tilt,
    )

    call = MagicMock()
    call.data = {"entity_id": ["cover.unknown"], "tilt": 40}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_tilt_service._resolve_targets",
        return_value={},
    ):
        await async_handle_set_tilt(call)
