"""End-to-end integration tests: state → calculation → pipeline → diagnostics."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

from custom_components.adaptive_cover_pro.calculation import NormalCoverState
from tests.conftest import make_snapshot_for_cover
from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
    ClimateCoverData,
    ClimateCoverState,
)
from custom_components.adaptive_cover_pro.config_context_adapter import (
    ConfigContextAdapter,
)
from custom_components.adaptive_cover_pro.diagnostics.builder import (
    DiagnosticContext,
    DiagnosticsBuilder,
)
from custom_components.adaptive_cover_pro.enums import ClimateStrategy, ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers import (
    ClimateHandler,
    DefaultHandler,
    ForceOverrideHandler,
    ManualOverrideHandler,
    MotionTimeoutHandler,
    SolarHandler,
    WeatherOverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.cloud_suppression import (
    CloudSuppressionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import (
    ClimateOptions,
    CustomPositionSensorState,
    PipelineSnapshot,
)
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings
from custom_components.adaptive_cover_pro.sun import SunData

from .cover_helpers import (
    build_horizontal_cover,
    build_tilt_cover,
    build_vertical_cover,
)

_NOON = datetime(2024, 6, 15, 12, 0, 0)
_SUNSET = datetime(2024, 6, 15, 20, 0, 0)
_SUNRISE = datetime(2024, 6, 15, 5, 0, 0)

_DATETIME_PATCH = "custom_components.adaptive_cover_pro.engine.sun_geometry.datetime"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger() -> MagicMock:
    """Spec'd mock of ConfigContextAdapter."""
    logger = MagicMock(spec=ConfigContextAdapter)
    logger.debug = Mock()
    logger.info = Mock()
    logger.warning = Mock()
    logger.error = Mock()
    return logger


def _make_sun_data() -> MagicMock:
    """Spec'd mock of SunData; sunset/sunrise return real datetimes."""
    sun_data = MagicMock(spec=SunData)
    sun_data.timezone = "UTC"
    sun_data.sunset.return_value = _SUNSET
    sun_data.sunrise.return_value = _SUNRISE
    return sun_data


def _make_climate_data(**overrides) -> ClimateCoverData:
    """Real ClimateCoverData with sensible defaults; accepts field overrides."""
    defaults = {
        "temp_low": 20.0,
        "temp_high": 25.0,
        "temp_switch": True,
        "blind_type": "cover_blind",
        "transparent_blind": False,
        "temp_summer_outside": 22.0,
        "outside_temperature": 22.5,
        "inside_temperature": None,
        "is_presence": True,
        "is_sunny": True,
        "lux_below_threshold": False,
        "irradiance_below_threshold": False,
        "winter_close_insulation": False,
    }
    defaults.update(overrides)
    return ClimateCoverData(**defaults)


def _make_pipeline() -> PipelineRegistry:
    """Real PipelineRegistry with all six exported handler classes."""
    return PipelineRegistry(
        [
            ForceOverrideHandler(),
            MotionTimeoutHandler(),
            ManualOverrideHandler(),
            ClimateHandler(),
            SolarHandler(),
            DefaultHandler(),
        ]
    )


def _build_pipeline_snapshot(
    cover,
    *,
    climate_mode_enabled: bool = False,
    climate_readings: ClimateReadings | None = None,
    climate_options: ClimateOptions | None = None,
    force_override_sensors: dict[str, bool] | None = None,
    force_override_position: int = 0,
    motion_timeout_active: bool = False,
    manual_override_active: bool = False,
    weather_override_active: bool = False,
    weather_override_position: int = 0,
    cover_type: str = "cover_blind",
) -> PipelineSnapshot:
    """Build a PipelineSnapshot from a real cover instance."""
    return PipelineSnapshot(
        cover=cover,
        config=cover.config,
        cover_type=cover_type,
        default_position=int(cover.config.h_def),
        is_sunset_active=False,
        climate_readings=climate_readings,
        climate_mode_enabled=climate_mode_enabled,
        climate_options=climate_options,
        force_override_sensors=force_override_sensors or {},
        force_override_position=force_override_position,
        manual_override_active=manual_override_active,
        motion_timeout_active=motion_timeout_active,
        weather_override_active=weather_override_active,
        weather_override_position=weather_override_position,
        glare_zones=None,
        active_zone_names=set(),
    )


