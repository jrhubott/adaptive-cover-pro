"""Tests for the sill_height parameter in AdaptiveVerticalCover.

The sill_height parameter accounts for windows that do not start at floor level.
When a window sill is at height S above the floor, the blind bottom is also at
height S. The sill already blocks S/tan(elevation) meters of horizontal sun
penetration for free, so the effective distance the blind needs to cover is
reduced. This means the blind can be raised higher (smaller blind height needed).

Implementation: effective_distance -= sill_height / max(tan(elevation), 0.05)

When effective_distance goes negative (sill shadow deeper than shaded distance),
the blind must be FULLY CLOSED (position=0), not fully open. See the geometry
explanation in TestRaysAbovefloorAtShadedBoundary and vertical.py for details.
"""

import math

import numpy as np
import pytest

from custom_components.adaptive_cover_pro.calculation import (
    AdaptiveVerticalCover,
)
from tests.cover_helpers import build_horizontal_cover, build_vertical_cover


def gamma_to_sol_azi(win_azi: float, gamma: float) -> float:
    """Convert gamma angle to sol_azi.

    gamma = (win_azi - sol_azi + 180) % 360 - 180
    Solving for sol_azi: sol_azi = (win_azi - gamma) % 360
    """
    return (win_azi - gamma) % 360


@pytest.fixture
def base_cover_params(mock_sun_data, mock_logger):
    """Return base parameters for AdaptiveVerticalCover with sill_height=0."""
    return {
        "logger": mock_logger,
        "sol_azi": 180.0,
        "sol_elev": 45.0,
        "sunset_pos": 0,
        "sunset_off": 0,
        "sunrise_off": 0,
        "sun_data": mock_sun_data,
        "fov_left": 90,
        "fov_right": 90,
        "win_azi": 180,
        "h_def": 50,
        "max_pos": 100,
        "min_pos": 0,
        "max_pos_bool": False,
        "min_pos_bool": False,
        "blind_spot_left": None,
        "blind_spot_right": None,
        "blind_spot_elevation": None,
        "blind_spot_on": False,
        "min_elevation": None,
        "max_elevation": None,
        "distance": 0.5,
        "h_win": 2.1,
        # sill_height omitted — should default to 0.0
    }


def make_vertical_cover(
    base_params: dict, gamma: float, sol_elev: float, **overrides
) -> AdaptiveVerticalCover:
    """Create an AdaptiveVerticalCover with specific angles and optional overrides."""
    params = base_params.copy()
    params.update(overrides)
    params["sol_azi"] = gamma_to_sol_azi(params["win_azi"], gamma)
    params["sol_elev"] = sol_elev
    return build_vertical_cover(**params)


class TestBackwardCompatibility:
    """Tests that sill_height=0 (default) produces bit-identical results."""

    def test_default_sill_height_is_zero(self, base_cover_params):
        """sill_height should default to 0.0 when not specified."""
        cover = build_vertical_cover(**base_cover_params)
        assert cover.sill_height == 0.0

    def test_explicit_zero_matches_default_at_normal_angle(self, base_cover_params):
        """Explicit sill_height=0.0 must produce same result as omitting the field."""
        params_default = base_cover_params.copy()
        params_default["sol_azi"] = gamma_to_sol_azi(params_default["win_azi"], 30.0)

        params_explicit = base_cover_params.copy()
        params_explicit["sill_height"] = 0.0
        params_explicit["sol_azi"] = gamma_to_sol_azi(params_explicit["win_azi"], 30.0)

        cover_default = build_vertical_cover(**params_default)
        cover_explicit = build_vertical_cover(**params_explicit)

        assert cover_default.calculate_position() == cover_explicit.calculate_position()

    def test_zero_sill_height_no_regression_multiple_angles(self, base_cover_params):
        """sill_height=0 must not change results vs. no sill_height, across many angles."""
        test_cases = [
            (0.0, 30.0),
            (0.0, 45.0),
            (0.0, 60.0),
            (30.0, 45.0),
            (60.0, 45.0),
            (-30.0, 30.0),
            (45.0, 20.0),
        ]

        for gamma, sol_elev in test_cases:
            params_no_sill = base_cover_params.copy()
            params_no_sill["sol_azi"] = gamma_to_sol_azi(
                params_no_sill["win_azi"], gamma
            )
            params_no_sill["sol_elev"] = sol_elev

            params_zero_sill = params_no_sill.copy()
            params_zero_sill["sill_height"] = 0.0

            pos_no_sill = build_vertical_cover(**params_no_sill).calculate_position()
            pos_zero_sill = build_vertical_cover(
                **params_zero_sill
            ).calculate_position()

            assert pos_no_sill == pos_zero_sill, (
                f"Mismatch at gamma={gamma}, elev={sol_elev}: "
                f"no_sill={pos_no_sill:.6f}, zero_sill={pos_zero_sill:.6f}"
            )


