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

from custom_components.adaptive_cover_pro.const import (
    CONF_INTERP,
    CONF_INTERP_LIST,
    CONF_INTERP_LIST_NEW,
    CONF_INVERSE_STATE,
)


def _make_coord(*, entities: list[str] | None = None):
    from custom_components.adaptive_cover_pro.const import CoverType
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    coord = MagicMock()
    coord.entities = entities or ["cover.venetian"]
    # The service reads the policy's ordered dispatch view (#1115); a MagicMock
    # policy would hand back a MagicMock that iterates as empty.
    coord._policy = get_policy(CoverType.VENETIAN)
    coord.async_apply_user_tilt = AsyncMock(return_value=("sent", ""))
    coord.async_apply_user_position = AsyncMock(return_value=("sent", ""))
    # set_tilt now routes through the axis collapse point (issue #725); bind the
    # real dispatcher so it forwards to the mocked async_apply_user_tilt, keeping
    # these delegation assertions a true parity guard for the tilt path.
    coord.async_apply_user_axis = (
        AdaptiveDataUpdateCoordinator.async_apply_user_axis.__get__(coord)
    )
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


# ---------------------------------------------------------------------------
# Issue #1027: a tilt-only cover falls back to the position path, so the frame
# transform has to reach it too.
# ---------------------------------------------------------------------------


def _make_fallback_coord(options: dict):
    """Build a ``cover_tilt`` coord that reaches the real position dispatch boundary.

    ``cover_tilt``'s primary axis IS the tilt, so ``apply_user_tilt`` returns
    not-handled and ``async_apply_user_tilt`` falls back to
    ``async_apply_user_position`` (coordinator.py). Bind both real methods so
    the value that actually leaves the coordinator is observable.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from custom_components.adaptive_cover_pro.const import ControlMethod
    from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult
    from tests.ha_helpers import wire_dispatch_frame
    from tests.test_pipeline.conftest import make_snapshot

    coord = MagicMock()
    coord.entities = ["cover.slats"]
    coord.config_entry = MagicMock()
    coord.config_entry.options = options
    wire_dispatch_frame(coord, options, cover_type="cover_tilt")
    coord._resolved_options = options
    coord._snapshot_builder = MagicMock()
    coord._snapshot_builder.build = MagicMock(return_value=make_snapshot())
    ctx = MagicMock(name="position_context")
    coord._build_position_context.return_value = ctx
    coord._cmd_svc = MagicMock()
    coord._cmd_svc.apply_position = AsyncMock(return_value=("sent", ""))
    coord._pipeline_bypasses_auto_control = False
    coord.async_apply_user_position = (
        AdaptiveDataUpdateCoordinator.async_apply_user_position.__get__(coord)
    )
    coord.async_apply_user_tilt = (
        AdaptiveDataUpdateCoordinator.async_apply_user_tilt.__get__(coord)
    )
    coord.async_apply_user_axis = (
        AdaptiveDataUpdateCoordinator.async_apply_user_axis.__get__(coord)
    )
    coord._ctx = ctx

    def _auto_state(logical: int) -> int:
        coord._pipeline_result = PipelineResult(
            position=logical, control_method=ControlMethod.SOLAR, reason="solar"
        )
        return AdaptiveDataUpdateCoordinator.state.fget(coord)

    coord._auto_state = _auto_state
    return coord


async def _call_set_tilt(coord, tilt: int) -> None:
    from custom_components.adaptive_cover_pro.services.set_tilt_service import (
        async_handle_set_tilt,
    )

    call = MagicMock()
    call.data = {"tilt": tilt, "force": True}
    with patch(
        "custom_components.adaptive_cover_pro.services.set_tilt_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_tilt(call)


_TILT_INTERP_OPTIONS = {
    CONF_INTERP: True,
    CONF_INTERP_LIST: [0, 25, 58, 100],
    CONF_INTERP_LIST_NEW: [0, 45, 58, 100],
}


@pytest.mark.asyncio
async def test_set_tilt_cover_tilt_fallback_interpolates() -> None:
    """``set_tilt: 25`` on a calibrated tilt-only cover dispatches motor 45.

    The fallback into the position path must carry the same logical → cover
    frame mapping the automatic path applies (#1027), not the raw request.
    """
    coord = _make_fallback_coord(dict(_TILT_INTERP_OPTIONS))

    await _call_set_tilt(coord, 25)

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.slats", 45, "set_tilt", coord._ctx
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        pytest.param({CONF_INVERSE_STATE: True}, id="inverse_state"),
        pytest.param(dict(_TILT_INTERP_OPTIONS), id="interpolation"),
    ],
)
async def test_set_tilt_cover_tilt_fallback_dispatches_same_frame_as_state(
    options: dict,
) -> None:
    """The tilt fallback and the automatic path agree on the dispatched frame.

    This is #1027's whole contract stated directly: for one logical value, the
    number the user path hands ``CoverCommandService`` must equal the number
    ``coordinator.state`` publishes for the same value. Asserting the
    invariant rather than a literal keeps the guard honest for ``cover_tilt``,
    whose axis-level inversion key is a separate open question (#1028).
    """
    coord = _make_fallback_coord(options)

    await _call_set_tilt(coord, 25)

    dispatched = coord._cmd_svc.apply_position.await_args.args[1]
    assert dispatched == coord._auto_state(25)