def _build_diagnostic_context(
    cover,
    pipeline_result,
    *,
    climate_mode: bool = False,
    force_override_position: int = 0,
    final_state: int = 0,
) -> DiagnosticContext:
    """Build a DiagnosticContext from real cover and pipeline result objects."""
    return DiagnosticContext(
        pos_sun=[cover.sol_azi, cover.sol_elev],
        cover=cover,
        pipeline_result=pipeline_result,
        climate_mode=climate_mode,
        check_adaptive_time=True,
        after_start_time=True,
        before_end_time=True,
        start_time=None,
        end_time=None,
        automatic_control=True,
        final_state=final_state,
        force_override_position=force_override_position,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEndToEndIntegration:
    """Full pipeline: state snapshot → calculation → pipeline → diagnostics."""

    def test_vertical_sun_tracking(self):
        """Sun directly in front of south-facing window → SolarHandler wins."""
        logger = _make_logger()
        sun_data = _make_sun_data()

        with patch(_DATETIME_PATCH) as mock_dt:
            mock_dt.now.return_value = _NOON

            cover = build_vertical_cover(
                logger=logger,
                sol_azi=180.0,
                sol_elev=45.0,
                sun_data=sun_data,
                win_azi=180,
                fov_left=45,
                fov_right=45,
                h_def=50,
                distance=0.5,
                h_win=2.0,
                sunset_pos=0,
                sunset_off=0,
                sunrise_off=0,
                max_pos=100,
                min_pos=0,
            )

            normal_state = NormalCoverState(cover=cover)
            assert cover.direct_sun_valid is True
            calc_pos = normal_state.get_state()
            assert 0 <= calc_pos <= 100

            pipeline = _make_pipeline()
            snapshot = _build_pipeline_snapshot(cover, cover_type="cover_blind")
            result = pipeline.evaluate(snapshot)

            assert result.control_method == ControlMethod.SOLAR
            assert 0 <= result.position <= 100
            assert result.decision_trace
            winning = next(s for s in result.decision_trace if s.matched)
            assert winning.handler == SolarHandler().name

            diag_ctx = _build_diagnostic_context(cover, result)
            diag_dict, explanation = DiagnosticsBuilder().build(diag_ctx)

            assert diag_dict["sun_azimuth"] == 180.0
            assert diag_dict["sun_elevation"] == 45.0
            assert "gamma" in diag_dict
            assert diag_dict["control_status"] == "active"
            assert "sun" in explanation.lower() and "position" in explanation.lower()

    def test_sun_outside_fov_uses_default(self):
        """Sun at east azimuth (90°) is outside south window FOV → default position."""
        logger = _make_logger()
        sun_data = _make_sun_data()

        with patch(_DATETIME_PATCH) as mock_dt:
            mock_dt.now.return_value = _NOON

            cover = build_vertical_cover(
                logger=logger,
                sol_azi=90.0,
                sol_elev=45.0,
                sun_data=sun_data,
                win_azi=180,
                fov_left=45,
                fov_right=45,
                h_def=50,
                distance=0.5,
                h_win=2.0,
                sunset_pos=0,
                sunset_off=0,
                sunrise_off=0,
                max_pos=100,
                min_pos=0,
            )

            normal_state = NormalCoverState(cover=cover)
            assert cover.direct_sun_valid is False
            calc_pos = normal_state.get_state()
            assert calc_pos == 50  # h_def

            pipeline = _make_pipeline()
            snapshot = _build_pipeline_snapshot(cover, cover_type="cover_blind")
            result = pipeline.evaluate(snapshot)

            assert result.control_method == ControlMethod.DEFAULT
            assert result.position == 50
            assert result.decision_trace
            winning = next(s for s in result.decision_trace if s.matched)
            assert winning.handler == DefaultHandler().name

            diag_ctx = _build_diagnostic_context(cover, result)
            diag_dict, explanation = DiagnosticsBuilder().build(diag_ctx)

            assert diag_dict["sun_azimuth"] == 90.0
            assert diag_dict["control_status"] == "sun_not_visible"
            assert "default position" in explanation.lower()

    def test_climate_winter_heating(self):
        """Cold outside temp + sun in FOV → ClimateHandler opens blind fully (100%)."""
        logger = _make_logger()
        sun_data = _make_sun_data()

        with patch(_DATETIME_PATCH) as mock_dt:
            mock_dt.now.return_value = _NOON

            cover = build_vertical_cover(
                logger=logger,
                sol_azi=180.0,
                sol_elev=45.0,
                sun_data=sun_data,
                win_azi=180,
                fov_left=45,
                fov_right=45,
                h_def=50,
                distance=0.5,
                h_win=2.0,
                sunset_pos=0,
                sunset_off=0,
                sunrise_off=0,
                max_pos=100,
                min_pos=0,
            )

            climate_data = _make_climate_data(
                outside_temperature=15.0,  # below temp_low=20 → winter
                is_sunny=True,
                is_presence=True,
            )

            climate_state = ClimateCoverState(
                make_snapshot_for_cover(cover), climate_data
            )
            assert cover.direct_sun_valid is True
            assert climate_data.is_winter is True
            climate_pos = climate_state.get_state()
            assert climate_pos == 100
            assert climate_state.climate_strategy == ClimateStrategy.WINTER_HEATING

            pipeline = _make_pipeline()
            climate_readings = ClimateReadings(
                outside_temperature=15.0,
                inside_temperature=None,
                is_presence=True,
                is_sunny=True,
                lux_below_threshold=False,
                irradiance_below_threshold=False,
                cloud_coverage_above_threshold=False,
            )
            climate_options = ClimateOptions(
                temp_low=20.0,
                temp_high=25.0,
                temp_switch=True,
                transparent_blind=False,
                temp_summer_outside=22.0,
                cloud_suppression_enabled=False,
                winter_close_insulation=False,
            )
            snapshot = _build_pipeline_snapshot(
                cover,
                cover_type="cover_blind",
                climate_mode_enabled=True,
                climate_readings=climate_readings,
                climate_options=climate_options,
            )
            result = pipeline.evaluate(snapshot)

            assert result.control_method == ControlMethod.WINTER
            assert result.position == 100
            winning = next(s for s in result.decision_trace if s.matched)
            assert winning.handler == ClimateHandler().name

            diag_ctx = _build_diagnostic_context(cover, result, climate_mode=True)
            diag_dict, explanation = DiagnosticsBuilder().build(diag_ctx)

            assert diag_dict["control_status"] == "active"
            assert diag_dict.get("calculated_position_climate") == 100

    def test_climate_summer_cooling(self):
        """Hot outside temp + transparent blind → ClimateHandler closes blind fully (0%)."""
        logger = _make_logger()
        sun_data = _make_sun_data()

        with patch(_DATETIME_PATCH) as mock_dt:
            mock_dt.now.return_value = _NOON

            cover = build_vertical_cover(
                logger=logger,
                sol_azi=180.0,
                sol_elev=45.0,
                sun_data=sun_data,
                win_azi=180,
                fov_left=45,
                fov_right=45,
                h_def=50,
                distance=0.5,
                h_win=2.0,
                sunset_pos=0,
                sunset_off=0,
                sunrise_off=0,
                max_pos=100,
                min_pos=0,
            )

            climate_data = _make_climate_data(
                outside_temperature=30.0,  # above temp_high=25 and temp_summer_outside=22 → summer
                transparent_blind=True,
                is_sunny=True,
                is_presence=True,
            )

            climate_state = ClimateCoverState(
                make_snapshot_for_cover(cover), climate_data
            )
            assert climate_data.is_summer is True
            climate_pos = climate_state.get_state()
            assert climate_pos == 0
            assert climate_state.climate_strategy == ClimateStrategy.SUMMER_COOLING

            pipeline = _make_pipeline()
            climate_readings = ClimateReadings(
                outside_temperature=30.0,
                inside_temperature=None,
                is_presence=True,
                is_sunny=True,
                lux_below_threshold=False,
                irradiance_below_threshold=False,
                cloud_coverage_above_threshold=False,
            )
            climate_options = ClimateOptions(
                temp_low=20.0,
                temp_high=25.0,
                temp_switch=True,
                transparent_blind=True,
                temp_summer_outside=22.0,
                cloud_suppression_enabled=False,
                winter_close_insulation=False,
            )
            snapshot = _build_pipeline_snapshot(
                cover,
                cover_type="cover_blind",
                climate_mode_enabled=True,
                climate_readings=climate_readings,
                climate_options=climate_options,
            )
            result = pipeline.evaluate(snapshot)

            assert result.control_method == ControlMethod.SUMMER
            assert result.position == 0
            winning = next(s for s in result.decision_trace if s.matched)
            assert winning.handler == ClimateHandler().name

            diag_ctx = _build_diagnostic_context(cover, result, climate_mode=True)
            diag_dict, explanation = DiagnosticsBuilder().build(diag_ctx)

            assert diag_dict["control_status"] == "active"
            assert diag_dict.get("calculated_position_climate") == 0

    def test_force_override_trumps_solar(self):
        """ForceOverrideHandler wins even when sun is in FOV; decision trace shows it."""
        logger = _make_logger()
        sun_data = _make_sun_data()

        with patch(_DATETIME_PATCH) as mock_dt:
            mock_dt.now.return_value = _NOON

            cover = build_vertical_cover(
                logger=logger,
                sol_azi=180.0,
                sol_elev=45.0,
                sun_data=sun_data,
                win_azi=180,
                fov_left=45,
                fov_right=45,
                h_def=50,
                distance=0.5,
                h_win=2.0,
                sunset_pos=0,
                sunset_off=0,
                sunrise_off=0,
                max_pos=100,
                min_pos=0,
            )

            normal_state = NormalCoverState(cover=cover)
            assert cover.direct_sun_valid is True
            normal_state.get_state()

            pipeline = _make_pipeline()
            snapshot = _build_pipeline_snapshot(
                cover,
                cover_type="cover_blind",
                force_override_sensors={"binary_sensor.force": True},
                force_override_position=75,
            )
            result = pipeline.evaluate(snapshot)

            assert result.control_method == ControlMethod.FORCE
            assert result.position == 75
            assert result.decision_trace[0].matched is True
            assert result.decision_trace[0].handler == ForceOverrideHandler().name
            for step in result.decision_trace[1:]:
                assert step.matched is False

            diag_ctx = _build_diagnostic_context(
                cover, result, force_override_position=75, final_state=75
            )
            diag_dict, explanation = DiagnosticsBuilder().build(diag_ctx)

            assert diag_dict["control_status"] == "force_override_active"
            assert "force override" in explanation.lower()
            assert "75%" in explanation

    def test_manual_override(self):
        """ManualOverrideHandler wins when manual_override_active=True."""
        logger = _make_logger()
        sun_data = _make_sun_data()

        with patch(_DATETIME_PATCH) as mock_dt:
            mock_dt.now.return_value = _NOON

            cover = build_vertical_cover(
                logger=logger,
                sol_azi=180.0,
                sol_elev=45.0,
                sun_data=sun_data,
                win_azi=180,
                fov_left=45,
                fov_right=45,
                h_def=50,
                distance=0.5,
                h_win=2.0,
                sunset_pos=0,
                sunset_off=0,
                sunrise_off=0,
                max_pos=100,
                min_pos=0,
            )

            normal_state = NormalCoverState(cover=cover)
            normal_state.get_state()

            pipeline = _make_pipeline()
            snapshot = _build_pipeline_snapshot(
                cover, cover_type="cover_blind", manual_override_active=True
            )
            result = pipeline.evaluate(snapshot)

            assert result.control_method == ControlMethod.MANUAL
            winning = next(s for s in result.decision_trace if s.matched)
            assert winning.handler == ManualOverrideHandler().name

            diag_ctx = _build_diagnostic_context(cover, result)
            diag_dict, explanation = DiagnosticsBuilder().build(diag_ctx)

            assert diag_dict["control_status"] == "manual_override"
            assert "manual override" in explanation.lower()

    def test_motion_timeout(self):
        """MotionTimeoutHandler wins when motion_timeout_active=True."""
        logger = _make_logger()
        sun_data = _make_sun_data()

        with patch(_DATETIME_PATCH) as mock_dt:
            mock_dt.now.return_value = _NOON

            cover = build_vertical_cover(
                logger=logger,
                sol_azi=180.0,
                sol_elev=45.0,
                sun_data=sun_data,
                win_azi=180,
                fov_left=45,
                fov_right=45,
                h_def=50,
                distance=0.5,
                h_win=2.0,
                sunset_pos=0,
                sunset_off=0,
                sunrise_off=0,
                max_pos=100,
                min_pos=0,
            )

            normal_state = NormalCoverState(cover=cover)
            normal_state.get_state()

            pipeline = _make_pipeline()
            snapshot = _build_pipeline_snapshot(
                cover, cover_type="cover_blind", motion_timeout_active=True
            )
            result = pipeline.evaluate(snapshot)

            assert result.control_method == ControlMethod.MOTION
            assert result.position == int(cover.config.h_def)
            winning = next(s for s in result.decision_trace if s.matched)
            assert winning.handler == MotionTimeoutHandler().name

            diag_ctx = _build_diagnostic_context(cover, result)
            diag_dict, explanation = DiagnosticsBuilder().build(diag_ctx)

            assert diag_dict["control_status"] == "motion_timeout"
            assert "motion" in explanation.lower()

    def test_horizontal_awning_sun_tracking(self):
        """Horizontal awning with sun in FOV → SolarHandler wins."""
        logger = _make_logger()
        sun_data = _make_sun_data()

        with patch(_DATETIME_PATCH) as mock_dt:
            mock_dt.now.return_value = _NOON

            cover = build_horizontal_cover(
                logger=logger,
                sol_azi=180.0,
                sol_elev=45.0,
                sun_data=sun_data,
                win_azi=180,
                fov_left=45,
                fov_right=45,
                h_def=100,
                distance=0.5,
                h_win=2.0,
                awn_length=2.0,
                awn_angle=0,
                sunset_pos=0,
                sunset_off=0,
                sunrise_off=0,
                max_pos=100,
                min_pos=0,
            )

            normal_state = NormalCoverState(cover=cover)
            assert cover.direct_sun_valid is True
            calc_pos = normal_state.get_state()
            assert 0 <= calc_pos <= 100

            pipeline = _make_pipeline()
            snapshot = _build_pipeline_snapshot(cover, cover_type="cover_awning")
            result = pipeline.evaluate(snapshot)

            assert result.control_method == ControlMethod.SOLAR

            diag_ctx = _build_diagnostic_context(cover, result)
            diag_dict, explanation = DiagnosticsBuilder().build(diag_ctx)

            assert diag_dict["sun_azimuth"] == 180.0
            assert diag_dict["sun_elevation"] == 45.0
            assert "gamma" in diag_dict
            assert "sun" in explanation.lower() and "position" in explanation.lower()

    def test_tilt_cover_sun_tracking(self):
        """Tilt (venetian) cover with sun in FOV → SolarHandler wins.

        Uses sol_elev=70° because the default slat geometry (slat_distance/depth=1.5)
        produces a negative discriminant at 45° elevation (a known edge case tested in
        test_adaptive_tilt_cover.py).  70° gives tan²(beta)≈7.5, keeping it positive.
        """
        logger = _make_logger()
        sun_data = _make_sun_data()

        with patch(_DATETIME_PATCH) as mock_dt:
            mock_dt.now.return_value = _NOON

            cover = build_tilt_cover(
                logger=logger,
                sol_azi=180.0,
                sol_elev=70.0,
                sun_data=sun_data,
                win_azi=180,
                fov_left=45,
                fov_right=45,
                h_def=50,
                slat_distance=0.03,
                depth=0.02,
                mode="mode1",
                sunset_pos=0,
                sunset_off=0,
                sunrise_off=0,
                max_pos=100,
                min_pos=0,
            )

            normal_state = NormalCoverState(cover=cover)
            assert cover.direct_sun_valid is True
            calc_pos = normal_state.get_state()
            assert 0 <= calc_pos <= 100

            pipeline = _make_pipeline()
            snapshot = _build_pipeline_snapshot(cover, cover_type="cover_tilt")
            result = pipeline.evaluate(snapshot)

            assert result.control_method == ControlMethod.SOLAR

            diag_ctx = _build_diagnostic_context(cover, result)
            diag_dict, explanation = DiagnosticsBuilder().build(diag_ctx)

            assert diag_dict["sun_azimuth"] == 180.0
            assert diag_dict["sun_elevation"] == 70.0
            assert "gamma" in diag_dict
            assert "sun" in explanation.lower() and "position" in explanation.lower()


# ---------------------------------------------------------------------------
# Step 24: Weather override → pipeline → diagnostics
# ---------------------------------------------------------------------------


class TestWeatherOverrideEndToEnd:
    """WeatherOverrideHandler wins with bypass_auto_control=True; diagnostics confirm."""

    def test_weather_override_trumps_solar(self):
        """WeatherOverrideHandler wins even when sun is in FOV."""
        logger = _make_logger()
        sun_data = _make_sun_data()

        with patch(_DATETIME_PATCH) as mock_dt:
            mock_dt.now.return_value = _NOON

            cover = build_vertical_cover(
                logger=logger,
                sol_azi=180.0,
                sol_elev=45.0,
                sun_data=sun_data,
                win_azi=180,
                fov_left=45,
                fov_right=45,
                h_def=50,
                distance=0.5,
                h_win=2.0,
                sunset_pos=0,
                sunset_off=0,
                sunrise_off=0,
                max_pos=100,
                min_pos=0,
            )

            pipeline = PipelineRegistry(
                [
                    WeatherOverrideHandler(),
                    SolarHandler(),
                    DefaultHandler(),
                ]
            )
            snapshot = _build_pipeline_snapshot(
                cover,
                cover_type="cover_blind",
                weather_override_active=True,
                weather_override_position=0,
            )
            result = pipeline.evaluate(snapshot)

            assert result.control_method == ControlMethod.WEATHER
            assert result.position == 0
            assert result.bypass_auto_control is True

            diag_ctx = _build_diagnostic_context(cover, result)
            diag_dict, explanation = DiagnosticsBuilder().build(diag_ctx)

            assert diag_dict["control_status"] == "weather_override_active"
            assert "weather" in explanation.lower()


# ---------------------------------------------------------------------------
# Step 25: Cloud suppression → pipeline → diagnostics
# ---------------------------------------------------------------------------


class TestCloudSuppressionEndToEnd:
    """CloudSuppressionHandler fires when not sunny and suppression is enabled."""

    def test_cloud_suppression_beats_solar(self):
        """CLOUD control method: sun in FOV but cloudy → cloud suppression fires."""
        logger = _make_logger()
        sun_data = _make_sun_data()

        with patch(_DATETIME_PATCH) as mock_dt:
            mock_dt.now.return_value = _NOON

            cover = build_vertical_cover(
                logger=logger,
                sol_azi=180.0,
                sol_elev=45.0,
                sun_data=sun_data,
                win_azi=180,
                fov_left=45,
                fov_right=45,
                h_def=50,
                distance=0.5,
                h_win=2.0,
                sunset_pos=0,
                sunset_off=0,
                sunrise_off=0,
                max_pos=100,
                min_pos=0,
            )

            pipeline = PipelineRegistry(
                [CloudSuppressionHandler(), SolarHandler(), DefaultHandler()]
            )
            climate_readings = ClimateReadings(
                outside_temperature=None,
                inside_temperature=None,
                is_presence=True,
                is_sunny=False,  # not sunny → cloud suppression fires
                lux_below_threshold=False,
                irradiance_below_threshold=False,
                cloud_coverage_above_threshold=False,
            )
            from custom_components.adaptive_cover_pro.pipeline.types import (
                ClimateOptions,
            )

            climate_options = ClimateOptions(
                temp_low=None,
                temp_high=None,
                temp_switch=False,
                transparent_blind=False,
                temp_summer_outside=None,
                cloud_suppression_enabled=True,
                winter_close_insulation=False,
            )
            snapshot = _build_pipeline_snapshot(
                cover,
                cover_type="cover_blind",
                climate_readings=climate_readings,
                climate_options=climate_options,
            )
            result = pipeline.evaluate(snapshot)

            assert result.control_method == ControlMethod.CLOUD
            assert result.position == 50  # default position (h_def)

            diag_ctx = _build_diagnostic_context(cover, result)
            diag_dict, explanation = DiagnosticsBuilder().build(diag_ctx)

            # Cloud suppression maps to "active" status (same as solar)
            assert diag_dict["control_status"] == "active"
            assert "cloud" in explanation.lower()


# ---------------------------------------------------------------------------
# Step 27: Custom position → pipeline → diagnostics
# ---------------------------------------------------------------------------


class TestCustomPositionEndToEnd:
    """CustomPositionHandler wins when its sensor is ON."""

    def test_custom_position_wins_over_solar(self):
        """Custom position at priority 77 beats solar (40) when sensor is active."""
        logger = _make_logger()
        sun_data = _make_sun_data()

        with patch(_DATETIME_PATCH) as mock_dt:
            mock_dt.now.return_value = _NOON

            cover = build_vertical_cover(
                logger=logger,
                sol_azi=180.0,
                sol_elev=45.0,
                sun_data=sun_data,
                win_azi=180,
                fov_left=45,
                fov_right=45,
                h_def=50,
                distance=0.5,
                h_win=2.0,
                sunset_pos=0,
                sunset_off=0,
                sunrise_off=0,
                max_pos=100,
                min_pos=0,
            )

            pipeline = PipelineRegistry(
                [
                    CustomPositionHandler(
                        slot=1,
                        entity_id="binary_sensor.scene",
                        position=33,
                        priority=77,
                    ),
                    SolarHandler(),
                    DefaultHandler(),
                ]
            )
            snapshot = PipelineSnapshot(
                cover=cover,
                config=cover.config,
                cover_type="cover_blind",
                default_position=50,
                is_sunset_active=False,
                climate_readings=None,
                climate_mode_enabled=False,
                climate_options=None,
                force_override_sensors={},
                force_override_position=0,
                manual_override_active=False,
                motion_timeout_active=False,
                weather_override_active=False,
                weather_override_position=0,
                glare_zones=None,
                active_zone_names=frozenset(),
                custom_position_sensors=[
                    CustomPositionSensorState(
                        entity_id="binary_sensor.scene",
                        is_on=True,
                        position=33,
                        priority=77,
                        min_mode=False,
                        use_my=False,
                    )
                ],
            )
            result = pipeline.evaluate(snapshot)

            assert result.control_method == ControlMethod.CUSTOM_POSITION
            assert result.position == 33

            diag_ctx = _build_diagnostic_context(cover, result)
            diag_dict, explanation = DiagnosticsBuilder().build(diag_ctx)

            # Custom position maps to "active" status
            assert diag_dict["control_status"] == "active"


# ---------------------------------------------------------------------------
# Step 28: Climate intermediate (glare control) → pipeline → diagnostics
# ---------------------------------------------------------------------------


class TestClimateGlareControlEndToEnd:
    """When temp between thresholds with presence, climate mode uses solar tracking."""

    def test_climate_glare_control_uses_solar_position(self):
        """Inside temp between thresholds + opaque blind → SUMMER strategy but solar pos."""
        logger = _make_logger()
        sun_data = _make_sun_data()

        with patch(_DATETIME_PATCH) as mock_dt:
            mock_dt.now.return_value = _NOON

            cover = build_vertical_cover(
                logger=logger,
                sol_azi=180.0,
                sol_elev=45.0,
                sun_data=sun_data,
                win_azi=180,
                fov_left=45,
                fov_right=45,
                h_def=50,
                distance=0.5,
                h_win=2.0,
                sunset_pos=0,
                sunset_off=0,
                sunrise_off=0,
                max_pos=100,
                min_pos=0,
            )

            pipeline = PipelineRegistry(
                [ClimateHandler(), SolarHandler(), DefaultHandler()]
            )
            climate_readings = ClimateReadings(
                outside_temperature=None,
                inside_temperature=22.0,  # between temp_low=18 and temp_high=26
                is_presence=True,
                is_sunny=True,
                lux_below_threshold=False,
                irradiance_below_threshold=False,
                cloud_coverage_above_threshold=False,
            )
            from custom_components.adaptive_cover_pro.pipeline.types import (
                ClimateOptions,
            )

            climate_options = ClimateOptions(
                temp_low=18.0,
                temp_high=26.0,
                temp_switch=False,
                transparent_blind=False,
                temp_summer_outside=None,
                cloud_suppression_enabled=False,
                winter_close_insulation=False,
            )
            snapshot = _build_pipeline_snapshot(
                cover,
                cover_type="cover_blind",
                climate_mode_enabled=True,
                climate_readings=climate_readings,
                climate_options=climate_options,
            )
            result = pipeline.evaluate(snapshot)

            # ClimateHandler fires in GLARE_CONTROL mode → uses solar position
            assert result.control_method == ControlMethod.SOLAR
            assert 0 <= result.position <= 100

            diag_ctx = _build_diagnostic_context(cover, result, climate_mode=True)
            diag_dict, explanation = DiagnosticsBuilder().build(diag_ctx)

            assert diag_dict["control_status"] == "active"


# ---------------------------------------------------------------------------
# Step 29: Multiple overrides active, highest priority wins
# ---------------------------------------------------------------------------


class TestMultipleOverridesHighestPriorityWins:
    """When multiple overrides are active simultaneously, the highest-priority wins."""

    def test_force_beats_manual_and_motion(self):
        """Force (100) > Manual (80) > Motion (75): force wins with full trace."""
        logger = _make_logger()
        sun_data = _make_sun_data()

        with patch(_DATETIME_PATCH) as mock_dt:
            mock_dt.now.return_value = _NOON

            cover = build_vertical_cover(
                logger=logger,
                sol_azi=180.0,
                sol_elev=45.0,
                sun_data=sun_data,
                win_azi=180,
                fov_left=45,
                fov_right=45,
                h_def=50,
                distance=0.5,
                h_win=2.0,
                sunset_pos=0,
                sunset_off=0,
                sunrise_off=0,
                max_pos=100,
                min_pos=0,
            )

            pipeline = PipelineRegistry(
                [
                    ForceOverrideHandler(),
                    ManualOverrideHandler(),
                    MotionTimeoutHandler(),
                    SolarHandler(),
                    DefaultHandler(),
                ]
            )
            snapshot = _build_pipeline_snapshot(
                cover,
                cover_type="cover_blind",
                force_override_sensors={"binary_sensor.wind": True},
                force_override_position=0,
                manual_override_active=True,
                motion_timeout_active=True,
            )
            result = pipeline.evaluate(snapshot)

            assert result.control_method == ControlMethod.FORCE
            assert result.position == 0
            assert result.bypass_auto_control is True

            # Only first step (force) matched; all others did not
            matched = [s for s in result.decision_trace if s.matched]
            assert len(matched) == 1
            assert matched[0].handler == "force_override"

            # Full trace has all handlers
            handler_names = {s.handler for s in result.decision_trace}
            assert "force_override" in handler_names
            assert "manual_override" in handler_names
            assert "motion_timeout" in handler_names

            diag_ctx = _build_diagnostic_context(
                cover, result, force_override_position=0, final_state=0
            )
            diag_dict, explanation = DiagnosticsBuilder().build(diag_ctx)
            assert diag_dict["control_status"] == "force_override_active"


# ---------------------------------------------------------------------------
# Step 30: Horizontal awning with climate mode
# ---------------------------------------------------------------------------


class TestHorizontalAwningWithClimateMode:
    """Horizontal awning with climate mode: covers awning-specific calculation."""

    def test_horizontal_awning_winter_heating(self):
        """Cold temp + sun in FOV → ClimateHandler retracts awning fully (0%) so sun reaches window."""
        logger = _make_logger()
        sun_data = _make_sun_data()

        with patch(_DATETIME_PATCH) as mock_dt:
            mock_dt.now.return_value = _NOON

            cover = build_horizontal_cover(
                logger=logger,
                sol_azi=180.0,
                sol_elev=45.0,
                sun_data=sun_data,
                win_azi=180,
                fov_left=45,
                fov_right=45,
                h_def=100,
                distance=0.5,
                h_win=2.0,
                awn_length=2.0,
                awn_angle=0,
                sunset_pos=0,
                sunset_off=0,
                sunrise_off=0,
                max_pos=100,
                min_pos=0,
            )

            pipeline = _make_pipeline()
            climate_readings = ClimateReadings(
                outside_temperature=None,
                inside_temperature=15.0,  # below temp_low=20 → winter
                is_presence=True,
                is_sunny=True,
                lux_below_threshold=False,
                irradiance_below_threshold=False,
                cloud_coverage_above_threshold=False,
            )
            from custom_components.adaptive_cover_pro.pipeline.types import (
                ClimateOptions,
            )

            climate_options = ClimateOptions(
                temp_low=20.0,
                temp_high=25.0,
                temp_switch=False,
                transparent_blind=False,
                temp_summer_outside=None,
                cloud_suppression_enabled=False,
                winter_close_insulation=False,
            )
            snapshot = _build_pipeline_snapshot(
                cover,
                cover_type="cover_awning",
                climate_mode_enabled=True,
                climate_readings=climate_readings,
                climate_options=climate_options,
            )
            result = pipeline.evaluate(snapshot)

            assert result.control_method == ControlMethod.WINTER
            assert result.position == 0, (
                "Horizontal awning winter heating must retract (0%) so sun reaches the "
                "window for solar gain — extending (100%) blocks the heating sun. (#337)"
            )

            diag_ctx = _build_diagnostic_context(cover, result, climate_mode=True)
            diag_dict, _ = DiagnosticsBuilder().build(diag_ctx)

            assert diag_dict["sun_azimuth"] == 180.0
            assert diag_dict["control_status"] == "active"

    def test_horizontal_awning_solar_tracking(self):
        """Awning with sun in FOV but comfortable temp → solar tracking."""
        logger = _make_logger()
        sun_data = _make_sun_data()

        with patch(_DATETIME_PATCH) as mock_dt:
            mock_dt.now.return_value = _NOON

            cover = build_horizontal_cover(
                logger=logger,
                sol_azi=180.0,
                sol_elev=45.0,
                sun_data=sun_data,
                win_azi=180,
                fov_left=45,
                fov_right=45,
                h_def=100,
                distance=0.5,
                h_win=2.0,
                awn_length=2.0,
                awn_angle=0,
                sunset_pos=0,
                sunset_off=0,
                sunrise_off=0,
                max_pos=100,
                min_pos=0,
            )

            assert cover.direct_sun_valid is True
            pipeline = _make_pipeline()
            snapshot = _build_pipeline_snapshot(cover, cover_type="cover_awning")
            result = pipeline.evaluate(snapshot)

            assert result.control_method == ControlMethod.SOLAR
            assert 0 <= result.position <= 100


# ---------------------------------------------------------------------------
# Step 31: Tilt cover with climate mode
# ---------------------------------------------------------------------------


class TestTiltCoverWithClimateMode:
    """Tilt (venetian) cover with climate mode active."""

    def test_tilt_cover_winter_heating(self):
        """Cold temp + tilt cover → ClimateHandler opens slats fully."""
        logger = _make_logger()
        sun_data = _make_sun_data()

        with patch(_DATETIME_PATCH) as mock_dt:
            mock_dt.now.return_value = _NOON

            cover = build_tilt_cover(
                logger=logger,
                sol_azi=180.0,
                sol_elev=70.0,
                sun_data=sun_data,
                win_azi=180,
                fov_left=45,
                fov_right=45,
                h_def=50,
                slat_distance=0.03,
                depth=0.02,
                mode="mode1",
                sunset_pos=0,
                sunset_off=0,
                sunrise_off=0,
                max_pos=100,
                min_pos=0,
            )

            pipeline = _make_pipeline()
            climate_readings = ClimateReadings(
                outside_temperature=None,
                inside_temperature=10.0,  # very cold → winter
                is_presence=True,
                is_sunny=True,
                lux_below_threshold=False,
                irradiance_below_threshold=False,
                cloud_coverage_above_threshold=False,
            )
            from custom_components.adaptive_cover_pro.pipeline.types import (
                ClimateOptions,
            )

            climate_options = ClimateOptions(
                temp_low=18.0,
                temp_high=26.0,
                temp_switch=False,
                transparent_blind=False,
                temp_summer_outside=None,
                cloud_suppression_enabled=False,
                winter_close_insulation=False,
            )
            snapshot = _build_pipeline_snapshot(
                cover,
                cover_type="cover_tilt",
                climate_mode_enabled=True,
                climate_readings=climate_readings,
                climate_options=climate_options,
            )
            result = pipeline.evaluate(snapshot)

            assert result.control_method == ControlMethod.WINTER
            assert result.position == 100

            diag_ctx = _build_diagnostic_context(cover, result, climate_mode=True)
            diag_dict, _ = DiagnosticsBuilder().build(diag_ctx)

            assert diag_dict["sun_azimuth"] == 180.0
            assert diag_dict["control_status"] == "active"