class TestGeometrySillHeightEffect:
    """Tests that verify the correct geometric effect of sill_height.

    Correct geometry: When a window sill is at height S above the floor, the blind
    bottom starts at S meters height. The sill already provides S/tan(elevation) meters
    of "free" horizontal sun protection. So the effective distance the blind needs to
    cover is reduced by S/tan(elevation), meaning:
      - effective_distance = distance - sill_height / tan(elevation)
      - base_height = effective_distance * tan(elevation) / cos(gamma)
      - base_height DECREASES with sill_height (blind can be raised higher)

    When effective_distance goes negative (sill shadow deeper than shaded distance),
    it is clamped to 0 → blind fully closed. See TestRaysAbovefloorAtShadedBoundary.
    """

    def test_sill_height_decreases_position_low_elevation(self, base_cover_params):
        """sill_height > 0 DECREASES blind height at low elevation (20°).

        The sill provides free protection, so less blind extension is needed.
        Uses sill_height=0.1 so effective_distance stays positive (no sill-blocks-sun
        short-circuit): sill_offset≈0.275m, effective=0.225m > 0.
        """
        cover_no_sill = make_vertical_cover(base_cover_params, gamma=0.0, sol_elev=20.0)
        cover_with_sill = make_vertical_cover(
            base_cover_params, gamma=0.0, sol_elev=20.0, sill_height=0.1
        )

        pos_no_sill = cover_no_sill.calculate_position()
        pos_with_sill = cover_with_sill.calculate_position()

        # Correct: sill subtracts from effective_distance -> decreases position
        assert pos_with_sill < pos_no_sill, (
            f"sill_height should decrease position at low elevation: "
            f"no_sill={pos_no_sill:.4f}, with_sill={pos_with_sill:.4f}"
        )

    def test_sill_height_decreases_position_medium_elevation(self, base_cover_params):
        """sill_height > 0 DECREASES blind height at medium elevation (45°).

        The sill provides free protection, so less blind extension is needed.
        Uses sill_height=0.2 so effective_distance stays positive (no sill-blocks-sun
        short-circuit): sill_offset=0.2m, effective=0.3m > 0.
        """
        cover_no_sill = make_vertical_cover(base_cover_params, gamma=0.0, sol_elev=45.0)
        cover_with_sill = make_vertical_cover(
            base_cover_params, gamma=0.0, sol_elev=45.0, sill_height=0.2
        )

        pos_no_sill = cover_no_sill.calculate_position()
        pos_with_sill = cover_with_sill.calculate_position()

        # Correct: sill subtracts from effective_distance -> decreases position
        assert pos_with_sill < pos_no_sill, (
            f"sill_height should decrease position at medium elevation: "
            f"no_sill={pos_no_sill:.4f}, with_sill={pos_with_sill:.4f}"
        )

    def test_sill_height_decreases_position_high_elevation(self, base_cover_params):
        """sill_height > 0 DECREASES blind height at high elevation (70°).

        The sill provides free protection, so less blind extension is needed.
        """
        cover_no_sill = make_vertical_cover(base_cover_params, gamma=0.0, sol_elev=70.0)
        cover_with_sill = make_vertical_cover(
            base_cover_params, gamma=0.0, sol_elev=70.0, sill_height=0.5
        )

        pos_no_sill = cover_no_sill.calculate_position()
        pos_with_sill = cover_with_sill.calculate_position()

        # Correct: sill subtracts from effective_distance -> decreases position
        assert pos_with_sill < pos_no_sill, (
            f"sill_height should decrease position at high elevation: "
            f"no_sill={pos_no_sill:.4f}, with_sill={pos_with_sill:.4f}"
        )

    def test_larger_sill_height_decreases_position_more(self, base_cover_params):
        """Larger sill_height produces progressively SMALLER blind height.

        More sill height = more free protection = less blind extension needed.
        Uses h_win=2.1 and distance=0.5; at 45° elevation, base without sill=0.5m.
        Sweep stays in the no-clip regime (all sill_offsets < 0.5m distance).
        """
        sill_heights = [0.0, 0.1, 0.2, 0.3]
        positions = []

        for sh in sill_heights:
            cover = make_vertical_cover(
                base_cover_params, gamma=0.0, sol_elev=45.0, sill_height=sh
            )
            positions.append(cover.calculate_position())

        # Positions should be monotonically decreasing with sill_height
        for i in range(len(positions) - 1):
            assert (
                positions[i] >= positions[i + 1]
            ), f"Expected monotonic decrease: sill_heights={sill_heights}, positions={positions}"

        # Once sill_offset >= distance (sill_height=0.5 at 45°), even the lowest
        # glass entry produces a ray above the floor at the shaded boundary →
        # blind must be fully closed (position=0), not open. Issue #358.
        clip_case = make_vertical_cover(
            base_cover_params, gamma=0.0, sol_elev=45.0, sill_height=0.5
        )
        assert clip_case.calculate_position() == 0.0


