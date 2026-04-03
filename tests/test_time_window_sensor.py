"""Tests for position sensor behavior outside the start_time/end_time window.

Verifies that when the current time is outside the configured time window,
the position sensor reports the sunset/default position (matching what the
cover was actually commanded to) instead of the stale sun-calculated value.

Fixes GitHub issue #66.
"""

from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_DEFAULT_HEIGHT,
    CONF_SUNSET_POS,
)
from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
    ClimateHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
    DefaultHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.force_override import (
    ForceOverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.manual_override import (
    ManualOverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.motion_timeout import (
    MotionTimeoutHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.solar import SolarHandler
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry


@pytest.fixture
def cover_in_fov(mock_sun_data, mock_logger):
    """Create a vertical cover with sun directly in FOV (would calculate a tracking position)."""
    from tests.cover_helpers import build_vertical_cover

    return build_vertical_cover(
        logger=mock_logger,
        sol_azi=180.0,
        sol_elev=30.0,
        sunset_pos=0,
        sunset_off=0,
        sunrise_off=0,
        sun_data=mock_sun_data,
        fov_left=45,
        fov_right=45,
        win_azi=180,
        h_def=50,
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


def make_coordinator(extra_attrs=None):
    """Create a plain MagicMock coordinator with real method/property bindings.

    Uses a plain MagicMock (no spec) so attributes can be freely set.
    Binds the real _calculate_cover_state method and state property.
    """
    coordinator = MagicMock()
    coordinator.logger = MagicMock()
    coordinator._climate_mode = False
    coordinator._toggles = MagicMock()
    coordinator._toggles.switch_mode = False
    coordinator._use_interpolation = False
    coordinator._inverse_state = False
    coordinator.is_force_override_active = False
    coordinator.is_weather_override_active = False
    coordinator.is_motion_timeout_active = False
    coordinator.manager = MagicMock()
    coordinator.manager.binary_cover_manual = False
    coordinator.climate_state = 50
    coordinator.climate_data = None
    coordinator.default_state = 0
    coordinator.raw_calculated_position = 0
    coordinator.control_method = ControlMethod.SOLAR
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {}
    coordinator._pipeline_result = None
    coordinator._pipeline = PipelineRegistry(
        [
            ForceOverrideHandler(),
            MotionTimeoutHandler(),
            ManualOverrideHandler(),
            ClimateHandler(),
            SolarHandler(),
            DefaultHandler(),
        ]
    )

    if extra_attrs:
        for k, v in extra_attrs.items():
            setattr(coordinator, k, v)

    # Bind the real _calculate_cover_state method
    coordinator._calculate_cover_state = (
        AdaptiveDataUpdateCoordinator._calculate_cover_state.__get__(coordinator)
    )

    # Bind the real state property to the mock's type (each instance gets its own type)
    type(coordinator).state = AdaptiveDataUpdateCoordinator.state

    return coordinator


class TestOutsideTimeWindowWithSunsetPos:
    """Tests for position sensor when outside time window with sunset_pos configured."""

    def test_after_end_time_reports_sunset_pos(self, cover_in_fov):
        """After end_time, sensor should show sunset_pos, not sun-calculated value."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False

        options = {CONF_SUNSET_POS: 0, CONF_DEFAULT_HEIGHT: 50}
        result = coordinator._calculate_cover_state(cover_in_fov, options)

        assert coordinator.raw_calculated_position == 0
        assert coordinator.default_state == 0
        assert coordinator.control_method == ControlMethod.DEFAULT
        assert result == 0

    def test_before_start_time_reports_sunset_pos(self, cover_in_fov):
        """Before start_time, sensor should show sunset_pos."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False

        options = {CONF_SUNSET_POS: 10, CONF_DEFAULT_HEIGHT: 50}
        result = coordinator._calculate_cover_state(cover_in_fov, options)

        assert coordinator.raw_calculated_position == 10
        assert coordinator.default_state == 10
        assert result == 10

    def test_sunset_pos_zero_not_treated_as_none(self, cover_in_fov):
        """sunset_pos=0 is a valid value and should not fall back to default_height."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False

        options = {CONF_SUNSET_POS: 0, CONF_DEFAULT_HEIGHT: 50}
        result = coordinator._calculate_cover_state(cover_in_fov, options)

        assert coordinator.raw_calculated_position == 0
        assert result == 0


class TestOutsideTimeWindowWithoutSunsetPos:
    """Tests for position sensor when outside time window without sunset_pos."""

    def test_no_sunset_pos_falls_back_to_default_height(self, cover_in_fov):
        """When sunset_pos is None, should use default_height."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False

        options = {CONF_SUNSET_POS: None, CONF_DEFAULT_HEIGHT: 50}
        result = coordinator._calculate_cover_state(cover_in_fov, options)

        assert coordinator.raw_calculated_position == 50
        assert coordinator.default_state == 50
        assert result == 50

    def test_no_sunset_pos_no_default_height_falls_back_to_zero(self, cover_in_fov):
        """When both sunset_pos and default_height are missing, should use 0."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False

        options = {}
        result = coordinator._calculate_cover_state(cover_in_fov, options)

        assert coordinator.raw_calculated_position == 0
        assert result == 0


class TestWithinTimeWindow:
    """Tests verifying normal behavior is unchanged when within the time window."""

    def test_within_window_uses_sun_calculation(self, cover_in_fov):
        """Within time window, should use normal sun-based calculation."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = True

        with patch.object(
            type(cover_in_fov),
            "sunset_valid",
            new_callable=PropertyMock,
            return_value=False,
        ):
            options = {CONF_SUNSET_POS: 0, CONF_DEFAULT_HEIGHT: 50}
            result = coordinator._calculate_cover_state(cover_in_fov, options)

        # The sun-calculated position should NOT be sunset_pos (0)
        assert result > 0
        assert coordinator.control_method == ControlMethod.SOLAR

    def test_no_time_window_configured_uses_sun_calculation(self, cover_in_fov):
        """When no start/end time configured, check_adaptive_time=True, normal behavior."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = True

        with patch.object(
            type(cover_in_fov),
            "sunset_valid",
            new_callable=PropertyMock,
            return_value=False,
        ):
            options = {CONF_SUNSET_POS: 0, CONF_DEFAULT_HEIGHT: 50}
            result = coordinator._calculate_cover_state(cover_in_fov, options)

        assert result > 0


class TestOutsideTimeWindowWithOverrides:
    """Tests for priority behavior when outside time window with overrides active."""

    def test_force_override_takes_precedence_outside_window(self, cover_in_fov):
        """Force override should take precedence even outside time window."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False
        coordinator.is_force_override_active = True
        coordinator.config_entry.options = {
            "force_override_position": 75,
            CONF_SUNSET_POS: 0,
            CONF_DEFAULT_HEIGHT: 50,
        }

        options = {CONF_SUNSET_POS: 0, CONF_DEFAULT_HEIGHT: 50}
        result = coordinator._calculate_cover_state(cover_in_fov, options)

        assert result == 75

    def test_motion_timeout_uses_correct_default_outside_window(self, cover_in_fov):
        """Motion timeout should use the outside-window default_state."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False
        coordinator.is_force_override_active = False
        coordinator.is_motion_timeout_active = True

        options = {CONF_SUNSET_POS: 5, CONF_DEFAULT_HEIGHT: 50}
        result = coordinator._calculate_cover_state(cover_in_fov, options)

        # Motion timeout returns default_state which is now set to sunset_pos (5)
        assert coordinator.default_state == 5
        assert result == 5


class TestOutsideTimeWindowControlMethod:
    """Tests for control_method when outside time window."""

    def test_control_method_is_default_outside_window(self, cover_in_fov):
        """control_method should be DEFAULT when outside time window."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False

        options = {CONF_SUNSET_POS: 0, CONF_DEFAULT_HEIGHT: 50}
        coordinator._calculate_cover_state(cover_in_fov, options)

        assert coordinator.control_method == ControlMethod.DEFAULT

    def test_raw_calculated_position_matches_outside_window_pos(self, cover_in_fov):
        """raw_calculated_position should be set to the outside-window position."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False

        options = {CONF_SUNSET_POS: 15, CONF_DEFAULT_HEIGHT: 50}
        coordinator._calculate_cover_state(cover_in_fov, options)

        assert coordinator.raw_calculated_position == 15


class TestOutsideTimeWindowInverseState:
    """Tests for inverse_state behavior outside time window."""

    def test_inverse_state_applied_outside_window(self, cover_in_fov):
        """inverse_state should be applied to the outside-window position by state property."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False
        coordinator._inverse_state = True

        options = {CONF_SUNSET_POS: 0, CONF_DEFAULT_HEIGHT: 50}
        result = coordinator._calculate_cover_state(cover_in_fov, options)

        # inverse_state flips: 100 - 0 = 100
        assert result == 100
        # raw_calculated_position is set before inverse is applied
        assert coordinator.raw_calculated_position == 0

    def test_inverse_state_non_zero_outside_window(self, cover_in_fov):
        """inverse_state applied to non-zero outside-window position."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False
        coordinator._inverse_state = True

        options = {CONF_SUNSET_POS: 30, CONF_DEFAULT_HEIGHT: 50}
        result = coordinator._calculate_cover_state(cover_in_fov, options)

        # inverse_state flips: 100 - 30 = 70
        assert result == 70
        assert coordinator.raw_calculated_position == 30
