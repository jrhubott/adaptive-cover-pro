"""Tests for AdaptiveTiltCover calculations and tilt configuration service."""

import math

import pytest
import numpy as np
from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.engine.covers.tilt import (
    constrain_reflected_beam,
    reflected_beam_elevation,
    slat_cutoff_angle,
)
from tests.cover_helpers import build_louvered_roof_cover, build_tilt_cover

# Window azimuth every ``_tilt_at`` cover faces, so a test can place the sun by
# surface-solar azimuth (``gamma``) rather than restating the facade orientation.
_WIN_AZI = 180


def _tilt_at(
    *,
    sol_azi,
    sol_elev,
    slat_distance,
    depth,
    mode,
    safety_margin=0.0,
    min_reflected_elevation=0.0,
):
    """Build an AdaptiveTiltCover at an explicit sun/slat geometry.

    Wide FOV so the sun is always "in front"; only the grazing-angle math is
    exercised. ``safety_margin`` threads the configurable venetian tilt margin
    (issue #783) through to ``TiltConfig``; ``min_reflected_elevation`` threads
    the reflected-beam floor (issue #1282), whose ``0`` is the disabled
    sentinel.
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
        min_reflected_elevation=min_reflected_elevation,
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


def test_get_tilt_data_reads_min_reflected_elevation():
    """The reflected-beam floor reaches the engine (#1282)."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_TILT_MIN_REFLECTED_ELEVATION,
    )

    result = _tilt_config_service().get_tilt_data(
        {
            "slat_distance": 7.5,
            "slat_depth": 8.0,
            "tilt_mode": "mode2",
            CONF_TILT_MIN_REFLECTED_ELEVATION: 30,
        }
    )

    assert result.min_reflected_elevation == 30.0