class TestMathVerification:
    """Tests that verify the sill_height offset formula is correctly implemented."""

    def test_sill_offset_formula_at_45_degrees(self, base_cover_params):
        """At 45° elevation, sill_offset = sill_height / tan(45°) = sill_height."""
        # With gamma=0, cos(gamma)=1, so path_length = effective_distance
        # base_height = effective_distance * tan(sol_elev)
        # At sol_elev=45°, tan(45°)=1, so base_height = effective_distance
        # effective_distance = distance - sill_offset = 0.5 - 1.0 / tan(45°) = 0.5 - 1.0 = -0.5
        # Clipped to 0 by np.clip -> position = 0.0 (sill already blocks all sun)
        # Use a smaller sill_height to stay positive: sill_height=0.3
        # effective_distance = 0.5 - 0.3 = 0.2
        # base_height = 0.2 * tan(45°) = 0.2

        sill_height = 0.3
        sol_elev = 45.0
        distance = 0.5
        expected_sill_offset = sill_height / math.tan(math.radians(sol_elev))  # = 0.3
        expected_effective_distance = distance - expected_sill_offset  # = 0.2
        expected_base_height = expected_effective_distance * math.tan(
            math.radians(sol_elev)
        )  # = 0.2

        cover = make_vertical_cover(
            base_cover_params,
            gamma=0.0,
            sol_elev=sol_elev,
            distance=distance,
            sill_height=sill_height,
        )
        position = cover.calculate_position()

        # At gamma=0, sol_elev=45°, safety margin = 1.0, so result = expected_base_height
        # expected 0.2 < h_win=2.1, so no clipping
        assert (
            abs(position - expected_base_height) < 1e-6
        ), f"Expected {expected_base_height:.6f} but got {position:.6f}"

    def test_sill_offset_formula_at_30_degrees(self, base_cover_params):
        """At 30° elevation, sill_offset = sill_height / tan(30°)."""
        sill_height = 0.1
        sol_elev = 30.0
        distance = 0.5

        expected_sill_offset = sill_height / math.tan(math.radians(sol_elev))
        expected_effective_distance = distance - expected_sill_offset
        expected_base_height = expected_effective_distance * math.tan(
            math.radians(sol_elev)
        )

        cover = make_vertical_cover(
            base_cover_params,
            gamma=0.0,
            sol_elev=sol_elev,
            distance=distance,
            sill_height=sill_height,
        )
        position = cover.calculate_position()

        # At gamma=0, sol_elev=30°, safety margin = 1.0
        assert (
            abs(position - expected_base_height) < 1e-6
        ), f"Expected {expected_base_height:.6f} but got {position:.6f}"

    def test_sill_offset_reduces_effective_distance(self, base_cover_params):
        """The sill_offset subtracts from effective_distance, reducing required position.

        The sill provides free horizontal protection, so less blind extension is needed.
        Correct behavior: sill_height reduces effective_distance -> reduces position.
        """
        cover_no_sill = make_vertical_cover(
            base_cover_params, gamma=0.0, sol_elev=45.0, distance=0.5
        )
        cover_with_sill = make_vertical_cover(
            base_cover_params, gamma=0.0, sol_elev=45.0, distance=0.5, sill_height=0.3
        )
        pos_no_sill = cover_no_sill.calculate_position()
        pos_with_sill = cover_with_sill.calculate_position()

        # Correct: sill reduces effective_distance -> reduces position (blind raised higher)
        assert pos_with_sill < pos_no_sill, (
            f"With sill, position should be smaller (less blind needed): "
            f"no_sill={pos_no_sill:.4f}, with_sill={pos_with_sill:.4f}"
        )


