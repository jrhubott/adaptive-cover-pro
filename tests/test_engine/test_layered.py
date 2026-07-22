"""Pure-engine unit tests for the layered dual-panel calculation (#996).

``engine/covers/layered.py`` is a pure, HA-free module: it *decides* each of a
dual-panel cover's two independent panel targets (a sheer front that tracks the
sun, a blackout back that deploys on a trigger set). The owning
``DualPanelPolicy`` maps the pipeline result onto the trigger set and the
open/closed values; these functions only compose primitives.
"""

from __future__ import annotations

import dataclasses

import pytest

from custom_components.adaptive_cover_pro.engine.covers.layered import (
    LayeredResult,
    blackout_should_deploy,
    compute_layered,
)


class TestBlackoutShouldDeploy:
    """``blackout_should_deploy`` — deploy iff any configured trigger is active."""

    def test_deploys_when_a_configured_trigger_is_active(self) -> None:
        assert blackout_should_deploy(["heat"], ["heat"]) is True

    def test_deploys_when_any_of_several_configured_triggers_matches(self) -> None:
        # Only "night" is active; it is one of the configured triggers → deploy.
        assert blackout_should_deploy(["night"], ["heat", "privacy", "night"]) is True

    def test_empty_configured_set_never_deploys(self) -> None:
        # No trigger is configured to drive the blackout → never deploys, even
        # with several active triggers.
        assert blackout_should_deploy(["heat", "privacy", "night"], []) is False

    def test_ignores_active_triggers_that_are_not_configured(self) -> None:
        # "heat" is active but only "privacy" is configured → no deploy.
        assert blackout_should_deploy(["heat"], ["privacy"]) is False

    def test_no_active_triggers_does_not_deploy(self) -> None:
        assert blackout_should_deploy([], ["heat", "privacy", "night"]) is False

    def test_accepts_arbitrary_iterables(self) -> None:
        # Sets and tuples work as well as lists.
        assert blackout_should_deploy({"privacy"}, ("privacy",)) is True


class TestComputeLayered:
    """``compute_layered`` — front passes through, back is open/closed."""

    def test_front_passes_through_verbatim(self) -> None:
        result = compute_layered(
            front=37, deploy_blackout=False, open_position=0, closed_position=100
        )
        assert result.front == 37

    def test_back_closed_when_blackout_deploys(self) -> None:
        result = compute_layered(
            front=37, deploy_blackout=True, open_position=0, closed_position=100
        )
        assert result.back == 100

    def test_back_open_when_blackout_retracts(self) -> None:
        result = compute_layered(
            front=37, deploy_blackout=False, open_position=0, closed_position=100
        )
        assert result.back == 0

    def test_back_uses_supplied_wire_space_values(self) -> None:
        # The caller maps open/closed into the dispatched coordinate space, so
        # inverted wire values flow straight through (no direction semantics).
        deployed = compute_layered(
            front=37, deploy_blackout=True, open_position=100, closed_position=0
        )
        assert deployed.back == 0
        retracted = compute_layered(
            front=37, deploy_blackout=False, open_position=100, closed_position=0
        )
        assert retracted.back == 100


class TestLayeredResultShape:
    """``LayeredResult`` is a frozen, slotted dataclass."""

    def test_is_frozen(self) -> None:
        result = LayeredResult(front=10, back=20)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.front = 99  # type: ignore[misc]

    def test_uses_slots(self) -> None:
        result = LayeredResult(front=10, back=20)
        with pytest.raises(AttributeError):
            result.__dict__  # noqa: B018