def test_get_tilt_data_defaults_min_reflected_elevation_to_disabled():
    """An entry written before the option existed reads as the 0 sentinel."""
    from custom_components.adaptive_cover_pro.const import (
        DEFAULT_TILT_MIN_REFLECTED_ELEVATION,
    )

    result = _tilt_config_service().get_tilt_data(
        {
            "slat_distance": 7.5,
            "slat_depth": 8.0,
            "tilt_mode": "mode2",
        }
    )

    assert result.min_reflected_elevation == DEFAULT_TILT_MIN_REFLECTED_ELEVATION == 0


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
        """``pct → angle → pct`` is the identity on every PHYSICAL scale.

        "Physical" is the scope, and it is narrower than the option ranges:
        every calibration parametrised here keeps both endpoints inside the
        0–180° slat range, which is where the forward map's
        ``_specified_target_angle`` clamp is inert and the two directions are
        genuine inverses. ``_RANGE_TILT_ANGLE_0``/``_RANGE_TILT_ANGLE_100``
        admit endpoints outside that range, and there the identity stops
        holding — deliberately, and pinned next door by
        ``test_the_inverse_stops_inverting_outside_the_physical_angle_range``.
        """
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

    @pytest.mark.unit
    def test_the_inverse_stops_inverting_outside_the_physical_angle_range(self):
        """The two directions are inverses only inside 0–180° (#1222 audit).

        ``_RANGE_TILT_ANGLE_100`` runs to 360° and ``_RANGE_TILT_ANGLE_0`` down
        to −180°, while ``hinge_is_usable`` asks only that the pair straddle
        horizontal. A ``0/200`` calibration therefore stores and hinges fine —
        and is the one place the round trip breaks, because only the FORWARD
        direction clamps: ``_specified_target_angle`` pins its input into the
        physical 0–180° slat range before mapping, so the upper segment tops out
        at 90.9 %, while the inverse answers what the scale literally says and
        hands back the configured 200°.

        Characterization, not a defect report. CLAMPING the inverse is the wrong
        repair twice over: it would not restore the identity
        (``forward(180)`` is still 90.9 %, not 100 %), and it would flatten
        ``coverage_distance`` across every percentage that images outside the
        physical range — see
        ``test_clamping_the_inverse_would_flatten_a_calibration_below_zero``
        next door for a calibration where that flattening changes the
        comparator's answer. The honest statement is that a calibration outside
        0–180° is outside the map's domain, so it is pinned rather than papered
        over.
        """
        from custom_components.adaptive_cover_pro.const import TILT_HORIZONTAL_DEG

        cover = _calibrated_tilt(angle_0=0.0, angle_100=200.0, horizontal_percent=50.0)
        assert cover._hinge_percent() == 50.0

        # The inverse reports the configured endpoint verbatim.
        assert cover._angle_from_percentage(100.0) == pytest.approx(200.0)
        # The forward map cannot get back — nor even reach 100 % at all.
        assert cover._percentage_from_angle(200.0) == pytest.approx(1000 / 11)
        assert cover._percentage_from_angle(180.0) == pytest.approx(1000 / 11)
        # So the ordering metric reads 110° off horizontal for a slat the
        # forward map caps at 90° off.
        assert cover.coverage_distance(100) == pytest.approx(110.0)
        assert cover._specified_target_angle(200.0) == TILT_HORIZONTAL_DEG + 90.0

    @pytest.mark.unit
    def test_clamping_the_inverse_would_flatten_a_calibration_below_zero(self):
        """Why the inverse is left unclamped, on a calibration that proves it.

        ``_RANGE_TILT_ANGLE_0`` reaches −180° and ``_RANGE_TILT_ANGLE_100``
        reaches 360°, and the only ordering rule either config surface applies
        to the pair is ``angle_0 < angle_100`` — so a −180°/360° calibration
        stores. Its forward map, clamped into the physical 0–180° slat range,
        occupies only the middle third of the percentage scale: 0° is 33.3 %
        and 180° is 66.7 %. Every percentage below and above that images
        outside the physical range, and the inverse says so — 0 % is −180°, a
        full 270° off horizontal, and 100 % is 360°.

        Clamping the inverse into 0–180° would read all of them as one of the
        two rails and hand back a constant 90°. Below 33 % that costs the
        metric its resolution; above 67 % it changes the answer, and this test
        asserts that case, because 70 % (198°) and 90 % (306°) tie under a
        clamp and fall through to the axis rule, which commands the more open
        of the two.

        The earlier parametrisations of
        ``test_off_travel_pivot_still_orders_the_comparator`` cannot show this:
        ``0/45`` and ``120/180`` map every percentage they rank inside 0–180°,
        so a clamp leaves them green and they are not what protects this
        decision.
        """
        from custom_components.adaptive_cover_pro.cover_types import get_policy

        cover = _calibrated_tilt(angle_0=-180.0, angle_100=360.0)
        assert cover._percentage_from_angle(0.0) == pytest.approx(100 / 3)
        assert cover._percentage_from_angle(180.0) == pytest.approx(200 / 3)

        below = [cover.coverage_distance(pct) for pct in (0, 10, 20, 33)]
        assert below == pytest.approx([270.0, 216.0, 162.0, 91.8])
        assert below == sorted(below, reverse=True)
        assert len(set(below)) == len(below)

        assert cover.coverage_distance(70) == pytest.approx(108.0)
        assert cover.coverage_distance(90) == pytest.approx(216.0)
        policy = get_policy("cover_tilt")
        assert policy.more_protective_position(70, 90, cover=cover) == 90
        assert policy.more_protective_position(90, 70, cover=cover) == 90
        # The answer a clamped inverse would produce, via the axis rule.
        assert policy.more_protective_position(70, 90) == 70

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

        The key carries the ``_pct`` suffix ``const.py`` reserves for "percent
        (0-100)" — the same suffix rule that turns the two endpoint angles into
        ``*_deg`` — because ``DiagnosticsBuilder._round_trace_value`` reads that
        suffix to decide the presentation rounding. Without it the mid-point
        would be filed as a unit-less ratio and surface as ``50.0`` where every
        other percentage in the trace surfaces as ``50``.
        """
        cover = _calibrated_tilt(
            angle_0=0.0, angle_100=130.0, horizontal_percent=hp, sol_elev=30.0
        )
        cover.calculate_percentage()

        trace = cover._last_calc_details
        assert trace["tilt_angle_0_deg"] == 0.0
        assert trace["tilt_angle_100_deg"] == 130.0
        assert trace["tilt_horizontal_pct"] == hp


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
    def test_a_one_sided_calibration_answers_a_blocking_target_by_closing(self):
        """A 100°/170° pair never reaches 80°, so the target images negative.

        ``specify_angles`` accepts a calibration entirely above horizontal, and
        the map is deliberately unclamped so an off-travel pivot keeps ordering
        correctly. The climate percentage is a COMMAND, though, and −29 % is not
        one — so the answer has to be pinned onto the scale.

        WHICH end it is pinned to is the point. A plain nearest-value clamp
        answers 0 %, and on this calibration 0 % is 100° — the most OPEN slat the
        drive has, ten degrees off horizontal. The request was
        ``CLIMATE_DEFAULT_TILT_ANGLE``, a block-the-sun intent, so answering it
        with maximum openness is the same defect this test used to enshrine
        (#1222 audit). 100 % is 170°, eighty degrees off horizontal: the most
        protective slat the drive can actually reach, and the honest answer to a
        request it cannot meet exactly.
        """
        from custom_components.adaptive_cover_pro.cover_types.tilt import TiltPolicy

        cover = _calibrated_tilt(angle_0=100.0, angle_100=170.0)
        assert cover._percentage_from_angle(80.0) == pytest.approx(-200 / 7)
        # The two reachable ends, measured the way coverage is measured.
        assert cover.coverage_distance(0.0) == pytest.approx(10.0)
        assert cover.coverage_distance(100.0) == pytest.approx(80.0)
        assert cover.climate_tilt_percentage(80.0) == 100
        assert (
            TiltPolicy.climate_tilt_percentage(
                angle_deg=80.0, mode="specify_angles", cover=cover
            )
            == 100
        )

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("angle_deg", "mode", "sun_through", "unclamped", "expected"),
        [
            # MODE1 divides by 90°, so anything past horizontal overshoots.
            (200.0, "mode1", False, 222, 100),
            (-45.0, "mode1", False, -50, 0),
            # MODE2 divides by 180° and its winter mirror adds 90° first, so a
            # profile angle past 90° overshoots the same way.
            (120.0, "mode2", True, 117, 100),
            (200.0, "mode2", False, 111, 100),
            (-30.0, "mode2", False, -17, 0),
        ],
    )
    def test_the_engine_less_fallback_keeps_the_contract_too(
        self, angle_deg, mode, sun_through, unclamped, expected
    ):
        """The degraded path owes the same 0–100 promise as the engine path.

        The two branches above cover the engine; these cover the mode-based
        fallback, whose own ``clamp_to_percentage_scale`` calls were otherwise
        unpinned — mutating both to the identity failed nothing in the suite.
        They are defensive rather than live: the production callers feed it
        ``CLIMATE_SUMMER_TILT_ANGLE`` (45° → 50 %) and
        ``CLIMATE_DEFAULT_TILT_ANGLE`` (80° → 89 %), and the only variable
        input, the MODE2 winter mirror's profile angle, stays inside
        ``(−90°, 90°)`` and so maps inside the scale. The public static method
        takes any float, though, and ``unclamped`` is what each case would
        answer without the clamp — a percentage no cover entity accepts.
        """
        from custom_components.adaptive_cover_pro.cover_types.tilt import TiltPolicy

        max_degrees = 180.0 if mode == "mode2" else 90.0
        mirrored = angle_deg + (90.0 if (sun_through and mode == "mode2") else 0.0)
        assert round(mirrored / max_degrees * 100) == unclamped

        assert (
            TiltPolicy.climate_tilt_percentage(
                angle_deg=angle_deg, mode=mode, sun_through=sun_through
            )
            == expected
        )


# ---------------------------------------------------------------------------
# An unreachable climate target is answered by INTENT, not by proximity (#1222)
# ---------------------------------------------------------------------------
# ``specify_angles`` accepts any ordered pair, including one that lies wholly at
# or above horizontal, and the climate rules ask for fixed angles that lie
# BELOW it (``CLIMATE_SUMMER_TILT_ANGLE`` 45°, ``CLIMATE_DEFAULT_TILT_ANGLE``
# 80°). On those pairs maximum openness sits between the target and the WHOLE
# reachable travel: no reachable slat closes the way the rule asked, and the
# nearest end is the one nearest horizontal — the least-blocking slat the drive
# has. Every one of these is a block-the-sun request, and occlusion is symmetric
# about horizontal, so the intent still has a reachable expression: the end
# FARTHER from horizontal.
#
# This is the ONLY situation that leaves the clamp. A drive on the same side of
# horizontal as the target closes the way the rule asked and merely runs short,
# and keeps its nearest end — see
# ``TestADriveShortOfTheTargetKeepsItsOwnEnd`` below.
#
# ``angle_0``, ``angle_100``, the climate rule's target angle, and the
# percentage the engine's map images it at. Both rules are live in production —
# ``_tilt_summer`` under ``TILT_WITH_PRESENCE`` and ``_tilt_default`` as the
# GLARE_CONTROL catch-all in both tilt tables.
_OFF_SCALE_BLOCKING_TARGETS = [
    # 90/180 — slats run horizontal → closed upward. 0 % IS horizontal.
    (90.0, 180.0, 45.0, -50.0, 180.0),
    (90.0, 180.0, 80.0, -100 / 9, 180.0),
    # 100/170 and 120/180 — calibrated entirely above horizontal.
    (100.0, 170.0, 80.0, -200 / 7, 170.0),
    (120.0, 180.0, 80.0, -200 / 3, 180.0),
]


class TestAnUnreachableClimateTargetServesTheIntent:
    """Off-scale climate targets pin to the intent's end, not the nearest."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("angle_0", "angle_100", "angle_deg", "raw_pct", "closing_angle"),
        _OFF_SCALE_BLOCKING_TARGETS,
    )
    def test_a_blocking_target_below_the_scale_closes_instead_of_opening(
        self, angle_0, angle_100, angle_deg, raw_pct, closing_angle
    ):
        """The four calibrations a nearest-value clamp answered wide open.

        Each row asserts the mechanism as well as the answer: the map really
        does image the target below 0 %, 100 % really is the end farther from
        horizontal on that calibration, and that is the percentage commanded.
        """
        from custom_components.adaptive_cover_pro.const import (
            CLIMATE_DEFAULT_TILT_ANGLE,
            CLIMATE_SUMMER_TILT_ANGLE,
        )
        from custom_components.adaptive_cover_pro.cover_types.tilt import TiltPolicy

        assert angle_deg in (CLIMATE_SUMMER_TILT_ANGLE, CLIMATE_DEFAULT_TILT_ANGLE)

        cover = _calibrated_tilt(angle_0=angle_0, angle_100=angle_100)
        assert cover._percentage_from_angle(angle_deg) == pytest.approx(raw_pct)
        assert cover._angle_from_percentage(100.0) == pytest.approx(closing_angle)
        assert cover.coverage_distance(100.0) > cover.coverage_distance(0.0)

        assert cover.climate_tilt_percentage(angle_deg) == 100
        assert (
            TiltPolicy.climate_tilt_percentage(
                angle_deg=angle_deg, mode="specify_angles", cover=cover
            )
            == 100
        )

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("horizontal_percent", "angle_deg", "expected"),
        [(None, 45.0, 35), (None, 80.0, 62), (50.0, 45.0, 25), (50.0, 80.0, 44)],
    )
    def test_a_two_sided_calibration_is_untouched(
        self, horizontal_percent, angle_deg, expected
    ):
        """The reporter's own 0°/130° pair reaches both angles, so nothing pins.

        The pinning rule only ever fires on a target the scale cannot express,
        and this pair expresses both climate angles directly, hinge or no hinge
        — the ``0.0 <= raw <= 100.0`` assertion is what says so, and it is the
        precondition the expected values depend on.

        Deliberately narrower than "any calibration that straddles horizontal".
        Straddling is not sufficient: ``70/130`` straddles and still images
        ``CLIMATE_SUMMER_TILT_ANGLE`` at −41.7 %, because its lower endpoint
        stops twenty degrees short of the requested slat. What makes THIS pair
        immune is that its travel contains both requested angles, which is a
        property of the endpoints and the two constants together, not of the
        pivot's position.
        """
        from custom_components.adaptive_cover_pro.cover_types.tilt import TiltPolicy

        cover = _calibrated_tilt(
            angle_0=0.0, angle_100=130.0, horizontal_percent=horizontal_percent
        )
        raw = cover._percentage_from_angle(angle_deg)
        assert 0.0 <= raw <= 100.0
        assert cover.climate_tilt_percentage(angle_deg) == expected
        assert (
            TiltPolicy.climate_tilt_percentage(
                angle_deg=angle_deg, mode="specify_angles", cover=cover
            )
            == expected
        )

    @pytest.mark.unit
    def test_letting_the_sun_through_keeps_the_nearest_reachable_slat(self):
        """``sun_through`` never leaves the clamp, and here neither does blocking.

        A 20°/70° pair sits wholly BELOW horizontal, the same side as a 10°
        target, so both ends close the way a blocking rule asks and the drive
        merely runs short: 0 % is 20°, seventy degrees off horizontal, and the
        nearest reachable slat to the request. Blocking keeps it.

        ``sun_through`` keeps it too, and for a reason that does not depend on
        this scale. Winter heating wants the slat PARALLEL to the beam — the
        zero-occlusion orientation — and that angle has no mirror: reflecting it
        across horizontal does not transmit equally, it points somewhere else
        entirely. So there is no second reachable expression of the intent to
        fall back on, and the slat that transmits most is the one closest to the
        one asked for. Blocking has a mirror precisely because occlusion IS
        symmetric about horizontal, which is what ``coverage_distance``
        measures; that asymmetry between the two intents is the physics.

        Both halves are production-unreachable on THIS scale, and the docstring
        says so rather than leaving it to be rediscovered.
        ``_tilt_winter_mode2`` is the only rule that passes ``sun_through=True``
        and it is gated on ``is_tilt_mode2``, so a ``specify_angles`` cover
        never reaches the flag at all. (A MODE2 louvered roof does, and its
        mirrored target really can overshoot — see
        ``test_a_rescaled_mirror_cannot_overshoot_the_top_of_the_scale``, which
        clamps.) The blocking half is unreachable for a different reason: the
        climate rules only ever ask for 45° and 80°, both of which this pair
        images ON its scale. 10° is chosen so a single cover can carry both
        halves; the angles production does ask of a 20°/70° pair are pinned in
        ``TestADriveShortOfTheTargetKeepsItsOwnEnd``.

        The winter mirror is not in play here either — it is gated on the pivot
        being strictly interior and this scale images horizontal at 140 % — so
        the flag reaches ``_pin_climate_target`` with the raw angle.
        """
        cover = _calibrated_tilt(angle_0=20.0, angle_100=70.0)
        assert cover.coverage_pivot_percentage() == pytest.approx(140.0)
        assert cover._percentage_from_angle(10.0) == pytest.approx(-20.0)
        assert cover.coverage_distance(0.0) == pytest.approx(70.0)
        assert cover.coverage_distance(100.0) == pytest.approx(20.0)

        assert cover.climate_tilt_percentage(10.0) == 0
        assert cover.climate_tilt_percentage(10.0, sun_through=True) == 0

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("angle_deg", "raw_pct", "expected"),
        [(200.0, 1000 / 9, 100), (-30.0, -50 / 3, 0)],
    )
    def test_a_symmetric_scale_has_no_favoured_end_and_keeps_the_clamp(
        self, angle_deg, raw_pct, expected
    ):
        """MODE2's two ends are equally protective, so proximity still decides.

        Both rails of the shipped default are ninety degrees off horizontal, so
        neither serves a blocking request better than the other and there is no
        end for an intent to prefer. The clamp is what MODE2 has always done and
        keeps doing.

        Two independent things hold it there, and the test asserts the first so
        the second cannot be the only thing standing: the rails tie on
        ``coverage_distance``, AND horizontal sits at an interior 50 % with the
        whole overshoot outside the travel on one side of it, so the target
        never lies across maximum openness from the drive at all. An
        implementation that reached past the near end here would swing an
        overshoot 100 points across the scale.
        """
        cover = _calibrated_tilt(mode="mode2")
        assert cover.coverage_distance(0.0) == cover.coverage_distance(100.0) == 90.0
        assert cover._percentage_from_angle(angle_deg) == pytest.approx(raw_pct)
        assert cover.climate_tilt_percentage(angle_deg) == expected

    @pytest.mark.unit
    def test_mode1_stops_answering_an_overshoot_with_its_open_rail(self):
        """MODE1 inherits the intent rule, and its answer moves (#1222 audit).

        MODE1 runs 0° → 0 % (closed downward, ninety degrees off horizontal) to
        90° → 100 % (horizontal, wide open), so a blocking target past its
        travel used to clamp onto the open rail — the same shape as the
        calibrated cases above, on the preset scale. Nothing routes there in
        production: the climate rules ask for 45° and 80°, both inside the
        scale, and the winter mirror declines on MODE1 because its pivot is the
        100 % rail rather than an interior point. Pinned because the engine's
        public method takes any float and its answer changed.

        The engine-less fallback still clamps, because it has no scale to ask
        where coverage bottoms out — the divergence is the reason the production
        callers all pass the engine.
        """
        from custom_components.adaptive_cover_pro.cover_types.tilt import TiltPolicy

        cover = _calibrated_tilt(mode="mode1")
        assert cover.coverage_distance(0.0) == pytest.approx(90.0)
        assert cover.coverage_distance(100.0) == pytest.approx(0.0)
        assert cover._percentage_from_angle(200.0) == pytest.approx(2000 / 9)

        assert cover.climate_tilt_percentage(200.0) == 0
        assert TiltPolicy.climate_tilt_percentage(angle_deg=200.0, mode="mode1") == 100

    @pytest.mark.unit
    def test_an_inverted_calibration_needs_no_sign_test(self):
        """Reversing a calibration reverses the percentage, never the slat.

        The engine supports an inverted calibration even though both config
        surfaces reject one today (#749), and nothing in the pinning rule looks
        at which way the endpoints run: both the "is the target across maximum
        openness from the travel?" question and the coverage ranking are asked
        of the scale's own images, so the reversal is read for free.

        The check is a physical identity rather than a number. ``170/100`` is
        ``100/170`` written backwards — the same drive, the same two reachable
        slats, the percentages swapped — so the two calibrations must command
        the SAME ANGLE for the same request, at opposite percentages. The
        upright pair is one of the sanctioned rows above (100 % → 170°); this
        one has to answer 0 %, and 0 % has to be 170°.
        """
        upright = _calibrated_tilt(angle_0=100.0, angle_100=170.0)
        inverted = _calibrated_tilt(angle_0=170.0, angle_100=100.0)

        assert upright.climate_tilt_percentage(80.0) == 100
        assert inverted.climate_tilt_percentage(80.0) == 0
        assert upright._angle_from_percentage(100.0) == pytest.approx(
            inverted._angle_from_percentage(0.0)
        )

    @pytest.mark.unit
    def test_an_inverted_calibration_that_merely_runs_short_keeps_its_near_end(self):
        """A 140°/20° pair straddles horizontal, so it is not the crossing case.

        Its travel reaches 50° above horizontal and 70° below, and a 170° target
        asks for 80° above. The drive closes the way the rule asked and simply
        runs out of travel, so its nearest end — 0 %, at 140° — is the answer,
        the same way ``TestMode1ClampCrossover`` argues a solve past the rail
        stays at the rail rather than sweeping to the far endpoint.

        Asserted next to the reversal test because the two together are what
        say the rule reads the SCALE and not the sign: 100 % here is the more
        covering end (70° off horizontal against 50°) and still does not win.
        """
        cover = _calibrated_tilt(angle_0=140.0, angle_100=20.0)
        assert cover.coverage_distance(0.0) == pytest.approx(50.0)
        assert cover.coverage_distance(100.0) == pytest.approx(70.0)
        assert cover._percentage_from_angle(170.0) == pytest.approx(-25.0)
        assert cover.coverage_pivot_percentage() == pytest.approx(125 / 3)
        assert cover.climate_tilt_percentage(170.0) == 0


