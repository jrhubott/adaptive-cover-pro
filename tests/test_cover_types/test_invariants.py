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

from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.cover_types.base import (
    CAP_HAS_SET_POSITION,
    CAP_HAS_SET_TILT_POSITION,
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
    touching every policy. ``cover_building_profile`` is the one shipped
    virtual entry type that registers no platforms and has no axes, so it is
    the sole policy allowed to report ``False``. Pinning both directions
    keeps the cover-contract suites and cover-only menus exercising every
    real cover type and guards against a real cover accidentally opting out.
    """
    from custom_components.adaptive_cover_pro.cover_types.base import CoverTypePolicy

    assert CoverTypePolicy.controls_cover is True

    from custom_components.adaptive_cover_pro.cover_types import POLICY_REGISTRY

    expected_non_cover = {"cover_building_profile"}
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
