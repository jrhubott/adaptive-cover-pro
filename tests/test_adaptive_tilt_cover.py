"""Tests for AdaptiveTiltCover calculations and tilt configuration service."""

import math

import pytest
import numpy as np
from unittest.mock import MagicMock

from tests.cover_helpers import build_louvered_roof_cover, build_tilt_cover

# Window azimuth every ``_tilt_at`` cover faces, so a test can place the sun by
# surface-solar azimuth (``gamma``) rather than restating the facade orientation.
_WIN_AZI = 180


def _tilt_at(*, sol_azi, sol_elev, slat_distance, depth, mode, safety_margin=0.0):
    """Build an AdaptiveTiltCover at an explicit sun/slat geometry.

    Wide FOV so the sun is always "in front"; only the grazing-angle math is
    exercised. ``safety_margin`` threads the configurable venetian tilt margin
    (issue #783) through to ``TiltConfig``.
    """
    return build_tilt_cover(
        logger=MagicMock(),
        sol_azi=sol_azi,
        sol_elev=sol_elev,
        sunset_pos=0,
        sunset_off=0,
        sunrise_off=0,
        sun_data=MagicMock(),
        fov_left=90,
        fov_right=90,
        win_azi=_WIN_AZI,
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
        slat_distance=slat_distance,
        depth=depth,
        mode=mode,
        safety_margin=safety_margin,
    )


class TestAdaptiveTiltCover:
    """Test AdaptiveTiltCover calculations."""

    @pytest.mark.unit
    def test_beta_property(self, tilt_cover_instance):
        """Test beta angle calculation."""
        beta = tilt_cover_instance.beta
        # Beta should be in radians
        assert isinstance(beta, float | np.floating)

    @pytest.mark.unit
    def test_calculate_position_mode1(self, tilt_cover_instance):
        """Test tilt angle calculation in mode1 (90°)."""
        tilt_cover_instance.mode = "mode1"
        angle = tilt_cover_instance.calculate_position()
        # With negative-discriminant protection: returns 0.0 (closed) safely
        assert not np.isnan(angle), "calculate_position() must never return NaN"
        assert 0 <= angle <= 90

    @pytest.mark.unit
    def test_calculate_position_mode2(self, tilt_cover_instance):
        """Test tilt angle calculation in mode2 (180°)."""
        tilt_cover_instance.mode = "mode2"
        angle = tilt_cover_instance.calculate_position()
        # With negative-discriminant protection: returns 0.0 (closed) safely
        assert not np.isnan(angle), "calculate_position() must never return NaN"
        assert 0 <= angle <= 180

    @pytest.mark.unit
    def test_calculate_percentage_mode1(self, tilt_cover_instance):
        """Test percentage conversion in mode1 returns 0% when math would be invalid.

        The default tilt cover instance has a negative discriminant (slat geometry
        at 45° elevation with depth=0.02, distance=0.03). Previously this raised
        ValueError via round(NaN); now it safely returns 0.0 (blind closed).
        """
        tilt_cover_instance.mode = "mode1"
        pct = tilt_cover_instance.calculate_percentage()
        assert not np.isnan(pct), "calculate_percentage() must never return NaN"
        assert 0 <= pct <= 100

    @pytest.mark.unit
    def test_calculate_percentage_mode2(self, tilt_cover_instance):
        """Test percentage conversion in mode2 returns 0% when math would be invalid.

        The default tilt cover instance has a negative discriminant (slat geometry
        at 45° elevation with depth=0.02, distance=0.03). Previously this raised
        ValueError via round(NaN); now it safely returns 0.0 (blind closed).
        """
        tilt_cover_instance.mode = "mode2"
        pct = tilt_cover_instance.calculate_percentage()
        assert not np.isnan(pct), "calculate_percentage() must never return NaN"
        assert 0 <= pct <= 100

    @pytest.mark.unit
    @pytest.mark.parametrize("depth", [0.01, 0.02, 0.03, 0.04])
    def test_slat_depth_variations(self, tilt_cover_instance, depth):
        """Test with different slat depths."""
        tilt_cover_instance.depth = depth
        angle = tilt_cover_instance.calculate_position()
        # Negative-discriminant guard ensures NaN is never returned
        assert not np.isnan(angle), "calculate_position() must never return NaN"
        assert 0 <= angle <= 180

    @pytest.mark.unit
    @pytest.mark.parametrize("distance", [0.02, 0.03, 0.04, 0.05])
    def test_slat_distance_variations(self, tilt_cover_instance, distance):
        """Test with different slat distances."""
        tilt_cover_instance.slat_distance = distance
        angle = tilt_cover_instance.calculate_position()
        # Negative-discriminant guard ensures NaN is never returned
        assert not np.isnan(angle), "calculate_position() must never return NaN"
        assert 0 <= angle <= 180

    @pytest.mark.unit
    @pytest.mark.parametrize("elev", [10, 30, 45, 60, 80])
    def test_beta_with_different_sun_angles(self, tilt_cover_instance, elev):
        """Test beta calculation with various sun positions."""
        tilt_cover_instance.sol_elev = elev
        beta = tilt_cover_instance.beta
        assert isinstance(beta, float | np.floating)

    @pytest.mark.unit
    def test_position_with_gamma_angle(self, tilt_cover_instance):
        """Test tilt position with angled sun (gamma != 0)."""
        tilt_cover_instance.sol_azi = 210.0  # gamma = -30°
        angle = tilt_cover_instance.calculate_position()
        assert 0 <= angle <= 180


