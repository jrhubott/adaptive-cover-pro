"""Shared synthetic cover-type policies used as parametrize fodder.

These are hypothetical "fifth cover types" — they satisfy the
``CoverTypePolicy`` ABC with only the abstract method overridden, and serve
as canaries for the invariant: code outside ``cover_types/`` must tolerate
a registered policy it has never heard of.

If a future change to coordinator / managers / pipeline / config_flow /
sensor / switch / binary_sensor introduces an assumption that crashes for
an unknown cover type, the tests parametrising over ``ALL_POLICIES_WITH_STUBS``
catch it before the real fifth cover type lands.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import ClassVar
from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.cover_types import POLICY_REGISTRY
from custom_components.adaptive_cover_pro.cover_types.base import (
    POSITION_AXIS,
    TILT_AXIS,
    CoverAxis,
    CoverTypePolicy,
)


class StubSingleAxisPolicy(CoverTypePolicy):
    """The smallest legal single-axis policy — one axis, no extra hooks.

    Models a fifth cover type added later that overrides only the abstract
    ``build_calc_engine`` and leaves every config-flow / pipeline hook on
    its base-class default. Verifies the base defaults don't crash when a
    partial implementation lands.
    """

    cover_type: ClassVar[str] = "cover_stub"
    axes: ClassVar[tuple[CoverAxis, ...]] = (POSITION_AXIS,)

    def build_calc_engine(self, **kwargs):  # type: ignore[override]  # noqa: ARG002
        return MagicMock()


class StubDualAxisPolicy(CoverTypePolicy):
    """The smallest legal dual-axis policy — position + tilt, no extra hooks.

    Models a fifth cover type that happens to be dual-axis but doesn't
    inherit any of ``VenetianPolicy``'s machinery. Verifies dual-axis-aware
    code paths don't assume ``isinstance(policy, VenetianPolicy)`` — they
    must rely on ``len(policy.axes) == 2`` or capability flags instead.
    """

    cover_type: ClassVar[str] = "cover_stub_dual"
    axes: ClassVar[tuple[CoverAxis, ...]] = (POSITION_AXIS, TILT_AXIS)

    def build_calc_engine(self, **kwargs):  # type: ignore[override]  # noqa: ARG002
        return MagicMock()


@contextmanager
def register_stub_policy(policy_cls: type[CoverTypePolicy]):
    """Temporarily insert a stub policy into ``POLICY_REGISTRY`` for one test.

    Used by tests that exercise registry-based dispatch (``get_policy()`` and
    its consumers). Method-level invariants don't need this — they
    instantiate the stub class directly.

    Usage::

        with register_stub_policy(StubSingleAxisPolicy):
            assert get_policy("cover_stub") is not None
    """
    key = policy_cls.cover_type
    assert (
        key not in POLICY_REGISTRY
    ), f"{key} already registered — pick a unique stub cover_type"
    POLICY_REGISTRY[key] = policy_cls
    try:
        yield policy_cls
    finally:
        POLICY_REGISTRY.pop(key, None)


# The four real policies plus both stubs. Most invariant tests parametrise
# over this list so adding a fifth real cover type automatically extends
# coverage without further edits.
ALL_POLICIES_WITH_STUBS: tuple[type[CoverTypePolicy], ...] = (
    *POLICY_REGISTRY.values(),
    StubSingleAxisPolicy,
    StubDualAxisPolicy,
)
