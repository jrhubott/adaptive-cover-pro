"""Method-level invariants every CoverTypePolicy — including a stub fifth cover type — must honour.

These tests parametrise over the four registered policies plus the synthetic
stubs in :mod:`stub_policy`. They catch the class of bug where adding a
fifth cover type breaks a default-hook contract that previously held only
by coincidence (e.g. ``cover_capability_warnings`` returning ``None``
instead of ``[]`` because the only callers happened to handle ``None``).

Two flavours of stub are exercised:

* ``StubSingleAxisPolicy`` — minimal one-axis policy. Catches assumptions
  that a fifth cover type would need any of the venetian-specific hooks.
* ``StubDualAxisPolicy`` — minimal two-axis policy. Catches assumptions
  that "dual-axis" implies ``isinstance(policy, VenetianPolicy)`` rather
  than ``len(policy.axes) == 2``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import voluptuous as vol

from custom_components.adaptive_cover_pro.const import CoverType
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.base import (
    CAP_HAS_SET_POSITION,
    CAP_HAS_SET_TILT_POSITION,
    ENTITY_FILTER_FEATURE_CAPS,
    AxisDescriptor,
    CoverAxis,
    CoverDescriptor,
    CoverTypePolicy,
)

from .stub_policy import (
    ALL_POLICIES_WITH_STUBS,
    StubDualAxisPolicy,
    StubSingleAxisPolicy,
    register_stub_policy,
)


@pytest.fixture(params=ALL_POLICIES_WITH_STUBS, ids=lambda p: p.cover_type)
def policy(request) -> CoverTypePolicy:
    """One policy instance per registered type + each stub."""
    return request.param()


# ---- Default-hook return-type contracts ---------------------------------- #


@pytest.mark.unit
def test_cover_capability_warnings_returns_list(policy: CoverTypePolicy) -> None:
    """Warnings is always a list — config flow extends it unconditionally."""
    assert isinstance(policy.cover_capability_warnings(known={}), list)


@pytest.mark.unit
def test_capability_warnings_for_options_matches_plain_by_default(
    policy: CoverTypePolicy,
) -> None:
    """The additive per-options hook defaults to the plain warnings (Liskov)."""
    known = {"cover.x": {"has_set_position": False, "has_set_tilt_position": False}}
    assert policy.capability_warnings_for_options(
        known, {}
    ) == policy.cover_capability_warnings(known)


@pytest.mark.unit
def test_prospective_capability_warnings_returns_list(policy: CoverTypePolicy) -> None:
    """Always a list — the "Change Cover Type" confirm step extends it (#1137)."""
    assert isinstance(policy.prospective_capability_warnings(known={}), list)


@pytest.mark.unit
@pytest.mark.parametrize(
    "known",
    [
        {"cover.x": {"has_set_position": False, "has_set_tilt_position": False}},
        {"cover.x": {"has_set_position": True, "has_set_tilt_position": False}},
        {"cover.x": {"has_set_position": False, "has_set_tilt_position": True}},
        {"cover.x": {"has_set_position": True, "has_set_tilt_position": True}},
    ],
    ids=["both_missing", "position_only", "tilt_only", "both_present"],
)
def test_prospective_defaults_to_plain_warnings(
    policy: CoverTypePolicy, known: dict
) -> None:
    """The pre-configuration hook defaults to the plain warnings (Liskov, #1137).

    Gated reflectively, not by cover-type name: a policy that overrides
    ``prospective_capability_warnings`` — today only ``DayNightShadePolicy``,
    deliberately tilt-relaxed because the control model isn't known before
    the geometry step — opts itself out of this invariant by definition and
    is skipped. Every non-overriding policy must match
    ``cover_capability_warnings`` across every combination of the two
    capability flags, not just "everything present": with both flags
    ``True`` every real policy already returns ``[]`` from both methods, so a
    hook silently rewritten to ``return []`` unconditionally — no longer
    delegating at all — would still pass that one case. Only the
    missing-capability combinations exercise the branches where a genuine
    delegation and a hardcoded empty list disagree, so a failure here means
    the hook has drifted from the plain method it is supposed to mirror by
    default.
    """
    if (
        type(policy).prospective_capability_warnings
        is not CoverTypePolicy.prospective_capability_warnings
    ):
        pytest.skip("policy legitimately overrides the hook")
    assert policy.prospective_capability_warnings(
        known
    ) == policy.cover_capability_warnings(known)


@pytest.mark.unit
def test_prospective_capability_warnings_override_allowlist() -> None:
    """Pin exactly which policies may opt out of the Liskov invariant above.

    ``test_prospective_defaults_to_plain_warnings`` skips any policy that
    overrides ``prospective_capability_warnings`` instead of failing it — a
    deliberate exemption for ``DayNightShadePolicy`` (#1137), whose control
    model isn't known until the following geometry step. But a ``skip`` has
    no failure mode of its own: nothing else pins *which* policies are
    allowed to take that exemption, so a future policy that overrides the
    hook for an unrelated reason would silently drop out of the invariant
    with no signal anywhere in the suite.

    This test closes that gap. It derives the overriding set reflectively
    from the same ``ALL_POLICIES_WITH_STUBS`` registry the parametrized test
    above draws its ``policy`` fixture from, and asserts it is exactly
    ``{CoverType.DAY_NIGHT_SHADE}``.

    Adding a legitimate second override is a deliberate act: when it
    happens, extend the right-hand side here on purpose, with the same kind
    of justification ``DayNightShadePolicy.prospective_capability_warnings``
    carries in its own docstring — don't widen this set to make a failure
    go away without reading why it fired.
    """
    overriding = {
        policy_cls.cover_type
        for policy_cls in ALL_POLICIES_WITH_STUBS
        if policy_cls.prospective_capability_warnings
        is not CoverTypePolicy.prospective_capability_warnings
    }
    assert overriding == {CoverType.DAY_NIGHT_SHADE}


@pytest.mark.unit
def test_disallowed_geometry_fields_returns_list(policy: CoverTypePolicy) -> None:
    """``options_service.validate_options_patch`` iterates this — never None."""
    result = policy.disallowed_geometry_fields(
        vertical_only=set(),
        awning_only=set(),
        tilt_only=set(),
    )
    assert isinstance(result, list)


@pytest.mark.unit
def test_glare_zones_config_safe_default(policy: CoverTypePolicy) -> None:
    """Default returns None; only BlindPolicy may return a GlareZonesConfig."""
    result = policy.glare_zones_config(MagicMock(), {})
    assert result is None or hasattr(result, "zones")


@pytest.mark.unit
def test_entity_selector_filter_targets_cover_domain(
    policy: CoverTypePolicy,
) -> None:
    """Every policy targets HA cover entities — the selector must say so."""
    flt = policy.entity_selector_filter()
    assert flt.get("domain") == "cover"


@pytest.mark.unit
def test_entity_filter_features_are_all_mapped(policy: CoverTypePolicy) -> None:
    """Every feature a policy's picker filters on must have a ``CAP_*`` counterpart.

    ``entities_satisfy_selector`` inverts the picker filter into a predicate via
    ``ENTITY_FILTER_FEATURE_CAPS``. A feature name missing from that map — a new
    cover type filtering on ``OPEN_TILT``, or a typo in an existing name — has no
    capability to test, so the "would the picker have admitted these covers?"
    question silently loses its content (issue #1132).
    """
    for feature in policy.entity_selector_filter().get("supported_features") or ():
        assert feature in ENTITY_FILTER_FEATURE_CAPS, (
            f"{policy.cover_type} filters on {feature!r}, which "
            "ENTITY_FILTER_FEATURE_CAPS does not map to a CAP_* flag"
        )


@pytest.mark.unit
def test_geometry_schema_is_voluptuous_schema(policy: CoverTypePolicy) -> None:
    """Geometry schema is always a ``vol.Schema``, never None or a raw dict."""
    schema = policy.geometry_schema()
    assert isinstance(schema, vol.Schema)


@pytest.mark.unit
def test_summary_geometry_lines_returns_list(policy: CoverTypePolicy) -> None:
    """Summary geometry block is always a list of strings."""
    lines = policy.summary_geometry_lines({})
    assert isinstance(lines, list)
    assert all(isinstance(line, str) for line in lines)


# ---- Hook signatures ----------------------------------------------------- #


@pytest.mark.unit
def test_is_in_tilt_suppression_returns_bool(policy: CoverTypePolicy) -> None:
    """Both positional and keyword forms return a bool — pinned for callbacks."""
    assert isinstance(policy.is_in_tilt_suppression("cover.x", 0.0), bool)
    assert isinstance(policy.is_in_tilt_suppression("cover.x", delta=10.0), bool)


@pytest.mark.unit
def test_targets_full_mechanical_endpoint_returns_bool(policy: CoverTypePolicy) -> None:
    """Every policy answers the endpoint predicate with a real bool (issue #897)."""
    from custom_components.adaptive_cover_pro.const import ControlMethod
    from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

    result = PipelineResult(position=0, control_method=ControlMethod.SOLAR, reason="t")
    assert isinstance(policy.targets_full_mechanical_endpoint(result), bool)


@pytest.mark.unit
def test_resolve_entity_target_identity_default(policy: CoverTypePolicy) -> None:
    """Every policy leaves a per-entity target unchanged by default (Model C hook).

    ``resolve_entity_target`` is the coordinator dispatch seam that lets a
    dual-rail day/night shade drive its two entities to different positions
    from one resolved state. Every other cover type — and an un-resolved
    day/night cycle — must return the position unchanged so the polymorphic
    hook is a safe identity at every dispatch site.
    """
    assert policy.resolve_entity_target("cover.x", 57) == 57
    assert policy.resolve_entity_target("cover.y", 0) == 0
    assert policy.resolve_entity_target("cover.z", 100) == 100


@pytest.mark.unit
def test_dispatch_order_key_default_is_zero(policy: CoverTypePolicy) -> None:
    """Every policy leaves the dispatch order untouched by default (issue #1115).

    ``dispatch_order_key`` feeds ``sorted(...)`` at the coordinator's dispatch
    seams. A constant key makes that a stable-sort no-op, so the user's
    config-flow pick order survives for every cover type that does not need
    rail sequencing.
    """
    assert policy.dispatch_order_key("cover.x") == 0
    assert policy.dispatch_order_key("cover.y") == 0


@pytest.mark.unit
def test_required_role_entity_missing_default_false(policy: CoverTypePolicy) -> None:
    """No cover type reports a missing role entity by default (B3, issue #1115).

    Only a cover type that binds a SECOND entity to a named physical role (the
    Model C day/night middle rail) can have that role unfilled. Every other
    policy — and a coherent day/night entry — must answer ``False`` so the
    generic B3 Repair never false-fires.
    """
    assert policy.required_role_entity_missing({}, ["cover.x", "cover.y"]) is False


@pytest.mark.unit
def test_position_for_intent_returns_open_or_closed(policy: CoverTypePolicy) -> None:
    """``position_for_intent`` returns 0 or 100, and the two intents differ."""
    pos_through = policy.position_for_intent(sun_through=True)
    pos_block = policy.position_for_intent(sun_through=False)
    assert pos_through in (0, 100)
    assert pos_block in (0, 100)
    # Otherwise the policy can't distinguish sun-through from block-sun.
    assert pos_through != pos_block


@pytest.mark.unit
def test_select_default_axis_returns_cover_axis(policy: CoverTypePolicy) -> None:
    """``select_default_axis`` always returns a ``CoverAxis``, never None."""
    # Empty caps → fallback through ``should_use_tilt``.
    axis = policy.select_default_axis(caps={})
    assert isinstance(axis, CoverAxis)
    # Full caps → primary axis wins.
    full_caps = {CAP_HAS_SET_POSITION: True, CAP_HAS_SET_TILT_POSITION: True}
    axis = policy.select_default_axis(caps=full_caps)
    assert isinstance(axis, CoverAxis)


@pytest.mark.unit
def test_axes_tuple_non_empty(policy: CoverTypePolicy) -> None:
    """Every policy declares at least one axis — selecting one must be safe."""
    assert len(policy.axes) >= 1


@pytest.mark.unit
def test_tilt_capability_contradiction_contract(policy: CoverTypePolicy) -> None:
    """A3 (#991): the predicate fires iff a declared tilt axis can't be driven.

    ``tilt_capability_contradiction`` is the single source of truth the A3
    runtime Repair consults — True means "this cover type drives a tilt axis the
    bound device can't honour". The Liskov-safe base default derives the answer
    from ``self.axes`` alone, so every registered policy and stub satisfies it
    without an override:

    * a tilt-declaring type (tilt / louvered_roof / venetian) on a device that
      lacks ``set_tilt_position`` → contradiction;
    * any type on a fully-capable device → never a contradiction;
    * a position-only cover reached via open/close (no ``set_tilt_position``) is
      explicitly OUT of scope — it only trips for a *declared* tilt axis, so a
      blind/awning never fires.
    """
    from custom_components.adaptive_cover_pro.cover_types.base import (
        CAP_HAS_CLOSE,
        CAP_HAS_OPEN,
    )

    has_tilt_axis = any(
        a.capability_key == CAP_HAS_SET_TILT_POSITION for a in policy.axes
    )

    # Device advertises position but NOT tilt → contradiction iff a tilt axis
    # is declared by this cover type.
    no_tilt = {CAP_HAS_SET_POSITION: True, CAP_HAS_SET_TILT_POSITION: False}
    assert policy.tilt_capability_contradiction(no_tilt) is has_tilt_axis

    # Fully-capable device → never a contradiction, for any cover type.
    full = {CAP_HAS_SET_POSITION: True, CAP_HAS_SET_TILT_POSITION: True}
    assert policy.tilt_capability_contradiction(full) is False

    # Open/close-only position cover (no set_position, no set_tilt_position):
    # the position axis is drivable via the open/close fallback, so only a
    # declared tilt axis can contradict — proving the out-of-scope carve-out.
    open_close = {CAP_HAS_OPEN: True, CAP_HAS_CLOSE: True}
    assert policy.tilt_capability_contradiction(open_close) is has_tilt_axis


# ---- Discovery descriptors (issue #725) ---------------------------------- #


@pytest.mark.unit
def test_describe_returns_cover_descriptor(policy: CoverTypePolicy) -> None:
    """``describe`` yields a ``CoverDescriptor`` for any conformant policy.

    Base-class defaults must satisfy this for a stub fifth cover type — no
    per-policy override required. One ``AxisDescriptor`` per declared axis,
    each carrying the axis name as its id and ``supported=True`` under full caps.
    """
    caps = {CAP_HAS_SET_POSITION: True, CAP_HAS_SET_TILT_POSITION: True}
    desc = policy.describe(caps=caps)
    assert isinstance(desc, CoverDescriptor)
    assert desc.cover_type == policy.cover_type
    assert len(desc.axes) == len(policy.axes)
    for axis_desc, axis in zip(desc.axes, policy.axes, strict=True):
        assert isinstance(axis_desc, AxisDescriptor)
        assert axis_desc.id == axis.name
        assert axis_desc.supported is True


@pytest.mark.unit
def test_supported_axes_returns_subset(policy: CoverTypePolicy) -> None:
    """``supported_axes`` returns a subset of declared axes; full caps → all."""
    caps = {CAP_HAS_SET_POSITION: True, CAP_HAS_SET_TILT_POSITION: True}
    supported = policy.supported_axes(caps)
    assert set(supported) <= set(policy.axes)
    assert len(supported) == len(policy.axes)


@pytest.mark.unit
def test_empty_axes_policy_describe_and_supported() -> None:
    """A policy with zero axes still describes cleanly via base defaults.

    Guards the Liskov contract that a hypothetical axis-less conformant policy
    (like the virtual entry types) needs no discovery-builder edits: ``describe``
    returns an empty axis tuple and ``supported_axes`` returns ``()``.
    """

    class _EmptyAxesPolicy(CoverTypePolicy):
        cover_type = "cover_empty_stub"

        def build_calc_engine(self, **kwargs):  # type: ignore[override]  # noqa: ARG002
            return MagicMock()

    policy = _EmptyAxesPolicy()
    desc = policy.describe(caps={})
    assert isinstance(desc, CoverDescriptor)
    assert desc.axes == ()
    assert policy.supported_axes({}) == ()


# ---- Registry-level invariants ------------------------------------------- #


@pytest.mark.unit
@pytest.mark.parametrize("policy_cls", [StubSingleAxisPolicy, StubDualAxisPolicy])
def test_register_stub_policy_round_trip(policy_cls) -> None:
    """A stub policy registers and unregisters via the context manager.

    Pins the invariant that ``POLICY_REGISTRY`` is mutable enough to accept
    a fifth cover type at test time — i.e. no hidden global state assumes
    only the four registered types.
    """
    with register_stub_policy(policy_cls):
        retrieved = get_policy(policy_cls.cover_type)
        assert isinstance(retrieved, policy_cls)

    # After the context exits, the stub is gone — the registry was restored.
    with pytest.raises(ValueError, match="Unsupported cover type"):
        get_policy(policy_cls.cover_type)


@pytest.mark.unit
def test_controls_cover_default_true() -> None:
    """``controls_cover`` defaults True; only virtual entry types opt out.

    The base default is ``True`` so adding the discriminator didn't require
    touching every policy. ``cover_building_profile`` and
    ``cover_command_queue`` are the shipped virtual entry types that register no
    platforms and have no axes, so they are the only policies allowed to report
    ``False``. Pinning both directions keeps the cover-contract suites and
    cover-only menus exercising every real cover type and guards against a real
    cover accidentally opting out.
    """
    from custom_components.adaptive_cover_pro.cover_types.base import CoverTypePolicy

    assert CoverTypePolicy.controls_cover is True

    from custom_components.adaptive_cover_pro.cover_types import POLICY_REGISTRY

    expected_non_cover = {"cover_building_profile", "cover_command_queue"}
    for cover_type, policy_cls in POLICY_REGISTRY.items():
        if cover_type in expected_non_cover:
            assert (
                policy_cls.controls_cover is False
            ), f"{cover_type} is a virtual entry type — controls_cover must be False"
        else:
            assert (
                policy_cls.controls_cover is True
            ), f"{cover_type} must declare controls_cover=True"


@pytest.mark.unit
def test_stub_policy_passes_capability_warning_with_stub_registered() -> None:
    """Registered stub policy can answer ``cover_capability_warnings`` cleanly.

    A real consumer of the registry is config_flow's capability-warning
    builder; this test asserts it doesn't crash for an unknown-to-it
    cover type. The check itself stays inside the policy layer (no
    config_flow import) to keep this an isolated invariant.
    """
    with register_stub_policy(StubSingleAxisPolicy):
        policy = get_policy("cover_stub")
        assert policy.cover_capability_warnings(known={}) == []


# ---- Per-entity hold dispatch is gated on entity independence (#1174) ----- #
#
# A hold's floor/ceiling verdict is decided per cover only where each bound
# entity's position is genuinely its own. Three hooks say otherwise — a
# per-entity remap, a post-pipeline position rewrite, and a mandated dispatch
# order for physically coupled entities — and the base predicate derives its
# answer from all three so that a NEW cover type is excluded automatically
# rather than by someone remembering to say so.


@pytest.mark.unit
def test_entities_move_independently_is_true_for_an_untouched_policy(
    policy: CoverTypePolicy,
) -> None:
    """Leave the three dispatch hooks alone and the per-cover path stays on."""
    cls = type(policy)
    untouched = (
        cls.resolve_entity_target is CoverTypePolicy.resolve_entity_target
        and cls.post_pipeline_resolve is CoverTypePolicy.post_pipeline_resolve
        and cls.dispatch_order_key is CoverTypePolicy.dispatch_order_key
    )
    if untouched:
        assert policy.entities_move_independently() is True


@pytest.mark.unit
def test_a_policy_that_overrides_a_dispatch_hook_opts_out_or_says_why() -> None:
    """Overriding any of the three hooks turns per-cover judging off by default.

    A policy may override the predicate back to ``True``, but then it owes the
    argument in its own docstring — so this test demands one rather than
    letting an opt-in be silent. ``VenetianPolicy`` is the shipped example: its
    ``post_pipeline_resolve`` never rewrites the position under a hold.
    """
    from custom_components.adaptive_cover_pro.cover_types import POLICY_REGISTRY

    for cover_type, policy_cls in POLICY_REGISTRY.items():
        overrides = (
            policy_cls.resolve_entity_target
            is not CoverTypePolicy.resolve_entity_target
            or policy_cls.post_pipeline_resolve
            is not CoverTypePolicy.post_pipeline_resolve
            or policy_cls.dispatch_order_key is not CoverTypePolicy.dispatch_order_key
        )
        if not overrides:
            continue
        if policy_cls().entities_move_independently():
            own = policy_cls.entities_move_independently
            assert own is not CoverTypePolicy.entities_move_independently, (
                f"{cover_type} overrides a per-entity dispatch hook but still "
                "reports independent entities via the derived default"
            )
            assert own.__doc__, (
                f"{cover_type} opts back into per-cover hold dispatch — its "
                "override must document why that is safe"
            )


@pytest.mark.unit
def test_the_coupled_cover_types_are_judged_as_one_unit() -> None:
    """The shipped answers, pinned: day/night and dual panel are coupled.

    Both derive one entity's target from another's (or fold a second axis into
    the position wire), so a floor that moves one of their entities moves the
    whole geometry. Naming them explicitly keeps the derived predicate honest —
    a refactor that made either look "untouched" would flip this.
    """
    assert get_policy("cover_day_night_shade").entities_move_independently() is False
    assert get_policy("cover_dual_panel").entities_move_independently() is False
    assert get_policy("cover_blind").entities_move_independently() is True
