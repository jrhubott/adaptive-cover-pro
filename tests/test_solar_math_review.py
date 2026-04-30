"""Tests for solar math review findings.

Covers gaps identified in the 2026-04-12 solar code review:
- Horizontal awning division-by-zero guard (sin_c < 1e-6)
- Tilt cover negative discriminant
- solar_times() method
- Non-zero sill_height
- valid_elevation with min-only / max-only limits
- Exact FOV boundary (gamma == fov_left)
- control_state_reason missing branches
- effective_distance_override (non-None)
- Gamma docstring sign convention
- Sill height effective_distance floor
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, UTC
from unittest.mock import MagicMock, PropertyMock, patch

import pandas as pd
import pytest

from custom_components.adaptive_cover_pro.engine.covers.vertical import (
    AdaptiveVerticalCover,
)
from custom_components.adaptive_cover_pro.engine.sun_geometry import SunGeometry
from tests.cover_helpers import (
    build_horizontal_cover,
    build_tilt_cover,
    build_vertical_cover,
    make_cover_config,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _common_kwargs(
    mock_sun_data, mock_logger, *, sol_elev: float = 45.0, sol_azi: float = 180.0
):
    """Return base kwargs for building cover instances. sol_elev and sol_azi can be overridden."""
    return {
        "logger": mock_logger,
        "sol_azi": sol_azi,
        "sol_elev": sol_elev,
        "sun_data": mock_sun_data,
    }


def _make_sun_geometry(
    sol_azi: float,
    sol_elev: float = 45.0,
    win_azi: int = 180,
    fov_left: int = 45,
    fov_right: int = 45,
    *,
    min_elevation=None,
    max_elevation=None,
    blind_spot_on=False,
    blind_spot_left=None,
    blind_spot_right=None,
    blind_spot_elevation=None,
    sunset_off=0,
    sunrise_off=0,
    sun_data=None,
):
    """Build a SunGeometry with a minimal mock SunData."""
    if sun_data is None:
        sun_data = MagicMock()
        sun_data.timezone = "UTC"
        now_utc = datetime.now(UTC).replace(tzinfo=None)
        sun_data.sunset.return_value = (now_utc + timedelta(hours=12)).replace(
            tzinfo=None
        )
        sun_data.sunrise.return_value = (now_utc - timedelta(hours=6)).replace(
            tzinfo=None
        )

    config = make_cover_config(
        win_azi=win_azi,
        fov_left=fov_left,
        fov_right=fov_right,
        min_elevation=min_elevation,
        max_elevation=max_elevation,
        blind_spot_on=blind_spot_on,
        blind_spot_left=blind_spot_left,
        blind_spot_right=blind_spot_right,
        blind_spot_elevation=blind_spot_elevation,
        sunset_off=sunset_off,
        sunrise_off=sunrise_off,
    )
    return SunGeometry(
        sol_azi=sol_azi,
        sol_elev=sol_elev,
        sun_data=sun_data,
        config=config,
        logger=MagicMock(),
    )


def _make_full_day_sun_data(
    azimuth: float = 180.0, elevation: float = 30.0
) -> MagicMock:
    """Build a mock SunData where the sun is at constant azimuth/elevation all day.

    sunrise and sunset are placed at the very start and end of the time grid so
    in_sun_window covers the entire day regardless of real-world clock.
    """
    today = date.today()
    times = pd.date_range(
        start=pd.Timestamp(today),
        end=pd.Timestamp(today) + pd.Timedelta(days=1),
        freq="5min",
        tz="UTC",
    )
    n = len(times)

    sun_data = MagicMock()
    sun_data.times = times
    sun_data.solar_azimuth = [azimuth] * n
    sun_data.solar_elevation = [elevation] * n

    # Sunrise/sunset at grid edges so every time point is inside the sun window
    grid_start = times[0].replace(tzinfo=None)
    grid_end = times[-1].replace(tzinfo=None)
    sun_data.sunrise.return_value = grid_start
    sun_data.sunset.return_value = grid_end
    return sun_data


# ===========================================================================
# 1. Gamma sign convention
# ===========================================================================


class TestGammaSignConvention:
    """Verify sign convention: positive gamma = sun to the LEFT of window normal."""

    @pytest.mark.unit
    def test_gamma_positive_means_sun_left_of_normal(self):
        """When sol_azi < win_azi, gamma should be positive (sun to the left)."""
        # Window faces south (180°). Sun at 135° is 45° clockwise from south,
        # which is to the LEFT looking out from the window.
        sg = _make_sun_geometry(sol_azi=135.0, win_azi=180)
        # gamma = (180 - 135 + 180) % 360 - 180 = 225 - 180 = 45
        assert sg.gamma == pytest.approx(45.0)

    @pytest.mark.unit
    def test_gamma_negative_means_sun_right_of_normal(self):
        """When sol_azi > win_azi, gamma should be negative (sun to the right)."""
        sg = _make_sun_geometry(sol_azi=225.0, win_azi=180)
        # gamma = (180 - 225 + 180) % 360 - 180 = 135 - 180 = -45
        assert sg.gamma == pytest.approx(-45.0)

    @pytest.mark.unit
    def test_gamma_zero_sun_directly_in_front(self):
        """Sun exactly in front of window gives gamma = 0."""
        sg = _make_sun_geometry(sol_azi=180.0, win_azi=180)
        assert sg.gamma == pytest.approx(0.0)

    @pytest.mark.unit
    def test_gamma_wraps_correctly_across_north(self):
        """North-facing window (0°/360°) with westward sun (315°) gives gamma = +45."""
        sg = _make_sun_geometry(sol_azi=315.0, win_azi=0)
        # gamma = (0 - 315 + 180) % 360 - 180 = (-135 + 180) % 360 - 180 = 45 % 360 - 180 = 45
        assert sg.gamma == pytest.approx(45.0)


# ===========================================================================
# 2. valid_elevation with partial limits
# ===========================================================================


class TestValidElevationPartialLimits:
    """valid_elevation with only min or only max configured."""

    @pytest.mark.unit
    def test_min_elevation_only_accepts_at_or_above(self):
        """With only min_elevation set, sun at or above threshold is valid."""
        sg = _make_sun_geometry(sol_azi=180.0, sol_elev=20.0, min_elevation=15.0)
        assert sg.valid_elevation is True

    @pytest.mark.unit
    def test_min_elevation_only_rejects_below(self):
        """With only min_elevation set, sun below threshold is invalid."""
        sg = _make_sun_geometry(sol_azi=180.0, sol_elev=10.0, min_elevation=15.0)
        assert sg.valid_elevation is False

    @pytest.mark.unit
    def test_max_elevation_only_accepts_at_or_below(self):
        """With only max_elevation set, sun at or below threshold is valid."""
        sg = _make_sun_geometry(sol_azi=180.0, sol_elev=50.0, max_elevation=60.0)
        assert sg.valid_elevation is True

    @pytest.mark.unit
    def test_max_elevation_only_rejects_above(self):
        """With only max_elevation set, sun above threshold is invalid."""
        sg = _make_sun_geometry(sol_azi=180.0, sol_elev=70.0, max_elevation=60.0)
        assert sg.valid_elevation is False

    @pytest.mark.unit
    def test_no_limits_accepts_positive_elevation(self):
        """With no limits configured, any positive elevation is valid."""
        sg = _make_sun_geometry(sol_azi=180.0, sol_elev=1.0)
        assert sg.valid_elevation is True

    @pytest.mark.unit
    def test_no_limits_rejects_below_horizon(self):
        """With no limits configured, negative elevation (below horizon) is invalid."""
        sg = _make_sun_geometry(sol_azi=180.0, sol_elev=-5.0)
        assert sg.valid_elevation is False


# ===========================================================================
# 3. FOV boundary (gamma == fov_left / fov_right)
# ===========================================================================


class TestFOVBoundary:
    """Behavior at exact FOV boundary — the valid property uses strict inequalities."""

    @pytest.mark.unit
    def test_sun_at_exact_left_fov_boundary_is_outside(self):
        """Sun at gamma == fov_left (exact boundary) is NOT valid (strict <)."""
        # gamma = fov_left = 45; valid requires gamma < fov_left
        sg = _make_sun_geometry(sol_azi=135.0, win_azi=180, fov_left=45, fov_right=45)
        assert sg.gamma == pytest.approx(45.0)
        assert sg.valid is False

    @pytest.mark.unit
    def test_sun_one_degree_inside_left_fov_is_valid(self):
        """Sun just inside the left FOV (gamma = fov_left - 1) is valid."""
        sg = _make_sun_geometry(sol_azi=136.0, win_azi=180, fov_left=45, fov_right=45)
        assert sg.gamma == pytest.approx(44.0)
        assert sg.valid is True

    @pytest.mark.unit
    def test_sun_at_exact_right_fov_boundary_is_outside(self):
        """Sun at gamma == -fov_right (exact boundary) is NOT valid (strict >)."""
        sg = _make_sun_geometry(sol_azi=225.0, win_azi=180, fov_left=45, fov_right=45)
        assert sg.gamma == pytest.approx(-45.0)
        assert sg.valid is False

    @pytest.mark.unit
    def test_sun_one_degree_inside_right_fov_is_valid(self):
        """Sun just inside the right FOV (gamma = -(fov_right - 1)) is valid."""
        sg = _make_sun_geometry(sol_azi=224.0, win_azi=180, fov_left=45, fov_right=45)
        assert sg.gamma == pytest.approx(-44.0)
        assert sg.valid is True


# ===========================================================================
# 4. control_state_reason missing branches
# ===========================================================================


class TestControlStateReasonAllBranches:
    """All branches of control_state_reason including previously untested ones."""

    @pytest.mark.unit
    def test_reason_fov_exit(self, mock_sun_data, mock_logger):
        """Returns 'Default: FOV Exit' when sun is outside azimuth FOV (elevation valid)."""
        cover = build_vertical_cover(
            **_common_kwargs(mock_sun_data, mock_logger),
            h_win=2.0,
            distance=0.5,
        )
        with (
            patch.object(
                AdaptiveVerticalCover,
                "direct_sun_valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                AdaptiveVerticalCover,
                "sunset_valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                AdaptiveVerticalCover,
                "valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                AdaptiveVerticalCover,
                "valid_elevation",
                new_callable=PropertyMock,
                return_value=True,
            ),
        ):
            assert cover.control_state_reason == "Default: FOV Exit"

    @pytest.mark.unit
    def test_reason_elevation_limit(self, mock_sun_data, mock_logger):
        """Returns 'Default: Elevation Limit' when elevation is outside configured range."""
        cover = build_vertical_cover(
            **_common_kwargs(mock_sun_data, mock_logger),
            h_win=2.0,
            distance=0.5,
        )
        with (
            patch.object(
                AdaptiveVerticalCover,
                "direct_sun_valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                AdaptiveVerticalCover,
                "sunset_valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                AdaptiveVerticalCover,
                "valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                AdaptiveVerticalCover,
                "valid_elevation",
                new_callable=PropertyMock,
                return_value=False,
            ),
        ):
            assert cover.control_state_reason == "Default: Elevation Limit"

    @pytest.mark.unit
    def test_reason_blind_spot(self, mock_sun_data, mock_logger):
        """Returns 'Default: Blind Spot' when sun is within configured blind spot."""
        cover = build_vertical_cover(
            **_common_kwargs(mock_sun_data, mock_logger),
            h_win=2.0,
            distance=0.5,
        )
        with (
            patch.object(
                AdaptiveVerticalCover,
                "direct_sun_valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                AdaptiveVerticalCover,
                "sunset_valid",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                AdaptiveVerticalCover,
                "valid",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                AdaptiveVerticalCover,
                "is_sun_in_blind_spot",
                new_callable=PropertyMock,
                return_value=True,
            ),
        ):
            assert cover.control_state_reason == "Default: Blind Spot"


# ===========================================================================
# 5. Tilt cover negative discriminant
# ===========================================================================


class TestTiltNegativeDiscriminant:
    """Tilt calculation when slat geometry cannot block the sun (negative discriminant)."""

    @pytest.mark.unit
    def test_negative_discriminant_returns_zero(self, mock_sun_data, mock_logger):
        """Extremely wide/shallow slats produce negative discriminant → 0.0 (closed)."""
        # slat_distance=10, depth=0.01 → s/d=1000.
        # discriminant = tan(beta)^2 - 1000^2 + 1 << 0 for any physical beta.
        cover = build_tilt_cover(
            **_common_kwargs(mock_sun_data, mock_logger),
            slat_distance=10.0,
            depth=0.01,
            mode="mode1",
        )
        result = cover.calculate_position()
        assert result == pytest.approx(
            0.0
        ), f"Expected 0.0 (closed) for negative discriminant, got {result}"

    @pytest.mark.unit
    def test_negative_discriminant_percentage_returns_zero(
        self, mock_sun_data, mock_logger
    ):
        """calculate_percentage() returns 0 (closed) when discriminant is negative."""
        cover = build_tilt_cover(
            **_common_kwargs(mock_sun_data, mock_logger),
            slat_distance=10.0,
            depth=0.01,
            mode="mode1",
        )
        assert cover.calculate_percentage() == pytest.approx(0.0)

    @pytest.mark.unit
    def test_normal_slats_give_positive_discriminant(self, mock_sun_data, mock_logger):
        """Deep slats (s/d < 1) always have positive discriminant and produce nonzero tilt."""
        # slat_distance=0.02m, depth=0.05m → s/d=0.4
        # discriminant = tan(beta)^2 - 0.16 + 1 = tan(beta)^2 + 0.84 → always positive
        cover = build_tilt_cover(
            **_common_kwargs(mock_sun_data, mock_logger),
            slat_distance=0.02,
            depth=0.05,
            mode="mode1",
        )
        result = cover.calculate_position()
        assert 0.0 < result <= 90.0, f"Expected angle in (0, 90], got {result}"

    @pytest.mark.unit
    def test_discriminant_boundary_s_over_d_equals_one(
        self, mock_sun_data, mock_logger
    ):
        """With s/d = 1, discriminant = tan(beta)^2 which is non-negative for all beta."""
        # slat_distance=depth → s/d=1, discriminant = tan(beta)^2 - 1 + 1 = tan(beta)^2 >= 0
        cover = build_tilt_cover(
            **_common_kwargs(mock_sun_data, mock_logger),
            slat_distance=0.03,
            depth=0.03,
            mode="mode1",
        )
        result = cover.calculate_position()
        # At elevation=45, gamma=0: beta=45°, tan(beta)=1, discriminant=1 >= 0
        assert result >= 0.0


# ===========================================================================
# 6. Horizontal awning division-by-zero guard
# ===========================================================================


class TestHorizontalDivisionByZeroGuard:
    """Horizontal awning geometry.

    The formula is: c_angle = awn_angle_conf + sol_elev (derived from the law-of-sines
    triangle). c_angle is always in [0°, 180°] for physical inputs, so length is always
    non-negative and no clipping to zero occurs in normal operation.  The guard triggers
    when c_angle ≈ 0° (both angles near 0°) or ≈ 180° (both near 90°).
    """

    @pytest.mark.unit
    def test_normal_geometry_gives_positive_extension(self, mock_sun_data, mock_logger):
        """Normal geometry (c_angle > 0) gives a positive, clipped awning extension."""
        # awn_angle=45°, sol_elev=45°: c_angle = 90°, sin(90°) = 1 → well-conditioned
        cover = build_horizontal_cover(
            **_common_kwargs(mock_sun_data, mock_logger, sol_elev=45.0),
            h_win=2.0,
            distance=0.5,
            awn_length=3.0,
            awn_angle=45.0,
        )
        result = cover.calculate_position()
        assert 0.0 <= result <= 3.0  # clipped to awn_length

    @pytest.mark.unit
    def test_percentage_always_bounded(self, mock_sun_data, mock_logger):
        """calculate_percentage() result is always in [0, 100] for normal inputs."""
        cover = build_horizontal_cover(
            **_common_kwargs(mock_sun_data, mock_logger, sol_elev=45.0),
            h_win=2.0,
            distance=0.5,
            awn_length=3.0,
            awn_angle=30.0,
        )
        pct = cover.calculate_percentage()
        assert 0.0 <= pct <= 100.0

    @pytest.mark.unit
    def test_guard_returns_full_extension_when_sin_c_near_zero(
        self, mock_sun_data, mock_logger
    ):
        """When sin(c_angle) < 1e-6, the guard returns full awn_length (safe fallback)."""
        from unittest.mock import patch
        import numpy as np
        import custom_components.adaptive_cover_pro.engine.covers.horizontal as horiz_mod

        cover = build_horizontal_cover(
            **_common_kwargs(mock_sun_data, mock_logger, sol_elev=45.0),
            h_win=2.0,
            distance=0.5,
            awn_length=3.0,
            awn_angle=45.0,
        )
        # Patch sin to return near-zero regardless of argument, triggering the guard
        with patch.object(horiz_mod, "sin", return_value=np.float64(1e-8)):
            result = cover.calculate_position()

        assert result == pytest.approx(
            3.0
        ), f"Guard should return full extension 3.0m, got {result}"

    @pytest.mark.unit
    def test_extension_increases_with_larger_gap(self, mock_sun_data, mock_logger):
        """A larger uncovered gap (taller window) requires more awning extension."""
        # Taller window → larger gap when sun is high → more awning needed
        small_window = build_horizontal_cover(
            **_common_kwargs(mock_sun_data, mock_logger, sol_elev=60.0),
            h_win=1.0,
            distance=0.5,
            awn_length=5.0,
            awn_angle=60.0,
        )
        large_window = build_horizontal_cover(
            **_common_kwargs(mock_sun_data, mock_logger, sol_elev=60.0),
            h_win=2.5,
            distance=0.5,
            awn_length=5.0,
            awn_angle=60.0,
        )
        assert large_window.calculate_position() >= small_window.calculate_position()


# ===========================================================================
# 7. Non-zero sill_height
# ===========================================================================


class TestSillHeight:
    """Sill height parameter reduces required blind coverage."""

    @pytest.mark.unit
    def test_sill_height_zero_matches_default(self, mock_sun_data, mock_logger):
        """sill_height=0 produces the same result as no sill_height configured."""
        base = build_vertical_cover(
            **_common_kwargs(mock_sun_data, mock_logger),
            h_win=2.0,
            distance=1.0,
            sill_height=0.0,
        )
        no_sill = build_vertical_cover(
            **_common_kwargs(mock_sun_data, mock_logger),
            h_win=2.0,
            distance=1.0,
        )
        assert base.calculate_position() == pytest.approx(no_sill.calculate_position())

    @pytest.mark.unit
    def test_sill_height_reduces_required_coverage(self, mock_sun_data, mock_logger):
        """A raised sill reduces the effective distance, requiring less blind deployment."""
        no_sill = build_vertical_cover(
            **_common_kwargs(mock_sun_data, mock_logger, sol_elev=30.0),
            h_win=2.0,
            distance=1.0,
            sill_height=0.0,
        )
        with_sill = build_vertical_cover(
            **_common_kwargs(mock_sun_data, mock_logger, sol_elev=30.0),
            h_win=2.0,
            distance=1.0,
            sill_height=0.5,  # 50cm sill
        )
        assert with_sill.calculate_position() < no_sill.calculate_position()

    @pytest.mark.unit
    def test_large_sill_clamps_to_zero_not_negative(self, mock_sun_data, mock_logger):
        """A sill larger than effective distance clamps to 0 (sun can't reach floor)."""
        # sol_elev=30°: tan(30°)≈0.577; sill_offset = 3.0/0.577 ≈ 5.2m >> 1m distance
        cover = build_vertical_cover(
            **_common_kwargs(mock_sun_data, mock_logger, sol_elev=30.0),
            h_win=2.0,
            distance=1.0,
            sill_height=3.0,
        )
        result = cover.calculate_position()
        assert result == pytest.approx(0.0)
        assert result >= 0.0

    @pytest.mark.unit
    def test_sill_height_result_always_non_negative(self, mock_sun_data, mock_logger):
        """calculate_position() is never negative regardless of sill_height value."""
        for sill in [0.0, 0.5, 1.0, 2.0, 5.0]:
            cover = build_vertical_cover(
                **_common_kwargs(mock_sun_data, mock_logger, sol_elev=20.0),
                h_win=2.0,
                distance=0.5,
                sill_height=sill,
            )
            result = cover.calculate_position()
            assert result >= 0.0, f"Negative result at sill_height={sill}: {result}"


# ===========================================================================
# 8. effective_distance_override (GlareZoneHandler path)
# ===========================================================================


class TestEffectiveDistanceOverride:
    """Non-None effective_distance_override uses the supplied distance."""

    @pytest.mark.unit
    def test_override_larger_distance_gives_taller_shadow(
        self, mock_sun_data, mock_logger
    ):
        """Override with larger distance → taller shadow than using self.distance."""
        cover = build_vertical_cover(
            **_common_kwargs(mock_sun_data, mock_logger),
            h_win=2.0,
            distance=0.5,  # base distance
        )
        pos_base = cover.calculate_position()
        pos_override = cover.calculate_position(effective_distance_override=2.0)
        assert pos_override > pos_base, (
            f"Override 2.0m should be taller than base 0.5m: "
            f"base={pos_base:.3f}, override={pos_override:.3f}"
        )

    @pytest.mark.unit
    def test_override_smaller_distance_gives_shorter_shadow(
        self, mock_sun_data, mock_logger
    ):
        """Override with smaller distance → shorter shadow than using self.distance."""
        cover = build_vertical_cover(
            **_common_kwargs(mock_sun_data, mock_logger),
            h_win=2.0,
            distance=1.5,
        )
        pos_base = cover.calculate_position()
        pos_override = cover.calculate_position(effective_distance_override=0.2)
        assert pos_override < pos_base

    @pytest.mark.unit
    def test_override_none_equals_no_argument(self, mock_sun_data, mock_logger):
        """Explicit None override behaves the same as omitting the argument."""
        cover = build_vertical_cover(
            **_common_kwargs(mock_sun_data, mock_logger),
            h_win=2.0,
            distance=0.5,
        )
        assert cover.calculate_position(
            effective_distance_override=None
        ) == pytest.approx(cover.calculate_position())

    @pytest.mark.unit
    def test_override_source_recorded_as_glare_zone(self, mock_sun_data, mock_logger):
        """With an override, _last_calc_details records source as 'glare_zone'."""
        cover = build_vertical_cover(
            **_common_kwargs(mock_sun_data, mock_logger),
            h_win=2.0,
            distance=0.5,
        )
        cover.calculate_position(effective_distance_override=1.0)
        assert cover._last_calc_details["effective_distance_source"] == "glare_zone"

    @pytest.mark.unit
    def test_no_override_source_recorded_as_base(self, mock_sun_data, mock_logger):
        """Without an override, _last_calc_details records source as 'base'."""
        cover = build_vertical_cover(
            **_common_kwargs(mock_sun_data, mock_logger),
            h_win=2.0,
            distance=0.5,
        )
        cover.calculate_position()
        assert cover._last_calc_details["effective_distance_source"] == "base"


# ===========================================================================
# 9. solar_times() method
# ===========================================================================


class TestSolarTimes:
    """solar_times() returns the FOV entry/exit time window for the current day.

    All tests use _make_full_day_sun_data() which pins sunrise/sunset to the
    grid boundaries so the in_sun_window filter covers the entire day,
    making tests deterministic regardless of when they run.
    """

    @pytest.mark.unit
    def test_solar_times_returns_none_when_sun_never_in_fov(self):
        """Returns (None, None) when sun never enters the FOV today."""
        # Sun at azimuth 0 (north); window faces south (180) with 45° FOV → never valid
        sun_data = _make_full_day_sun_data(azimuth=0.0, elevation=30.0)
        config = make_cover_config(win_azi=180, fov_left=45, fov_right=45)
        sg = SunGeometry(
            sol_azi=0.0,
            sol_elev=30.0,
            sun_data=sun_data,
            config=config,
            logger=MagicMock(),
        )
        start, end = sg.solar_times()
        assert start is None
        assert end is None

    @pytest.mark.unit
    def test_solar_times_returns_datetimes_when_sun_in_fov(self):
        """Returns valid (start, end) datetimes when sun is in FOV all day."""
        # Sun always at azimuth 180 (center of FOV) with elevation 30° (above horizon)
        sun_data = _make_full_day_sun_data(azimuth=180.0, elevation=30.0)
        config = make_cover_config(win_azi=180, fov_left=45, fov_right=45)
        sg = SunGeometry(
            sol_azi=180.0,
            sol_elev=30.0,
            sun_data=sun_data,
            config=config,
            logger=MagicMock(),
        )
        start, end = sg.solar_times()
        assert start is not None
        assert end is not None
        assert isinstance(start, datetime)
        assert isinstance(end, datetime)
        assert start <= end

    @pytest.mark.unit
    def test_solar_times_excludes_below_horizon_points(self):
        """Elevation = 0 is excluded from solar_times (requires elev > 0)."""
        # Sun in FOV but at the horizon (elev=0) — valid_elev requires > 0
        sun_data = _make_full_day_sun_data(azimuth=180.0, elevation=0.0)
        config = make_cover_config(win_azi=180, fov_left=45, fov_right=45)
        sg = SunGeometry(
            sol_azi=180.0,
            sol_elev=0.0,
            sun_data=sun_data,
            config=config,
            logger=MagicMock(),
        )
        start, end = sg.solar_times()
        assert start is None
        assert end is None

    @pytest.mark.unit
    def test_solar_times_excludes_points_outside_fov_azimuth(self):
        """Azimuth just outside the FOV boundary is excluded."""
        # Window faces 180°, FOV ±45°, so valid range is 135–225°.
        # Sun at azimuth 226° is just outside the right edge.
        sun_data = _make_full_day_sun_data(azimuth=226.0, elevation=30.0)
        config = make_cover_config(win_azi=180, fov_left=45, fov_right=45)
        sg = SunGeometry(
            sol_azi=226.0,
            sol_elev=30.0,
            sun_data=sun_data,
            config=config,
            logger=MagicMock(),
        )
        start, end = sg.solar_times()
        assert start is None
        assert end is None

    @pytest.mark.unit
    def test_solar_times_with_min_elevation_constraint(self):
        """Times where elevation is below min_elevation are excluded."""
        # Sun at elevation 10°; min_elevation=15° → all points excluded
        sun_data = _make_full_day_sun_data(azimuth=180.0, elevation=10.0)
        config = make_cover_config(
            win_azi=180, fov_left=45, fov_right=45, min_elevation=15.0
        )
        sg = SunGeometry(
            sol_azi=180.0,
            sol_elev=10.0,
            sun_data=sun_data,
            config=config,
            logger=MagicMock(),
        )
        start, end = sg.solar_times()
        assert start is None
        assert end is None

    @pytest.mark.unit
    def test_solar_times_with_position_returns_azimuth_and_elevation(self):
        """solar_times_with_position returns (time, azimuth, elevation) tuples."""
        sun_data = _make_full_day_sun_data(azimuth=180.0, elevation=30.0)
        config = make_cover_config(win_azi=180, fov_left=45, fov_right=45)
        sg = SunGeometry(
            sol_azi=180.0,
            sol_elev=30.0,
            sun_data=sun_data,
            config=config,
            logger=MagicMock(),
        )
        start, end = sg.solar_times_with_position()
        assert start is not None
        assert end is not None
        s_time, s_azi, s_elev = start
        e_time, e_azi, e_elev = end
        assert isinstance(s_time, datetime)
        assert isinstance(e_time, datetime)
        assert s_azi == pytest.approx(180.0)
        assert e_azi == pytest.approx(180.0)
        assert s_elev == pytest.approx(30.0)
        assert e_elev == pytest.approx(30.0)
        assert s_time <= e_time

    @pytest.mark.unit
    def test_solar_times_with_position_returns_none_when_sun_never_in_fov(self):
        """solar_times_with_position returns (None, None) when sun never enters window."""
        sun_data = _make_full_day_sun_data(azimuth=0.0, elevation=30.0)
        config = make_cover_config(win_azi=180, fov_left=45, fov_right=45)
        sg = SunGeometry(
            sol_azi=0.0,
            sol_elev=30.0,
            sun_data=sun_data,
            config=config,
            logger=MagicMock(),
        )
        start, end = sg.solar_times_with_position()
        assert start is None
        assert end is None
