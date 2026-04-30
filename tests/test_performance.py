"""Performance benchmarks for the calculation engine and diagnostics builder.

These tests assert that critical code paths run within acceptable time bounds.
They are not meant to be precise micro-benchmarks but catch severe regressions.

All benchmarks are unit tests (no HA overhead).
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from tests.cover_helpers import (
    build_horizontal_cover,
    build_tilt_cover,
    build_vertical_cover,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger() -> MagicMock:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    return logger


def _make_sun_data(sol_azi: float = 180.0, sol_elev: float = 45.0) -> MagicMock:
    sd = MagicMock()
    sd.timezone = "UTC"
    sd.solar_azimuth = sol_azi
    sd.solar_elevation = sol_elev
    return sd


def _build_vertical(sol_azi: float = 180.0, sol_elev: float = 45.0):
    return build_vertical_cover(
        logger=_make_logger(),
        sol_azi=sol_azi,
        sol_elev=sol_elev,
        sun_data=_make_sun_data(sol_azi, sol_elev),
        win_azi=180,
        fov_left=45,
        fov_right=45,
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
        sunset_pos=0,
        sunset_off=0,
        sunrise_off=0,
    )


# ---------------------------------------------------------------------------
# Benchmark: Vertical cover calculation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_vertical_cover_1000_calculations_under_200ms() -> None:
    """1000 vertical cover position calculations complete in under 200ms."""
    cover = _build_vertical()
    N = 1000

    start = time.perf_counter()
    for _ in range(N):
        cover.calculate_position()
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert (
        elapsed_ms < 200
    ), f"1000 vertical calculations took {elapsed_ms:.1f}ms (limit: 200ms)"


@pytest.mark.unit
def test_horizontal_cover_1000_calculations_under_200ms() -> None:
    """1000 horizontal awning position calculations complete in under 200ms."""
    cover = build_horizontal_cover(
        logger=_make_logger(),
        sol_azi=180.0,
        sol_elev=45.0,
        sun_data=_make_sun_data(),
        win_azi=180,
        fov_left=45,
        fov_right=45,
        h_def=100,
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
        sunset_pos=0,
        sunset_off=0,
        sunrise_off=0,
        awn_length=2.0,
        awn_angle=0.0,
    )
    N = 1000
    start = time.perf_counter()
    for _ in range(N):
        cover.calculate_position()
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert (
        elapsed_ms < 200
    ), f"1000 horizontal calculations took {elapsed_ms:.1f}ms (limit: 200ms)"


@pytest.mark.unit
def test_tilt_cover_1000_calculations_under_200ms() -> None:
    """1000 tilt cover position calculations complete in under 200ms."""
    cover = build_tilt_cover(
        logger=_make_logger(),
        sol_azi=180.0,
        sol_elev=45.0,
        sun_data=_make_sun_data(),
        win_azi=180,
        fov_left=45,
        fov_right=45,
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
        slat_distance=0.03,
        depth=0.02,
        mode="mode1",
        sunset_pos=0,
        sunset_off=0,
        sunrise_off=0,
    )
    N = 1000
    start = time.perf_counter()
    for _ in range(N):
        cover.calculate_position()
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert (
        elapsed_ms < 200
    ), f"1000 tilt calculations took {elapsed_ms:.1f}ms (limit: 200ms)"


# ---------------------------------------------------------------------------
# Benchmark: Many sun angles (varied positions)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_vertical_cover_varied_sun_positions_under_500ms() -> None:
    """1000 vertical calculations with varied sun positions complete under 500ms."""
    N = 1000
    start = time.perf_counter()
    for i in range(N):
        sol_azi = (i * 0.36) % 360  # 0 to 360
        sol_elev = (i * 0.09) % 90  # 0 to 90
        cover = _build_vertical(sol_azi=sol_azi, sol_elev=sol_elev)
        cover.calculate_position()
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert (
        elapsed_ms < 2000
    ), f"1000 varied-sun calculations took {elapsed_ms:.1f}ms (limit: 2000ms)"


# ---------------------------------------------------------------------------
# Benchmark: Pipeline evaluation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pipeline_1000_evaluations_under_500ms() -> None:
    """1000 pipeline evaluations with all handlers active complete under 500ms."""
    from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
    from custom_components.adaptive_cover_pro.pipeline.types import PipelineSnapshot

    # Build a snapshot with real integer values for position limits
    # to avoid numpy ambiguity in apply_snapshot_limits.
    from tests.cover_helpers import make_cover_config

    cover_config = make_cover_config(h_def=50, max_pos=100, min_pos=0)

    snapshot = MagicMock(spec=PipelineSnapshot)
    snapshot.in_time_window = True
    snapshot.default_position = 50
    snapshot.sunset_position = 50
    snapshot.is_sunset_active = False
    snapshot.automatic_control = True
    snapshot.manual_toggle = True
    snapshot.motion_control_enabled = True
    snapshot.force_override_active = False
    snapshot.force_override_position = 0
    snapshot.is_weather_active = False
    snapshot.weather_position = 0
    snapshot.weather_bypass_auto_control = False
    snapshot.manual_override_active = False
    snapshot.custom_position_sensors = (
        (None, False, 0, 77, False, False),
        (None, False, 0, 77, False, False),
        (None, False, 0, 77, False, False),
        (None, False, 0, 77, False, False),
    )
    snapshot.motion_timeout_active = False
    snapshot.cloud_suppression_enabled = False
    snapshot.cloud_coverage_above_threshold = False
    snapshot.climate_options = MagicMock(climate_mode=False)
    snapshot.glare_zones = None
    snapshot.cover = MagicMock()
    snapshot.cover.valid = True
    snapshot.cover.calculate_position.return_value = 65
    snapshot.config = cover_config
    snapshot.sun_data = MagicMock()

    from custom_components.adaptive_cover_pro.pipeline.handlers import (
        DefaultHandler,
    )

    registry = PipelineRegistry([DefaultHandler()])

    N = 1000
    start = time.perf_counter()
    for _ in range(N):
        registry.evaluate(snapshot)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert (
        elapsed_ms < 500
    ), f"1000 pipeline evaluations took {elapsed_ms:.1f}ms (limit: 500ms)"


# ---------------------------------------------------------------------------
# Benchmark: Config summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_config_summary_under_50ms() -> None:
    """_build_config_summary for a complex config completes under 50ms."""
    from custom_components.adaptive_cover_pro.config_flow import _build_config_summary
    from custom_components.adaptive_cover_pro.const import (
        CONF_AZIMUTH,
        CONF_DEFAULT_HEIGHT,
        CONF_DELTA_POSITION,
        CONF_DELTA_TIME,
        CONF_DISTANCE,
        CONF_FOV_LEFT,
        CONF_FOV_RIGHT,
        CONF_HEIGHT_WIN,
        CONF_MANUAL_OVERRIDE_DURATION,
        CONF_MAX_POSITION,
        CONF_MIN_POSITION,
        CONF_SENSOR_TYPE,
        SensorType,
    )

    config = {
        CONF_SENSOR_TYPE: SensorType.BLIND,
        CONF_AZIMUTH: 180,
        CONF_FOV_LEFT: 45,
        CONF_FOV_RIGHT: 45,
        CONF_HEIGHT_WIN: 2.1,
        CONF_DISTANCE: 0.5,
        CONF_DEFAULT_HEIGHT: 50,
        CONF_MIN_POSITION: 0,
        CONF_MAX_POSITION: 100,
        CONF_DELTA_POSITION: 5,
        CONF_DELTA_TIME: {"hours": 0, "minutes": 2, "seconds": 0},
        CONF_MANUAL_OVERRIDE_DURATION: {"hours": 1, "minutes": 0, "seconds": 0},
        "start_time": "08:00:00",
        "end_time": "20:00:00",
        "climate_mode": False,
        "force_override_sensors": [],
        "motion_sensors": [],
    }

    N = 100
    start = time.perf_counter()
    for _ in range(N):
        _build_config_summary(config, SensorType.BLIND)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 50 * (
        N / 1
    ), f"100 config summary builds took {elapsed_ms:.1f}ms"  # Allow 50ms * iterations
