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

import ast
import inspect
import json
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers.entity import Entity

from custom_components.adaptive_cover_pro.binary_sensor import (
    _BINARY_SENSOR_SPECS,
    AdaptiveCoverBinarySensor,
    AdaptiveCoverPositionMismatchSensor,
)
from custom_components.adaptive_cover_pro.button import (
    AdaptiveCoverApplyCalculatedPositionButton,
    AdaptiveCoverButton,
    AdaptiveCoverMyPositionButton,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_ENABLE_GLARE_ZONES,
    CONF_SENSOR_TYPE,
    GLARE_ZONE_SLOT_NUMBERS,
    ControlStatus,
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
    _DIAGNOSTIC_CLASSES,
    _DIAGNOSTIC_SPECS,
    _STANDARD_CLASSES,
    _STANDARD_SPECS,
)
from custom_components.adaptive_cover_pro.switch import _SWITCH_SPECS, _glare_zone_specs
from custom_components.adaptive_cover_pro.templates import ACP_TEMPLATE_ENTITY_KEYS
from tests._helpers.skip_codes import (
    EXPECTED_SKIP_CODES,
    emitted_record_skipped_action_reasons,
    idle_last_skipped_value,
)

_TRANSLATIONS_DIR = (
    Path(__file__).parent.parent
    / "custom_components"
    / "adaptive_cover_pro"
    / "translations"
)


def _load_translation_bundle(language: str) -> dict:
    """Load a shipped ``translations/<language>.json`` bundle."""
    return json.loads(
        (_TRANSLATIONS_DIR / f"{language}.json").read_text(encoding="utf-8")
    )


def _load_all_translation_bundles() -> dict[str, dict]:
    """Load ``translations/{en,de,fr}.json`` keyed by language code."""
    return {
        language: _load_translation_bundle(language) for language in ("en", "de", "fr")
    }


_EN_JSON: dict = _load_translation_bundle("en")

