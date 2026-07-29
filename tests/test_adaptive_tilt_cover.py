"""Tests for AdaptiveTiltCover calculations and tilt configuration service."""

import math

import pytest
import numpy as np
from unittest.mock import MagicMock

from tests.cover_helpers import build_tilt_cover

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
