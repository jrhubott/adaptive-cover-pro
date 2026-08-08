"""Tests for enhanced geometric accuracy in shadow/glare calculations.

Tests Phase 1 improvements:
- Angle-dependent safety margins
- Edge case handling
- Smooth transitions
- Regression testing (normal angles should show minimal change)
"""

import itertools
import math

import pytest
import numpy as np

from custom_components.adaptive_cover_pro.calculation import AdaptiveVerticalCover
from custom_components.adaptive_cover_pro.geometry import SafetyMarginCalculator
from tests.cover_helpers import build_vertical_cover


def gamma_to_sol_azi(win_azi: float, gamma: float) -> float:
    """Convert gamma angle to sol_azi.

    gamma = (win_azi - sol_azi + 180) % 360 - 180
    Solving for sol_azi:
    sol_azi = (win_azi - gamma) % 360
    """
    return (win_azi - gamma) % 360


def make_cover_with_angles(
    base_params: dict, gamma: float, sol_elev: float
) -> AdaptiveVerticalCover:
    """Create a cover with specific gamma and elevation angles.

    Args:
        base_params: Base parameters dictionary
        gamma: Desired gamma angle (-180 to 180)
        sol_elev: Desired elevation angle (0-90)

    Returns:
        AdaptiveVerticalCover instance configured with the specified angles

    """
    params = base_params.copy()
    params["sol_azi"] = gamma_to_sol_azi(params["win_azi"], gamma)
    params["sol_elev"] = sol_elev
    return build_vertical_cover(**params)


@pytest.fixture
def base_cover_params(mock_sun_data, mock_logger):
    """Return base parameters for AdaptiveVerticalCover (flat kwargs style)."""
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
        "distance": 0.5,  # 50cm glare zone
        "h_win": 2.1,  # 2.1m window height
    }  # These flat kwargs are routed by build_vertical_cover() to typed configs


class TestSafetyMarginCalculatorValues:
    """Test SafetyMarginCalculator's angle-dependent multiplier values.

    The vertical axis retired this multiplier from its output path (#1173:
    it was sign-inverted there, increasing sun penetration instead of
    reducing it). ``SafetyMarginCalculator`` itself is unchanged and still
    correctly consumed by the tilt axis (`tilt.py`, #783/#1089), so these
    tests call the calculator directly rather than through
    ``AdaptiveVerticalCover`` — they pin the multiplier's *values*, not any
    vertical-axis behaviour.
    """

    def test_safety_margin_normal_angles_returns_baseline(self, base_cover_params):
        """Safety margin should be 1.0 for normal angles."""
        # gamma = (win_azi - sol_azi + 180) % 360 - 180
        # For gamma=0: sol_azi = win_azi
        base_cover_params["sol_azi"] = 180.0  # Same as win_azi
        base_cover_params["sol_elev"] = 45.0
        cover = build_vertical_cover(**base_cover_params)

        margin = SafetyMarginCalculator.calculate(cover.gamma, cover.sol_elev)
        assert margin == 1.0

    def test_safety_margin_moderate_gamma_returns_baseline(self, base_cover_params):
        """Safety margin should be 1.0 for gamma <= 45°."""
        for gamma in [0, 15, 30, 45]:
            margin = SafetyMarginCalculator.calculate(gamma, 45.0)
            assert margin == 1.0, f"Expected 1.0 at gamma={gamma}, got {margin}"

    def test_safety_margin_extreme_gamma_increases(self, base_cover_params):
        """Safety margin should increase at extreme gamma angles."""
        # Test progressive increase
        margin_60 = SafetyMarginCalculator.calculate(60.0, 45.0)
        margin_75 = SafetyMarginCalculator.calculate(75.0, 45.0)
        margin_90 = SafetyMarginCalculator.calculate(90.0, 45.0)

        assert 1.0 < margin_60 < margin_75 < margin_90
        assert margin_90 <= 1.2  # Max 20% increase

    def test_safety_margin_low_elevation_increases(self, base_cover_params):
        """Safety margin should increase at low elevations."""
        # Test progressive increase as elevation decreases
        margin_10 = SafetyMarginCalculator.calculate(0.0, 10.0)
        margin_5 = SafetyMarginCalculator.calculate(0.0, 5.0)
        margin_2 = SafetyMarginCalculator.calculate(0.0, 2.0)

        assert margin_10 == 1.0  # Threshold
        assert 1.0 < margin_5 < margin_2
        assert margin_2 <= 1.15  # Max 15% increase

    def test_safety_margin_high_elevation_increases(self, base_cover_params):
        """Safety margin should increase at high elevations."""
        # Test progressive increase as elevation increases
        margin_75 = SafetyMarginCalculator.calculate(0.0, 75.0)
        margin_82 = SafetyMarginCalculator.calculate(0.0, 82.5)
        margin_90 = SafetyMarginCalculator.calculate(0.0, 90.0)

        assert margin_75 == 1.0  # Threshold
        assert 1.0 < margin_82 < margin_90
        assert margin_90 <= 1.1  # Max 10% increase

    def test_safety_margin_combined_extremes(self, base_cover_params):
        """Safety margin should combine gamma and elevation effects."""
        # Extreme gamma + low elevation
        margin = SafetyMarginCalculator.calculate(85.0, 5.0)
        assert 1.2 < margin <= 1.35  # ~20% + ~7.5% combined

        # Extreme gamma + high elevation
        margin = SafetyMarginCalculator.calculate(85.0, 85.0)
        assert 1.2 < margin <= 1.30  # ~20% + ~6.7% combined

    def test_safety_margin_symmetric_gamma(self, base_cover_params):
        """Safety margin should be symmetric for positive/negative gamma."""
        margin_pos = SafetyMarginCalculator.calculate(70.0, 45.0)
        margin_neg = SafetyMarginCalculator.calculate(-70.0, 45.0)

        assert margin_pos == margin_neg

    def test_safety_margin_smoothstep_interpolation(self, base_cover_params):
        """Safety margin should use smooth interpolation (no sharp transitions)."""
        # Test smooth transition in gamma range
        margins = [
            SafetyMarginCalculator.calculate(gamma, 45.0) for gamma in range(45, 91, 5)
        ]

        # Check monotonic increase
        for i in range(len(margins) - 1):
            assert margins[i] <= margins[i + 1]

        # Check smooth (no large jumps)
        diffs = [margins[i + 1] - margins[i] for i in range(len(margins) - 1)]
        max_diff = max(diffs)
        assert max_diff < 0.05  # No jump > 5%


