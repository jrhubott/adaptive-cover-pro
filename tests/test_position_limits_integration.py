"""Integration tests for min/max position limits through the pipeline.

Tests the full chain: cover config limits → pipeline snapshot config →
apply_snapshot_limits() in handlers → correct clamped position in PipelineResult.

Covers:
- Step 19: Solar position clamped by min_position
- Step 20: Solar position clamped by max_position
- Step 21: Sun-only min limit NOT applied to default position
- Step 22: Sun-only min limit IS applied during solar tracking
- Step 23: Climate handler respects position limits
"""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
    ClimateHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
    DefaultHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.solar import SolarHandler
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import (
    ClimateOptions,
    PipelineSnapshot,
)
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings

from tests.test_pipeline.conftest import make_snapshot, _make_mock_cover

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config_with_limits(
    *,
    min_pos: int | None = None,
    max_pos: int | None = None,
    min_pos_sun_only: bool = False,
    max_pos_sun_only: bool = False,
):
    """Build a mock cover config with specific position limits."""
    config = MagicMock()
    config.min_pos = min_pos
    config.max_pos = max_pos
    config.min_pos_sun_only = min_pos_sun_only
    config.max_pos_sun_only = max_pos_sun_only
    return config


def _snap_with_solar(
    *,
    calculate_return: float,
    min_pos: int | None = None,
    max_pos: int | None = None,
    min_pos_sun_only: bool = False,
    max_pos_sun_only: bool = False,
    direct_sun_valid: bool = True,
    default_position: int = 50,
) -> PipelineSnapshot:
    """Build a snapshot with a solar-valid cover and the given limits."""
    config = _config_with_limits(
        min_pos=min_pos,
        max_pos=max_pos,
        min_pos_sun_only=min_pos_sun_only,
        max_pos_sun_only=max_pos_sun_only,
    )
    cover = _make_mock_cover(
        direct_sun_valid=direct_sun_valid,
        calculate_percentage_return=calculate_return,
        config=config,
    )
    return make_snapshot(
        cover=cover,
        default_position=default_position,
        direct_sun_valid=direct_sun_valid,
    )


def _registry_solar_default():
    return PipelineRegistry([SolarHandler(), DefaultHandler()])


# ---------------------------------------------------------------------------
# Step 19: Solar position clamped by min_position
# ---------------------------------------------------------------------------


class TestSolarClampedByMinPosition:
    """When solar calculation falls below min_position, the result is clamped up."""

    def test_solar_below_min_clamped_to_min(self):
        """Solar returns 20%, min_pos=30% → final position is 30%."""
        snap = _snap_with_solar(
            calculate_return=20.0,
            min_pos=30,
            min_pos_sun_only=False,  # always apply
        )
        registry = _registry_solar_default()
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.SOLAR
        assert result.position == 30

    def test_solar_above_min_not_clamped(self):
        """Solar returns 60%, min_pos=30% → position passes through at 60%."""
        snap = _snap_with_solar(
            calculate_return=60.0,
            min_pos=30,
            min_pos_sun_only=False,
        )
        registry = _registry_solar_default()
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.SOLAR
        assert result.position == 60

    def test_solar_at_min_boundary_not_clamped(self):
        """Solar exactly at min_pos passes through unchanged."""
        snap = _snap_with_solar(
            calculate_return=30.0,
            min_pos=30,
            min_pos_sun_only=False,
        )
        registry = _registry_solar_default()
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.SOLAR
        assert result.position == 30


# ---------------------------------------------------------------------------
# Step 20: Solar position clamped by max_position
# ---------------------------------------------------------------------------


class TestSolarClampedByMaxPosition:
    """When solar calculation exceeds max_position, the result is clamped down."""

    def test_solar_above_max_clamped_to_max(self):
        """Solar returns 90%, max_pos=80% → final position is 80%."""
        snap = _snap_with_solar(
            calculate_return=90.0,
            max_pos=80,
            max_pos_sun_only=False,
        )
        registry = _registry_solar_default()
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.SOLAR
        assert result.position == 80

    def test_solar_below_max_not_clamped(self):
        """Solar returns 60%, max_pos=80% → position passes through at 60%."""
        snap = _snap_with_solar(
            calculate_return=60.0,
            max_pos=80,
            max_pos_sun_only=False,
        )
        registry = _registry_solar_default()
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.SOLAR
        assert result.position == 60

    def test_both_min_and_max_active(self):
        """Solar returns 95%, min=20%, max=80% → clamped to 80%."""
        snap = _snap_with_solar(
            calculate_return=95.0,
            min_pos=20,
            max_pos=80,
            min_pos_sun_only=False,
            max_pos_sun_only=False,
        )
        registry = _registry_solar_default()
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.SOLAR
        assert result.position == 80


# ---------------------------------------------------------------------------
# Step 21: Sun-only limits NOT applied to default
# ---------------------------------------------------------------------------


class TestSunOnlyLimitsNotAppliedToDefault:
    """When min_pos_sun_only=True, the min limit is NOT enforced on default position."""

    def test_default_below_min_not_clamped_when_sun_only(self):
        """Default 10%, min_pos=30% with sun_only=True → default returns 10% (no clamp)."""
        # sun not in FOV → default handler fires, sun_valid=False
        snap = _snap_with_solar(
            calculate_return=50.0,
            min_pos=30,
            min_pos_sun_only=True,  # ← only apply during direct sun tracking
            direct_sun_valid=False,
            default_position=10,
        )
        registry = _registry_solar_default()
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.DEFAULT
        assert result.position == 10  # NOT clamped to 30 — sun-only limit

    def test_max_sun_only_not_applied_to_default(self):
        """Default 90%, max_pos=80% with sun_only=True → default returns 90% (no clamp)."""
        snap = _snap_with_solar(
            calculate_return=50.0,
            max_pos=80,
            max_pos_sun_only=True,
            direct_sun_valid=False,
            default_position=90,
        )
        registry = _registry_solar_default()
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.DEFAULT
        assert result.position == 90  # NOT clamped to 80