class TestVenetianTiltSafetyMargin:
    """Configurable venetian tilt safety margin (issue #783)."""

    # Low elevation + high gamma: positive discriminant, raw grazing angle in
    # (0, 90), and geo_margin well above 1.0 so the margin transform is visible.
    _EXTREME = {"sol_azi": 255, "sol_elev": 8, "slat_distance": 0.02, "depth": 0.03}

    # Inside the inert envelope (|gamma| <= 45, 10 <= elev <= 75) so
    # geo_margin == 1.0 exactly. mode2 keeps the result off both the 0° and
    # 180° rails, so the margin transform is visible rather than clamped.
    _BENIGN = {"sol_azi": 180, "sol_elev": 45, "slat_distance": 0.02, "depth": 0.03}

    # Slats spaced strictly farther apart than they are deep: the cut-off
    # discriminant goes negative across much of the envelope, so the solve parks
    # them fully closed and returns before the margin block runs at all. The
    # inequality is strict — at ``slat_distance == depth`` the discriminant
    # reduces to ``tan²β``, which is never negative.
    _WIDE_SPACED = {"slat_distance": 0.05, "depth": 0.03}

    @pytest.mark.unit
    def test_safety_margin_default_is_identity(self):
        """safety_margin=0.0 must be a byte-for-byte no-op on the grazing angle."""
        c = _tilt_at(mode="mode1", safety_margin=0.0, **self._EXTREME)
        result = c.calculate_position()
        raw = c._last_calc_details["slat_angle_raw_deg"]
        expected = max(0.0, min(90.0, raw))
        assert result == expected

    @pytest.mark.unit
    def test_safety_margin_closes_more_mode1(self):
        """safety_margin=1.0 closes the slats more (smaller angle) in mode1."""
        a0 = _tilt_at(
            mode="mode1", safety_margin=0.0, **self._EXTREME
        ).calculate_position()
        a1 = _tilt_at(
            mode="mode1", safety_margin=1.0, **self._EXTREME
        ).calculate_position()
        assert a1 < a0
        assert 0 <= a1 <= 90

    @pytest.mark.unit
    def test_safety_margin_closes_more_mode2_upper_branch(self):
        """On the mode2 upper branch (raw > 90) the margin drives toward 180."""
        params = {"sol_azi": 240, "sol_elev": 30, "slat_distance": 0.02, "depth": 0.03}
        a0 = _tilt_at(mode="mode2", safety_margin=0.0, **params).calculate_position()
        a1 = _tilt_at(mode="mode2", safety_margin=1.0, **params).calculate_position()
        assert a0 > 90, f"test setup: raw angle {a0} must be on the upper branch"
        assert a1 > a0
        assert 90 < a1 <= 180

    @pytest.mark.unit
    def test_safety_margin_bites_at_benign_angle(self):
        """Issue #1089: the slider must do real work where geo_margin == 1.0."""
        from custom_components.adaptive_cover_pro.const import (
            SAFETY_MARGIN_USER_SLACK_MAX,
            TILT_HORIZONTAL_DEG,
        )
        from custom_components.adaptive_cover_pro.geometry import (
            SafetyMarginCalculator,
        )

        c0 = _tilt_at(mode="mode2", safety_margin=0.0, **self._BENIGN)
        a0 = c0.calculate_position()
        assert (
            SafetyMarginCalculator.calculate(c0.gamma, c0.sol_elev) == 1.0
        ), "test setup: this geometry must sit inside the inert envelope"

        a1 = _tilt_at(
            mode="mode2", safety_margin=1.0, **self._BENIGN
        ).calculate_position()
        # mode2 upper branch: closing drives the slat toward 180°.
        expected = TILT_HORIZONTAL_DEG + (a0 - TILT_HORIZONTAL_DEG) * (
            1.0 + SAFETY_MARGIN_USER_SLACK_MAX
        )
        assert a1 == pytest.approx(expected)
        assert a1 - a0 > 1.0, "the slider must move the slat, not float-noise it"
        assert TILT_HORIZONTAL_DEG < a1 <= 180

    @pytest.mark.unit
    def test_build_trace_includes_safety_margin(self):
        """_build_trace records the effective margin (diagnostics parity, #682)."""
        from custom_components.adaptive_cover_pro.const import (
            SAFETY_MARGIN_USER_SLACK_MAX,
        )
        from custom_components.adaptive_cover_pro.geometry import (
            SafetyMarginCalculator,
        )

        c = _tilt_at(mode="mode1", safety_margin=1.0, **self._EXTREME)
        c.calculate_position()
        trace = c._last_calc_details
        assert "safety_margin" in trace
        geo_margin = SafetyMarginCalculator.calculate(c.gamma, c.sol_elev)
        assert geo_margin > 1.0, "test setup: _EXTREME is outside the benign envelope"
        # #1089: at full strength the effective margin is the geometry's own
        # excess PLUS the flat user-slack budget — strictly more than geo_margin.
        assert trace["safety_margin"] == pytest.approx(
            geo_margin + SAFETY_MARGIN_USER_SLACK_MAX
        )
        assert trace["safety_margin"] > geo_margin

    @pytest.mark.unit
    def test_build_trace_safety_margin_identity_default(self):
        """At safety_margin=0.0 the recorded effective margin is exactly 1.0."""
        c = _tilt_at(mode="mode1", safety_margin=0.0, **self._EXTREME)
        c.calculate_position()
        assert c._last_calc_details["safety_margin"] == 1.0

    @pytest.mark.unit
    @pytest.mark.parametrize("sol_azi", [165, 180, 195])
    @pytest.mark.parametrize("sol_elev", [15, 45, 60, 70])
    def test_safety_margin_bites_wherever_mode2_is_off_its_travel_limits(
        self, sol_azi, sol_elev
    ):
        """#1089: in mode2 the slider moves any slat that is off its travel limits.

        NOT a claim that mode2 never freezes inside the envelope — it does, in
        two ways, each pinned by its own companion test. ``TILT_HORIZONTAL_DEG``
        is the fixed point the closing transform pivots around, and mode2 lands
        exactly there whenever the profile angle matches the slat aspect ratio
        (``test_safety_margin_is_inert_at_the_mode2_horizontal_fixed_point``);
        slats spaced at or wider than their depth make the cut-off discriminant
        negative and park the solve fully closed before the margin block runs at
        all (``test_safety_margin_is_inert_when_the_solve_parks_fully_closed``).
        Mode1 additionally freezes above its 90° ceiling
        (``test_safety_margin_is_inert_above_the_mode1_open_rail``).

        The setup guard below is what scopes this test: it asserts the sampled
        geometry is off the horizontal fixed point, and what is then verified is
        that the transform is visible everywhere the slat has closure left.
        """
        from custom_components.adaptive_cover_pro.const import TILT_HORIZONTAL_DEG
        from custom_components.adaptive_cover_pro.geometry import (
            SafetyMarginCalculator,
        )

        params = {
            "sol_azi": sol_azi,
            "sol_elev": sol_elev,
            "slat_distance": 0.02,
            "depth": 0.03,
        }
        c0 = _tilt_at(mode="mode2", safety_margin=0.0, **params)
        a0 = c0.calculate_position()
        assert SafetyMarginCalculator.calculate(c0.gamma, c0.sol_elev) == 1.0
        raw = c0._last_calc_details["slat_angle_raw_deg"]
        assert raw != pytest.approx(
            TILT_HORIZONTAL_DEG
        ), "test setup: a slat exactly at horizontal is a fixed point of the transform"

        a1 = _tilt_at(mode="mode2", safety_margin=1.0, **params).calculate_position()
        assert a1 != pytest.approx(a0, abs=1e-6)
        assert 0.0 <= a1 <= 180.0

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("sol_azi", "sol_elev"), [(180, 12), (180, 20), (165, 15), (195, 25)]
    )
    def test_safety_margin_bites_below_the_mode1_open_rail(self, sol_azi, sol_elev):
        """#1089 in mode1 too — wherever the raw cut-off is under the 90° ceiling.

        Below the rail the slider closes the slats by exactly the flat user-slack
        budget, since ``geo_margin`` is 1.0 across this envelope. NOT a claim
        that the 90° clamp is mode1's only freeze — the negative-discriminant
        park is mode-independent and pins mode1 too
        (``test_safety_margin_is_inert_when_the_solve_parks_fully_closed`` is
        parametrized over both modes). The setup guard below is what scopes this
        test to geometries that still have closure left.
        """
        from custom_components.adaptive_cover_pro.const import (
            SAFETY_MARGIN_USER_SLACK_MAX,
            TILT_HORIZONTAL_DEG,
        )
        from custom_components.adaptive_cover_pro.geometry import (
            SafetyMarginCalculator,
        )

        params = {
            "sol_azi": sol_azi,
            "sol_elev": sol_elev,
            "slat_distance": 0.02,
            "depth": 0.03,
        }
        c0 = _tilt_at(mode="mode1", safety_margin=0.0, **params)
        a0 = c0.calculate_position()
        assert SafetyMarginCalculator.calculate(c0.gamma, c0.sol_elev) == 1.0
        raw = c0._last_calc_details["slat_angle_raw_deg"]
        assert raw < TILT_HORIZONTAL_DEG, "test setup: must sit below the mode1 rail"

        a1 = _tilt_at(mode="mode1", safety_margin=1.0, **params).calculate_position()
        expected = TILT_HORIZONTAL_DEG - (TILT_HORIZONTAL_DEG - raw) * (
            1.0 + SAFETY_MARGIN_USER_SLACK_MAX
        )
        assert a1 == pytest.approx(expected)
        assert a0 - a1 > 1.0, "the slider must move the slat, not float-noise it"
        assert 0.0 <= a1 <= TILT_HORIZONTAL_DEG

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("sol_azi", "sol_elev"), [(180, 40), (180, 55), (180, 70), (150, 45), (210, 60)]
    )
    def test_safety_margin_is_inert_above_the_mode1_open_rail(self, sol_azi, sol_elev):
        """KNOWN LIMIT (#1089): a slat already pinned fully open cannot close more.

        In mode1 the raw cut-off runs past the 90° ceiling once the elevation
        clears ``atan(r · cos γ)`` — the same condition that freezes mode2 at
        horizontal — so the output clamp pins the result at
        ``TILT_HORIZONTAL_DEG`` both before and after the margin transform. That
        crossover is not one number: it moves with gamma, and with the shipped
        2 cm / 3 cm slats it runs from 25.2° at the ``γ = ±45°`` envelope edge up
        to 33.7° at ``γ = 0`` — so 26° is the WORST case, not a general
        threshold, and at ``γ = 0`` the slider is still live right through it.
        Every case below therefore sits above its own crossover, not above 26°.
        That is the physics — a slat at its fully-open travel limit
        has no closure left for a multiplier to scale — not a defect, and it is
        why the user-facing copy scopes the claim instead of promising every sun
        angle. This test fails loudly if the clamp is ever changed.
        """
        from custom_components.adaptive_cover_pro.const import TILT_HORIZONTAL_DEG
        from custom_components.adaptive_cover_pro.geometry import (
            SafetyMarginCalculator,
        )

        params = {
            "sol_azi": sol_azi,
            "sol_elev": sol_elev,
            "slat_distance": 0.02,
            "depth": 0.03,
        }
        c0 = _tilt_at(mode="mode1", safety_margin=0.0, **params)
        a0 = c0.calculate_position()
        assert SafetyMarginCalculator.calculate(c0.gamma, c0.sol_elev) == 1.0
        raw = c0._last_calc_details["slat_angle_raw_deg"]
        assert raw > TILT_HORIZONTAL_DEG, "test setup: must sit above the mode1 rail"

        a1 = _tilt_at(mode="mode1", safety_margin=1.0, **params).calculate_position()
        assert a0 == TILT_HORIZONTAL_DEG
        assert a1 == a0

    @pytest.mark.unit
    @pytest.mark.parametrize("gamma", [-45, -30, -15, 0, 15, 30, 45])
    def test_safety_margin_is_inert_at_the_mode2_horizontal_fixed_point(self, gamma):
        """KNOWN LIMIT (#1089): mode2 freezes too, wherever the slat resolves flat.

        The cut-off solve ``2·atan((tan β + √(tan²β − r² + 1)) / (1 + r))``
        collapses to exactly ``TILT_HORIZONTAL_DEG`` when ``tan β`` equals the
        slat aspect ratio ``r = slat_distance / depth``: the discriminant is 1
        and the arctan argument is 1. Horizontal is the pivot the closing
        transform scales away from, so it is a fixed point — the slats are
        already fully open and there is no closure left to scale, exactly as at
        the mode1 ceiling. With ``β = atan(tan(elev) / cos γ)`` the freeze sits
        at ``elev = atan(r · cos γ)``, which lands inside the normal envelope for
        every ``γ`` the envelope covers, so mode2's 180° ceiling is NOT what
        keeps the slider alive. This is why the user-facing copy states the
        travel-limit rule generally instead of promising any cover type a
        sun-angle range.
        """
        from custom_components.adaptive_cover_pro.const import TILT_HORIZONTAL_DEG
        from custom_components.adaptive_cover_pro.geometry import (
            SafetyMarginCalculator,
        )

        ratio = self._BENIGN["slat_distance"] / self._BENIGN["depth"]
        params = {
            **self._BENIGN,
            "sol_azi": _WIN_AZI - gamma,
            "sol_elev": math.degrees(math.atan(ratio * math.cos(math.radians(gamma)))),
        }
        c0 = _tilt_at(mode="mode2", safety_margin=0.0, **params)
        a0 = c0.calculate_position()
        assert c0.gamma == pytest.approx(gamma)
        assert SafetyMarginCalculator.calculate(c0.gamma, c0.sol_elev) == 1.0
        raw = c0._last_calc_details["slat_angle_raw_deg"]
        assert raw == pytest.approx(TILT_HORIZONTAL_DEG)

        a1 = _tilt_at(mode="mode2", safety_margin=1.0, **params).calculate_position()
        assert a1 == a0, "horizontal is the fixed point — no closure left to scale"

    @pytest.mark.unit
    @pytest.mark.parametrize("mode", ["mode1", "mode2"])
    @pytest.mark.parametrize(
        ("sol_azi", "sol_elev"), [(180, 20), (180, 45), (165, 30), (195, 50), (150, 40)]
    )
    def test_safety_margin_is_inert_when_the_solve_parks_fully_closed(
        self, sol_azi, sol_elev, mode
    ):
        """KNOWN LIMIT (#1089): the third freeze — slats spaced wider than their depth.

        With ``slat_distance > depth`` the cut-off discriminant
        ``tan²β − r² + 1`` goes negative across much of the envelope: there is no
        real root, the solve parks the slats fully closed, and it returns BEFORE
        the margin block runs at all. Same user-visible outcome as the two rail
        cases — the slats are already at a travel limit — reached by a third
        mechanism, and it is mode-independent.
        """
        from custom_components.adaptive_cover_pro.geometry import (
            SafetyMarginCalculator,
        )

        params = {**self._WIDE_SPACED, "sol_azi": sol_azi, "sol_elev": sol_elev}
        c0 = _tilt_at(mode=mode, safety_margin=0.0, **params)
        a0 = c0.calculate_position()
        assert SafetyMarginCalculator.calculate(c0.gamma, c0.sol_elev) == 1.0
        trace = c0._last_calc_details
        assert trace["negative_discriminant"] is True
        assert trace["discriminant"] < 0
        assert a0 == 0.0

        a1 = _tilt_at(mode=mode, safety_margin=1.0, **params).calculate_position()
        assert a1 == a0, "fully closed is a travel limit — nothing left to close"

    @pytest.mark.unit
    def test_safety_margin_monotonic_in_strength_at_benign_angle(self):
        """Raising the slider always closes further, even inside the envelope."""
        angles = [
            _tilt_at(mode="mode2", safety_margin=s, **self._BENIGN).calculate_position()
            for s in (0.0, 0.25, 0.5, 0.75, 1.0)
        ]
        assert all(b > a for a, b in zip(angles, angles[1:], strict=False))

    @pytest.mark.unit
    def test_build_trace_safety_margin_at_benign_angle(self):
        """#1089: the trace reports the user slack even where geo_margin == 1.0."""
        from custom_components.adaptive_cover_pro.const import (
            SAFETY_MARGIN_USER_SLACK_MAX,
        )

        c = _tilt_at(mode="mode2", safety_margin=1.0, **self._BENIGN)
        c.calculate_position()
        assert c._last_calc_details["safety_margin"] == pytest.approx(
            1.0 + SAFETY_MARGIN_USER_SLACK_MAX
        )


