"""Tests for AdaptiveHorizontalCover calculations."""

import pytest

from tests.cover_helpers import build_horizontal_cover, make_daytime_sun_data

# ---------------------------------------------------------------------------
# Issue #1025 — corrected overhang projection + area/window shade modes.
#
# Reporter geometry (win_azi=142, h_win=2.10, awn_length=6.0, awn_angle=0,
# distance=0.099). ``build_horizontal_cover`` realises a requested surface-solar
# azimuth ``gamma`` by offsetting ``sol_azi`` from ``win_azi``:
#   gamma = (win_azi − sol_azi + 180) % 360 − 180  ⇒  sol_azi = win_azi − gamma.
#
# The window-mode overhang closed form (awn_angle=0) is
#   L = h_win · cos γ / tan α − d
# (the general awn_angle form divides the elevation profile by cos γ inside the
# denominator). Expected values below INCLUDE the ``− d`` term (d=0.099), per the
# approved formula `L = h_win / denom - self.distance` and the "awnings keep
# distance" design note.
# ---------------------------------------------------------------------------

_WIN_AZI_1025 = 142.0
_H_WIN_1025 = 2.10
_AWN_LENGTH_1025 = 6.0
_DISTANCE_1025 = 0.099


def _awning_1025(*, sol_elev, gamma, shade_mode=None, awn_angle=0.0, **overrides):
    """Build a reporter-geometry awning at a precise surface-solar azimuth."""
    kwargs = {
        "logger": __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock(),
        "sol_azi": _WIN_AZI_1025 - gamma,
        "sol_elev": sol_elev,
        "sun_data": __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock(),
        "win_azi": int(_WIN_AZI_1025),
        "fov_left": 90,
        "fov_right": 137,
        "h_win": _H_WIN_1025,
        "distance": _DISTANCE_1025,
        "awn_length": _AWN_LENGTH_1025,
        "awn_angle": awn_angle,
    }
    if shade_mode is not None:
        kwargs["shade_mode"] = shade_mode
    kwargs.update(overrides)
    return build_horizontal_cover(**kwargs)


