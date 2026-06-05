"""Tests for the per-role panel Target sensors (dual-/split-panel)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_DUAL_PANEL_BACK,
    CONF_DUAL_PANEL_FRONT,
    CONF_SENSOR_TYPE,
    CONF_SPLIT_PANEL_BOTTOM,
    CONF_SPLIT_PANEL_TOP,
    CoverType,
)
from custom_components.adaptive_cover_pro.sensor import _STANDARD_SPECS

PANEL_SUFFIXES = {"Panel_Front", "Panel_Back", "Panel_Top", "Panel_Bottom"}


def _spec(suffix: str):
    for spec in _STANDARD_SPECS:
        if spec.suffix == suffix:
            return spec
    raise AssertionError(f"{suffix} spec not found in _STANDARD_SPECS")


def _entry(sensor_type: str, options: dict):
    entry = MagicMock()
    entry.entry_id = "panel_entry"
    entry.data = {"name": "P", CONF_SENSOR_TYPE: sensor_type}
    entry.options = options
    return entry


_DUAL_OPTS = {
    CONF_DUAL_PANEL_FRONT: "cover.sheer",
    CONF_DUAL_PANEL_BACK: "cover.blackout",
}
_SPLIT_OPTS = {
    CONF_SPLIT_PANEL_TOP: "cover.top",
    CONF_SPLIT_PANEL_BOTTOM: "cover.bottom",
}


@pytest.mark.unit
class TestPanelTargetSpecs:
    """The four per-role specs exist, gate correctly, and read targets."""

    def test_all_four_specs_exist(self):
        present = {s.suffix for s in _STANDARD_SPECS} & PANEL_SUFFIXES
        assert present == PANEL_SUFFIXES

    def test_dual_panel_enables_front_back_only(self):
        assert _spec("Panel_Front").enabled_when(
            _entry(CoverType.DUAL_PANEL, _DUAL_OPTS)
        )
        assert _spec("Panel_Back").enabled_when(
            _entry(CoverType.DUAL_PANEL, _DUAL_OPTS)
        )
        assert not _spec("Panel_Top").enabled_when(
            _entry(CoverType.DUAL_PANEL, _DUAL_OPTS)
        )
        assert not _spec("Panel_Bottom").enabled_when(
            _entry(CoverType.DUAL_PANEL, _DUAL_OPTS)
        )

    def test_split_panel_enables_top_bottom_only(self):
        assert _spec("Panel_Top").enabled_when(
            _entry(CoverType.SPLIT_PANEL, _SPLIT_OPTS)
        )
        assert _spec("Panel_Bottom").enabled_when(
            _entry(CoverType.SPLIT_PANEL, _SPLIT_OPTS)
        )
        assert not _spec("Panel_Front").enabled_when(
            _entry(CoverType.SPLIT_PANEL, _SPLIT_OPTS)
        )

    @pytest.mark.parametrize("suffix", sorted(PANEL_SUFFIXES))
    def test_single_entity_types_enable_no_panel_sensors(self, suffix):
        assert not _spec(suffix).enabled_when(_entry(CoverType.BLIND, {}))
        assert not _spec(suffix).enabled_when(_entry(CoverType.VENETIAN, {}))

    def test_value_fn_reads_role_from_coordinator(self):
        stub = MagicMock()
        stub.coordinator._panel_targets = {"front": 40, "back": 100}
        assert _spec("Panel_Front").value_fn(stub) == 40
        assert _spec("Panel_Back").value_fn(stub) == 100

    def test_value_fn_none_when_role_absent(self):
        stub = MagicMock()
        stub.coordinator._panel_targets = {}
        assert _spec("Panel_Top").value_fn(stub) is None

    def test_panel_specs_are_not_diagnostic(self):
        for suffix in PANEL_SUFFIXES:
            assert _spec(suffix).diagnostic is False