class TestEdgeCasesLowElevation:
    """Tests for edge cases at low sun elevation angles."""

    def test_very_low_elevation_large_sill_returns_zero(self, base_cover_params):
        """At sol_elev=2.5° with large sill_height, sill shadow deeply exceeds shaded distance.

        tan(2.5°) ≈ 0.0437 < 0.05 guard → sill_offset = 3.0 / 0.05 = 60.0
        effective_distance = 0.5 - 60.0 = -59.5 → clamped to 0 → position = 0 (fully closed).

        The sill shadow passes the shaded boundary, but sun still enters through the glass
        above the sill and those rays are above the floor at the boundary. Blind must close.
        Issue #358 (reverts inverted #304 logic).
        """
        cover = make_vertical_cover(
            base_cover_params,
            gamma=0.0,
            sol_elev=2.5,
            sill_height=3.0,
        )
        position = cover.calculate_position()

        assert (
            position == 0.0
        ), f"Expected position=0.0 (blind fully closed) but got {position}"

    def test_very_low_elevation_no_exception_raised(self, base_cover_params):
        """At sol_elev=2.5° with large sill_height, no exception should be raised."""
        cover = make_vertical_cover(
            base_cover_params,
            gamma=0.0,
            sol_elev=2.5,
            sill_height=3.0,
        )
        # Should not raise
        position = cover.calculate_position()
        assert position is not None

    def test_moderate_sill_height_at_low_elevation(self, base_cover_params):
        """At sol_elev=2.5° with moderate sill_height, guard clamps the tan denominator.

        tan(2.5°) ≈ 0.0437 < 0.05 guard → sill_offset = 1.0 / 0.05 = 20.0
        effective_distance = 0.5 - 20.0 = -19.5 → clamped to 0 → position = 0 (fully closed).
        """
        cover = make_vertical_cover(
            base_cover_params,
            gamma=0.0,
            sol_elev=2.5,
            sill_height=1.0,
        )
        position = cover.calculate_position()

        assert 0 <= position <= cover.h_win, f"Position out of bounds: {position}"
        assert not np.isnan(position)
        assert not np.isinf(position)


class TestEdgeCasesAtThreshold:
    """Tests for the 2° edge case threshold interaction with sill_height."""

    def test_elevation_below_threshold_edge_case_overrides_sill(
        self, base_cover_params
    ):
        """At sol_elev < 2° (edge case), result is h_win regardless of sill_height."""
        # Edge case handler runs BEFORE sill_height calculation, so sill_height is irrelevant
        cover_no_sill = make_vertical_cover(base_cover_params, gamma=0.0, sol_elev=1.0)
        cover_with_sill = make_vertical_cover(
            base_cover_params, gamma=0.0, sol_elev=1.0, sill_height=5.0
        )

        pos_no_sill = cover_no_sill.calculate_position()
        pos_with_sill = cover_with_sill.calculate_position()

        # Both should return h_win (edge case handler fires first)
        assert pos_no_sill == cover_no_sill.h_win
        assert pos_with_sill == cover_with_sill.h_win

    def test_elevation_below_threshold_different_sill_heights_same_result(
        self, base_cover_params
    ):
        """Different sill_heights produce identical results when elevation < 2°."""
        results = []
        for sill_h in [0.0, 0.5, 1.0, 2.0, 5.0]:
            cover = make_vertical_cover(
                base_cover_params, gamma=0.0, sol_elev=1.5, sill_height=sill_h
            )
            results.append(cover.calculate_position())

        # All should be h_win, all identical
        assert all(
            r == results[0] for r in results
        ), f"Expected identical results for all sill_heights at elev=1.5°: {results}"


