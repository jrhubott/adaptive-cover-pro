"""Entity spec translation_key consistency guard.

Three guards:

1. Sensor specs with ``translation_key`` must reference a key that exists in
   ``en.json["entity"]["sensor"]``. Fails when a new sensor spec is added with
   a translation_key that was not added to the translation file.

2. Non-glare switch translation keys in ``en.json["entity"]["switch"]`` must
   correspond to an actual switch spec ``key`` value. Fails when an entry is
   added to en.json without a matching spec (orphaned translation) or vice versa.

3. Every key in ``templates.ACP_TEMPLATE_ENTITY_KEYS`` must name a
   ``(domain, translation_key)`` pair some platform really registers, and every
   registered translation_key must be either exposed there or explicitly
   withheld (issue #1159). The ``acp`` namespace resolves by matching
   ``RegistryEntry.translation_key``, so a key renamed in ``switch.py`` /
   ``sensor.py`` / ``binary_sensor.py`` silently breaks every template that
   references it — this is the guard that turns that into a red test.

When you add a new sensor spec with a translation_key:
  - Add the entry to translations/en.json under entity.sensor
  - Run the acp-translate skill to propagate to de.json and fr.json
  - Update _EXPECTED_SENSOR_TRANSLATION_KEYS below
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.binary_sensor import (
    _BINARY_SENSOR_SPECS,
    AdaptiveCoverPositionMismatchSensor,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_ENABLE_GLARE_ZONES,
    CONF_SENSOR_TYPE,
    GLARE_ZONE_SLOT_NUMBERS,
    CoverType,
)
from custom_components.adaptive_cover_pro.group_entities import (
    GroupActiveSceneSensor,
    GroupAutomationSwitch,
    GroupClimateSensor,
    GroupClimateSwitch,
    GroupLockSwitch,
    GroupPositionSensor,
    GroupStateSensor,
    GroupWhoWonSensor,
)
from custom_components.adaptive_cover_pro.sensor import (
    _DIAGNOSTIC_SPECS,
    _STANDARD_SPECS,
)
from custom_components.adaptive_cover_pro.switch import _SWITCH_SPECS, _glare_zone_specs
from custom_components.adaptive_cover_pro.templates import ACP_TEMPLATE_ENTITY_KEYS

_EN_JSON: dict = json.loads(
    (
        Path(__file__).parent.parent
        / "custom_components"
        / "adaptive_cover_pro"
        / "translations"
        / "en.json"
    ).read_text()
)

# Canary: lock the expected set of sensor translation_keys. Update here when a
# new sensor spec with translation_key is added.
_EXPECTED_SENSOR_TRANSLATION_KEYS: frozenset[str] = frozenset(
    {
        "climate_status",
        "control_status",
        "decision_trace",
        "motion_status",
        "position_forecast",
        "solar_calculation",
        "solar_gain",
        "travel_calibration",
    }
)

# Switch keys that appear in en.json under entity.switch but are generated
# dynamically (not in _SWITCH_SPECS) and therefore excluded from the spec check.
_DYNAMIC_SWITCH_KEYS_PREFIX = "glare_zone_"

# Cover-group entities (issue #790) are class-driven, not spec-driven; their
# translation_keys come straight from the classes so a rename stays in sync.
# HA's CachedProperties metaclass rewrites class-level ``_attr_translation_key``
# into a property whose default lands under ``__attr_translation_key``.


def _class_translation_key(cls: type) -> str:
    value = cls.__dict__.get(
        "__attr_translation_key", cls.__dict__.get("_attr_translation_key")
    )
    assert isinstance(value, str), f"{cls.__name__} has no class translation_key"
    return value


_GROUP_SENSOR_TRANSLATION_KEYS: frozenset[str] = frozenset(
    _class_translation_key(cls)
    for cls in (
        GroupPositionSensor,
        GroupStateSensor,
        GroupActiveSceneSensor,
        GroupClimateSensor,
        GroupWhoWonSensor,
    )
)
_GROUP_SWITCH_TRANSLATION_KEYS: frozenset[str] = frozenset(
    _class_translation_key(cls)
    for cls in (GroupAutomationSwitch, GroupLockSwitch, GroupClimateSwitch)
)


class TestSensorSpecTranslationKeys:
    """Sensor spec translation_keys must stay in sync with en.json."""

    def test_sensor_translation_keys_exist_in_en_json(self) -> None:
        """Every sensor spec translation_key must exist in en.json entity.sensor.

        Fails when a new spec is added with translation_key="foo" but "foo" is
        not yet added to en.json (and the three language files that mirror it).
        """
        all_specs = (*_STANDARD_SPECS, *_DIAGNOSTIC_SPECS)
        sensor_translations = _EN_JSON.get("entity", {}).get("sensor", {})

        missing = [
            f"{s.suffix!r} → translation_key={s.translation_key!r}"
            for s in all_specs
            if getattr(s, "translation_key", None)
            and s.translation_key not in sensor_translations
        ]
        assert not missing, (
            "Sensor specs reference translation_key values absent from "
            "en.json entity.sensor:\n"
            + "\n".join(f"  {m}" for m in missing)
            + "\nAdd the key to translations/en.json, then run `acp-translate` to sync."
        )

    def test_sensor_translation_keys_canary(self) -> None:
        """Lock the exact set of sensor translation_keys in use.

        Fails when a translation_key is added or removed from a sensor spec
        without updating _EXPECTED_SENSOR_TRANSLATION_KEYS in this file.
        """
        all_specs = (*_STANDARD_SPECS, *_DIAGNOSTIC_SPECS)
        actual = frozenset(
            s.translation_key for s in all_specs if getattr(s, "translation_key", None)
        )
        assert actual == _EXPECTED_SENSOR_TRANSLATION_KEYS, (
            f"Sensor translation_key set changed.\n"
            f"  Now in specs: {sorted(actual)}\n"
            f"  Expected:     {sorted(_EXPECTED_SENSOR_TRANSLATION_KEYS)}\n"
            "Update _EXPECTED_SENSOR_TRANSLATION_KEYS in this file."
        )

    def test_no_orphaned_sensor_translation_entries(self) -> None:
        """en.json entity.sensor must not contain keys unused by any sensor spec.

        Fails when a translation entry is left behind after removing a sensor
        spec's translation_key (or after renaming it).
        """
        all_specs = (*_STANDARD_SPECS, *_DIAGNOSTIC_SPECS)
        spec_keys = frozenset(
            s.translation_key for s in all_specs if getattr(s, "translation_key", None)
        )
        en_keys = frozenset(_EN_JSON.get("entity", {}).get("sensor", {}).keys())

        orphaned = en_keys - spec_keys - _GROUP_SENSOR_TRANSLATION_KEYS
        assert not orphaned, (
            f"en.json entity.sensor contains entries with no matching sensor spec "
            f"translation_key: {sorted(orphaned)}\n"
            "Remove the orphaned entry from en.json (and de.json, fr.json)."
        )


def _registered_entities() -> dict[str, dict[str, str | None]]:
    """Every entity the platforms register, as ``domain → {name: translation_key}``.

    Read off the same spec tuples and entity classes ``async_setup_entry`` uses,
    so a renamed key shows up here without anyone updating a literal list. The
    glare-zone switches are built dynamically, so they are produced by calling
    the real builder against an entry with every zone slot named.

    *name* is the spec's stable identifier — the sensor spec ``suffix``, the
    switch / binary-sensor ``key``. *translation_key* is what the ``acp``
    resolver matches on, and is ``None`` for a spec that sets none. Carrying
    both is what makes the exposed-or-withheld guard below non-vacuous:
    enumerating only the specs that *have* a translation_key let the nine
    sensors that don't (``sun_position``, ``Cover_Position``,
    ``last_cover_action`` …) escape the forced decision entirely, so a new
    sensor added without one would have escaped it too.
    """
    glare_entry = MagicMock()
    glare_entry.data = {CONF_SENSOR_TYPE: CoverType.BLIND}
    glare_entry.options = {
        CONF_ENABLE_GLARE_ZONES: True,
        **{f"glare_zone_{idx}_name": f"Zone {idx}" for idx in GLARE_ZONE_SLOT_NUMBERS},
    }
    mismatch_key = _class_translation_key(AdaptiveCoverPositionMismatchSensor)

    return {
        "binary_sensor": {
            **{s.key: s.key for s in _BINARY_SENSOR_SPECS},
            mismatch_key: mismatch_key,
        },
        "switch": {
            **{s.key: s.key for s in _SWITCH_SPECS},
            **{s.key: s.key for s in _glare_zone_specs(glare_entry)},
        },
        "sensor": {
            s.suffix: getattr(s, "translation_key", None)
            for s in (*_STANDARD_SPECS, *_DIAGNOSTIC_SPECS)
        },
    }


# Registered entities deliberately kept OUT of the ``acp`` namespace (issue
# #1159), listed by the spec name ``_registered_entities`` reports.
#
# Every one is continuously-varying, a timestamp, or an event echo: a tracked
# template reading one would re-render every cycle and drive its own refresh.
# The nine with no translation_key at all are additionally unresolvable by a
# translation_key-keyed resolver, so exposing them would not work even if we
# wanted to. Adding an entity without deciding either way is what the guard
# below is for.
_WITHHELD_FROM_NAMESPACE: dict[str, frozenset[str]] = {
    "binary_sensor": frozenset(),
    "switch": frozenset(),
    "sensor": frozenset(
        {
            # Have a translation_key, deliberately not exposed.
            "solar_calculation",
            "decision_trace",
            "position_forecast",
            # A ±30 %-band physics ESTIMATE (#1237). Exposing it in the acp
            # namespace would invite templates that branch on a number whose
            # error bars are widest exactly where a threshold would sit; the
            # entity is readable directly for anyone who wants it anyway.
            "solar_gain",
            # Setup-time measurement state. Nothing an automation would branch
            # on: it reports whether a calibration pass is running, which is a
            # thing a human does from the options flow once per install.
            "travel_calibration",
            # No translation_key — unresolvable by the namespace resolver.
            "Cover_Position",
            "Cover_Tilt",
            "Start Sun",
            "End Sun",
            "sun_position",
            "last_skipped_action",
            "last_cover_action",
            "manual_override_end_time",
            "position_verification",
        }
    ),
}


class TestAcpNamespaceKeys:
    """``ACP_TEMPLATE_ENTITY_KEYS`` must track what the platforms register."""

    def test_every_namespace_key_is_registered_by_its_platform(self) -> None:
        """Each exposed key must resolve to a real ``(domain, translation_key)``.

        The resolver matches ``RegistryEntry.translation_key``, so renaming a
        key in ``switch.py`` / ``sensor.py`` / ``binary_sensor.py`` without
        updating the namespace map turns every template that references it into
        a permanent render failure — with no other test noticing.
        """
        resolvable = {
            domain: {key for key in entities.values() if key}
            for domain, entities in _registered_entities().items()
        }
        unresolvable = [
            f"{key!r} → {domain}.{translation_key!r}"
            for key, (domain, translation_key) in ACP_TEMPLATE_ENTITY_KEYS.items()
            if translation_key not in resolvable.get(domain, set())
        ]
        assert not unresolvable, (
            "templates.ACP_TEMPLATE_ENTITY_KEYS names entities no platform "
            "registers:\n"
            + "\n".join(f"  {u}" for u in unresolvable)
            + "\nEither the platform's translation_key was renamed (update the "
            "namespace map and the wiki page) or the entity was removed."
        )

    def test_every_registered_entity_is_exposed_or_explicitly_withheld(self) -> None:
        """A new entity forces a decision instead of silently missing the namespace.

        Every spec counts, including one that sets no ``translation_key``. Such a
        spec can never be reached by the resolver, so it must be listed as
        withheld — which is the moment somebody notices that adding a
        translation_key is what it would take to expose it.
        """
        exposed: dict[str, set[str]] = {
            "binary_sensor": set(),
            "switch": set(),
            "sensor": set(),
        }
        for domain, translation_key in ACP_TEMPLATE_ENTITY_KEYS.values():
            exposed[domain].add(translation_key)

        undecided = {}
        for domain, entities in _registered_entities().items():
            withheld = _WITHHELD_FROM_NAMESPACE[domain]
            names = sorted(
                name
                for name, translation_key in entities.items()
                if translation_key not in exposed[domain] and name not in withheld
            )
            if names:
                undecided[domain] = names
        assert not undecided, (
            f"These registered entities are neither in the acp namespace nor "
            f"listed as deliberately withheld: {undecided}\n"
            "Add them to templates.ACP_TEMPLATE_ENTITY_KEYS (and the "
            "Template-Self-References wiki page), or to _WITHHELD_FROM_NAMESPACE "
            "here with the reason. A spec with no translation_key cannot be "
            "resolved by the namespace at all, so it can only be withheld."
        )


class TestSwitchTranslationKeys:
    """Non-dynamic switch translation entries must correspond to real switch specs."""

    def test_no_orphaned_static_switch_translation_entries(self) -> None:
        """Static switch entries in en.json must correspond to a _SWITCH_SPECS key.

        Glare zone entries (glare_zone_N) are dynamically generated and exempt.
        Fails when a static switch entry is added to en.json without a matching
        _SwitchSpec, or when a spec key is renamed without updating en.json.
        """
        spec_keys = frozenset(s.key for s in _SWITCH_SPECS)
        en_switch_keys = frozenset(_EN_JSON.get("entity", {}).get("switch", {}).keys())

        static_en_keys = frozenset(
            k for k in en_switch_keys if not k.startswith(_DYNAMIC_SWITCH_KEYS_PREFIX)
        )

        orphaned = static_en_keys - spec_keys - _GROUP_SWITCH_TRANSLATION_KEYS
        assert not orphaned, (
            f"en.json entity.switch contains static entries with no matching "
            f"_SwitchSpec key: {sorted(orphaned)}\n"
            "Remove the orphaned entry or add a matching _SwitchSpec."
        )
