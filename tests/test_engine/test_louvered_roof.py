"""Engine tests for the louvered (lamella) roof cover type (#830).

The louvered-roof engine is the cross-product of the venetian slat cut-off
solver and the pitched-plane sun geometry:

* the slat cut-off equation is shared with ``engine/covers/tilt.py`` via the
  extracted ``slat_cutoff_angle`` helper (this file pins the extraction guard),
* the profile angle ``beta`` is driven by the roof-plane slope ratio
  (``roof_slope_ratio``, extracted from ``roof_window._project_drop``),
* the illumination / FOV gates borrow ``roof_cos_aoi`` / ``roof_effective_gamma``.

The reduction anchors below are hand-computed from first principles, NOT copied
from the venetian suite.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock

import numpy as np
import pytest

from custom_components.adaptive_cover_pro.config_types import LouveredRoofConfig
from custom_components.adaptive_cover_pro.engine.covers.louvered_roof import (
    AdaptiveLouveredRoofCover,
)
from custom_components.adaptive_cover_pro.engine.covers.roof_window import (
    roof_slope_ratio,
)
from custom_components.adaptive_cover_pro.engine.covers.tilt import (
    AdaptiveTiltCover,
    slat_cutoff_angle,
)

from tests.cover_helpers import make_cover_config, make_tilt_config

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _louvered(
    *,
    sol_azi: float,
    sol_elev: float,
    roof_pitch: float,
    slat_distance: float = 0.02,
    depth: float = 0.03,
    mode: str = "mode2",
    win_azi: float = 180.0,
    fov_left: float = 90.0,
    fov_right: float = 90.0,
) -> AdaptiveLouveredRoofCover:
    """Build a louvered-roof engine at an explicit sun/slat geometry."""
    return AdaptiveLouveredRoofCover(
        logger=MagicMock(),
        sol_azi=sol_azi,
        sol_elev=sol_elev,
        sun_data=MagicMock(),
        config=make_cover_config(
            win_azi=win_azi, fov_left=fov_left, fov_right=fov_right
        ),
        tilt_config=make_tilt_config(
            slat_distance=slat_distance, depth=depth, mode=mode
        ),
        roof_config=LouveredRoofConfig(roof_pitch=roof_pitch),
    )


def _tilt(
    *,
    sol_azi: float,
    sol_elev: float,
    slat_distance: float = 0.02,
    depth: float = 0.03,
    mode: str = "mode2",
    win_azi: float = 180.0,
) -> AdaptiveTiltCover:
    """Build a plain venetian/tilt engine (the pitch=90 reduction target)."""
    return AdaptiveTiltCover(
        logger=MagicMock(),
        sol_azi=sol_azi,
        sol_elev=sol_elev,
        sun_data=MagicMock(),
        config=make_cover_config(win_azi=win_azi, fov_left=90, fov_right=90),
        tilt_config=make_tilt_config(
            slat_distance=slat_distance, depth=depth, mode=mode
        ),
    )


# ---------------------------------------------------------------------------
# Extraction guard — slat_cutoff_angle (shared with tilt.py)
# ---------------------------------------------------------------------------


class TestSlatCutoffAngleExtraction:
    """``slat_cutoff_angle`` returns hand-computed values; tilt delegates to it."""

    def test_matches_hand_computed_positive_discriminant(self) -> None:
        beta = math.atan(0.893)  # ~0.7289 rad (a venetian anchor profile angle)
        slat_distance, depth = 0.02, 0.03
        ratio = slat_distance / depth  # 0.6667
        result, discriminant, negative = slat_cutoff_angle(beta, slat_distance, depth)
        # Independent hand formula (the MDPI cut-off expression).
        expected_disc = math.tan(beta) ** 2 - ratio**2 + 1
        expected_deg = math.degrees(
            2 * math.atan((math.tan(beta) + math.sqrt(expected_disc)) / (1 + ratio))
        )
        assert negative is False
        assert discriminant == pytest.approx(expected_disc)
        assert result == pytest.approx(expected_deg)
        # Pin the concrete number so a formula drift is caught.
        assert result == pytest.approx(101.9, abs=0.1)

    def test_negative_discriminant_returns_zero(self) -> None:
        # tan(beta) small, ratio large → discriminant < 0 → closed fallback.
        beta = math.radians(5.0)
        result, discriminant, negative = slat_cutoff_angle(beta, 0.10, 0.02)
        assert negative is True
        assert result == 0.0
        assert discriminant < 0

    def test_tilt_delegates_byte_for_byte(self) -> None:
        # The tilt engine's slat angle equals the helper driven by tilt's beta.
        cover = _tilt(sol_azi=200, sol_elev=40, mode="mode2")
        beta = cover.beta
        expected, _, _ = slat_cutoff_angle(beta, cover.slat_distance, cover.depth)
        # Re-run the tilt engine and compare its raw slat angle (safety_margin=0).
        result = cover.calculate_position()
        assert result == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Extraction guard — roof_slope_ratio (shared with roof_window.py)
# ---------------------------------------------------------------------------


class TestRoofSlopeRatioExtraction:
    """``roof_slope_ratio`` returns hand-computed values; anchors at pitch 0/90."""

    def test_pitch_90_returns_f(self) -> None:
        # Vertical plane: slope ratio == tan(elev)/cos(gamma) (the vertical f).
        for elev, gamma in [(40, 0), (30, 20), (55, -35)]:
            f = math.tan(math.radians(elev)) / math.cos(math.radians(gamma))
            assert roof_slope_ratio(gamma, elev, 90) == pytest.approx(f)

    def test_pitch_0_gamma_0_returns_negative_reciprocal(self) -> None:
        # Flat roof, aligned sun: slope ratio == -1/f == -cot(elev).
        for elev in [20, 40, 65]:
            f = math.tan(math.radians(elev))
            assert roof_slope_ratio(0, elev, 0) == pytest.approx(-1.0 / f)
            assert roof_slope_ratio(0, elev, 0) == pytest.approx(
                -1.0 / math.tan(math.radians(elev))
            )


# ---------------------------------------------------------------------------
# Reduction anchor 1 — pitch = 90 collapses to the venetian/tilt profile angle
# ---------------------------------------------------------------------------


class TestPitch90ReducesToVenetian:
    """At vertical pitch the louvered beta equals the tilt beta (true superset)."""

    @pytest.mark.parametrize(
        ("sol_azi", "sol_elev"),
        [(180, 40), (200, 30), (160, 55), (210, 15)],
    )
    def test_beta_matches_tilt(self, sol_azi: float, sol_elev: float) -> None:
        louvered = _louvered(sol_azi=sol_azi, sol_elev=sol_elev, roof_pitch=90)
        tilt = _tilt(sol_azi=sol_azi, sol_elev=sol_elev)
        # Sun in front (cos(gamma) > 0, elev > 0) so abs() is a no-op.
        assert louvered.beta == pytest.approx(tilt.beta)

    def test_position_matches_tilt_at_pitch_90(self) -> None:
        louvered = _louvered(sol_azi=205, sol_elev=35, roof_pitch=90, mode="mode2")
        tilt = _tilt(sol_azi=205, sol_elev=35, mode="mode2")
        assert louvered.calculate_position() == pytest.approx(tilt.calculate_position())


# ---------------------------------------------------------------------------
# Reduction anchor 2 — flat roof: abs(beta) == 90° − elev  (complement)
# ---------------------------------------------------------------------------


class TestFlatRoofComplementAnchor:
    """pitch=0, gamma=0 → the profile angle is the COMPLEMENT of the vertical case.

    Hand derivation (2-D cross-section ⟂ to the slat axis): a flat slat plane
    has a vertical normal. A sun ray at elevation ``e`` above the horizon makes
    angle ``90 − e`` with that vertical normal. The venetian solver measures the
    profile angle from the plane's in-face reference, so beta = 90 − e.
    """

    @pytest.mark.parametrize("sol_elev", [20, 35, 50, 65])
    def test_abs_beta_is_complement(self, sol_elev: float) -> None:
        louvered = _louvered(sol_azi=180, sol_elev=sol_elev, roof_pitch=0)
        beta_deg = math.degrees(louvered.beta)
        assert abs(beta_deg) == pytest.approx(90.0 - sol_elev)


# ---------------------------------------------------------------------------
# Reduction anchor 3 — AOI illumination gate
# ---------------------------------------------------------------------------


class TestAoiGate:
    """The working-face illumination gate borrows ``roof_cos_aoi``."""

    @pytest.mark.parametrize("gamma_azi", [180, 120, 250, 90, 270])
    def test_flat_roof_valid_iff_above_horizon(self, gamma_azi: float) -> None:
        # pitch=0: cos_aoi = sin(elev) → azimuth-independent, true iff elev > 0.
        up = _louvered(sol_azi=gamma_azi, sol_elev=10, roof_pitch=0)
        down = _louvered(sol_azi=gamma_azi, sol_elev=-5, roof_pitch=0)
        assert up.valid_elevation is True
        assert down.valid_elevation is False

    def test_pitched_roof_false_when_cos_aoi_not_positive(self) -> None:
        # Steep pitch facing south (win_azi=180); sun low in the north
        # (azimuth 0) → sun strikes the BACK of the plane → cos_aoi <= 0.
        cover = _louvered(sol_azi=0, sol_elev=10, roof_pitch=80, win_azi=180)
        assert cover._cos_aoi() <= 0
        assert cover.valid_elevation is False

    def test_pitched_roof_true_when_sun_on_face(self) -> None:
        cover = _louvered(sol_azi=180, sol_elev=40, roof_pitch=40, win_azi=180)
        assert cover._cos_aoi() > 0
        assert cover.valid_elevation is True


# ---------------------------------------------------------------------------
# Full calculate_position — flat roof vs an independent hand-computed cut-off
# ---------------------------------------------------------------------------


class TestCalculatePositionFlatRoof:
    """A full solve on a flat roof matches a hand-computed 2-D cut-off angle."""

    def test_flat_roof_slat_angle(self) -> None:
        sol_elev = 30.0
        slat_distance, depth, mode = 0.02, 0.03, "mode2"
        cover = _louvered(
            sol_azi=180,
            sol_elev=sol_elev,
            roof_pitch=0,
            slat_distance=slat_distance,
            depth=depth,
            mode=mode,
        )
        # Hand path: flat-roof beta = 90 - elev = 60°, then the MDPI cut-off.
        beta = math.radians(90.0 - sol_elev)
        ratio = slat_distance / depth
        disc = math.tan(beta) ** 2 - ratio**2 + 1
        expected = math.degrees(
            2 * math.atan((math.tan(beta) + math.sqrt(disc)) / (1 + ratio))
        )
        result = cover.calculate_position()
        assert not np.isnan(result)
        assert result == pytest.approx(expected)
        # Sanity: mode2 caps at 180.
        assert 0 <= result <= 180

    def test_trace_surfaces_roof_keys(self) -> None:
        cover = _louvered(sol_azi=180, sol_elev=35, roof_pitch=25)
        cover.calculate_position()
        trace = cover._last_calc_details
        assert trace["roof_pitch_deg"] == pytest.approx(25.0)
        assert "cos_aoi" in trace
        assert "slope_ratio" in trace
        assert "beta_rad" in trace  # inherited from the tilt trace


# ---------------------------------------------------------------------------
# FOV gate — in-plane effective gamma below vertical, raw gamma at vertical
# ---------------------------------------------------------------------------


class TestFovAngle:
    """``fov_angle`` uses the raw gamma at pitch=90 and the in-plane azimuth below."""

    def test_pitch_90_uses_raw_gamma(self) -> None:
        cover = _louvered(sol_azi=200, sol_elev=40, roof_pitch=90)
        assert cover.fov_angle == pytest.approx(cover.gamma)

    def test_below_vertical_uses_effective_gamma(self) -> None:
        from custom_components.adaptive_cover_pro.engine.covers.roof_window import (
            roof_effective_gamma,
        )

        cover = _louvered(sol_azi=200, sol_elev=40, roof_pitch=30)
        expected = roof_effective_gamma(cover.gamma, cover.sol_elev, 30)
        assert cover.fov_angle == pytest.approx(expected)
        # Below vertical the in-plane azimuth widens away from the raw gamma.
        assert cover.fov_angle != pytest.approx(cover.gamma)