# ---------------------------------------------------------------------------
# A drive that runs SHORT of a climate target keeps its own end (#1222 audit)
# ---------------------------------------------------------------------------
# The mirror image of the calibrations above, and the case the intent rule got
# backwards. Both climate blocking angles sit BELOW horizontal
# (``CLIMATE_SUMMER_TILT_ANGLE`` 45°, ``CLIMATE_DEFAULT_TILT_ANGLE`` 80°), so a
# drive whose whole travel is also below horizontal closes exactly the way the
# rule asked — it simply runs out of travel before it gets there, and its top
# end is both the nearest reachable slat and the one closest to the requested
# angle. Ranking the two ends purely by coverage answers that with 0 %: the
# BOTTOM of the travel, a full sweep away and eighty degrees more closed than
# anything asked for.
#
# This is not confined to the opt-in three-point calibration. A louvered roof
# is one of only two cover types whose ``axes[0]`` is TILT, so a preset
# ``mode1``/``mode2`` pergola routes through these same tilt climate tables, and
# ``max_slat_angle`` — a polymorphic ``_effective_max_degrees`` override — puts
# the fixed 80° target off the top of its scale for every drive under 80°.

# ``max_slat_angle``, and the raw percentage ``CLIMATE_DEFAULT_TILT_ANGLE``
# images at on that drive. 80 is the crossover: exactly 100 %, the last value
# still on the scale.
_SHORT_LOUVERED_DRIVES = [
    (45, 8000 / 45),
    (60, 8000 / 60),
    (70, 8000 / 70),
    (79, 8000 / 79),
]