@pytest.mark.unit
def test_get_tilt_data_reads_safety_margin():
    """get_tilt_data threads CONF_VENETIAN_TILT_SAFETY_MARGIN into TiltConfig."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_TILT_SAFETY_MARGIN,
    )
    from custom_components.adaptive_cover_pro.services.configuration_service import (
        ConfigurationService,
    )

    config_entry = MagicMock()
    config_entry.data = {"name": "Test Tilt"}
    config_service = ConfigurationService(
        MagicMock(), config_entry, MagicMock(), "cover_venetian", None, None, None
    )

    result_custom = config_service.get_tilt_data(
        {
            "slat_distance": 3.0,
            "slat_depth": 2.0,
            "tilt_mode": "mode1",
            CONF_VENETIAN_TILT_SAFETY_MARGIN: 0.5,
        }
    )
    assert result_custom.safety_margin == 0.5

    result_default = config_service.get_tilt_data(
        {"slat_distance": 3.0, "slat_depth": 2.0, "tilt_mode": "mode1"}
    )
    assert result_default.safety_margin == 0.0


@pytest.mark.unit
def test_tilt_data_cm_to_meter_conversion():
    """Test that ConfigurationService.get_tilt_data converts centimeters to meters.

    This is a critical test for Issue #5 - ensures the UI input in cm
    is correctly converted to meters for calculation formulas.
    """
    from custom_components.adaptive_cover_pro.services.configuration_service import (
        ConfigurationService,
    )

    # Create a mock configuration service instance
    config_entry = MagicMock()
    config_entry.data = {"name": "Test Tilt"}
    logger = MagicMock()
    hass = MagicMock()

    config_service = ConfigurationService(
        hass,
        config_entry,
        logger,
        "cover_tilt",
        None,
        None,
        None,
    )

    # Use the actual get_tilt_data method
    options = {
        "slat_distance": 2.0,  # 2.0 cm (user input)
        "slat_depth": 2.5,  # 2.5 cm (user input)
        "tilt_mode": "mode2",
    }

    # Call the actual method
    result = config_service.get_tilt_data(options)

    # Should convert cm to meters — result is a TiltConfig dataclass
    assert result.slat_distance == pytest.approx(0.02, abs=0.0001)  # 2.0 cm -> 0.02 m
    assert result.depth == pytest.approx(0.025, abs=0.0001)  # 2.5 cm -> 0.025 m
    assert result.mode == "mode2"


@pytest.mark.unit
def test_get_tilt_data_reads_max_tilt():
    """get_tilt_data populates TiltConfig.max_tilt from options; defaults to 100."""
    from custom_components.adaptive_cover_pro.services.configuration_service import (
        ConfigurationService,
    )

    config_entry = MagicMock()
    config_entry.data = {"name": "Test Tilt"}
    logger = MagicMock()
    hass = MagicMock()

    config_service = ConfigurationService(
        hass, config_entry, logger, "cover_venetian", None, None, None
    )

    result_custom = config_service.get_tilt_data(
        {"slat_distance": 3.0, "slat_depth": 2.0, "tilt_mode": "mode1", "max_tilt": 60}
    )
    assert result_custom.max_tilt == 60

    result_default = config_service.get_tilt_data(
        {"slat_distance": 3.0, "slat_depth": 2.0, "tilt_mode": "mode1"}
    )
    assert result_default.max_tilt == 100


def test_get_tilt_data_reads_min_tilt():
    """get_tilt_data populates TiltConfig.min_tilt from options; defaults to 0."""
    from custom_components.adaptive_cover_pro.services.configuration_service import (
        ConfigurationService,
    )

    config_entry = MagicMock()
    config_entry.data = {"name": "Test Tilt"}
    logger = MagicMock()
    hass = MagicMock()

    config_service = ConfigurationService(
        hass, config_entry, logger, "cover_venetian", None, None, None
    )

    result_custom = config_service.get_tilt_data(
        {"slat_distance": 3.0, "slat_depth": 2.0, "tilt_mode": "mode1", "min_tilt": 25}
    )
    assert result_custom.min_tilt == 25

    result_default = config_service.get_tilt_data(
        {"slat_distance": 3.0, "slat_depth": 2.0, "tilt_mode": "mode1"}
    )
    assert result_default.min_tilt == 0


def test_get_tilt_data_reads_tilt_transform():
    """get_tilt_data populates TiltConfig.tilt_transform; defaults to clamp."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_TILT_TRANSFORM,
        VENETIAN_TILT_TRANSFORM_CLAMP,
        VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
    )
    from custom_components.adaptive_cover_pro.services.configuration_service import (
        ConfigurationService,
    )

    config_entry = MagicMock()
    config_entry.data = {"name": "Test Tilt"}
    config_service = ConfigurationService(
        MagicMock(), config_entry, MagicMock(), "cover_venetian", None, None, None
    )

    result_custom = config_service.get_tilt_data(
        {
            "slat_distance": 3.0,
            "slat_depth": 2.0,
            "tilt_mode": "mode1",
            CONF_VENETIAN_TILT_TRANSFORM: VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
        }
    )
    assert result_custom.tilt_transform == VENETIAN_TILT_TRANSFORM_PROPORTIONAL

    result_default = config_service.get_tilt_data(
        {"slat_distance": 3.0, "slat_depth": 2.0, "tilt_mode": "mode1"}
    )
    assert result_default.tilt_transform == VENETIAN_TILT_TRANSFORM_CLAMP


