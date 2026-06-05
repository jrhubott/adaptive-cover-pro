"""Tests for the layered (dual-panel) engine helpers."""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.engine.covers.layered import (
    LayeredResult,
    blackout_should_deploy,
    compute_layered,
)


class TestBlackoutShouldDeploy:
    """The blackout deploys only when a configured trigger is active."""

    @pytest.mark.unit
    def test_deploys_when_a_configured_trigger_is_active(self):
        assert blackout_should_deploy({"sunset"}, ("climate", "sunset")) is True

    @pytest.mark.unit
    def test_does_not_deploy_when_active_trigger_is_unconfigured(self):
        # sunset is active but the user only configured "climate"
        assert blackout_should_deploy({"sunset"}, ("climate",)) is False

    @pytest.mark.unit
    def test_does_not_deploy_when_nothing_active(self):
        assert blackout_should_deploy(set(), ("climate", "sunset")) is False

    @pytest.mark.unit
    def test_empty_configured_triggers_never_deploys(self):
        assert blackout_should_deploy({"climate", "sunset"}, ()) is False


class TestComputeLayered:
    """Composing front + back panel targets."""

    @pytest.mark.unit
    def test_back_open_when_not_deployed(self):
        out = compute_layered(
            front=42, deploy_blackout=False, open_position=100, closed_position=0
        )
        assert out == LayeredResult(front=42, back=100)

    @pytest.mark.unit
    def test_back_closed_when_deployed(self):
        out = compute_layered(
            front=42, deploy_blackout=True, open_position=100, closed_position=0
        )
        assert out == LayeredResult(front=42, back=0)

    @pytest.mark.unit
    def test_front_is_passed_through_untouched(self):
        out = compute_layered(
            front=73, deploy_blackout=True, open_position=100, closed_position=0
        )
        assert out.front == 73

    @pytest.mark.unit
    def test_inverse_space_open_closed_are_caller_supplied(self):
        # Caller maps open/closed into dispatched space; under inverse the
        # open value is 0 and closed is 100.
        out = compute_layered(
            front=10, deploy_blackout=False, open_position=0, closed_position=100
        )
        assert out.back == 0
        out2 = compute_layered(
            front=10, deploy_blackout=True, open_position=0, closed_position=100
        )
        assert out2.back == 100
