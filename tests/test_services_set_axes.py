"""Tests for the generalized set_axes service (issue #725).

``set_axes`` takes a per-axis target map (``{position, tilt}``) plus ``force``
and dispatches each requested axis through the coordinator's
``async_apply_user_axis`` collapse point. It validates every requested axis
against ``policy.supported_axes(caps)`` for each target entity, raising
``ServiceValidationError`` for an unsupported axis — the issue is explicit that
this is an error, not a silent no-op. No cover-type string branching: dispatch
is keyed on the ``AXIS_NAME_*`` constants.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError

from custom_components.adaptive_cover_pro.const import (
    CONF_INTERP,
    CONF_INTERP_LIST,
    CONF_INTERP_LIST_NEW,
    CONF_INVERSE_STATE,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.base import (
    AXIS_NAME_POSITION,
    AXIS_NAME_TILT,
)
from custom_components.adaptive_cover_pro.state.snapshot import CoverCapabilities

pytestmark = pytest.mark.unit


def _make_coord(
    *,
    cover_type: str,
    caps: CoverCapabilities,
    entities: list[str] | None = None,
) -> MagicMock:
    coord = MagicMock()
    coord.entities = entities or ["cover.test"]
    coord._policy = get_policy(cover_type)
    coord._cover_provider = MagicMock()
    coord._cover_provider.read_single_capabilities.return_value = caps
    coord.async_apply_user_axis = AsyncMock(return_value=("sent", ""))
    return coord


_DUAL_CAPS = CoverCapabilities(
    has_set_position=True,
    has_set_tilt_position=True,
    has_open=True,
    has_close=True,
)
_POSITION_ONLY_CAPS = CoverCapabilities(
    has_set_position=True,
    has_set_tilt_position=False,
    has_open=True,
    has_close=True,
)
# Open/close-only cover: no native set_cover_position, but the integration
# drives the position axis via open_cover/close_cover (#886).
_OPEN_CLOSE_ONLY_CAPS = CoverCapabilities(
    has_set_position=False,
    has_set_tilt_position=False,
    has_open=True,
    has_close=True,
)
# A cover the integration cannot drive on any axis at all.
_NO_DRIVE_CAPS = CoverCapabilities(
    has_set_position=False,
    has_set_tilt_position=False,
    has_open=False,
    has_close=False,
)


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_schema_rejects_missing_axes() -> None:
    from custom_components.adaptive_cover_pro.services.set_axes_service import (
        SET_AXES_SCHEMA,
    )

    with pytest.raises(vol.Invalid):
        SET_AXES_SCHEMA({"entity_id": ["cover.test"]})


def test_schema_accepts_both_axes() -> None:
    from custom_components.adaptive_cover_pro.services.set_axes_service import (
        SET_AXES_SCHEMA,
    )

    result = SET_AXES_SCHEMA(
        {"axes": {"position": 60, "tilt": 30}, "entity_id": ["cover.test"]}
    )
    assert result["axes"]["position"] == 60
    assert result["axes"]["tilt"] == 30


def test_schema_rejects_out_of_range_axis_value() -> None:
    from custom_components.adaptive_cover_pro.services.set_axes_service import (
        SET_AXES_SCHEMA,
    )

    with pytest.raises(vol.Invalid):
        SET_AXES_SCHEMA({"axes": {"position": 150}, "entity_id": ["cover.test"]})


def test_schema_defaults_force_to_false() -> None:
    from custom_components.adaptive_cover_pro.services.set_axes_service import (
        SET_AXES_SCHEMA,
    )

    result = SET_AXES_SCHEMA({"axes": {"position": 50}, "entity_id": ["cover.test"]})
    assert result.get("force") is False


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sets_both_axes_on_venetian() -> None:
    """A single call with both axes dispatches each to the collapse point."""
    from custom_components.adaptive_cover_pro.services.set_axes_service import (
        async_handle_set_axes,
    )

    coord = _make_coord(
        cover_type="cover_venetian", caps=_DUAL_CAPS, entities=["cover.venetian"]
    )
    call = MagicMock()
    call.data = {"axes": {"position": 60, "tilt": 30}}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_axes_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_axes(call)

    awaited = {
        (c.args[0], c.args[1], c.args[2], c.kwargs.get("force"))
        for c in coord.async_apply_user_axis.await_args_list
    }
    assert awaited == {
        ("cover.venetian", AXIS_NAME_POSITION, 60, False),
        ("cover.venetian", AXIS_NAME_TILT, 30, False),
    }


@pytest.mark.asyncio
async def test_unsupported_axis_raises() -> None:
    """Requesting tilt on a position-only blind raises ServiceValidationError."""
    from custom_components.adaptive_cover_pro.services.set_axes_service import (
        async_handle_set_axes,
    )

    coord = _make_coord(
        cover_type="cover_blind", caps=_POSITION_ONLY_CAPS, entities=["cover.blind"]
    )
    call = MagicMock()
    call.data = {"axes": {"tilt": 30}}

    with (
        patch(
            "custom_components.adaptive_cover_pro.services.set_axes_service._resolve_targets",
            return_value={coord: None},
        ),
        pytest.raises(ServiceValidationError, match="tilt"),
    ):
        await async_handle_set_axes(call)

    coord.async_apply_user_axis.assert_not_awaited()


@pytest.mark.asyncio
async def test_position_axis_on_open_close_only_blind_dispatches() -> None:
    """Position on an open/close-only blind dispatches — it is not rejected.

    Regression for #886: the companion card's ``set_axes`` call failed with
    "does not support the 'position' axis" for a vertical blind lacking native
    ``set_cover_position``, even though the integration drives it via
    ``open_cover`` / ``close_cover``.
    """
    from custom_components.adaptive_cover_pro.services.set_axes_service import (
        async_handle_set_axes,
    )

    coord = _make_coord(
        cover_type="cover_blind",
        caps=_OPEN_CLOSE_ONLY_CAPS,
        entities=["cover.deck_front_shade"],
    )
    call = MagicMock()
    call.data = {"axes": {"position": 100}}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_axes_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_axes(call)

    coord.async_apply_user_axis.assert_awaited_once_with(
        "cover.deck_front_shade",
        AXIS_NAME_POSITION,
        100,
        trigger="set_axes",
        force=False,
    )


@pytest.mark.asyncio
async def test_position_axis_on_undrivable_cover_raises() -> None:
    """Position on a cover with neither set_position nor open+close still errors."""
    from custom_components.adaptive_cover_pro.services.set_axes_service import (
        async_handle_set_axes,
    )

    coord = _make_coord(
        cover_type="cover_blind", caps=_NO_DRIVE_CAPS, entities=["cover.blind"]
    )
    call = MagicMock()
    call.data = {"axes": {"position": 50}}

    with (
        patch(
            "custom_components.adaptive_cover_pro.services.set_axes_service._resolve_targets",
            return_value={coord: None},
        ),
        pytest.raises(ServiceValidationError, match="position"),
    ):
        await async_handle_set_axes(call)

    coord.async_apply_user_axis.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_axes_raises() -> None:
    """An empty axes map is an error, not a silent no-op."""
    from custom_components.adaptive_cover_pro.services.set_axes_service import (
        async_handle_set_axes,
    )

    coord = _make_coord(cover_type="cover_blind", caps=_POSITION_ONLY_CAPS)
    call = MagicMock()
    call.data = {"axes": {}}

    with (
        patch(
            "custom_components.adaptive_cover_pro.services.set_axes_service._resolve_targets",
            return_value={coord: None},
        ),
        pytest.raises(ServiceValidationError),
    ):
        await async_handle_set_axes(call)

    coord.async_apply_user_axis.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_propagates() -> None:
    """force=true reaches the collapse point."""
    from custom_components.adaptive_cover_pro.services.set_axes_service import (
        async_handle_set_axes,
    )

    coord = _make_coord(
        cover_type="cover_blind", caps=_POSITION_ONLY_CAPS, entities=["cover.blind"]
    )
    call = MagicMock()
    call.data = {"axes": {"position": 40}, "force": True}

    with patch(
        "custom_components.adaptive_cover_pro.services.set_axes_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_axes(call)

    coord.async_apply_user_axis.assert_awaited_once_with(
        "cover.blind", AXIS_NAME_POSITION, 40, trigger="set_axes", force=True
    )


@pytest.mark.asyncio
async def test_unsupported_axis_rejected_before_any_dispatch() -> None:
    """One unsupported axis rejects the whole call — no partial dispatch."""
    from custom_components.adaptive_cover_pro.services.set_axes_service import (
        async_handle_set_axes,
    )

    coord = _make_coord(
        cover_type="cover_blind", caps=_POSITION_ONLY_CAPS, entities=["cover.blind"]
    )
    call = MagicMock()
    call.data = {"axes": {"position": 40, "tilt": 30}}

    with (
        patch(
            "custom_components.adaptive_cover_pro.services.set_axes_service._resolve_targets",
            return_value={coord: None},
        ),
        pytest.raises(ServiceValidationError),
    ):
        await async_handle_set_axes(call)

    coord.async_apply_user_axis.assert_not_awaited()


# ---------------------------------------------------------------------------
# Issue #1027: set_axes speaks the logical frame on the position axis
# ---------------------------------------------------------------------------


def _make_dispatch_coord(options: dict) -> MagicMock:
    """Build a coord whose position axis reaches the real dispatch boundary.

    ``_make_coord`` above stops at ``async_apply_user_axis`` so the routing
    tests stay unit-level. These frame tests need the value that actually
    leaves the coordinator, so bind the real collapse point and the real
    position setter and observe ``CoverCommandService.apply_position``.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from tests.ha_helpers import bind_user_position_seam, wire_dispatch_frame
    from tests.test_pipeline.conftest import make_snapshot

    coord = MagicMock()
    coord.entities = ["cover.blind"]
    coord.config_entry = MagicMock()
    coord.config_entry.options = options
    wire_dispatch_frame(coord, options)
    coord._resolved_options = options
    coord._cover_provider = MagicMock()
    coord._cover_provider.read_single_capabilities.return_value = _POSITION_ONLY_CAPS
    coord._snapshot_builder = MagicMock()
    coord._snapshot_builder.build = MagicMock(return_value=make_snapshot())
    ctx = MagicMock(name="position_context")
    coord._build_position_context.return_value = ctx
    coord._cmd_svc = MagicMock()
    coord._cmd_svc.apply_position = AsyncMock(return_value=("sent", ""))
    bind_user_position_seam(coord)
    coord.async_apply_user_axis = (
        AdaptiveDataUpdateCoordinator.async_apply_user_axis.__get__(coord)
    )
    coord._ctx = ctx
    return coord


