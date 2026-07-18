"""Tests for min/max position limit application.

These tests verify the correct behavior of position limits in different scenarios,
particularly the interaction between enable_min_position/enable_max_position flags
and sunset_position functionality.

Regression tests for Issue #24: Invalid sunset cover position.
"""

import pytest
from unittest.mock import patch
from datetime import datetime

from custom_components.adaptive_cover_pro.position_utils import PositionConverter
from tests.cover_helpers import build_vertical_cover


@pytest.mark.unit
def test_apply_limits_always_enforce_min(mock_sun_data, mock_logger):
    """Test min_pos always enforced when enable_min_position = False."""
    # enable_min_position = False → min_pos always applied
    result = PositionConverter.apply_limits(
        value=20,
        min_pos=35,
        max_pos=100,
        apply_min=False,  # Always enforce
        apply_max=False,
        sun_valid=False,  # Even when sun not valid
    )
    assert result == 35  # Should clamp to min_pos


@pytest.mark.unit
def test_apply_limits_always_enforce_max(mock_sun_data, mock_logger):
    """Test max_pos always enforced when enable_max_position = False."""
    # enable_max_position = False → max_pos always applied
    result = PositionConverter.apply_limits(
        value=80,
        min_pos=0,
        max_pos=60,
        apply_min=False,
        apply_max=False,  # Always enforce
        sun_valid=False,  # Even when sun not valid
    )
    assert result == 60  # Should clamp to max_pos


@pytest.mark.unit
def test_apply_limits_conditional_min_sun_valid(mock_sun_data, mock_logger):
    """Test min_pos applied when sun valid and enable_min_position = True."""
    # enable_min_position = True → only apply when sun valid
    result = PositionConverter.apply_limits(
        value=20,
        min_pos=35,
        max_pos=100,
        apply_min=True,  # Conditional
        apply_max=False,
        sun_valid=True,  # Sun is valid
    )
    assert result == 35  # Should clamp to min_pos


@pytest.mark.unit
def test_apply_limits_conditional_min_sun_not_valid(mock_sun_data, mock_logger):
    """Test min_pos NOT applied when sun not valid and enable_min_position = True."""
    # enable_min_position = True → only apply when sun valid
    result = PositionConverter.apply_limits(
        value=20,
        min_pos=35,
        max_pos=100,
        apply_min=True,  # Conditional
        apply_max=False,
        sun_valid=False,  # Sun not valid
    )
    assert result == 20  # Should NOT clamp, return original value


@pytest.mark.unit
def test_apply_limits_conditional_max_sun_valid(mock_sun_data, mock_logger):
    """Test max_pos applied when sun valid and enable_max_position = True."""
    # enable_max_position = True → only apply when sun valid
    result = PositionConverter.apply_limits(
        value=80,
        min_pos=0,
        max_pos=60,
        apply_min=False,
        apply_max=True,  # Conditional
        sun_valid=True,  # Sun is valid
    )
    assert result == 60  # Should clamp to max_pos


@pytest.mark.unit
def test_apply_limits_conditional_max_sun_not_valid(mock_sun_data, mock_logger):
    """Test max_pos NOT applied when sun not valid and enable_max_position = True."""
    # enable_max_position = True → only apply when sun valid
    result = PositionConverter.apply_limits(
        value=80,
        min_pos=0,
        max_pos=60,
        apply_min=False,
        apply_max=True,  # Conditional
        sun_valid=False,  # Sun not valid
    )
    assert result == 80  # Should NOT clamp, return original value