class TestVerticalSafetyMarginRetirement:
    """Direction/no-op guards for the vertical safety margin fix (#1173).

    ``TestSafetyMarginCalculatorValues`` above tests the multiplier's
    *values*; these tests exercise its *effect* on ``calculate_position()``
    — the assertion that would have caught the #1173 inversion when it
    shipped (evidence packet TEST_GAP #3: "Eight tests assert ... None
    asserts that applying the margin makes the cover shade more").
    """

    def test_vertical_margin_never_increases_penetration(self, base_cover_params):
        """The vertical margin must never let more sun penetrate past the
        configured ``distance`` than the base projection alone allows
        (#1173) — a direction guard the 216-case contract grid never had.

        ``h_win=8.0`` is deliberately taller than the grid's usual 0.62/2.2:
        at ``h_win=2.2`` the base projection alone already saturates to
        ``h_win`` once ``elev > 75``, which masks the high-elevation branch
        of the margin behind clipping (clipping only ever *reduces*
        penetration, so it can hide but never cause a violation). A taller
        window keeps that branch observable so this sweep actually exercises
        both elevation thresholds (``elev < 10`` and ``elev > 75``), not just
        the gamma one — see TEST_GAP #2 in the #1173 evidence packet.
        """
        params = base_cover_params.copy()
        params["distance"] = 1.5
        params["h_win"] = 8.0

        for gamma in range(-89, 90):
            for elev in (2.1, *range(3, 90)):
                cover = make_cover_with_angles(
                    params, gamma=float(gamma), sol_elev=float(elev)
                )
                position = cover.calculate_position()
                penetration = _lintel_gate_max_penetration(
                    position, params["h_win"], 0.0, gamma, elev
                )
                assert penetration <= params["distance"] + 1e-9, (
                    f"gamma={gamma} elev={elev}: position={position:.4f} -> "
                    f"penetration={penetration:.4f} exceeds "
                    f"distance={params['distance']}"
                )

    def test_no_op_inside_the_normal_envelope(self, base_cover_params):
        """Inside |gamma| <= 45 deg and 10 <= elev <= 75 deg, calculate_position()
        must exactly match the un-margined base projection.

        Characterization test (passes both before and after this fix): the
        margin's own thresholds are gamma>45, elev<10 and elev>75, so it is
        already 1.0 throughout this envelope today, and after the fix it is
        1.0 everywhere. This pins the #783/#1089-style no-op guarantee to the
        vertical axis too, so a future change to either envelope — not this
        fix — is what would break it.
        """
        params = base_cover_params.copy()
        params["distance"] = 1.5
        params["h_win"] = 2.2

        for gamma in range(-45, 46):
            for elev in range(10, 76):
                cover = make_cover_with_angles(
                    params, gamma=float(gamma), sol_elev=float(elev)
                )
                base_height, *_ = cover._project_drop(cover.distance)
                expected = float(np.clip(base_height, 0, cover.h_win))
                actual = cover.calculate_position()
                assert actual == pytest.approx(expected), (
                    f"gamma={gamma} elev={elev}: expected no-op position "
                    f"{expected:.6f}, got {actual:.6f}"
                )


