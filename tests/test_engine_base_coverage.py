"""Unit tests for engine/covers/base.py uncovered branches."""

from __future__ import annotations
from unittest.mock import MagicMock

import pytest

from tests.cover_helpers import (
    build_vertical_cover,
    build_tilt_cover,
    build_horizontal_cover,
)


def _common_kwargs():
    """Return required base dataclass kwargs."""
    return {
        "logger": MagicMock(),
        "sol_azi": 180.0,
        "sol_elev": 45.0,
        "sun_data": MagicMock(),
    }


# ---------------------------------------------------------------------------
# __getattr__ fallback chain
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_getattr_vert_config_field_on_vertical_cover():
    """Accessing a vert_config field (h_win) on vertical cover delegates correctly."""
    cover = build_vertical_cover(**_common_kwargs(), h_win=2.0, distance=0.5)
    # h_win is in _VERT_CONFIG_FIELDS — should not raise
    val = cover.h_win
    assert val == 2.0


@pytest.mark.unit
def test_getattr_tilt_config_field_on_tilt_cover():
    """Accessing a tilt_config field (slat_distance) on tilt cover delegates correctly."""
    cover = build_tilt_cover(
        **_common_kwargs(), slat_distance=0.03, depth=0.02, mode="mode1"
    )
    val = cover.slat_distance
    assert val == 0.03


@pytest.mark.unit
def test_getattr_horiz_config_field_on_horizontal_cover():
    """Accessing a horiz_config field (awn_length) on horizontal cover delegates correctly."""
    cover = build_horizontal_cover(**_common_kwargs(), awn_length=2.0, awn_angle=0.0)
    val = cover.awn_length
    assert val == 2.0


@pytest.mark.unit
def test_getattr_vert_field_on_non_vertical_raises():
    """Accessing a vert_config field on a tilt cover (which has no vert_config) raises AttributeError."""
    cover = build_tilt_cover(
        **_common_kwargs(), slat_distance=0.03, depth=0.02, mode="mode1"
    )
    with pytest.raises(AttributeError):
        _ = cover.h_win


@pytest.mark.unit
def test_getattr_unknown_field_raises():
    """Accessing an unknown field raises AttributeError."""
    cover = build_vertical_cover(**_common_kwargs(), h_win=2.0, distance=0.5)
    with pytest.raises(AttributeError):
        _ = cover.this_field_does_not_exist


# ---------------------------------------------------------------------------
# control_state_reason: "Default" fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_control_state_reason_default_fallback():
    """Returns 'Default' when sun is valid but not direct, not in blind spot, and not sunset."""
    from unittest.mock import patch, PropertyMock
    from custom_components.adaptive_cover_pro.engine.covers.vertical import (
        AdaptiveVerticalCover,
    )

    cover = build_vertical_cover(**_common_kwargs(), h_win=2.0, distance=0.5)

    # Patch to produce the "Default" path:
    # valid=True, direct_sun_valid=False, sunset_valid=False, is_sun_in_blind_spot=False
    with (
        patch.object(
            AdaptiveVerticalCover,
            "direct_sun_valid",
            new_callable=PropertyMock,
            return_value=False,
        ),
        patch.object(
            AdaptiveVerticalCover,
            "sunset_valid",
            new_callable=PropertyMock,
            return_value=False,
        ),
        patch.object(
            AdaptiveVerticalCover, "valid", new_callable=PropertyMock, return_value=True
        ),
        patch.object(
            AdaptiveVerticalCover,
            "is_sun_in_blind_spot",
            new_callable=PropertyMock,
            return_value=False,
        ),
    ):
        reason = cover.control_state_reason

    assert reason == "Default"


@pytest.mark.unit
def test_control_state_reason_direct_sun():
    """Returns 'Direct Sun' when direct_sun_valid is True."""
    from unittest.mock import patch, PropertyMock
    from custom_components.adaptive_cover_pro.engine.covers.vertical import (
        AdaptiveVerticalCover,
    )

    cover = build_vertical_cover(**_common_kwargs(), h_win=2.0, distance=0.5)

    with patch.object(
        AdaptiveVerticalCover,
        "direct_sun_valid",
        new_callable=PropertyMock,
        return_value=True,
    ):
        reason = cover.control_state_reason

    assert reason == "Direct Sun"


@pytest.mark.unit
def test_control_state_reason_sunset_offset():
    """Returns 'Default: Sunset Offset' when sunset_valid is True."""
    from unittest.mock import patch, PropertyMock
    from custom_components.adaptive_cover_pro.engine.covers.vertical import (
        AdaptiveVerticalCover,
    )

    cover = build_vertical_cover(**_common_kwargs(), h_win=2.0, distance=0.5)

    with (
        patch.object(
            AdaptiveVerticalCover,
            "direct_sun_valid",
            new_callable=PropertyMock,
            return_value=False,
        ),
        patch.object(
            AdaptiveVerticalCover,
            "sunset_valid",
            new_callable=PropertyMock,
            return_value=True,
        ),
    ):
        reason = cover.control_state_reason

    assert reason == "Default: Sunset Offset"
