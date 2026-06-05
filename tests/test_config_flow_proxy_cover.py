"""Config-flow plumbing for the opt-in proxy cover toggle."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from custom_components.adaptive_cover_pro.config_flow import _build_cover_entity_schema
from custom_components.adaptive_cover_pro.const import (
    CONF_DUAL_PANEL_BACK,
    CONF_DUAL_PANEL_FRONT,
    CONF_ENABLE_PROXY_COVER,
    CONF_ENTITIES,
    DEFAULT_ENABLE_PROXY_COVER,
    CoverType,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy


def _schema_defaults(schema: vol.Schema) -> dict[str, Any]:
    """Return {key_name: default_value} for every Optional key in ``schema``."""
    out: dict[str, Any] = {}
    for key in schema.schema:
        if isinstance(key, vol.Optional):
            default = key.default
            value = default() if callable(default) else default
            out[str(key)] = value
    return out


def test_cover_entity_schema_contains_enable_proxy_cover_field() -> None:
    """``_build_cover_entity_schema`` exposes the new toggle."""
    schema = _build_cover_entity_schema(CoverType.BLIND)
    names = [str(k) for k in schema.schema]
    assert CONF_ENABLE_PROXY_COVER in names


def test_proxy_cover_defaults_to_false() -> None:
    """The toggle defaults to the DEFAULT_ENABLE_PROXY_COVER value (False)."""
    schema = _build_cover_entity_schema(CoverType.BLIND)
    defaults = _schema_defaults(schema)
    assert (
        defaults.get(CONF_ENABLE_PROXY_COVER) is DEFAULT_ENABLE_PROXY_COVER
    ), f"expected default False; got {defaults!r}"


def test_proxy_cover_schema_validates_boolean_round_trip() -> None:
    """User input of ``True`` round-trips through the schema."""
    schema = _build_cover_entity_schema(CoverType.BLIND)
    out = schema({CONF_ENTITIES: [], CONF_ENABLE_PROXY_COVER: True})
    assert out[CONF_ENABLE_PROXY_COVER] is True


def test_dual_panel_entity_step_shows_two_role_pickers() -> None:
    """Dual-panel replaces the single multi-select with two role pickers."""
    schema = _build_cover_entity_schema(CoverType.DUAL_PANEL)
    names = {str(k) for k in schema.schema}
    assert {CONF_DUAL_PANEL_FRONT, CONF_DUAL_PANEL_BACK} <= names
    assert CONF_ENTITIES not in names  # the flat multi-select is replaced
    assert CONF_ENABLE_PROXY_COVER in names  # shared fields still present


def test_dual_panel_normalize_folds_role_picks_into_entities() -> None:
    """On submit the role picks become the flat CONF_ENTITIES list."""
    policy = get_policy(CoverType.DUAL_PANEL)
    out = policy.normalize_entities(
        {CONF_DUAL_PANEL_FRONT: "cover.sheer", CONF_DUAL_PANEL_BACK: "cover.blackout"}
    )
    assert out[CONF_ENTITIES] == ["cover.sheer", "cover.blackout"]