@pytest.mark.unit
def test_issue_24_sunset_position_with_conditional_min_pos(mock_sun_data, mock_logger):
    """Test Issue #24: sunset_position should be used after sunset, not min_position.

    With the pipeline architecture, DefaultHandler applies position limits
    to snapshot.default_position.  When enable_min_position=True (sun-only),
    min_pos is NOT applied when direct_sun_valid=False (sun not in window).
    After sunset, compute_effective_default() returns sunset_pos=0;
    the DefaultHandler sees direct_sun_valid=False so does not apply min_pos.
    Result should be 0 (sunset_pos), not 35 (min_pos).
    """
    from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
        DefaultHandler,
    )
    from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
    from custom_components.adaptive_cover_pro.pipeline.types import PipelineSnapshot

    with patch(
        "custom_components.adaptive_cover_pro.engine.sun_geometry.datetime"
    ) as mock_datetime:
        mock_datetime.now.return_value = datetime(2024, 1, 1, 20, 0, 0)

        cover = build_vertical_cover(
            logger=mock_logger,
            sol_azi=180.0,
            sol_elev=-10.0,
            sunset_pos=0,
            sunset_off=0,
            sunrise_off=0,
            sun_data=mock_sun_data,
            fov_left=90,
            fov_right=90,
            win_azi=180,
            h_def=60,
            max_pos=100,
            min_pos=35,
            max_pos_bool=False,
            min_pos_bool=True,  # only apply min_pos when sun in window
            blind_spot_left=None,
            blind_spot_right=None,
            blind_spot_elevation=None,
            blind_spot_on=False,
            min_elevation=None,
            max_elevation=None,
            distance=0.5,
            h_win=2.0,
        )

        cover.sun_data.sunset = lambda: datetime(2024, 1, 1, 17, 0, 0)
        cover.sun_data.sunrise = lambda: datetime(2024, 1, 2, 7, 0, 0)

        assert cover.sunset_valid is True
        assert cover.direct_sun_valid is False

        # Build snapshot with effective default = sunset_pos = 0
        snapshot = PipelineSnapshot(
            cover=cover,
            config=cover.config,
            cover_type="cover_blind",
            default_position=0,  # sunset_pos via compute_effective_default
            is_sunset_active=True,
            climate_readings=None,
            climate_mode_enabled=False,
            climate_options=None,
            manual_override_active=False,
            motion_timeout_active=False,
            weather_override_active=False,
            weather_override_position=0,
            weather_bypass_auto_control=True,
            glare_zones=None,
            active_zone_names=frozenset(),
        )

        result = PipelineRegistry([DefaultHandler()]).evaluate(snapshot)

        # min_pos_bool=True means min_pos is only applied when sun is in window.
        # direct_sun_valid=False so min_pos is NOT applied.
        # Result should be sunset_pos (0), NOT min_pos (35).
        assert (
            result.position == 0
        ), f"Expected 0 (sunset_pos), got {result.position} (min_pos applied incorrectly)"


@pytest.mark.unit
def test_sunset_position_with_always_min_pos(mock_sun_data, mock_logger):
    """Test sunset_position with enable_min_position = False (always apply).

    Even when min_pos_bool=False (enable_min_position=False, meaning "always
    apply min_pos"), the sunset position is exempt from min/max clamping (#128).
    The sunset position is an explicit user configuration for nighttime and
    must not be overridden by daytime safety limits.

    After sunset: effective default = sunset_pos = 0.
    min_pos = 35 with always-apply setting.
    Expected: 0 (sunset position wins, limits bypassed).
    """
    from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
        DefaultHandler,
    )
    from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
    from custom_components.adaptive_cover_pro.pipeline.types import PipelineSnapshot

    with patch(
        "custom_components.adaptive_cover_pro.engine.sun_geometry.datetime"
    ) as mock_datetime:
        mock_datetime.now.return_value = datetime(2024, 1, 1, 20, 0, 0)

        cover = build_vertical_cover(
            logger=mock_logger,
            sol_azi=180.0,
            sol_elev=-10.0,
            sunset_pos=0,
            sunset_off=0,
            sunrise_off=0,
            sun_data=mock_sun_data,
            fov_left=90,
            fov_right=90,
            win_azi=180,
            h_def=60,
            max_pos=100,
            min_pos=35,
            max_pos_bool=False,
            min_pos_bool=False,  # always apply min_pos
            blind_spot_left=None,
            blind_spot_right=None,
            blind_spot_elevation=None,
            blind_spot_on=False,
            min_elevation=None,
            max_elevation=None,
            distance=0.5,
            h_win=2.0,
        )

        cover.sun_data.sunset = lambda: datetime(2024, 1, 1, 17, 0, 0)
        cover.sun_data.sunrise = lambda: datetime(2024, 1, 2, 7, 0, 0)

        assert cover.sunset_valid is True
        assert cover.direct_sun_valid is False

        snapshot = PipelineSnapshot(
            cover=cover,
            config=cover.config,
            cover_type="cover_blind",
            default_position=0,  # sunset_pos via compute_effective_default
            is_sunset_active=True,
            climate_readings=None,
            climate_mode_enabled=False,
            climate_options=None,
            manual_override_active=False,
            motion_timeout_active=False,
            weather_override_active=False,
            weather_override_position=0,
            weather_bypass_auto_control=True,
            glare_zones=None,
            active_zone_names=frozenset(),
        )

        result = PipelineRegistry([DefaultHandler()]).evaluate(snapshot)

        # Sunset position is exempt from min/max limits (#128).
        # sunset_pos=0 must not be clamped to min_pos=35.
        assert (
            result.position == 0
        ), f"Expected 0 (sunset position exempt from min_pos), got {result.position}"


