"""Tests for per-entity write gating in AdaptiveCoverBaseEntity.

The coordinator notifies every listener on each update cycle. Most cycles leave
an individual entity's rendered output unchanged (sun micro-move, chatty temp
sensor), so writing every entity every cycle floods HA's state machine, event
bus, and recorder with no-op updates.

``AdaptiveCoverBaseEntity._handle_coordinator_update`` now gates the write on a
render signature ``(available, state, repr(extra_state_attributes))`` and only
calls ``async_write_ha_state()`` when that signature changes. The proxy cover's
independent source-mirror path (``_handle_source_event``) has its own parallel
gate. These tests pin both behaviours, including the fail-open contract.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import CONF_SENSOR_TYPE, CoverType
from custom_components.adaptive_cover_pro.cover import AdaptiveProxyCover
from custom_components.adaptive_cover_pro.entity_base import (
    _SENTINEL,
    AdaptiveCoverBaseEntity,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_hass():
    hass = MagicMock()
    hass.config.units.temperature_unit = "°C"
    return hass


def _make_config_entry():
    entry = MagicMock()
    entry.entry_id = "test_gate_entry"
    entry.title = "Test"
    entry.data = {"name": "Test", CONF_SENSOR_TYPE: CoverType.BLIND}
    entry.options = {}
    return entry


def _make_coordinator(*, data=None):
    coord = MagicMock()
    coord.data = data
    coord.last_update_success = True
    coord.logger = MagicMock()
    coord.hass = _make_hass()
    return coord


class _GateEntity(AdaptiveCoverBaseEntity):
    """Minimal concrete entity with controllable render surface.

    ``state`` and ``extra_state_attributes`` read from plain attributes so each
    test can drive the render signature directly. ``available`` is inherited
    from the base class (False while ``coordinator.data`` is None).
    """

    def __init__(self, coord, *, state="idle", attrs=None) -> None:
        super().__init__("test_gate_entry", _make_hass(), _make_config_entry(), coord)
        self._test_state = state
        self._test_attrs = attrs

    @property
    def state(self):
        return self._test_state

    @property
    def extra_state_attributes(self):
        return self._test_attrs


def _build(coord=None, **kwargs) -> _GateEntity:
    entity = _GateEntity(coord or _make_coordinator(data={"ready": True}), **kwargs)
    entity.async_write_ha_state = MagicMock()
    return entity


# ---------------------------------------------------------------------------
# Base-class coordinator-update gating
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_first_update_always_writes():
    entity = _build()
    entity._handle_coordinator_update()
    entity.async_write_ha_state.assert_called_once()
    assert entity._acp_last_write_sig is not _SENTINEL


@pytest.mark.unit
def test_unchanged_update_suppressed():
    entity = _build()
    entity._handle_coordinator_update()
    entity._handle_coordinator_update()
    entity._handle_coordinator_update()
    entity.async_write_ha_state.assert_called_once()


@pytest.mark.unit
def test_changed_state_writes():
    entity = _build(state="idle")
    entity._handle_coordinator_update()
    entity._test_state = "active"
    entity._handle_coordinator_update()
    assert entity.async_write_ha_state.call_count == 2


@pytest.mark.unit
def test_changed_attributes_writes():
    entity = _build(attrs={"trace": [1, 2]})
    entity._handle_coordinator_update()
    entity._test_attrs = {"trace": [1, 2, 3]}
    entity._handle_coordinator_update()
    assert entity.async_write_ha_state.call_count == 2


@pytest.mark.unit
def test_availability_flip_writes():
    coord = _make_coordinator(data=None)  # available -> False
    entity = _build(coord)
    entity._handle_coordinator_update()  # first write (unavailable)
    assert entity.async_write_ha_state.call_count == 1
    coord.data = {"ready": True}  # available -> True
    entity._handle_coordinator_update()
    assert entity.async_write_ha_state.call_count == 2


@pytest.mark.unit
def test_signature_exception_fails_open():
    entity = _build()
    entity._handle_coordinator_update()
    entity.async_write_ha_state.reset_mock()

    # Force the signature build to raise on the next update.
    boom = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    type(entity).extra_state_attributes = boom
    try:
        entity._handle_coordinator_update()
    finally:
        # Restore so the class isn't left poisoned for other tests.
        type(entity).extra_state_attributes = _GateEntity.extra_state_attributes

    entity.async_write_ha_state.assert_called_once()
    assert entity._acp_last_write_sig is _SENTINEL


@pytest.mark.unit
def test_attribute_mutation_in_place_detected():
    """A subclass that mutates a reused dict must still trigger a write.

    repr-snapshotting the attributes (rather than holding the live mapping)
    guards against the alias trap where ``prev is current`` would compare equal.
    """
    shared = {"count": 1}
    entity = _build(attrs=shared)
    entity._handle_coordinator_update()
    shared["count"] = 2  # same object, mutated in place
    entity._handle_coordinator_update()
    assert entity.async_write_ha_state.call_count == 2


# ---------------------------------------------------------------------------
# Proxy cover source-event gating
# ---------------------------------------------------------------------------


class _FakeState:
    def __init__(self, state: str, attributes: dict) -> None:
        self.state = state
        self.attributes = attributes


def _make_proxy():
    hass = _make_hass()
    holder = {"state": None}
    hass.states.get = MagicMock(side_effect=lambda _eid: holder["state"])
    proxy = AdaptiveProxyCover(
        entry_id="test_gate_entry",
        hass=hass,
        config_entry=_make_config_entry(),
        coordinator=_make_coordinator(data={"ready": True}),
        source_entity_id="cover.test_source",
        multi=False,
    )
    proxy.async_write_ha_state = MagicMock()
    return proxy, holder


@pytest.mark.unit
def test_proxy_unchanged_source_event_suppressed():
    proxy, holder = _make_proxy()
    holder["state"] = _FakeState(
        "open", {"current_position": 50, "supported_features": 15}
    )
    event = MagicMock()
    proxy._handle_source_event(event)
    proxy._handle_source_event(event)  # identical observable state
    proxy.async_write_ha_state.assert_called_once()


@pytest.mark.unit
def test_proxy_changed_position_writes():
    proxy, holder = _make_proxy()
    holder["state"] = _FakeState(
        "open", {"current_position": 50, "supported_features": 15}
    )
    event = MagicMock()
    proxy._handle_source_event(event)
    holder["state"] = _FakeState(
        "open", {"current_position": 70, "supported_features": 15}
    )
    proxy._handle_source_event(event)
    assert proxy.async_write_ha_state.call_count == 2


@pytest.mark.unit
def test_proxy_source_gate_is_separate_from_base_gate():
    proxy, _holder = _make_proxy()
    assert proxy._proxy_source_sig is _SENTINEL
    assert proxy._acp_last_write_sig is _SENTINEL
