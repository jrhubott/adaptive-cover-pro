"""Tests for the roof / skylight window cover type (#212).

The engine subclasses :class:`AdaptiveVerticalCover` and re-projects the sun
geometry onto pitched glass. Pitch ``β`` is measured FROM HORIZONTAL:
``90`` = vertical (must reproduce the vertical engine bit-for-bit), ``0`` = flat
skylight. Tests are property-based (equivalence, illumination conditions,
monotonicity, bounds, ridge gating) plus a few first-principles spot checks, so
they pin the specified geometry without re-deriving the implementation's exact
arithmetic.

``gamma = (win_azi − sol_azi + 180) % 360 − 180``; with ``win_azi = 180`` a
``sol_azi`` of ``180 − gamma`` realises exactly the requested surface-solar
azimuth, so each case can drive a real engine while pinning gamma precisely.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from math import atan, atan2, cos, degrees, radians, sin, tan
from unittest.mock import MagicMock

import pandas as pd
import pytest

from custom_components.adaptive_cover_pro.config_types import RoofWindowConfig
from custom_components.adaptive_cover_pro.cover_types import POLICY_REGISTRY, get_policy
from custom_components.adaptive_cover_pro.engine.covers import (
    AdaptiveRoofWindowCover,
    AdaptiveVerticalCover,
)
from custom_components.adaptive_cover_pro.engine.sun_geometry import SunGeometry
from custom_components.adaptive_cover_pro.engine.covers.roof_window import (
    TRACE_KEY_COS_AOI,
    TRACE_KEY_RIDGE_GATE_ENABLED,
    TRACE_KEY_RIDGE_GATE_OCCLUDED,
    TRACE_KEY_ROOF_PITCH_DEG,
    TRACE_KEY_SLOPE_RATIO,
)
from tests.cover_helpers import make_cover_config, make_vertical_config

_WIN_AZI = 180.0


def _safe_sun_data() -> MagicMock:
    """Build a sun_data mock with far-off sunset/sunrise (sunset_valid=False)."""
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    now = datetime.now(UTC)
    sun_data.sunset.return_value = now + timedelta(hours=6)
    sun_data.sunrise.return_value = now - timedelta(hours=6)
    return sun_data


def _roof(
    *,
    roof_pitch: float,
    sol_elev: float,
    gamma: float,
    distance: float = 1.0,
    h_win: float = 2.0,
    window_depth: float = 0.0,
    sill_height: float = 0.0,
    roof_height_above: float = 0.0,
    fov_left: int = 90,
    fov_right: int = 90,
) -> AdaptiveRoofWindowCover:
    return AdaptiveRoofWindowCover(
        logger=MagicMock(),
        sol_azi=_WIN_AZI - gamma,
        sol_elev=sol_elev,
        sun_data=_safe_sun_data(),
        config=make_cover_config(
            win_azi=_WIN_AZI, fov_left=fov_left, fov_right=fov_right
        ),
        vert_config=make_vertical_config(
            distance=distance,
            h_win=h_win,
            window_depth=window_depth,
            sill_height=sill_height,
        ),
        roof_config=RoofWindowConfig(
            roof_pitch=roof_pitch, roof_height_above=roof_height_above
        ),
    )


def _vertical(
    *,
    sol_elev: float,
    gamma: float,
    distance: float = 1.0,
    h_win: float = 2.0,
    window_depth: float = 0.0,
    sill_height: float = 0.0,
    fov_left: int = 90,
    fov_right: int = 90,
) -> AdaptiveVerticalCover:
    return AdaptiveVerticalCover(
        logger=MagicMock(),
        sol_azi=_WIN_AZI - gamma,
        sol_elev=sol_elev,
        sun_data=_safe_sun_data(),
        config=make_cover_config(
            win_azi=_WIN_AZI, fov_left=fov_left, fov_right=fov_right
        ),
        vert_config=make_vertical_config(
            distance=distance,
            h_win=h_win,
            window_depth=window_depth,
            sill_height=sill_height,
        ),
    )


def _make_sweep_sun_data(*, gammas, elev, win_azi=_WIN_AZI):
    """Full-day sun grid: solar_azimuth = win_azi - gamma per sample, constant elev.

    Sunrise/sunset pinned to the grid edges so in_sun_window covers the whole day.
    """
    today = date.today()
    times = pd.date_range(
        start=pd.Timestamp(today), periods=len(gammas), freq="5min", tz="UTC"
    )
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    sun_data.times = times
    sun_data.solar_azimuth = [win_azi - g for g in gammas]
    sun_data.solar_elevation = [elev] * len(gammas)
    sun_data.sunrise.return_value = times[0].replace(tzinfo=None)
    sun_data.sunset.return_value = times[-1].replace(tzinfo=None)
    return sun_data


def _live_window_oracle(*, roof_pitch, elev, gammas, fov, sun_data):
    """First/last (time, azi, elev) where the LIVE scalar direct_sun_valid is True."""
    hits = []
    for i, g in enumerate(gammas):
        ts = sun_data.times[i]
        cover = _roof(
            roof_pitch=roof_pitch, sol_elev=elev, gamma=g, fov_left=fov, fov_right=fov
        )
        cover.sun_data = sun_data
        cover.eval_time = ts.to_pydatetime()
        if cover.direct_sun_valid:
            hits.append((ts.to_pydatetime(), _WIN_AZI - g, elev))
    return (hits[0], hits[-1]) if hits else (None, None)


# ---------------------------------------------------------------------------
# Registration / policy wiring
# ---------------------------------------------------------------------------


def test_registered_and_in_picker():
    from custom_components.adaptive_cover_pro.const import CoverType

    assert "cover_roof_window" in POLICY_REGISTRY
    assert CoverType.ROOF_WINDOW.value == "cover_roof_window"


# ---------------------------------------------------------------------------
# Step 2 — β = 90° reproduces the vertical engine exactly (regression anchor)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gamma", [-80.0, -45.0, -10.0, 0.0, 10.0, 45.0, 80.0])
@pytest.mark.parametrize("elev", [5.0, 20.0, 40.0, 60.0, 85.0])
def test_pitch_90_matches_vertical_position(elev, gamma):
    """At β=90° the slope projection must equal the vertical drop bit-for-bit."""
    kw = {"window_depth": 0.1, "sill_height": 0.3, "distance": 1.2, "h_win": 2.0}
    roof = _roof(roof_pitch=90, sol_elev=elev, gamma=gamma, **kw)
    vert = _vertical(sol_elev=elev, gamma=gamma, **kw)
    assert roof.calculate_position() == vert.calculate_position()


@pytest.mark.parametrize("gamma", [-80.0, -10.0, 0.0, 10.0, 80.0])
@pytest.mark.parametrize("elev", [-5.0, 0.0, 30.0, 70.0])
def test_pitch_90_matches_vertical_validity(elev, gamma):
    """At β=90° illumination + direct-sun gating match the vertical engine."""
    roof = _roof(roof_pitch=90, sol_elev=elev, gamma=gamma, roof_height_above=0.0)
    vert = _vertical(sol_elev=elev, gamma=gamma)
    assert roof.valid_elevation == vert.valid_elevation
    assert roof.valid == vert.valid
    assert roof.direct_sun_valid == vert.direct_sun_valid


# ---------------------------------------------------------------------------
# Step 3 — β = 0° flat skylight
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gamma", [0.0, 30.0, 60.0, 85.0])
def test_flat_skylight_illuminated_iff_above_horizon(gamma):
    """β=0°: cos(AOI)=sin(elev) → illuminated iff elev>0, azimuth-independent."""
    assert _roof(roof_pitch=0, sol_elev=10.0, gamma=gamma).valid_elevation is True
    assert _roof(roof_pitch=0, sol_elev=-5.0, gamma=gamma).valid_elevation is False
    # Exactly at the horizon cos(AOI)=0 (not strictly > 0) → not illuminated.
    assert _roof(roof_pitch=0, sol_elev=0.0, gamma=gamma).valid_elevation is False


def test_flat_skylight_illumination_is_azimuth_independent():
    """The flat-skylight illumination gate does not depend on gamma."""
    vals = {
        _roof(roof_pitch=0, sol_elev=30.0, gamma=g).valid_elevation
        for g in (0.0, 25.0, 55.0, 85.0)
    }
    assert vals == {True}


def test_flat_skylight_cos_aoi_equals_sin_elev():
    """β=0° reduces the angle-of-incidence cosine to sin(elev)."""
    roof = _roof(roof_pitch=0, sol_elev=37.0, gamma=42.0)
    roof.calculate_position()
    assert roof._last_calc_details[TRACE_KEY_COS_AOI] == pytest.approx(
        sin(radians(37.0)), abs=1e-9
    )


@pytest.mark.parametrize("gamma", [0.0, 45.0, 89.9])
@pytest.mark.parametrize("elev", [10.0, 40.0, 80.0])
def test_flat_skylight_position_bounded_no_singularity(gamma, elev):
    """β=0° positions stay finite within [0, h_win] even as gamma→90°."""
    pos = _roof(
        roof_pitch=0, sol_elev=elev, gamma=gamma, h_win=2.0
    ).calculate_position()
    assert 0.0 <= pos <= 2.0


# ---------------------------------------------------------------------------
# Step 4 — intermediate pitch (β = 45°)
# ---------------------------------------------------------------------------


def test_perpendicular_incidence_needs_no_coverage():
    """At β=45°, a sun perpendicular to the glass (elev=45°, gamma=0) projects
    zero slope shadow → minimal coverage.
    """
    pos = _roof(
        roof_pitch=45, sol_elev=45.0, gamma=0.0, distance=1.0
    ).calculate_position()
    assert pos == pytest.approx(0.0, abs=1e-9)


def test_perpendicular_incidence_cos_aoi_is_one():
    """Perpendicular incidence → cos(AOI)=1."""
    roof = _roof(roof_pitch=45, sol_elev=45.0, gamma=0.0)
    roof.calculate_position()
    assert roof._last_calc_details[TRACE_KEY_COS_AOI] == pytest.approx(1.0, abs=1e-9)


def test_intermediate_pitch_monotonic_above_perpendicular():
    """Above the perpendicular angle, higher sun → more slope coverage."""
    positions = [
        _roof(
            roof_pitch=45, sol_elev=elev, gamma=0.0, distance=1.0
        ).calculate_position()
        for elev in (50.0, 60.0, 70.0, 80.0, 85.0)
    ]
    for lower, higher in zip(positions, positions[1:], strict=False):
        assert higher >= lower - 1e-9


@pytest.mark.parametrize("gamma", [-60.0, -20.0, 0.0, 20.0, 60.0])
@pytest.mark.parametrize("elev", [15.0, 35.0, 55.0, 80.0])
def test_intermediate_pitch_positions_finite_and_bounded(gamma, elev):
    pos = _roof(
        roof_pitch=45, sol_elev=elev, gamma=gamma, h_win=2.0, distance=1.0
    ).calculate_position()
    assert 0.0 <= pos <= 2.0


# ---------------------------------------------------------------------------
# Step 5 — ridge occlusion gate
# ---------------------------------------------------------------------------


def _theta_r(roof_pitch: float, gamma: float) -> float:
    """θ_R = atan(tanβ · (−cos Δazi)); Δazi = −gamma so cos Δazi = cos gamma."""
    return degrees(atan(tan(radians(roof_pitch)) * (-cos(radians(gamma)))))


def test_ridge_gate_occludes_low_up_dip_sun():
    """Up-dip (cos gamma < 0): occluded below θ_R, clear above it (H > 0)."""
    roof_pitch, gamma = 30.0, 130.0  # cos130 < 0 → up-dip
    theta = _theta_r(roof_pitch, gamma)
    assert 0.0 < theta < 90.0
    below = _roof(
        roof_pitch=roof_pitch,
        sol_elev=theta - 5.0,
        gamma=gamma,
        roof_height_above=2.0,
        fov_left=180,
        fov_right=180,
    )
    above = _roof(
        roof_pitch=roof_pitch,
        sol_elev=theta + 5.0,
        gamma=gamma,
        roof_height_above=2.0,
        fov_left=180,
        fov_right=180,
    )
    assert below._is_sun_behind_ridge() is True
    assert above._is_sun_behind_ridge() is False
    # Direct-sun validity follows: occluded below the ridge horizon, lit above.
    assert below.direct_sun_valid is False
    assert above.direct_sun_valid is True


def test_ridge_gate_disabled_when_height_above_zero():
    """roof_height_above = 0 disables the ridge gate entirely."""
    roof_pitch, gamma = 30.0, 130.0
    theta = _theta_r(roof_pitch, gamma)
    roof = _roof(
        roof_pitch=roof_pitch,
        sol_elev=theta - 5.0,
        gamma=gamma,
        roof_height_above=0.0,
        fov_left=180,
        fov_right=180,
    )
    assert roof._is_sun_behind_ridge() is False


def test_ridge_gate_inactive_down_dip():
    """Down-dip (cos gamma ≥ 0) the ridge never occludes, even with H > 0."""
    roof = _roof(
        roof_pitch=40,
        sol_elev=15.0,
        gamma=20.0,  # cos20 > 0 → down-dip
        roof_height_above=3.0,
    )
    assert roof._is_sun_behind_ridge() is False


# ---------------------------------------------------------------------------
# Step 8 — diagnostics trace
# ---------------------------------------------------------------------------


def test_trace_surfaces_roof_keys():
    """The engine trace carries pitch, AOI, slope ratio and ridge-gate state."""
    roof = _roof(
        roof_pitch=40, sol_elev=35.0, gamma=20.0, roof_height_above=2.0, distance=1.0
    )
    roof.calculate_position()
    details = roof._last_calc_details
    for key in (
        TRACE_KEY_ROOF_PITCH_DEG,
        TRACE_KEY_COS_AOI,
        TRACE_KEY_SLOPE_RATIO,
        TRACE_KEY_RIDGE_GATE_ENABLED,
        TRACE_KEY_RIDGE_GATE_OCCLUDED,
    ):
        assert key in details
    assert details[TRACE_KEY_ROOF_PITCH_DEG] == pytest.approx(40.0)
    assert details[TRACE_KEY_RIDGE_GATE_ENABLED] is True


# ---------------------------------------------------------------------------
# Config plumbing + cross-type rejection (Step 6)
# ---------------------------------------------------------------------------


def test_roof_window_config_from_options_defaults():
    cfg = RoofWindowConfig.from_options({})
    assert cfg.roof_pitch == 40.0
    assert cfg.roof_height_above == 0.0


def test_roof_window_config_from_options_reads_values():
    from custom_components.adaptive_cover_pro.const import (
        CONF_ROOF_HEIGHT_ABOVE,
        CONF_ROOF_PITCH,
    )

    cfg = RoofWindowConfig.from_options(
        {CONF_ROOF_PITCH: 35, CONF_ROOF_HEIGHT_ABOVE: 1.5}
    )
    assert cfg.roof_pitch == 35.0
    assert cfg.roof_height_above == 1.5


def test_roof_geometry_keys_only_in_roof_window_live_keys():
    from custom_components.adaptive_cover_pro.const import (
        CONF_ROOF_HEIGHT_ABOVE,
        CONF_ROOF_PITCH,
    )

    roof_keys = get_policy("cover_roof_window").live_option_keys()
    assert CONF_ROOF_PITCH in roof_keys
    assert CONF_ROOF_HEIGHT_ABOVE in roof_keys
    for other in ("cover_blind", "cover_awning", "cover_tilt", "cover_venetian"):
        keys = get_policy(other).live_option_keys()
        assert CONF_ROOF_PITCH not in keys
        assert CONF_ROOF_HEIGHT_ABOVE not in keys


def test_roof_window_reuses_window_dimension_keys():
    from custom_components.adaptive_cover_pro.const import (
        CONF_HEIGHT_WIN,
        CONF_WINDOW_WIDTH,
    )

    keys = get_policy("cover_roof_window").live_option_keys()
    assert CONF_HEIGHT_WIN in keys
    assert CONF_WINDOW_WIDTH in keys


def test_roof_pitch_in_option_ranges_and_validators():
    from custom_components.adaptive_cover_pro.const import (
        CONF_ROOF_HEIGHT_ABOVE,
        CONF_ROOF_PITCH,
        OPTION_RANGES,
        _RANGE_ROOF_HEIGHT_ABOVE,
        _RANGE_ROOF_PITCH,
    )
    from custom_components.adaptive_cover_pro.services.options_service import (
        FIELD_VALIDATORS,
    )

    assert OPTION_RANGES[CONF_ROOF_PITCH] == _RANGE_ROOF_PITCH
    assert OPTION_RANGES[CONF_ROOF_HEIGHT_ABOVE] == _RANGE_ROOF_HEIGHT_ABOVE
    assert CONF_ROOF_PITCH in FIELD_VALIDATORS
    assert CONF_ROOF_HEIGHT_ABOVE in FIELD_VALIDATORS


def test_roof_window_lift_travel_is_window_height():
    svc = MagicMock()
    svc.get_vertical_data.return_value = MagicMock(h_win=1.9)
    assert get_policy("cover_roof_window").lift_travel_metres(svc, {}) == 1.9


def test_roof_window_geometry_length_keys_include_height_above():
    from custom_components.adaptive_cover_pro.const import CONF_ROOF_HEIGHT_ABOVE

    keys = get_policy("cover_roof_window").geometry_length_keys()
    assert CONF_ROOF_HEIGHT_ABOVE in keys


# ---------------------------------------------------------------------------
# Config-flow summary (Step 7)
# ---------------------------------------------------------------------------


def test_summary_renders_pitch_and_ridge_height():
    from custom_components.adaptive_cover_pro.const import (
        CONF_HEIGHT_WIN,
        CONF_ROOF_HEIGHT_ABOVE,
        CONF_ROOF_PITCH,
    )

    policy = get_policy("cover_roof_window")
    lines = policy.summary_geometry_lines(
        {CONF_HEIGHT_WIN: 1.2, CONF_ROOF_PITCH: 35, CONF_ROOF_HEIGHT_ABOVE: 1.5}
    )
    joined = " ".join(lines)
    assert "roof pitch 35° from horizontal" in joined
    assert "1.5m roof above window" in joined


def test_direct_sun_valid_true_when_lit_and_unobstructed():
    """Sun in front, no ridge occlusion → direct sun is valid."""
    roof = _roof(roof_pitch=40, sol_elev=40.0, gamma=10.0, roof_height_above=0.0)
    assert roof.direct_sun_valid is True


def test_projection_denominator_guard_stays_finite():
    """A near-zero slope denominator is clamped rather than dividing by zero."""
    # β=45, gamma=120 (cos=−0.5), elev=atan(0.5): denominator = sinβ + cosβ·f
    # with f = tan(elev)/cos(gamma) = 0.5/−0.5 = −1 → sin45 − cos45 ≈ 0.
    elev = degrees(atan(0.5))
    pos = _roof(
        roof_pitch=45,
        sol_elev=elev,
        gamma=120.0,
        h_win=2.0,
        fov_left=180,
        fov_right=180,
    ).calculate_position()
    assert 0.0 <= pos <= 2.0


# ---------------------------------------------------------------------------
# Policy hooks
# ---------------------------------------------------------------------------


def test_policy_wiki_anchor():
    assert get_policy("cover_roof_window").wiki_anchor() == "Configuration-Roof-Window"


def test_policy_display_label():
    assert get_policy("cover_roof_window").display_label() == "Roof Window"


def test_policy_display_label_honours_translation_override():
    out = get_policy("cover_roof_window").display_label(
        labels={"cover_types.roof_window": "Dachfenster"}
    )
    assert out == "Dachfenster"


def test_policy_geometry_schema_localised_path():
    from custom_components.adaptive_cover_pro.const import CONF_ROOF_PITCH

    schema = get_policy("cover_roof_window").geometry_schema(hass=MagicMock())
    keys = {str(m) for m in schema.schema}
    assert CONF_ROOF_PITCH in keys


def test_policy_capability_warning_when_no_set_position():
    warnings = get_policy("cover_roof_window").cover_capability_warnings(
        {"cover.x": {"has_set_position": False}}
    )
    assert warnings and "roof window" in warnings[0]


def test_policy_no_capability_warning_with_set_position():
    warnings = get_policy("cover_roof_window").cover_capability_warnings(
        {"cover.x": {"has_set_position": True}}
    )
    assert warnings == []


def test_policy_build_calc_engine_returns_roof_engine():
    from custom_components.adaptive_cover_pro.const import (
        CONF_HEIGHT_WIN,
        CONF_ROOF_PITCH,
    )

    svc = MagicMock()
    svc.get_vertical_data.return_value = make_vertical_config(h_win=1.8)
    options = {CONF_HEIGHT_WIN: 1.8, CONF_ROOF_PITCH: 35}
    engine = get_policy("cover_roof_window").build_calc_engine(
        logger=MagicMock(),
        sol_azi=170.0,
        sol_elev=40.0,
        sun_data=_safe_sun_data(),
        config=make_cover_config(),
        config_service=svc,
        options=options,
    )
    assert isinstance(engine, AdaptiveRoofWindowCover)
    assert engine.roof_pitch == 35.0


def test_summary_omits_ridge_height_when_zero():
    from custom_components.adaptive_cover_pro.const import (
        CONF_ROOF_HEIGHT_ABOVE,
        CONF_ROOF_PITCH,
    )

    policy = get_policy("cover_roof_window")
    lines = policy.summary_geometry_lines(
        {CONF_ROOF_PITCH: 35, CONF_ROOF_HEIGHT_ABOVE: 0.0}
    )
    joined = " ".join(lines)
    assert "roof pitch 35°" in joined
    assert "roof above window" not in joined


# ---------------------------------------------------------------------------
# Step 6 — tilt-aware azimuth FOV gate (#212)
#
# On a pitched roof window the glass "sees" a far wider azimuth swath than a
# vertical wall, so the bare ``|gamma| < fov`` gate dropped the sun while the
# glass was still fully lit. The acceptance gate now compares the in-plane
# (tilted-glass) azimuth — ``fov_angle`` = effective gamma — against the FOV,
# while the position projection keeps the raw horizontal gamma.
# ---------------------------------------------------------------------------


def _effective_gamma(pitch: float, elev: float, gamma: float) -> float:
    """FOV azimuth in the tilted glass plane — the expectation Option B derives.

    ``atan2(cos(elev)·sin(gamma), cos(AOI))`` with
    ``cos(AOI) = sinβ·cos(elev)·cos(gamma) + cosβ·sin(elev)``.
    """
    beta = radians(pitch)
    e = radians(elev)
    cos_aoi = sin(beta) * cos(e) * cos(radians(gamma)) + cos(beta) * sin(e)
    return degrees(atan2(cos(e) * sin(radians(gamma)), cos_aoi))


@pytest.mark.parametrize("gamma", [-85.0, -105.0, -125.0, -165.0])
def test_reporter_geometry_stays_valid_beyond_fov(gamma):
    """β=45°, FOV 85°, elev 60°: lit glass stays valid even past raw |gamma|=fov."""
    roof = _roof(roof_pitch=45, sol_elev=60.0, gamma=gamma, fov_left=85, fov_right=85)
    assert roof._cos_aoi() > 0
    assert roof.valid is True
    assert roof.direct_sun_valid is True
    assert roof.control_state_reason == "Direct Sun"


@pytest.mark.parametrize("gamma", [-105.0, -125.0, -165.0])
def test_in_fov_tracks_illumination_for_tilted_pitch(gamma):
    """in_fov follows the tilted-plane gate, not the raw horizontal azimuth."""
    roof = _roof(roof_pitch=45, sol_elev=60.0, gamma=gamma, fov_left=85, fov_right=85)
    assert roof.in_fov is True


@pytest.mark.parametrize("gamma", [-85.0, -110.0, -140.0])
def test_pitch_30_stays_valid_beyond_fov(gamma):
    """A shallower pitch (β=30°) widens acceptance even further."""
    roof = _roof(roof_pitch=30, sol_elev=45.0, gamma=gamma, fov_left=85, fov_right=85)
    assert roof._cos_aoi() > 0
    assert roof.valid is True


def test_effective_gamma_within_fov_when_lit():
    """fov_angle is the in-plane azimuth — inside FOV and distinct from raw gamma."""
    for gamma in (-85.0, -105.0, -125.0, -165.0):
        roof = _roof(
            roof_pitch=45, sol_elev=60.0, gamma=gamma, fov_left=85, fov_right=85
        )
        assert abs(roof.fov_angle) < 85
        assert roof.fov_angle != pytest.approx(roof.gamma)
    # gamma=-125 projects to ≈ -45° in the β=45/elev=60 tilted plane.
    roof = _roof(roof_pitch=45, sol_elev=60.0, gamma=-125.0, fov_left=85, fov_right=85)
    assert roof.fov_angle == pytest.approx(-45.0, abs=0.5)


def test_narrow_fov_still_rejects_far_sideways_sun():
    """A user-narrowed FOV still cuts a sun whose in-plane azimuth exceeds it."""
    pitch, elev, gamma, fov = 45.0, 20.0, -60.0, 20
    # Intent: the in-plane azimuth exceeds the narrow FOV while the glass is lit.
    assert abs(_effective_gamma(pitch, elev, gamma)) > fov
    roof = _roof(
        roof_pitch=pitch, sol_elev=elev, gamma=gamma, fov_left=fov, fov_right=fov
    )
    assert roof._cos_aoi() > 0
    assert roof.valid is False


@pytest.mark.parametrize("fov", [70, 85, 90])
@pytest.mark.parametrize("elev", [-5.0, 0.0, 30.0, 70.0])
@pytest.mark.parametrize("gamma", [-89.0, -80.0, -10.0, 0.0, 10.0, 80.0, 89.0])
def test_pitch_90_validity_unchanged_with_narrow_fov(fov, elev, gamma):
    """β=90° vertical anchor: every gate matches the vertical engine bit-for-bit."""
    roof = _roof(roof_pitch=90, sol_elev=elev, gamma=gamma, fov_left=fov, fov_right=fov)
    vert = _vertical(sol_elev=elev, gamma=gamma, fov_left=fov, fov_right=fov)
    assert roof.valid == vert.valid
    assert roof.in_fov == vert.in_fov
    assert roof.direct_sun_valid == vert.direct_sun_valid
    assert roof.fov_angle == vert.gamma


def test_wide_gamma_does_not_force_full_coverage():
    """The removed |gamma|>85 → full-coverage edge case stays gone for roof (#212)."""
    roof = _roof(
        roof_pitch=45,
        sol_elev=60.0,
        gamma=-125.0,
        distance=1.0,
        h_win=2.0,
        fov_left=85,
        fov_right=85,
    )
    assert roof.valid is True
    pos = roof.calculate_position()
    # A real geometric projection (here it saturates at h_win), NOT the old
    # forced-closed 0.0 the inherited edge case used to return.
    assert 0.0 < pos <= 2.0
    assert roof._last_calc_details["edge_case_detected"] is False


# ---------------------------------------------------------------------------
# Blind spot evaluated in the effective (acceptance) frame (#913)
#
# The blind-spot test must run in the SAME frame the FOV gate accepts — the
# tilted-plane effective gamma — not raw horizontal gamma. Otherwise an
# obstruction the near-flat glass physically responds to (raw gamma outside the
# raw FOV, effective gamma inside it) is unreachable by any blind spot.
# ---------------------------------------------------------------------------


def _roof_with_blind_spot(
    *,
    roof_pitch: float,
    sol_elev: float,
    gamma: float,
    blind_spot_left: int,
    blind_spot_right: int,
    fov_left: int = 90,
    fov_right: int = 90,
) -> AdaptiveRoofWindowCover:
    """Build a roof-window cover with a single active blind-spot slot."""
    return AdaptiveRoofWindowCover(
        logger=MagicMock(),
        sol_azi=_WIN_AZI - gamma,
        sol_elev=sol_elev,
        sun_data=_safe_sun_data(),
        config=make_cover_config(
            win_azi=_WIN_AZI,
            fov_left=fov_left,
            fov_right=fov_right,
            blind_spot_left=blind_spot_left,
            blind_spot_right=blind_spot_right,
            blind_spot_on=True,
        ),
        vert_config=make_vertical_config(distance=1.0, h_win=2.0),
        roof_config=RoofWindowConfig(roof_pitch=roof_pitch, roof_height_above=0.0),
    )


def test_blind_spot_masks_obstruction_in_effective_frame():
    """A blind spot masks an obstruction the flat glass sees (#913).

    #913 geometry: pitch 12°, FOV 90/90, low eastern sun (elev 10°, raw gamma
    110°). The glass accepts it (effective gamma ≈ 83.8° ∈ FOV), and a blind spot
    whose wedge covers ≈ [80, 88] in the *effective* frame now masks it. With the
    legacy FOV-relative storage the raw-frame wedge is
    ``[fov_left - right, fov_left - left] = [80, 88]``; before the frame fix the
    wedge was tested against raw gamma 110 (∉ [80, 88]) and never fired.
    """
    from custom_components.adaptive_cover_pro.const import ReasonCode

    roof = _roof_with_blind_spot(
        roof_pitch=12,
        sol_elev=10.0,
        gamma=110.0,
        blind_spot_left=88,  # signed-gamma upper edge (#247)
        blind_spot_right=-80,  # negated lower edge → wedge [80, 88]
    )
    # Sanity-pin the geometry: raw gamma 110 maps into the effective FOV.
    assert roof.gamma == pytest.approx(110.0)
    assert roof.fov_angle == pytest.approx(83.8, abs=0.5)
    # Glass accepts (effective gamma inside FOV), but the obstruction is masked.
    assert roof.valid is True
    assert roof.is_sun_in_blind_spot is True
    assert roof.direct_sun_valid is False
    assert roof.control_state_reason_code == ReasonCode.ENGINE_DEFAULT_BLIND_SPOT


# ---------------------------------------------------------------------------
# Frame-injection no-op locks (§299 β=90 anchor + vertical no-op)
# ---------------------------------------------------------------------------


def test_blind_spot_bit_identical_at_vertical_pitch():
    """β=90° roof and vertical cover agree on the blind spot bit-for-bit (§299).

    At vertical pitch ``fov_angle == gamma``, so routing the blind spot through
    the acceptance frame is a pure no-op — the roof-window result equals the
    plain vertical-cover result for the same config + sun.

    The wedge (signed gamma left=25, right=-15 → ``15 <= gamma <= 25``) is chosen
    so the test's gamma=20 sits INSIDE it: both covers report a genuine wedge HIT
    (``True``), not a shared ``False`` miss that would never exercise the frame.
    """
    kwargs = {
        "sol_elev": 30.0,
        "gamma": 20.0,
        "blind_spot_left": 25,
        "blind_spot_right": -15,
    }
    # Guard: gamma=20 is inside the wedge (-right <= gamma <= left).
    assert -kwargs["blind_spot_right"] <= kwargs["gamma"] <= kwargs["blind_spot_left"]
    roof = _roof_with_blind_spot(roof_pitch=90, **kwargs)
    vert = AdaptiveVerticalCover(
        logger=MagicMock(),
        sol_azi=_WIN_AZI - kwargs["gamma"],
        sol_elev=kwargs["sol_elev"],
        sun_data=_safe_sun_data(),
        config=make_cover_config(
            win_azi=_WIN_AZI,
            fov_left=90,
            fov_right=90,
            blind_spot_left=kwargs["blind_spot_left"],
            blind_spot_right=kwargs["blind_spot_right"],
            blind_spot_on=True,
        ),
        vert_config=make_vertical_config(distance=1.0, h_win=2.0),
    )
    # Both must be True (a real wedge hit), and bit-identical.
    assert roof.is_sun_in_blind_spot is True
    assert roof.is_sun_in_blind_spot == vert.is_sun_in_blind_spot


def test_vertical_cover_blind_spot_equals_raw_sun_geometry():
    """A vertical cover's blind spot equals raw ``SunGeometry`` (fov_angle == gamma).

    Locks the no-op claim: because ``fov_angle`` defaults to ``gamma`` for a
    vertical cover, routing the blind spot through the acceptance frame leaves the
    result identical to the raw-gamma ``SunGeometry`` property.

    The wedge (signed gamma left=25, right=-15 → ``15 <= gamma <= 25``) puts the
    test's gamma=20 INSIDE, so the equality compares two genuine ``True`` hits.
    """
    gamma, sol_elev = 20.0, 30.0
    blind_spot_left, blind_spot_right = 25, -15
    # Guard: gamma=20 is inside the wedge (-right <= gamma <= left).
    assert -blind_spot_right <= gamma <= blind_spot_left
    cover_config = make_cover_config(
        win_azi=_WIN_AZI,
        fov_left=90,
        fov_right=90,
        blind_spot_left=blind_spot_left,
        blind_spot_right=blind_spot_right,
        blind_spot_on=True,
    )
    vert = AdaptiveVerticalCover(
        logger=MagicMock(),
        sol_azi=_WIN_AZI - gamma,
        sol_elev=sol_elev,
        sun_data=_safe_sun_data(),
        config=cover_config,
        vert_config=make_vertical_config(distance=1.0, h_win=2.0),
    )
    sg = SunGeometry(
        _WIN_AZI - gamma, sol_elev, _safe_sun_data(), cover_config, MagicMock()
    )
    assert vert.fov_angle == pytest.approx(sg.gamma)
    assert vert.is_sun_in_blind_spot is True  # a real wedge hit, not a shared miss
    assert vert.is_sun_in_blind_spot == sg.is_sun_in_blind_spot


# ---------------------------------------------------------------------------
# Step 7 — per-day predicted sun window is tilt-aware (#729)
#
# The live scalar direct_sun_valid became tilt-aware in #728, but the per-day
# predicted window (solar_times_with_position) still used the inline raw-azimuth
# vertical gate. These tests pin the predicted window to the live transitions.
# ---------------------------------------------------------------------------

_SWEEP_GAMMAS = [130 - 5 * i for i in range(53)]  # +130 .. -130


def test_roof_predicted_window_matches_live_direct_sun_valid_pitch45():
    sun_data = _make_sweep_sun_data(gammas=_SWEEP_GAMMAS, elev=60.0)
    roof = _roof(roof_pitch=45, sol_elev=60.0, gamma=0.0, fov_left=85, fov_right=85)
    roof.sun_data = sun_data
    start, end = roof.solar_times_with_position()
    exp_start, exp_end = _live_window_oracle(
        roof_pitch=45, elev=60.0, gammas=_SWEEP_GAMMAS, fov=85, sun_data=sun_data
    )
    assert start is not None and end is not None
    assert start[0] == exp_start[0] and end[0] == exp_end[0]
    assert start[1] == pytest.approx(exp_start[1]) and start[2] == pytest.approx(
        exp_start[2]
    )
    assert end[1] == pytest.approx(exp_end[1]) and end[2] == pytest.approx(exp_end[2])


def test_roof_predicted_window_wider_than_vertical_gate_pitch45():
    sun_data = _make_sweep_sun_data(gammas=_SWEEP_GAMMAS, elev=60.0)
    roof = _roof(roof_pitch=45, sol_elev=60.0, gamma=0.0, fov_left=85, fov_right=85)
    roof.sun_data = sun_data
    vert = _vertical(sol_elev=60.0, gamma=0.0, fov_left=85, fov_right=85)
    vert.sun_data = sun_data
    r_start, r_end = roof.solar_times_with_position()
    v_start, v_end = vert.solar_times_with_position()
    assert r_start[0] < v_start[0]
    assert r_end[0] > v_end[0]


def test_roof_predicted_window_matches_live_direct_sun_valid_pitch30():
    sun_data = _make_sweep_sun_data(gammas=_SWEEP_GAMMAS, elev=45.0)
    roof = _roof(roof_pitch=30, sol_elev=45.0, gamma=0.0, fov_left=85, fov_right=85)
    roof.sun_data = sun_data
    start, end = roof.solar_times_with_position()
    exp_start, exp_end = _live_window_oracle(
        roof_pitch=30, elev=45.0, gammas=_SWEEP_GAMMAS, fov=85, sun_data=sun_data
    )
    assert start[0] == exp_start[0] and end[0] == exp_end[0]


def test_roof_pitch90_predicted_window_bitforbit_vertical():
    sun_data = _make_sweep_sun_data(gammas=_SWEEP_GAMMAS, elev=60.0)
    roof = _roof(roof_pitch=90, sol_elev=60.0, gamma=0.0, fov_left=45, fov_right=45)
    roof.sun_data = sun_data
    vert = _vertical(sol_elev=60.0, gamma=0.0, fov_left=45, fov_right=45)
    vert.sun_data = sun_data
    assert roof.solar_times_with_position() == vert.solar_times_with_position()
