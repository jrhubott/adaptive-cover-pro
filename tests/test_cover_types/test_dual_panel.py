"""Tests for the dual-panel (sheer front + blackout back) cover policy."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.adaptive_cover_pro.const import (
    CONF_DUAL_PANEL_BACK,
    CONF_DUAL_PANEL_BLACKOUT_TRIGGERS,
    CONF_DUAL_PANEL_FRONT,
    CONF_ENTITIES,
    ClimateStrategy,
    ControlMethod,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.axis_target import (
    DispatchStrategy,
)
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

FRONT = "cover.sheer"
BACK = "cover.blackout"
OPTS = {CONF_DUAL_PANEL_FRONT: FRONT, CONF_DUAL_PANEL_BACK: BACK}
ENTS = [FRONT, BACK]


def _policy(options: dict | None = None):
    p = get_policy("cover_dual_panel")
    p.panel_role_map(options or OPTS)  # primes blackout-trigger config
    return p


def _roles(options: dict | None = None) -> dict[str, str]:
    return get_policy("cover_dual_panel").panel_role_map(options or OPTS)


def _result(**kw) -> PipelineResult:
    base = {
        "position": kw.pop("position", 40),
        "control_method": kw.pop("control_method", ControlMethod.SOLAR),
        "reason": kw.pop("reason", "solar"),
    }
    base.update(kw)
    return PipelineResult(**base)


class TestRoleAndEntityPlumbing:
    """Role mapping, entity normalisation, and the two-picker entity step."""

    @pytest.mark.unit
    def test_panel_role_map_tags_each_entity(self):
        assert _roles() == {FRONT: "front", BACK: "back"}

    @pytest.mark.unit
    def test_panel_role_map_empty_when_unconfigured(self):
        assert get_policy("cover_dual_panel").panel_role_map({}) == {}

    @pytest.mark.unit
    def test_normalize_folds_roles_into_entities(self):
        out = get_policy("cover_dual_panel").normalize_entities(OPTS)
        assert out[CONF_ENTITIES] == [FRONT, BACK]

    @pytest.mark.unit
    def test_entity_step_schema_has_two_required_pickers(self):
        schema = get_policy("cover_dual_panel").entity_step_schema()
        keys = {str(m) for m in schema.schema}
        assert keys == {CONF_DUAL_PANEL_FRONT, CONF_DUAL_PANEL_BACK}
        assert all(isinstance(m, vol.Required) for m in schema.schema)

    @pytest.mark.unit
    def test_exposes_dual_panel_sensors_not_dual_axis(self):
        p = get_policy("cover_dual_panel")
        assert p.exposes_dual_panel_sensors is True
        assert p.exposes_dual_axis_sensor is False


class TestResolveAxisTargets:
    """Per-role target resolution across solar, climate, sunset, inverse."""

    @pytest.mark.unit
    def test_solar_no_trigger_front_tracks_back_open(self):
        p = _policy()
        targets = p.resolve_axis_targets(
            _result(position=40, is_sunset_active=False, climate_strategy=None),
            40,
            ENTS,
            _roles(),
        )
        by_role = {t.role: t.value for t in targets}
        assert by_role == {"front": 40, "back": 100}
        assert all(t.strategy is DispatchStrategy.INDEPENDENT for t in targets)

    @pytest.mark.unit
    def test_sunset_deploys_blackout(self):
        p = _policy()
        targets = p.resolve_axis_targets(
            _result(position=0, is_sunset_active=True), 0, ENTS, _roles()
        )
        assert {t.role: t.value for t in targets} == {"front": 0, "back": 0}

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "strategy",
        [ClimateStrategy.SUMMER_COOLING, ClimateStrategy.WINTER_INSULATION],
    )
    def test_climate_full_block_deploys_blackout(self, strategy):
        p = _policy()
        targets = p.resolve_axis_targets(
            _result(position=30, climate_strategy=strategy), 30, ENTS, _roles()
        )
        assert {t.role: t.value for t in targets} == {"front": 30, "back": 0}

    @pytest.mark.unit
    def test_winter_heating_does_not_deploy(self):
        p = _policy()
        targets = p.resolve_axis_targets(
            _result(climate_strategy=ClimateStrategy.WINTER_HEATING),
            55,
            ENTS,
            _roles(),
        )
        assert {t.role: t.value for t in targets}["back"] == 100

    @pytest.mark.unit
    def test_configured_triggers_gate_deploy(self):
        # Only "sunset" configured; a climate full-block must NOT deploy.
        p = _policy({**OPTS, CONF_DUAL_PANEL_BLACKOUT_TRIGGERS: ["sunset"]})
        targets = p.resolve_axis_targets(
            _result(climate_strategy=ClimateStrategy.SUMMER_COOLING),
            20,
            ENTS,
            _roles(),
        )
        assert {t.role: t.value for t in targets}["back"] == 100

    @pytest.mark.unit
    def test_inverse_state_flips_back_open_closed(self):
        p = _policy()
        # No deploy → back "open"; under inverse, open maps to 0.
        targets = p.resolve_axis_targets(
            _result(is_sunset_active=False), 60, ENTS, _roles(), True
        )
        assert {t.role: t.value for t in targets} == {"front": 60, "back": 0}
        # Deploy → back "closed"; under inverse, closed maps to 100.
        targets2 = p.resolve_axis_targets(
            _result(is_sunset_active=True), 40, ENTS, _roles(), True
        )
        assert {t.role: t.value for t in targets2}["back"] == 100

    @pytest.mark.unit
    def test_safety_bypass_mirrors_state_to_both(self):
        p = _policy()
        targets = p.resolve_axis_targets(
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
        assert {t.role: t.value for t in targets} == {"front": 0, "back": 0}

    @pytest.mark.unit
    def test_unknown_role_falls_back_to_broadcast_state(self):
        # An entity with no role mapping gets the broadcast state, not a crash.
        p = _policy()
        targets = p.resolve_axis_targets(
            _result(), 50, [FRONT, "cover.mystery"], {FRONT: "front"}
        )
        by_entity = {t.entity_id: t.value for t in targets}
        assert by_entity["cover.mystery"] == 50