class TestOverhangProjection1025:
    """Window-mode overhang math (issue #1025)."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("gamma", "expected"),
        [
            (0.0, 4.4045),
            (45.0, 3.0854),
            (60.0, 2.1527),
            (75.0, 1.0666),
            (85.0, 0.2935),
        ],
    )
    def test_window_mode_overhang_table(self, gamma, expected):
        """L decays as cos γ (α=25°) — not the ~4.5 flat develop coupling reads."""
        cover = _awning_1025(sol_elev=25.0, gamma=gamma)
        assert cover.calculate_position() == pytest.approx(expected, abs=1e-2)

    @pytest.mark.unit
    def test_reduces_to_closed_form_at_awn_angle_zero(self):
        """awn_angle=0 reduces to h_win·cos γ/tan α − d exactly."""
        import math

        for gamma in (0.0, 30.0, 55.0):
            cover = _awning_1025(sol_elev=32.0, gamma=gamma)
            expected = (
                _H_WIN_1025
                * math.cos(math.radians(gamma))
                / math.tan(math.radians(32.0))
                - _DISTANCE_1025
            )
            assert cover.calculate_position() == pytest.approx(expected, abs=1e-6)

    @pytest.mark.unit
    @pytest.mark.parametrize("gamma", [95.0, 120.0, 137.0])
    def test_retracts_behind_glass_plane(self, gamma):
        """|γ|>90° (sun behind the window plane) → fully retracted (0.0)."""
        cover = _awning_1025(sol_elev=25.0, gamma=gamma)
        assert cover.calculate_position() == 0.0

    @pytest.mark.unit
    def test_retracts_at_exactly_90(self):
        """At exactly γ=90° cos γ ≈ +6e-17; guard still retracts (< 1e-9)."""
        cover = _awning_1025(sol_elev=25.0, gamma=90.0)
        assert cover.calculate_position() < 1e-9

    @pytest.mark.unit
    def test_no_discontinuity_across_gamma_90(self):
        """Sweeping γ 85°→95° (α=35°): monotone non-increasing, no notch."""
        gammas = [85.0 + 0.25 * i for i in range(int((95.0 - 85.0) / 0.25) + 1)]
        lengths = [
            _awning_1025(sol_elev=35.0, gamma=g).calculate_position() for g in gammas
        ]
        for prev, cur in zip(lengths, lengths[1:], strict=False):
            assert cur <= prev + 1e-12  # non-increasing
            assert abs(cur - prev) < 0.05  # no jump
        # L(90°) collapses to (near) zero.
        idx90 = gammas.index(90.0)
        assert lengths[idx90] < 1e-3

    @pytest.mark.unit
    def test_elevation_scaling(self):
        """γ=0: lower sun → longer overhang, tracking cot α (out of clip band)."""
        import math

        # 25/45/70° all yield L < awn_length (6.0), so none clip.
        l25 = _awning_1025(sol_elev=25.0, gamma=0.0).calculate_position()
        l45 = _awning_1025(sol_elev=45.0, gamma=0.0).calculate_position()
        l70 = _awning_1025(sol_elev=70.0, gamma=0.0).calculate_position()
        assert l25 > l45 > l70
        for elev, length in ((25.0, l25), (45.0, l45), (70.0, l70)):
            expected = _H_WIN_1025 / math.tan(math.radians(elev)) - _DISTANCE_1025
            assert length == pytest.approx(expected, abs=1e-2)


class TestAreaShadeMode1025:
    """Area (patio) shade mode stays fully extended across the whole FOV."""

    @pytest.mark.unit
    @pytest.mark.parametrize("gamma", [0.0, 60.0, 89.0, 95.0, 130.0])
    @pytest.mark.parametrize("sol_elev", [15.0, 45.0, 70.0])
    def test_area_mode_stays_extended(self, gamma, sol_elev):
        """Area mode returns full extension whenever called — incl. past γ=90°."""
        cover = _awning_1025(sol_elev=sol_elev, gamma=gamma, shade_mode="area")
        assert cover.calculate_position() == pytest.approx(_AWN_LENGTH_1025)
        assert cover.calculate_percentage() == pytest.approx(100.0)

    @pytest.mark.unit
    def test_window_mode_is_default(self):
        """A HorizontalConfig built without shade_mode defaults to window mode."""
        from tests.cover_helpers import make_horizontal_config

        assert make_horizontal_config().shade_mode == "window"
        # And a window-mode awning does NOT sit at full extension at moderate sun.
        cover = _awning_1025(sol_elev=25.0, gamma=45.0)
        assert cover.calculate_position() < _AWN_LENGTH_1025


class TestAreaShadeModeGate:
    """#1025 area mode at the GATE, not just at ``calculate_position`` (#1030).

    ``TestAreaShadeMode1025`` calls ``calculate_position()`` directly, so it
    never touches ``direct_sun_valid`` — the illumination gate #1030 adds could
    silently retract an area-mode patio awning past ``|gamma| = 90`` while that
    class stayed green. These assert the routing decision itself.
    """

    @pytest.mark.unit
    @pytest.mark.parametrize("gamma", [95.0, 130.0])
    def test_area_mode_direct_sun_valid_past_90(self, gamma):
        """A patio awning shades the PATIO, not the glass — it opts out of the gate."""
        cover = _awning_1025(
            sol_elev=30.0,
            gamma=gamma,
            shade_mode="area",
            fov_left=137,
            sun_data=make_daytime_sun_data(),
        )
        assert cover.direct_sun_valid is True
        assert cover.calculate_position() == pytest.approx(_AWN_LENGTH_1025)

    @pytest.mark.unit
    def test_window_mode_direct_sun_valid_gated_past_90(self):
        """Window mode shades the glass, so past 90 it falls through to default."""
        cover = _awning_1025(
            sol_elev=30.0,
            gamma=91.0,
            fov_left=137,
            sun_data=make_daytime_sun_data(),
        )
        assert cover.direct_sun_valid is False


class TestAdaptiveHorizontalCover:
    """Test AdaptiveHorizontalCover calculations."""

    @pytest.mark.unit
    def test_calculate_position_standard(self, horizontal_cover_instance):
        """Test awning extension calculation."""
        length = horizontal_cover_instance.calculate_position()
        # Awning extends based on vertical height and angles
        assert length >= 0

    @pytest.mark.unit
    def test_calculate_position_with_awning_angle(self, horizontal_cover_instance):
        """Test awning calculation with non-zero angle."""
        horizontal_cover_instance.awn_angle = 15.0
        length = horizontal_cover_instance.calculate_position()
        assert length >= 0

    @pytest.mark.unit
    def test_calculate_position_high_sun(self, horizontal_cover_instance):
        """Test awning with high sun angle."""
        horizontal_cover_instance.sol_elev = 80.0
        length = horizontal_cover_instance.calculate_position()
        # High sun creates minimal shadow
        assert length >= 0

    @pytest.mark.unit
    def test_calculate_position_low_sun(self, horizontal_cover_instance):
        """Test awning with low sun angle."""
        horizontal_cover_instance.sol_elev = 20.0
        length = horizontal_cover_instance.calculate_position()
        # Low sun creates longer shadow
        assert length >= 0

    @pytest.mark.unit
    def test_calculate_percentage_standard(self, horizontal_cover_instance):
        """Test percentage conversion for awning."""
        percentage = horizontal_cover_instance.calculate_percentage()
        assert 0 <= percentage <= 100

    @pytest.mark.unit
    def test_calculate_percentage_with_different_awning_length(
        self, horizontal_cover_instance
    ):
        """Test percentage with different awning length."""
        horizontal_cover_instance.awn_length = 3.0
        percentage = horizontal_cover_instance.calculate_percentage()
        # Longer awning means smaller percentage for same extension
        assert 0 <= percentage <= 100

    @pytest.mark.unit
    @pytest.mark.parametrize("angle", [0, 15, 30, 45])
    def test_awning_angle_variations(self, horizontal_cover_instance, angle):
        """Test various awning angles."""
        horizontal_cover_instance.awn_angle = angle
        result = horizontal_cover_instance.calculate_position()
        assert result >= 0

    @pytest.mark.unit
    def test_calculate_position_saturates_at_awn_length(
        self, mock_sun_data, mock_logger
    ):
        """Regression (#209): extension must saturate at awn_length, never exceed it.

        Low sun (15°) + tall window (3 m) + short awning (1 m) forces the raw
        trig result to ~11.2 m.  Old code clipped at 2×awn_length (2.0 m → 200%);
        new code clips at awn_length (1.0 m → 100%).
        """
        cover = build_horizontal_cover(
            logger=mock_logger,
            sol_azi=180.0,
            sol_elev=15.0,
            sun_data=mock_sun_data,
            h_win=3.0,
            distance=0.5,
            awn_length=1.0,
            awn_angle=0.0,
        )
        assert cover.calculate_position() == pytest.approx(1.0)
        assert cover.calculate_percentage() == pytest.approx(100.0)
