"""Entity spec translation_key consistency guard.

Two guards:

1. Sensor specs with ``translation_key`` must reference a key that exists in
   ``en.json["entity"]["sensor"]``. Fails when a new sensor spec is added with
   a translation_key that was not added to the translation file.

2. Non-glare switch translation keys in ``en.json["entity"]["switch"]`` must
   correspond to an actual switch spec ``key`` value. Fails when an entry is
   added to en.json without a matching spec (orphaned translation) or vice versa.

When you add a new sensor spec with a translation_key:
  - Add the entry to translations/en.json under entity.sensor
  - Run the acp-translate skill to propagate to de.json and fr.json
  - Update _EXPECTED_SENSOR_TRANSLATION_KEYS below
"""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.adaptive_cover_pro.sensor import (
    _DIAGNOSTIC_SPECS,
    _STANDARD_SPECS,
)
from custom_components.adaptive_cover_pro.switch import _SWITCH_SPECS

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
    {"control_status", "decision_trace", "motion_status"}
)

# Switch keys that appear in en.json under entity.switch but are generated
# dynamically (not in _SWITCH_SPECS) and therefore excluded from the spec check.
_DYNAMIC_SWITCH_KEYS_PREFIX = "glare_zone_"


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

        orphaned = en_keys - spec_keys
        assert not orphaned, (
            f"en.json entity.sensor contains entries with no matching sensor spec "
            f"translation_key: {sorted(orphaned)}\n"
            "Remove the orphaned entry from en.json (and de.json, fr.json)."
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

        orphaned = static_en_keys - spec_keys
        assert not orphaned, (
            f"en.json entity.switch contains static entries with no matching "
            f"_SwitchSpec key: {sorted(orphaned)}\n"
            "Remove the orphaned entry or add a matching _SwitchSpec."
        )
