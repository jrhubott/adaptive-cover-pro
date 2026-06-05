"""Tests for the split-panel (one fabric, top + bottom) cover policy."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.adaptive_cover_pro.const import (
    CONF_ENTITIES,
    CONF_SPLIT_PANEL_BOTTOM,
    CONF_SPLIT_PANEL_TOP,
    ControlMethod,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.axis_target import (
    DispatchStrategy,
)
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

TOP = "cover.top"
BOTTOM = "cover.bottom"
OPTS = {CONF_SPLIT_PANEL_TOP: TOP, CONF_SPLIT_PANEL_BOTTOM: BOTTOM}
ENTS = [TOP, BOTTOM]


def _policy():
    return get_policy("cover_split_panel")


def _roles() -> dict[str, str]:
    return _policy().panel_role_map(OPTS)


def _result(**kw) -> PipelineResult:
    base = {
        "position": kw.pop("position", 40),
        "control_method": kw.pop("control_method", ControlMethod.SOLAR),
        "reason": kw.pop("reason", "solar"),
    }
    base.update(kw)
    return PipelineResult(**base)


class TestRoleAndEntityPlumbing:
    """Role mapping, normalisation, and the two-picker entity step."""

    @pytest.mark.unit
    def test_panel_role_map_tags_each_section(self):
        assert _roles() == {TOP: "top", BOTTOM: "bottom"}

    @pytest.mark.unit
    def test_normalize_folds_sections_into_entities(self):
        assert _policy().normalize_entities(OPTS)[CONF_ENTITIES] == [TOP, BOTTOM]

    @pytest.mark.unit
    def test_entity_step_schema_has_two_required_pickers(self):
        schema = _policy().entity_step_schema()
        keys = {str(m) for m in schema.schema}
        assert keys == {CONF_SPLIT_PANEL_TOP, CONF_SPLIT_PANEL_BOTTOM}
        assert all(isinstance(m, vol.Required) for m in schema.schema)

    @pytest.mark.unit
    def test_exposes_dual_panel_sensors_not_dual_axis(self):
        p = _policy()
        assert p.exposes_dual_panel_sensors is True
        assert p.exposes_dual_axis_sensor is False


class TestResolveAxisTargets:
    """Bottom tracks the sun; top stays open; safety mirrors."""

    @pytest.mark.unit
    def test_bottom_tracks_state_top_open(self):
        targets = _policy().resolve_axis_targets(_result(), 40, ENTS, _roles())
        by_role = {t.role: t.value for t in targets}
        assert by_role == {"top": 100, "bottom": 40}
        assert all(t.strategy is DispatchStrategy.INDEPENDENT for t in targets)

    @pytest.mark.unit
    def test_inverse_flips_top_open_constant_only(self):
        targets = _policy().resolve_axis_targets(_result(), 40, ENTS, _roles(), True)
        # Bottom keeps the coordinator-supplied (already inverse-applied) state;
        # only the top-open constant flips to 0.
        assert {t.role: t.value for t in targets} == {"top": 0, "bottom": 40}

    @pytest.mark.unit
    def test_safety_bypass_mirrors_state_to_both(self):
        targets = _policy().resolve_axis_targets(
            _result(
                position=0,
                control_method=ControlMethod.WEATHER,
                reason="weather",
                bypass_auto_control=True,
            ),
            0,
            ENTS,
            _roles(),
        )
        assert {t.role: t.value for t in targets} == {"top": 0, "bottom": 0}

    @pytest.mark.unit
    def test_unknown_role_falls_back_to_broadcast_state(self):
        targets = _policy().resolve_axis_targets(
            _result(), 55, [BOTTOM, "cover.mystery"], {BOTTOM: "bottom"}
        )
        by_entity = {t.entity_id: t.value for t in targets}
        assert by_entity["cover.mystery"] == 55
        assert by_entity[BOTTOM] == 55
