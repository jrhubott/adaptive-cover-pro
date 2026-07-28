"""Assumed-position store + display surfacing for open/close-only covers (#888).

An assumed-state / open-close-only cover (e.g. Somfy RTS) never reports a
numeric position, so the companion card renders ``—``. When ACP drives such a
cover — an ACP My move, or an external stop that engages the #875 override —
we stash the routed target as a *display-only* assumed position that the
reported-position surfaces fall back to ONLY when the live HA read is None.

Binding invariant §3b: the assumed value must NEVER enter the command-dispatch
read path (``get_current_position`` / ``_read_position_with_capabilities``);
those keep reading raw HA state so the delta / same-position / endpoint gates
are unaffected.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
)
from custom_components.adaptive_cover_pro.state.cover_provider import CoverProvider

_OPEN_CLOSE_ONLY = {
    "has_set_position": False,
    "has_set_tilt_position": False,
    "has_open": True,
    "has_close": True,
    "has_stop": True,
}


@pytest.fixture
def mock_hass():
    h = MagicMock()
    h.services.async_call = AsyncMock()
    return h


@pytest.fixture
def svc(mock_hass):
    return CoverCommandService(
        hass=mock_hass,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=MagicMock(),
        open_close_threshold=50,
        check_interval_minutes=1,
        position_tolerance=3,
        max_retries=3,
    )


def _unknown_state(mock_hass) -> None:
    state_obj = MagicMock()
    state_obj.state = "unknown"
    state_obj.attributes = {}
    mock_hass.states.get.return_value = state_obj


# ---------------------------------------------------------------------------
# Step 1 — the per-entity assumed-position store
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_record_and_clear_assumed_position(svc):
    """Record → get → clear round-trips through PerEntityState.assumed_position."""
    entity = "cover.somfy"
    assert svc.get_assumed_position(entity) is None
    svc.record_assumed_position(entity, 50)
    assert svc.get_assumed_position(entity) == 50
    svc.clear_assumed_position(entity)
    assert svc.get_assumed_position(entity) is None


# ---------------------------------------------------------------------------
# Step 3 — display carries assumed; the command gate does not
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_read_positions_surfaces_assumed(mock_hass):
    """read_positions(..., assumed=fn) returns the assumed value when live is None."""
    _unknown_state(mock_hass)
    provider = CoverProvider(mock_hass, MagicMock())
    policy = get_policy("cover_blind")
    with patch(
        "custom_components.adaptive_cover_pro.state.cover_provider.check_cover_features",
        return_value=_OPEN_CLOSE_ONLY,
    ):
        positions = provider.read_positions(
            ["cover.somfy"], policy, assumed=lambda _e: 50
        )
    assert positions == {"cover.somfy": 50}


@pytest.mark.unit
def test_get_current_position_stays_raw(svc, mock_hass):
    """§3b: the command-dispatch read never sees the assumed value."""
    _unknown_state(mock_hass)
    svc.record_assumed_position("cover.somfy", 50)
    with patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
        return_value=_OPEN_CLOSE_ONLY,
    ):
        assert svc.get_current_position("cover.somfy") is None
