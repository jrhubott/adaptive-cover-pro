"""Illumination gate — the sun behind the cover plane (issue #1030).

``f = tan(elev)/cos(gamma)`` is the textbook Vertical Shadow Angle tangent, and
VSA is only defined for ``|gamma| < 90``. Past that the sun is BEHIND the glass
and the face is not lit at all, so the projection is not a hard number to
compute — it is a question with no answer. Before #1030 every vertical-family
engine answered it anyway (with a sign-flipped ``cos gamma``), slamming the
cover to an endpoint one hundredth of a degree past 90.

The fix promotes the illumination test the roof window already shipped
(``cos(AOI) > 0``) to :class:`AdaptiveGeneralCover`, so an unlit face never
reaches the projection and the pipeline routes to the DefaultHandler instead.
These tests pin the gate itself (not a position value), the pitched cover types
that legitimately track past ``gamma = 90``, and the continuity of every engine
across the former discontinuity.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.config_types import (
    LouveredRoofConfig,
    OscillatingConfig,
    RoofWindowConfig,
)
from custom_components.adaptive_cover_pro.const import ReasonCode
from custom_components.adaptive_cover_pro.engine.covers.louvered_roof import (
    AdaptiveLouveredRoofCover,
)
from custom_components.adaptive_cover_pro.engine.covers.oscillating import (
    AdaptiveOscillatingCover,
)
from custom_components.adaptive_cover_pro.engine.covers.roof_window import (
    AdaptiveRoofWindowCover,
)
from custom_components.adaptive_cover_pro.engine.covers.tilt import AdaptiveTiltCover
from custom_components.adaptive_cover_pro.engine.covers.venetian import (
    VenetianCoverCalculation,
)
from custom_components.adaptive_cover_pro.engine.covers.vertical import (
    AdaptiveVerticalCover,
)

from tests.cover_helpers import (
    make_cover_config,
    make_daytime_sun_data,
    make_tilt_config,
    make_vertical_config,
)

pytestmark = pytest.mark.unit

# The real field config from #1025's reporter: a 137° acceptance angle admits
# |gamma| > 90, which is exactly how the sign flip reaches production.
_FOV = 137
_WIN_AZI = 180
_SOL_ELEV = 30.0

# gamma = (win_azi − sol_azi + 180) % 360 − 180, so sol_azi = win_azi − gamma
# realises an exact surface-solar azimuth.


def _cover_config(**overrides):
    return make_cover_config(
        win_azi=_WIN_AZI, fov_left=_FOV, fov_right=_FOV, **overrides
    )


def _vertical(gamma: float, sol_elev: float = _SOL_ELEV) -> AdaptiveVerticalCover:
    return AdaptiveVerticalCover(
        logger=MagicMock(),
        sol_azi=_WIN_AZI - gamma,
        sol_elev=sol_elev,
        sun_data=make_daytime_sun_data(),
        config=_cover_config(),
        vert_config=make_vertical_config(distance=0.5, h_win=2.0),
    )


def _tilt(
    gamma: float, sol_elev: float = _SOL_ELEV, mode: str = "mode1"
) -> AdaptiveTiltCover:
    return AdaptiveTiltCover(
        logger=MagicMock(),
        sol_azi=_WIN_AZI - gamma,
        sol_elev=sol_elev,
        sun_data=make_daytime_sun_data(),
        config=_cover_config(),
        tilt_config=make_tilt_config(mode=mode),
    )


def _venetian(gamma: float, sol_elev: float = _SOL_ELEV) -> VenetianCoverCalculation:
    return VenetianCoverCalculation(
        config=_cover_config(),
        vert_config=make_vertical_config(distance=0.5, h_win=2.0),
        tilt_config=make_tilt_config(),
        sun_data=make_daytime_sun_data(),
        sol_azi=_WIN_AZI - gamma,
        sol_elev=sol_elev,
        logger=MagicMock(),
    )


def _oscillating(gamma: float, sol_elev: float = _SOL_ELEV) -> AdaptiveOscillatingCover:
    """Build the drop-arm awning EXACTLY as production builds it.

    ``OscillatingAwningCoverType.build_calc_engine`` passes ``vert_config`` +
    ``osc_config`` only — ``horiz_config`` stays ``None``. The horizontal
    area-mode opt-out is inherited by this subclass, so it must be None-safe and
    must fall through to the gate; otherwise the fail-open bug survives here.
    """
    return AdaptiveOscillatingCover(
        logger=MagicMock(),
        sol_azi=_WIN_AZI - gamma,
        sol_elev=sol_elev,
        sun_data=make_daytime_sun_data(),
        config=_cover_config(),
        vert_config=make_vertical_config(distance=0.5, h_win=2.0),
        osc_config=OscillatingConfig(arm_length=0.85, housing_offset=0.15),
    )


def _roof_window(gamma: float, pitch: float) -> AdaptiveRoofWindowCover:
    return AdaptiveRoofWindowCover(
        logger=MagicMock(),
        sol_azi=_WIN_AZI - gamma,
        sol_elev=_SOL_ELEV,
        sun_data=make_daytime_sun_data(),
        config=_cover_config(),
        vert_config=make_vertical_config(distance=0.5, h_win=2.0),
        roof_config=RoofWindowConfig(roof_pitch=pitch),
    )


def _louvered(gamma: float, pitch: float = 0.0) -> AdaptiveLouveredRoofCover:
    return AdaptiveLouveredRoofCover(
        logger=MagicMock(),
        sol_azi=_WIN_AZI - gamma,
        sol_elev=_SOL_ELEV,
        sun_data=make_daytime_sun_data(),
        config=_cover_config(),
        tilt_config=make_tilt_config(slat_distance=0.02, depth=0.03, mode="mode2"),
        roof_config=LouveredRoofConfig(roof_pitch=pitch),
    )


class TestIlluminationGate:
    """``direct_sun_valid`` must go False once the sun is behind the plane."""

    def test_vertical_direct_sun_valid_flips_at_90(self):
        assert _vertical(89.0).direct_sun_valid is True
        assert _vertical(91.0).direct_sun_valid is False
        assert _vertical(120.0).direct_sun_valid is False

    def test_tilt_direct_sun_valid_flips_at_90(self):
        assert _tilt(89.0).direct_sun_valid is True
        assert _tilt(91.0).direct_sun_valid is False
        assert _tilt(120.0).direct_sun_valid is False

    def test_venetian_direct_sun_valid_flips_at_90(self):
        assert _venetian(89.0).direct_sun_valid is True
        assert _venetian(91.0).direct_sun_valid is False

    def test_oscillating_direct_sun_valid_flips_at_90(self):
        """Anti-leak lock: the horizontal area-mode opt-out must not reach here."""
        assert _oscillating(89.0).direct_sun_valid is True
        assert _oscillating(91.0).direct_sun_valid is False
        assert _oscillating(120.0).direct_sun_valid is False

    def test_boundary_gamma_90_still_lit(self):
        """``cos(radians(90)) = +6.12e-17 > 0`` — the gate is strict, so 90 is lit.

        Pins the boundary so ``test_fov_exit_sun_outside_fov_left`` (gamma = 90
        exactly, expecting the acceptance-angle reason) can never move.
        """
        assert _vertical(90.0).direct_sun_valid is True
        assert _tilt(90.0).direct_sun_valid is True

    def test_pitched_planes_keep_tracking_past_90(self):
        """The gate is ``cos(AOI) > 0``, NOT ``cos(gamma) > 0`` — pitch-aware.

        A 30°-pitch roof window and a flat louvered roof are both genuinely lit
        at gamma = 120 (``cos(AOI) = 0.2165`` and ``0.5``), so promoting the gate
        to the base must leave them tracking.
        """
        roof = _roof_window(120.0, pitch=30.0)
        assert roof.valid_elevation is True
        assert roof.direct_sun_valid is True

        louvered = _louvered(120.0, pitch=0.0)
        assert louvered.valid_elevation is True
        assert louvered.direct_sun_valid is True

    def test_reason_code_sun_behind_plane(self):
        """In-FOV but unlit reports its own reason, not an azimuth/elevation exit."""
        cover = _vertical(120.0)
        assert cover.in_fov is True
        assert (
            cover.control_state_reason_code
            == ReasonCode.ENGINE_DEFAULT_SUN_BEHIND_PLANE
        )
        assert cover.control_state_reason == "Default: Sun Behind Plane"


# ---------------------------------------------------------------------------
# Continuity across the former discontinuity
# ---------------------------------------------------------------------------

_SWEEP = [85.0 + 0.05 * i for i in range(int((95.0 - 85.0) / 0.05) + 1)]
# One 0.01° step of azimuth used to flip a blind from 100 % to 0 %. Anything
# above a couple of percent per 0.05° step is a slam, not a projection.
_MAX_STEP_PCT = 2.0


def _assert_continuous(values: list[float]) -> None:
    """Every sample finite, in [0, 100], and no jump between adjacent samples."""
    for gamma, value in zip(_SWEEP, values, strict=True):
        assert math.isfinite(value), f"non-finite at gamma={gamma}: {value}"
        assert 0.0 <= value <= 100.0, f"out of range at gamma={gamma}: {value}"
    for gamma, prev, cur in zip(_SWEEP[1:], values[:-1], values[1:], strict=True):
        assert (
            abs(cur - prev) <= _MAX_STEP_PCT
        ), f"discontinuity at gamma={gamma}: {prev} → {cur}"


class TestGammaContinuityAcrossNinety:
    """Sweep gamma 85→95 calling the raw engine, bypassing the routing gate.

    ``test_pole_regions_finite_and_bounded`` only asserts "finite and in
    [0, 100]" — a 100 → 0 slam passes that. These sweeps pin the engine's own
    continuity independent of whether the pipeline would have routed there.
    """

    def test_vertical_percentage_is_continuous(self):
        _assert_continuous([_vertical(g).calculate_percentage() for g in _SWEEP])

    def test_oscillating_percentage_is_continuous(self):
        _assert_continuous([_oscillating(g).calculate_percentage() for g in _SWEEP])

    @pytest.mark.parametrize("mode", ["mode1", "mode2"])
    def test_tilt_percentage_is_continuous(self, mode):
        _assert_continuous([_tilt(g, mode=mode).calculate_percentage() for g in _SWEEP])

    def test_venetian_both_axes_are_continuous(self):
        duals = [_venetian(g).calculate_dual() for g in _SWEEP]
        _assert_continuous([float(d.position) for d in duals])
        _assert_continuous([float(d.tilt) for d in duals])
