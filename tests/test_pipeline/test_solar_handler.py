"""Tests for SolarHandler."""

from __future__ import annotations

from unittest.mock import MagicMock


from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.solar import SolarHandler
from tests.test_pipeline.conftest import make_snapshot


class TestSolarHandler:
    """Test SolarHandler."""

    handler = SolarHandler()

    def test_returns_none_when_sun_not_valid(self) -> None:
        """Return None when direct_sun_valid is False."""
        snap = make_snapshot(direct_sun_valid=False)
        assert self.handler.evaluate(snap) is None

    def test_matches_when_sun_valid(self) -> None:
        """Return SOLAR result when direct sun is valid."""
        snap = make_snapshot(direct_sun_valid=True, calculate_percentage_return=60.0)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.control_method == ControlMethod.SOLAR

    def test_uses_calculate_percentage(self) -> None:
        """Position comes from cover.calculate_percentage()."""
        snap = make_snapshot(direct_sun_valid=True, calculate_percentage_return=72.0)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position == 72

    def test_minimum_position_is_1_when_sun_valid(self) -> None:
        """Position never returns 0 when sun is in FOV."""
        snap = make_snapshot(direct_sun_valid=True, calculate_percentage_return=0.0)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position >= 1

    def test_applies_max_position_limit(self) -> None:
        """Max position limit is applied when configured."""
        from tests.test_pipeline.conftest import _make_mock_cover

        config = MagicMock()
        config.min_pos = None
        config.max_pos = 80
        config.min_pos_sun_only = False
        config.max_pos_sun_only = False
        cover = _make_mock_cover(
            direct_sun_valid=True,
            calculate_percentage_return=95.0,
            config=config,
        )
        snap = make_snapshot(cover=cover)
        result = self.handler.evaluate(snap)
        assert result is not None
        assert result.position <= 80

    def test_returns_none_outside_time_window(self) -> None:
        """Return None when in_time_window is False even if sun is valid."""
        snap = make_snapshot(
            direct_sun_valid=True,
            calculate_percentage_return=60.0,
            in_time_window=False,
        )
        assert self.handler.evaluate(snap) is None

    def test_matches_inside_time_window(self) -> None:
        """Return result when in_time_window is True and sun is valid."""
        snap = make_snapshot(
            direct_sun_valid=True, calculate_percentage_return=60.0, in_time_window=True
        )
        assert self.handler.evaluate(snap) is not None

    def test_describe_skip_outside_time_window(self) -> None:
        """describe_skip returns 'outside time window' when in_time_window is False."""
        snap = make_snapshot(direct_sun_valid=True, in_time_window=False)
        assert self.handler.describe_skip(snap) == "outside time window"

    def test_describe_skip_mentions_sun(self) -> None:
        """describe_skip mentions sun or FOV when skipped inside the window."""
        snap = make_snapshot(direct_sun_valid=False, in_time_window=True)
        reason = self.handler.describe_skip(snap)
        assert any(word in reason.lower() for word in ("sun", "fov", "elevation"))

    def test_priority_is_40(self) -> None:
        """SolarHandler has priority 40."""
        assert SolarHandler.priority == 40

    def test_name(self) -> None:
        """SolarHandler name is 'solar'."""
        assert SolarHandler.name == "solar"