# Canary: lock the expected set of sensor translation_keys. Update here when a
# new sensor spec with translation_key is added.
_EXPECTED_SENSOR_TRANSLATION_KEYS: frozenset[str] = frozenset(
    {
        "climate_status",
        "control_status",
        "decision_trace",
        "end_sun",
        "last_cover_action",
        "last_skipped_action",
        "manual_override_end_time",
        "motion_status",
        "position_verification",
        "position_forecast",
        "solar_calculation",
        "solar_gain",
        "start_sun",
        "sun_position",
        "target_position",
        "target_tilt",
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


# ---------------------------------------------------------------------------
# English name snapshot (issue #1353 audit findings #1/#2)
# ---------------------------------------------------------------------------
#
# Frozen legacy English names for every entity whose hardcoded ``name``
# override was removed in favor of a translation_key (issue #1353). Each
# value is verified byte-for-byte against the pre-refactor hardcoded string
# at develop `2e58424b` — see
# ``git show 2e58424b:custom_components/adaptive_cover_pro/{sensor,
# binary_sensor,button}.py`` for the removed ``name``/``_sensor_name``/
# ``_binary_name``/``_button_name`` properties this replaces.
#
# DO NOT edit this dict to match a renamed en.json string, and do not derive
# it from the spec/class definitions — either would let the two sides drift
# in lockstep and defeat the lock. HA derives the English object_id from
# ``entity.<platform>.<translation_key>.name`` for any newly-registered
# entity with no ``name`` override (``has_entity_name=True``), so a silent
# rename here silently renames every new English install's entity_id. A
# deliberate rename needs its own migration story (CLAUDE.md §
# Rollback-Safe Config Migrations), not a snapshot update.
_ENGLISH_NAME_SNAPSHOT: dict[tuple[str, str], str] = {
    ("sensor", "target_position"): "Target Position",
    ("sensor", "target_tilt"): "Target Tilt",
    ("sensor", "start_sun"): "Start Sun",
    ("sensor", "end_sun"): "End Sun",
    ("sensor", "sun_position"): "Sun Position",
    ("sensor", "solar_calculation"): "Solar Calculation",
    ("sensor", "control_status"): "Control Status",
    ("sensor", "decision_trace"): "Decision Trace",
    ("sensor", "position_forecast"): "Position Forecast",
    ("sensor", "last_skipped_action"): "Last Skipped Action",
    ("sensor", "last_cover_action"): "Last Cover Action",
    ("sensor", "manual_override_end_time"): "Manual Override End Time",
    ("sensor", "position_verification"): "Position Verification",
    ("sensor", "motion_status"): "Occupancy Status",
    ("sensor", "travel_calibration"): "Travel Time Calibration",
    ("sensor", "solar_gain"): "Estimated Solar Gain",
    ("sensor", "climate_status"): "Climate Status",
    ("binary_sensor", "sun_motion"): "Sun Infront",
    ("binary_sensor", "manual_override"): "Manual Override",
    ("binary_sensor", "glare_active"): "Glare Active",
    ("binary_sensor", "position_mismatch"): "Position Mismatch",
    ("button", "reset_manual_override"): "Reset Manual Override",
}


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

    def test_english_entity_names_match_legacy_names(self) -> None:
        """English names must byte-match the removed hardcoded ``name`` values.

        HA derives both the friendly name and (for a newly-registered entity
        with ``has_entity_name=True`` and no override) the English object_id
        from ``entity.<platform>.<translation_key>.name`` in en.json. Since
        the localization refactor (issue #1353) replaced each hardcoded
        ``name`` property with a translation_key, a mismatch here would
        silently change every English installation's entity_ids.

        Compared against ``_ENGLISH_NAME_SNAPSHOT``, a frozen literal — NOT
        against the spec/class definitions, which could be renamed in
        lockstep with en.json and defeat a self-referential comparison (see
        the snapshot's module-level comment for why it must never be edited
        to match a rename).
        """
        en = _load_translation_bundle("en")
        bundles = {
            "sensor": en["entity"]["sensor"],
            "binary_sensor": en["entity"]["binary_sensor"],
            "button": en["entity"]["button"],
        }

        mismatches = [
            f"{platform}.{key}: expected {expected!r}, got "
            f"{bundles[platform].get(key, {}).get('name')!r}"
            for (platform, key), expected in _ENGLISH_NAME_SNAPSHOT.items()
            if bundles[platform].get(key, {}).get("name") != expected
        ]
        assert not mismatches, (
            "en.json name(s) drifted from the frozen _ENGLISH_NAME_SNAPSHOT "
            "(this would change English entity_ids):\n"
            + "\n".join(f"  {m}" for m in mismatches)
        )

        # Completeness: every entity converted by #1353 must be snapshotted,
        # so a new one added later without a matching entry here is forced
        # into the same lock instead of silently escaping it.
        mismatch_key = _class_translation_key(AdaptiveCoverPositionMismatchSensor)
        reset_key = _class_translation_key(AdaptiveCoverButton)
        expected_pairs = (
            {
                ("sensor", spec.translation_key)
                for spec in (*_STANDARD_SPECS, *_DIAGNOSTIC_SPECS)
                if spec.translation_key is not None
            }
            | {("binary_sensor", spec.key) for spec in _BINARY_SENSOR_SPECS}
            | {("binary_sensor", mismatch_key), ("button", reset_key)}
        )
        missing_from_snapshot = sorted(
            f"{platform}.{key}"
            for platform, key in expected_pairs
            if (platform, key) not in _ENGLISH_NAME_SNAPSHOT
        )
        assert not missing_from_snapshot, (
            f"New name-bearing entit{'y is' if len(missing_from_snapshot) == 1 else 'ies are'} "
            f"missing from _ENGLISH_NAME_SNAPSHOT: {missing_from_snapshot}\n"
            "Add it with its current en.json name."
        )

        # And the removed hardcoded overrides must STAY removed: HA only
        # reads the translation_key name when a subclass has not overridden
        # Entity.name itself. This is the same check HA's own
        # Entity.suggested_object_id uses to detect an override. Every class
        # production can actually instantiate — including the RestoreEntity
        # subclasses and the per-suffix ``_resolve_cls`` subclasses sensor.py
        # builds for unrecorded_attributes — not just the generic bases
        # (issue #1353 audit finding #1).
        production_classes = (
            set(_STANDARD_CLASSES.values())
            | set(_DIAGNOSTIC_CLASSES.values())
            | {
                AdaptiveCoverBinarySensor,
                AdaptiveCoverPositionMismatchSensor,
                AdaptiveCoverButton,
                AdaptiveCoverMyPositionButton,
                AdaptiveCoverApplyCalculatedPositionButton,
            }
        )
        for cls in production_classes:
            assert type.__getattribute__(cls, "name") is type.__getattribute__(
                Entity, "name"
            ), f"{cls.__name__} overrides Entity.name — remove it, use translation_key."

    # NOTE: a prior version of this file had
    # test_all_sensor_names_are_translated_in_every_language here, asserting
    # that every spec's translation_key has a non-empty
    # entity.sensor.<key>.name in en/de/fr. Deleted (audit finding #4): it
    # was fully redundant. test_sensor_translation_keys_exist_in_en_json
    # above already guarantees the key exists in en.json for every spec, and
    # DE/FR key-for-key parity with en.json (leaf-path level, so it covers
    # the nested ``.name`` sub-key too) is enforced by
    # tests/test_translations.py::test_key_structure_matches_en, while
    # non-empty values everywhere are enforced by
    # tests/test_translations.py::test_no_empty_string_values. Nothing here
    # asserted anything those two didn't already cover.

    def test_control_status_values_are_translated_in_every_language(self) -> None:
        """Every ControlStatus value has a localized display label."""
        status_values = {
            value
            for name, value in vars(ControlStatus).items()
            if name.isupper() and isinstance(value, str)
        }
        languages = _load_all_translation_bundles()

        for language, data in languages.items():
            states = data["entity"]["sensor"]["control_status"]["state"]
            assert set(states) >= status_values, (
                f"{language}: missing ControlStatus translations: "
                f"{sorted(status_values - set(states))}"
            )
            for value in status_values:
                assert states[value].strip()

    def test_binary_sensor_and_button_names_are_translated_in_every_language(
        self,
    ) -> None:
        """Binary-sensor and button names carry a real, non-empty DE/FR name.

        Checks presence + non-empty rather than pinning exact wording: the
        exact English strings are locked byte-for-byte by
        ``test_english_entity_names_match_legacy_names`` above (issue #1353
        audit finding #4), so this only needs to catch a missing key or an
        accidentally-blanked translation — not block a legitimate DE/FR
        wording fix. Includes the button's translation key, which previously
        had no DE/FR coverage at all.
        """
        mismatch_key = _class_translation_key(AdaptiveCoverPositionMismatchSensor)
        binary_keys = {spec.key for spec in _BINARY_SENSOR_SPECS} | {mismatch_key}
        button_keys = {_class_translation_key(AdaptiveCoverButton)}

        for language in ("de", "fr"):
            data = _load_translation_bundle(language)
            binary_sensor = data["entity"]["binary_sensor"]
            for key in binary_keys:
                assert (
                    key in binary_sensor
                ), f"{language}: missing entity.binary_sensor.{key}"
                assert binary_sensor[key][
                    "name"
                ].strip(), f"{language}: entity.binary_sensor.{key}.name is empty"

            button = data["entity"]["button"]
            for key in button_keys:
                assert key in button, f"{language}: missing entity.button.{key}"
                assert button[key][
                    "name"
                ].strip(), f"{language}: entity.button.{key}.name is empty"

    def test_last_skipped_action_reasons_are_translated_in_every_language(self) -> None:
        """Every code the coordinator can write into ``last_skipped_action.reason``
        must have a translated display state.

        The expected set is derived from the code that actually emits skip
        reasons, not hand-maintained: ``EXPECTED_SKIP_CODES`` (the
        ``cover_command`` ``_skip()`` canon) unioned with
        ``emitted_record_skipped_action_reasons()`` — an AST scan of
        coordinator.py and cover_command/__init__.py that resolves every
        reason argument reaching ``record_skipped_action()`` by any shape:
        a literal, a module-level constant (e.g. coordinator's
        ``_MANUAL_OVERRIDE_SKIP_LABEL``), or the ``_HOLD_SKIP_LABEL``
        dynamic lookup (read live off the dict, fallback literal read from
        the AST) — plus ``idle_last_skipped_value()`` (the idle state, read
        by calling the real ``sensor._last_skipped_value`` rather than
        retyping its return value).

        Building this from the scan result (rather than re-combining
        ``EXTRA_RECORD_SKIPPED_ACTION_REASONS`` and ``_HOLD_SKIP_LABEL``
        by hand here too) is what closes #1353 round 2's gap: a regex-based
        precursor to the scan could not see a reason passed as a bare
        constant ``Name`` (never a quoted literal), so
        ``_MANUAL_OVERRIDE_SKIP_LABEL`` silently escaped this union and only
        passed because its value happens to equal an already-documented
        ``_skip()`` code. A *new* module constant used the same way now
        fails in ``test_skip_reason_guard.py`` before it can reach this test
        untranslated.
        """
        reasons = (
            EXPECTED_SKIP_CODES
            | emitted_record_skipped_action_reasons()
            | {idle_last_skipped_value()}
        )

        for language, data in _load_all_translation_bundles().items():
            states = data["entity"]["sensor"]["last_skipped_action"]["state"]
            missing = reasons - set(states)
            assert (
                not missing
            ), f"{language}: missing skip-reason translations: {sorted(missing)}"
            for reason in reasons:
                assert states[
                    reason
                ].strip(), f"{language}: last_skipped_action.state.{reason} is empty"


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
    enumerating only the specs that *have* a translation_key would let a new
    sensor added without one escape the forced decision entirely, so this
    carries both values explicitly.
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
# Adding an entity without deciding either way is what the guard below is for.
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
            # These nine gained a translation_key under #1353 (they used to be
            # withheld because the namespace resolver had nothing to match on
            # at all), but they still belong here: each is continuously
            # varying, a timestamp, or an event echo — the same churn reason
            # the module docstring above gives for the whole set. A tracked
            # template reading Cover_Position/Cover_Tilt/sun_position would
            # re-render on every cycle's new percentage/angle; Start Sun/End
            # Sun/manual_override_end_time are timestamps; last_skipped_action
            # /last_cover_action are event echoes; position_verification's
            # retry count changes with every reconcile pass.
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


# ---------------------------------------------------------------------------
# ENUM sensor options-coverage guard (issue #1162)
# ---------------------------------------------------------------------------
#
# A static source scan over every ENUM sensor spec's ``value_fn``: every
# string literal it can return must be a declared member of the spec's
# ``options`` tuple. This is what would have caught #1162 — the
# ``motion_status`` spec's ``_motion_status_value`` had a reachable
# ``return "holding"`` that was never added to ``options``, so HA raised
# ValueError instead of publishing the state.
#
# Deliberately a static scan, not a dynamic reachability prover: it only
# collects literal string returns, never computed expressions (attribute
# access, calls — e.g. ``result.control_method.value``,
# ``climate_mode_from_diagnostics(...)``). Those sensors build their
# ``options`` directly from the same vocabulary their value_fn reads
# (decision_trace, travel_calibration, climate_status) and are correct by
# construction; a heuristic for computed expressions would create noise,
# not safety.


def _literal_strings_in_expr(node: ast.expr) -> set[str]:
    """Collect string-literal constants out of an expression.

    Follows the branches of a ternary (``a if cond else b``) so a value_fn
    that picks between literals via a single conditional expression is still
    fully covered. Anything else (attribute access, calls, names — computed
    expressions) contributes nothing: chasing those is not feasible in
    general, see the module-level note above.
    """
    if isinstance(node, ast.IfExp):
        return _literal_strings_in_expr(node.body) | _literal_strings_in_expr(
            node.orelse
        )
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    return set()


def _extract_lambda_expr(source: str, value_fn: Any) -> ast.Lambda:
    """Isolate a standalone-parseable lambda expression out of raw source.

    ``inspect.getsource`` on a lambda that is one of several keyword
    arguments on the same source line (e.g. ``value_fn=lambda e: ..., ``)
    returns the surrounding statement, which is not valid Python on its own,
    so a direct ``ast.parse`` of it can raise ``SyntaxError``. Find the
    ``lambda`` token and progressively trim trailing characters (a comma, an
    enclosing call's closing paren, …) until what remains parses as a
    standalone expression whose body is a ``Lambda`` node.

    Raises AssertionError naming ``value_fn`` if no such expression can be
    recovered, so an unprocessable spec fails loudly instead of silently
    reporting zero literals (and therefore vacuously "passing").
    """
    idx = source.find("lambda")
    assert idx != -1, f"No 'lambda' token found in source for {value_fn!r}: {source!r}"
    candidate = source[idx:]
    for end in range(len(candidate), 0, -1):
        snippet = candidate[:end]
        try:
            parsed = ast.parse(snippet, mode="eval")
        except SyntaxError:
            continue
        if isinstance(parsed.body, ast.Lambda):
            return parsed.body
    raise AssertionError(
        f"Could not isolate a parseable lambda expression for {value_fn!r} "
        f"from source: {source!r}"
    )


def _string_literals_returned(value_fn: Any) -> set[str]:
    """Every string-literal value ``value_fn`` can return.

    Handles both plain ``def`` functions (walk every ``ast.Return`` node)
    and lambdas — whose body has no ``Return`` node at all, since it is a
    bare expression, so the expression itself (and any ternary branches
    within it) is inspected directly.

    Fails loudly (an AssertionError naming ``value_fn``) if the source
    cannot be retrieved or parsed, rather than swallowing the exception and
    silently reporting no findings for a spec the scan can no longer see
    into.
    """
    try:
        source = textwrap.dedent(inspect.getsource(value_fn))
    except (OSError, TypeError) as exc:
        raise AssertionError(
            f"Could not retrieve source for value_fn {value_fn!r}: {exc}"
        ) from exc

    if getattr(value_fn, "__name__", None) == "<lambda>":
        lambda_node = _extract_lambda_expr(source, value_fn)
        return _literal_strings_in_expr(lambda_node.body)

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise AssertionError(
            f"Could not parse source for value_fn {value_fn!r}: {exc}\n{source}"
        ) from exc

    literals: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.value is not None:
            literals |= _literal_strings_in_expr(node.value)
    return literals


class TestEnumSensorOptionsCoverage:
    """Every ENUM sensor's literal value_fn returns must be declared options."""

    def test_enum_sensor_literal_returns_are_declared_options(self) -> None:
        """Regression guard for issue #1162.

        For every sensor spec with ``device_class is SensorDeviceClass.ENUM``,
        every string literal its ``value_fn`` can return must be a member of
        the spec's declared ``options`` tuple — otherwise HA's SensorEntity
        raises ValueError instead of publishing the state the first time that
        branch fires in production.
        """
        all_specs = (*_STANDARD_SPECS, *_DIAGNOSTIC_SPECS)
        enum_specs = [
            spec for spec in all_specs if spec.device_class is SensorDeviceClass.ENUM
        ]
        assert enum_specs, (
            "Expected at least one ENUM sensor spec to scan. An empty list "
            "means the scan mechanism itself broke (e.g. every value_fn "
            "became a functools.partial), not that there is nothing left to "
            "check — a vacuous pass here would hide that."
        )

        failures = []
        for spec in enum_specs:
            literals = _string_literals_returned(spec.value_fn)
            undeclared = literals - set(spec.options or ())
            if undeclared:
                failures.append(
                    f"{spec.suffix!r} value_fn returns literal(s) "
                    f"{sorted(undeclared)} not present in its declared "
                    f"options {spec.options!r}"
                )
        assert not failures, "\n".join(failures)