class TestInteractionWithWindowDepth:
    """Tests for combined sill_height + window_depth interaction."""

    def test_both_params_combine_independently(self, base_cover_params):
        """With both sill_height and window_depth, both affect effective_distance.

        window_depth adds to effective_distance; sill_height subtracts from it.
        """
        # gamma=30° > 10° threshold, so window_depth contributes
        gamma = 30.0
        sol_elev = 45.0

        cover_neither = make_vertical_cover(
            base_cover_params,
            gamma=gamma,
            sol_elev=sol_elev,
            sill_height=0.0,
            window_depth=0.0,
        )
        cover_depth_only = make_vertical_cover(
            base_cover_params,
            gamma=gamma,
            sol_elev=sol_elev,
            sill_height=0.0,
            window_depth=0.1,
        )
        cover_sill_only = make_vertical_cover(
            base_cover_params,
            gamma=gamma,
            sol_elev=sol_elev,
            sill_height=0.5,
            window_depth=0.0,
        )
        cover_both = make_vertical_cover(
            base_cover_params,
            gamma=gamma,
            sol_elev=sol_elev,
            sill_height=0.5,
            window_depth=0.1,
        )

        pos_neither = cover_neither.calculate_position()
        pos_depth_only = cover_depth_only.calculate_position()
        pos_sill_only = cover_sill_only.calculate_position()
        pos_both = cover_both.calculate_position()

        # Both individually change position from baseline
        assert pos_depth_only != pos_neither
        assert pos_sill_only != pos_neither

        # Combined should differ from each individually
        assert pos_both != pos_neither

    def test_combined_result_is_consistent_with_offsets(self, base_cover_params):
        """window_depth adds and sill_height subtracts from effective_distance independently."""
        gamma = 30.0
        sol_elev = 45.0
        window_depth = 0.1
        sill_height = 0.1  # Small enough that effective_distance stays positive

        # Calculate expected combined effective_distance manually:
        # window_depth adds, sill_height subtracts
        depth_contrib = window_depth * math.sin(math.radians(abs(gamma)))
        sill_offset = sill_height / math.tan(math.radians(sol_elev))
        expected_effective_dist = 0.5 + depth_contrib - sill_offset  # base distance=0.5

        # Expected base_height (gamma=30°, safety margin = 1.0 at this angle)
        expected_path_length = expected_effective_dist / math.cos(math.radians(gamma))
        expected_base_height = expected_path_length * math.tan(math.radians(sol_elev))

        cover_both = make_vertical_cover(
            base_cover_params,
            gamma=gamma,
            sol_elev=sol_elev,
            sill_height=sill_height,
            window_depth=window_depth,
        )
        actual_position = cover_both.calculate_position()

        # At gamma=30°, safety margin = 1.0, so actual should match expected
        assert (
            abs(actual_position - expected_base_height) < 1e-6
        ), f"Expected {expected_base_height:.6f} but got {actual_position:.6f}"