def test_get_tilt_data_reads_specified_endpoint_angles():
    """get_tilt_data stores explicit endpoint angles for specify-angles mode."""
    from custom_components.adaptive_cover_pro.services.configuration_service import (
        ConfigurationService,
    )

    config_entry = MagicMock()
    config_entry.data = {"name": "Test Tilt"}
    logger = MagicMock()
    hass = MagicMock()

    config_service = ConfigurationService(
        hass, config_entry, logger, "cover_venetian", None, None, None
    )

    result = config_service.get_tilt_data(
        {
            "slat_distance": 3.0,
            "slat_depth": 2.0,
            "tilt_mode": "specify_angles",
            "tilt_angle_0": 20,
            "tilt_angle_100": 140,
        }
    )

    assert result.mode == "specify_angles"
    assert result.angle_0 == 20
    assert result.angle_100 == 140


def test_get_tilt_data_defaults_specified_endpoint_angles_to_full_raw_range():
    """Missing specify-angles endpoints default to 0°..180°."""
    from custom_components.adaptive_cover_pro.services.configuration_service import (
        ConfigurationService,
    )

    config_entry = MagicMock()
    config_entry.data = {"name": "Test Tilt"}
    logger = MagicMock()
    hass = MagicMock()

    config_service = ConfigurationService(
        hass, config_entry, logger, "cover_venetian", None, None, None
    )

    result = config_service.get_tilt_data(
        {
            "slat_distance": 3.0,
            "slat_depth": 2.0,
            "tilt_mode": "specify_angles",
        }
    )

    assert result.angle_0 == 0
    assert result.angle_100 == 180


