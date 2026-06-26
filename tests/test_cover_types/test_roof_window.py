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

from datetime import UTC, datetime, timedelta
from math import atan, cos, degrees, radians, sin, tan
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.config_types import RoofWindowConfig
from custom_components.adaptive_cover_pro.cover_types import POLICY_REGISTRY, get_policy
from custom_components.adaptive_cover_pro.engine.covers import (
    AdaptiveRoofWindowCover,
    AdaptiveVerticalCover,
)
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
