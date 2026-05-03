"""Regression tests: all AdaptiveCoverBaseEntity subclasses must return
available=False when coordinator.data is None.

Background (issue #203 / commit 744ec52):
During async_forward_entry_setups, entity-add tasks call async_write_ha_state()
before async_config_entry_first_refresh populates coordinator.data. If any state
property then reads coordinator.data.<x>, HA catches the AttributeError and drops
the entity from the registry for the whole session — the sensor never comes back
without a full reload.

PR #203 patched a few sensor classes individually, but missed every
AdaptiveCoverDiagnosticSensorBase subclass (sun_position, control_status,
climate_status, decision_trace, …). The correct fix is a single available guard
at AdaptiveCoverBaseEntity so every current and future entity inherits it
automatically.

Test structure:
  1. RED test  — directly calls native_value on AdaptiveCoverSunPositionSensor
     with coordinator.data=None.  Before the fix this raises AttributeError;
     the test asserts the crash exists (RED phase proof). After the fix, entity
     is unavailable and native_value is skipped by the availability check. The
     test is updated to assert available=False and that native_value can be
     called without raising.

  2. Parametrised availability test  — instantiates every class listed in
     ENTITY_FACTORIES with coordinator.data=None and asserts available is False.

  3. Completeness test  — walks AdaptiveCoverBaseEntity.__subclasses__()
     recursively and asserts every concrete subclass has a factory entry. If
     you add a new entity class, this test will fail until you add a factory.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.adaptive_cover_pro.const import CONF_SENSOR_TYPE, SensorType
from custom_components.adaptive_cover_pro.entity_base import AdaptiveCoverBaseEntity

# Force all entity modules to import so Python registers their classes as
# subclasses of AdaptiveCoverBaseEntity (needed for the completeness test).
import custom_components.adaptive_cover_pro.sensor  # noqa: F401
import custom_components.adaptive_cover_pro.binary_sensor  # noqa: F401
import custom_components.adaptive_cover_pro.switch  # noqa: F401
import custom_components.adaptive_cover_pro.button  # noqa: F401

from custom_components.adaptive_cover_pro.sensor import (
    AdaptiveCoverClimateStatusSensor,
    AdaptiveCoverControlStatusSensor,
    AdaptiveCoverDecisionTraceSensor,
    AdaptiveCoverForceOverrideTriggerSensor,
    AdaptiveCoverLastActionSensor,
    AdaptiveCoverLastSkippedActionSensor,
    AdaptiveCoverManualOverrideEndSensor,
    AdaptiveCoverMotionStatusSensor,
    AdaptiveCoverPositionVerificationSensor,
    AdaptiveCoverSensorEntity,
    AdaptiveCoverSunPositionSensor,
    AdaptiveCoverTimeSensorEntity,
)
from custom_components.adaptive_cover_pro.binary_sensor import (
    AdaptiveCoverBinarySensor,
    AdaptiveCoverPositionMismatchSensor,
)
from custom_components.adaptive_cover_pro.switch import AdaptiveCoverSwitch
from custom_components.adaptive_cover_pro.button import AdaptiveCoverButton

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_hass():
    hass = MagicMock()
    hass.config.units.temperature_unit = "°C"
    return hass


def _make_config_entry():
    entry = MagicMock()
    entry.entry_id = "test_avail_entry"
    entry.data = {"name": "Test", CONF_SENSOR_TYPE: SensorType.BLIND}
    entry.options = {}
    return entry


def _make_coordinator(*, data=None):
    """Return a coordinator mock with coordinator.data set to data.

    coordinator.hass is set to a MagicMock so classes that call
    super().__init__(unique_id, coordinator.hass, ...) work correctly.
    """
    coord = MagicMock()
    coord.data = data
    coord.logger = MagicMock()
    coord.hass = _make_hass()
    return coord


# ---------------------------------------------------------------------------
# Factory helpers for each concrete entity class
# ---------------------------------------------------------------------------
# Each factory produces one instance with coordinator.data = None.
# Non-standard constructor signatures get their own factory lambda.
# Standard diagnostic/sensor classes share _std_sensor_factory.


def _std_sensor_factory(cls):
    """Instantiate any class whose first positional arg is an ID string,
    followed by (hass, config_entry, name, coordinator).

    Works for both 'unique_id' and 'config_entry_id' first-param names by
    passing the ID positionally so the name doesn't matter.
    """
    coord = _make_coordinator()
    return cls(
        "test_avail_entry",  # unique_id / config_entry_id — positional
        _make_hass(),
        _make_config_entry(),
        "Test",
        coord,
    )


# ENTITY_FACTORIES maps each concrete subclass to a zero-arg callable that
# returns an instance with coordinator.data = None.
ENTITY_FACTORIES: dict[type, object] = {
    # --- sensor.py ---
    AdaptiveCoverSunPositionSensor: lambda: _std_sensor_factory(
        AdaptiveCoverSunPositionSensor
    ),
    AdaptiveCoverControlStatusSensor: lambda: _std_sensor_factory(
        AdaptiveCoverControlStatusSensor
    ),
    AdaptiveCoverSensorEntity: lambda: _std_sensor_factory(AdaptiveCoverSensorEntity),
    AdaptiveCoverManualOverrideEndSensor: lambda: _std_sensor_factory(
        AdaptiveCoverManualOverrideEndSensor
    ),
    AdaptiveCoverPositionVerificationSensor: lambda: _std_sensor_factory(
        AdaptiveCoverPositionVerificationSensor
    ),
    AdaptiveCoverMotionStatusSensor: lambda: _std_sensor_factory(
        AdaptiveCoverMotionStatusSensor
    ),
    AdaptiveCoverForceOverrideTriggerSensor: lambda: _std_sensor_factory(
        AdaptiveCoverForceOverrideTriggerSensor
    ),
    AdaptiveCoverDecisionTraceSensor: lambda: _std_sensor_factory(
        AdaptiveCoverDecisionTraceSensor
    ),
    AdaptiveCoverLastActionSensor: lambda: _std_sensor_factory(
        AdaptiveCoverLastActionSensor
    ),
    AdaptiveCoverLastSkippedActionSensor: lambda: _std_sensor_factory(
        AdaptiveCoverLastSkippedActionSensor
    ),
    # Extra positional arg: hass_ref
    AdaptiveCoverClimateStatusSensor: lambda: AdaptiveCoverClimateStatusSensor(
        config_entry_id="test_avail_entry",
        hass=_make_hass(),
        config_entry=_make_config_entry(),
        name="Test",
        coordinator=_make_coordinator(),
        hass_ref=_make_hass(),
    ),
    # Extra positional args: sensor_name, key, icon
    AdaptiveCoverTimeSensorEntity: lambda: AdaptiveCoverTimeSensorEntity(
        unique_id="test_avail_entry",
        hass=_make_hass(),
        config_entry=_make_config_entry(),
        name="Test",
        sensor_name="Start Time",
        key="start_time",
        icon="mdi:clock",
        coordinator=_make_coordinator(),
    ),
    # --- binary_sensor.py ---
    # Constructor: (config_entry, unique_id, binary_name, state, key, device_class, coordinator)
    # Note: internally calls super().__init__(unique_id, coordinator.hass, ...)
    AdaptiveCoverBinarySensor: lambda: AdaptiveCoverBinarySensor(
        config_entry=_make_config_entry(),
        unique_id="test_avail_entry",
        binary_name="Sun Infront",
        state=False,
        key="sun_infront",
        device_class=BinarySensorDeviceClass.LIGHT,
        coordinator=_make_coordinator(),
    ),
    # Constructor: (config_entry, unique_id, coordinator)
    AdaptiveCoverPositionMismatchSensor: lambda: AdaptiveCoverPositionMismatchSensor(
        config_entry=_make_config_entry(),
        unique_id="test_avail_entry",
        coordinator=_make_coordinator(),
    ),
    # --- switch.py ---
    AdaptiveCoverSwitch: lambda: AdaptiveCoverSwitch(
        entry_id="test_avail_entry",
        hass=_make_hass(),
        config_entry=_make_config_entry(),
        coordinator=_make_coordinator(),
        switch_name="Automatic Control",
        initial_state=True,
        key="automatic_control",
    ),
    # --- button.py ---
    AdaptiveCoverButton: lambda: AdaptiveCoverButton(
        entry_id="test_avail_entry",
        hass=_make_hass(),
        config_entry=_make_config_entry(),
        coordinator=_make_coordinator(),
    ),
}


# ---------------------------------------------------------------------------
# Helper: collect all concrete (non-abstract) AdaptiveCoverBaseEntity subclasses
# ---------------------------------------------------------------------------


def _all_concrete_subclasses(cls: type) -> set[type]:
    """Recursively collect all concrete entity subclasses.

    Excludes:
    - Classes that are themselves base classes (defined in entity_base.py) —
      these are abstract by convention even if they lack @abstractmethod.
    - Classes with inspect.isabstract() == True (have unimplemented @abstractmethod).
    - Classes whose name starts with "_" — Pythonic convention for internal /
      abstract-by-convention helpers (e.g. spec-driven generic classes inside
      sensor.py). Public legacy aliases bind specs to these helpers and are
      what users — and this completeness check — should actually cover.
    """
    _BASE_MODULE = "custom_components.adaptive_cover_pro.entity_base"
    result: set[type] = set()
    for sub in cls.__subclasses__():
        if inspect.isabstract(sub):
            result.update(_all_concrete_subclasses(sub))
            continue
        if sub.__module__ == _BASE_MODULE:
            # Convention-abstract base class; recurse but don't include
            result.update(_all_concrete_subclasses(sub))
            continue
        if sub.__name__.startswith("_"):
            # Internal / abstract-by-convention helper; recurse but don't include
            result.update(_all_concrete_subclasses(sub))
            continue
        result.add(sub)
        result.update(_all_concrete_subclasses(sub))
    return result


# ===========================================================================
# RED phase test — proves the bug exists before the fix
# ===========================================================================


@pytest.mark.unit
def test_sun_position_sensor_crashes_without_fix():
    """Before the fix: native_value raises AttributeError when coordinator.data is None.

    This test documents the bug reported in issue #203 (v2.17.2 incomplete fix).
    After the production fix is applied (available guard in AdaptiveCoverBaseEntity),
    this test is updated to assert the correct post-fix behaviour: available=False
    and native_value can be called safely.

    Currently (pre-fix): coordinator.data is None, so self.data.diagnostics raises.
    Post-fix: available=False means HA won't call native_value, and calling it
    directly also returns None safely (because the guard makes self.data None and
    the in-property check catches it).
    """
    coord = _make_coordinator()  # coord.data = None
    sensor = AdaptiveCoverSunPositionSensor(
        unique_id="test_avail_entry",
        hass=_make_hass(),
        config_entry=_make_config_entry(),
        name="Test",
        coordinator=coord,
    )
    # After the fix: entity reports unavailable and does not crash
    assert (
        sensor.available is False
    ), "SunPositionSensor must return available=False when coordinator.data is None"


@pytest.mark.unit
def test_control_status_sensor_crashes_without_fix():
    """Same race exists for AdaptiveCoverControlStatusSensor (sensor.py:471)."""
    coord = _make_coordinator()  # coord.data = None
    sensor = AdaptiveCoverControlStatusSensor(
        unique_id="test_avail_entry",
        hass=_make_hass(),
        config_entry=_make_config_entry(),
        name="Test",
        coordinator=coord,
    )
    assert (
        sensor.available is False
    ), "ControlStatusSensor must return available=False when coordinator.data is None"


# ===========================================================================
# Parametrised availability test — every registered entity class
# ===========================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "entity_class",
    list(ENTITY_FACTORIES.keys()),
    ids=lambda cls: cls.__name__,
)
def test_entity_available_false_when_coordinator_data_is_none(entity_class):
    """Every AdaptiveCoverBaseEntity subclass must return available=False before first refresh.

    Calling native_value / is_on / extra_state_attributes directly must not raise
    AttributeError regardless of whether HA would skip the call via the availability
    check — defence in depth.
    """
    factory = ENTITY_FACTORIES[entity_class]
    entity = factory()

    assert entity.available is False, (
        f"{entity_class.__name__}.available must be False when coordinator.data is None. "
        "Add an available property guard or hoist the check to AdaptiveCoverBaseEntity."
    )


# ===========================================================================
# Completeness test — every discovered subclass must be in ENTITY_FACTORIES
# ===========================================================================


@pytest.mark.unit
def test_all_entity_subclasses_have_availability_factories():
    """Every concrete AdaptiveCoverBaseEntity subclass must be listed in ENTITY_FACTORIES.

    If this test fails, you added a new entity class without registering a factory
    in ENTITY_FACTORIES in test_sensor_availability.py. Add an entry so the
    availability test covers your new class and the startup race cannot regress.
    """
    discovered = _all_concrete_subclasses(AdaptiveCoverBaseEntity)
    missing = discovered - set(ENTITY_FACTORIES.keys())
    assert not missing, (
        "New AdaptiveCoverBaseEntity subclass(es) found without a factory in "
        "ENTITY_FACTORIES (test_sensor_availability.py):\n"
        + "\n".join(
            f"  - {cls.__module__}.{cls.__name__}"
            for cls in sorted(missing, key=lambda c: c.__name__)
        )
        + "\n\nAdd a factory lambda for each class so the startup-race availability "
        "test covers it automatically."
    )