def _tilt_config_service():
    from custom_components.adaptive_cover_pro.services.configuration_service import (
        ConfigurationService,
    )

    config_entry = MagicMock()
    config_entry.data = {"name": "Test Tilt"}
    return ConfigurationService(
        MagicMock(), config_entry, MagicMock(), "cover_venetian", None, None, None
    )


def test_get_tilt_data_reads_horizontal_percent():
    """The three-point mid-point reaches the engine (#1222)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_TILT_HORIZONTAL_PERCENT,
    )

    result = _tilt_config_service().get_tilt_data(
        {
            "slat_distance": 3.0,
            "slat_depth": 2.0,
            "tilt_mode": "specify_angles",
            "tilt_angle_0": 0,
            "tilt_angle_100": 130,
            CONF_TILT_HORIZONTAL_PERCENT: 50,
        }
    )

    assert result.horizontal_percent == 50


def test_get_tilt_data_defaults_horizontal_percent_to_disabled():
    """An entry written before the option existed reads as the 0 sentinel."""
    from custom_components.adaptive_cover_pro.const import (
        DEFAULT_TILT_HORIZONTAL_PERCENT,
    )

    result = _tilt_config_service().get_tilt_data(
        {
            "slat_distance": 3.0,
            "slat_depth": 2.0,
            "tilt_mode": "specify_angles",
        }
    )

    assert result.horizontal_percent == DEFAULT_TILT_HORIZONTAL_PERCENT == 0


@pytest.mark.unit
def test_tilt_data_warns_on_small_values(caplog):
    """Test that ConfigurationService.get_tilt_data warns when values are suspiciously small.

    Values < 0.1 likely indicate user entered meters (following old instructions)
    instead of centimeters.
    """
    import logging
    from custom_components.adaptive_cover_pro.services.configuration_service import (
        ConfigurationService,
    )

    # Create a mock configuration service instance
    config_entry = MagicMock()
    config_entry.data = {"name": "Test Tilt Small"}
    logger = MagicMock()
    hass = MagicMock()

    config_service = ConfigurationService(
        hass,
        config_entry,
        logger,
        "cover_tilt",
        None,
        None,
        None,
    )

    # Use very small values (likely meters entered by mistake)
    options = {
        "slat_distance": 0.02,  # 0.02 cm (suspiciously small - likely meant 0.02m)
        "slat_depth": 0.025,  # 0.025 cm (suspiciously small - likely meant 0.025m)
        "tilt_mode": "mode2",
    }

    with caplog.at_level(logging.WARNING):
        result = config_service.get_tilt_data(options)

    # Should still convert (0.02 cm -> 0.0002 m) but log warning — result is TiltConfig
    assert result.slat_distance == pytest.approx(0.0002, abs=0.00001)
    assert result.depth == pytest.approx(0.00025, abs=0.00001)

    # Should have logged a warning
    assert any(
        "slat dimensions are very small" in record.message for record in caplog.records
    )
    assert any("CENTIMETERS" in record.message for record in caplog.records)


class TestTiltAxisLimits:
    """Shared tilt-axis limits honored on the standalone tilt engine (issue #964).

    ``AdaptiveTiltCover.calculate_percentage`` self-applies ``[min_tilt,
    max_tilt]`` (plus the ``*_sun_only`` flags and the ``tilt_transform``) via
    the shared ``PositionConverter.apply_tilt_limits`` seam, so a tilt-only (and
    louvered-roof) cover honors the same tilt-band controls venetian already
    reaches. The venetian sub-engine opts out (``apply_tilt_axis_limits=False``)
    because ``VenetianCoverCalculation._clamp_tilt`` applies them downstream.
    """

    # Low elevation + high gamma → positive discriminant, mid-range raw tilt %,
    # so a cap/floor/transform is observable.
    _GEO = {"sol_azi": 255, "sol_elev": 8, "slat_distance": 0.02, "depth": 0.03}

    def _pct(self, **tilt_over):
        cover = build_tilt_cover(
            logger=MagicMock(),
            sol_azi=self._GEO["sol_azi"],
            sol_elev=self._GEO["sol_elev"],
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
            slat_distance=self._GEO["slat_distance"],
            depth=self._GEO["depth"],
            mode="mode1",
            **tilt_over,
        )
        return cover.calculate_percentage()

    @pytest.mark.unit
    def test_baseline_is_mid_range(self):
        baseline = int(round(self._pct()))
        assert 0 < baseline < 100, f"geometry must yield mid-range tilt, got {baseline}"

    @pytest.mark.unit
    def test_default_limits_are_no_op(self):
        assert self._pct() == self._pct(min_tilt=0, max_tilt=100)

    @pytest.mark.unit
    def test_max_tilt_caps_output(self):
        baseline = int(round(self._pct()))
        cap = baseline - 5
        assert int(round(self._pct(max_tilt=cap))) == cap

    @pytest.mark.unit
    def test_min_tilt_floors_output(self):
        baseline = int(round(self._pct()))
        floor = baseline + 5
        assert int(round(self._pct(min_tilt=floor))) == floor

    @pytest.mark.unit
    def test_proportional_transform_remaps_into_band(self):
        from custom_components.adaptive_cover_pro.const import (
            VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
        )
        from custom_components.adaptive_cover_pro.position_utils import (
            PositionConverter,
        )

        raw = int(round(self._pct()))
        got = int(
            round(
                self._pct(
                    min_tilt=20,
                    max_tilt=60,
                    tilt_transform=VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
                )
            )
        )
        expected = PositionConverter.apply_tilt_limits(
            raw,
            20,
            60,
            False,
            False,
            sun_valid=True,
            transform=VENETIAN_TILT_TRANSFORM_PROPORTIONAL,
        )
        assert got == expected
        # Proportional actually differs from a flat clamp here.
        assert got != int(round(self._pct(min_tilt=20, max_tilt=60)))

    @pytest.mark.unit
    def test_sun_only_flag_still_applies_on_sun_tracking_path(self):
        # The engine path is always sun-tracking (sun_valid=True), so a
        # sun-only cap still bites here.
        baseline = int(round(self._pct()))
        cap = baseline - 5
        assert int(round(self._pct(max_tilt=cap, max_tilt_sun_only=True))) == cap

    @pytest.mark.unit
    def test_venetian_subengine_opts_out_of_self_clamp(self):
        """A tilt engine with apply_tilt_axis_limits=False returns raw (uncapped).

        This is the seam VenetianCoverCalculation relies on to avoid applying
        the tilt band twice (its own _clamp_tilt does it downstream).
        """
        from custom_components.adaptive_cover_pro.engine.covers import (
            AdaptiveTiltCover,
        )
        from tests.cover_helpers import make_cover_config, make_tilt_config

        baseline = int(round(self._pct()))
        cap = baseline - 5
        raw_engine = AdaptiveTiltCover(
            logger=MagicMock(),
            sol_azi=self._GEO["sol_azi"],
            sol_elev=self._GEO["sol_elev"],
            sun_data=MagicMock(),
            config=make_cover_config(win_azi=180, fov_left=90, fov_right=90),
            tilt_config=make_tilt_config(
                slat_distance=self._GEO["slat_distance"],
                depth=self._GEO["depth"],
                mode="mode1",
                max_tilt=cap,
            ),
            apply_tilt_axis_limits=False,
        )
        assert int(round(raw_engine.calculate_percentage())) == baseline


# ---------------------------------------------------------------------------
# Three-point calibration (issue #1222)
# ---------------------------------------------------------------------------
# ``specify_angles`` gains an optional third calibration point: the tilt
# percentage at which the slats are exactly horizontal. Set it and the scale
# becomes two straight segments hinged at ``TILT_HORIZONTAL_DEG``; leave it at
# the ``0`` disabled sentinel and the map is the two-point affine one, unchanged.


def _calibrated_tilt(
    *,
    angle_0: float = 0.0,
    angle_100: float = 180.0,
    horizontal_percent: float | None = None,
    mode: str = "specify_angles",
    sol_elev: float = 45.0,
):
    """Build a tilt engine on an explicit endpoint calibration.

    ``horizontal_percent=None`` leaves the field at its dataclass default, which
    is what an install that never touched the new option stores.
    """
    from custom_components.adaptive_cover_pro.engine.covers import AdaptiveTiltCover
    from tests.cover_helpers import make_cover_config, make_tilt_config

    extra = (
        {} if horizontal_percent is None else {"horizontal_percent": horizontal_percent}
    )
    return AdaptiveTiltCover(
        logger=MagicMock(),
        sol_azi=_WIN_AZI,
        sol_elev=sol_elev,
        sun_data=MagicMock(),
        config=make_cover_config(win_azi=_WIN_AZI, fov_left=90, fov_right=90),
        tilt_config=make_tilt_config(
            slat_distance=0.02,
            depth=0.03,
            mode=mode,
            angle_0=angle_0,
            angle_100=angle_100,
            **extra,
        ),
    )


def _two_point_percentage(angle: float, angle_0: float, angle_100: float) -> float:
    """Oracle for the two-point affine map, written the way the engine writes it."""
    return ((max(0.0, min(180.0, angle)) - angle_0) / (angle_100 - angle_0)) * 100.0


# The reporter's KNX venetian: 0 % is closed downward, horizontal sits at the
# midpoint of the reported travel, and 100 % is only 130° — 1.8 °/% below
# horizontal against 0.8 °/% above it.
_REPORTER = {"angle_0": 0.0, "angle_100": 130.0, "horizontal_percent": 50.0}


class TestThreePointCalibration:
    """The hinged angle→percentage map and its exact inverse (#1222)."""

    @pytest.mark.unit
    def test_midpoint_maps_the_three_calibration_points(self):
        """The whole point of the option: all three points hold at once.

        No affine map can, which is why ``specify_angles`` alone puts this
        blind's horizontal slat at 69.23 % instead of the 50 % it reports.
        """
        from custom_components.adaptive_cover_pro.const import TILT_HORIZONTAL_DEG

        cover = _calibrated_tilt(**_REPORTER)
        assert cover._percentage_from_angle(0.0) == pytest.approx(0.0)
        assert cover._percentage_from_angle(TILT_HORIZONTAL_DEG) == pytest.approx(50.0)
        assert cover._percentage_from_angle(130.0) == pytest.approx(100.0)

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("angle", "expected"),
        [
            (45.0, 25.0),  # halfway up the lower segment
            (22.5, 12.5),
            (107.4, 71.75),  # the reporter's tabulated interior point
            (110.0, 75.0),
        ],
    )
    def test_midpoint_maps_interior_points_piecewise(self, angle, expected):
        """Each side runs at its own degrees-per-percent, meeting at the hinge."""
        cover = _calibrated_tilt(**_REPORTER)
        assert cover._percentage_from_angle(angle) == pytest.approx(expected)

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "angle", [0.0, 12.5, 45.0, 89.9, 90.0, 90.1, 107.4, 130.0, 180.0]
    )
    @pytest.mark.parametrize(
        ("angle_0", "angle_100"),
        [(0.0, 130.0), (20.0, 140.0), (140.0, 20.0), (0.0, 45.0), (120.0, 180.0)],
    )
    def test_zero_midpoint_is_byte_identical_to_two_point(
        self, angle_0, angle_100, angle
    ):
        """The ``0`` sentinel — and an absent option — leave the map untouched.

        Exact equality, not ``approx``: this is the compatibility promise every
        existing ``specify_angles`` install rides on.
        """
        expected = _two_point_percentage(angle, angle_0, angle_100)
        disabled = _calibrated_tilt(
            angle_0=angle_0, angle_100=angle_100, horizontal_percent=0.0
        )
        unset = _calibrated_tilt(angle_0=angle_0, angle_100=angle_100)
        assert disabled._percentage_from_angle(angle) == expected
        assert unset._percentage_from_angle(angle) == expected

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("angle_0", "angle_100", "hp"),
        [
            (100.0, 170.0, 50.0),  # horizontal is below the whole travel
            (0.0, 80.0, 50.0),  # horizontal is above the whole travel
            (0.0, 130.0, 100.0),  # not strictly interior — no upper segment
            (140.0, 20.0, 50.0),  # inverted calibration — no ordered hinge
        ],
    )
    def test_invalid_midpoint_falls_back_to_two_point(self, angle_0, angle_100, hp):
        """A hinge the calibration cannot carry is ignored, never raised on.

        Config-flow and ``set_geometry`` both reject these combinations, so this
        is defence in depth for a hand-edited or partially-migrated entry.
        """
        cover = _calibrated_tilt(
            angle_0=angle_0, angle_100=angle_100, horizontal_percent=hp
        )
        for angle in (0.0, 45.0, 90.0, 130.0, 180.0):
            assert cover._percentage_from_angle(angle) == _two_point_percentage(
                angle, angle_0, angle_100
            )

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("mode", "max_degrees"), [("mode1", 90.0), ("mode2", 180.0)]
    )
    def test_midpoint_is_inert_outside_specify_angles(self, mode, max_degrees):
        """The hinge belongs to the calibrated mode; the presets keep their scale."""
        cover = _calibrated_tilt(mode=mode, horizontal_percent=50.0)
        for angle in (0.0, 45.0, 90.0):
            assert cover._percentage_from_angle(angle) == (angle / max_degrees) * 100.0

    # -- inverse -----------------------------------------------------------

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("mode", "angle_0", "angle_100", "hp"),
        [
            ("mode1", 0.0, 180.0, 0.0),
            ("mode2", 0.0, 180.0, 0.0),
            ("specify_angles", 0.0, 130.0, 0.0),
            ("specify_angles", 0.0, 130.0, 50.0),
            ("specify_angles", 20.0, 140.0, 35.0),
            ("specify_angles", 140.0, 20.0, 0.0),  # inverted, still affine
        ],
    )
    @pytest.mark.parametrize(
        "pct", [0.0, 1.0, 12.5, 34.615384, 50.0, 71.75, 99.0, 100.0]
    )
    def test_angle_from_percentage_round_trips_every_mode(
        self, mode, angle_0, angle_100, hp, pct
    ):
        """``pct → angle → pct`` is the identity on every scale the engine drives."""
        cover = _calibrated_tilt(
            mode=mode, angle_0=angle_0, angle_100=angle_100, horizontal_percent=hp
        )
        angle = cover._angle_from_percentage(pct)
        assert angle is not None
        assert cover._percentage_from_angle(angle) == pytest.approx(pct)

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("pct", "angle"),
        [(0.0, 0.0), (25.0, 45.0), (50.0, 90.0), (71.75, 107.4), (100.0, 130.0)],
    )
    def test_angle_from_percentage_inverts_the_calibration_points(self, pct, angle):
        """The inverse uses the same two segments, so the hinge holds both ways."""
        cover = _calibrated_tilt(**_REPORTER)
        assert cover._angle_from_percentage(pct) == pytest.approx(angle)

    @pytest.mark.unit
    def test_angle_from_percentage_none_on_degenerate_scale(self):
        """A scale the forward map cannot express has no inverse either."""
        degenerate = _calibrated_tilt(
            angle_0=90.0, angle_100=90.0, horizontal_percent=50.0
        )
        assert degenerate._percentage_from_angle(45.0) is None
        assert degenerate._angle_from_percentage(40.0) is None

        legacy = _calibrated_tilt(mode="mode2")
        legacy._effective_max_degrees = MagicMock(return_value=0.0)
        assert legacy._percentage_from_angle(45.0) is None
        assert legacy._angle_from_percentage(40.0) is None

    # -- the pivot everything downstream rounds away from -------------------

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("angle_0", "angle_100", "hp"),
        [(0.0, 130.0, 50.0), (20.0, 140.0, 35.0), (0.0, 130.0, 80.0)],
    )
    def test_pivot_lands_exactly_on_the_configured_midpoint(
        self, angle_0, angle_100, hp
    ):
        """Shared-map wiring, verified end to end (#1090/#1104 ride on this).

        ``coverage_pivot_percentage`` is nothing but the horizontal slat pushed
        through ``_percentage_from_angle``, so hinging that map is all it takes
        for the rounding pivot, the coverage-step anchor and the protective
        comparator to land on the percentage the user calibrated.
        """
        cover = _calibrated_tilt(
            angle_0=angle_0, angle_100=angle_100, horizontal_percent=hp
        )
        assert cover.coverage_pivot_percentage() == pytest.approx(hp)
        assert (
            cover.round_toward_coverage(hp - 0.1, full_coverage_at_zero=True)
            == int(hp) - 1
        )
        assert (
            cover.round_toward_coverage(hp + 0.1, full_coverage_at_zero=True)
            == int(hp) + 1
        )

    # -- trace --------------------------------------------------------------

    @pytest.mark.unit
    @pytest.mark.parametrize("hp", [50.0, 0.0])
    def test_trace_publishes_the_configured_midpoint(self, hp):
        """The companion card rebuilds the scale from the trace (#1222).

        It already reads ``tilt_angle_0_deg``/``tilt_angle_100_deg`` to draw the
        slat; without the mid-point it would draw a straight scale over a hinged
        one. Published unconditionally, including at the ``0`` sentinel, so the
        key set stays stable for consumers.
        """
        cover = _calibrated_tilt(
            angle_0=0.0, angle_100=130.0, horizontal_percent=hp, sol_elev=30.0
        )
        cover.calculate_percentage()

        trace = cover._last_calc_details
        assert trace["tilt_angle_0_deg"] == 0.0
        assert trace["tilt_angle_100_deg"] == 130.0
        assert trace["tilt_horizontal_percent"] == hp


