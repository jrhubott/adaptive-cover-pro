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


def test_set_position_service_exists_in_yaml():
    svc = _load()
    assert (
        "set_position" in svc
    ), "set_position service is registered in Python but has no entry in services.yaml"


def test_set_position_has_target_block():
    svc = _load()["set_position"]
    assert "target" in svc
    assert svc["target"]["entity"]["integration"] == "adaptive_cover_pro"


def test_set_position_has_position_field_with_correct_range():
    fields = _load()["set_position"]["fields"]
    assert "position" in fields
    sel = fields["position"]["selector"]["number"]
    assert sel["min"] == 0
    assert sel["max"] == 100
    assert sel["step"] == 1
    assert sel["mode"] == "slider"
    assert sel["unit_of_measurement"] == "%"


# Names wired with hass.services.async_register in services/__init__.py.
# Add here when registering a new service; remove when deregistering.
REGISTERED_SERVICES = {
    "export_config",
    "get_diagnostics",
    "integration_enable",
    "integration_disable",
    "emergency_stop",
    "set_position",
    # Options services (registered via register_options_services / OPTIONS_SERVICE_NAMES)
    "set_position_limits",
    "set_sunset_sunrise",
    "set_automation_timing",
    "set_manual_override",
    "set_force_override",
    "set_custom_position",
    "set_motion",
    "set_light_cloud",
    "set_climate",
    "set_weather_safety",
    "set_sun_tracking",
    "set_blind_spot",
    "set_interpolation",
    "set_geometry",
    "set_venetian",
    "set_option",
}


def test_all_registered_services_have_yaml_entry():
    documented = set(_load().keys())
    missing = REGISTERED_SERVICES - documented
    assert (
        not missing
    ), f"Service(s) registered in Python but missing from services.yaml: {sorted(missing)}"
