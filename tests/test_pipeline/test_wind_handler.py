"""Tests for WindOverrideHandler stub."""

from __future__ import annotations

from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.wind import (
    WindOverrideHandler,
)

from tests.test_pipeline.conftest import make_ctx


class TestWindOverrideHandler:
    """Tests for WindOverrideHandler stub."""

    handler = WindOverrideHandler()

    def test_returns_none_when_inactive_default(self) -> None:
        """Return None when wind_active is False (default)."""
        ctx = make_ctx()
        assert self.handler.evaluate(ctx) is None

    def test_returns_none_when_explicitly_inactive(self) -> None:
        """Return None when wind_active is explicitly False."""
        ctx = make_ctx(wind_active=False)
        assert self.handler.evaluate(ctx) is None

    def test_returns_result_when_active(self) -> None:
        """Return a PipelineResult when wind_active is True."""
        ctx = make_ctx(wind_active=True)
        result = self.handler.evaluate(ctx)
        assert result is not None

    def test_uses_wind_retract_position(self) -> None:
        """Use wind_retract_position from context."""
        ctx = make_ctx(wind_active=True, wind_retract_position=25)
        result = self.handler.evaluate(ctx)
        assert result is not None
        assert result.position == 25

    def test_default_retract_position_is_100(self) -> None:
        """Default wind_retract_position is 100 (fully retracted)."""
        ctx = make_ctx(wind_active=True)
        result = self.handler.evaluate(ctx)
        assert result is not None
        assert result.position == 100

    def test_control_method_is_wind(self) -> None:
        """Result must use ControlMethod.WIND."""
        ctx = make_ctx(wind_active=True)
        result = self.handler.evaluate(ctx)
        assert result is not None
        assert result.control_method == ControlMethod.WIND

    def test_reason_is_non_empty(self) -> None:
        """Result must have a non-empty reason string."""
        ctx = make_ctx(wind_active=True)
        result = self.handler.evaluate(ctx)
        assert result is not None
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0

    def test_reason_mentions_wind(self) -> None:
        """Reason string should mention wind."""
        ctx = make_ctx(wind_active=True)
        result = self.handler.evaluate(ctx)
        assert result is not None
        assert "wind" in result.reason.lower()

    def test_priority_is_90(self) -> None:
        """Priority must be 90."""
        assert WindOverrideHandler.priority == 90

    def test_name_is_wind(self) -> None:
        """Handler name must be 'wind'."""
        assert WindOverrideHandler.name == "wind"

    def test_describe_skip_mentions_wind(self) -> None:
        """describe_skip returns a non-empty string mentioning wind."""
        ctx = make_ctx(wind_active=False)
        reason = self.handler.describe_skip(ctx)
        assert isinstance(reason, str)
        assert len(reason) > 0
        assert "wind" in reason.lower()
