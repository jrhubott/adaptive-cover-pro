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

from custom_components.adaptive_cover_pro.const import (
    SAFETY_MARGIN_USER_SLACK_MAX,
    TILT_HORIZONTAL_DEG,
)
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
from custom_components.adaptive_cover_pro.geometry import SafetyMarginCalculator

from tests.cover_helpers import (
    build_louvered_roof_cover,
    make_cover_config,
    make_tilt_config,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

# Shared with tests/test_pipeline/test_pipeline_helpers.py — the
# AdaptiveLouveredRoofCover construction lives once in tests/cover_helpers.py
# (#1105 audit) rather than being reimplemented per test file.
_louvered = build_louvered_roof_cover


def _tilt(
    *,
    sol_azi: float,
    sol_elev: float,
    slat_distance: float = 0.02,
    depth: float = 0.03,
    mode: str = "mode2",
    win_azi: float = 180.0,
    safety_margin: float = 0.0,
    min_reflected_elevation: float = 0.0,
) -> AdaptiveTiltCover:
    """Build a plain venetian/tilt engine (the pitch=90 reduction target)."""
    return AdaptiveTiltCover(
        logger=MagicMock(),
        sol_azi=sol_azi,
        sol_elev=sol_elev,
        sun_data=MagicMock(),
        config=make_cover_config(win_azi=win_azi, fov_left=90, fov_right=90),
        tilt_config=make_tilt_config(
            slat_distance=slat_distance,
            depth=depth,
            mode=mode,
            safety_margin=safety_margin,
            min_reflected_elevation=min_reflected_elevation,
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
        assert cover.cos_aoi <= 0
        assert cover.valid_elevation is False

    def test_pitched_roof_true_when_sun_on_face(self) -> None:
        cover = _louvered(sol_azi=180, sol_elev=40, roof_pitch=40, win_azi=180)
        assert cover.cos_aoi > 0
        assert cover.valid_elevation is True


# ---------------------------------------------------------------------------
# Full calculate_position — flat roof vs an independent hand-computed cut-off
# ---------------------------------------------------------------------------


class TestCalculatePositionFlatRoof:
    """A full solve on a flat roof matches a hand-computed 2-D cut-off angle."""

    def test_flat_roof_slat_angle(self) -> None:
        # Max-opening objective: a low near-side sun whose raw MDPI cut-off runs
        # PAST vertical (≥ 90°) means the slats already self-block, so the roof
        # drives to fully OPEN (90°) — not the steep 135.5° interior-venetian
        # cut-off the old objective produced.
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
        # Hand path: flat-roof beta = 90 - elev = 60°; the MDPI cut-off is > 90.
        # The solve uses the interlock effective depth 2d − s (#830).
        beta = math.radians(90.0 - sol_elev)
        ratio = slat_distance / (2 * depth - slat_distance)
        disc = math.tan(beta) ** 2 - ratio**2 + 1
        cutoff = math.degrees(
            2 * math.atan((math.tan(beta) + math.sqrt(disc)) / (1 + ratio))
        )
        assert cutoff > 90.0  # precondition: raw cut-off past vertical
        result = cover.calculate_position()
        assert not np.isnan(result)
        assert result == pytest.approx(90.0)  # max-opening clamp, not the cut-off
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
        # Effective blocking depth 2d − s (#830): d=0.03, s=0.02 → 0.04.
        assert trace["louvered_blocking_depth"] == pytest.approx(0.04)


# ---------------------------------------------------------------------------
# FOV gate — in-plane effective gamma below vertical, raw gamma at vertical
# ---------------------------------------------------------------------------


class TestFarSideFlip:
    """Far-side sun realizes the flipped face (180° − θ) past the 90° turnover.

    On a flat roof the AOI gate (sin(elev)) is azimuth-independent, so far-side
    (evening) sun is tracked. The raw cut-off magnitude is identical near/far —
    the flip picks the correct physical face. Criterion: ``cos(gamma) < 0``.
    """

    def test_flat_roof_near_and_far_mirror(self) -> None:
        # elev=50 (raised from 40): at 40 the interlock effective depth pushes both
        # folds to the fully-open 90° clamp, so the mirror pins lose their purpose.
        elev = 50.0
        # win_azi=180: gamma = (180 - sol_azi + 180) % 360 - 180.
        # sol_azi=120 → gamma=60 (near, cos>0); sol_azi=60 → gamma=120 (far).
        near = _louvered(sol_azi=120, sol_elev=elev, roof_pitch=0, mode="mode2")
        far = _louvered(sol_azi=60, sol_elev=elev, roof_pitch=0, mode="mode2")
        assert near.gamma == pytest.approx(60.0)
        assert far.gamma == pytest.approx(120.0)

        near_angle = near.calculate_position()
        far_angle = far.calculate_position()

        # The two physical slat angles still mirror across the 90° turnover.
        assert near_angle + far_angle == pytest.approx(180.0)

        # Max-opening objective: the raw cut-off is < 90 here, so the facing side
        # opens PAST vertical to 180 − cut-off while the far side rests at the
        # cut-off itself — the near/far roles invert vs the old steep objective.
        # The solve uses the interlock effective depth 2d − s (#830).
        beta = math.atan(abs(roof_slope_ratio(60.0, elev, 0)))
        raw, _, _ = slat_cutoff_angle(
            beta, near.slat_distance, 2 * near.depth - near.slat_distance
        )
        assert raw < 90.0
        assert near_angle == pytest.approx(180.0 - raw)
        assert far_angle == pytest.approx(raw)
        # Pin the concrete numbers so a formula drift is caught (effective depth).
        assert near_angle == pytest.approx(94.7, abs=0.1)
        assert far_angle == pytest.approx(85.3, abs=0.1)

    def test_no_flip_at_pitch_90_near_side(self) -> None:
        # At vertical pitch the lit sun is always near side (cos_aoi needs
        # cos(gamma) > 0), so the flip never fires and the vertical/venetian
        # anchor is byte-for-byte preserved.
        louvered = _louvered(sol_azi=205, sol_elev=35, roof_pitch=90, mode="mode2")
        tilt = _tilt(sol_azi=205, sol_elev=35, mode="mode2")
        assert louvered.calculate_position() == pytest.approx(tilt.calculate_position())
        assert louvered._last_calc_details["louvered_far_side_branch"] is False

    def test_mode1_near_side_clamps_to_open(self) -> None:
        # Mode1 caps at 90°. Under the max-opening objective the NEAR (facing)
        # side opens PAST vertical (> 90) whenever the raw cut-off is < 90, so it
        # clamps to fully open (90). (The far side now rests at/below 90 instead,
        # inverting the old behaviour where the far side was the one clamping.)
        # elev=50 (raised from 40) so the near angle (94.7°) genuinely exceeds 90
        # and the mode1 clamp is exercised — under the effective depth the near
        # angle at elev=40 lands exactly on 90 and never crosses the ceiling.
        near = _louvered(sol_azi=120, sol_elev=50, roof_pitch=0, mode="mode1")
        assert near.gamma == pytest.approx(60.0)
        assert near.calculate_position() == pytest.approx(90.0)

    def test_trace_far_side_branch_flag(self) -> None:
        near = _louvered(sol_azi=120, sol_elev=40, roof_pitch=0)
        far = _louvered(sol_azi=60, sol_elev=40, roof_pitch=0)
        near.calculate_position()
        far.calculate_position()
        assert near._last_calc_details["louvered_far_side_branch"] is False
        assert far._last_calc_details["louvered_far_side_branch"] is True


class TestMaxSlatAngle:
    """``max_slat_angle`` overrides the mode's 90/180 as clamp + %-denominator."""

    def test_ceiling_above_raw_scales_percentage(self) -> None:
        # gamma=0 (near side, no flip), elev=65 → raw cut-off 88.1° < 90 (interlock
        # effective depth 2d − s), so the max-opening angle is 180 − 88.1 = 91.9°
        # (past vertical). This exercises the max_slat_angle scaling on a near-side
        # angle above the horizontal.
        base = _louvered(sol_azi=180, sol_elev=65, roof_pitch=0, mode="mode2")
        raw = base.calculate_position()
        assert raw == pytest.approx(91.9, abs=0.2)

        denom = int(raw) + 10  # ceiling above the raw angle: position unchanged
        wide = _louvered(
            sol_azi=180, sol_elev=65, roof_pitch=0, mode="mode2", max_slat_angle=denom
        )
        assert wide.calculate_position() == pytest.approx(raw)
        assert wide.calculate_percentage() == round(raw / denom * 100)

    def test_ceiling_below_raw_clamps_and_saturates(self) -> None:
        base = _louvered(sol_azi=180, sol_elev=65, roof_pitch=0, mode="mode2")
        raw = base.calculate_position()

        ceil = int(raw) - 20  # ceiling below the raw angle: clamp + 100%
        low = _louvered(
            sol_azi=180, sol_elev=65, roof_pitch=0, mode="mode2", max_slat_angle=ceil
        )
        assert low.calculate_position() == pytest.approx(float(ceil))
        assert low.calculate_percentage() == 100

    def test_explicit_denominator_example(self) -> None:
        # A raw angle of 120° with a 160° ceiling maps to 75% (plan example).
        assert round(120.0 / 160.0 * 100) == 75

    def test_default_zero_matches_mode(self) -> None:
        m = _louvered(
            sol_azi=180, sol_elev=65, roof_pitch=0, mode="mode2", max_slat_angle=0
        )
        ref = _louvered(sol_azi=180, sol_elev=65, roof_pitch=0, mode="mode2")
        assert m.calculate_position() == pytest.approx(ref.calculate_position())
        assert m.calculate_percentage() == ref.calculate_percentage()

    def test_trace_max_degrees_reflects_effective_ceiling(self) -> None:
        cover = _louvered(
            sol_azi=180, sol_elev=65, roof_pitch=0, mode="mode2", max_slat_angle=160
        )
        cover.calculate_position()
        assert cover._last_calc_details["max_degrees"] == 160

    def test_fractional_sub_one_does_not_raise_zero_division(self) -> None:
        # A fractional max_slat_angle in the open interval (0, 1) is truthy, so
        # it must NOT be routed to the ``0`` sentinel branch — but it also must
        # not truncate to a literal ``0`` denominator (issue #1105).
        cover = _louvered(
            sol_azi=180, sol_elev=65, roof_pitch=0, mode="mode2", max_slat_angle=0.4
        )
        result = cover.calculate_position()
        assert isinstance(result, float)
        # Pin the effective ceiling itself, not just "didn't raise" — every
        # non-raising return path of calculate_position() is a float, so the
        # isinstance check alone proves nothing about the denominator actually
        # landing on 0.4 rather than being silently truncated to 0 elsewhere.
        assert cover._last_calc_details["max_degrees"] == pytest.approx(0.4)

    def test_fractional_denominator_is_not_truncated(self) -> None:
        cover = _louvered(
            sol_azi=180, sol_elev=65, roof_pitch=0, mode="mode2", max_slat_angle=137.5
        )
        assert cover._effective_max_degrees() == pytest.approx(137.5)

    def test_int_max_slat_angle_returns_float(self) -> None:
        # ``_effective_max_degrees`` is typed ``-> float``; a plain int
        # ``max_slat_angle`` (as several tests in this class pass, e.g. 160
        # above) must actually come back as a float, not just an int that
        # happens to compare equal to one (issue #1105 audit).
        cover = _louvered(
            sol_azi=180, sol_elev=65, roof_pitch=0, mode="mode2", max_slat_angle=160
        )
        result = cover._effective_max_degrees()
        assert result == 160
        assert isinstance(result, float)


class TestMaxOpeningObjective:
    """The louvered slat objective is MAX OPENING (closest to vertical/90°).

    Unlike the interior venetian, the bioclimatic roof wants the slats as OPEN
    as possible while still blocking the direct beam. The physical inclination
    from vertical is ``i = max(0, 90 − cutoff)`` and the slat angle is ``90 + i``
    on the facing side / ``90 − i`` on the far side — i.e. ``max(90, 180 − cutoff)``
    facing and ``min(90, cutoff)`` far. Once the slats self-block (``cutoff ≥ 90``)
    the angle clamps to fully open (90°), NOT the steep venetian cut-off.
    """

    def test_full_open_when_cutoff_ge_90(self) -> None:
        # Flat roof, low near-side sun (elev=30) → raw cut-off 135.5° ≥ 90 →
        # inclination 0 → fully OPEN (90°), not the steep 135.5° the interior
        # venetian objective would drive to. Uses the interlock effective depth
        # 2d − s (#830).
        cover = _louvered(sol_azi=180, sol_elev=30, roof_pitch=0, mode="mode2")
        beta = math.radians(90.0 - 30.0)  # flat-roof complement, gamma=0
        raw, _, _ = slat_cutoff_angle(
            beta, cover.slat_distance, 2 * cover.depth - cover.slat_distance
        )
        assert raw > 90.0  # precondition: old objective drove past vertical
        assert cover.calculate_position() == pytest.approx(90.0)

    def test_near_side_tracks_toward_vertical(self) -> None:
        # Flat roof, high near-side sun (elev=65) → raw cut-off 88.1° < 90 →
        # max-opening angle is 180 − cut-off = 91.9° (just past vertical), NOT
        # the 88.1° venetian cut-off. Uses the interlock effective depth 2d − s.
        cover = _louvered(sol_azi=180, sol_elev=65, roof_pitch=0, mode="mode2")
        beta = math.radians(90.0 - 65.0)
        raw, _, _ = slat_cutoff_angle(
            beta, cover.slat_distance, 2 * cover.depth - cover.slat_distance
        )
        assert raw < 90.0
        result = cover.calculate_position()
        assert result == pytest.approx(180.0 - raw)
        assert result > 90.0

    def test_far_side_full_open_low_sun(self) -> None:
        # Far-side (gamma=120) low sun (elev=20) → raw cut-off ≥ 90 → inclination
        # 0 → fully OPEN (90°) == min(90, cut-off). The OLD far-side mirror drove
        # this to a near-closed 59.2° instead.
        cover = _louvered(sol_azi=60, sol_elev=20, roof_pitch=0, mode="mode2")
        assert cover._is_far_side() is True
        assert cover.calculate_position() == pytest.approx(90.0)


class TestMeasuredOptimaIssue830:
    """Data-anchored regression: the resolved slat angle tracks a beta tester's
    hand-measured max-opening optima on a real bioclimatic pergola (#830).

    The nominal tip-to-tip venetian ratio ``s/d`` over-closes by ~8-9° across the
    working range because watertight interlocking lamellae shade over a larger
    EFFECTIVE blocking depth ``d_eff = 2d - s`` (each joint overlaps by ``d - s``),
    giving ``r_eff = s/(2d - s)``. For s=0.150, d=0.170 → d_eff=0.190, r_eff=0.789.
    These pins would fail against the old nominal-ratio objective (mid-range points
    ~+5.4…+9.5° too closed).
    """

    # (sol_elev, expected_theta, tol) — beta tester's 6-point table, γ=0 so
    # β_facade == elev. The 61.6° row is a single noisy measurement whose
    # back-solved r is inconsistent with its neighbours (residual ~2.58°).
    _MEASURED = [
        (51.6, 90.0, 2.0),
        (61.6, 103.0, 3.0),
        (66.6, 113.0, 2.0),
        (71.2, 120.0, 2.0),
        (77.05, 127.0, 2.0),
        (81.7, 133.0, 2.0),
    ]

    @pytest.mark.parametrize(("sol_elev", "expected_theta", "tol"), _MEASURED)
    def test_near_side_matches_measured_optimum(
        self, sol_elev: float, expected_theta: float, tol: float
    ) -> None:
        # sol_azi=180, win_azi=180 → γ=0 → β_facade = elev (near side).
        cover = _louvered(
            sol_azi=180,
            sol_elev=sol_elev,
            roof_pitch=0,
            slat_distance=0.150,
            depth=0.170,
            mode="mode2",
            win_azi=180,
        )
        assert cover.calculate_position() == pytest.approx(expected_theta, abs=tol)

    @pytest.mark.parametrize(("sol_elev", "expected_theta", "tol"), _MEASURED)
    def test_far_side_mirrors_measured_optimum(
        self, sol_elev: float, expected_theta: float, tol: float
    ) -> None:
        # sol_azi=0, win_azi=180 → γ=180 (far side); |cos γ|=1 keeps β_facade=elev.
        # The far face mirrors across the 90° turnover: θ_far = 180 − θ_near.
        cover = _louvered(
            sol_azi=0,
            sol_elev=sol_elev,
            roof_pitch=0,
            slat_distance=0.150,
            depth=0.170,
            mode="mode2",
            win_azi=180,
        )
        assert cover._is_far_side() is True
        assert cover.calculate_position() == pytest.approx(
            180.0 - expected_theta, abs=tol
        )

    def test_issue_830_live_datapoint(self) -> None:
        # mellomanfl's live decision_trace: γ=−63.83 (near side). The pre-fix
        # engine returned 136.4° (trace target_angle_deg); the measured optimum
        # is 127°.
        cover = _louvered(
            sol_azi=206.83,
            sol_elev=62.47,
            roof_pitch=0,
            slat_distance=0.150,
            depth=0.170,
            mode="mode2",
            win_azi=143,
            max_slat_angle=158,
        )
        assert cover.gamma == pytest.approx(-63.83, abs=0.01)
        assert cover.calculate_position() == pytest.approx(127.0, abs=2.0)

    def test_blocking_depth_hook(self) -> None:
        # Flat roof, interlocking geometry (s < d): d_eff = d + (d − s).
        interlock = _louvered(
            sol_azi=180,
            sol_elev=60,
            roof_pitch=0,
            slat_distance=0.150,
            depth=0.170,
        )
        assert interlock._blocking_depth() == pytest.approx(0.170 + (0.170 - 0.150))

        # Vertical pitch → nominal chord (venetian anchor untouched).
        vertical = _louvered(
            sol_azi=180,
            sol_elev=60,
            roof_pitch=90,
            slat_distance=0.150,
            depth=0.170,
        )
        assert vertical._blocking_depth() == pytest.approx(0.170)

        # Non-interlocking geometry (s ≥ d) degrades to the nominal chord.
        degrade = _louvered(
            sol_azi=180,
            sol_elev=60,
            roof_pitch=0,
            slat_distance=0.03,
            depth=0.02,
        )
        assert degrade._blocking_depth() == pytest.approx(0.02)


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


# ---------------------------------------------------------------------------
# Tilt safety margin — inherited from AdaptiveTiltCover (#783/#964), reshaped
# by #1089
# ---------------------------------------------------------------------------


def _margin_applied(
    cover: AdaptiveLouveredRoofCover, resolved: float, s: float
) -> float:
    """Re-derive the margin transform outside the engine.

    Mirrors ``engine/covers/tilt.py``: the user's strength ``s`` scales the
    geometry margin's own excess PLUS the flat ``SAFETY_MARGIN_USER_SLACK_MAX``
    budget, and the closure is measured away from horizontal — which for a
    louvered roof is the fully-OPEN (vertical-slat) angle, so the transform
    pushes the slats shut in whichever direction they were already inclined.
    """
    geo = SafetyMarginCalculator.calculate(cover.gamma, cover.sol_elev)
    eff = 1.0 + s * ((geo - 1.0) + SAFETY_MARGIN_USER_SLACK_MAX)
    return TILT_HORIZONTAL_DEG - (TILT_HORIZONTAL_DEG - resolved) * eff


def _flat_roof_cutoff(cover: AdaptiveLouveredRoofCover, sol_elev: float) -> float:
    """Hand path to the raw cut-off on a flat roof with aligned sun (gamma=0)."""
    beta = math.radians(90.0 - sol_elev)  # flat-roof complement
    cutoff, _, _ = slat_cutoff_angle(
        beta, cover.slat_distance, 2 * cover.depth - cover.slat_distance
    )
    return float(cutoff)


class TestSafetyMarginOnLouveredRoof:
    """The roof inherits the tilt safety-margin slider, so #1089 moves it too.

    ``AdaptiveLouveredRoofCover`` does not override ``calculate_position`` — it
    delegates to ``AdaptiveTiltCover`` and only decorates the trace — and its
    geometry schema copies the tilt schema, carrying ``CONF_TILT_SAFETY_MARGIN``.
    The reformulated ``eff_margin`` therefore changes roof behaviour, and these
    are its regression guards.
    """

    # Flat roof, high near-side sun: the raw cut-off is UNDER 90°, so the roof
    # inclines the slats past vertical and the margin has somewhere to close to.
    # Low sun does the opposite — see the self-blocking known-limit test below.
    _MOVING = {"sol_azi": 180.0, "sol_elev": 75.0, "roof_pitch": 0.0}

    def test_zero_strength_is_byte_identical(self) -> None:
        """The default 0.0 leaves the max-opening angle untouched, bit for bit."""
        cover = _louvered(**self._MOVING, mode="mode2", safety_margin=0.0)
        result = cover.calculate_position()
        # The transform never ran: the output IS the resolved slat angle.
        assert result == cover._last_calc_details["slat_angle_raw_deg"]
        assert cover._last_calc_details["safety_margin"] == 1.0
        # Independent hand path — max opening is 180 − cut-off on the near side.
        cutoff = _flat_roof_cutoff(cover, self._MOVING["sol_elev"])
        assert cutoff < TILT_HORIZONTAL_DEG
        assert result == pytest.approx(180.0 - cutoff)

    def test_strength_closes_the_slats_further_near_side(self) -> None:
        """#1089: the slider bites on a roof whose slats are off the open rail."""
        c0 = _louvered(**self._MOVING, mode="mode2", safety_margin=0.0)
        a0 = c0.calculate_position()
        assert (
            SafetyMarginCalculator.calculate(c0.gamma, c0.sol_elev) == 1.0
        ), "test setup: inside the envelope, so only the flat user slack applies"
        assert a0 > TILT_HORIZONTAL_DEG, "test setup: inclined past vertical"

        a1 = _louvered(
            **self._MOVING, mode="mode2", safety_margin=1.0
        ).calculate_position()
        assert a1 == pytest.approx(_margin_applied(c0, a0, 1.0))
        assert a1 - a0 > 1.0, "the slider must move the slat, not float-noise it"
        assert TILT_HORIZONTAL_DEG < a1 <= 180.0

    def test_strength_closes_the_slats_further_far_side(self) -> None:
        """The far-side face closes toward 0°, the mirror of the near side."""
        far = {"sol_azi": 0.0, "sol_elev": 70.0, "roof_pitch": 0.0}
        c0 = _louvered(**far, mode="mode2", safety_margin=0.0)
        a0 = c0.calculate_position()
        assert c0._is_far_side() is True
        assert a0 < TILT_HORIZONTAL_DEG, "test setup: inclined short of vertical"

        a1 = _louvered(**far, mode="mode2", safety_margin=1.0).calculate_position()
        assert a1 == pytest.approx(_margin_applied(c0, a0, 1.0))
        assert a1 < a0
        assert 0.0 <= a1 < TILT_HORIZONTAL_DEG

    def test_strength_is_monotonic(self) -> None:
        """Raising the slider always closes further — no plateau in between."""
        angles = [
            _louvered(
                **self._MOVING, mode="mode2", safety_margin=s
            ).calculate_position()
            for s in (0.0, 0.25, 0.5, 0.75, 1.0)
        ]
        assert all(b > a for a, b in zip(angles, angles[1:], strict=False))

    def test_trace_reports_the_effective_margin(self) -> None:
        """Diagnostics parity (#682): the roof trace carries eff_margin and its own keys."""
        cover = _louvered(**self._MOVING, mode="mode2", safety_margin=1.0)
        cover.calculate_position()
        trace = cover._last_calc_details
        geo = SafetyMarginCalculator.calculate(cover.gamma, cover.sol_elev)
        assert trace["safety_margin"] == pytest.approx(
            geo + SAFETY_MARGIN_USER_SLACK_MAX
        )
        # The roof decoration still lands on top of the marginated tilt trace.
        assert trace["roof_pitch_deg"] == pytest.approx(0.0)
        assert trace["louvered_far_side_branch"] is False

    @pytest.mark.parametrize("sol_elev", [20.0, 30.0, 45.0])
    def test_known_limit_self_blocking_roof_is_a_fixed_point(
        self, sol_elev: float
    ) -> None:
        """KNOWN LIMIT (#1089): a roof already fully open cannot be closed further.

        Once the raw cut-off reaches 90° the slats self-block, so
        ``_resolve_slat_angle`` returns exactly ``TILT_HORIZONTAL_DEG`` — a fixed
        point of the closing transform (``90 − (90 − 90)·m == 90``). With shipped
        slat proportions that covers most of the day on a flat roof, which is why
        the user-facing copy scopes the claim rather than promising every sun
        angle. This fails loudly if ``_resolve_slat_angle`` or the clamp changes.
        """
        params = {"sol_azi": 180.0, "sol_elev": sol_elev, "roof_pitch": 0.0}
        c0 = _louvered(**params, mode="mode2", safety_margin=0.0)
        a0 = c0.calculate_position()
        assert _flat_roof_cutoff(c0, sol_elev) >= TILT_HORIZONTAL_DEG
        assert a0 == float(TILT_HORIZONTAL_DEG)

        a1 = _louvered(**params, mode="mode2", safety_margin=1.0).calculate_position()
        assert a1 == a0

    def test_known_limit_mode1_roof_clamps_the_closure_away(self) -> None:
        """KNOWN LIMIT (#1089): mode1's 90° ceiling IS the roof's fully-open angle.

        A near-side roof inclines PAST vertical, so a single-direction drive
        clamps to 90° whether or not the margin ran. The slider is invisible on
        that FACE — the geometry that would show it lives above the ceiling.
        Not on mode1 roofs generally: the far-side face resolves to ``90° − i``,
        which sits below the ceiling and does respond. Sweeping a flat mode1 roof
        over the whole sky, the margin moves 0 of the near-side sun positions and
        roughly half of the far-side ones.
        """
        c0 = _louvered(**self._MOVING, mode="mode1", safety_margin=0.0)
        a0 = c0.calculate_position()
        assert c0._last_calc_details["slat_angle_raw_deg"] > TILT_HORIZONTAL_DEG
        assert a0 == float(TILT_HORIZONTAL_DEG)

        a1 = _louvered(
            **self._MOVING, mode="mode1", safety_margin=1.0
        ).calculate_position()
        assert a1 == a0

    def test_pitch_90_still_matches_the_tilt_engine_with_the_margin_on(self) -> None:
        """The vertical reduction stays byte-for-byte once the slider is raised."""
        louvered = _louvered(
            sol_azi=205, sol_elev=35, roof_pitch=90, mode="mode2", safety_margin=1.0
        )
        tilt = _tilt(sol_azi=205, sol_elev=35, mode="mode2", safety_margin=1.0)
        assert louvered.calculate_position() == tilt.calculate_position()


class TestReflectedBeamFloorOnLouveredRoof:
    """The reflected-beam floor is inert on a real roof plane (#1282 x #830).

    ``AdaptiveLouveredRoofCover`` inherits ``calculate_position`` wholesale, so a
    constraint added to the base engine reaches it whether or not the roof's
    geometry step offers the option — the ``set_geometry`` service can write the
    key onto any entry. But two of #830's changes break the mapping the
    constraint is derived from: ``_resolve_slat_angle`` realizes the pose as
    ``90 +/- i`` rather than the raw cut-off, so ``phi = 90 - code_angle`` is
    false, and ``beta`` is measured against the ROOF plane rather than the
    facade. Applying the vertical-facade clamp there would compute a confident
    number about nothing. It is closed with a polymorphic hook rather than a
    cover-type string check, which the repo bans outside ``cover_types/``.

    Each case below is chosen so the floor WOULD move the angle if the base hook
    were inherited — a geometry where it happens to be inert proves nothing.
    """

    @pytest.mark.parametrize(("sol_azi", "sol_elev"), [(180.0, 70.0), (20.0, 70.0)])
    def test_flat_roof_ignores_the_floor(self, sol_azi: float, sol_elev: float) -> None:
        """Near side (``90 + i``) and far side (``90 - i``) alike."""
        params = {
            "sol_azi": sol_azi,
            "sol_elev": sol_elev,
            "roof_pitch": 0.0,
            "mode": "mode2",
        }
        floored = _louvered(**params, min_reflected_elevation=60)
        assert floored.calculate_position() == _louvered(**params).calculate_position()
        trace = floored._last_calc_details
        assert trace["reflected_beam_min_elevation_deg"] == 0.0
        assert trace["reflected_beam_constrained"] is False

    @pytest.mark.parametrize(("sol_azi", "sol_elev"), [(180.0, 50.0), (20.0, 70.0)])
    def test_pitched_roof_ignores_the_floor(
        self, sol_azi: float, sol_elev: float
    ) -> None:
        """A pitched plane is no more the facade plane than a flat one is."""
        params = {
            "sol_azi": sol_azi,
            "sol_elev": sol_elev,
            "roof_pitch": 30.0,
            "mode": "mode2",
        }
        floored = _louvered(**params, min_reflected_elevation=60)
        assert floored.calculate_position() == _louvered(**params).calculate_position()
        trace = floored._last_calc_details
        assert trace["reflected_beam_min_elevation_deg"] == 0.0
        assert trace["reflected_beam_constrained"] is False

    def test_pitch_90_still_matches_the_tilt_engine_with_the_floor_on(self) -> None:
        """The vertical reduction keeps the floor — it IS the venetian case."""
        sun = {"sol_azi": 205, "sol_elev": 35}
        louvered = _louvered(**sun, roof_pitch=90, mode="mode2")
        floored = _louvered(
            **sun, roof_pitch=90, mode="mode2", min_reflected_elevation=30
        )
        # The floor must actually bite here, or the equality below is vacuous.
        assert floored.calculate_position() < louvered.calculate_position()
        assert floored.calculate_position() == pytest.approx(
            _tilt(**sun, mode="mode2", min_reflected_elevation=30).calculate_position()
        )