# ``angle_0``/``angle_100`` pairs calibrated wholly below horizontal, the climate
# angle asked of them, and the raw percentage it images at. Every one of them
# overshoots the TOP of its scale, so every one of them wants 100 %.
_SHORT_CALIBRATIONS = [
    (0.0, 45.0, 80.0, 8000 / 45),
    (0.0, 60.0, 80.0, 8000 / 60),
    (20.0, 70.0, 80.0, 120.0),
    (0.0, 30.0, 80.0, 8000 / 30),
    (0.0, 30.0, 45.0, 150.0),
]


class TestADriveShortOfTheTargetKeepsItsOwnEnd:
    """An off-scale target on the SAME side of horizontal clamps (#1222 audit)."""

    @pytest.mark.unit
    @pytest.mark.parametrize("mode", ["mode1", "mode2"])
    @pytest.mark.parametrize(("max_slat_angle", "raw_pct"), _SHORT_LOUVERED_DRIVES)
    def test_a_louvered_roof_under_eighty_degrees_opens_to_its_top_end(
        self, mode, max_slat_angle, raw_pct
    ):
        """A pergola that cannot reach 80° answers with the closest slat it has.

        The drive's whole travel is 0° (closed) to ``max_slat_angle``, all of it
        below horizontal, so ``CLIMATE_DEFAULT_TILT_ANGLE`` images past 100 %.
        Its top end is ``max_slat_angle`` — the nearest reachable slat to the
        request, and already MORE closed than the request. Its bottom end is 0°,
        fully closed, which is what ranking the ends by coverage alone picks.

        Both preset modes are asserted because ``max_slat_angle`` overrides the
        percentage denominator for either one, so the flip was never a MODE2
        speciality.
        """
        from custom_components.adaptive_cover_pro.const import (
            CLIMATE_DEFAULT_TILT_ANGLE,
        )
        from custom_components.adaptive_cover_pro.cover_types.tilt import TiltPolicy

        cover = build_louvered_roof_cover(
            sol_azi=180.0,
            sol_elev=45.0,
            roof_pitch=0.0,
            mode=mode,
            max_slat_angle=max_slat_angle,
        )
        assert cover._percentage_from_angle(
            CLIMATE_DEFAULT_TILT_ANGLE
        ) == pytest.approx(raw_pct)
        # The far end really is the more covering one — this is not a case where
        # coverage happens to agree; it is the case where it must not decide.
        assert cover.coverage_distance(0.0) > cover.coverage_distance(100.0)

        assert cover.climate_tilt_percentage(CLIMATE_DEFAULT_TILT_ANGLE) == 100
        assert (
            TiltPolicy.climate_tilt_percentage(
                angle_deg=CLIMATE_DEFAULT_TILT_ANGLE, mode=mode, cover=cover
            )
            == 100
        )

    @pytest.mark.unit
    def test_one_degree_of_travel_cannot_swing_the_command_across_the_scale(self):
        """79° and 80° of travel must not answer 0 % and 100 %.

        ``max_slat_angle = 80`` puts ``CLIMATE_DEFAULT_TILT_ANGLE`` exactly on
        the top rail, so it is the last value still on the scale and 79 is the
        first one off it. A rule that changes which END it answers at that
        boundary turns one degree of configured travel into a hundred points of
        commanded tilt.
        """
        from custom_components.adaptive_cover_pro.const import (
            CLIMATE_DEFAULT_TILT_ANGLE,
        )

        answers = {}
        for max_slat_angle in (78, 79, 80, 81):
            cover = build_louvered_roof_cover(
                sol_azi=180.0,
                sol_elev=45.0,
                roof_pitch=0.0,
                mode="mode1",
                max_slat_angle=max_slat_angle,
            )
            answers[max_slat_angle] = cover.climate_tilt_percentage(
                CLIMATE_DEFAULT_TILT_ANGLE
            )
        assert answers == {78: 100, 79: 100, 80: 100, 81: 99}

    @pytest.mark.unit
    def test_the_commanded_tilt_is_continuous_across_every_reachable_travel(self):
        """No adjacent pair of ``max_slat_angle`` values may jump the scale.

        Sweeps the whole configurable range a degree at a time and asserts the
        commanded tilt never moves more than a couple of points between
        neighbours. The band this protects is 30–90°, where the fixed 80° target
        crosses the top rail; the sweep runs past it on both sides so a future
        rule cannot move the discontinuity somewhere else instead of removing
        it.
        """
        from custom_components.adaptive_cover_pro.const import (
            CLIMATE_DEFAULT_TILT_ANGLE,
        )

        answers = [
            build_louvered_roof_cover(
                sol_azi=180.0,
                sol_elev=45.0,
                roof_pitch=0.0,
                mode="mode1",
                max_slat_angle=max_slat_angle,
            ).climate_tilt_percentage(CLIMATE_DEFAULT_TILT_ANGLE)
            for max_slat_angle in range(20, 181)
        ]
        steps = [abs(b - a) for a, b in zip(answers, answers[1:])]
        assert max(steps) <= 2, f"largest jump {max(steps)} in {answers}"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("angle_0", "angle_100", "angle_deg", "raw_pct"), _SHORT_CALIBRATIONS
    )
    def test_a_calibration_below_horizontal_closes_to_its_top_end(
        self, angle_0, angle_100, angle_deg, raw_pct
    ):
        """The ``specify_angles`` mirror of the pergola case, at both angles.

        A pair calibrated wholly below horizontal reaches neither climate angle
        that lies above its top endpoint, and the request always overshoots the
        TOP of the scale. ``0/45`` and ``0/60`` are ordinary short-travel
        venetians; ``20/70`` is the very pair
        ``test_letting_the_sun_through_keeps_the_nearest_reachable_slat`` uses,
        exercised here at the angles production actually asks for.
        """
        from custom_components.adaptive_cover_pro.const import (
            CLIMATE_DEFAULT_TILT_ANGLE,
            CLIMATE_SUMMER_TILT_ANGLE,
        )
        from custom_components.adaptive_cover_pro.cover_types.tilt import TiltPolicy

        assert angle_deg in (CLIMATE_SUMMER_TILT_ANGLE, CLIMATE_DEFAULT_TILT_ANGLE)

        cover = _calibrated_tilt(angle_0=angle_0, angle_100=angle_100)
        assert cover._percentage_from_angle(angle_deg) == pytest.approx(raw_pct)
        assert cover.coverage_distance(0.0) > cover.coverage_distance(100.0)

        assert cover.climate_tilt_percentage(angle_deg) == 100
        assert (
            TiltPolicy.climate_tilt_percentage(
                angle_deg=angle_deg, mode="specify_angles", cover=cover
            )
            == 100
        )

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("angle_0", "angle_100", "angle_deg", "expected"),
        [(0.0, 45.0, 45.0, 100), (0.0, 60.0, 45.0, 75), (20.0, 70.0, 45.0, 50)],
    )
    def test_the_summer_angle_still_lands_on_the_scale_where_it_fits(
        self, angle_0, angle_100, angle_deg, expected
    ):
        """The same pairs at 45°, where the target is still reachable.

        Asserted alongside the overshoot rows so the pair either side of each
        top rail is visible: ``0/45`` sits exactly ON it at 100 %, and one
        degree of extra travel moves the answer a point or two, not a hundred.
        """
        cover = _calibrated_tilt(angle_0=angle_0, angle_100=angle_100)
        raw = cover._percentage_from_angle(angle_deg)
        assert 0.0 <= raw <= 100.0
        assert cover.climate_tilt_percentage(angle_deg) == expected