class TestEdgeCases:
    """Test edge case handling for extreme angles."""

    def test_edge_case_very_low_elevation(self, base_cover_params):
        """Very low elevation should fully cover (position 0 = closed)."""
        cover = make_cover_with_angles(base_cover_params, gamma=0.0, sol_elev=1.0)

        is_edge_case, position = cover._handle_edge_cases()

        assert is_edge_case is True
        assert position == 0.0

    def test_edge_case_elevation_threshold(self, base_cover_params):
        """Edge case should trigger below 2° elevation."""
        # Well below threshold
        cover = make_cover_with_angles(base_cover_params, gamma=0.0, sol_elev=1.0)
        is_edge_case, _ = cover._handle_edge_cases()
        assert is_edge_case is True

        # Well above threshold
        cover = make_cover_with_angles(base_cover_params, gamma=0.0, sol_elev=5.0)
        is_edge_case, _ = cover._handle_edge_cases()
        assert is_edge_case is False

    def test_extreme_gamma_no_longer_edge_case(self, base_cover_params):
        """Issue #600: extreme gamma is handled by the normal clamped projection.

        The former |gamma|>85° full-close branch was removed; the projection's
        cos(gamma) clamp keeps the result finite and bounded without it.
        """
        for gamma in (86.0, 89.0, -86.0, -89.0):
            cover = make_cover_with_angles(
                base_cover_params, gamma=gamma, sol_elev=45.0
            )
            is_edge_case, _ = cover._handle_edge_cases()
            assert is_edge_case is False, f"gamma={gamma} should not be an edge case"
            assert 0.0 <= cover.calculate_percentage() <= 100.0

    def test_extreme_gamma_high_elevation_not_full_close(self, base_cover_params):
        """Issue #598/#600: extreme gamma + high sun is open, not slammed closed."""
        cover = make_cover_with_angles(base_cover_params, gamma=88.0, sol_elev=70.0)
        is_edge_case, _ = cover._handle_edge_cases()
        assert is_edge_case is False
        assert cover.calculate_percentage() > 50

    def test_extreme_gamma_low_elevation_not_edge_case(self, base_cover_params):
        """Issue #600: grazing extreme gamma above the 2° floor uses the normal path.

        Below the 2° floor the low-sun guard still closes — see
        test_edge_case_very_low_elevation.
        """
        cover = make_cover_with_angles(base_cover_params, gamma=88.0, sol_elev=20.0)
        is_edge_case, _ = cover._handle_edge_cases()
        assert is_edge_case is False

    def test_fov_entry_no_spurious_close(self, base_cover_params):
        """Issue #598 regression: no 0→open jump across the 85° FOV edge at high sun.

        Reproduces the side-yard-shade V-notch: a sample just inside the former
        extreme-gamma band (86°) at high elevation stays open and matches the
        sample just outside it (84°), rather than slamming to fully closed.
        """
        just_inside = make_cover_with_angles(
            base_cover_params, gamma=86.0, sol_elev=70.0
        )
        just_outside = make_cover_with_angles(
            base_cover_params, gamma=84.0, sol_elev=70.0
        )
        pct_inside = just_inside.calculate_percentage()
        pct_outside = just_outside.calculate_percentage()
        # Pre-fix the inside sample returned 0 (spurious full-close).
        assert pct_inside > 50, f"FOV-entry sample slammed closed: {pct_inside}%"
        assert abs(pct_inside - pct_outside) <= 5

    def test_very_high_elevation_no_longer_edge_case(self, base_cover_params):
        """Issue #600: near-overhead sun is handled by the normal projection.

        The former >88° simplified branch was redundant — the normal path
        saturates to h_win identically — and was removed.
        """
        for sol_elev in (88.5, 89.0, 89.9):
            cover = make_cover_with_angles(
                base_cover_params, gamma=0.0, sol_elev=sol_elev
            )
            is_edge_case, _ = cover._handle_edge_cases()
            assert is_edge_case is False
            assert 0.0 <= cover.calculate_percentage() <= 100.0

    def test_pole_regions_finite_and_bounded(self, base_cover_params):
        """Issue #600: the self-guarding projection never returns NaN/out-of-range.

        Sweeps the trig-pole regions the removed edge cases used to short-circuit
        (elevation → 0/90, |gamma| → 90) and asserts every result is a finite
        percentage in [0, 100]. The only forced full-close is the sub-2° floor.
        """
        for sol_elev in (0.1, 1.0, 2.0, 5.0, 45.0, 85.0, 88.0, 89.9):
            for gamma in (0.0, 45.0, 80.0, 85.0, 89.0, 89.9, -89.0):
                cover = make_cover_with_angles(
                    base_cover_params, gamma=gamma, sol_elev=sol_elev
                )
                pct = cover.calculate_percentage()
                assert np.isfinite(pct), f"non-finite at gamma={gamma}, elev={sol_elev}"
                assert 0.0 <= pct <= 100.0
                is_edge_case, _ = cover._handle_edge_cases()
                assert is_edge_case is (sol_elev < 2.0)

    @pytest.mark.parametrize(
        "gamma,sol_elev",
        [
            (0.0, 45.0),  # Direct front, mid elevation
            (30.0, 30.0),  # Moderate angle
            (60.0, 60.0),  # Higher angle (below 85° gamma threshold)
            (-45.0, 15.0),  # Negative gamma
            (45.0, 45.0),  # 45 degree angle
        ],
    )
    def test_edge_case_normal_angles_returns_false(
        self, base_cover_params, gamma, sol_elev
    ):
        """Normal angles should not trigger edge case handling."""
        cover = make_cover_with_angles(
            base_cover_params, gamma=gamma, sol_elev=sol_elev
        )
        is_edge_case, _ = cover._handle_edge_cases()
        assert (
            is_edge_case is False
        ), f"False edge case at gamma={gamma}, elev={sol_elev}"

    def test_low_elevation_calc_percentage_is_fully_closed(self, base_cover_params):
        """Sub-2° sun must drive the blind CLOSED (≈0%), not open (100%) — issue #559."""
        cover = make_cover_with_angles(base_cover_params, gamma=24.6, sol_elev=0.6)
        pct = cover.calculate_percentage()
        assert pct <= 1, f"low-sun edge case should be ≈0% (closed), got {pct}%"


