"""Docstring hygiene for services.yaml (Issue #211 Option 2)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

SERVICES_YAML = (
    Path(__file__).parent.parent
    / "custom_components"
    / "adaptive_cover_pro"
    / "services.yaml"
)


def _load():
    with SERVICES_YAML.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_set_blind_spot_left_description_uses_fov_frame():
    svc = _load()["set_blind_spot"]["fields"]["blind_spot_left"]
    desc = svc["description"]
    assert "window azimuth" not in desc.lower()
    assert "fov left" in desc.lower()


def test_set_blind_spot_right_description_uses_fov_frame():
    svc = _load()["set_blind_spot"]["fields"]["blind_spot_right"]
    desc = svc["description"]
    assert "window azimuth" not in desc.lower()
    assert "fov left" in desc.lower()
    assert "greater than" in desc.lower()


def test_set_blind_spot_service_description_mentions_fov_frame():
    svc = _load()["set_blind_spot"]
    desc = svc["description"].lower()
    assert "fov" in desc or "field of view" in desc
