"""Tests for AdaptiveTiltCover calculations and tilt configuration service."""

import pytest
import numpy as np
from unittest.mock import MagicMock

from tests.cover_helpers import build_tilt_cover


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
    def test_safety_margin_benign_angle_is_noop(self):
        """Where geo_margin == 1.0 (midday), strength 1.0 == strength 0.0."""
        params = {"sol_azi": 180, "sol_elev": 45, "slat_distance": 0.02, "depth": 0.03}
        a0 = _tilt_at(mode="mode2", safety_margin=0.0, **params).calculate_position()
        a1 = _tilt_at(mode="mode2", safety_margin=1.0, **params).calculate_position()
        assert a1 == a0

    @pytest.mark.unit
    def test_build_trace_includes_safety_margin(self):
        """_build_trace records the effective margin (diagnostics parity, #682)."""
        from custom_components.adaptive_cover_pro.geometry import (
            SafetyMarginCalculator,
        )

        c = _tilt_at(mode="mode1", safety_margin=1.0, **self._EXTREME)
        c.calculate_position()
        trace = c._last_calc_details
        assert "safety_margin" in trace
        geo_margin = SafetyMarginCalculator.calculate(c.gamma, c.sol_elev)
        assert trace["safety_margin"] == pytest.approx(geo_margin)
        assert trace["safety_margin"] > 1.0

    @pytest.mark.unit
    def test_build_trace_safety_margin_identity_default(self):
        """At safety_margin=0.0 the recorded effective margin is exactly 1.0."""
        c = _tilt_at(mode="mode1", safety_margin=0.0, **self._EXTREME)
        c.calculate_position()
        assert c._last_calc_details["safety_margin"] == 1.0


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