class TestEnhancedCalculatePosition:
    """Test the enhanced calculate_position method."""

    def test_calculate_position_uses_edge_case_handling(self, base_cover_params):
        """calculate_position should use edge case handling — position 0 = fully closed."""
        cover = make_cover_with_angles(
            base_cover_params, gamma=0.0, sol_elev=1.5
        )  # Triggers edge case

        position = cover.calculate_position()

        # Should return full coverage (position 0 = closed)
        assert position == 0.0

    def test_calculate_position_applies_safety_margin(self, base_cover_params):
        """calculate_position should apply safety margins at extreme angles."""
        # Create two covers with different gamma angles
        cover_normal = make_cover_with_angles(
            base_cover_params, gamma=0.0, sol_elev=45.0
        )  # No margin
        cover_extreme = make_cover_with_angles(
            base_cover_params, gamma=70.0, sol_elev=45.0
        )  # Margin applied

        pos_normal = cover_normal.calculate_position()
        pos_extreme = cover_extreme.calculate_position()

        # Extreme angle should have higher position (but not capped at h_win)
        assert pos_extreme > pos_normal, f"Expected {pos_extreme} > {pos_normal}"

    def test_calculate_position_clips_to_window_height(self, base_cover_params):
        """calculate_position should never exceed window height."""
        # Test various angles that might cause overflow
        test_cases = [
            (0.0, 89.0),  # Near vertical
            (10.0, 85.0),  # High elevation
            (80.0, 70.0),  # Extreme gamma with safety margin
        ]

        for gamma, sol_elev in test_cases:
            cover = make_cover_with_angles(
                base_cover_params, gamma=gamma, sol_elev=sol_elev
            )
            position = cover.calculate_position()
            assert (
                position <= cover.h_win
            ), f"Exceeded h_win at gamma={gamma}, elev={sol_elev}"

    def test_calculate_position_never_negative(self, base_cover_params):
        """calculate_position should never return negative values."""
        # Test various angles
        test_cases = [
            (0.0, 5.0),
            (45.0, 10.0),
            (70.0, 15.0),
            (-60.0, 20.0),
        ]

        for gamma, sol_elev in test_cases:
            cover = make_cover_with_angles(
                base_cover_params, gamma=gamma, sol_elev=sol_elev
            )
            position = cover.calculate_position()
            assert position >= 0, f"Negative position at gamma={gamma}, elev={sol_elev}"

    def test_calculate_position_no_nan_or_inf(self, base_cover_params):
        """calculate_position should never return NaN or infinity."""
        # Test wide range of angles including extremes
        for gamma in range(-90, 91, 10):
            for sol_elev in range(0, 91, 10):
                cover = make_cover_with_angles(
                    base_cover_params, gamma=float(gamma), sol_elev=float(sol_elev)
                )
                position = cover.calculate_position()

                assert not np.isnan(position), f"NaN at gamma={gamma}, elev={sol_elev}"
                assert not np.isinf(position), f"Inf at gamma={gamma}, elev={sol_elev}"