class TestReflectedBeamHelpers:
    """Pure specular-reflection helpers beside the cut-off solve (issue #1282).

    Writing ``phi = 90 - code_angle`` (positive = outer slat edge DOWN), a beam
    arriving at profile angle ``beta`` leaves the slat's upper face at profile
    elevation ``theta_r = beta + 2*phi``. Above ``beta = arctan(slat_distance /
    depth)`` the cut-off solve asks for a past-horizontal, outer-edge-up slat,
    and that pose aims the reflection INTO the room.
    """

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("beta_deg", "slat_angle_deg"),
        [
            (58.7, 119.55),  # the reporter's WAREMA row (#1282 / #1086)
            (41.6, 87.1),
            (0.0, 90.0),
            (60.0, 122.0),
            (20.0, 48.2),
            (89.0, 178.0),
        ],
    )
    def test_reflected_beam_elevation_matches_the_closed_form(
        self, beta_deg: float, slat_angle_deg: float
    ) -> None:
        assert reflected_beam_elevation(beta_deg, slat_angle_deg) == pytest.approx(
            beta_deg + 2 * (90.0 - slat_angle_deg)
        )

    @pytest.mark.unit
    def test_reflected_beam_elevation_pins_the_reporters_row(self) -> None:
        """The daylight-optimal pose fires the beam essentially horizontally in."""
        assert reflected_beam_elevation(58.7, 119.55) == pytest.approx(-0.4, abs=0.05)

    @pytest.mark.unit
    @pytest.mark.parametrize("min_elevation_deg", [0, 0.0, -1, -30.0])
    def test_constrain_reflected_beam_is_identity_at_the_disabled_sentinel(
        self, min_elevation_deg: float
    ) -> None:
        """0 (and anything below it) is the disabled state — no arithmetic at all."""
        assert constrain_reflected_beam(119.55, 58.7, min_elevation_deg) == 119.55
        assert constrain_reflected_beam(12.25, 4.5, min_elevation_deg) == 12.25

    @pytest.mark.unit
    def test_constrain_reflected_beam_caps_the_reporters_cutoff(self) -> None:
        """30° of clearance turns the mirror away from eye level."""
        capped = constrain_reflected_beam(119.55, 58.7, 30)
        assert capped == pytest.approx(104.35, abs=0.01)
        assert reflected_beam_elevation(58.7, capped) == pytest.approx(30.0)

    @pytest.mark.unit
    def test_constrain_reflected_beam_can_close_below_horizontal(self) -> None:
        """A steep floor pulls the slat back PAST horizontal, not just to it."""
        capped = constrain_reflected_beam(84.10, 40, 60)
        assert capped == pytest.approx(80.0)
        assert reflected_beam_elevation(40, capped) == pytest.approx(60.0)

    @pytest.mark.unit
    def test_constrain_reflected_beam_never_opens_a_slat(self) -> None:
        """A floor the pose already clears leaves the angle untouched."""
        assert constrain_reflected_beam(48.2, 20, 30) == 48.2

    @pytest.mark.unit
    @pytest.mark.parametrize(("slat_distance", "depth"), [(0.075, 0.08), (0.02, 0.03)])
    def test_constrained_angle_never_opens_past_the_cutoff_and_still_blocks(
        self, slat_distance: float, depth: float
    ) -> None:
        """The clamp only ever CLOSES, and stays inside the blocking band.

        ``sin(phi + beta) >= r*cos(beta)`` is a BAND, not a half-line: the slat
        can be turned back toward — and past — horizontal without ever leaking
        direct sun. That is what makes a floor safe to apply on top of the
        cut-off rather than a trade against it.
        """
        ratio = slat_distance / depth
        for beta_deg in range(1, 90):
            beta_rad = math.radians(beta_deg)
            cutoff, _disc, negative = slat_cutoff_angle(beta_rad, slat_distance, depth)
            assert negative is False
            for min_elev in range(1, 91):
                capped = constrain_reflected_beam(cutoff, beta_deg, min_elev)
                assert capped <= cutoff + 1e-9
                phi = 90.0 - capped
                assert math.sin(math.radians(phi + beta_deg)) >= (
                    ratio * math.cos(beta_rad) - 1e-9
                ), f"beta={beta_deg} N={min_elev} leaks direct sun"


