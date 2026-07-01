"""Tests for anticipatory solar positioning across the throttle window (#616).

When the solar handler wins and ``CONF_DELTA_TIME`` (minutes) > 0, the solar
target is the most-protective position needed across ``[now, now + delta_time]``.
The horizon is threaded onto the snapshot as ``time_threshold_minutes`` and the
helper samples future sun positions, folding each valid sample through the
policy's ``more_protective_position`` comparator.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_DELTA_TIME,
    SOLAR_ANTICIPATION_SAMPLES,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.pipeline.helpers import (
    anticipated_solar_position,
    compute_solar_position,
)
from custom_components.adaptive_cover_pro.pipeline.snapshot_builder import (
    PipelineSnapshotBuilder,
)
from custom_components.adaptive_cover_pro.state.climate_provider import (
    ClimateProvider,
)
from tests.cover_helpers import build_horizontal_cover, build_vertical_cover

# A fixed reference day so all timestamps are deterministic.
_DAY = datetime(2024, 6, 21, tzinfo=UTC)
_STEP = timedelta(minutes=5)


class _FakeSunData:
    """Minimal SunData stand-in exposing the per-day 5-minute table.

    The anticipation helper reads ``times`` / ``solar_azimuth`` /
    ``solar_elevation`` and the geometry engine reads ``sunset()`` / ``sunrise()``
    for the sunset gate. The table is centred on a noon reference so every
    sampled ``eval_time`` falls in full daylight (sunset gate False).
    """

    def __init__(self, azimuths: list[float], elevations: list[float]):
        start = _DAY.replace(hour=10)
        self.times = [start + i * _STEP for i in range(len(azimuths))]
        self.solar_azimuth = azimuths
        self.solar_elevation = elevations

    def sunset(self) -> datetime:
        return _DAY.replace(hour=21)

    def sunrise(self) -> datetime:
        return _DAY.replace(hour=5)


def _vertical_cover(sun_data: _FakeSunData, *, sol_azi: float, sol_elev: float):
    cover = build_vertical_cover(
        logger=MagicMock(),
        sol_azi=sol_azi,
        sol_elev=sol_elev,
        sunset_pos=0,
        sunset_off=0,
        sunrise_off=0,
        sun_data=sun_data,
        fov_left=90,
        fov_right=90,
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
    cover.eval_time = sun_data.times[0]
    return cover


def _horizontal_cover(sun_data: _FakeSunData, *, sol_azi: float, sol_elev: float):
    cover = build_horizontal_cover(
        logger=MagicMock(),
        sol_azi=sol_azi,
        sol_elev=sol_elev,
        sunset_pos=0,
        sunset_off=0,
        sunrise_off=0,
        sun_data=sun_data,
        fov_left=90,
        fov_right=90,
        win_azi=180,
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
        awn_length=2.0,
        awn_angle=0.0,
    )
    cover.eval_time = sun_data.times[0]
    return cover


def _snapshot(cover, *, cover_type: str, time_threshold_minutes: int):
    return SimpleNamespace(
        cover=cover,
        config=cover.config,
        cover_type=cover_type,
        policy=get_policy(cover_type),
        minimize_movements=False,
        max_coverage_steps=1,
        solar_floor_active=True,
        time_threshold_minutes=time_threshold_minutes,
    )


# ---------------------------------------------------------------------------
# Step 3 — snapshot carries the horizon
# ---------------------------------------------------------------------------


def _make_builder() -> PipelineSnapshotBuilder:
    hass = MagicMock()
    hass.states.get.return_value = None
    climate_provider = MagicMock(spec=ClimateProvider)
    toggles = MagicMock()
    toggles.lux_toggle = False
    toggles.irradiance_toggle = False
    toggles.temp_toggle = False
    toggles.switch_mode = False
    toggles.motion_control = False
    policy = MagicMock()
    policy.glare_zones_config.return_value = None
    policy.position_axis_supported.return_value = True
    return PipelineSnapshotBuilder(
        hass=hass,
        logger=MagicMock(),
        climate_provider=climate_provider,
        toggles=toggles,
        policy=policy,
        config_service=MagicMock(),
    )


def _build_snapshot_with_options(options: dict):
    builder = _make_builder()
    sun_data = _FakeSunData([180.0], [45.0])
    cover = _vertical_cover(sun_data, sol_azi=180.0, sol_elev=45.0)
    return builder.build(
        options,
        cover_data=cover,
        cover_type="cover_blind",
        climate_readings=None,
        manual_override_active=False,
        motion_timeout_active=False,
        weather_override_active=False,
        in_time_window=True,
        current_cover_position=None,
        is_glare_zone_enabled=lambda _idx: False,
        effective_default=0,
        is_sunset_active=False,
    )


@pytest.mark.unit
def test_snapshot_carries_delta_time_as_horizon():
    snap = _build_snapshot_with_options({CONF_DELTA_TIME: 15})
    assert snap.time_threshold_minutes == 15


@pytest.mark.unit
def test_snapshot_horizon_defaults_to_zero_when_absent():
    snap = _build_snapshot_with_options({})
    assert snap.time_threshold_minutes == 0


# ---------------------------------------------------------------------------
# Step 5 — anticipation helper, geometry-driven
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_horizon_matches_live_solar_position():
    # A flat table; horizon 0 → identical to the live solar position.
    sun_data = _FakeSunData([180.0] * 6, [45.0] * 6)
    cover = _vertical_cover(sun_data, sol_azi=180.0, sol_elev=45.0)
    snap = _snapshot(cover, cover_type="cover_blind", time_threshold_minutes=0)
    assert anticipated_solar_position(snap) == compute_solar_position(snap)


@pytest.mark.unit
def test_vertical_anticipates_more_protective_future_sample():
    # Sun starts off-axis (gamma high → less coverage) and sweeps toward the
    # window centre (gamma → 0 → deeper, more shade → lower %). Anticipation
    # must pick the lower (more protective) future value.
    azimuths = [220.0, 210.0, 200.0, 190.0, 180.0, 180.0]
    elevations = [45.0] * 6
    sun_data = _FakeSunData(azimuths, elevations)
    cover = _vertical_cover(sun_data, sol_azi=220.0, sol_elev=45.0)
    snap = _snapshot(cover, cover_type="cover_blind", time_threshold_minutes=25)

    live = compute_solar_position(snap)
    anticipated = anticipated_solar_position(snap)
    assert anticipated < live
    # It equals the most-protective sampled position — the centred-sun sample.
    centred = _vertical_cover(sun_data, sol_azi=180.0, sol_elev=45.0)
    centred_snap = _snapshot(
        centred, cover_type="cover_blind", time_threshold_minutes=0
    )
    assert anticipated == compute_solar_position(centred_snap)


@pytest.mark.unit
def test_awning_anticipates_higher_future_sample():
    # Awning: more protective = higher %. Sun sweeps into the window so the
    # awning must extend further (higher %) ahead of time.
    azimuths = [220.0, 210.0, 200.0, 190.0, 180.0, 180.0]
    elevations = [45.0] * 6
    sun_data = _FakeSunData(azimuths, elevations)
    cover = _horizontal_cover(sun_data, sol_azi=220.0, sol_elev=45.0)
    snap = _snapshot(cover, cover_type="cover_awning", time_threshold_minutes=25)

    live = compute_solar_position(snap)
    anticipated = anticipated_solar_position(snap)
    assert anticipated > live


@pytest.mark.unit
def test_sun_leaving_fov_within_window_keeps_live_target():
    # Sun starts centred (valid, deep coverage) and sweeps OUT of the FOV.
    # Future samples become direct_sun_valid=False → skipped. Result equals
    # the live target (no spurious change).
    azimuths = [180.0, 250.0, 300.0, 330.0, 350.0, 5.0]
    elevations = [45.0] * 6
    sun_data = _FakeSunData(azimuths, elevations)
    cover = _vertical_cover(sun_data, sol_azi=180.0, sol_elev=45.0)
    snap = _snapshot(cover, cover_type="cover_blind", time_threshold_minutes=25)
    assert anticipated_solar_position(snap) == compute_solar_position(snap)


@pytest.mark.unit
def test_empty_sun_data_table_falls_back_to_live():
    # An empty table has no future angles to sample → live target unchanged.
    sun_data = _FakeSunData([180.0], [45.0])
    cover = _vertical_cover(sun_data, sol_azi=180.0, sol_elev=45.0)
    # Replace the single-entry table with an empty one (the cover keeps its
    # live angles, so compute_solar_position still works).
    sun_data.times = []
    sun_data.solar_azimuth = []
    sun_data.solar_elevation = []
    snap = _snapshot(cover, cover_type="cover_blind", time_threshold_minutes=25)
    assert anticipated_solar_position(snap) == compute_solar_position(snap)


@pytest.mark.unit
def test_short_horizon_deduplicates_grid_indices():
    # A horizon shorter than the 5-min grid makes every fractional sample snap
    # to the same index, exercising the duplicate-index skip. The single deduped
    # sample equals the live position, so the result is the live target.
    sun_data = _FakeSunData([180.0] * 6, [45.0] * 6)
    cover = _vertical_cover(sun_data, sol_azi=180.0, sol_elev=45.0)
    snap = _snapshot(cover, cover_type="cover_blind", time_threshold_minutes=2)
    assert anticipated_solar_position(snap) == compute_solar_position(snap)


@pytest.mark.unit
def test_sample_count_is_named_constant():
    # Guard that the horizon sampler uses the named constant, not a literal.
    assert isinstance(SOLAR_ANTICIPATION_SAMPLES, int)
    assert SOLAR_ANTICIPATION_SAMPLES >= 1


# ---------------------------------------------------------------------------
# Step 7 — solar handler routes through anticipation; raw position follows
# ---------------------------------------------------------------------------


def _full_snapshot(cover, *, cover_type: str, time_threshold_minutes: int):
    """Build a snapshot with the extra fields the SolarHandler / raw path read."""
    return SimpleNamespace(
        cover=cover,
        config=cover.config,
        cover_type=cover_type,
        policy=get_policy(cover_type),
        minimize_movements=False,
        max_coverage_steps=1,
        solar_floor_active=True,
        time_threshold_minutes=time_threshold_minutes,
        in_time_window=True,
        enable_sun_tracking=True,
        is_sunset_active=False,
        default_position=0,
    )


@pytest.mark.unit
def test_solar_handler_returns_anticipated_position():
    from custom_components.adaptive_cover_pro.pipeline.handlers.solar import (
        SolarHandler,
    )

    azimuths = [220.0, 210.0, 200.0, 190.0, 180.0, 180.0]
    sun_data = _FakeSunData(azimuths, [45.0] * 6)
    cover = _vertical_cover(sun_data, sol_azi=220.0, sol_elev=45.0)
    snap = _full_snapshot(cover, cover_type="cover_blind", time_threshold_minutes=25)

    result = SolarHandler().evaluate(snap)
    expected = anticipated_solar_position(snap)
    assert result is not None
    assert result.position == expected
    assert result.raw_calculated_position == expected
    # Anticipation must actually have moved the target below the live value.
    assert expected < compute_solar_position(snap)


@pytest.mark.unit
def test_raw_calculated_position_routes_through_anticipation():
    from custom_components.adaptive_cover_pro.pipeline.helpers import (
        compute_raw_calculated_position,
    )

    azimuths = [220.0, 210.0, 200.0, 190.0, 180.0, 180.0]
    sun_data = _FakeSunData(azimuths, [45.0] * 6)
    cover = _vertical_cover(sun_data, sol_azi=220.0, sol_elev=45.0)
    snap = _full_snapshot(cover, cover_type="cover_blind", time_threshold_minutes=25)

    assert compute_raw_calculated_position(snap) == anticipated_solar_position(snap)
    assert compute_raw_calculated_position(snap) < compute_solar_position(snap)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (2, 2),
        (0, 0),
        (25.0, 25),
        (None, 0),
        # Malformed values must never crash the update cycle — a legacy duration
        # dict (or any non-number) coerces to 0 (anticipation disabled), not a
        # ``dict``/``str`` that later blows up ``horizon <= 0`` with a TypeError.
        ({"hours": 0, "minutes": 2, "seconds": 0}, 0),
        ("nonsense", 0),
        (True, 0),
        (False, 0),
    ],
)
def test_delta_time_minutes_coerces_safely(value, expected):
    from custom_components.adaptive_cover_pro.pipeline.snapshot_builder import (
        _delta_time_minutes,
    )

    result = _delta_time_minutes(value)
    assert result == expected
    # The whole point of the guard: the result is always ``<=``-comparable.
    assert result <= 0 or result > 0