async def _call_set_axes(coord: MagicMock, position: int) -> None:
    from custom_components.adaptive_cover_pro.services.set_axes_service import (
        async_handle_set_axes,
    )

    call = MagicMock()
    call.data = {"axes": {AXIS_NAME_POSITION: position}, "force": True}
    with patch(
        "custom_components.adaptive_cover_pro.services.set_axes_service._resolve_targets",
        return_value={coord: None},
    ):
        await async_handle_set_axes(call)


@pytest.mark.asyncio
async def test_set_axes_position_inverse_state_dispatches_inverted() -> None:
    """``set_axes: {position: 30}`` on an inverse-state instance dispatches 70."""
    coord = _make_dispatch_coord({CONF_INVERSE_STATE: True})

    await _call_set_axes(coord, 30)

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.blind", 70, "set_axes", coord._ctx
    )


@pytest.mark.asyncio
async def test_set_axes_position_interpolation_dispatches_motor_value() -> None:
    """``set_axes: {position: 25}`` on a calibrated instance dispatches motor 45."""
    coord = _make_dispatch_coord(
        {
            CONF_INTERP: True,
            CONF_INTERP_LIST: [0, 25, 58, 100],
            CONF_INTERP_LIST_NEW: [0, 45, 58, 100],
        }
    )

    await _call_set_axes(coord, 25)

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.blind", 45, "set_axes", coord._ctx
    )
