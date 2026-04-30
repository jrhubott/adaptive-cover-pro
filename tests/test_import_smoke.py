"""Smoke tests: verify integration imports cleanly and forbidden import patterns are absent."""

from pathlib import Path

import pytest


@pytest.mark.unit
def test_state_attr_importable_from_local_helpers():
    """state_attr must be importable from the local helpers module, not HA template."""
    from custom_components.adaptive_cover_pro.helpers import state_attr

    assert callable(state_attr)


@pytest.mark.unit
def test_no_homeassistant_template_state_attr_imports():
    """No file in the integration may import state_attr from homeassistant.helpers.template.

    That helper was removed in HA 2026.5; it must come from the local helpers module.
    """
    root = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "adaptive_cover_pro"
    )
    forbidden = "from homeassistant.helpers.template import state_attr"
    offenders = [
        str(p.relative_to(root.parent.parent))
        for p in root.rglob("*.py")
        if forbidden in p.read_text()
    ]
    assert offenders == [], f"Forbidden import found in: {offenders}"