class TestReflectedBeamFloor:
    """The reflected-beam floor applied inside ``calculate_position`` (#1282).

    The reporter's WAREMA geometry (``depth 8.0 cm`` / ``slat_distance 7.5 cm``,
    ``r = 0.9375``) crosses ``beta = arctan(r) ~ 43.2`` for most of a south-facade
    day, above which the daylight-optimal cut-off is a past-horizontal,
    outer-edge-up slat whose upper face mirrors the beam into the room.
    """

    # The reporter's own diagnostics row from #1086, replayed through the engine.
    _REPORTER = {
        "sol_azi": 118.4,
        "sol_elev": 38,
        "slat_distance": 0.075,
        "depth": 0.08,
    }

    _SWEEP_AZI = (120, 150, 180, 210, 240)
    _SWEEP_ELEV = (5, 15, 25, 35, 45, 55, 65, 75, 85)
    _SWEEP_SLATS = ((0.02, 0.03), (0.075, 0.08), (0.05, 0.03))
    _SWEEP_MODES = ("mode1", "mode2", "specify_angles")

    @pytest.mark.unit
    @pytest.mark.parametrize("slat_distance,depth", _SWEEP_SLATS)
    @pytest.mark.parametrize("mode", _SWEEP_MODES)
    def test_disabled_option_is_byte_identical_at_every_geometry(
        self, slat_distance: float, depth: float, mode: str
    ) -> None:
        """THE acceptance gate: the 0 sentinel changes nothing, anywhere.

        Exact ``==`` rather than ``pytest.approx`` — the same standard
        ``safety_margin = 0.0`` is held to. A rearrangement that is only
        *approximately* a no-op still moves every existing install's slats.
        """
        for sol_azi in self._SWEEP_AZI:
            for sol_elev in self._SWEEP_ELEV:
                for safety_margin in (0.0, 1.0):
                    params = {
                        "sol_azi": sol_azi,
                        "sol_elev": sol_elev,
                        "slat_distance": slat_distance,
                        "depth": depth,
                        "mode": mode,
                        "safety_margin": safety_margin,
                    }
                    baseline = _tilt_at(**params)
                    sentinel = _tilt_at(**params, min_reflected_elevation=0)
                    assert (
                        sentinel.calculate_position() == baseline.calculate_position()
                    ), f"position moved at {params}"
                    assert (
                        sentinel.calculate_percentage()
                        == baseline.calculate_percentage()
                    ), f"percentage moved at {params}"

    @pytest.mark.unit
    def test_reporters_geometry_no_longer_mirrors_the_sun_into_the_room(self) -> None:
        """The headline case: -0.3° into the room becomes 30° above horizontal."""
        unconstrained = _tilt_at(**self._REPORTER, mode="mode2")
        angle = unconstrained.calculate_position()
        trace = unconstrained._last_calc_details
        assert angle == pytest.approx(119.49, abs=0.05)
        assert trace["reflected_beam_elevation_deg"] == pytest.approx(-0.32, abs=0.05)
        assert trace["reflected_beam_constrained"] is False

        floored = _tilt_at(**self._REPORTER, mode="mode2", min_reflected_elevation=30)
        angle = floored.calculate_position()
        trace = floored._last_calc_details
        assert angle == pytest.approx(104.33, abs=0.05)
        assert trace["reflected_beam_elevation_deg"] == pytest.approx(30.0)
        assert trace["reflected_beam_min_elevation_deg"] == 30.0
        assert trace["reflected_beam_constrained"] is True

    @pytest.mark.unit
    @pytest.mark.parametrize("min_elevation", [15, 30, 60, 90])
    @pytest.mark.parametrize("mode", _SWEEP_MODES)
    def test_floor_only_ever_closes(self, min_elevation: int, mode: str) -> None:
        """The floor is a cap: it can lower the slat angle, never raise it."""
        for slat_distance, depth in self._SWEEP_SLATS:
            for sol_azi in self._SWEEP_AZI:
                for sol_elev in self._SWEEP_ELEV:
                    params = {
                        "sol_azi": sol_azi,
                        "sol_elev": sol_elev,
                        "slat_distance": slat_distance,
                        "depth": depth,
                        "mode": mode,
                    }
                    open_angle = _tilt_at(**params).calculate_position()
                    floored = _tilt_at(
                        **params, min_reflected_elevation=min_elevation
                    ).calculate_position()
                    assert floored <= open_angle + 1e-9, f"{params} N={min_elevation}"

    @pytest.mark.unit
    def test_floor_runs_after_the_safety_margin(self) -> None:
        """Ordering is load-bearing, not incidental.

        The margin's ``result > 90`` branch scales the slat FURTHER past
        horizontal, so a floor applied before it would simply be undone (30°
        floor -> 104.35°, then eff_margin -> ~107°, reflection back down to
        ~24°). Applied after, the cap is idempotent and the floor is a true
        invariant.
        """
        floored = _tilt_at(
            **self._REPORTER,
            mode="mode2",
            safety_margin=1.0,
            min_reflected_elevation=30,
        )
        angle = floored.calculate_position()
        trace = floored._last_calc_details
        assert trace["safety_margin"] > 1.0  # the margin really did run
        assert angle == pytest.approx(104.33, abs=0.05)
        assert trace["reflected_beam_elevation_deg"] >= 30.0 - 1e-9

    @pytest.mark.unit
    def test_floor_acts_on_mode1_below_horizontal(self) -> None:
        """A steep floor pulls a MODE1 slat back below horizontal, not just to it."""
        cover = _tilt_at(
            sol_azi=180,
            sol_elev=40,
            slat_distance=0.075,
            depth=0.08,
            mode="mode1",
            min_reflected_elevation=60,
        )
        angle = cover.calculate_position()
        assert angle == pytest.approx(80.0, abs=0.05)
        assert cover._last_calc_details[
            "reflected_beam_elevation_deg"
        ] == pytest.approx(60.0)

    @pytest.mark.unit
    def test_floor_can_lower_a_horizontal_slat(self) -> None:
        """At beta ~ arctan(r) the cut-off is horizontal, and the floor still bites."""
        cover = _tilt_at(
            sol_azi=180,
            sol_elev=43.15,
            slat_distance=0.075,
            depth=0.08,
            mode="mode2",
            min_reflected_elevation=60,
        )
        assert cover.calculate_position() == pytest.approx(81.6, abs=0.05)

    @pytest.mark.unit
    def test_trace_publishes_reflected_beam_keys_at_the_sentinel(self) -> None:
        """A stable key set: the card reads it without probing (as for #1222)."""
        cover = _tilt_at(**self._REPORTER, mode="mode2")
        cover.calculate_position()
        trace = cover._last_calc_details
        assert trace["reflected_beam_min_elevation_deg"] == 0.0
        assert trace["reflected_beam_constrained"] is False
        assert trace["reflected_beam_elevation_deg"] is not None

        # Negative discriminant (slat_distance >> depth at a low sun) returns
        # 0° closed before any slat angle exists — so does the reflection.
        closed = _tilt_at(
            sol_azi=180, sol_elev=20, slat_distance=0.05, depth=0.03, mode="mode2"
        )
        assert closed.calculate_position() == 0.0
        closed_trace = closed._last_calc_details
        assert closed_trace["negative_discriminant"] is True
        assert closed_trace["reflected_beam_elevation_deg"] is None
        assert closed_trace["reflected_beam_min_elevation_deg"] == 0.0
        assert closed_trace["reflected_beam_constrained"] is False
