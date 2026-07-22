"""Unit tests for the dual-panel cover type (#996).

A dual-panel cover drives TWO independent HA shade entities over one window:

* the **front** is a sheer / light-filtering shade that tracks the sun exactly
  like a plain vertical blind — ``resolve_entity_target`` returns its adaptive
  position verbatim;
* the **back** is a blackout shade that deploys (closes) only when one of the
  configured triggers ``{heat, privacy, night}`` is active, and retracts (opens)
  otherwise.

Unlike the day/night Model C shade (#993) the two panels are INDEPENDENT — there
is no ``M >= P`` no-pass coupling. The back's deploy/retract decision is composed
once through the pure ``engine/covers/layered.py`` helpers and dispatched on the
shipped ``resolve_entity_target`` / ``post_pipeline_resolve`` seam.
"""

from __future__ import annotations

from unittest.mock import MagicMock


from custom_components.adaptive_cover_pro.const import (
    CONF_DUAL_PANEL_BLACKOUT_TRIGGERS,
    CONF_DUAL_PANEL_FRONT_ENTITY,
    DEFAULT_DUAL_PANEL_BLACKOUT_TRIGGERS,
    DUAL_PANEL_BLACKOUT_TRIGGERS,
    DUAL_PANEL_TRIGGER_HEAT,
    DUAL_PANEL_TRIGGER_NIGHT,
    DUAL_PANEL_TRIGGER_PRIVACY,
    POSITION_CLOSED,
    POSITION_OPEN,
    ControlMethod,
    CoverType,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.base import (
    POSITION_AXIS,
)
from custom_components.adaptive_cover_pro.cover_types.dual_panel import (
    DualPanelPolicy,
)
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

# ---------------------------------------------------------------------------
# Step 2 — enum, display name, trigger constants
# ---------------------------------------------------------------------------


def test_cover_type_registered_in_enum() -> None:
    """The new sensor_type string resolves to a CoverType with a display name."""
    assert CoverType("cover_dual_panel") is CoverType.DUAL_PANEL
    assert CoverType.DUAL_PANEL.display_name == "Dual Panel Shade"


def test_trigger_constants() -> None:
    """The three trigger literals + the canonical tuple + the empty default."""
    assert DUAL_PANEL_TRIGGER_HEAT == "heat"
    assert DUAL_PANEL_TRIGGER_PRIVACY == "privacy"
    assert DUAL_PANEL_TRIGGER_NIGHT == "night"
    assert DUAL_PANEL_BLACKOUT_TRIGGERS == (
        DUAL_PANEL_TRIGGER_HEAT,
        DUAL_PANEL_TRIGGER_PRIVACY,
        DUAL_PANEL_TRIGGER_NIGHT,
    )
    assert DEFAULT_DUAL_PANEL_BLACKOUT_TRIGGERS == ()


def test_conf_keys() -> None:
    """The two config-wire keys are stable strings."""
    assert CONF_DUAL_PANEL_FRONT_ENTITY == "dual_panel_front_entity"
    assert CONF_DUAL_PANEL_BLACKOUT_TRIGGERS == "dual_panel_blackout_triggers"


# ---------------------------------------------------------------------------
# Step 3 — policy registration + ClassVar gates + geometry surfaces
# ---------------------------------------------------------------------------


def test_policy_registered_with_flags() -> None:
    policy = get_policy("cover_dual_panel")
    assert isinstance(policy, DualPanelPolicy)
    # Single position axis — the front sheer sun-tracks like a vertical blind.
    assert policy.axes == (POSITION_AXIS,)
    assert policy.supports_glare_zones is True
    assert policy.supports_return_to_default_switch is True
    assert policy.supports_fov_compute is True
    assert policy.exposes_dual_axis_sensor is False
    assert policy.custom_position_includes_tilt is False
    assert policy.wiki_anchor() == "Configuration-Dual-Panel"
    assert policy.display_label() == "Dual Panel Shade"


def test_geometry_schema_has_vertical_plus_dual_panel_keys() -> None:
    import voluptuous as vol

    from custom_components.adaptive_cover_pro.const import CONF_HEIGHT_WIN

    schema = DualPanelPolicy().geometry_schema()
    keys = {(k.schema if isinstance(k, vol.Marker) else k) for k in schema.schema}
    # Vertical window geometry (the front sheer is a plain vertical blind).
    assert CONF_HEIGHT_WIN in keys
    # Plus the dual-panel designator + trigger-set option.
    assert CONF_DUAL_PANEL_FRONT_ENTITY in keys
    assert CONF_DUAL_PANEL_BLACKOUT_TRIGGERS in keys


def test_geometry_length_keys_are_vertical() -> None:
    from custom_components.adaptive_cover_pro.cover_types.blind import (
        VERTICAL_LENGTH_KEYS,
    )

    assert DualPanelPolicy().geometry_length_keys() == VERTICAL_LENGTH_KEYS


def test_entity_selector_filter_is_plain_cover_domain() -> None:
    flt = DualPanelPolicy().entity_selector_filter()
    assert flt.get("domain") == "cover"
    # No tilt-capability requirement — both panels are plain position covers.
    assert "supported_features" not in flt


def test_build_calc_engine_returns_vertical_cover() -> None:
    from tests.cover_helpers import make_cover_config, make_vertical_config

    from custom_components.adaptive_cover_pro.engine.covers import (
        AdaptiveVerticalCover,
    )

    svc = MagicMock()
    svc.get_vertical_data.return_value = make_vertical_config()
    svc.get_glare_zones_config.return_value = None
    engine = DualPanelPolicy().build_calc_engine(
        logger=MagicMock(),
        sol_azi=180.0,
        sol_elev=45.0,
        sun_data=MagicMock(),
        config=make_cover_config(),
        config_service=svc,
        options={},
    )
    assert isinstance(engine, AdaptiveVerticalCover)


class TestCapabilityWarnings:
    """Dual-panel needs set_position on each panel and a valid front entity."""

    def test_warns_on_missing_set_position(self) -> None:
        warn = DualPanelPolicy().cover_capability_warnings(
            {"cover.a": {"has_set_position": False}}
        )
        assert any("set_position" in w for w in warn)

    def test_no_warning_when_set_position_present(self) -> None:
        warn = DualPanelPolicy().cover_capability_warnings(
            {"cover.a": {"has_set_position": True}}
        )
        assert warn == []

    def test_warns_when_front_entity_not_among_covers(self) -> None:
        opts = {CONF_DUAL_PANEL_FRONT_ENTITY: "cover.not_configured"}
        warn = DualPanelPolicy().capability_warnings_for_options(
            {
                "cover.front": {"has_set_position": True},
                "cover.back": {"has_set_position": True},
            },
            opts,
        )
        assert any("cover.not_configured" in w for w in warn)

    def test_no_front_warning_when_front_is_a_configured_cover(self) -> None:
        opts = {CONF_DUAL_PANEL_FRONT_ENTITY: "cover.front"}
        warn = DualPanelPolicy().capability_warnings_for_options(
            {
                "cover.front": {"has_set_position": True},
                "cover.back": {"has_set_position": True},
            },
            opts,
        )
        assert warn == []


# ---------------------------------------------------------------------------
# Step 4 — the resolve seam (front identity, independent blackout back)
# ---------------------------------------------------------------------------

_FRONT = "cover.front_sheer"
_BACK = "cover.back_blackout"


def _resolve_kwargs(**overrides):
    from tests.cover_helpers import make_cover_config, make_vertical_config

    svc = MagicMock()
    svc.get_vertical_data.return_value = make_vertical_config()
    svc.get_glare_zones_config.return_value = None
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    kw = {
        "logger": MagicMock(),
        "sol_azi": 180.0,
        "sol_elev": 45.0,
        "sun_data": sun_data,
        "config": make_cover_config(),
        "config_service": svc,
        "options": {},
    }
    kw.update(overrides)
    return kw


def _opts(*, triggers=None, front: str | None = _FRONT, inverse: bool = False) -> dict:
    opts: dict = {}
    if front is not None:
        opts[CONF_DUAL_PANEL_FRONT_ENTITY] = front
    if triggers is not None:
        opts[CONF_DUAL_PANEL_BLACKOUT_TRIGGERS] = list(triggers)
    if inverse:
        from custom_components.adaptive_cover_pro.const import CONF_INVERSE_STATE

        opts[CONF_INVERSE_STATE] = True
    return opts


def _resolve(
    policy: DualPanelPolicy,
    *,
    control_method: ControlMethod = ControlMethod.SOLAR,
    position: int = 40,
    is_sunset_active: bool = False,
    bypass: bool = False,
    sol_elev: float = 45.0,
    triggers=None,
    front: str | None = _FRONT,
    inverse: bool = False,
) -> PipelineResult:
    """Run ``post_pipeline_resolve`` so the per-cycle dispatch cache is set."""
    kw = _resolve_kwargs(
        options=_opts(triggers=triggers, front=front, inverse=inverse),
        sol_elev=sol_elev,
    )
    result = PipelineResult(
        position=position,
        control_method=control_method,
        reason="test",
        is_sunset_active=is_sunset_active,
        bypass_auto_control=bypass,
    )
    return policy.post_pipeline_resolve(result, cover=None, **kw)


class TestResolveFrontPassThrough:
    """The front sheer panel always dispatches its adaptive position verbatim."""

    def test_front_identity_after_resolve(self) -> None:
        policy = DualPanelPolicy()
        _resolve(policy, triggers=["heat"], control_method=ControlMethod.SUMMER)
        assert policy.resolve_entity_target(_FRONT, 40) == 40
        assert policy.resolve_entity_target(_FRONT, 73) == 73


class TestResolveBackDeployDecision:
    """The back blackout deploys/retracts on the configured trigger set."""

    def test_back_deploys_on_heat(self) -> None:
        policy = DualPanelPolicy()
        _resolve(policy, control_method=ControlMethod.SUMMER, triggers=["heat"])
        # Deployed → closed (0 in open-percent, non-inverse).
        assert policy.resolve_entity_target(_BACK, 40) == POSITION_CLOSED

    def test_back_deploys_on_extreme_heat(self) -> None:
        policy = DualPanelPolicy()
        _resolve(policy, control_method=ControlMethod.EXTREME_HEAT, triggers=["heat"])
        assert policy.resolve_entity_target(_BACK, 40) == POSITION_CLOSED

    def test_back_retracts_with_no_active_trigger(self) -> None:
        policy = DualPanelPolicy()
        _resolve(policy, control_method=ControlMethod.SOLAR, triggers=["heat"])
        # No heat/privacy/night active → retracted (open, 100).
        assert policy.resolve_entity_target(_BACK, 40) == POSITION_OPEN

    def test_night_uses_sol_elev(self) -> None:
        policy = DualPanelPolicy()
        _resolve(policy, sol_elev=-3.0, triggers=["night"])
        assert policy.resolve_entity_target(_BACK, 40) == POSITION_CLOSED
        # Daytime (elevation positive) → not deployed.
        policy2 = DualPanelPolicy()
        _resolve(policy2, sol_elev=10.0, triggers=["night"])
        assert policy2.resolve_entity_target(_BACK, 40) == POSITION_OPEN

    def test_privacy_uses_is_sunset_active(self) -> None:
        policy = DualPanelPolicy()
        _resolve(policy, is_sunset_active=True, triggers=["privacy"])
        assert policy.resolve_entity_target(_BACK, 40) == POSITION_CLOSED
        policy2 = DualPanelPolicy()
        _resolve(policy2, is_sunset_active=False, triggers=["privacy"])
        assert policy2.resolve_entity_target(_BACK, 40) == POSITION_OPEN

    def test_empty_trigger_set_never_deploys(self) -> None:
        policy = DualPanelPolicy()
        # Every condition active, but no trigger configured → never deploys.
        _resolve(
            policy,
            control_method=ControlMethod.SUMMER,
            is_sunset_active=True,
            sol_elev=-5.0,
            triggers=[],
        )
        assert policy.resolve_entity_target(_BACK, 40) == POSITION_OPEN

    def test_active_trigger_not_configured_does_not_deploy(self) -> None:
        policy = DualPanelPolicy()
        # Heat active but only "privacy" configured → no deploy.
        _resolve(policy, control_method=ControlMethod.SUMMER, triggers=["privacy"])
        assert policy.resolve_entity_target(_BACK, 40) == POSITION_OPEN


class TestResolveNoFrontConfigured:
    """With no front designated the cover degrades to a plain vertical blind."""

    def test_identity_for_all_entities(self) -> None:
        policy = DualPanelPolicy()
        _resolve(
            policy,
            control_method=ControlMethod.SUMMER,
            triggers=["heat"],
            front=None,
        )
        # Even a would-be back entity passes through untouched.
        assert policy.resolve_entity_target(_BACK, 40) == 40
        assert policy.resolve_entity_target(_FRONT, 40) == 40


class TestResolveIndependence:
    """The back is NOT coupled to the front — no ``M >= P`` no-pass clamp."""

    def test_back_deployed_is_not_clamped_to_front(self) -> None:
        policy = DualPanelPolicy()
        _resolve(policy, control_method=ControlMethod.SUMMER, triggers=["heat"])
        # Front sits low (20) or high (90); the deployed back is a pure closed
        # value regardless — it never tracks or clamps to the front position.
        assert policy.resolve_entity_target(_BACK, 20) == POSITION_CLOSED
        assert policy.resolve_entity_target(_BACK, 90) == POSITION_CLOSED

    def test_back_retracted_is_not_clamped_to_front(self) -> None:
        policy = DualPanelPolicy()
        _resolve(policy, control_method=ControlMethod.SOLAR, triggers=["heat"])
        # Retracted back is fully open regardless of a high front position —
        # the day/night Model C M >= P clamp is deliberately absent here.
        assert policy.resolve_entity_target(_BACK, 90) == POSITION_OPEN
        assert policy.resolve_entity_target(_BACK, 5) == POSITION_OPEN


class TestResolveInverseState:
    """Inverse-state swaps open/closed in wire space; kwarg overrides the cache."""

    def test_cached_inverse_swaps_open_closed(self) -> None:
        from custom_components.adaptive_cover_pro.managers.manual_override import (
            inverse_state,
        )

        deployed = DualPanelPolicy()
        _resolve(
            deployed,
            control_method=ControlMethod.SUMMER,
            triggers=["heat"],
            inverse=True,
        )
        # Deployed closed (0) inverts to wire 100.
        assert deployed.resolve_entity_target(_BACK, 40) == inverse_state(
            POSITION_CLOSED
        )

        retracted = DualPanelPolicy()
        _resolve(
            retracted,
            control_method=ControlMethod.SOLAR,
            triggers=["heat"],
            inverse=True,
        )
        assert retracted.resolve_entity_target(_BACK, 40) == inverse_state(
            POSITION_OPEN
        )

    def test_explicit_inverted_kwarg_overrides_cache(self) -> None:
        from custom_components.adaptive_cover_pro.managers.manual_override import (
            inverse_state,
        )

        policy = DualPanelPolicy()
        # Cache says non-inverse, but the broadcast seam passes inverted=True.
        _resolve(policy, control_method=ControlMethod.SUMMER, triggers=["heat"])
        assert policy.resolve_entity_target(_BACK, 40, inverted=True) == inverse_state(
            POSITION_CLOSED
        )
        # And inverted=False forces non-inverse even if the cache said invert.
        inv = DualPanelPolicy()
        _resolve(
            inv, control_method=ControlMethod.SUMMER, triggers=["heat"], inverse=True
        )
        assert inv.resolve_entity_target(_BACK, 40, inverted=False) == POSITION_CLOSED


class TestResolveBypassCycle:
    """A safety/bypass cycle passes every entity through untouched."""

    def test_bypass_makes_back_identity(self) -> None:
        policy = DualPanelPolicy()
        _resolve(
            policy,
            control_method=ControlMethod.SUMMER,
            triggers=["heat"],
            bypass=True,
        )
        # Safety wins: the back is NOT substituted, it dispatches verbatim.
        assert policy.resolve_entity_target(_BACK, 40) == 40


class TestResolveStaleCache:
    """The dispatch cache is recomputed every cycle."""

    def test_second_cycle_flips_decision(self) -> None:
        policy = DualPanelPolicy()
        _resolve(policy, control_method=ControlMethod.SUMMER, triggers=["heat"])
        assert policy.resolve_entity_target(_BACK, 40) == POSITION_CLOSED
        # Next cycle: no trigger active → the cache flips to retract.
        _resolve(policy, control_method=ControlMethod.SOLAR, triggers=["heat"])
        assert policy.resolve_entity_target(_BACK, 40) == POSITION_OPEN


class TestResolveDecisionTrace:
    """``post_pipeline_resolve`` records the back decision in the trace."""

    def test_trace_records_deploy_and_active_triggers(self) -> None:
        policy = DualPanelPolicy()
        out = _resolve(policy, control_method=ControlMethod.SUMMER, triggers=["heat"])
        steps = [s for s in out.decision_trace if s.handler == "dual_panel_back"]
        assert steps
        assert "heat" in steps[-1].reason
        # Position / tilt are left unchanged by the resolve.
        assert out.position == 40
        assert out.tilt is None


# ---------------------------------------------------------------------------
# Step 5 — config-summary geometry rendering
# ---------------------------------------------------------------------------


def test_summary_geometry_lines_render_front_and_triggers() -> None:
    from custom_components.adaptive_cover_pro.const import CONF_HEIGHT_WIN

    lines = DualPanelPolicy().summary_geometry_lines(
        {
            CONF_HEIGHT_WIN: 2.0,
            CONF_DUAL_PANEL_FRONT_ENTITY: "cover.sheer_front",
            CONF_DUAL_PANEL_BLACKOUT_TRIGGERS: ["heat", "privacy"],
        }
    )
    assert any("cover.sheer_front" in line for line in lines)
    assert any("heat, privacy" in line for line in lines)


def test_summary_geometry_lines_render_no_triggers() -> None:
    from custom_components.adaptive_cover_pro.const import CONF_HEIGHT_WIN

    lines = DualPanelPolicy().summary_geometry_lines({CONF_HEIGHT_WIN: 2.0})
    assert any("no triggers" in line for line in lines)
