"""Tests for CloudSuppressionHandler stub."""

from __future__ import annotations

from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.cloud_suppression import (
    CloudSuppressionHandler,
)

from tests.test_pipeline.conftest import make_ctx


class TestCloudSuppressionHandler:
    """Tests for CloudSuppressionHandler stub."""

    handler = CloudSuppressionHandler()

    def test_returns_none_when_inactive_default(self) -> None:
        """Return None when cloud_suppression_active is False (default)."""
        ctx = make_ctx()
        assert self.handler.evaluate(ctx) is None

    def test_returns_none_when_explicitly_inactive(self) -> None:
        """Return None when cloud_suppression_active is explicitly False."""
        ctx = make_ctx(cloud_suppression_active=False)
        assert self.handler.evaluate(ctx) is None

    def test_returns_result_when_active(self) -> None:
        """Return a PipelineResult when cloud_suppression_active is True."""
        ctx = make_ctx(cloud_suppression_active=True, default_position=30)
        result = self.handler.evaluate(ctx)
        assert result is not None

    def test_uses_default_position(self) -> None:
        """Use default_position from context."""
        ctx = make_ctx(cloud_suppression_active=True, default_position=40)
        result = self.handler.evaluate(ctx)
        assert result is not None
        assert result.position == 40

    def test_default_position_zero(self) -> None:
        """Handle default_position=0 correctly (falsy value)."""
        ctx = make_ctx(cloud_suppression_active=True, default_position=0)
        result = self.handler.evaluate(ctx)
        assert result is not None
        assert result.position == 0

    def test_control_method_is_cloud(self) -> None:
        """Result must use ControlMethod.CLOUD."""
        ctx = make_ctx(cloud_suppression_active=True, default_position=20)
        result = self.handler.evaluate(ctx)
        assert result is not None
        assert result.control_method == ControlMethod.CLOUD

    def test_reason_is_non_empty(self) -> None:
        """Result must have a non-empty reason string."""
        ctx = make_ctx(cloud_suppression_active=True, default_position=10)
        result = self.handler.evaluate(ctx)
        assert result is not None
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0

    def test_reason_mentions_cloud(self) -> None:
        """Reason string should mention cloud."""
        ctx = make_ctx(cloud_suppression_active=True, default_position=10)
        result = self.handler.evaluate(ctx)
        assert result is not None
        assert "cloud" in result.reason.lower()

    def test_priority_is_60(self) -> None:
        """Priority must be 60."""
        assert CloudSuppressionHandler.priority == 60

    def test_name_is_cloud_suppression(self) -> None:
        """Handler name must be 'cloud_suppression'."""
        assert CloudSuppressionHandler.name == "cloud_suppression"

    def test_describe_skip_mentions_cloud(self) -> None:
        """describe_skip returns a non-empty string mentioning cloud."""
        ctx = make_ctx(cloud_suppression_active=False)
        reason = self.handler.describe_skip(ctx)
        assert isinstance(reason, str)
        assert len(reason) > 0
        assert "cloud" in reason.lower()
