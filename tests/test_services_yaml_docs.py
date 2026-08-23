"""Docstring hygiene for services.yaml (Issue #211 Option 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from custom_components.adaptive_cover_pro.config_fields import _BINARY_ON_DOMAINS
from custom_components.adaptive_cover_pro.const import (
    CONF_VENETIAN_BACKROTATE_PUBLISH_LAG,
    OPTION_RANGES,
)

pytestmark = pytest.mark.unit

SERVICES_YAML = (
    Path(__file__).parent.parent
    / "custom_components"
    / "adaptive_cover_pro"
    / "services.yaml"
)

EN_JSON = (
    Path(__file__).parent.parent
    / "custom_components"
    / "adaptive_cover_pro"
    / "translations"
    / "en.json"
)


def _load():
    with SERVICES_YAML.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _load_en_services():
    """Return the ``services`` block of translations/en.json."""
    with EN_JSON.open(encoding="utf-8") as fh:
        return json.load(fh)["services"]


def _fields(block, name):
    """Return the set of field names documented for ``name`` in ``block``."""
    return set((block.get(name) or {}).get("fields") or {})


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


def test_set_blind_spot_en_json_legacy_fields_marked_deprecated():
    """The legacy edges in en.json must be flagged deprecated (frame switch #247)."""
    fields = _load_en_services()["set_blind_spot"]["fields"]
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


def test_set_light_cloud_is_sunny_sensor_offers_every_binary_on_domain():
    """Issue #1251: the option is wired (_SECTION_LIGHT_CLOUD, FIELD_VALIDATORS)
    and already documented in en.json; only the yaml field the Actions UI renders
    from was missing. The picker must offer the same domains as the config-flow
    selector (binary_on_selector) — a narrower list would make an input_boolean
    set through the UI unsettable through the service.
    """
    fields = _load()["set_light_cloud"]["fields"]
    assert "is_sunny_sensor" in fields
    assert set(fields["is_sunny_sensor"]["selector"]["entity"]["domain"]) == set(
        _BINARY_ON_DOMAINS
    )


def test_set_venetian_backrotate_publish_lag_selector_matches_option_range():
    """Issue #1251: wired via _SECTION_VENETIAN + FIELD_VALIDATORS(_range) and
    already in en.json; only the yaml field was missing. The slider bounds are
    read from OPTION_RANGES — the same tuple options_service._range() validates
    against — so the UI can never offer a value the handler rejects.
    """
    fields = _load()["set_venetian"]["fields"]
    assert "venetian_backrotate_publish_lag" in fields
    sel = fields["venetian_backrotate_publish_lag"]["selector"]["number"]
    lo, hi = OPTION_RANGES[CONF_VENETIAN_BACKROTATE_PUBLISH_LAG]
    assert sel["min"] == lo
    assert sel["max"] == hi
    assert sel["unit_of_measurement"] == "s"


def test_set_geometry_does_not_expose_venetian_tilt_skip_above():
    """Issue #1251: CONF_VENETIAN_TILT_SKIP_ABOVE is in _SECTION_VENETIAN, not
    _SECTION_GEOMETRY_ALL (services/options_service.py:1023-1043), so _build_patch
    would silently drop it from a set_geometry call. Exposing it there — in yaml
    OR in a translation — creates a UI-visible field that does nothing.
    set_venetian owns this option and documents it in both files.
    """
    services = _load()
    en_services = _load_en_services()
    assert "venetian_tilt_skip_above" not in services["set_geometry"]["fields"]
    assert "venetian_tilt_skip_above" not in en_services["set_geometry"]["fields"]
    assert "venetian_tilt_skip_above" in services["set_venetian"]["fields"]


# Known services.yaml ↔ en.json field drift, one entry per (service, field).
# Issue #1251. THIS LIST ONLY EVER SHRINKS.
#
# Each pair is a field documented in exactly one of the two files. Every entry
# here is a real gap that still needs fixing; the pair is suppressed only so the
# parity lock below can guard the other 35 services today instead of after the
# cleanup lands.
#
# To remove an entry: make the two files agree on that field (add the missing
# services.yaml field, add the missing en.json name+description and sync DE/FR
# via the acp-translate skill, or delete the field if the service's handler
# section does not accept it), then delete the line. Deleting is not optional —
# test_known_field_drift_entries_are_still_drifting fails on a stale entry.
#
# NEVER add an entry. A new drift is a bug in the PR that introduced it, not a
# known-issue to park here. If you believe an addition is genuinely warranted,
# that is a maintainer decision — open an issue.
KNOWN_FIELD_DRIFT: frozenset[tuple[str, str]] = frozenset(
    {
        # set_custom_position — 4 fields in services.yaml with no en.json entry
        # (#943 axis constraints, #1318 window scope). Needs EN/DE/FR authoring.
        ("set_custom_position", "outside_window"),
        ("set_custom_position", "position_max"),
        ("set_custom_position", "tilt_max"),
        ("set_custom_position", "tilt_min"),
        # set_climate — 3 fields in services.yaml with no en.json entry.
        ("set_climate", "extreme_heat_position"),
        ("set_climate", "temp_extreme_heat"),
        ("set_climate", "tracking_seasons"),
    }
)


@pytest.mark.parametrize("service", sorted(_load()))
def test_services_yaml_en_json_field_parity(service: str) -> None:
    """Every services.yaml service must document exactly the en.json fields.

    Issue #1251: hand-written per-service parity guards existed for two services
    only (set_blind_spot, set_position_limits), so the other 35 could — and did —
    ship a yaml-only field (raw English in a translated Actions UI) or an
    en.json-only field (invisible in the Actions UI) with no test failing.
    """
    yaml_services = _load()
    en_services = _load_en_services()
    assert service in en_services, (
        f"services.yaml documents '{service}' but translations/en.json has no "
        f"services.{service} entry — the whole action renders untranslated."
    )
    suppressed = {f for (s, f) in KNOWN_FIELD_DRIFT if s == service}
    yaml_fields = _fields(yaml_services, service)
    en_fields = _fields(en_services, service)
    only_yaml = yaml_fields - en_fields - suppressed
    only_en = en_fields - yaml_fields - suppressed
    assert not only_yaml and not only_en, (
        f"services.yaml and translations/en.json disagree on '{service}' fields.\n"
        "  only in services.yaml (no translation — the Actions UI shows raw "
        f"English even in DE/FR): {sorted(only_yaml)}\n"
        "  only in en.json (no yaml field — the option is invisible in the "
        f"Actions UI): {sorted(only_en)}\n"
        "Fix: add the missing entry to the other file — or, if the field is not "
        "an option this service's handler section accepts, delete it (a field "
        "outside the handler's allowed_keys is silently dropped by _build_patch).\n"
        "Do NOT add it to KNOWN_FIELD_DRIFT: that list only shrinks (issue #1251)."
    )


def test_known_field_drift_entries_are_still_drifting() -> None:
    """Every KNOWN_FIELD_DRIFT pair must name a field that is genuinely still
    drifting. Issue #1251: the list only shrinks, and nothing shrinks it unless
    forgetting to prune is a hard failure. A suppression left standing after the
    drift is fixed silently swallows the next real regression on that field.
    """
    yaml_services = _load()
    en_services = _load_en_services()
    stale = []
    for service, field in sorted(KNOWN_FIELD_DRIFT):
        if service not in yaml_services:
            stale.append(f"  {service}.{field} (service no longer in services.yaml)")
            continue
        drifting = _fields(yaml_services, service) ^ _fields(en_services, service)
        if field not in drifting:
            stale.append(f"  {service}.{field} (services.yaml and en.json now agree)")
    assert not stale, (
        "KNOWN_FIELD_DRIFT (tests/test_services_yaml_docs.py) lists (service, field) "
        "pairs that are no longer drifting — services.yaml and translations/en.json "
        "now agree on them, so the suppression is dead weight hiding the next real "
        "regression.\n"
        "Delete these entries from KNOWN_FIELD_DRIFT:\n"
        + "\n".join(stale)
        + "\nThis list only ever shrinks (issue #1251)."
    )
