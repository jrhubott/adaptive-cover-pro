"""Tests for position sensor behavior outside the start_time/end_time window.

The pipeline now always runs regardless of time window.  Outside the window
the engine's .default property returns:
  - h_def  (default_height) when before actual sunset
  - sunset_pos              when past actual sunset (sunset_valid=True)

DefaultHandler uses cover.default, so the pipeline naturally produces the
correct position for sensor display.  Cover *commands* outside the window are
blocked by the in_time_window gate in CoverCommandService; only explicit
timed triggers (end-time, sunset) use force=True to bypass that gate.
"""

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

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
    """Create a plain MagicMock coordinator with real method/property bindings."""
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

    coordinator._calculate_cover_state = (
        AdaptiveDataUpdateCoordinator._calculate_cover_state.__get__(coordinator)
    )
    type(coordinator).state = AdaptiveDataUpdateCoordinator.state

    return coordinator


class TestOutsideTimeWindowBeforeSunset:
    """Outside time window but before actual sunset — should use default_height."""

    def test_after_end_time_before_sunset_reports_default_height(self, cover_in_fov):
        """After end_time but before sunset, sensor should show default_height."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False

        with patch.object(
            type(cover_in_fov), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            options = {CONF_SUNSET_POS: 10, CONF_DEFAULT_HEIGHT: 50}
            result = coordinator._calculate_cover_state(cover_in_fov, options)

        assert result == 50
        assert coordinator.control_method == ControlMethod.DEFAULT

    def test_before_start_time_before_sunset_reports_default_height(self, cover_in_fov):
        """Before start_time and before sunset, sensor should show default_height."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False

        with patch.object(
            type(cover_in_fov), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            options = {CONF_SUNSET_POS: 10, CONF_DEFAULT_HEIGHT: 50}
            result = coordinator._calculate_cover_state(cover_in_fov, options)

        assert result == 50

    def test_no_sunset_pos_before_sunset_uses_default_height(self, cover_in_fov):
        """When sunset_pos is None and before sunset, should use default_height."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False

        with patch.object(
            type(cover_in_fov), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            options = {CONF_SUNSET_POS: None, CONF_DEFAULT_HEIGHT: 50}
            result = coordinator._calculate_cover_state(cover_in_fov, options)

        assert result == 50

    def test_no_default_height_no_sunset_pos_falls_back_to_zero(self, cover_in_fov):
        """When both are missing and before sunset, should use 0."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False

        with patch.object(
            type(cover_in_fov), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            result = coordinator._calculate_cover_state(cover_in_fov, {})

        assert result == 0


class TestOutsideTimeWindowAfterSunset:
    """Outside time window and past actual sunset — should use sunset_pos."""

    def test_after_end_time_past_sunset_reports_sunset_pos(self, cover_in_fov):
        """After end_time and past sunset, sensor should show sunset_pos."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False

        with patch.object(
            type(cover_in_fov), "sunset_valid", new_callable=PropertyMock, return_value=True
        ):
            options = {CONF_SUNSET_POS: 10, CONF_DEFAULT_HEIGHT: 50}
            result = coordinator._calculate_cover_state(cover_in_fov, options)

        assert result == 10
        assert coordinator.control_method == ControlMethod.DEFAULT

    def test_past_sunset_no_sunset_pos_falls_back_to_default_height(self, cover_in_fov):
        """Past sunset but no sunset_pos configured — engine default returns h_def."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False

        with patch.object(
            type(cover_in_fov), "sunset_valid", new_callable=PropertyMock, return_value=True
        ):
            # sunset_pos=None means engine .default stays at h_def even when sunset_valid
            options = {CONF_SUNSET_POS: None, CONF_DEFAULT_HEIGHT: 50}
            # engine.default returns h_def when sunset_pos is None (see sun_geometry.py)
            result = coordinator._calculate_cover_state(cover_in_fov, options)

        # h_def=50 from cover_in_fov fixture (sunset_pos not set on config)
        assert result == 50

    def test_sunset_pos_zero_past_sunset(self, cover_in_fov):
        """sunset_pos=0 past sunset — should return 0, not fall back to default_height."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False

        with patch.object(
            type(cover_in_fov), "sunset_valid", new_callable=PropertyMock, return_value=True
        ):
            options = {CONF_SUNSET_POS: 0, CONF_DEFAULT_HEIGHT: 50}
            # cover_in_fov has sunset_pos=0 in its config (fixture default)
            result = coordinator._calculate_cover_state(cover_in_fov, options)

        assert result == 0


class TestWithinTimeWindow:
    """Normal behavior inside the time window is unchanged."""

    def test_within_window_before_sunset_uses_sun_calculation(self, cover_in_fov):
        """Within time window before sunset, should use normal sun-based calculation."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = True

        with patch.object(
            type(cover_in_fov), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            options = {CONF_SUNSET_POS: 0, CONF_DEFAULT_HEIGHT: 50}
            result = coordinator._calculate_cover_state(cover_in_fov, options)

        assert result > 0
        assert coordinator.control_method == ControlMethod.SOLAR

    def test_within_window_past_sunset_uses_sunset_pos(self, cover_in_fov):
        """Within time window but past actual sunset — engine uses sunset_pos as default."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = True

        with patch.object(
            type(cover_in_fov), "sunset_valid", new_callable=PropertyMock, return_value=True
        ), patch.object(
            type(cover_in_fov), "direct_sun_valid", new_callable=PropertyMock, return_value=False
        ):
            options = {CONF_SUNSET_POS: 25, CONF_DEFAULT_HEIGHT: 50}
            result = coordinator._calculate_cover_state(cover_in_fov, options)

        # sunset_valid=True → engine.default returns sunset_pos → DefaultHandler returns it
        # cover_in_fov was built with sunset_pos=0 in its config object, so engine uses that
        assert coordinator.control_method == ControlMethod.DEFAULT


class TestOutsideTimeWindowWithOverrides:
    """Priority overrides work correctly outside the time window (pipeline always runs)."""

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

        with patch.object(
            type(cover_in_fov), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            options = {CONF_SUNSET_POS: 0, CONF_DEFAULT_HEIGHT: 50}
            result = coordinator._calculate_cover_state(cover_in_fov, options)

        assert result == 75

    def test_motion_timeout_outside_window_before_sunset_uses_default_height(self, cover_in_fov):
        """Motion timeout outside window before sunset returns default_height."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False
        coordinator.is_force_override_active = False
        coordinator.is_motion_timeout_active = True

        with patch.object(
            type(cover_in_fov), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            options = {CONF_SUNSET_POS: 5, CONF_DEFAULT_HEIGHT: 50}
            result = coordinator._calculate_cover_state(cover_in_fov, options)

        # cover.default = h_def (50) when before sunset
        assert result == 50


class TestControlMethodOutsideWindow:
    """control_method is DEFAULT when outside time window."""

    def test_control_method_is_default_outside_window_before_sunset(self, cover_in_fov):
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False

        with patch.object(
            type(cover_in_fov), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            coordinator._calculate_cover_state(cover_in_fov, {CONF_DEFAULT_HEIGHT: 50})

        assert coordinator.control_method == ControlMethod.DEFAULT

    def test_control_method_is_default_outside_window_after_sunset(self, cover_in_fov):
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False

        with patch.object(
            type(cover_in_fov), "sunset_valid", new_callable=PropertyMock, return_value=True
        ):
            coordinator._calculate_cover_state(cover_in_fov, {CONF_SUNSET_POS: 10})

        assert coordinator.control_method == ControlMethod.DEFAULT


class TestInverseStateOutsideWindow:
    """inverse_state is applied via the state property regardless of time window."""

    def test_inverse_state_before_sunset_outside_window(self, cover_in_fov):
        """inverse_state applied to default_height when before sunset."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False
        coordinator._inverse_state = True

        with patch.object(
            type(cover_in_fov), "sunset_valid", new_callable=PropertyMock, return_value=False
        ):
            options = {CONF_SUNSET_POS: 0, CONF_DEFAULT_HEIGHT: 50}
            result = coordinator._calculate_cover_state(cover_in_fov, options)

        # inverse_state: 100 - 50 = 50
        assert result == 50

    def test_inverse_state_after_sunset_outside_window(self, cover_in_fov):
        """inverse_state applied to sunset_pos when past sunset."""
        coordinator = make_coordinator()
        coordinator.check_adaptive_time = False
        coordinator._inverse_state = True

        with patch.object(
            type(cover_in_fov), "sunset_valid", new_callable=PropertyMock, return_value=True
        ):
            options = {CONF_SUNSET_POS: 0, CONF_DEFAULT_HEIGHT: 50}
            # cover_in_fov has sunset_pos=0 → engine.default=0 → inverse: 100-0=100
            result = coordinator._calculate_cover_state(cover_in_fov, options)

        assert result == 100


class TestSunsetTriggerScheduling:
    """async_schedule_sunset_trigger schedules correctly."""

    @pytest.mark.asyncio
    async def test_no_schedule_when_no_sunset_pos(self):
        """No sunset trigger scheduled when sunset_pos is not configured."""
        coordinator = MagicMock()
        coordinator.logger = MagicMock()
        coordinator._scheduled_sunset_time = None
        coordinator._sunset_listener = None
        coordinator._sun_provider = MagicMock()
        coordinator._async_cancel_sunset_listener = MagicMock()

        bound = AdaptiveDataUpdateCoordinator.async_schedule_sunset_trigger.__get__(
            coordinator
        )
        await bound({CONF_SUNSET_POS: None})

        coordinator._async_cancel_sunset_listener.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_schedule_when_sunset_already_passed(self):
        """No sunset trigger scheduled when sunset time has already passed today."""
        import datetime as dt
        import pytz

        coordinator = MagicMock()
        coordinator.logger = MagicMock()
        coordinator._scheduled_sunset_time = None
        coordinator._sunset_listener = None
        coordinator._async_cancel_sunset_listener = MagicMock()

        # Mock sun_data returning a past sunset time
        past_sunset = dt.datetime.utcnow() - dt.timedelta(hours=2)
        sun_data = MagicMock()
        sun_data.sunset.return_value = past_sunset
        coordinator._sun_provider = MagicMock()
        coordinator._sun_provider.create_sun_data.return_value = sun_data
        coordinator.hass = MagicMock()
        coordinator.hass.config.time_zone = "UTC"

        bound = AdaptiveDataUpdateCoordinator.async_schedule_sunset_trigger.__get__(
            coordinator
        )
        await bound({CONF_SUNSET_POS: 20})

        # Should log and return without scheduling
        coordinator._async_cancel_sunset_listener.assert_not_called()

    @pytest.mark.asyncio
    async def test_schedule_when_sunset_in_future(self):
        """Sunset trigger is scheduled when sunset_pos configured and sunset is future."""
        import datetime as dt

        coordinator = MagicMock()
        coordinator.logger = MagicMock()
        coordinator._scheduled_sunset_time = None
        coordinator._sunset_listener = None
        coordinator._async_cancel_sunset_listener = MagicMock()

        # Mock sun_data returning a future sunset time
        future_sunset = dt.datetime.utcnow() + dt.timedelta(hours=2)
        sun_data = MagicMock()
        sun_data.sunset.return_value = future_sunset
        coordinator._sun_provider = MagicMock()
        coordinator._sun_provider.create_sun_data.return_value = sun_data
        coordinator.hass = MagicMock()
        coordinator.hass.config.time_zone = "UTC"

        with patch(
            "custom_components.adaptive_cover_pro.coordinator.async_track_point_in_time"
        ) as mock_track:
            mock_track.return_value = MagicMock()
            bound = AdaptiveDataUpdateCoordinator.async_schedule_sunset_trigger.__get__(
                coordinator
            )
            await bound({CONF_SUNSET_POS: 20, "sunset_offset": 0})

        mock_track.assert_called_once()
        assert coordinator._scheduled_sunset_time is not None

    @pytest.mark.asyncio
    async def test_no_reschedule_when_already_scheduled_for_same_time(self):
        """Does not reschedule if sunset trigger already set for same time."""
        import datetime as dt
        import pytz

        coordinator = MagicMock()
        coordinator.logger = MagicMock()
        coordinator._async_cancel_sunset_listener = MagicMock()

        future_sunset = dt.datetime.utcnow() + dt.timedelta(hours=2)
        sun_data = MagicMock()
        sun_data.sunset.return_value = future_sunset
        coordinator._sun_provider = MagicMock()
        coordinator._sun_provider.create_sun_data.return_value = sun_data
        coordinator.hass = MagicMock()
        coordinator.hass.config.time_zone = "UTC"

        # Pre-set scheduled time to match what would be computed
        local_tz = pytz.utc
        trigger_local = pytz.utc.localize(future_sunset).astimezone(local_tz)
        coordinator._scheduled_sunset_time = trigger_local

        with patch(
            "custom_components.adaptive_cover_pro.coordinator.async_track_point_in_time"
        ) as mock_track:
            bound = AdaptiveDataUpdateCoordinator.async_schedule_sunset_trigger.__get__(
                coordinator
            )
            await bound({CONF_SUNSET_POS: 20, "sunset_offset": 0})

        mock_track.assert_not_called()


class TestTimedRefreshUsesDefaultHeight:
    """async_handle_timed_refresh now commands default_height at end time."""

    @pytest.mark.asyncio
    async def test_timed_refresh_commands_default_height(self):
        """End-time timed refresh should command default_height, not sunset_pos."""
        coordinator = MagicMock()
        coordinator.logger = MagicMock()
        coordinator._inverse_state = False
        coordinator.entities = ["cover.test"]
        coordinator.timed_refresh = True
        coordinator._cmd_svc = MagicMock()
        coordinator._cmd_svc.apply_position = AsyncMock()

        ctx = MagicMock()
        coordinator._build_position_context.return_value = ctx

        bound = AdaptiveDataUpdateCoordinator.async_handle_timed_refresh.__get__(
            coordinator
        )
        options = {CONF_DEFAULT_HEIGHT: 40, CONF_SUNSET_POS: 10}
        await bound(options)

        coordinator._cmd_svc.apply_position.assert_called_once_with(
            "cover.test", 40, "end_time", context=ctx
        )
        assert coordinator.timed_refresh is False

    @pytest.mark.asyncio
    async def test_timed_refresh_uses_zero_when_no_default_height(self):
        """End-time timed refresh falls back to 0 when default_height not set."""
        coordinator = MagicMock()
        coordinator.logger = MagicMock()
        coordinator._inverse_state = False
        coordinator.entities = ["cover.test"]
        coordinator.timed_refresh = True
        coordinator._cmd_svc = MagicMock()
        coordinator._cmd_svc.apply_position = AsyncMock()

        ctx = MagicMock()
        coordinator._build_position_context.return_value = ctx

        bound = AdaptiveDataUpdateCoordinator.async_handle_timed_refresh.__get__(
            coordinator
        )
        await bound({})

        coordinator._cmd_svc.apply_position.assert_called_once_with(
            "cover.test", 0, "end_time", context=ctx
        )

    @pytest.mark.asyncio
    async def test_timed_refresh_applies_inverse_state(self):
        """inverse_state is applied to default_height in timed refresh."""
        coordinator = MagicMock()
        coordinator.logger = MagicMock()
        coordinator._inverse_state = True
        coordinator.entities = ["cover.test"]
        coordinator.timed_refresh = True
        coordinator._cmd_svc = MagicMock()
        coordinator._cmd_svc.apply_position = AsyncMock()

        ctx = MagicMock()
        coordinator._build_position_context.return_value = ctx

        bound = AdaptiveDataUpdateCoordinator.async_handle_timed_refresh.__get__(
            coordinator
        )
        await bound({CONF_DEFAULT_HEIGHT: 30})

        # inverse_state(30) = 100 - 30 = 70
        coordinator._cmd_svc.apply_position.assert_called_once_with(
            "cover.test", 70, "end_time", context=ctx
        )
