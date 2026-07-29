"""Tests for directional (conservative) position rounding (issue #978).

Conservative rounding biases the solar position toward full coverage instead of
nearest integer:
  - Blind / tilt / venetian  (0% = closed = full coverage): floor()
  - Awning                   (100% = extended = full coverage): ceil()

This is now always-on behavior (no opt-in flag) keyed off the policy's
``open_blocks_sun`` axis attribute.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import TILT_HORIZONTAL_DEG, TiltMode
from custom_components.adaptive_cover_pro.engine.covers import AdaptiveTiltCover
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

    def test_mode1_still_floors_everywhere(self):
        """#978 regression guard: MODE1 spans 0–90°, so horizontal is 100 %.

        Every reachable MODE1 percentage is therefore at or below the pivot and
        the away-from-horizontal rule collapses to the plain floor MODE1 always
        had — the direction must fall out of the geometry, not a mode branch.
        """
        cover = _tilt_cover(sol_elev=45.0, mode="mode1")
        cover.calculate_position = MagicMock(return_value=41.0)

        raw = cover.calculate_raw_percentage()
        assert raw == pytest.approx(41.0 / 90.0 * 100.0)
        assert _tilt_solar_position(cover) == 45  # round() would give 46


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