class TestHorizontalCoverSillHeight:
    """Tests for sill_height behavior in AdaptiveHorizontalCover."""

    def test_horizontal_cover_has_sill_height_field(self, mock_sun_data, mock_logger):
        """AdaptiveHorizontalCover inherits sill_height from AdaptiveVerticalCover."""
        cover = build_horizontal_cover(
            logger=mock_logger,
            sol_azi=180.0,
            sol_elev=45.0,
            sunset_pos=0,
            sunset_off=0,
            sunrise_off=0,
            sun_data=mock_sun_data,
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
            h_win=2.1,
            awn_length=2.0,
            awn_angle=0,
        )
        # sill_height defaults to 0.0 (in vert_config)
        assert cover.sill_height == 0.0

    def test_horizontal_cover_default_sill_vs_explicit_zero(
        self, mock_sun_data, mock_logger
    ):
        """Horizontal cover with sill_height=0.0 and without it produce identical results."""
        common_params = {
            "logger": mock_logger,
            "sol_azi": 180.0,
            "sol_elev": 45.0,
            "sunset_pos": 0,
            "sunset_off": 0,
            "sunrise_off": 0,
            "sun_data": mock_sun_data,
            "fov_left": 90,
            "fov_right": 90,
            "win_azi": 180,
            "h_def": 50,
            "max_pos": 100,
            "min_pos": 0,
            "max_pos_bool": False,
            "min_pos_bool": False,
            "blind_spot_left": None,
            "blind_spot_right": None,
            "blind_spot_elevation": None,
            "blind_spot_on": False,
            "min_elevation": None,
            "max_elevation": None,
            "distance": 0.5,
            "h_win": 2.1,
            "awn_length": 2.0,
            "awn_angle": 0,
        }

        cover_default = build_horizontal_cover(**common_params)

        params_explicit = common_params.copy()
        params_explicit["sill_height"] = 0.0
        cover_explicit = build_horizontal_cover(**params_explicit)

        assert cover_default.calculate_position() == cover_explicit.calculate_position()

    def test_sill_height_not_in_horizontal_cover_config_schema(self):
        """Verify sill_height is accessible on horizontal covers via vert_config.

        AdaptiveHorizontalCover inherits sill_height from AdaptiveVerticalCover
        (via vert_config), but sill_height is a vertical-cover-specific parameter.
        The config flow should not expose it for horizontal covers.
        """
        from custom_components.adaptive_cover_pro.config_types import VerticalConfig

        # sill_height is a field on VerticalConfig with default 0.0
        import dataclasses

        fields = {f.name: f for f in dataclasses.fields(VerticalConfig)}
        assert "sill_height" in fields
        assert fields["sill_height"].default == 0.0


class TestIssue358SillBlocksSunFullyCloses:
    """Regression for issue #358: when the sill shadow depth exceeds the shaded
    distance, every ray entering the glass is still above the floor at the shaded
    boundary and keeps traveling deeper into the room. The blind must be FULLY CLOSED
    (position=0), not fully open.

    Geometry: a ray entering at height h is at h - d*tan(θ) at horizontal distance d.
    When sill/tan(θ) >= shaded_distance, even the lowest glass entry (at sill_height)
    is at height sill_height - d*tan(θ) > 0 at the boundary — it hasn't hit the floor
    and keeps penetrating. All higher entries are worse. Blind must be fully closed.

    (Issue #304 had this inverted — it returned h_win (fully open). Issue #358 corrects
    it by clamping effective_distance to 0 and letting the normal path produce position=0.)
    """

    def test_sill_blocks_sun_returns_zero_low_elevation(self, base_cover_params):
        """sol_elev=20°, sill=0.5, distance=0.5: sill_offset≈1.37m exceeds distance.
        Every ray through the glass is still above the floor at the boundary.
        Blind must be fully closed → position=0.
        """
        cover = make_vertical_cover(
            base_cover_params, gamma=0.0, sol_elev=20.0, sill_height=0.5
        )
        position = cover.calculate_position()
        assert position == 0.0, (
            f"Sill shadow exceeds shaded distance → blind must be fully closed (0.0), "
            f"got {position}"
        )

    def test_sill_blocks_sun_returns_zero_exact_clip(self, base_cover_params):
        """sol_elev=45°, sill=0.5, distance=0.5: sill_offset=0.5 exactly cancels distance.
        effective_distance=0 → lowest glass entry ray is exactly at floor at boundary.
        Position must be 0 (fully closed — no glass can be safely exposed).
        """
        cover = make_vertical_cover(
            base_cover_params, gamma=0.0, sol_elev=45.0, sill_height=0.5
        )
        assert cover.calculate_position() == 0.0

    def test_sill_blocks_sun_percentage_is_zero(self, base_cover_params):
        """HA-facing percentage must be 0 (closed) when sill shadow exceeds shaded distance."""
        cover = make_vertical_cover(
            base_cover_params, gamma=0.0, sol_elev=20.0, sill_height=0.5
        )
        assert cover.calculate_percentage() == 0


