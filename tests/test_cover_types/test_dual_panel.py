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


def _sunset_cover(sunset_valid: bool):
    """Minimal cover stub exposing ``sunset_valid`` (the privacy-window signal)."""
    cover = MagicMock()
    cover.sunset_valid = sunset_valid
    return cover


def _resolve(
    policy: DualPanelPolicy,
    *,
    control_method: ControlMethod = ControlMethod.SOLAR,
    position: int = 40,
    sunset_valid: bool = False,
    bypass: bool = False,
    floor_clamp: bool = False,
    interp: bool = False,
    sol_elev: float = 45.0,
    triggers=None,
    front: str | None = _FRONT,
    inverse: bool = False,
) -> PipelineResult:
    """Run ``post_pipeline_resolve`` so the per-cycle dispatch cache is set."""
    opts = _opts(triggers=triggers, front=front, inverse=inverse)
    if interp:
        from custom_components.adaptive_cover_pro.const import CONF_INTERP

        opts[CONF_INTERP] = True
    kw = _resolve_kwargs(options=opts, sol_elev=sol_elev)
    result = PipelineResult(
        position=position,
        control_method=control_method,
        reason="test",
        bypass_auto_control=bypass,
        position_constraint_applied=floor_clamp,
    )
    return policy.post_pipeline_resolve(result, cover=_sunset_cover(sunset_valid), **kw)


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

    def test_privacy_uses_sunset_window(self) -> None:
        # Privacy fires on the astronomical sunset WINDOW (cover.sunset_valid),
        # independent of whether a sunset_position is configured (#996 Finding 2).
        policy = DualPanelPolicy()
        _resolve(policy, sunset_valid=True, triggers=["privacy"])
        assert policy.resolve_entity_target(_BACK, 40) == POSITION_CLOSED
        policy2 = DualPanelPolicy()
        _resolve(policy2, sunset_valid=False, triggers=["privacy"])
        assert policy2.resolve_entity_target(_BACK, 40) == POSITION_OPEN

    def test_empty_trigger_set_never_deploys(self) -> None:
        policy = DualPanelPolicy()
        # Every condition active, but no trigger configured → never deploys.
        _resolve(
            policy,
            control_method=ControlMethod.SUMMER,
            sunset_valid=True,
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

    def test_empty_string_front_is_identity_for_all(self) -> None:
        # A stored empty-string front must degrade to identity, not flip every
        # entity to the blackout substitution (#996 Finding 6).
        policy = DualPanelPolicy()
        _resolve(
            policy,
            control_method=ControlMethod.SUMMER,
            triggers=["heat"],
            front="",
        )
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
    """Inverse-state swaps open/closed in the back's OWN device wire space.

    The cache is the only source of that space; no caller can override it
    (#1035). Contrast ``DayNightShadePolicy``, whose middle rail IS derived
    from the front's dispatched value and therefore must consult the frame.
    """

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

    def test_back_wire_space_ignores_caller_frame(self) -> None:
        """No caller can push the back into a wire space its device is not in.

        #1035: ``compute_layered`` discards ``front`` when computing ``back``,
        so the frame naming the incoming ``position`` (#993/#1027) says nothing
        about how this panel is wired. Its wire space is device semantics only
        (#996 Finding 1) — the same invariant ``TestResolveInverseIndependent
        OfClamp`` states for the clamp axis, now closed against the caller axis
        too. This replaces ``test_explicit_inverted_kwarg_overrides_cache``,
        whose ``inverted=False`` assertion was the #1035 defect written as an
        expectation and contradicted that sibling class outright.
        """
        from custom_components.adaptive_cover_pro.const import (
            CONF_INTERP,
            CONF_INTERP_END,
            CONF_INTERP_START,
        )
        from custom_components.adaptive_cover_pro.managers.manual_override import (
            inverse_state,
        )
        from custom_components.adaptive_cover_pro.position_utils import (
            interpolate_position,
        )

        # 1. Cache non-inverse — a caller asking for inversion cannot impose it.
        plain = DualPanelPolicy()
        _resolve(plain, control_method=ControlMethod.SUMMER, triggers=["heat"])
        assert plain.resolve_entity_target(_BACK, 40, inverted=True) == POSITION_CLOSED

        # 2. Cache inverse — the floor-raise seam's ``inverted=False`` cannot
        #    un-invert it. THIS assertion is issue #1035.
        inv = DualPanelPolicy()
        _resolve(
            inv, control_method=ControlMethod.SUMMER, triggers=["heat"], inverse=True
        )
        assert inv.resolve_entity_target(_BACK, 40, inverted=False) == inverse_state(
            POSITION_CLOSED
        )

        # 3. Cache interpolating — a broadcast seam's implicit
        #    ``interpolated=False`` cannot defeat the calibration curve
        #    (#996 Finding 5; seams 4/5).
        interp = DualPanelPolicy()
        opts = _opts(triggers=["heat"], front=_FRONT)
        opts[CONF_INTERP] = True
        opts[CONF_INTERP_START] = 10
        opts[CONF_INTERP_END] = 90
        interp.post_pipeline_resolve(
            PipelineResult(
                position=40, control_method=ControlMethod.SUMMER, reason="t"
            ),
            cover=_sunset_cover(False),
            **_resolve_kwargs(options=opts),
        )
        expected = int(round(interpolate_position(POSITION_CLOSED, 10, 90, None, None)))
        assert interp.resolve_entity_target(_BACK, 40, inverted=False) == expected

        # 4. Cache non-interpolating — a caller cannot impose a curve either.
        assert (
            plain.resolve_entity_target(_BACK, 40, interpolated=True) == POSITION_CLOSED
        )


class TestResolveInverseIndependentOfClamp:
    """The back's inverse wire-space is device semantics only (#996 Finding 1).

    The back target is an ABSOLUTE SUBSTITUTION (open/closed), not a transform
    of the front's dispatched value, so its wire space must NOT depend on whether
    the front's value was floor-clamped. Inverse-state alone decides it.
    """

    def test_deploy_inverse_on_floor_clamp_stays_inverted(self) -> None:
        from custom_components.adaptive_cover_pro.managers.manual_override import (
            inverse_state,
        )

        policy = DualPanelPolicy()
        _resolve(
            policy,
            control_method=ControlMethod.SUMMER,
            triggers=["heat"],
            inverse=True,
            floor_clamp=True,
        )
        # Deployed → device physically CLOSED → wire inverse_state(CLOSED)=100.
        # The floor clamp on the FRONT must not un-invert the back's blackout.
        assert policy.resolve_entity_target(_BACK, 40) == inverse_state(POSITION_CLOSED)

    def test_retract_inverse_on_floor_clamp_stays_inverted(self) -> None:
        from custom_components.adaptive_cover_pro.managers.manual_override import (
            inverse_state,
        )

        policy = DualPanelPolicy()
        _resolve(
            policy,
            control_method=ControlMethod.SOLAR,
            triggers=["heat"],
            inverse=True,
            floor_clamp=True,
        )
        # Retracted → device physically OPEN → wire inverse_state(OPEN)=0.
        assert policy.resolve_entity_target(_BACK, 40) == inverse_state(POSITION_OPEN)

    def test_non_clamp_matches_clamp(self) -> None:
        """Clamp and non-clamp cycles produce the SAME back wire target."""
        clamp = DualPanelPolicy()
        _resolve(
            clamp,
            control_method=ControlMethod.SUMMER,
            triggers=["heat"],
            inverse=True,
            floor_clamp=True,
        )
        plain = DualPanelPolicy()
        _resolve(
            plain,
            control_method=ControlMethod.SUMMER,
            triggers=["heat"],
            inverse=True,
            floor_clamp=False,
        )
        assert clamp.resolve_entity_target(_BACK, 40) == plain.resolve_entity_target(
            _BACK, 40
        )

    def test_inverse_off_floor_clamp_is_canonical(self) -> None:
        policy = DualPanelPolicy()
        _resolve(
            policy,
            control_method=ControlMethod.SUMMER,
            triggers=["heat"],
            inverse=False,
            floor_clamp=True,
        )
        assert policy.resolve_entity_target(_BACK, 40) == POSITION_CLOSED


class TestResolveInterpolation:
    """With interpolation on the back endpoints go through the calibration curve.

    Interpolation and inverse-state are mutually exclusive at dispatch (the
    coordinator ignores inverse under interp), so the back must interpolate its
    canonical 0/100 rather than send raw values that could overdrive a
    narrower-calibrated device (#996 Finding 5).
    """

    def _interp_opts(self, base: dict) -> dict:
        from custom_components.adaptive_cover_pro.const import (
            CONF_INTERP_END,
            CONF_INTERP_START,
        )

        # A device whose physical travel is calibrated to 10..90 %.
        base[CONF_INTERP_START] = 10
        base[CONF_INTERP_END] = 90
        return base

    def test_back_deploy_is_interpolated(self) -> None:
        from custom_components.adaptive_cover_pro.const import (
            CONF_INTERP,
            CONF_INTERP_END,
            CONF_INTERP_START,
        )
        from custom_components.adaptive_cover_pro.position_utils import (
            interpolate_position,
        )

        policy = DualPanelPolicy()
        opts = _opts(triggers=["heat"], front=_FRONT)
        opts[CONF_INTERP] = True
        opts[CONF_INTERP_START] = 10
        opts[CONF_INTERP_END] = 90
        kw = _resolve_kwargs(options=opts)
        result = PipelineResult(
            position=40, control_method=ControlMethod.SUMMER, reason="t"
        )
        policy.post_pipeline_resolve(result, cover=_sunset_cover(False), **kw)
        expected = int(round(interpolate_position(POSITION_CLOSED, 10, 90, None, None)))
        assert policy.resolve_entity_target(_BACK, 40) == expected
        # Overdrive guard: not the raw canonical 0.
        assert policy.resolve_entity_target(_BACK, 40) != POSITION_CLOSED

    def test_back_retract_is_interpolated(self) -> None:
        from custom_components.adaptive_cover_pro.const import (
            CONF_INTERP,
            CONF_INTERP_END,
            CONF_INTERP_START,
        )
        from custom_components.adaptive_cover_pro.position_utils import (
            interpolate_position,
        )

        policy = DualPanelPolicy()
        opts = _opts(triggers=["heat"], front=_FRONT)
        opts[CONF_INTERP] = True
        opts[CONF_INTERP_START] = 10
        opts[CONF_INTERP_END] = 90
        kw = _resolve_kwargs(options=opts)
        result = PipelineResult(
            position=40, control_method=ControlMethod.SOLAR, reason="t"
        )
        policy.post_pipeline_resolve(result, cover=_sunset_cover(False), **kw)
        expected = int(round(interpolate_position(POSITION_OPEN, 10, 90, None, None)))
        assert policy.resolve_entity_target(_BACK, 40) == expected

    def test_interp_wins_over_inverse(self) -> None:
        """Under interp the back is interpolated, not inverted (matches state)."""
        from custom_components.adaptive_cover_pro.const import (
            CONF_INTERP,
            CONF_INTERP_END,
            CONF_INTERP_START,
        )
        from custom_components.adaptive_cover_pro.position_utils import (
            interpolate_position,
        )

        policy = DualPanelPolicy()
        opts = _opts(triggers=["heat"], front=_FRONT, inverse=True)
        opts[CONF_INTERP] = True
        opts[CONF_INTERP_START] = 10
        opts[CONF_INTERP_END] = 90
        kw = _resolve_kwargs(options=opts)
        result = PipelineResult(
            position=40, control_method=ControlMethod.SUMMER, reason="t"
        )
        policy.post_pipeline_resolve(result, cover=_sunset_cover(False), **kw)
        expected = int(round(interpolate_position(POSITION_CLOSED, 10, 90, None, None)))
        assert policy.resolve_entity_target(_BACK, 40) == expected


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

    def test_no_trace_step_when_no_front_configured(self) -> None:
        # The back decision is never applied without a front, so no trace step
        # should appear for a cycle that can't dispatch it (#996 Finding 8).
        policy = DualPanelPolicy()
        out = _resolve(
            policy, control_method=ControlMethod.SUMMER, triggers=["heat"], front=None
        )
        assert [s for s in out.decision_trace if s.handler == "dual_panel_back"] == []

    def test_no_trace_step_on_bypass_cycle(self) -> None:
        # A safety/bypass winner overwrites every entity — the back is never
        # substituted, so no dual_panel_back step should be recorded.
        policy = DualPanelPolicy()
        out = _resolve(
            policy, control_method=ControlMethod.SUMMER, triggers=["heat"], bypass=True
        )
        assert [s for s in out.decision_trace if s.handler == "dual_panel_back"] == []


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