# ---------------------------------------------------------------------------
# Step 22: Sun-only limits applied during solar tracking
# ---------------------------------------------------------------------------


class TestSunOnlyLimitsAppliedDuringSolarTracking:
    """When min_pos_sun_only=True and sun is valid, the limit IS enforced."""

    def test_min_sun_only_applied_when_sun_valid(self):
        """Solar 20%, min_pos=30% with sun_only=True → clamped to 30% when sun valid."""
        snap = _snap_with_solar(
            calculate_return=20.0,
            min_pos=30,
            min_pos_sun_only=True,  # only during sun tracking
            direct_sun_valid=True,
        )
        registry = _registry_solar_default()
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.SOLAR
        assert result.position == 30  # clamped to min_pos

    def test_max_sun_only_applied_when_sun_valid(self):
        """Solar 90%, max_pos=80% with sun_only=True → clamped to 80% when sun valid."""
        snap = _snap_with_solar(
            calculate_return=90.0,
            max_pos=80,
            max_pos_sun_only=True,
            direct_sun_valid=True,
        )
        registry = _registry_solar_default()
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.SOLAR
        assert result.position == 80

    def test_sun_only_limit_vs_no_sun_only_same_solar(self):
        """With solar valid, sun_only and always-apply produce the same result."""
        for sun_only in [True, False]:
            snap = _snap_with_solar(
                calculate_return=15.0,
                min_pos=25,
                min_pos_sun_only=sun_only,
                direct_sun_valid=True,
            )
            result = _registry_solar_default().evaluate(snap)
            assert (
                result.position == 25
            ), f"sun_only={sun_only} should clamp to 25 when sun is valid"


# ---------------------------------------------------------------------------
# Step 23: Climate handler respects position limits
# ---------------------------------------------------------------------------


class TestClimateHandlerRespectsPositionLimits:
    """ClimateHandler's winter/summer positions are also limited by config limits."""

    def _climate_snap_with_limits(
        self,
        *,
        inside_temperature: float,
        max_pos: int | None = None,
        min_pos: int | None = None,
        max_pos_sun_only: bool = False,
        min_pos_sun_only: bool = False,
    ) -> PipelineSnapshot:
        """Build a snapshot for climate mode with position limits."""
        config = _config_with_limits(
            min_pos=min_pos,
            max_pos=max_pos,
            min_pos_sun_only=min_pos_sun_only,
            max_pos_sun_only=max_pos_sun_only,
        )
        cover = _make_mock_cover(
            direct_sun_valid=True,
            calculate_percentage_return=50.0,
            config=config,
        )
        readings = ClimateReadings(
            outside_temperature=None,
            inside_temperature=inside_temperature,
            is_presence=True,
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        options = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=False,
            temp_summer_outside=None,
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        )
        return make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=readings,
            climate_options=options,
        )

    def test_winter_open_100_clamped_by_max_pos(self):
        """Winter strategy opens to 100%, but max_pos=80% → clamped to 80%."""
        snap = self._climate_snap_with_limits(
            inside_temperature=10.0,  # below temp_low=18 → winter heating
            max_pos=80,
            max_pos_sun_only=False,
        )
        registry = PipelineRegistry(
            [ClimateHandler(), SolarHandler(), DefaultHandler()]
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.WINTER
        assert result.position == 80  # clamped from 100 to 80

    def test_summer_close_0_clamped_by_min_pos(self):
        """Summer cooling closes to 0%; min_pos=20% clamps the result up to 20%.

        Summer COOLING (no presence → opaque blind closes fully) sends 0%.
        When min_pos=20% is configured without sun_only, it applies during
        all modes and clamps the summer position from 0% to 20%.
        """
        config = _config_with_limits(
            min_pos=20,
            min_pos_sun_only=False,
        )
        cover = _make_mock_cover(
            direct_sun_valid=True,
            calculate_percentage_return=50.0,
            config=config,
        )
        readings = ClimateReadings(
            outside_temperature=None,
            inside_temperature=30.0,  # above temp_high=26
            is_presence=False,  # no presence → close fully
            is_sunny=True,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        )
        options = ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=False,
            temp_summer_outside=None,
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        )
        snap = make_snapshot(
            cover=cover,
            climate_mode_enabled=True,
            climate_readings=readings,
            climate_options=options,
        )
        registry = PipelineRegistry(
            [ClimateHandler(), SolarHandler(), DefaultHandler()]
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.SUMMER
        # Summer closes to 0%, but min_pos=20% clamps it up
        assert result.position == 20

    def test_winter_without_limits_opens_full_100(self):
        """Winter strategy without limits correctly opens to 100%."""
        snap = self._climate_snap_with_limits(
            inside_temperature=10.0,
            max_pos=None,  # no limit
        )
        registry = PipelineRegistry(
            [ClimateHandler(), SolarHandler(), DefaultHandler()]
        )
        result = registry.evaluate(snap)

        assert result.control_method == ControlMethod.WINTER
        assert result.position == 100