class TestRegressionNormalAngles:
    """Test that normal angles show minimal change from baseline behavior."""

    def _baseline_calculation(self, distance, gamma, sol_elev, h_win):
        """Original calculation logic (without enhancements)."""
        from numpy import cos, tan
        from numpy import radians as rad

        blind_height = np.clip(
            (distance / cos(rad(gamma))) * tan(rad(sol_elev)),
            0,
            h_win,
        )
        return blind_height

    def test_regression_normal_angles_within_tolerance(self, base_cover_params):
        """Normal angles should show <5% deviation from baseline."""
        # Test "normal" operating range
        normal_test_cases = [
            (0.0, 30.0),  # Direct front, low-mid elevation
            (0.0, 45.0),  # Direct front, mid elevation
            (0.0, 60.0),  # Direct front, high elevation
            (15.0, 45.0),  # Slight angle
            (30.0, 45.0),  # Moderate angle
            (45.0, 30.0),  # Threshold angle
            (-30.0, 45.0),  # Negative gamma
        ]

        for gamma, sol_elev in normal_test_cases:
            cover = make_cover_with_angles(
                base_cover_params, gamma=gamma, sol_elev=sol_elev
            )

            enhanced_pos = cover.calculate_position()
            baseline_pos = self._baseline_calculation(
                cover.distance, cover.gamma, cover.sol_elev, cover.h_win
            )

            # Calculate percent deviation
            if baseline_pos > 0:
                deviation = abs(enhanced_pos - baseline_pos) / baseline_pos * 100
                assert deviation < 5.0, (
                    f"Excessive deviation at gamma={gamma}, elev={sol_elev}: "
                    f"{deviation:.1f}% (enhanced={enhanced_pos:.3f}, baseline={baseline_pos:.3f})"
                )

    def test_regression_direct_front_matches_baseline(self, base_cover_params):
        """Direct front (gamma=0) should match baseline exactly at normal elevations."""
        # Only test elevations where no safety margins are applied
        for sol_elev in [30.0, 45.0, 60.0, 70.0]:
            cover = make_cover_with_angles(
                base_cover_params, gamma=0.0, sol_elev=sol_elev
            )

            enhanced_pos = cover.calculate_position()
            baseline_pos = self._baseline_calculation(
                cover.distance, cover.gamma, cover.sol_elev, cover.h_win
            )

            # Should match within floating point precision
            assert (
                abs(enhanced_pos - baseline_pos) < 1e-6
            ), f"Mismatch at gamma=0, elev={sol_elev}: enhanced={enhanced_pos:.6f}, baseline={baseline_pos:.6f}"

    def test_regression_extreme_angles_conservative(self, base_cover_params):
        """Extreme angles should be conservative (≥ baseline)."""
        # Test extreme angles (where safety margins apply)
        extreme_test_cases = [
            (60.0, 45.0),  # High gamma
            (75.0, 45.0),  # Very high gamma
            (0.0, 8.0),  # Low elevation (with margin)
            (45.0, 8.0),  # Combined moderate gamma + low elevation
        ]

        for gamma, sol_elev in extreme_test_cases:
            cover = make_cover_with_angles(
                base_cover_params, gamma=gamma, sol_elev=sol_elev
            )

            enhanced_pos = cover.calculate_position()
            baseline_pos = self._baseline_calculation(
                cover.distance, cover.gamma, cover.sol_elev, cover.h_win
            )

            # Enhanced should be >= baseline (more conservative)
            assert enhanced_pos >= baseline_pos - 1e-6, (
                f"Less conservative at gamma={gamma}, elev={sol_elev}: "
                f"enhanced={enhanced_pos:.3f}, baseline={baseline_pos:.3f}"
            )


