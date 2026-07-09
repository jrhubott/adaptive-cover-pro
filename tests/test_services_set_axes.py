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
