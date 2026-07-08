"""Tests for AreaSensorResolver — cover area → indoor temp sensor (issue #786).

The resolver lives in the ``state/`` boundary and is the only place the
device/area registries are read for temperature resolution. Explicit config
always wins; the area's configured ``temperature_entity_id`` is the fallback.
"""

from unittest.mock import MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.state.area_resolver import (
    AreaSensorResolver,
    ResolvedSensor,
    SENSOR_SOURCE_AREA,
    SENSOR_SOURCE_EXPLICIT,
    SENSOR_SOURCE_NONE,
)

_MOD = "custom_components.adaptive_cover_pro.state.area_resolver"


def _patch_registries(*, device_area_id=None, area_temp_entity=None):
    """Patch the device + area registries the resolver reads.

    ``device_area_id`` is the area a device belongs to (None = no device / no
    area). ``area_temp_entity`` is the area's configured temperature entity
    (None = area has none configured).
    """
    device = MagicMock()
    device.area_id = device_area_id
    device_reg = MagicMock()
    device_reg.async_get.return_value = device if device_area_id is not None else None

    area = MagicMock()
    area.temperature_entity_id = area_temp_entity
    area_reg = MagicMock()
    area_reg.async_get_area.return_value = area if device_area_id is not None else None

    return (
        patch(f"{_MOD}.dr.async_get", return_value=device_reg),
        patch(f"{_MOD}.ar.async_get", return_value=area_reg),
    )


@pytest.fixture
def hass():
    """Minimal mock HomeAssistant — registries are patched per test."""
    return MagicMock()


class TestResolveTemperatureEntity:
    """Explicit-wins precedence and area fallback for the temp sensor."""

    @pytest.mark.unit
    def test_explicit_temp_entity_wins_over_area(self, hass):
        """An explicit CONF_TEMP_ENTITY always wins; the area is never read."""
        dev_patch, area_patch = _patch_registries(
            device_area_id="area_bedroom", area_temp_entity="sensor.area_temp"
        )
        resolver = AreaSensorResolver(hass)
        with dev_patch as dev, area_patch as area:
            result = resolver.resolve_temperature_entity(
                explicit_entity="sensor.explicit_temp",
                device_id="device_1",
            )
        assert result == ResolvedSensor(
            entity_id="sensor.explicit_temp",
            source=SENSOR_SOURCE_EXPLICIT,
            area_id=None,
        )
        # Explicit short-circuits — registries untouched.
        dev.assert_not_called()
        area.assert_not_called()

    @pytest.mark.unit
    def test_area_temp_resolved_when_no_explicit(self, hass):
        """No explicit entity → resolve the area's configured temperature entity."""
        dev_patch, area_patch = _patch_registries(
            device_area_id="area_bedroom", area_temp_entity="sensor.bedroom_temp"
        )
        resolver = AreaSensorResolver(hass)
        with dev_patch, area_patch:
            result = resolver.resolve_temperature_entity(
                explicit_entity=None,
                device_id="device_1",
            )
        assert result == ResolvedSensor(
            entity_id="sensor.bedroom_temp",
            source=SENSOR_SOURCE_AREA,
            area_id="area_bedroom",
        )

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("device_id", "device_area_id", "area_temp_entity"),
        [
            (None, None, None),  # no device linked
            ("device_1", None, None),  # device has no area
            ("device_1", "area_bedroom", None),  # area has no temp entity
        ],
        ids=["no_device", "no_area", "no_temp_entity"],
    )
    def test_falls_through_to_none(
        self, hass, device_id, device_area_id, area_temp_entity
    ):
        """Any missing hop → None / source none, matching 'no sensor' today."""
        dev_patch, area_patch = _patch_registries(
            device_area_id=device_area_id, area_temp_entity=area_temp_entity
        )
        resolver = AreaSensorResolver(hass)
        with dev_patch, area_patch:
            result = resolver.resolve_temperature_entity(
                explicit_entity=None,
                device_id=device_id,
            )
        assert result == ResolvedSensor(
            entity_id=None, source=SENSOR_SOURCE_NONE, area_id=None
        )

    @pytest.mark.unit
    def test_auto_resolve_disabled_skips_area(self, hass):
        """With auto_resolve off and no explicit entity → None (opt-out)."""
        dev_patch, area_patch = _patch_registries(
            device_area_id="area_bedroom", area_temp_entity="sensor.bedroom_temp"
        )
        resolver = AreaSensorResolver(hass)
        with dev_patch as dev, area_patch as area:
            result = resolver.resolve_temperature_entity(
                explicit_entity=None,
                device_id="device_1",
                auto_resolve=False,
            )
        assert result == ResolvedSensor(
            entity_id=None, source=SENSOR_SOURCE_NONE, area_id=None
        )
        dev.assert_not_called()
        area.assert_not_called()
