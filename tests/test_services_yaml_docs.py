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


def test_set_blind_spot_gamma_fields_exist_with_signed_range():
    """The primary signed-gamma fields are documented with a -180..180 range."""
    fields = _load()["set_blind_spot"]["fields"]
    for key in ("blind_spot_left_gamma", "blind_spot_right_gamma"):
        assert key in fields
        number = fields[key]["selector"]["number"]
        assert number["min"] == -180
        assert number["max"] == 180


def test_set_blind_spot_gamma_description_uses_window_normal_frame():
    left = _load()["set_blind_spot"]["fields"]["blind_spot_left_gamma"]["description"]
    assert "window normal" in left.lower()


def test_set_blind_spot_legacy_fields_are_deprecated():
    """Legacy fields remain accepted for back-compat but are flagged deprecated."""
    fields = _load()["set_blind_spot"]["fields"]
    for key in ("blind_spot_left", "blind_spot_right"):
        assert key in fields
        assert "deprecated" in fields[key]["description"].lower()


def test_set_blind_spot_service_description_mentions_window_normal():
    desc = _load()["set_blind_spot"]["description"].lower()
    assert "window normal" in desc


def test_get_diagnostics_and_get_troubleshooting_config_entry_id_descriptions_match():
    """Both services expose the identical config_entry_id escape hatch — the
    field description must read identically in the action picker rather than
    presenting two texts for the same thing (issue #1059 audit round 3, nit #5).
    """
    services = _load()
    diag_desc = services["get_diagnostics"]["fields"]["config_entry_id"]["description"]
    troubleshoot_desc = services["get_troubleshooting"]["fields"]["config_entry_id"][
        "description"
    ]
    assert diag_desc == troubleshoot_desc


def test_set_blind_spot_en_json_fields_match_services_yaml():
    """en.json set_blind_spot.fields must document exactly the yaml fields.

    Without this lock a new yaml field (the signed-gamma edges, #247) can ship
    with a stale en.json that still describes the old FOV-relative frame and
    lacks the gamma fields — HA then renders no label/description for them. Keys
    must match 1:1 in both directions.
    """
    import json

    yaml_fields = set(_load()["set_blind_spot"]["fields"])
    en_path = (
        Path(__file__).parent.parent
        / "custom_components"
        / "adaptive_cover_pro"
        / "translations"
        / "en.json"
    )
    with en_path.open(encoding="utf-8") as fh:
        en = json.load(fh)
    en_fields = set(en["services"]["set_blind_spot"]["fields"])
    assert en_fields == yaml_fields, (
        "services.yaml and en.json set_blind_spot fields disagree: "
        f"only in yaml={sorted(yaml_fields - en_fields)}, "
        f"only in en.json={sorted(en_fields - yaml_fields)}"
    )


def test_set_blind_spot_en_json_legacy_fields_marked_deprecated():
    """The legacy edges in en.json must be flagged deprecated (frame switch #247)."""
    import json

    en_path = (
        Path(__file__).parent.parent
        / "custom_components"
        / "adaptive_cover_pro"
        / "translations"
        / "en.json"
    )
    with en_path.open(encoding="utf-8") as fh:
        en = json.load(fh)
    fields = en["services"]["set_blind_spot"]["fields"]
    for key in ("blind_spot_left", "blind_spot_right"):
        assert "deprecated" in fields[key]["description"].lower()


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
    "get_troubleshooting",
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
    "set_occupancy",
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


def test_set_occupancy_fields_are_occupancy_prefixed():
    """Issue #723: set_occupancy exposes occupancy_* wire fields (aliased to the
    frozen CONF_MOTION_* option keys). The yaml must NOT expose motion_* keys —
    set_motion keeps those; set_occupancy is the renamed API.
    """
    fields = _load()["set_occupancy"]["fields"]
    assert "occupancy_sensors" in fields
    assert "occupancy_timeout" in fields
    assert "occupancy_media_players" in fields
    assert not any(k.startswith("motion_") for k in fields)


def test_set_position_limits_field_is_default_percentage_not_default_height():
    """Issue #792: the service field name must match the CONF_DEFAULT_HEIGHT option
    key (``default_percentage``), or _build_patch silently drops it. The old
    ``default_height`` name is kept working via a deprecated alias, not the yaml.
    """
    fields = _load()["set_position_limits"]["fields"]
    assert "default_percentage" in fields
    assert "default_height" not in fields


def test_set_position_limits_min_position_sun_tracking_field_exists_with_correct_range():
    """Issue #1242: min_position_sun_tracking must be a set_position_limits field —
    the option key is already wired into FIELD_VALIDATORS and
    _SECTION_POSITION_LIMITS (options_service.py), and translations/en.json already
    carries a services.set_position_limits.fields.min_position_sun_tracking entry;
    only services.yaml was missing the field the UI actually renders from.
    """
    fields = _load()["set_position_limits"]["fields"]
    assert "min_position_sun_tracking" in fields
    sel = fields["min_position_sun_tracking"]["selector"]["number"]
    assert sel["min"] == 0
    assert sel["max"] == 99
    assert sel["step"] == 1
    assert sel["mode"] == "slider"
    assert sel["unit_of_measurement"] == "%"


def test_set_position_limits_en_json_fields_match_services_yaml():
    """services.yaml and en.json set_position_limits fields must match 1:1 — mirrors
    test_set_blind_spot_en_json_fields_match_services_yaml (issue #247's guard).
    Without this lock, a yaml field can ship with no translation (silently falls back
    to raw English in the Actions UI, confirmed in issue #1242's screenshot for
    enable_position_matching) or a translation entry can exist with no yaml field
    behind it (silently invisible in the Actions UI — issue #1242's root cause for
    min_position_sun_tracking).
    """
    import json

    yaml_fields = set(_load()["set_position_limits"]["fields"])
    en_path = (
        Path(__file__).parent.parent
        / "custom_components"
        / "adaptive_cover_pro"
        / "translations"
        / "en.json"
    )
    with en_path.open(encoding="utf-8") as fh:
        en = json.load(fh)
    en_fields = set(en["services"]["set_position_limits"]["fields"])
    assert en_fields == yaml_fields, (
        "services.yaml and en.json set_position_limits fields disagree: "
        f"only in yaml={sorted(yaml_fields - en_fields)}, "
        f"only in en.json={sorted(en_fields - yaml_fields)}"
    )