class TestIssue358ReporterRegression:
    """Exact reproduction of the reporter's values from issue #358.

    Reporter config: sill_height=1.6m, h_win=0.55m, distance=1.0m,
    sol_elev=47.4°, gamma≈35.8°.

    Geometry: sill_offset = 1.6 / tan(47.4°) ≈ 1.467m
    effective_distance = 1.0 - 1.467 = -0.467 (< 0 → clamp to 0 → position=0)

    The sun enters through 0.55m of glass above the sill and every ray through
    that glass is still above the floor at the 1.0m shaded boundary. The blind
    must be fully closed.
    """

    def test_reporter_values_position_is_zero(self, base_cover_params):
        """Reporter's geometry must produce position=0 (fully closed)."""
        cover = make_vertical_cover(
            base_cover_params,
            gamma=35.8,
            sol_elev=47.4,
            sill_height=1.6,
            h_win=0.55,
            distance=1.0,
        )
        assert cover.calculate_position() == 0.0, (
            f"Reporter's geometry should produce fully closed blind (position=0), "
            f"got {cover.calculate_position()}"
        )

    def test_reporter_values_percentage_is_zero(self, base_cover_params):
        """Reporter's geometry must produce percentage=0 (HA shows 0% = fully closed)."""
        cover = make_vertical_cover(
            base_cover_params,
            gamma=35.8,
            sol_elev=47.4,
            sill_height=1.6,
            h_win=0.55,
            distance=1.0,
        )
        assert cover.calculate_percentage() == 0, (
            f"Reporter's geometry should produce percentage=0, "
            f"got {cover.calculate_percentage()}"
        )


class TestRaysAbovefloorAtShadedBoundary:
    """Tests for the regime where sill shadow exceeds shaded distance.

    When sill/tan(θ) > shaded_distance, a ray entering at the sill (lowest glass point)
    is at height sill - d*tan(θ) > 0 at horizontal distance d = shaded_distance. That
    ray is above the floor and keeps travelling into the room. Every higher entry is
    worse. The blind must be fully closed (position=0).

    This is NOT the same as "the sill blocks all sun." The sill creates a shadow zone
    close to the window, but sun enters through the glass above the sill and illuminates
    the room past the shaded distance. The blind is needed to stop it.
    """

    def test_tall_sill_high_elevation(self, base_cover_params):
        """sill=1.6, dist=1.0, elev=47.4°: sill_offset≈1.47m > 1.0m → position=0."""
        cover = make_vertical_cover(
            base_cover_params,
            gamma=0.0,
            sol_elev=47.4,
            sill_height=1.6,
            distance=1.0,
            h_win=0.55,
        )
        assert cover.calculate_position() == 0.0
        assert cover.calculate_percentage() == 0

    def test_moderate_sill_low_elevation(self, base_cover_params):
        """sill=0.5, dist=0.5, elev=20°: sill_offset≈1.37m > 0.5m → position=0."""
        cover = make_vertical_cover(
            base_cover_params,
            gamma=0.0,
            sol_elev=20.0,
            sill_height=0.5,
            distance=0.5,
        )
        assert cover.calculate_position() == 0.0
        assert cover.calculate_percentage() == 0

    def test_exact_zero_boundary(self, base_cover_params):
        """sill=0.5, dist=0.5, elev=45°: sill_offset=0.5 exactly equals distance.
        effective_distance=0 → position=0 (lowest entry ray hits the floor right at
        the boundary — no glass can be safely exposed).
        """
        cover = make_vertical_cover(
            base_cover_params,
            gamma=0.0,
            sol_elev=45.0,
            sill_height=0.5,
            distance=0.5,
        )
        assert cover.calculate_position() == 0.0
        assert cover.calculate_percentage() == 0


