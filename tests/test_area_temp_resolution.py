"""Tests for AreaSensorResolver — cover area → indoor temp sensor (issue #786).

The resolver lives in the ``state/`` boundary and is the only place the
device/area registries are read for temperature resolution. Explicit config
always wins; the area's configured ``temperature_entity_id`` is the fallback.
"""

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import DOMAIN
from custom_components.adaptive_cover_pro.state.area_resolver import (
    AreaSensorResolver,
    ResolvedSensor,
    SENSOR_SOURCE_AREA,
    SENSOR_SOURCE_EXPLICIT,
    SENSOR_SOURCE_NONE,
    area_device_ids,
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
def mock_hass():
    """Minimal mock HomeAssistant — registries are patched per test."""
    return MagicMock()


class TestResolveTemperatureEntity:
    """Explicit-wins precedence and area fallback for the temp sensor."""

    @pytest.mark.unit
    def test_explicit_temp_entity_wins_over_area(self, mock_hass):
        """An explicit CONF_TEMP_ENTITY always wins; the area is never read."""
        dev_patch, area_patch = _patch_registries(
            device_area_id="area_bedroom", area_temp_entity="sensor.area_temp"
        )
        resolver = AreaSensorResolver(mock_hass)
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
    def test_area_temp_resolved_when_no_explicit(self, mock_hass):
        """No explicit entity → resolve the area's configured temperature entity."""
        dev_patch, area_patch = _patch_registries(
            device_area_id="area_bedroom", area_temp_entity="sensor.bedroom_temp"
        )
        resolver = AreaSensorResolver(mock_hass)
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
        self, mock_hass, device_id, device_area_id, area_temp_entity
    ):
        """Any missing hop → None / source none, matching 'no sensor' today."""
        dev_patch, area_patch = _patch_registries(
            device_area_id=device_area_id, area_temp_entity=area_temp_entity
        )
        resolver = AreaSensorResolver(mock_hass)
        with dev_patch, area_patch:
            result = resolver.resolve_temperature_entity(
                explicit_entity=None,
                device_id=device_id,
            )
        assert result == ResolvedSensor(
            entity_id=None, source=SENSOR_SOURCE_NONE, area_id=None
        )

    @pytest.mark.unit
    def test_auto_resolve_disabled_skips_area(self, mock_hass):
        """With auto_resolve off and no explicit entity → None (opt-out)."""
        dev_patch, area_patch = _patch_registries(
            device_area_id="area_bedroom", area_temp_entity="sensor.bedroom_temp"
        )
        resolver = AreaSensorResolver(mock_hass)
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


class TestAreaDeviceIds:
    """The inverse hop: area → the ids of the devices in it (issue #1339).

    The two real-registry tests run against a real ``DeviceRegistry`` rather
    than a mock of one. ``dr.async_entries_for_area`` is a thin wrapper over
    whichever index HA maintains this release, so a mock of its innards pins
    an implementation detail and silently stops intercepting the day HA
    rewrites the wrapper — which is exactly what HA 2026.8 did (issue #1373).
    A real registry tracks the rewrite for free.
    """

    @pytest.mark.integration
    async def test_returns_device_ids_in_area(
        self,
        hass: HomeAssistant,
        device_registry: dr.DeviceRegistry,
        area_registry: ar.AreaRegistry,
    ) -> None:
        """An area resolves to exactly the devices assigned to it, and no others.

        Selectivity is the point: a second area holding a third device is what
        proves the area index is being consulted at all, rather than every
        device in the install coming back regardless of the ``area_id`` asked
        for.
        """
        entry = MockConfigEntry(domain=DOMAIN, entry_id="area_device_ids")
        entry.add_to_hass(hass)

        living = area_registry.async_create("Living Room")
        bedroom = area_registry.async_create("Bedroom")

        def _device(unique: str, area):
            device = device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, unique)},
            )
            return device_registry.async_update_device(device.id, area_id=area.id)

        first = _device("cover-1", living)
        second = _device("cover-2", living)
        third = _device("cover-3", bedroom)

        assert sorted(area_device_ids(hass, living.id)) == sorted([first.id, second.id])
        assert area_device_ids(hass, bedroom.id) == [third.id]

    @pytest.mark.unit
    def test_delegates_to_the_public_area_accessor(self, mock_hass):
        """The hop goes through ``dr.async_entries_for_area``, not the index itself.

        The API-shape pin that outlives the AST guard in
        ``test_registry_index_guard.py``, now sitting on the public helper HA
        promises rather than on the private index that helper happens to call.
        The registry is spec'd to ``dr.DeviceRegistry``, whose ``devices`` is
        annotation-only, so reaching for the index raises here instead of
        quietly auto-vivifying.
        """
        device = MagicMock()
        device.id = "dev1"
        registry = MagicMock(spec=dr.DeviceRegistry)

        with (
            patch(f"{_MOD}.dr.async_get", return_value=registry),
            patch(
                f"{_MOD}.dr.async_entries_for_area", return_value=[device]
            ) as accessor,
        ):
            assert area_device_ids(mock_hass, "area_x") == ["dev1"]

        accessor.assert_called_once_with(registry, "area_x")

    @pytest.mark.unit
    @pytest.mark.parametrize("area_id", [None, ""], ids=["none", "empty_string"])
    def test_empty_area_returns_empty_list(self, mock_hass, area_id):
        """No area → no devices, and the registry is never touched (fail-open)."""
        with patch(f"{_MOD}.dr.async_get") as async_get:
            assert area_device_ids(mock_hass, area_id) == []

        async_get.assert_not_called()

    @pytest.mark.integration
    async def test_unknown_area_returns_empty_list(
        self,
        hass: HomeAssistant,
        device_registry: dr.DeviceRegistry,
        area_registry: ar.AreaRegistry,
    ) -> None:
        """An area the registry does not know resolves to no devices."""
        entry = MockConfigEntry(domain=DOMAIN, entry_id="area_device_ids_unknown")
        entry.add_to_hass(hass)

        device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, "cover-1")},
        )
        device_registry.async_update_device(
            device.id, area_id=area_registry.async_create("Living Room").id
        )

        assert area_device_ids(hass, "area_missing") == []