class TestWindowDepth:
    """Test window depth parameter functionality."""

    def test_window_depth_default_zero(self, base_cover_params):
        """Window depth should default to 0 (disabled)."""
        cover = make_cover_with_angles(base_cover_params, gamma=0.0, sol_elev=45.0)
        assert cover.window_depth == 0.0

    def test_window_depth_disabled_matches_baseline(self, base_cover_params):
        """Window depth=0 should match baseline behavior exactly."""
        # Configure with window_depth explicitly set to 0
        cover_no_depth = make_cover_with_angles(
            base_cover_params, gamma=30.0, sol_elev=45.0
        )

        # Configure with window_depth parameter
        params_with_depth = base_cover_params.copy()
        params_with_depth["window_depth"] = 0.0
        cover_with_zero_depth = build_vertical_cover(**params_with_depth)
        cover_with_zero_depth.sol_azi = gamma_to_sol_azi(
            cover_with_zero_depth.config.win_azi, 30.0
        )
        cover_with_zero_depth.sol_elev = 45.0

        pos_no_depth = cover_no_depth.calculate_position()
        pos_zero_depth = cover_with_zero_depth.calculate_position()

        assert abs(pos_no_depth - pos_zero_depth) < 1e-10

    def test_window_depth_realistic_values(self, base_cover_params):
        """Test with realistic window depth values."""
        test_depths = [
            (0.05, "flush mount"),
            (0.10, "standard frame"),
            (0.15, "deep reveal"),
        ]

        params = base_cover_params.copy()
        for depth, description in test_depths:
            params["window_depth"] = depth
            cover = build_vertical_cover(**params)
            cover.sol_azi = gamma_to_sol_azi(cover.config.win_azi, 45.0)
            cover.sol_elev = 45.0

            position = cover.calculate_position()

            # All should be valid positions
            assert (
                0 <= position <= cover.h_win
            ), f"Invalid position for {description}: {position}"

    def test_window_depth_backward_compatibility(self, base_cover_params):
        """Cover without window_depth parameter should work (backward compatibility)."""
        # Create cover without window_depth parameter (old code style)
        cover = build_vertical_cover(**base_cover_params)

        # Should work and use default
        assert cover.window_depth == 0.0
        position = cover.calculate_position()
        assert 0 <= position <= cover.h_win

    def test_window_depth_large_value_clipped(self, base_cover_params):
        """window_depth=5.0m (new max) must produce a finite position clipped to [0, h_win]."""
        params = dict(base_cover_params)
        params["window_depth"] = 5.0
        cover = build_vertical_cover(**params)
        position = cover.calculate_position()
        assert 0 <= position <= cover.h_win
        assert not (position != position)  # not NaN