class TestMode1ClampCrossover:
    """Why MODE1's 90° output clamp is the physics, not a leak (#1222 audit)."""

    @pytest.mark.unit
    @pytest.mark.parametrize("sol_azi", [180.0, 165.0, 135.0])
    @pytest.mark.parametrize(
        ("slat_distance", "depth"),
        [
            (0.02, 0.03),  # the shipped default slats
            (0.08, 0.085),  # the reporting install's 8.0 cm / 8.5 cm venetian
        ],
    )
    @pytest.mark.parametrize(
        "sol_elev", [5.0, 12.0, 20.0, 26.0, 33.0, 34.0, 45.0, 60.0, 80.0]
    )
    def test_mode1_clamp_engages_only_where_horizontal_already_blocks(
        self, sol_azi, sol_elev, slat_distance, depth
    ):
        """The clamp fires exactly where a horizontal slat already blocks.

        Reading a MODE1 solve of 106° as "the slats must rotate past horizontal"
        makes the 90° clamp look like it commands maximum openness at the worst
        moment of the day. The geometry says otherwise, and says it as an
        identity rather than a coincidence:

            raw solve > 90°  ⟺  depth · tan β > slat_distance

        (Algebra: ``2·arctan((tanβ + √(tan²β − r² + 1))/(1 + r)) > 90°`` reduces
        to ``tan β > r`` for ``r = slat_distance/depth``, since ``r² + r =
        r(1 + r)``.) The right-hand side is precisely the condition for a
        HORIZONTAL slat to intercept the beam: over its own depth the ray drops
        ``depth · tan β``, and it lands on the slat below once that exceeds the
        spacing. So wherever the clamp engages, 90° is already a blocking
        position and the clamped answer is sound — a 90°-travel drive simply has
        no more-closed position to offer on that side, and the reachable 0° end
        is a full sweep away.

        Characterization of behaviour that is NOT changing here. It exists so
        that the identity is checked rather than asserted in prose, and so any
        future attempt to "fix" the clamp by flipping to the far endpoint has to
        argue with the geometry first.
        """
        cover = _tilt_at(
            sol_azi=sol_azi,
            sol_elev=sol_elev,
            slat_distance=slat_distance,
            depth=depth,
            mode="mode1",
            safety_margin=0.0,
        )
        from custom_components.adaptive_cover_pro.const import TILT_HORIZONTAL_DEG

        commanded = cover.calculate_position()
        trace = cover._last_calc_details
        assert not trace["negative_discriminant"], (
            "test setup: this geometry must resolve a cut-off angle — the "
            "wide-spacing park at 0° is a different mechanism"
        )
        raw = trace["slat_angle_raw_deg"]

        horizontal_already_blocks = depth * math.tan(cover.beta) > slat_distance
        assert (raw > TILT_HORIZONTAL_DEG) == horizontal_already_blocks

        if horizontal_already_blocks:
            assert commanded == TILT_HORIZONTAL_DEG
        else:
            assert commanded == pytest.approx(raw)


