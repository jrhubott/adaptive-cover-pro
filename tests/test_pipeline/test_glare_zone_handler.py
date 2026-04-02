"""Tests for GlareZoneHandler."""

from __future__ import annotations

from custom_components.adaptive_cover_pro.enums import ControlMethod


def test_glare_zone_control_method_exists() -> None:
    """GLARE_ZONE must be a valid ControlMethod value."""
    assert ControlMethod.GLARE_ZONE == "glare_zone"