# ---------------------------------------------------------------------------
# Issue #467: sun_tracking_min_pos tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_apply_limits_sun_tracking_min_overrides_when_sun_valid():
    """When sun_tracking_min_pos is set AND sun_valid=True, it overrides min_pos."""
    result = PositionConverter.apply_limits(
        value=5,
        min_pos=0,
        max_pos=100,
        apply_min=False,
        apply_max=False,
        sun_valid=True,
        sun_tracking_min_pos=15,
    )
    assert result == 15  # sun-tracking floor wins


@pytest.mark.unit
def test_apply_limits_sun_tracking_min_ignored_when_sun_not_valid():
    """When sun_valid=False, sun_tracking_min_pos is ignored and min_pos applies."""
    result = PositionConverter.apply_limits(
        value=5,
        min_pos=0,
        max_pos=100,
        apply_min=False,
        apply_max=False,
        sun_valid=False,
        sun_tracking_min_pos=15,
    )
    assert result == 5  # min_pos=0 doesn't clamp; sun-tracking floor doesn't apply


@pytest.mark.unit
def test_apply_limits_sun_tracking_min_none_falls_back_to_min_pos():
    """When sun_tracking_min_pos=None (default), behavior matches today exactly."""
    result = PositionConverter.apply_limits(
        value=5,
        min_pos=20,
        max_pos=100,
        apply_min=False,
        apply_max=False,
        sun_valid=True,
        sun_tracking_min_pos=None,
    )
    assert result == 20  # falls through to min_pos


@pytest.mark.unit
def test_apply_limits_sun_tracking_min_zero_is_distinct_from_unset():
    """sun_tracking_min_pos=0 means 'no sun-tracking floor' (not 'unset')."""
    result = PositionConverter.apply_limits(
        value=5,
        min_pos=20,
        max_pos=100,
        apply_min=True,  # min_pos only during sun tracking → applies here
        apply_max=False,
        sun_valid=True,
        sun_tracking_min_pos=0,
    )
    # When sun_tracking_min_pos=0 explicitly, it overrides min_pos=20 during sun-tracking
    assert result == 5


# ---------------------------------------------------------------------------
# Issue #689: summer-close sun-floor bypass tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_apply_limits_suppress_sun_tracking_min_falls_back_to_min_pos():
    """suppress_sun_tracking_min=True ignores the sun-tracking floor even when sun_valid."""
    result = PositionConverter.apply_limits(
        value=5,
        min_pos=0,
        max_pos=100,
        apply_min=False,
        apply_max=False,
        sun_valid=True,
        sun_tracking_min_pos=15,
        suppress_sun_tracking_min=True,
    )
    # Floor suppressed → falls back to min_pos=0 → no clamp → raw 5 reaches through.
    assert result == 5


@pytest.mark.unit
def test_apply_limits_suppress_sun_tracking_min_false_keeps_floor():
    """suppress_sun_tracking_min=False (default behavior) still honors the floor."""
    result = PositionConverter.apply_limits(
        value=5,
        min_pos=0,
        max_pos=100,
        apply_min=False,
        apply_max=False,
        sun_valid=True,
        sun_tracking_min_pos=15,
        suppress_sun_tracking_min=False,
    )
    # Floor active → clamps up to the sun-tracking min of 15.
    assert result == 15


@pytest.mark.unit
def test_direct_sun_valid_uses_and_operator(mock_sun_data, mock_logger):
    """Test that direct_sun_valid uses 'and' operator (not bitwise '&')."""
    # This test verifies the fix for the secondary issue in #24
    with patch(
        "custom_components.adaptive_cover_pro.engine.sun_geometry.datetime"
    ) as mock_datetime:
        # Set current time to daytime (not sunset)
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0)

        cover = build_vertical_cover(
            logger=mock_logger,
            sol_azi=180.0,
            sol_elev=45.0,
            sunset_pos=0,
            sunset_off=0,
            sunrise_off=0,
            sun_data=mock_sun_data,
            fov_left=90,
            fov_right=90,
            win_azi=180,
            h_def=60,
            max_pos=100,
            min_pos=0,
            max_pos_bool=False,
            min_pos_bool=False,
            blind_spot_left=None,
            blind_spot_right=None,
            blind_spot_elevation=None,
            blind_spot_on=False,
            min_elevation=None,
            max_elevation=None,
            distance=0.5,
            h_win=2.0,
        )

        # Mock sun_data methods after cover creation
        cover.sun_data.sunset = lambda: datetime(2024, 1, 1, 17, 0, 0)
        cover.sun_data.sunrise = lambda: datetime(2024, 1, 1, 7, 0, 0)

        # Verify individual components
        assert cover.valid is True  # Sun in FOV
        assert cover.sunset_valid is False  # Not sunset
        assert cover.is_sun_in_blind_spot is False  # No blind spot

        # direct_sun_valid should be True (all conditions met)
        assert cover.direct_sun_valid is True

        # Test that it's using 'and' by verifying the type is bool
        assert isinstance(cover.direct_sun_valid, bool)