# ---------------------------------------------------------------------------
# Climate tilt on a rescaled slat drive (issue #1222 audit)
# ---------------------------------------------------------------------------
# Routing the climate target angle through the engine's own map does not only
# reach ``specify_angles`` covers. A LOUVERED ROOF with a configured
# ``max_slat_angle`` is a rescaled drive too, and its climate percentages move
# as a result. The new answers track the slat's actual travel instead of a
# hardcoded 90°/180° denominator, which is the whole point of the change — but
# it is a behaviour change on a cover type the issue never mentions, so it is
# pinned here rather than left to be discovered.

# ``mode``, target angle, ``sun_through``, the mode-based fallback's answer
# (what a 140° louvered roof got before the engine seam existed), and the
# rescaled answer it gets now.
_LOUVERED_140_CLIMATE = [
    ("mode1", 80.0, False, 89, 57),
    ("mode1", 45.0, False, 50, 32),
    ("mode1", 30.0, True, 33, 86),
    ("mode2", 80.0, False, 44, 57),
    ("mode2", 45.0, False, 25, 32),
    ("mode2", 30.0, True, 67, 86),
]


class TestLouveredRoofClimateTiltIsRescaled:
    """A configured ``max_slat_angle`` rescales the climate tilt too (#1222)."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("mode", "angle_deg", "sun_through", "before", "after"), _LOUVERED_140_CLIMATE
    )
    def test_max_slat_angle_rescales_the_climate_percentage(
        self, mode, angle_deg, sun_through, before, after
    ):
        """The 140° drive's climate answers now sit on its own 140° scale.

        ``before`` is what the mode-based formula still answers with no engine
        in scope: MODE1 divides by 90° and MODE2 by 180°, neither of which is
        this pergola's travel. ``after`` is the engine's answer on the drive's
        real scale — an 80° slat is 57 % of 140°, not 89 % of 90°.

        The two ``sun_through`` rows carry the largest jump, and for a second
        reason: the hemisphere mirror is gated on the pivot being strictly
        interior, and ``max_slat_angle = 140`` puts horizontal at 64.3 %. So a
        MODE1 louvered roof now mirrors where plain MODE1 (pivot 100 %) never
        did — correct, because a 140° drive really does have travel on the far
        side of horizontal, and exactly the class of defect this change set out
        to close.
        """
        from custom_components.adaptive_cover_pro.cover_types.tilt import TiltPolicy

        cover = build_louvered_roof_cover(
            sol_azi=180.0, sol_elev=45.0, roof_pitch=0.0, mode=mode, max_slat_angle=140
        )
        assert cover.coverage_pivot_percentage() == pytest.approx(9000 / 140)
        assert (
            TiltPolicy.climate_tilt_percentage(
                angle_deg=angle_deg, mode=mode, sun_through=sun_through
            )
            == before
        )
        assert (
            TiltPolicy.climate_tilt_percentage(
                angle_deg=angle_deg, mode=mode, sun_through=sun_through, cover=cover
            )
            == after
        )

    @pytest.mark.unit
    @pytest.mark.parametrize(("mode", "before"), [("mode1", 89), ("mode2", 44)])
    def test_unset_max_slat_angle_leaves_the_preset_scale_alone(self, mode, before):
        """The ``0`` sentinel is not a scale — a plain pergola is unchanged."""
        from custom_components.adaptive_cover_pro.cover_types.tilt import TiltPolicy

        cover = build_louvered_roof_cover(
            sol_azi=180.0, sol_elev=45.0, roof_pitch=0.0, mode=mode, max_slat_angle=0
        )
        assert (
            TiltPolicy.climate_tilt_percentage(angle_deg=80.0, mode=mode, cover=cover)
            == before
        )


class TestClimateTiltPercentageStaysInRange:
    """``climate_tilt_percentage`` honours its own 0–100 contract (#1222 audit)."""

    @pytest.mark.unit
    def test_a_rescaled_mirror_cannot_overshoot_the_top_of_the_scale(self):
        """``max_slat_angle = 100`` puts the mirrored winter angle past 100 %.

        Horizontal sits at 90 % of a 100° drive, which is strictly interior, so
        winter heating mirrors a 30° profile angle to 120° — 120 % of the
        travel. The drive cannot go there; the reachable answer is its top end.
        """
        from custom_components.adaptive_cover_pro.cover_types.tilt import TiltPolicy

        cover = build_louvered_roof_cover(
            sol_azi=180.0,
            sol_elev=45.0,
            roof_pitch=0.0,
            mode="mode2",
            max_slat_angle=100,
        )
        assert cover._percentage_from_angle(120.0) == pytest.approx(120.0)
        assert cover.climate_tilt_percentage(30.0, sun_through=True) == 100
        assert (
            TiltPolicy.climate_tilt_percentage(
                angle_deg=30.0, mode="mode2", sun_through=True, cover=cover
            )
            == 100
        )

    @pytest.mark.unit
    def test_a_one_sided_calibration_cannot_undershoot_the_bottom(self):
        """A 100°/170° pair never reaches 80°, so the target images negative.

        ``specify_angles`` accepts a calibration entirely above horizontal, and
        the map is deliberately unclamped so an off-travel pivot keeps ordering
        correctly. The climate percentage is a COMMAND, though, and −29 % is not
        one.
        """
        from custom_components.adaptive_cover_pro.cover_types.tilt import TiltPolicy

        cover = _calibrated_tilt(angle_0=100.0, angle_100=170.0)
        assert cover._percentage_from_angle(80.0) == pytest.approx(-200 / 7)
        assert cover.climate_tilt_percentage(80.0) == 0
        assert (
            TiltPolicy.climate_tilt_percentage(
                angle_deg=80.0, mode="specify_angles", cover=cover
            )
            == 0
        )