class TestLintelGate:
    """Window depth is a binary full-open gate, not a continuous term (#1169).

    ``position`` is exposed glass measured up from the sill; ``window_depth``
    is a reveal that shadows the TOP of the glass — the same band the blind
    already covers from the top. That shadow can never license a *partial*
    opening (it never protects territory the blind wasn't already covering),
    only a *full* one, once the reveal shadow plus the blind's own coverage
    together span the whole pane. See ``vertical.py``'s lintel-gate comment
    for the derivation.
    """

    def test_depth_never_opens_glass_below_the_lintel_gate(self, base_cover_params):
        """The user asked for zero sun in the room; depth must not grant any.

        distance=0 means "no direct sun past the glass plane" — window_depth
        must never override that by opening the blind from the bottom, where
        the reveal shadow (which falls on the TOP of the glass) shades nothing.
        """
        params = base_cover_params.copy()
        params["distance"] = 0.0
        params["h_win"] = 2.2
        params["window_depth"] = 0.18
        cover = make_cover_with_angles(params, gamma=30.0, sol_elev=45.0)

        assert cover.calculate_position() == 0.0

    def test_lintel_gate_opens_fully_when_shadow_completes_coverage(
        self, base_cover_params
    ):
        """When the reveal shadow alone already covers the whole pane, open fully.

        Fires here at gamma=0°, where the old 10° gamma gate used to suppress
        the depth term entirely — this is a genuine 0-gamma full-open case the
        pre-#1169 code could never reach.
        """
        params = base_cover_params.copy()
        params["distance"] = 0.2
        params["h_win"] = 0.62
        params["window_depth"] = 0.18
        cover = make_cover_with_angles(params, gamma=0.0, sol_elev=60.0)

        assert cover.calculate_position() == pytest.approx(0.62)

    def test_lintel_shadow_is_foreshortened_by_gamma(self, base_cover_params):
        """The reveal depth is measured along the window normal, not the sun's
        bearing, so the sun's run through it is depth/cos(gamma) — the lintel
        shadow is ``depth * tan(elev) / cos(gamma)``, not ``depth * tan(elev)``.

        DISCRIMINATOR: a ``depth * tan(elev)`` variant (dropping the
        ``/cos(gamma)`` foreshortening — the reporter's original, numerically
        wrong proposal) computes a lintel shadow of only 0.5 m here, leaves the
        gate unfired (base 0.7071 + 0.5 = 1.2071 < h_win 1.35), and falls
        through to the normal path returning ~0.7071 instead of the correct
        fully-open 1.35. The pre-#1169 code (the old ``depth * sin(|gamma|)``
        permission-budget term) also misses the gate here, projecting a
        shadow-inflated ~1.2071. Both wrong answers happen to differ from the
        correct 1.35, so this test is a genuine discriminator against either
        regression — unlike the old params, which the pre-#1169 formula also
        passed (it returned 1.20 there, same as the correct answer; only the
        ``depth * tan(elev)`` variant failed, at ~0.9871). If this test starts
        failing after a "simplification" that
        removes the ``/cos(gamma)`` term, that removal is the regression.
        """
        params = base_cover_params.copy()
        params["distance"] = 0.5
        params["h_win"] = 1.35
        params["window_depth"] = 0.5
        cover = make_cover_with_angles(params, gamma=45.0, sol_elev=45.0)

        assert cover.calculate_position() == pytest.approx(1.35)

    def test_depth_has_no_effect_below_the_gate(self, base_cover_params):
        """Below the full-open threshold, window_depth must be a pure no-op —
        bit-identical to window_depth=0, not merely "close enough".
        """
        params_with_depth = base_cover_params.copy()
        params_with_depth["distance"] = 0.5
        params_with_depth["h_win"] = 2.1
        params_with_depth["window_depth"] = 0.10
        cover_with_depth = make_cover_with_angles(
            params_with_depth, gamma=45.0, sol_elev=45.0
        )

        params_no_depth = params_with_depth.copy()
        params_no_depth["window_depth"] = 0.0
        cover_no_depth = make_cover_with_angles(
            params_no_depth, gamma=45.0, sol_elev=45.0
        )

        assert (
            cover_with_depth.calculate_position() == cover_no_depth.calculate_position()
        )


def _lintel_gate_max_penetration(
    position: float, h_win: float, depth: float, gamma: float, elev: float
) -> float:
    """Upper bound on in-room sun penetration a returned ``position`` can produce.

    Mirrors the ``distance_shaded_area`` contract ("Zero keeps direct sun from
    passing the glass plane"): the highest lit point on the glass is capped
    both by the blind's own coverage (``position``) and by the physical limit
    of the lintel shadow (``h_win − depth·f``, the highest point the reveal can
    ever leave lit, regardless of blind position) — whichever is lower wins.
    """
    f = math.tan(math.radians(elev)) / math.cos(math.radians(gamma))
    lit_ceiling = max(h_win - depth * f, 0.0)
    z = min(position, lit_ceiling)
    return z * math.cos(math.radians(gamma)) / math.tan(math.radians(elev))


