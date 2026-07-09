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


def test_no_service_target_has_a_device_filter():
    """No service `target:` may carry a `device:` filter — hassfest rejects it.

    HA's service schema does not support a `device:` selector under `target:`
    ("Services do not support device filters on target"), so hassfest CI fails
    the whole integration if one is present. Device targeting still works from
    automations via entity resolution, so the `entity: integration:` picker is
    sufficient. This guard keeps the filter out permanently: it was removed once
    already (the April `services.yaml` fix), silently re-added across every
    service by #824, and had to be stripped again — this test is what stops the
    next round-trip.
    """
    offenders = [
        name
        for name, svc in _load().items()
        if isinstance((svc or {}).get("target"), dict) and "device" in svc["target"]
    ]
    assert not offenders, (
        "Service `target:` blocks must not contain a `device:` filter — hassfest "
        f"rejects it (use entity targeting instead): {sorted(offenders)}"
    )


def test_set_axes_service_exists_in_yaml():
    svc = _load()
    assert (
        "set_axes" in svc
    ), "set_axes service is registered in Python but has no entry in services.yaml"


def test_set_axes_has_target_block_and_axes_field():
    svc = _load()["set_axes"]
    assert "target" in svc
    assert svc["target"]["entity"]["integration"] == "adaptive_cover_pro"
    assert "axes" in svc["fields"]


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
    "set_tilt",
    "set_axes",
    "engage_manual_override",
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


def test_set_position_limits_field_is_default_percentage_not_default_height():
    """Issue #792: the service field name must match the CONF_DEFAULT_HEIGHT option
    key (``default_percentage``), or _build_patch silently drops it. The old
    ``default_height`` name is kept working via a deprecated alias, not the yaml.
    """
    fields = _load()["set_position_limits"]["fields"]
    assert "default_percentage" in fields
    assert "default_height" not in fields
