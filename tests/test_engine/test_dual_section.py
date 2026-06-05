"""Tests for the dual-section (split-panel) engine helper."""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.engine.covers.dual_section import (
    DualSectionResult,
    bottom_blocks_top_free,
)


class TestBottomBlocksTopFree:
    """Bottom section tracks the sun; top section stays open."""

    @pytest.mark.unit
    def test_bottom_carries_tracking_value_top_open(self):
        out = bottom_blocks_top_free(bottom=40, top_open=100)
        assert out == DualSectionResult(top=100, bottom=40)

    @pytest.mark.unit
    def test_top_open_value_is_caller_supplied(self):
        # Under inverse the caller passes the flipped "open" constant (0).
        out = bottom_blocks_top_free(bottom=40, top_open=0)
        assert out.top == 0
        assert out.bottom == 40

    @pytest.mark.unit
    @pytest.mark.parametrize("bottom", [0, 25, 60, 100])
    def test_bottom_passed_through_untouched(self, bottom):
        assert bottom_blocks_top_free(bottom=bottom, top_open=100).bottom == bottom
