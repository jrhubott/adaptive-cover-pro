"""Tests for CustomPositionHandler."""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)

from .conftest import make_snapshot


@pytest.fixture()
def handler() -> CustomPositionHandler:
    """Return a CustomPositionHandler instance."""
    return CustomPositionHandler()


class TestHandlerMetadata:
    """Verify static handler properties."""

    def test_priority(self, handler: CustomPositionHandler) -> None:
        """Handler priority must be 77 (between manual override 80 and motion timeout 75)."""
        assert handler.priority == 77

    def test_name(self, handler: CustomPositionHandler) -> None:
        """Handler name must be 'custom_position'."""
        assert handler.name == "custom_position"


class TestEvaluateNoSensors:
    """Handler passes through when no sensors are configured."""

    def test_returns_none_when_list_empty(self, handler: CustomPositionHandler) -> None:
        """Returns None when custom_position_sensors is empty."""
        snapshot = make_snapshot(custom_position_sensors=[])
        assert handler.evaluate(snapshot) is None

    def test_describe_skip_not_configured(self, handler: CustomPositionHandler) -> None:
        """describe_skip reports 'not configured' when list is empty."""
        snapshot = make_snapshot(custom_position_sensors=[])
        assert handler.describe_skip(snapshot) == "custom positions not configured"


class TestEvaluateAllOff:
    """Handler passes through when all sensors are off."""

    def test_returns_none_when_all_off(self, handler: CustomPositionHandler) -> None:
        """Returns None when all sensors report is_on=False."""
        sensors = [
            ("binary_sensor.slot1", False, 30),
            ("binary_sensor.slot2", False, 60),
        ]
        snapshot = make_snapshot(custom_position_sensors=sensors)
        assert handler.evaluate(snapshot) is None

    def test_describe_skip_none_active(self, handler: CustomPositionHandler) -> None:
        """describe_skip reports 'no active sensor' when sensors are configured but all off."""
        sensors = [("binary_sensor.slot1", False, 50)]
        snapshot = make_snapshot(custom_position_sensors=sensors)
        assert handler.describe_skip(snapshot) == "no custom position sensor active"


class TestEvaluateSingleSensor:
    """Handler returns correct position for a single active sensor."""

    def test_sensor_on_returns_position(self, handler: CustomPositionHandler) -> None:
        """Returns configured position when the sensor is on."""
        sensors = [("binary_sensor.scene_a", True, 45)]
        snapshot = make_snapshot(custom_position_sensors=sensors)
        result = handler.evaluate(snapshot)
        assert result is not None
        assert result.position == 45

    def test_control_method(self, handler: CustomPositionHandler) -> None:
        """Result uses CUSTOM_POSITION control method."""
        sensors = [("binary_sensor.scene_a", True, 45)]
        snapshot = make_snapshot(custom_position_sensors=sensors)
        result = handler.evaluate(snapshot)
        assert result is not None
        assert result.control_method == ControlMethod.CUSTOM_POSITION

    def test_reason_contains_entity_and_position(
        self, handler: CustomPositionHandler
    ) -> None:
        """Reason string includes the entity_id and position."""
        sensors = [("binary_sensor.morning_scene", True, 70)]
        snapshot = make_snapshot(custom_position_sensors=sensors)
        result = handler.evaluate(snapshot)
        assert result is not None
        assert "binary_sensor.morning_scene" in result.reason
        assert "70%" in result.reason

    def test_position_zero_is_valid(self, handler: CustomPositionHandler) -> None:
        """Position 0% must not be skipped — it is a valid target (closed)."""
        sensors = [("binary_sensor.blackout", True, 0)]
        snapshot = make_snapshot(custom_position_sensors=sensors)
        result = handler.evaluate(snapshot)
        assert result is not None
        assert result.position == 0

    def test_position_one_hundred_is_valid(
        self, handler: CustomPositionHandler
    ) -> None:
        """Position 100% must not be skipped — it is a valid target (open)."""
        sensors = [("binary_sensor.open_all", True, 100)]
        snapshot = make_snapshot(custom_position_sensors=sensors)
        result = handler.evaluate(snapshot)
        assert result is not None
        assert result.position == 100


class TestEvaluatePriority:
    """First active sensor in the list wins."""

    def test_first_active_wins_over_second(
        self, handler: CustomPositionHandler
    ) -> None:
        """When sensor 1 and sensor 2 are both on, sensor 1 position is used."""
        sensors = [
            ("binary_sensor.slot1", True, 30),
            ("binary_sensor.slot2", True, 70),
        ]
        snapshot = make_snapshot(custom_position_sensors=sensors)
        result = handler.evaluate(snapshot)
        assert result is not None
        assert result.position == 30

    def test_skips_inactive_sensor_to_reach_active(
        self, handler: CustomPositionHandler
    ) -> None:
        """When sensor 1 is off and sensor 2 is on, sensor 2 position is used."""
        sensors = [
            ("binary_sensor.slot1", False, 30),
            ("binary_sensor.slot2", True, 70),
        ]
        snapshot = make_snapshot(custom_position_sensors=sensors)
        result = handler.evaluate(snapshot)
        assert result is not None
        assert result.position == 70

    def test_fourth_sensor_active_when_first_three_off(
        self, handler: CustomPositionHandler
    ) -> None:
        """Sensor 4 wins when sensors 1–3 are all off."""
        sensors = [
            ("binary_sensor.slot1", False, 10),
            ("binary_sensor.slot2", False, 20),
            ("binary_sensor.slot3", False, 30),
            ("binary_sensor.slot4", True, 80),
        ]
        snapshot = make_snapshot(custom_position_sensors=sensors)
        result = handler.evaluate(snapshot)
        assert result is not None
        assert result.position == 80
        assert "binary_sensor.slot4" in result.reason

    def test_all_four_sensors_on_first_wins(
        self, handler: CustomPositionHandler
    ) -> None:
        """When all four sensors are active, the first (slot 1) wins."""
        sensors = [
            ("binary_sensor.slot1", True, 10),
            ("binary_sensor.slot2", True, 20),
            ("binary_sensor.slot3", True, 30),
            ("binary_sensor.slot4", True, 40),
        ]
        snapshot = make_snapshot(custom_position_sensors=sensors)
        result = handler.evaluate(snapshot)
        assert result is not None
        assert result.position == 10


class TestRawCalculatedPosition:
    """raw_calculated_position is populated on a match."""

    def test_raw_calculated_position_set(
        self, handler: CustomPositionHandler
    ) -> None:
        """raw_calculated_position is provided from compute_raw_calculated_position."""
        sensors = [("binary_sensor.scene_a", True, 55)]
        snapshot = make_snapshot(
            custom_position_sensors=sensors,
            direct_sun_valid=True,
            calculate_percentage_return=42.0,
        )
        result = handler.evaluate(snapshot)
        assert result is not None
        # raw_calculated_position should be set (exact value depends on helpers)
        assert result.raw_calculated_position is not None
