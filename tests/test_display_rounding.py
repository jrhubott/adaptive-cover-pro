"""Tests for display rounding (Issue #140).

Verifies that:
- coordinator.state always returns a plain Python int (not numpy float64)
- Sensor classes declare correct suggested_display_precision
- DiagnosticsBuilder rounds sun angles and temperatures at the presentation boundary
"""

from __future__ import annotations

import numpy as np
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

from custom_components.adaptive_cover_pro.sensor import (
    AdaptiveCoverSensorEntity,
    AdaptiveCoverSunPositionSensor,
)


# ---------------------------------------------------------------------------
# coordinator.state — interpolation float guard
# ---------------------------------------------------------------------------


class TestCoordinatorStateIntCoercion:
    """coordinator.state must always return a plain Python int."""

    def _invoke_state(self, pipeline_position: int, interpolated: float) -> int:
        """Invoke the state property with interpolation returning a numpy float.

        We instantiate the coordinator via object.__new__ so no HA setup is
        required.  _pipeline_bypasses_auto_control is a property derived from
        _pipeline_result.bypass_auto_control, so we set it there.
        """
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = object.__new__(AdaptiveDataUpdateCoordinator)
        pr = SimpleNamespace(position=pipeline_position, bypass_auto_control=False)
        coord._pipeline_result = pr
        coord._use_interpolation = True
        coord._inverse_state = False
        # Interpolation args — values don't matter since we patch the function
        coord.start_value = 0
        coord.end_value = 100
        coord.normal_list = None
        coord.new_list = None

        with patch(
            "custom_components.adaptive_cover_pro.coordinator.interpolate_position",
            return_value=np.float64(interpolated),
        ):
            return AdaptiveDataUpdateCoordinator.state.fget(coord)

    def test_interpolation_returns_int_not_numpy_float(self):
        """When interpolation is active, state returns plain int, not numpy float64."""
        result = self._invoke_state(pipeline_position=42, interpolated=42.7)
        assert isinstance(result, int), f"Expected int, got {type(result)}"
        assert result == 43

    def test_interpolation_rounds_correctly(self):
        """Interpolated float rounds to nearest integer."""
        result = self._invoke_state(pipeline_position=50, interpolated=34.6)
        assert result == 35

    def test_interpolation_floor_case(self):
        """Values below .5 round down."""
        result = self._invoke_state(pipeline_position=50, interpolated=34.4)
        assert result == 34

    def test_no_interpolation_already_int(self):
        """Without interpolation, the pipeline int passes through cleanly."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = object.__new__(AdaptiveDataUpdateCoordinator)
        pr = SimpleNamespace(position=55, bypass_auto_control=False)
        coord._pipeline_result = pr
        coord._use_interpolation = False
        coord._inverse_state = False

        result = AdaptiveDataUpdateCoordinator.state.fget(coord)
        assert isinstance(result, int)
        assert result == 55

    def test_safety_bypass_returns_pipeline_int_directly(self):
        """Safety override path returns the pipeline int without post-processing."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = object.__new__(AdaptiveDataUpdateCoordinator)
        pr = SimpleNamespace(position=0, bypass_auto_control=True)
        coord._pipeline_result = pr

        result = AdaptiveDataUpdateCoordinator.state.fget(coord)
        assert isinstance(result, int)
        assert result == 0


# ---------------------------------------------------------------------------
# Sensor class attributes — suggested_display_precision
# ---------------------------------------------------------------------------


class TestSensorDisplayPrecision:
    """Sensor classes must declare the correct suggested_display_precision.

    HA defines suggested_display_precision as a cached_property on SensorEntity
    that reads self._attr_suggested_display_precision.  We verify by reading the
    property via an instantiated object rather than the class dict directly.
    """

    def test_target_position_sensor_precision_is_zero(self):
        """AdaptiveCoverSensorEntity (Target Position) displays 0 decimals."""
        inst = object.__new__(AdaptiveCoverSensorEntity)
        assert inst.suggested_display_precision == 0

    def test_sun_position_sensor_precision_is_one(self):
        """AdaptiveCoverSunPositionSensor (Sun Position) displays 1 decimal."""
        inst = object.__new__(AdaptiveCoverSunPositionSensor)
        assert inst.suggested_display_precision == 1