@pytest.mark.parametrize(
    "distance,h_win,depth,gamma,elev",
    list(
        itertools.product(
            [0.0, 0.5, 1.5],
            [0.62, 2.2],
            [0.05, 0.18, 0.5],
            [0, 30, 60, 75],
            [20, 45, 75],
        )
    ),
)
def test_no_direct_sun_lands_past_the_configured_distance(
    base_cover_params, distance, h_win, depth, gamma, elev
):
    """Contract (#1169): no position the engine returns may leak direct sun past
    the user's configured ``distance``.

    This is the missing contract test — ``distance_shaded_area`` promises
    "Zero (0 m) keeps direct sun from passing the glass plane", generalised to
    any ``distance``. Fails on ~33% of these configs under the pre-#1169
    continuous ``depth_contribution`` term, which opens the blind from the
    BOTTOM — where the lintel shadow (which falls on the TOP of the glass)
    protects nothing.
    """
    params = base_cover_params.copy()
    params["distance"] = distance
    params["h_win"] = h_win
    params["window_depth"] = depth
    cover = make_cover_with_angles(params, gamma=gamma, sol_elev=elev)
    position = cover.calculate_position()

    penetration = _lintel_gate_max_penetration(position, h_win, depth, gamma, elev)

    assert penetration <= distance + 1e-9, (
        f"distance={distance} h_win={h_win} depth={depth} gamma={gamma} "
        f"elev={elev}: position={position:.4f} -> penetration={penetration:.4f} "
        "exceeds the configured distance"
    )


class TestSmoothTransitions:
    """Test that transitions are smooth across angle ranges."""

    def test_smooth_transition_across_gamma_threshold(self, base_cover_params):
        """Position should transition smoothly across gamma=45° threshold."""
        # Test positions around threshold
        positions = []
        for gamma in range(40, 51, 1):
            cover = make_cover_with_angles(
                base_cover_params, gamma=float(gamma), sol_elev=45.0
            )
            positions.append(cover.calculate_position())

        # Check no large jumps
        diffs = [positions[i + 1] - positions[i] for i in range(len(positions) - 1)]
        max_jump = max(abs(d) for d in diffs)

        # Maximum jump should be reasonable (not a discontinuity)
        assert max_jump < 0.05, f"Large jump detected: {max_jump:.3f}m"

    def test_smooth_transition_across_elevation_thresholds(self, base_cover_params):
        """Position should transition smoothly across elevation thresholds."""
        # Test around 10° threshold
        positions_low = []
        for elev in range(5, 16, 1):
            cover = make_cover_with_angles(
                base_cover_params, gamma=0.0, sol_elev=float(elev)
            )
            positions_low.append(cover.calculate_position())

        # Test around 75° threshold
        positions_high = []
        for elev in range(70, 81, 1):
            cover = make_cover_with_angles(
                base_cover_params, gamma=0.0, sol_elev=float(elev)
            )
            positions_high.append(cover.calculate_position())

        for positions, threshold in [(positions_low, 10), (positions_high, 75)]:
            diffs = [positions[i + 1] - positions[i] for i in range(len(positions) - 1)]
            max_jump = max(abs(d) for d in diffs)
            # High elevations naturally have larger changes per degree
            max_allowed = 0.20 if threshold == 75 else 0.15
            assert (
                max_jump < max_allowed
            ), f"Large jump near {threshold}° threshold: {max_jump:.3f}m"

    def test_monotonic_increase_with_elevation(self, base_cover_params):
        """Position should increase monotonically with elevation (at constant gamma)."""
        positions = []
        for elev in range(10, 81, 5):
            cover = make_cover_with_angles(
                base_cover_params, gamma=30.0, sol_elev=float(elev)
            )
            positions.append(cover.calculate_position())

        # Check monotonic increase
        for i in range(len(positions) - 1):
            assert (
                positions[i] <= positions[i + 1]
            ), f"Non-monotonic at elevation {10 + i * 5}°"
