"""Tests for the Building Profile diagnostics sensor source/state subsections (issue #693, Q3).

The diagnostics output distinguishes, per shared sensor key:
- ``source``  — "local" (configured on the cover) vs "profile" (inherited from
  a linked Building Profile).
- ``state``   — "not_configured" / "unavailable" / "available".

A linked cover emits two top-level subsections: ``building_profile_sensors``
(profile-owned keys) and ``local_sensors`` (everything kept locally). An
unlinked cover emits only ``local_sensors``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_BUILDING_PROFILE_ID,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_IRRADIANCE_ENTITY,
    CONF_LUX_ENTITY,
    CONF_OUTSIDETEMP_ENTITY,
)
from custom_components.adaptive_cover_pro.diagnostics.builder import DiagnosticsBuilder

from .test_builder import _base_ctx

# ---------------------------------------------------------------------------
# Minimal HA stand-ins — the builder only touches ``hass.states.get`` and
# ``hass.config_entries.async_entries(DOMAIN)``.
# ---------------------------------------------------------------------------


class _FakeStates:
    def __init__(self, mapping: dict) -> None:
        self._m = mapping

    def get(self, entity_id):
        return self._m.get(entity_id)


class _FakeConfigEntries:
    def __init__(self, entries: list) -> None:
        self._entries = entries

    def async_entries(self, _domain):
        return list(self._entries)


def _state(value: str) -> SimpleNamespace:
    return SimpleNamespace(state=value)


def _entry(entry_id: str, options: dict) -> SimpleNamespace:
    return SimpleNamespace(entry_id=entry_id, options=options)


def _make_hass(states_map: dict, entries: list) -> SimpleNamespace:
    return SimpleNamespace(
        states=_FakeStates(states_map),
        config_entries=_FakeConfigEntries(entries),
    )


@pytest.fixture
def builder() -> DiagnosticsBuilder:
    return DiagnosticsBuilder()


def _by_key(rows: list[dict]) -> dict:
    return {row["key"]: row for row in rows}


# ---------------------------------------------------------------------------


def test_unlinked_omits_profile_block(builder: DiagnosticsBuilder):
    """An unlinked cover has the local-sensors block and NO profile block."""
    ctx = _base_ctx(
        config_options={CONF_LUX_ENTITY: "sensor.lux"},
        hass=_make_hass({"sensor.lux": _state("100")}, []),
    )
    diag, _ = builder.build(ctx)

    assert "building_profile_sensors" not in diag
    assert "local_sensors" in diag

    local = _by_key(diag["local_sensors"])
    assert local[CONF_LUX_ENTITY]["source"] == "local"
    assert local[CONF_LUX_ENTITY]["state"] == "available"
    # An unset shared key is classified not_configured.
    assert local[CONF_CLOUD_COVERAGE_ENTITY]["source"] == "local"
    assert local[CONF_CLOUD_COVERAGE_ENTITY]["state"] == "not_configured"


def test_linked_emits_two_subsections(builder: DiagnosticsBuilder):
    """A linked cover emits both blocks with correct source/state per key.

    Profile holds an available lux sensor (copied into the cover) and an
    outside-temp sensor pointing at a missing entity. It leaves irradiance
    blank, so the cover keeps its own local irradiance sensor (Q2 fallback).
    """
    profile_id = "profile_entry_1"
    profile_options = {
        CONF_LUX_ENTITY: "sensor.lux",
        CONF_OUTSIDETEMP_ENTITY: "sensor.missing_temp",
    }
    cover_options = {
        CONF_BUILDING_PROFILE_ID: profile_id,
        CONF_LUX_ENTITY: "sensor.lux",  # copied from profile on link
        CONF_OUTSIDETEMP_ENTITY: "sensor.missing_temp",  # copied from profile
        CONF_IRRADIANCE_ENTITY: "sensor.local_irr",  # kept locally
    }
    hass = _make_hass(
        {"sensor.lux": _state("100"), "sensor.local_irr": _state("250")},
        [_entry(profile_id, profile_options)],
    )
    ctx = _base_ctx(config_options=cover_options, hass=hass)
    diag, _ = builder.build(ctx)

    profile = _by_key(diag["building_profile_sensors"])
    local = _by_key(diag["local_sensors"])

    # Profile-owned, available.
    assert profile[CONF_LUX_ENTITY]["source"] == "profile"
    assert profile[CONF_LUX_ENTITY]["state"] == "available"
    # Profile-owned key whose entity is missing → unavailable.
    assert profile[CONF_OUTSIDETEMP_ENTITY]["source"] == "profile"
    assert profile[CONF_OUTSIDETEMP_ENTITY]["state"] == "unavailable"

    # Profile left irradiance blank → cover keeps its own → local, available.
    assert local[CONF_IRRADIANCE_ENTITY]["source"] == "local"
    assert local[CONF_IRRADIANCE_ENTITY]["state"] == "available"
    # An unset shared key → local, not_configured.
    assert local[CONF_CLOUD_COVERAGE_ENTITY]["source"] == "local"
    assert local[CONF_CLOUD_COVERAGE_ENTITY]["state"] == "not_configured"

    # Profile-sourced keys do not also appear in the local block.
    assert CONF_LUX_ENTITY not in local
    assert CONF_OUTSIDETEMP_ENTITY not in local
