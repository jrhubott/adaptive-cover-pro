"""Tests for WindOverrideHandler."""

from __future__ import annotations

from custom_components.adaptive_cover_pro.pipeline.handlers.wind import WindOverrideHandler
from tests.test_pipeline.conftest import make_snapshot


class TestWindOverrideHandler:
    """Tests for WindOverrideHandler (stub)."""

    handler = WindOverrideHandler()

    def test_always_returns_none_until_implemented(self) -> None:
        """Wind handler is a stub and must return None for any snapshot."""
        snap = make_snapshot()
        assert self.handler.evaluate(snap) is None

    def test_priority_is_90(self) -> None:
        assert WindOverrideHandler.priority == 90

    def test_name(self) -> None:
        assert WindOverrideHandler.name == "wind"

    def test_describe_skip_meaningful(self) -> None:
        snap = make_snapshot()
        reason = self.handler.describe_skip(snap)
        assert isinstance(reason, str)
        assert len(reason) > 0
