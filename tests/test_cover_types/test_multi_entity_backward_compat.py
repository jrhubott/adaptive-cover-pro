"""Backward-compatibility / rollback safety for the multi-entity cover types.

The dual-/split-panel types are purely additive. This pins the guarantees that
make installing and rolling back safe:

- the config-flow version is unchanged (a bump would make HA refuse the entry
  after a downgrade);
- existing single-entity cover types are inert under the new code paths
  (``resolve_axis_targets`` still broadcasts, no panel roles, default entity
  step, identity ``normalize_entities``);
- an unknown cover type (the situation after a rollback that predates these
  types) fails with a clear ``ValueError`` rather than crashing or corrupting
  state;
- the new types' config keys never leak into an existing type's valid option
  set, and the new types keep their own keys (so an upgrade→rollback→upgrade
  cycle preserves them).
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.config_flow import ConfigFlowHandler
from custom_components.adaptive_cover_pro.const import (
    CONF_DUAL_PANEL_BACK,
    CONF_DUAL_PANEL_BLACKOUT_TRIGGERS,
    CONF_DUAL_PANEL_FRONT,
    CONF_ENTITIES,
    CONF_SPLIT_PANEL_BOTTOM,
    CONF_SPLIT_PANEL_TOP,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.axis_target import DispatchStrategy

EXISTING_TYPES = [
    "cover_blind",
    "cover_awning",
    "cover_tilt",
    "cover_venetian",
    "cover_oscillating_awning",
]

NEW_KEYS = frozenset(
    {
        CONF_DUAL_PANEL_FRONT,
        CONF_DUAL_PANEL_BACK,
        CONF_DUAL_PANEL_BLACKOUT_TRIGGERS,
        CONF_SPLIT_PANEL_TOP,
        CONF_SPLIT_PANEL_BOTTOM,
    }
)


class TestRollbackSafety:
    """Guarantees that make a downgrade safe."""

    @pytest.mark.unit
    def test_config_flow_version_unchanged(self):
        # A version bump is exactly what blocks rollback — HA refuses an entry
        # whose version exceeds the installed integration. The new types reuse
        # the existing entry schema, so VERSION must stay at 3.
        assert ConfigFlowHandler.VERSION == 3

    @pytest.mark.unit
    def test_unknown_cover_type_fails_cleanly(self):
        # After a rollback to code predating these types, an entry whose type
        # the code doesn't know must raise a clear ValueError — not crash or
        # silently corrupt other covers.
        with pytest.raises(ValueError):
            get_policy("cover_a_type_from_the_future")


class TestExistingTypesInert:
    """The new code paths are no-ops for every pre-existing cover type."""

    @pytest.mark.unit
    @pytest.mark.parametrize("cover_type", EXISTING_TYPES)
    def test_no_panel_roles(self, cover_type):
        policy = get_policy(cover_type)
        assert policy.panel_role_map({CONF_ENTITIES: ["cover.a", "cover.b"]}) == {}

    @pytest.mark.unit
    @pytest.mark.parametrize("cover_type", EXISTING_TYPES)
    def test_entity_step_uses_default_single_picker(self, cover_type):
        # None => config flow falls back to the historic CONF_ENTITIES picker.
        assert get_policy(cover_type).entity_step_schema() is None

    @pytest.mark.unit
    @pytest.mark.parametrize("cover_type", EXISTING_TYPES)
    def test_normalize_entities_is_identity(self, cover_type):
        opts = {CONF_ENTITIES: ["cover.a"], "name": "x"}
        assert get_policy(cover_type).normalize_entities(opts) == opts

    @pytest.mark.unit
    @pytest.mark.parametrize("cover_type", EXISTING_TYPES)
    def test_resolve_axis_targets_still_broadcasts(self, cover_type):
        policy = get_policy(cover_type)
        entities = ["cover.a", "cover.b"]
        targets = policy.resolve_axis_targets(
            result=None, state=55, entities=entities, panel_role={}
        )
        assert [t.entity_id for t in targets] == entities
        assert all(t.value == 55 for t in targets)
        assert all(t.role is None for t in targets)
        assert all(t.strategy is DispatchStrategy.INDEPENDENT for t in targets)


class TestConfigKeyIsolation:
    """New-type keys don't bleed into existing types; new types keep theirs."""

    @pytest.mark.unit
    @pytest.mark.parametrize("cover_type", EXISTING_TYPES)
    def test_new_keys_absent_from_existing_type_options(self, cover_type):
        assert NEW_KEYS.isdisjoint(get_policy(cover_type).live_option_keys())

    @pytest.mark.unit
    def test_dual_panel_keeps_its_keys(self):
        keys = get_policy("cover_dual_panel").live_option_keys()
        assert {
            CONF_DUAL_PANEL_FRONT,
            CONF_DUAL_PANEL_BACK,
            CONF_DUAL_PANEL_BLACKOUT_TRIGGERS,
        } <= keys

    @pytest.mark.unit
    def test_split_panel_keeps_its_keys(self):
        keys = get_policy("cover_split_panel").live_option_keys()
        assert {CONF_SPLIT_PANEL_TOP, CONF_SPLIT_PANEL_BOTTOM} <= keys