# ---------------------------------------------------------------------------
# apply_tilt_limits proportional transform (issue #957)
# ---------------------------------------------------------------------------


def test_apply_tilt_limits_transform_defaults_to_clamp():
    """No ``transform`` arg = today's flat clamp: v=50 on [0,40] → 40."""
    result = PositionConverter.apply_tilt_limits(
        50, 0, 40, False, False, sun_valid=True
    )
    assert result == 40


def test_apply_tilt_limits_proportional_remaps_into_band():
    """Proportional: v=50 on [0,40] → 20 (reporter's worked example)."""
    from custom_components.adaptive_cover_pro.const import (
        VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
    )

    result = PositionConverter.apply_tilt_limits(
        50,
        0,
        40,
        False,
        False,
        sun_valid=True,
        transform=VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
    )
    assert result == 20


def test_apply_tilt_limits_proportional_identity_on_full_band():
    """Proportional on [0,100] is the identity across the range."""
    from custom_components.adaptive_cover_pro.const import (
        VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
    )

    for v in (0, 25, 50, 75, 100):
        assert (
            PositionConverter.apply_tilt_limits(
                v,
                0,
                100,
                False,
                False,
                sun_valid=True,
                transform=VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
            )
            == v
        )


def test_apply_tilt_limits_proportional_is_monotonic_ascending():
    """Proportional remap is monotonic non-decreasing across a 0–100 sweep."""
    from custom_components.adaptive_cover_pro.const import (
        VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
    )

    results = [
        PositionConverter.apply_tilt_limits(
            v,
            0,
            40,
            False,
            False,
            sun_valid=True,
            transform=VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
        )
        for v in range(0, 101)
    ]
    assert all(b >= a for a, b in zip(results, results[1:]))
    assert results[0] == 0
    assert results[-1] == 40


def test_apply_tilt_limits_proportional_v_zero_maps_to_lo():
    """Proportional: v=0 on [10,40] → the band floor 10."""
    from custom_components.adaptive_cover_pro.const import (
        VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
    )

    result = PositionConverter.apply_tilt_limits(
        0,
        10,
        40,
        False,
        False,
        sun_valid=True,
        transform=VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
    )
    assert result == 10


def test_apply_tilt_limits_proportional_reversed_band_degrades_to_clamp():
    """Reversed band (min>max) must NOT ramp descending — fall back to clamp.

    With min=50, max=30 the proportional formula would produce a descending
    ramp (more sun → less tilt), diverging from clamp. The degenerate guard
    (hi <= lo) instead takes the clamp path so proportional == clamp here.
    """
    from custom_components.adaptive_cover_pro.const import (
        VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
    )

    for v in (0, 50, 100):
        proportional = PositionConverter.apply_tilt_limits(
            v,
            50,
            30,
            False,
            False,
            sun_valid=True,
            transform=VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
        )
        clamp = PositionConverter.apply_tilt_limits(
            v, 50, 30, False, False, sun_valid=True
        )
        assert proportional == clamp


def test_apply_tilt_limits_proportional_equal_band_is_constant():
    """Equal band (min==max) returns that constant for any v, matching clamp."""
    from custom_components.adaptive_cover_pro.const import (
        VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
    )

    for v in (0, 25, 50, 75, 100):
        proportional = PositionConverter.apply_tilt_limits(
            v,
            40,
            40,
            False,
            False,
            sun_valid=True,
            transform=VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
        )
        clamp = PositionConverter.apply_tilt_limits(
            v, 40, 40, False, False, sun_valid=True
        )
        assert proportional == clamp == 40


def test_apply_tilt_limits_proportional_with_sun_invalid_falls_back_to_clamp():
    """Proportional + sun_valid=False must fall through to clamp behavior.

    Characterization test locking the existing ``and sun_valid`` guard: with
    the sun not tracked, the proportional remap is NOT applied — the result
    equals the clamp-mode result for the same inputs.
    """
    from custom_components.adaptive_cover_pro.const import (
        VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
    )

    for v in (0, 50, 100):
        proportional = PositionConverter.apply_tilt_limits(
            v,
            10,
            40,
            False,
            False,
            sun_valid=False,
            transform=VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
        )
        clamp = PositionConverter.apply_tilt_limits(
            v, 10, 40, False, False, sun_valid=False
        )
        assert proportional == clamp
