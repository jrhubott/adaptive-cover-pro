"""Tests for directional (conservative) position rounding (issues #978, #1090).

Conservative rounding biases the solar position toward full coverage instead of
the nearest integer. Always-on, no opt-in flag, and decided in two layers:

1. **The policy states the axis-level direction.** ``CoverAxis.open_blocks_sun``
   says which END of the 0–100 % travel blocks the sun, and
   ``solar_position_from_geometry`` turns that into ``full_coverage_at_zero``:
   True for a blind / tilt / venetian axis (0 % is the covering end), False for
   an awning (100 % is). That answer is complete only where coverage is
   MONOTONIC in the percentage.
2. **The engine refines it where coverage is not monotonic.** The percentage is
   handed to ``AdaptiveGeneralCover.round_toward_coverage``, whose base
   implementation is exactly the monotonic rule — ``floor()`` when
   ``full_coverage_at_zero``, ``ceil()`` otherwise. ``AdaptiveTiltCover``
   overrides it, because a bi-directional slat is not monotonic: on MODE2 (the
   shipped tilt/venetian default) 0° is closed downward, 90° is horizontal and
   180° is closed upward, so openness PEAKS mid-travel. That axis rounds away
   from the percentage representing horizontal — up above the pivot, down below
   it — which subsumes the monotonic rule rather than special-casing it, since
   MODE1's pivot is 100 % and everything below it still floors.

That pivot is computed, not tabulated: it is ``TILT_HORIZONTAL_DEG`` pushed
through the engine's own angle→percentage map. The louvered roof is what proves
the difference matters — a configured ``max_slat_angle`` puts the pivot at 75 %
(120°) or a fractional 64.2857 % (140°), so a hardcoded 50 %/100 % would round
hundreds of real solves the wrong way. See
``TestLouveredRoofPivotFollowsMaxSlatAngle``.

So the direction is never a cover-type string test, and never a blanket
``floor()`` for "blind / tilt / venetian". The pipeline layer supplies the axis
semantic; only the engine knows the tilt scale, so only the engine can say which
arithmetic direction is conservative on it.

``AdaptiveTiltCover`` additionally re-bands the quantised integer, because the
tilt-only path applies ``[min_tilt, max_tilt]`` to the float BEFORE this rounding
runs — see ``TestTiltOnlyBandSurvivesTheQuantisation``.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.config_types import LouveredRoofConfig
from custom_components.adaptive_cover_pro.const import (
    TILT_HORIZONTAL_DEG,
    VENETIAN_TILT_TRANSFORM_CLAMP,
    VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
    TiltMode,
)
from custom_components.adaptive_cover_pro.engine.covers import AdaptiveTiltCover
from custom_components.adaptive_cover_pro.engine.covers.louvered_roof import (
    AdaptiveLouveredRoofCover,
)
from custom_components.adaptive_cover_pro.pipeline.helpers import (
    compute_solar_position,
    solar_position_from_geometry,
)
from tests.cover_helpers import (
    attach_coverage_rounding,
    build_tilt_cover,
    make_cover_config,
    make_tilt_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config():
    return SimpleNamespace(
        min_pos=None,
        max_pos=None,
        min_pos_sun_only=False,
        max_pos_sun_only=False,
        min_pos_sun_tracking=None,
    )


def _policy(*, open_blocks_sun: bool):
    return SimpleNamespace(axes=[SimpleNamespace(open_blocks_sun=open_blocks_sun)])


def _snapshot(
    *,
    calc_pct: float,
    open_blocks_sun: bool = False,
    floor_active: bool = False,
):
    """Build a minimal PipelineSnapshot-like namespace for solar branch tests.

    ``floor_active`` defaults to *False* so the 1%-floor doesn't mask rounding
    differences for values near zero.  Tests that specifically exercise the
    floor behaviour can opt in.
    """
    return SimpleNamespace(
        cover=attach_coverage_rounding(
            SimpleNamespace(
                direct_sun_valid=True,
                calculate_percentage=lambda: int(round(calc_pct)),
                calculate_raw_percentage=lambda: calc_pct,
            )
        ),
        config=_config(),
        policy=_policy(open_blocks_sun=open_blocks_sun),
        minimize_movements=False,
        max_coverage_steps=1,
        solar_floor_active=floor_active,
    )


# ---------------------------------------------------------------------------
# Blind direction (open_blocks_sun=False, full_coverage_at_zero=True)
# floor() toward 0 = more closed = more coverage
# ---------------------------------------------------------------------------


class TestBlindRounding:
    """Blinds always round DOWN (floor) toward closed."""

    @pytest.mark.parametrize(
        ("pct", "expected"),
        [
            (45.6, 45),  # round() would give 46; floor gives 45 (more closed)
            (45.4, 45),  # round() also gives 45; floor agrees
            (10.9, 10),  # round() would give 11; floor gives 10
            (99.9, 99),  # round() would give 100; floor gives 99 (still covered)
            (0.9, 0),  # floor → 0; solar floor clamp NOT active → stays 0
        ],
    )
    def test_floor_rounds_toward_closed(self, pct, expected):
        snap = _snapshot(calc_pct=pct, open_blocks_sun=False)
        assert compute_solar_position(snap) == expected

    @pytest.mark.parametrize("pct", [0.0, 10.0, 45.0, 67.0, 100.0])
    def test_integer_values_unchanged(self, pct):
        """floor(n.0) == round(n.0) — no extra movement on clean integers."""
        snap = _snapshot(calc_pct=pct, open_blocks_sun=False)
        assert compute_solar_position(snap) == int(pct)

    def test_floor_never_more_open_than_round(self):
        """floor(x) <= round(x) for blinds — directional rounding is never more open."""
        pct = 45.7  # round→46, floor→45
        snap = _snapshot(calc_pct=pct, open_blocks_sun=False)
        assert compute_solar_position(snap) <= int(round(pct))


# ---------------------------------------------------------------------------
# Awning direction (open_blocks_sun=True, full_coverage_at_zero=False)
# ceil() toward 100 = more extended = more coverage
# ---------------------------------------------------------------------------


class TestAwningRounding:
    """Awnings always round UP (ceil) toward extended."""

    @pytest.mark.parametrize(
        ("pct", "expected"),
        [
            (45.1, 46),  # round() would give 45; ceil gives 46 (more extended)
            (45.6, 46),  # round() also gives 46; ceil agrees
            (10.1, 11),  # round() would give 10; ceil gives 11
            (0.1, 1),  # round() would give 0; ceil gives 1
            (99.0, 99),  # already integer — no change
        ],
    )
    def test_ceil_rounds_toward_extended(self, pct, expected):
        snap = _snapshot(calc_pct=pct, open_blocks_sun=True)
        assert compute_solar_position(snap) == expected

    @pytest.mark.parametrize("pct", [0.0, 10.0, 45.0, 67.0, 100.0])
    def test_integer_values_unchanged(self, pct):
        """ceil(n.0) == round(n.0) — no extra movement on clean integers."""
        snap = _snapshot(calc_pct=pct, open_blocks_sun=True)
        assert compute_solar_position(snap) == int(pct)

    def test_ceil_never_less_extended_than_round(self):
        """ceil(x) >= round(x) for awnings — directional rounding is never less extended."""
        pct = 45.3  # round→45, ceil→46
        snap = _snapshot(calc_pct=pct, open_blocks_sun=True)
        assert compute_solar_position(snap) >= int(round(pct))


# ---------------------------------------------------------------------------
# solar_position_from_geometry primitive — direct unit tests
# ---------------------------------------------------------------------------


class TestSolarPositionFromGeometryPrimitive:
    """Test the lower-level primitive that compute_solar_position delegates to."""

    def _cover(self, pct: float):
        return attach_coverage_rounding(
            SimpleNamespace(
                calculate_percentage=lambda: int(round(pct)),
                calculate_raw_percentage=lambda: pct,
            )
        )

    def test_blind_floor(self):
        cover = self._cover(67.9)
        policy = _policy(open_blocks_sun=False)
        result = solar_position_from_geometry(
            cover,
            _config(),
            minimize_movements=False,
            max_coverage_steps=1,
            policy=policy,
            floor_active=False,
        )
        assert result == math.floor(67.9)

    def test_awning_ceil(self):
        cover = self._cover(67.1)
        policy = _policy(open_blocks_sun=True)
        result = solar_position_from_geometry(
            cover,
            _config(),
            minimize_movements=False,
            max_coverage_steps=1,
            policy=policy,
            floor_active=False,
        )
        assert result == math.ceil(67.1)

    def test_no_policy_falls_back_to_round(self):
        """When policy is None, falls back to round() without crashing."""
        cover = self._cover(67.7)
        result = solar_position_from_geometry(
            cover,
            _config(),
            minimize_movements=False,
            max_coverage_steps=1,
            policy=None,
            floor_active=False,
        )
        assert result == int(round(67.7))

    @pytest.mark.parametrize("pct", [10.0, 33.0, 67.0, 100.0])
    def test_integer_pct_same_as_round(self, pct):
        """floor/ceil of an integer == round of that integer."""
        cover = self._cover(pct)
        policy = _policy(open_blocks_sun=False)
        result = solar_position_from_geometry(
            cover,
            _config(),
            minimize_movements=False,
            max_coverage_steps=1,
            policy=policy,
            floor_active=False,
        )
        assert result == int(round(pct))


# ---------------------------------------------------------------------------
# Tilt: legacy/custom-max modes round internally via to_percentage(), so tilt
# overrides calculate_raw_percentage() to expose the true fraction (issue #978).
# Without the override, floor()/ceil() would see an already-rounded value and
# the direction signal would be a no-op.
# ---------------------------------------------------------------------------


class TestTiltRawPercentage:
    """AdaptiveTiltCover exposes an unrounded raw percentage in legacy modes."""

    def _mode1_tilt(self):
        return build_tilt_cover(
            logger=MagicMock(),
            sol_azi=180,
            sol_elev=45,
            sunset_pos=0,
            sunset_off=0,
            sunrise_off=0,
            sun_data=MagicMock(),
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
            slat_distance=0.03,
            depth=0.02,
            mode="mode1",
        )

    def test_override_exposes_unrounded_fraction(self):
        """Raw % keeps the sub-integer fraction that calculate_percentage() rounds away."""
        cover = self._mode1_tilt()
        assert not cover._is_specify_angles()
        # 41° in the mode1 0–90° range → 45.5556 %. calculate_percentage() rounds
        # to 46; calculate_raw_percentage() must keep the fraction so the solar
        # branch can floor toward coverage.
        cover.calculate_position = MagicMock(return_value=41.0)
        raw = cover.calculate_raw_percentage()
        assert raw == pytest.approx(41.0 / 90.0 * 100.0)
        assert cover.calculate_percentage() == 46.0
        assert math.floor(raw) == 45  # conservative floor differs from round()

    def test_override_is_defined_on_the_class(self):
        """The tilt class carries its own override, not the base delegation."""
        cover = self._mode1_tilt()
        assert "calculate_raw_percentage" in type(cover).__dict__


# ---------------------------------------------------------------------------
# Tilt direction: coverage is NOT monotonic in the tilt percentage (issue #1090)
# ---------------------------------------------------------------------------
# MODE2 — the shipped default for tilt-only and venetian covers — maps the slat
# angle 0–180° onto 0–100%, where 0° is closed downward, 90° is horizontal, and
# 180° is closed upward. Horizontal is MAXIMUM openness, so coverage increases
# as the angle moves away from 90° in EITHER direction, and the "round toward
# full coverage" rule of #978 cannot be a plain floor.
#
# The tilt engine returns the EXACT grazing angle — the most-open slat angle
# that still blocks the beam — so a quantisation that moves toward 90° always
# leaks a sliver of direct sun.
# ---------------------------------------------------------------------------


def _tilt_cover(*, sol_elev: float, **tilt_overrides) -> AdaptiveTiltCover:
    """Build a real ``AdaptiveTiltCover`` facing the sun at *sol_elev*.

    ``slat_distance``/``depth`` are the shipped venetian defaults (2 cm spacing,
    3 cm chord); with the sun on the window normal the solved slat angle sweeps
    from well below horizontal at low elevations to well above it by midday.
    """
    return AdaptiveTiltCover(
        logger=MagicMock(),
        sol_azi=180.0,
        sol_elev=sol_elev,
        sun_data=MagicMock(),
        config=make_cover_config(win_azi=180),
        tilt_config=make_tilt_config(
            slat_distance=0.02, depth=0.03, **{"mode": "mode2", **tilt_overrides}
        ),
    )


def _louvered_cover(
    *, sol_elev: float, sol_azi: float = 180.0, max_slat_angle: float = 0.0
) -> AdaptiveLouveredRoofCover:
    """Build a flat-roof ``AdaptiveLouveredRoofCover`` at the given sun position.

    ``roof_pitch=0`` is the shipped pergola default, and the pitch at which
    ``_resolve_slat_angle`` drives the slats toward maximum openness: near-side
    sun (``sol_azi=180``, on the window normal) is realized ABOVE horizontal as
    ``180° − θ``, far-side sun (``sol_azi=0``) BELOW it as ``θ``. That is what
    puts real solves on both sides of the pivot on one cover type.

    Same slat geometry as :func:`_tilt_cover` so the two are comparable; only
    ``max_slat_angle`` — the whole reason the pivot denominator is a
    polymorphic hook rather than a constant — varies.
    """
    return AdaptiveLouveredRoofCover(
        logger=MagicMock(),
        sol_azi=sol_azi,
        sol_elev=sol_elev,
        sun_data=MagicMock(),
        config=make_cover_config(win_azi=180, fov_left=90, fov_right=90),
        tilt_config=make_tilt_config(slat_distance=0.02, depth=0.03, mode="mode2"),
        roof_config=LouveredRoofConfig(roof_pitch=0.0, max_slat_angle=max_slat_angle),
    )


def _tilt_solar_position(cover) -> int:
    """Run *cover* through the tilt-only solar branch of the pipeline."""
    return solar_position_from_geometry(
        cover,
        _config(),
        minimize_movements=False,
        max_coverage_steps=1,
        # TILT_AXIS_PRIMARY (cover_tilt / cover_louvered_roof) declares
        # open_blocks_sun=False, so the axis-level rule is "0 % is full
        # coverage" — exactly the input that used to force a blanket floor().
        policy=_policy(open_blocks_sun=False),
        floor_active=False,
    )


def _tilt_solar_position_minimized(cover, n_steps: int) -> int:
    """Run the same branch as :func:`_tilt_solar_position`, minimisation on.

    The only difference is the opt-in coverage-step quantiser that runs
    immediately after the away-from-horizontal rounding (issue #1104).
    """
    return solar_position_from_geometry(
        cover,
        _config(),
        minimize_movements=True,
        max_coverage_steps=n_steps,
        policy=_policy(open_blocks_sun=False),
        floor_active=False,
    )


class TestTiltMode2DirectionalRounding:
    """MODE2 tilt quantises AWAY from horizontal, not always downward."""

    @pytest.mark.parametrize(
        ("sol_elev", "raw", "expected"),
        [
            # --- below horizontal: closing means a SMALLER angle → floor ---
            (10.0, 32.757550, 32),  # solves 58.96°
            (20.0, 39.561358, 39),  # solves 71.21°
            (30.0, 47.075339, 47),  # solves 84.74°
            # --- above horizontal: closing means a LARGER angle → ceil ---
            (35.0, 51.055578, 52),  # solves 91.90°
            (45.0, 59.374719, 60),  # solves 106.87°
            (60.0, 72.515989, 73),  # solves 130.53°
            (85.0, 95.371678, 96),  # solves 171.67°
        ],
    )
    def test_quantises_away_from_horizontal(self, sol_elev, raw, expected):
        cover = _tilt_cover(sol_elev=sol_elev)
        assert cover.calculate_raw_percentage() == pytest.approx(raw, abs=1e-5)
        assert _tilt_solar_position(cover) == expected

    def test_floor_at_the_boundary_would_command_exactly_horizontal(self):
        """The worst case: floor() lands the slats on 90.00° = maximum openness.

        At 34.4° elevation the engine solves 91.03° — just past horizontal. The
        raw percentage is 50.57 %, so flooring gives 50 %, which on the MODE2
        0–180° scale is *precisely* the horizontal slat: the single most
        sun-permissive angle the cover can hold, commanded in the name of
        conservative rounding.
        """
        cover = _tilt_cover(sol_elev=34.4)
        raw = cover.calculate_raw_percentage()
        assert raw == pytest.approx(50.570998, abs=1e-5)

        max_degrees = float(TiltMode.MODE2.max_degrees)
        assert math.floor(raw) / 100.0 * max_degrees == pytest.approx(
            TILT_HORIZONTAL_DEG
        )

        assert _tilt_solar_position(cover) == 51

    @pytest.mark.parametrize(
        "sol_elev", [5.0, 12.5, 21.0, 29.0, 34.4, 37.5, 44.0, 52.0, 68.0, 79.0, 88.0]
    )
    def test_commanded_angle_is_never_closer_to_horizontal_than_the_solve(
        self, sol_elev
    ):
        """Invariant behind the whole fix, swept across the tracking day.

        The solve is the most-open blocking angle, so the commanded integer
        percentage must sit at least as far from horizontal as the solve does.
        Any quantisation toward 90° violates this and lets direct sun through.
        """
        cover = _tilt_cover(sol_elev=sol_elev)
        exact_angle = cover.calculate_position()
        max_degrees = float(TiltMode.MODE2.max_degrees)
        commanded_angle = _tilt_solar_position(cover) / 100.0 * max_degrees

        assert abs(commanded_angle - TILT_HORIZONTAL_DEG) >= abs(
            exact_angle - TILT_HORIZONTAL_DEG
        )

    def test_degenerate_scale_falls_back_to_the_axis_rule(self):
        """A zero-width legacy scale has no pivot; the base floor/ceil stands.

        Unreachable from config — the tilt modes are 90°/180° and the louvered
        roof's ``max_slat_angle`` override is bounded away from zero — but
        ``_horizontal_percentage`` is the one place that division happens, so the
        guard is exercised head-on instead of left as an untested branch. It is
        the legacy/custom-max twin of the ``angle_0 == angle_100`` guard covered
        by ``TestTiltSpecifyAnglesDirectionalRounding``.
        """
        cover = _tilt_cover(sol_elev=45.0)
        cover._effective_max_degrees = MagicMock(return_value=0.0)

        assert cover._horizontal_percentage() is None
        # With no pivot the caller's axis-level answer is all there is, so the
        # two directions must now actually differ.
        assert cover.round_toward_coverage(45.6, full_coverage_at_zero=True) == 45
        assert cover.round_toward_coverage(45.6, full_coverage_at_zero=False) == 46

    def test_degenerate_scale_leaves_no_raw_percentage_either(self):
        """A scale with no pivot has no raw percentage either.

        The pivot and the solved position go through ONE map, so a scale that
        map cannot express is a scale neither of them can express.
        ``VenetianCoverCalculation._compute_tilt`` already catches
        ``ZeroDivisionError`` and falls back to the default position, which is
        what a zero denominator has always produced on this path.

        ``calculate_position`` is stubbed because it divides by the same
        denominator while building its diagnostics trace, and would otherwise
        raise first — making this pass for the wrong reason.
        """
        cover = _tilt_cover(sol_elev=45.0)
        cover._effective_max_degrees = MagicMock(return_value=0.0)
        cover.calculate_position = MagicMock(return_value=45.0)

        with pytest.raises(ZeroDivisionError):
            cover.calculate_raw_percentage()

    @pytest.mark.parametrize(
        ("solved_angle", "raw", "expected"),
        [
            # 45.5556 % — the #978 floor-vs-round anchor (round() would say 46).
            # It sits below BOTH the real MODE1 pivot and the 50 % one a MODE2
            # denominator would produce, so on its own it cannot see the
            # denominator at all.
            (41.0, 45.555556, 45),
            # 91.0889 % — ABOVE 50 %, which is the case that pins the
            # DENOMINATOR. It only floors while ``_horizontal_percentage``
            # divides ``TILT_HORIZONTAL_DEG`` by THIS engine's
            # ``_effective_max_degrees()`` (90 → a 100 % pivot). Hardcode the
            # MODE2 180° instead and the pivot drops to 50 %, putting this
            # percentage above it and turning the floor into a ceil (92) —
            # commanding a slat one point CLOSER to horizontal than the solve.
            (81.98, 91.088889, 91),
        ],
    )
    def test_mode1_still_floors_everywhere(self, solved_angle, raw, expected):
        """#978 regression guard: MODE1 spans 0–90°, so horizontal is 100 %.

        Every reachable MODE1 percentage is therefore at or below the pivot and
        the away-from-horizontal rule collapses to the plain floor MODE1 always
        had — the direction must fall out of the geometry, not a mode branch.
        """
        cover = _tilt_cover(sol_elev=45.0, mode="mode1")
        cover.calculate_position = MagicMock(return_value=solved_angle)

        assert cover.calculate_raw_percentage() == pytest.approx(raw, abs=1e-5)
        assert _tilt_solar_position(cover) == expected


class TestTiltMode2MinimizeMovements:
    """The coverage-step quantiser respects the horizontal pivot (issue #1104).

    ``minimize_movements`` snaps the commanded position onto one of N coverage
    levels, rounding toward MORE coverage. That quantiser used to divide the
    whole 0–100 range around a single coverage-zero end (0 % or 100 %), which is
    only the truth on a monotonic axis. On MODE2 the coverage-zero point is the
    horizontal slat mid-travel, so the old scale snapped every above-pivot solve
    back TOWARD horizontal — undoing, one step later, exactly what
    ``round_toward_coverage`` had just done (issue #1090).
    """

    def test_quantize_does_not_snap_back_toward_horizontal(self):
        """60 % is 10 points of coverage past the pivot, not 40 points short of it.

        At 45° elevation the solve is 106.87° → 59.3747 %, which the
        away-from-horizontal rule ceils to 60 %. With two levels per side the
        demand (10/50 = 0.2 of the upper side) rounds up to the half-step, i.e.
        50 % + 0.5 × 50 = 75 %. Measuring the same demand against a 100 %
        coverage-zero end instead gives 0.4 → the half-step at 50 %, which is the
        exactly-horizontal slat.
        """
        cover = _tilt_cover(sol_elev=45.0)
        assert _tilt_solar_position(cover) == 60
        assert _tilt_solar_position_minimized(cover, 2) == 75

    def test_quantize_never_crosses_the_pivot(self):
        """One level per side means "fully closed on THIS side", not on either.

        Crossing the pivot passes a pure distance-from-horizontal check — 0 % is
        90° from horizontal, more than the 16.87° the solve asks for — while
        still being wrong: it sweeps the slats through fully-open horizontal to
        get there, the wasted movement ``minimize_movements`` exists to avoid.
        """
        cover = _tilt_cover(sol_elev=45.0)
        assert _tilt_solar_position_minimized(cover, 1) == 100

    @pytest.mark.parametrize("n_steps", [2, 3])
    @pytest.mark.parametrize(
        "sol_elev", [5.0, 12.5, 21.0, 29.0, 34.4, 37.5, 44.0, 52.0, 68.0, 79.0, 88.0]
    )
    def test_commanded_angle_never_closer_to_horizontal_with_minimize(
        self, sol_elev, n_steps
    ):
        """The #1090 invariant, now with the quantiser in the path.

        Same statement as
        ``test_commanded_angle_is_never_closer_to_horizontal_than_the_solve``:
        the commanded slat must sit at least as far from horizontal as the
        grazing solve. Movement minimisation is allowed to command MORE closure
        than asked for; it is never allowed to command less.
        """
        cover = _tilt_cover(sol_elev=sol_elev)
        exact_angle = cover.calculate_position()
        max_degrees = float(TiltMode.MODE2.max_degrees)
        commanded_angle = (
            _tilt_solar_position_minimized(cover, n_steps) / 100.0 * max_degrees
        )

        assert abs(commanded_angle - TILT_HORIZONTAL_DEG) >= abs(
            exact_angle - TILT_HORIZONTAL_DEG
        )


class TestLouveredRoofPivotFollowsMaxSlatAngle:
    """A configured ``max_slat_angle`` moves the pivot off 50 %/100 % (#1090).

    ``AdaptiveLouveredRoofCover`` overrides ``_effective_max_degrees`` so a
    pergola drive whose mechanical travel is neither 90° nor 180° maps its OWN
    ceiling onto 100 %. That override is the entire reason
    ``_horizontal_percentage`` divides ``TILT_HORIZONTAL_DEG`` by a polymorphic
    hook instead of a literal: at ``max_slat_angle = 120`` horizontal is 75 %,
    so every percentage in ``(50, 75)`` has to round DOWN — the exact opposite
    of what a hardcoded MODE2 denominator would say. Neither the tilt-only nor
    the venetian suite can see that, because both of their pivots are whole
    numbers fixed by the mode.
    """

    @pytest.mark.parametrize(
        ("max_slat_angle", "pivot"),
        [
            (0.0, 50.0),  # sentinel → the tilt mode's max, i.e. the MODE2 pivot
            (90.0, 100.0),  # coincides with MODE1's pivot
            (120.0, 75.0),
            (140.0, 90.0 / 140.0 * 100.0),  # fractional — no whole-% pivot here
            (160.0, 56.25),
        ],
    )
    def test_pivot_is_the_horizontal_slat_on_the_configured_scale(
        self, max_slat_angle, pivot
    ):
        cover = _louvered_cover(sol_elev=45.0, max_slat_angle=max_slat_angle)
        assert cover._horizontal_percentage() == pytest.approx(pivot)

    @pytest.mark.parametrize(
        ("max_slat_angle", "sol_azi", "position", "raw", "expected"),
        [
            # --- below the pivot but ABOVE 50 % → floor ---------------------
            # Far-side sun realizes the cut-off BELOW horizontal, so the slat is
            # tilted down and closing means a smaller percentage. These are the
            # cases a 50 % pivot gets wrong: it would ceil them to 69 / 52,
            # landing one point CLOSER to horizontal than the exact solve.
            (120.0, 0.0, 81.975679, 68.313066, 68),
            (160.0, 0.0, 81.975679, 51.234800, 51),
            # --- above the pivot → ceil -------------------------------------
            # Near-side sun opens past horizontal (``180° − θ``), so closing now
            # means a larger percentage.
            (120.0, 180.0, 98.024321, 81.686934, 82),
        ],
    )
    def test_quantises_away_from_the_configured_pivot(
        self, max_slat_angle, sol_azi, position, raw, expected
    ):
        cover = _louvered_cover(
            sol_elev=70.0, sol_azi=sol_azi, max_slat_angle=max_slat_angle
        )
        assert cover.calculate_position() == pytest.approx(position, abs=1e-5)
        assert cover.calculate_raw_percentage() == pytest.approx(raw, abs=1e-5)
        assert _tilt_solar_position(cover) == expected

    def test_a_percentage_exactly_on_the_pivot_floors(self):
        """The tie is reachable in production, so the ``>`` boundary is pinned.

        ``_resolve_slat_angle`` returns exactly ``TILT_HORIZONTAL_DEG`` whenever
        the slats self-block (``cutoff >= 90°``) — on this geometry, every
        elevation up to ~62°. With ``max_slat_angle = 140`` the pivot is the
        fractional 64.2857 %, and the raw percentage is that same expression, so
        ``pct == horizontal_pct`` holds BIT-exactly rather than approximately.

        Neither direction is more conservative at the tie: 64 % is 89.6° and
        65 % is 91.0°, both farther from horizontal than the 90.0° solve. So the
        tie only has to be STABLE, not correct — it is pinned here so relaxing
        ``>`` to ``>=`` cannot pass unnoticed.
        """
        cover = _louvered_cover(sol_elev=45.0, max_slat_angle=140)
        solve = cover.calculate_position()

        assert solve == TILT_HORIZONTAL_DEG
        assert cover.calculate_raw_percentage() == cover._horizontal_percentage()

        assert _tilt_solar_position(cover) == 64
        for candidate in (64, 65):
            commanded = candidate / 100.0 * 140.0
            assert abs(commanded - TILT_HORIZONTAL_DEG) >= abs(
                solve - TILT_HORIZONTAL_DEG
            )


class TestTiltSpecifyAnglesDirectionalRounding:
    """``specify_angles`` calibration keeps the away-from-horizontal rule (#1090).

    The endpoint mapping is affine, so "away from horizontal in angle space" is
    "away from the percentage that represents horizontal" whichever way the
    calibration runs. An inverted calibration (``angle_0`` above ``angle_100``)
    therefore flips which arithmetic direction is conservative, and the pivot
    handles that without a sign test at the call site.
    """

    def test_forward_calibration(self):
        """0 % = 0°, 100 % = 180° — the pivot lands on 50 %, same as MODE2."""
        below = _tilt_cover(
            sol_elev=30.0, mode="specify_angles", angle_0=0.0, angle_100=180.0
        )
        assert below.calculate_raw_percentage() == pytest.approx(47.075339, abs=1e-5)
        assert _tilt_solar_position(below) == 47

        above = _tilt_cover(
            sol_elev=45.0, mode="specify_angles", angle_0=0.0, angle_100=180.0
        )
        assert above.calculate_raw_percentage() == pytest.approx(59.374719, abs=1e-5)
        assert _tilt_solar_position(above) == 60

    def test_inverted_calibration_reverses_the_arithmetic_direction(self):
        """0 % = 180°, 100 % = 0° — a higher percentage now means a LOWER angle.

        The pivot is still 50 %, but the conservative arithmetic swaps: the
        84.74° solve sits ABOVE the pivot here (52.92 %) and must round UP to
        stay below horizontal, while the 106.87° solve sits BELOW it (40.63 %)
        and must round DOWN to stay above horizontal.
        """
        below_horizontal = _tilt_cover(
            sol_elev=30.0, mode="specify_angles", angle_0=180.0, angle_100=0.0
        )
        assert below_horizontal.calculate_position() == pytest.approx(84.7356, abs=1e-4)
        assert below_horizontal.calculate_raw_percentage() == pytest.approx(
            52.924661, abs=1e-5
        )
        assert _tilt_solar_position(below_horizontal) == 53  # 84.60°, floor → 86.40°

        above_horizontal = _tilt_cover(
            sol_elev=45.0, mode="specify_angles", angle_0=180.0, angle_100=0.0
        )
        assert above_horizontal.calculate_position() == pytest.approx(
            106.8745, abs=1e-4
        )
        assert above_horizontal.calculate_raw_percentage() == pytest.approx(
            40.625281, abs=1e-5
        )
        assert _tilt_solar_position(above_horizontal) == 40  # 108.00°, ceil → 106.20°

    def test_degenerate_calibration_falls_back_to_the_axis_rule(self):
        """``angle_0 == angle_100`` has no pivot; the base floor/ceil still applies."""
        cover = _tilt_cover(
            sol_elev=45.0, mode="specify_angles", angle_0=90.0, angle_100=90.0
        )
        assert cover.calculate_raw_percentage() == 0.0
        assert _tilt_solar_position(cover) == 0


class TestOffTravelPivotIsAMonotonicAxis:
    """A scale that never reaches horizontal is monotonic after all (#1104).

    ``_horizontal_percentage`` is ``TILT_HORIZONTAL_DEG`` pushed through the
    engine's angle→percentage map, and NOTHING constrains that map's image to
    0–100. Two shipped configurations put the pivot off the travel:

    * a louvered roof whose ``max_slat_angle`` is below 90° — the field is a
      plain 0–180 number box, so 45° gives a 200 % pivot and 60° gives 150 %;
    * a ``specify_angles`` calibration with both endpoints on ONE side of
      horizontal — the config flow only enforces ``angle_0 < angle_100``.

    Such a slat never passes through maximum openness anywhere in its travel,
    so coverage IS monotonic in the percentage and the axis flag really is the
    whole story. The comparator can still use the off-travel pivot as an
    ordering reference (distance-from-a-point is affine-invariant, so it ranks
    correctly wherever the point sits — see ``test_protective.py``), but the
    coverage-step quantiser ANCHORS its levels on the pivot and spans them to
    the nearer end of the travel. Anchored on an unreachable point, a
    zero-coverage demand reads as a half-covered one and the single-level
    quantiser answers "fully closed" — a full-scale move in the wrong
    direction, which is why the guard lives in the quantiser rather than on the
    engine hook that both consumers share.
    """

    @pytest.mark.parametrize(
        ("max_slat_angle", "pivot"),
        [
            (45.0, 200.0),
            (60.0, 150.0),
        ],
    )
    @pytest.mark.parametrize("n_steps", [1, 2, 3])
    def test_louvered_roof_topping_out_below_horizontal(
        self, max_slat_angle, pivot, n_steps
    ):
        """The drive stops short of horizontal, so 100 % is the most-open slat.

        The solve saturates at the configured ceiling, so the raw percentage is
        100 — a demand for NO coverage. Movement minimisation must leave it
        there; measuring it as distance from an unreachable pivot instead makes
        it half-covered (100 is 100 of the 200 points below a 200 % pivot) and
        commands 0 — the fully-closed slat.
        """
        cover = _louvered_cover(sol_elev=45.0, max_slat_angle=max_slat_angle)
        assert cover._horizontal_percentage() == pytest.approx(pivot)
        assert cover.calculate_raw_percentage() == 100.0
        assert _tilt_solar_position(cover) == 100
        assert _tilt_solar_position_minimized(cover, n_steps) == 100

    @pytest.mark.parametrize("n_steps", [1, 2, 3])
    def test_specify_angles_calibrated_entirely_below_horizontal(self, n_steps):
        """0° → 0 %, 45° → 100 %: horizontal would be 200 %."""
        cover = _tilt_cover(
            sol_elev=45.0, mode="specify_angles", angle_0=0.0, angle_100=45.0
        )
        assert cover._horizontal_percentage() == pytest.approx(200.0)
        assert cover.calculate_raw_percentage() == 100.0
        assert _tilt_solar_position(cover) == 100
        assert _tilt_solar_position_minimized(cover, n_steps) == 100

    @pytest.mark.parametrize("n_steps", [1, 2, 3])
    def test_specify_angles_calibrated_entirely_above_horizontal(self, n_steps):
        """120° → 0 %, 180° → 100 %: horizontal would be −50 %.

        The mirror image, and the one that used to move the FARTHEST: every
        reachable percentage is at least a third of the extended span away from
        a −50 % pivot, so a single-level quantiser answered 100 % for the whole
        travel — the slats pinned fully closed for as long as the sun was
        tracked.
        """
        cover = _tilt_cover(
            sol_elev=45.0, mode="specify_angles", angle_0=120.0, angle_100=180.0
        )
        assert cover._horizontal_percentage() == pytest.approx(-50.0)
        assert cover.calculate_raw_percentage() == 0.0
        assert _tilt_solar_position(cover) == 0
        assert _tilt_solar_position_minimized(cover, n_steps) == 0


class TestProportionalBandKeepsTheCommandSpacePivot:
    """The ``[min_tilt, max_tilt]`` transform does not move horizontal (#1104).

    ``tilt_transform=proportional`` (#957) linearly remaps the full 0–100 %
    solar DEMAND onto the configured band. What comes out is the tilt
    percentage handed to the cover entity, and the entity's scale is whatever
    the tilt mode declares — on MODE2, 0–100 % is 0–180°, so the horizontal
    slat sits at 50 % of the COMMAND no matter which band the demand was
    squeezed into. That 50 % is exactly what ``_horizontal_percentage``
    reports, so the pivot and every value measured against it already share one
    space: ``round_toward_coverage``'s comparison, the coverage-step quantiser
    and the anticipation comparator all read post-transform commands, and none
    of them wants a second remap applied to the pivot.

    Remapping the pivot into the band instead (50 → 25 for ``[0, 50]``) would
    put the pivot at 45° — a slat 45° AWAY from horizontal — and then round the
    30 % command "away" from it to 62 %, which is both less protective than the
    command it replaced (21.6° off horizontal versus 36.0°) and outside the
    user's own ``max_tilt``.
    """

    def _banded(self, *, sol_elev=45.0, min_tilt=0, max_tilt=50):
        return _tilt_cover(
            sol_elev=sol_elev,
            min_tilt=min_tilt,
            max_tilt=max_tilt,
            tilt_transform=VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
        )

    def test_pivot_is_reported_in_the_command_space_the_band_produces(self):
        """The 59.37 % demand becomes a 30 % command; horizontal is still 50 %."""
        cover = self._banded()
        assert cover._percentage_from_angle(
            cover.calculate_position()
        ) == pytest.approx(59.374719, abs=1e-5)
        assert cover.calculate_raw_percentage() == 30.0
        assert cover.coverage_pivot_percentage() == 50.0
        assert _tilt_solar_position(cover) == 30

    @pytest.mark.parametrize(
        ("n_steps", "expected"),
        [
            # 30 % is 20 of the 50 points below the pivot → 0.4 of the lower
            # side. One level → the bottom of it; two → the half-step at 25 %;
            # three → ceil(1.2)/3 = 2/3 → 17 %.
            (1, 0),
            (2, 25),
            (3, 17),
        ],
    )
    def test_quantising_a_banded_command_increases_its_coverage(
        self, n_steps, expected
    ):
        cover = self._banded()
        commanded = _tilt_solar_position_minimized(cover, n_steps)
        assert commanded == expected
        # Every answer is a slat FARTHER from the horizontal 50 % than the
        # unquantised 30 % command it replaced — never nearer.
        assert abs(commanded - 50) >= abs(30 - 50)

    def test_upper_band_mirrors_the_lower_one(self):
        """``[50, 100]`` puts the same demand at 80 %, above the pivot."""
        cover = self._banded(min_tilt=50, max_tilt=100)
        assert cover.calculate_raw_percentage() == 80.0
        assert _tilt_solar_position(cover) == 80
        assert _tilt_solar_position_minimized(cover, 2) == 100
        assert _tilt_solar_position_minimized(cover, 3) == 83

    @pytest.mark.parametrize("n_steps", [1, 2, 3])
    @pytest.mark.parametrize(
        ("min_tilt", "max_tilt"), [(0, 50), (50, 100), (25, 75), (20, 60), (0, 100)]
    )
    @pytest.mark.parametrize(
        "sol_elev", [5.0, 12.5, 21.0, 29.0, 34.4, 37.5, 44.0, 52.0, 68.0, 79.0, 88.0]
    )
    def test_minimisation_never_reduces_coverage_under_a_band(
        self, sol_elev, min_tilt, max_tilt, n_steps
    ):
        """Swept: no band, elevation or step count lets the quantiser open up."""
        cover = self._banded(sol_elev=sol_elev, min_tilt=min_tilt, max_tilt=max_tilt)
        state = _tilt_solar_position(cover)
        commanded = _tilt_solar_position_minimized(cover, n_steps)
        pivot = cover.coverage_pivot_percentage()
        assert abs(commanded - pivot) >= abs(state - pivot)


class TestTiltOnlyBandSurvivesTheQuantisation:
    """The quantised tilt stays inside ``[min_tilt, max_tilt]`` (issue #1090).

    The tilt-only / louvered-roof path applies the band INSIDE
    ``calculate_raw_percentage`` — before the solar branch quantises — and
    ``_apply_tilt_axis_limits`` deliberately hands back the exact float whenever
    ``round()`` of it is still in band, to preserve precision. That prediction
    stopped matching the quantiser once the tilt axis started rounding away from
    horizontal: a raw percentage inside ``(max_tilt, max_tilt + 0.5)`` passes the
    band check and is then pushed one point past the cap by ``ceil``. The mirror
    window ``(min_tilt - 0.5, min_tilt)`` escapes downward under ``floor``.

    Venetian never had either problem — its ``_clamp_tilt`` runs after the
    rounding — so the guard belongs on the integer this engine hands out rather
    than mirrored at each call site.
    """

    def test_raw_percentage_can_sit_just_past_the_cap(self):
        """Pre-condition: the pre-quantisation band check cannot see 55.003 %.

        At 39.81° the MODE2 solve is 99.005°, i.e. 55.0029 % — above ``max_tilt``
        but under ``max_tilt + 0.5``, so ``int(round(pct))`` is exactly the cap
        and the band leaves the float untouched.
        """
        cover = _tilt_cover(sol_elev=39.81, max_tilt=55)
        raw = cover.calculate_raw_percentage()
        assert raw == pytest.approx(55.002900, abs=1e-5)
        assert 55 < raw < 55.5
        assert int(round(raw)) == 55

    @pytest.mark.parametrize(
        ("transform", "expected"),
        [
            # clamp: ceil(55.0029) = 56 must be pulled back onto the cap.
            (VENETIAN_TILT_TRANSFORM_CLAMP, 55),
            # proportional: the remap already lands the demand inside [0, 55]
            # (round(55 × 0.55) = 30) and re-banding an in-band value is a no-op,
            # so this path is unchanged by the guard.
            (VENETIAN_TILT_TRANSFORM_PROPORTIONAL, 30),
        ],
    )
    def test_ceil_cannot_command_past_max_tilt(self, transform, expected):
        cover = _tilt_cover(sol_elev=39.81, max_tilt=55, tilt_transform=transform)
        assert _tilt_solar_position(cover) == expected

    def test_raw_percentage_can_sit_just_under_the_floor(self):
        """Pre-condition mirror: 44.7546 % rounds to the ``min_tilt`` of 45."""
        cover = _tilt_cover(sol_elev=27.0, min_tilt=45)
        raw = cover.calculate_raw_percentage()
        assert raw == pytest.approx(44.754618, abs=1e-5)
        assert 44.5 < raw < 45
        assert int(round(raw)) == 45

    @pytest.mark.parametrize(
        ("transform", "expected"),
        [
            # clamp: floor(44.7546) = 44 must be lifted back onto the floor.
            (VENETIAN_TILT_TRANSFORM_CLAMP, 45),
            # proportional: round(45 + 55 × 0.45) = 70, already inside [45, 100].
            (VENETIAN_TILT_TRANSFORM_PROPORTIONAL, 70),
        ],
    )
    def test_floor_cannot_command_below_min_tilt(self, transform, expected):
        cover = _tilt_cover(sol_elev=27.0, min_tilt=45, tilt_transform=transform)
        assert _tilt_solar_position(cover) == expected

    @pytest.mark.parametrize(
        "transform",
        [VENETIAN_TILT_TRANSFORM_CLAMP, VENETIAN_TILT_TRANSFORM_PROPORTIONAL],
    )
    @pytest.mark.parametrize(
        ("min_tilt", "max_tilt"), [(0, 100), (0, 55), (45, 100), (45, 55)]
    )
    @pytest.mark.parametrize(
        "sol_elev", [5.0, 21.0, 27.0, 34.4, 39.81, 45.0, 68.0, 88.0]
    )
    def test_commanded_tilt_always_lands_inside_the_band(
        self, sol_elev, min_tilt, max_tilt, transform
    ):
        """Swept invariant: no band, elevation, or transform escapes by a point."""
        cover = _tilt_cover(
            sol_elev=sol_elev,
            min_tilt=min_tilt,
            max_tilt=max_tilt,
            tilt_transform=transform,
        )
        assert min_tilt <= _tilt_solar_position(cover) <= max_tilt

    def test_default_band_is_untouched(self):
        """0/100 is a no-op — the guard must not perturb the shipped default."""
        for sol_elev, expected in ((27.0, 44), (34.4, 51), (39.81, 56), (45.0, 60)):
            assert _tilt_solar_position(_tilt_cover(sol_elev=sol_elev)) == expected

    def test_venetian_ownership_flag_still_suppresses_the_band(self):
        """Venetian's sub-engine owns no band — ``_clamp_tilt`` applies it after.

        The composed engine is built with ``apply_tilt_axis_limits=False``, which
        must keep the post-quantisation guard out of the way exactly as it keeps
        the pre-quantisation transform out of the way; otherwise the band (and,
        on the proportional transform, the remap) would be applied twice.
        """
        cover = _tilt_cover(sol_elev=39.81, max_tilt=55)
        cover.apply_tilt_axis_limits = False
        assert cover.round_toward_coverage(55.0029, full_coverage_at_zero=True) == 56