class TestSillGeometryInvariant:
    """Parametrized invariant: whenever analytical effective_distance < 0, position==0.

    Sweeps a grid of (sill, distance, sol_elev, gamma) values. Any combination where
    sill_offset = sill/tan(elev) exceeds shaded_distance analytically must produce
    position=0 from calculate_position(). This guard makes any future "return h_win
    when effective_distance <= 0" regression break dozens of cases at once.
    """

    @pytest.mark.parametrize(
        "sill_height,distance,sol_elev,gamma",
        [
            # Reporter's exact values
            (1.6, 1.0, 47.4, 35.8),
            (1.6, 1.0, 47.4, 0.0),
            # Low elevation (uses MIN_TAN_ELEVATION_CLAMP=0.05)
            (3.0, 0.5, 2.5, 0.0),
            (1.0, 0.5, 2.5, 0.0),
            # Various sill/distance combos at mid elevations
            (0.5, 0.5, 20.0, 0.0),
            (0.5, 0.5, 20.0, 30.0),
            (0.5, 0.5, 20.0, -30.0),
            (1.0, 0.5, 30.0, 0.0),
            (1.0, 0.5, 45.0, 0.0),
            (2.0, 1.0, 30.0, 0.0),
            (2.0, 1.0, 45.0, 0.0),
            (2.0, 1.0, 60.0, 0.0),
            # Exact boundary: sill_offset == distance (effective_distance == 0)
            (0.5, 0.5, 45.0, 0.0),  # sill_offset = 0.5/tan(45°) = 0.5 = distance
            (1.0, 1.0, 45.0, 0.0),
            # Non-zero gamma
            (0.5, 0.5, 20.0, 60.0),
            (1.6, 1.0, 47.4, -35.8),
            (1.0, 0.5, 30.0, 45.0),
            (1.0, 0.5, 30.0, -45.0),
        ],
    )
    def test_negative_effective_distance_implies_position_zero(
        self,
        base_cover_params,
        sill_height: float,
        distance: float,
        sol_elev: float,
        gamma: float,
    ):
        """Any (sill, dist, elev, gamma) where effective_distance <= 0 must yield position=0.

        effective_distance = distance - sill_height / max(tan(sol_elev), 0.05)
        When <= 0, every ray through the glass is above the floor at the shaded
        boundary. Blind must be fully closed.
        """
        import math

        tan_elev = max(math.tan(math.radians(sol_elev)), 0.05)
        sill_offset = sill_height / tan_elev
        analytical_effective_distance = distance - sill_offset

        # Only run the assertion for cases that analytically trigger the clamp
        if analytical_effective_distance > 0:
            pytest.skip(
                f"effective_distance={analytical_effective_distance:.4f} > 0, "
                "skipping (not in clamp regime)"
            )

        cover = make_vertical_cover(
            base_cover_params,
            gamma=gamma,
            sol_elev=sol_elev,
            sill_height=sill_height,
            distance=distance,
        )
        position = cover.calculate_position()
        assert position == 0.0, (
            f"sill={sill_height}, dist={distance}, elev={sol_elev}°, gamma={gamma}°: "
            f"effective_distance={analytical_effective_distance:.4f} ≤ 0 → "
            f"expected position=0.0, got {position}"
        )


class TestNumericalStability:
    """Tests that sill_height does not introduce NaN or inf values."""

    def test_no_nan_or_inf_across_angle_range(self, base_cover_params):
        """No NaN or inf should appear across all valid angle combinations."""
        sill_heights = [0.0, 0.5, 1.0, 2.0]

        for sill_height in sill_heights:
            for gamma in range(-80, 81, 20):
                for sol_elev in range(3, 91, 10):  # Start at 3° (above 2° edge case)
                    cover = make_vertical_cover(
                        base_cover_params,
                        gamma=float(gamma),
                        sol_elev=float(sol_elev),
                        sill_height=sill_height,
                    )
                    position = cover.calculate_position()

                    assert not np.isnan(
                        position
                    ), f"NaN at gamma={gamma}, elev={sol_elev}, sill_height={sill_height}"
                    assert not np.isinf(
                        position
                    ), f"Inf at gamma={gamma}, elev={sol_elev}, sill_height={sill_height}"

    def test_position_always_in_valid_range(self, base_cover_params):
        """Position should always be in [0, h_win] with any sill_height."""
        h_win = 2.1
        for sill_height in [0.0, 0.5, 1.0, 3.0, 10.0]:
            for gamma in range(-80, 81, 20):
                for sol_elev in range(3, 91, 10):
                    cover = make_vertical_cover(
                        base_cover_params,
                        gamma=float(gamma),
                        sol_elev=float(sol_elev),
                        sill_height=sill_height,
                    )
                    position = cover.calculate_position()

                    assert 0 <= position <= h_win, (
                        f"Out-of-range position {position} at gamma={gamma}, "
                        f"elev={sol_elev}, sill_height={sill_height}"
                    )
