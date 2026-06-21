"""Tests for the oscillating (drop-arm) awning cover type and the config
abstraction capabilities it exercises (#412).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.config_types import OscillatingConfig
import voluptuous as vol

from custom_components.adaptive_cover_pro.const import (
    CONF_ARM_LENGTH,
    CONF_AWNING_ANGLE,
    CONF_AWNING_MAX_ANGLE,
    CONF_AWNING_MIN_ANGLE,
    CONF_HEIGHT_WIN,
    CoverType,
    _RANGE_ARM_LENGTH,
)
from custom_components.adaptive_cover_pro.cover_types import POLICY_REGISTRY, get_policy
from custom_components.adaptive_cover_pro.engine.covers import AdaptiveOscillatingCover
from tests.cover_helpers import (
    make_cover_config,
    make_horizontal_config,
    make_vertical_config,
)


def test_registered_and_in_picker():
    assert "cover_oscillating_awning" in POLICY_REGISTRY
    assert CoverType.OSCILLATING_AWNING.value == "cover_oscillating_awning"


def test_geometry_has_new_keys_and_drops_angle():
    policy = get_policy("cover_oscillating_awning")
    geo = {str(m) for m in policy.build_section_schema("geometry").schema}
    assert {CONF_ARM_LENGTH, CONF_AWNING_MIN_ANGLE, CONF_AWNING_MAX_ANGLE} <= geo
    # The fixed-angle field is disabled for this cover type.
    assert CONF_AWNING_ANGLE not in geo


def test_disabled_key_excluded_from_live_keys():
    policy = get_policy("cover_oscillating_awning")
    live = policy.live_option_keys()
    assert CONF_AWNING_ANGLE not in live
    assert CONF_ARM_LENGTH in live


def test_validation_rejects_angle_accepts_arm_length():
    from homeassistant.core import ServiceValidationError

    from custom_components.adaptive_cover_pro.services.options_service import (
        validate_options_patch,
    )

    # arm_length is valid for this cover type.
    validate_options_patch(
        {CONF_ARM_LENGTH: 0.8}, {}, sensor_type="cover_oscillating_awning"
    )
    # The fixed awning angle is rejected (it belongs to the fixed awning type).
    with pytest.raises(ServiceValidationError):
        validate_options_patch(
            {CONF_AWNING_ANGLE: 10}, {}, sensor_type="cover_oscillating_awning"
        )


# gamma = (win_azi − sol_azi + 180) % 360 − 180.  With win_azi=180 a sol_azi of
# (180 − gamma) produces exactly the requested surface-solar azimuth, so these
# tests can drive a real engine (sill/depth/distance wired through the inherited
# vertical solve) while pinning gamma precisely.
_WIN_AZI = 180.0


def _osc_full(
    *,
    arm: float = 0.85,
    housing: float = 0.15,
    h_win: float = 1.5,
    sol_elev: float,
    gamma: float,
    distance: float = 0.01,
    window_depth: float = 0.15,
    sill_height: float = 0.73,
    lo: float = 0.0,
    hi: float = 180.0,
) -> AdaptiveOscillatingCover:
    """Build a fully-plumbed oscillating engine driving the real geometry.

    ``gamma`` is realised by offsetting ``sol_azi`` from the window azimuth.
    """
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    return AdaptiveOscillatingCover(
        logger=MagicMock(),
        sol_azi=_WIN_AZI - gamma,
        sol_elev=sol_elev,
        sun_data=sun_data,
        config=make_cover_config(win_azi=_WIN_AZI, fov_left=90, fov_right=103),
        vert_config=make_vertical_config(
            distance=distance,
            h_win=h_win,
            window_depth=window_depth,
            sill_height=sill_height,
        ),
        horiz_config=make_horizontal_config(),
        osc_config=OscillatingConfig(
            arm_length=arm, min_angle=lo, max_angle=hi, housing_offset=housing
        ),
    )


def test_low_elevation_extends_past_fifty_percent():
    """#586: drop-arm lip must descend past horizontal at low sun.

    Reporter config (arm 0.85 m, housing 0.15 m, window 1.5 m). At the reported
    sun (elev 29.9°, gamma 57.6°) the old horizontal-reach model capped the
    position at exactly 50%; the vertical-drop model must drive the arm past
    horizontal (θ > 90° → pos > 50%). Lowering the sun further drops the lip
    even more (monotonic in coverage).
    """
    mid = _osc_full(sol_elev=29.9, gamma=57.6).calculate_percentage()
    low = _osc_full(sol_elev=8.0, gamma=57.6).calculate_percentage()
    assert mid > 50.0
    assert low > mid
    assert low <= 100.0


def test_reporter_case_pinned_position():
    """#586 reporter geometry pins to the wired vertical-drop solution.

    With the reporter's sill (0.73 m) the inherited vertical solve clamps the
    protected boundary to the window bottom (0.0), so the lip must shade the
    full face. The solved arm angle is ≈133° → ≈73.9%.
    """
    pos = _osc_full(sol_elev=29.9, gamma=57.6).calculate_percentage()
    assert pos == pytest.approx(73.9, abs=1.0)


def test_position_non_increasing_as_elevation_rises():
    """Higher sun → less coverage needed → position non-increasing (fixed gamma)."""
    positions = [
        _osc_full(sol_elev=elev, gamma=57.6).calculate_percentage()
        for elev in (5.0, 15.0, 30.0, 45.0, 60.0, 75.0)
    ]
    for higher, lower in zip(positions, positions[1:], strict=False):
        assert lower <= higher + 1e-6


def test_housing_offset_affects_position():
    """housing_offset (pivot height above window top) is now load-bearing.

    With exposed glass above the protected boundary (sill 0 so the vertical
    solve leaves a real boundary > 0), a higher pivot lets the lip start higher
    and reach the boundary at a larger arm angle → higher position.
    """
    common = {"sol_elev": 29.9, "gamma": 57.6, "sill_height": 0.0, "distance": 0.8}
    low = _osc_full(housing=0.0, **common).calculate_percentage()
    high = _osc_full(housing=0.5, **common).calculate_percentage()
    assert high != low
    assert high > low


def test_sill_or_depth_affects_position():
    """Changing sill_height / window_depth changes the protected boundary.

    Proves the boundary stays wired to the inherited sill/depth/distance solve
    rather than being pinned at the window bottom.
    """
    # A small sill brings the protected boundary into the window interior so the
    # window_depth contribution (which raises the boundary) is observable rather
    # than clamped at the window top.
    base = {"sol_elev": 35.0, "gamma": 57.6, "sill_height": 0.4, "distance": 0.6}
    baseline = _osc_full(window_depth=0.0, **base).calculate_percentage()
    deeper = _osc_full(window_depth=0.4, **base).calculate_percentage()
    assert deeper != baseline

    sill_base = {"sol_elev": 35.0, "gamma": 57.6, "window_depth": 0.0, "distance": 1.2}
    sill_low = _osc_full(sill_height=0.0, **sill_base).calculate_percentage()
    sill_high = _osc_full(sill_height=0.5, **sill_base).calculate_percentage()
    assert sill_high != sill_low


def test_awn_properties_track_arm():
    """The horizontal-parent reach properties stay wired for substitutability.

    ``awn_length`` reflects the arm length and ``awn_angle`` is flat (0) so the
    inherited ``AdaptiveHorizontalCover`` paths remain Liskov-substitutable.
    """
    eng = _osc_full(arm=0.85, sol_elev=29.9, gamma=57.6)
    assert eng.awn_length == pytest.approx(0.85)
    assert eng.awn_angle == 0.0


def test_degenerate_guards():
    """Degenerate geometry stays bounded and never raises."""
    # arm = 0 → no reach → fully closed.
    zero_arm = _osc_full(arm=0.0, sol_elev=29.9, gamma=57.6).calculate_percentage()
    assert zero_arm == 0.0
    # hi <= lo → degenerate sweep → 0.0.
    flat = _osc_full(sol_elev=29.9, gamma=57.6, lo=90.0, hi=90.0).calculate_percentage()
    assert flat == 0.0
    # gamma ≈ 90° must use the cos-gamma clamp (no ZeroDivision).
    grazing = _osc_full(sol_elev=29.9, gamma=89.9).calculate_percentage()
    assert 0.0 <= grazing <= 100.0
    # Very high sun → little coverage needed but never negative.
    high_sun = _osc_full(sol_elev=85.0, gamma=10.0).calculate_percentage()
    assert high_sun >= 0.0


def test_lip_shadow_top_helper_geometry():
    """#586: lip-shadow helper geometry.

    At the sweep endpoints the reach is zero, so the shadow top sits exactly at
    the lip: a full arm above the pivot at θ=0, a full arm below it at θ=180°.
    Between them the shadow descends to a mid-sweep minimum (max coverage) — the
    lip's reach (hence foreshortened drop) peaks near θ=90° while it keeps
    falling, so coverage is deepest past horizontal, not at θ=180°. This is the
    physics the solver's fail-open branch relies on (argmin lands mid-sweep).
    """
    from custom_components.adaptive_cover_pro.engine.covers.oscillating import (
        _lip_shadow_top,
    )

    kw = {"arm_length": 0.85, "pivot_y": 1.65, "sol_elev": 29.9, "gamma": 57.6}
    # Endpoints: no reach → shadow top is the bare lip height.
    assert _lip_shadow_top(0.0, **kw) == pytest.approx(1.65 + 0.85)
    assert _lip_shadow_top(180.0, **kw) == pytest.approx(1.65 - 0.85)
    # Descends monotonically from θ=0 up to a mid-sweep minimum…
    rising = [_lip_shadow_top(t, **kw) for t in (0.0, 30.0, 60.0, 90.0, 120.0)]
    for higher, lower in zip(rising, rising[1:], strict=False):
        assert lower < higher
    # …and the deepest coverage is past horizontal (θ > 90°), below both ends.
    deepest = min(_lip_shadow_top(t, **kw) for t in range(0, 181))
    assert deepest < _lip_shadow_top(0.0, **kw)
    assert deepest < _lip_shadow_top(180.0, **kw)


def test_solver_drives_position_to_reach_boundary():
    """#586: the solver picks a larger arm angle for a lower boundary.

    Lower protected boundaries demand the lip drop further (larger θ). A high
    boundary (at the lip's starting height) needs almost no sweep; the full-face
    case (boundary=0) must drive the arm strictly past horizontal (>50%), which
    the old reach-capped model could never do.
    """
    high = _osc_full(sol_elev=29.9, gamma=57.6)
    high._protected_boundary = lambda: 2.3  # type: ignore[method-assign]
    low = _osc_full(sol_elev=29.9, gamma=57.6)
    low._protected_boundary = lambda: 0.0  # type: ignore[method-assign]

    high_pos = high.calculate_percentage()
    low_pos = low.calculate_percentage()
    assert high_pos < 50.0  # lip barely needs to move
    assert low_pos > 50.0  # must sweep past horizontal — impossible pre-#586
    assert low_pos > high_pos


def test_reporter_sweep_is_linear_175_over_100():
    """#412 reporter: 175° total sweep ⇒ 1.75° per 1% (forward mapping)."""
    cfg = OscillatingConfig.from_options({CONF_AWNING_MAX_ANGLE: 175})
    span = cfg.max_angle - cfg.min_angle
    assert span / 100 == pytest.approx(1.75)


def test_config_from_options_defaults():
    cfg = OscillatingConfig.from_options({})
    assert cfg.arm_length == 0.8
    assert cfg.min_angle == 0
    assert cfg.max_angle == 175


def test_geometry_schema_accepts_arm_length_up_to_6m():
    """Regression for #636: arm_length selector must accept values up to 6 m.

    The config-flow geometry schema (the path the user hits at config-entry
    creation) previously capped arm_length at 3 m, causing "Value 3.6 is too
    large" when a user entered a physically-valid 3.6 m arm. The selector bound
    must match _RANGE_ARM_LENGTH so the UI cap and the service-validator cap
    share a single source of truth.
    """
    from custom_components.adaptive_cover_pro.cover_types.oscillating_awning import (
        geometry_oscillating_schema,
    )

    schema = geometry_oscillating_schema()  # hass=None → metric (metres)

    # Fill required geometry keys with valid in-range defaults; only arm_length
    # is under test here.
    base = {
        CONF_HEIGHT_WIN: 1.5,
        CONF_AWNING_MIN_ANGLE: 0,
        CONF_AWNING_MAX_ANGLE: 175,
    }

    # 3.6 m was the reported value that was rejected.
    result_36 = schema({**base, CONF_ARM_LENGTH: 3.6})
    assert result_36[CONF_ARM_LENGTH] == pytest.approx(3.6)

    # 6.0 m is the ceiling defined by _RANGE_ARM_LENGTH.
    result_max = schema({**base, CONF_ARM_LENGTH: _RANGE_ARM_LENGTH[1]})
    assert result_max[CONF_ARM_LENGTH] == pytest.approx(_RANGE_ARM_LENGTH[1])

    # Values strictly above the ceiling must still be rejected.
    with pytest.raises(vol.Invalid):
        schema({**base, CONF_ARM_LENGTH: 6.1})
