"""Tests for the reason-string i18n foundation (issue #882).

The pipeline decision-trace reason strings, diagnostics explanations, and
engine control-state reasons are localized via a ``reason_i18n/`` bundle that
mirrors the ``summary_i18n/`` mechanism. English output must stay
byte-identical to the pre-i18n f-strings so the ~40 reason-asserting test
files stay green.

This file covers the foundation only (plan steps 1 & 2): the ``ReasonCode``
vocabulary, the ``_REASON_TEMPLATES_EN`` map, the ``Reason`` payload + render
helpers, the shipped ``reason_i18n/{en,de,fr}.json`` bundles + parity locks,
and the shared ``i18n_bundle`` loader. No handler/builder/engine emitter is
migrated yet.
"""

from __future__ import annotations

import inspect
import json
import string
from pathlib import Path

import pytest

from custom_components.adaptive_cover_pro import i18n_bundle, reason_i18n
from custom_components.adaptive_cover_pro.const import ReasonCode
from custom_components.adaptive_cover_pro.reason_i18n import (
    _REASON_TEMPLATES_EN,
    Reason,
    async_prime,
    load_reason_labels,
    reason_to_dict,
    render,
    render_en,
)

pytestmark = pytest.mark.unit

REASON_I18N_DIR = (
    Path(__file__).parent.parent
    / "custom_components"
    / "adaptive_cover_pro"
    / "reason_i18n"
)


# ---------------------------------------------------------------------------
# Legacy-string render table — one row per ReasonCode with representative
# params, asserting render_en() reproduces the exact legacy f-string output.
# ``params`` values may be nested ``Reason`` fragments (composed sub-phrases)
# or plain values.
# ---------------------------------------------------------------------------

_SUNSET = Reason(ReasonCode.FRAGMENT_SUNSET_POSITION)
_DEFAULT = Reason(ReasonCode.FRAGMENT_DEFAULT_POSITION)

# (code, params, expected legacy string)
LEGACY_CASES: list[tuple[str, dict, str]] = [
    # --- fragments (rendered standalone) ---
    (ReasonCode.FRAGMENT_SUNSET_POSITION, {}, "sunset position"),
    (ReasonCode.FRAGMENT_DEFAULT_POSITION, {}, "default position"),
    (ReasonCode.FRAGMENT_CLOUDY_POSITION, {}, "cloudy position"),
    (ReasonCode.FRAGMENT_COVERAGE_STEP, {"steps": 3}, " (coverage step, max 3)"),
    (ReasonCode.FRAGMENT_Z_ADJUSTED, {}, " (Z-adjusted)"),
    (
        ReasonCode.FRAGMENT_BYPASS_NOTE,
        {},
        " [bypasses automatic control]",
    ),
    (ReasonCode.FRAGMENT_SEASON_EXTREME_HEAT, {}, "extreme heat"),
    (
        ReasonCode.FRAGMENT_SEASON_TRACKING_OFF,
        {},
        "default: tracking off this season",
    ),
    (ReasonCode.FRAGMENT_SEASON_SUMMER, {}, "summer"),
    (ReasonCode.FRAGMENT_SEASON_WINTER, {}, "winter"),
    (
        ReasonCode.FRAGMENT_SEASON_GLARE_LOW_LIGHT,
        {},
        "glare control (low light)",
    ),
    (ReasonCode.FRAGMENT_SEASON_GLARE, {}, "glare control"),
    (ReasonCode.FRAGMENT_TRIGGER_NOT_SUNNY, {}, "weather not sunny"),
    (ReasonCode.FRAGMENT_TRIGGER_LUX_BELOW, {}, "lux below threshold"),
    (
        ReasonCode.FRAGMENT_TRIGGER_IRRADIANCE_BELOW,
        {},
        "irradiance below threshold",
    ),
    (
        ReasonCode.FRAGMENT_TRIGGER_CLOUD_ABOVE,
        {},
        "cloud coverage above threshold",
    ),
    (ReasonCode.FRAGMENT_TRIGGER_SMOOTHING_HOLD, {}, "smoothing hold"),
    (ReasonCode.FRAGMENT_TRIGGER_TEMPLATE, {}, "template"),
    (ReasonCode.FRAGMENT_TRIGGER_FALLBACK, {}, "trigger"),
    # --- solar ---
    (
        ReasonCode.SOLAR_TRACKING,
        {"position": 50, "suffix": ""},
        "sun within acceptance angle — position 50%",
    ),
    (
        ReasonCode.SOLAR_TRACKING,
        {
            "position": 50,
            "suffix": Reason(ReasonCode.FRAGMENT_COVERAGE_STEP, {"steps": 4}),
        },
        "sun within acceptance angle — position 50% (coverage step, max 4)",
    ),
    # --- manual override ---
    (
        ReasonCode.MANUAL_HOLDING_SOLAR,
        {"held": 30, "position": 60},
        "manual override active — holding 30% (solar would-be 60%)",
    ),
    (
        ReasonCode.MANUAL_SOLAR_ONLY,
        {"position": 60},
        "manual override active — solar would-be 60%",
    ),
    (
        ReasonCode.MANUAL_HOLDING_LABEL,
        {"held": 30, "pos_label": _SUNSET, "position": 60},
        "manual override active — holding 30% (sunset position would be 60%)",
    ),
    (
        ReasonCode.MANUAL_LABEL_ONLY,
        {"pos_label": _DEFAULT, "position": 60},
        "manual override active — default position 60%",
    ),
    # --- occupancy / motion timeout (#881 wording) ---
    (
        ReasonCode.OCCUPANCY_HOLDING,
        {"held": 42},
        "occupancy timeout — holding position 42% (sun within acceptance angle)",
    ),
    (
        ReasonCode.OCCUPANCY_LABEL,
        {"pos_label": _DEFAULT, "position": 20},
        "occupancy timeout active — default position 20%",
    ),
    # --- climate ---
    (
        ReasonCode.CLIMATE_ACTIVE,
        {"season": Reason(ReasonCode.FRAGMENT_SEASON_SUMMER), "position": 10},
        "climate mode active (summer) — position 10%",
    ),
    # --- glare zone ---
    (
        ReasonCode.GLARE_PROTECTION,
        {"zones": "Desk, Sofa", "distance": 1.5, "z_suffix": "", "position": 35},
        "glare zone protection (Desk, Sofa) — effective distance 1.50m → position 35%",
    ),
    (
        ReasonCode.GLARE_PROTECTION,
        {
            "zones": "Desk",
            "distance": 2.0,
            "z_suffix": Reason(ReasonCode.FRAGMENT_Z_ADJUSTED),
            "position": 35,
        },
        "glare zone protection (Desk) — effective distance 2.00m (Z-adjusted) → position 35%",
    ),
    # --- cloud suppression ---
    (
        ReasonCode.CLOUD_SUPPRESSION,
        {
            "triggers": [
                Reason(ReasonCode.FRAGMENT_TRIGGER_NOT_SUNNY),
                Reason(ReasonCode.FRAGMENT_TRIGGER_LUX_BELOW),
            ],
            "pos_label": _DEFAULT,
            "position": 25,
        },
        "cloud/low-light suppression — weather not sunny, lux below threshold → default position 25%",
    ),
    # --- weather ---
    (
        ReasonCode.WEATHER_ACTIVE,
        {"position": 0, "bypass_note": ""},
        "weather override active — position 0%",
    ),
    (
        ReasonCode.WEATHER_ACTIVE,
        {"position": 0, "bypass_note": Reason(ReasonCode.FRAGMENT_BYPASS_NOTE)},
        "weather override active — position 0% [bypasses automatic control]",
    ),
    # --- custom position ---
    (
        ReasonCode.CUSTOM_HEAD_NAMED,
        {"name": "Privacy"},
        "Privacy active",
    ),
    (
        ReasonCode.CUSTOM_HEAD_SLOT,
        {"slot": 3, "trigger": "binary_sensor.a"},
        "custom position #3 active (binary_sensor.a)",
    ),
    (
        ReasonCode.CUSTOM_USE_MY,
        {
            "head": Reason(ReasonCode.CUSTOM_HEAD_NAMED, {"name": "Privacy"}),
            "position": 70,
            "bypass_note": "",
        },
        "Privacy active — use My position (70%)",
    ),
    (
        ReasonCode.CUSTOM_POSITION,
        {
            "head": Reason(
                ReasonCode.CUSTOM_HEAD_SLOT, {"slot": 3, "trigger": "template"}
            ),
            "position": 70,
            "bypass_note": Reason(ReasonCode.FRAGMENT_BYPASS_NOTE),
        },
        "custom position #3 active (template) — position 70% [bypasses automatic control]",
    ),
    # --- default handler ---
    (
        ReasonCode.DEFAULT_SUNSET_USE_MY,
        {"position": 55},
        "sunset position — use My position (55%)",
    ),
    (
        ReasonCode.DEFAULT_NO_CONDITION,
        {"pos_label": _SUNSET, "position": 55},
        "no active condition — sunset position 55%",
    ),
    # --- group ---
    (
        ReasonCode.GROUP_LOCK,
        {"group_id": "living", "position": 40},
        "group lock from group living — holding 40%",
    ),
    (
        ReasonCode.GROUP_SCENE,
        {"scene": "Privacy", "group_id": "living", "position": 40},
        "group scene 'Privacy' from group living → 40%",
    ),
    # --- registry ---
    (
        ReasonCode.REGISTRY_OUTPRIORITIZED,
        {"handler": "weather"},
        "outprioritized by weather",
    ),
    (
        ReasonCode.REGISTRY_FLOOR_RAISED,
        {"from_pos": 10, "to_pos": 60, "label": "Desk sensor"},
        "floor raised winner from 10% to 60% by Desk sensor",
    ),
    (
        ReasonCode.REGISTRY_FLOOR_INACTIVE,
        {"floor_pos": 60, "winner_pos": 80},
        "floor 60% inactive (winner 80% above floor)",
    ),
    (
        ReasonCode.REGISTRY_TILT_APPLIED,
        {"tilt": 30, "label": "Slat sensor", "handler": "solar"},
        "tilt-only: slat angle fixed at 30% by Slat sensor; position driven by solar",
    ),
    (
        ReasonCode.REGISTRY_TILT_DEFERRED,
        {"tilt": 30, "handler": "solar", "winner_tilt": 45},
        "tilt-only 30% deferred — solar already set tilt 45%",
    ),
    (
        ReasonCode.REGISTRY_CEILING_LOWERED,
        {"from_pos": 80, "to_pos": 60, "label": "Awning sensor"},
        "ceiling lowered winner from 80% to 60% by Awning sensor",
    ),
    (
        ReasonCode.REGISTRY_CEILING_INACTIVE,
        {"ceiling_pos": 60, "to_pos": 40},
        "ceiling 60% inactive (resolved 40% at or below ceiling)",
    ),
    (
        ReasonCode.REGISTRY_CEILING_OVERRIDDEN,
        {"ceiling_pos": 40, "to_pos": 60},
        "ceiling 40% overridden — a floor raised the cover to 60%",
    ),
    (
        ReasonCode.REGISTRY_FLOOR_OVERRIDES_CEILING,
        {"to_pos": 60, "ceiling_pos": 40, "label": "Floor sensor", "from_pos": 80},
        "floor raised to 60% over ceiling 40% by Floor sensor (winner was 80%)",
    ),
    (
        ReasonCode.REGISTRY_TILT_BOUND_ACTIVE,
        {"low_label": "50%", "high_label": "—", "label": "Door sensor"},
        "tilt bound 50%–— active by Door sensor; awaiting the resolved tilt",
    ),
    (
        ReasonCode.REGISTRY_TILT_BOUND_INACTIVE,
        {"low_label": "50%", "high_label": "—", "label": "Door sensor", "tilt": 75},
        "tilt bound 50%–— inactive by Door sensor; resolved tilt 75% already within",
    ),
    (
        ReasonCode.REGISTRY_TILT_CLAMPED,
        {"from_tilt": 30, "to_tilt": 50, "label": "Door sensor"},
        "tilt clamped from 30% to 50% by Door sensor",
    ),
    # --- builder ---
    (ReasonCode.BUILDER_UNKNOWN, {}, "Unknown"),
    (ReasonCode.BUILDER_CONTROL_OCCUPANCY_TIMEOUT, {}, "Occupancy Timeout"),
    (ReasonCode.BUILDER_CONTROL_MANUAL_OVERRIDE, {}, "Manual Override"),
    (
        ReasonCode.BUILDER_CONTROL_TRACKING_OFF_SEASON,
        {},
        "Default: Tracking Off This Season",
    ),
    (
        ReasonCode.BUILDER_CONTROL_TILT_FIXED,
        {"reason": "Manual Override", "slot": 5},
        "Manual Override — tilt fixed by Custom #5",
    ),
    (
        ReasonCode.BUILDER_OUTSIDE_WINDOW,
        {"pos_label": _DEFAULT, "pos": 50},
        "Outside time window → default position 50% (commands paused)",
    ),
    (
        ReasonCode.BUILDER_MANUAL_DIVERGENCE,
        {"held": 30, "raw": 60},
        "manual override active — holding cover at 30% (solar would be 60%)",
    ),
    (
        ReasonCode.BUILDER_TILT_FIXED,
        {"tilt": 30, "slot": 5},
        "tilt fixed at 30% by Custom #5",
    ),
    (ReasonCode.BUILDER_INTERPOLATED, {"final": 48}, "interpolated → 48%"),
    (ReasonCode.BUILDER_INVERSED, {"final": 52}, "inversed → 52%"),
    # --- engine control_state_reason ---
    (ReasonCode.ENGINE_DIRECT_SUN, {}, "Direct Sun"),
    (ReasonCode.ENGINE_DEFAULT_SUNSET_OFFSET, {}, "Default: Sunset Offset"),
    (ReasonCode.ENGINE_DEFAULT_ELEVATION_LIMIT, {}, "Default: Elevation Limit"),
    (
        ReasonCode.ENGINE_DEFAULT_ACCEPTANCE_ANGLE_EXIT,
        {},
        "Default: Acceptance Angle Exit",
    ),
    (ReasonCode.ENGINE_DEFAULT_BLIND_SPOT, {}, "Default: Blind Spot"),
    (ReasonCode.ENGINE_DEFAULT, {}, "Default"),
    # --- skip / describe_skip ---
    (ReasonCode.SKIP_OUTSIDE_WINDOW, {}, "outside time window"),
    (
        ReasonCode.SKIP_SUN_OUTSIDE,
        {},
        "sun outside acceptance angle or elevation limits",
    ),
    (ReasonCode.SKIP_MANUAL_NOT_ACTIVE, {}, "manual override not active"),
    (ReasonCode.SKIP_OCCUPANCY_DISABLED, {}, "occupancy detection disabled"),
    (ReasonCode.SKIP_OCCUPANCY_NOT_ACTIVE, {}, "occupancy timeout not active"),
    (ReasonCode.SKIP_CLIMATE_MODE_OFF, {}, "climate mode not enabled"),
    (
        ReasonCode.SKIP_CLIMATE_READINGS_UNAVAILABLE,
        {},
        "climate readings or options unavailable",
    ),
    (
        ReasonCode.SKIP_CLIMATE_DEFERRED,
        {},
        "deferred glare-control to solar/glare handlers",
    ),
    (
        ReasonCode.SKIP_NO_GLARE_ZONES,
        {},
        "no active glare zones or sun outside acceptance angle",
    ),
    (
        ReasonCode.SKIP_CLOUD_SKIPPED,
        {},
        "cloud suppression skipped (sun outside acceptance angle)",
    ),
    (
        ReasonCode.SKIP_CLOUD_INACTIVE,
        {},
        "cloud suppression inactive (direct sun present or feature disabled)",
    ),
    (ReasonCode.SKIP_WEATHER_NOT_ACTIVE, {}, "weather override not active"),
    (
        ReasonCode.SKIP_CUSTOM_NOT_ACTIVE,
        {"slot": 2},
        "custom position #2 not active",
    ),
    (ReasonCode.SKIP_ALWAYS_MATCHES, {}, "always matches"),
    (
        ReasonCode.SKIP_GROUP_SCENE_NOT_LOCK,
        {},
        "group intent is a scene, not a lock",
    ),
    (ReasonCode.SKIP_NO_GROUP_LOCK, {}, "no group lock intent"),
    (
        ReasonCode.SKIP_GROUP_LOCK_NOT_SCENE,
        {},
        "group intent is a lock, not a scene",
    ),
    (ReasonCode.SKIP_NO_GROUP_SCENE, {}, "no group scene intent"),
    (ReasonCode.SKIP_NOT_ACTIVE, {}, "not active"),
]


# ---------------------------------------------------------------------------
# Step 1: contract — every code has a template and vice-versa
# ---------------------------------------------------------------------------


def test_every_reason_code_has_template() -> None:
    codes = {c.value for c in ReasonCode}
    templates = set(_REASON_TEMPLATES_EN)
    assert codes == templates, (
        f"codes-without-template: {sorted(codes - templates)}\n"
        f"templates-without-code: {sorted(templates - codes)}"
    )


def test_en_render_matches_legacy_strings() -> None:
    """render_en() reproduces the exact legacy f-string output for every code."""
    for code, params, expected in LEGACY_CASES:
        got = render_en(Reason(code, params))
        assert got == expected, f"{code}: {got!r} != {expected!r}"


def test_legacy_cases_cover_every_code() -> None:
    """The legacy-render table must exercise every ReasonCode at least once."""
    covered = {code for code, _, _ in LEGACY_CASES}
    all_codes = {c.value for c in ReasonCode}
    assert covered == all_codes, (
        f"uncovered codes: {sorted(all_codes - covered)}\n"
        f"unknown codes in table: {sorted(covered - all_codes)}"
    )


# ---------------------------------------------------------------------------
# Fragment recursion + fallback
# ---------------------------------------------------------------------------


def test_nested_reason_fragment_renders_inline() -> None:
    reason = Reason(
        ReasonCode.MANUAL_LABEL_ONLY,
        {"pos_label": Reason(ReasonCode.FRAGMENT_SUNSET_POSITION), "position": 60},
    )
    assert render_en(reason) == "manual override active — sunset position 60%"


def test_fragment_sequence_joins_with_comma_space() -> None:
    reason = Reason(
        ReasonCode.CLOUD_SUPPRESSION,
        {
            "triggers": (
                Reason(ReasonCode.FRAGMENT_TRIGGER_NOT_SUNNY),
                Reason(ReasonCode.FRAGMENT_TRIGGER_LUX_BELOW),
                Reason(ReasonCode.FRAGMENT_TRIGGER_SMOOTHING_HOLD),
            ),
            "pos_label": Reason(ReasonCode.FRAGMENT_CLOUDY_POSITION),
            "position": 25,
        },
    )
    assert render_en(reason) == (
        "cloud/low-light suppression — weather not sunny, lux below threshold, "
        "smoothing hold → cloudy position 25%"
    )


def test_unknown_code_falls_back_to_code_string() -> None:
    """An unknown/missing code degrades to the code string itself."""
    assert render_en(Reason("does.not.exist")) == "does.not.exist"
    assert render(Reason("also.missing", {"x": 1}), {}) == "also.missing"


def test_reason_to_dict_is_json_safe_and_nested() -> None:
    reason = Reason(
        ReasonCode.CLOUD_SUPPRESSION,
        {
            "triggers": [Reason(ReasonCode.FRAGMENT_TRIGGER_NOT_SUNNY)],
            "pos_label": Reason(ReasonCode.FRAGMENT_DEFAULT_POSITION),
            "position": 25,
        },
    )
    d = reason_to_dict(reason)
    # Round-trips through JSON unchanged (proves it is JSON-safe).
    assert json.loads(json.dumps(d)) == d
    assert d["code"] == ReasonCode.CLOUD_SUPPRESSION
    assert d["params"]["position"] == 25
    assert d["params"]["triggers"][0]["code"] == ReasonCode.FRAGMENT_TRIGGER_NOT_SUNNY
    assert d["params"]["pos_label"]["code"] == ReasonCode.FRAGMENT_DEFAULT_POSITION


def test_reason_params_default_is_empty_mapping() -> None:
    reason = Reason(ReasonCode.ENGINE_DIRECT_SUN)
    assert dict(reason.params) == {}
    assert render_en(reason) == "Direct Sun"


# ---------------------------------------------------------------------------
# Bundle parity locks (mirror summary_i18n)
# ---------------------------------------------------------------------------


def _flat(data: dict) -> dict[str, str]:
    out: dict[str, str] = {}

    def _walk(node: object, prefix: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                _walk(v, f"{prefix}.{k}" if prefix else k)
        elif isinstance(node, str):
            out[prefix] = node

    _walk(data, "")
    return out


def _load_json(name: str) -> dict:
    with (REASON_I18N_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


def _placeholders(template: str) -> set[tuple[str, str | None, str | None]]:
    """Return the (field, format_spec, conversion) placeholder tuples.

    Includes the format spec (e.g. ``.2f``) so a DE/FR template that drops a
    numeric format is caught, not just a renamed field.
    """
    stripped = template.replace("{{", "").replace("}}", "")
    return {
        (field, spec, conv)
        for _, field, spec, conv in string.Formatter().parse(stripped)
        if field
    }


def test_reason_i18n_en_matches_code_defaults() -> None:
    """Flattened ``reason_i18n/en.json`` == ``_REASON_TEMPLATES_EN`` byte-for-byte."""
    assert _flat(_load_json("en.json")) == _REASON_TEMPLATES_EN


def test_reason_i18n_key_parity_de_fr() -> None:
    en = _flat(_load_json("en.json"))
    assert en, "en.json must not be empty"
    for lang in ("de", "fr"):
        target = _flat(_load_json(f"{lang}.json"))
        assert set(target) == set(en), (
            f"{lang}.json key-set differs from en.json:\n"
            f"  missing: {sorted(set(en) - set(target))[:10]}\n"
            f"  extra:   {sorted(set(target) - set(en))[:10]}"
        )


def test_reason_placeholder_parity_de_fr() -> None:
    en = _flat(_load_json("en.json"))
    for lang in ("de", "fr"):
        target = _flat(_load_json(f"{lang}.json"))
        for key, en_value in en.items():
            assert key in target, f"{lang}.json missing key {key!r}"
            assert _placeholders(en_value) == _placeholders(target[key]), (
                f"{lang}.json[{key}] placeholders {_placeholders(target[key])} "
                f"!= en {_placeholders(en_value)}"
            )


# ---------------------------------------------------------------------------
# Step 2: shared i18n_bundle loader
# ---------------------------------------------------------------------------


def test_flatten_bundle_nested_to_dotted() -> None:
    nested = {"solar": {"tracking": "T"}, "skip": {"not_active": "N"}}
    assert i18n_bundle.flatten_bundle(nested) == {
        "solar.tracking": "T",
        "skip.not_active": "N",
    }


def test_load_bundle_overlay_partial_overrides_only_its_keys(tmp_path: Path) -> None:
    """A partial overlay overrides only its keys; the rest fall back to defaults."""
    (tmp_path / "xx.json").write_text(
        json.dumps({"solar": {"tracking": "XX-TRACK"}}), encoding="utf-8"
    )
    defaults = {"solar.tracking": "EN-TRACK", "skip.not_active": "EN-NA"}
    overlay = i18n_bundle.load_bundle_overlay(tmp_path, "xx")
    merged = i18n_bundle.merge_labels(defaults, overlay)
    assert merged == {"solar.tracking": "XX-TRACK", "skip.not_active": "EN-NA"}


def test_load_bundle_overlay_missing_language_is_empty(tmp_path: Path) -> None:
    assert i18n_bundle.load_bundle_overlay(tmp_path, "zz") == {}


def test_load_bundle_overlay_en_is_empty(tmp_path: Path) -> None:
    (tmp_path / "en.json").write_text('{"a": "b"}', encoding="utf-8")
    assert i18n_bundle.load_bundle_overlay(tmp_path, "en") == {}


def test_load_bundle_overlay_malformed_is_empty(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert i18n_bundle.load_bundle_overlay(tmp_path, "bad") == {}


def test_load_reason_labels_en_returns_code_defaults() -> None:
    assert load_reason_labels("en") == _REASON_TEMPLATES_EN


def test_load_reason_labels_missing_language_falls_back_to_english() -> None:
    assert load_reason_labels("zz") == _REASON_TEMPLATES_EN


def test_load_reason_labels_de_fr_are_translated() -> None:
    """DE/FR ship real translations: they cover every code but diverge from EN."""
    for lang in ("de", "fr"):
        labels = load_reason_labels(lang)
        assert set(labels) == set(_REASON_TEMPLATES_EN)
        assert labels != _REASON_TEMPLATES_EN


def test_load_reason_labels_is_cached() -> None:
    assert load_reason_labels("fr") is not None
    assert load_reason_labels("fr") == load_reason_labels("fr")


async def test_async_prime_offloads_language_to_executor() -> None:
    class _FakeHass:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        async def async_add_executor_job(self, func, *args):
            self.calls.append(args)
            return func(*args)

    hass = _FakeHass()
    labels = await async_prime(hass, "fr")
    assert hass.calls == [("fr",)]
    assert labels == load_reason_labels("fr")


def test_reason_i18n_module_has_no_homeassistant_import() -> None:
    """The reason_i18n module must stay pure stdlib (engine-like constraint)."""
    src = inspect.getsource(reason_i18n)
    assert "import homeassistant" not in src
    assert "from homeassistant" not in src


def test_render_without_labels_equals_render_en() -> None:
    """``render(reason)`` with no labels arg == ``render_en(reason)`` for every code.

    Covers plain scalars, nested-fragment params, and fragment sequences.
    """
    for code, params, expected in LEGACY_CASES:
        reason = Reason(code, params)
        assert render(reason) == render_en(reason) == expected


def test_render_none_labels_matches_english() -> None:
    """Passing ``labels=None`` explicitly is identical to the English default."""
    reason = Reason(
        ReasonCode.CLOUD_SUPPRESSION,
        {
            "triggers": [
                Reason(ReasonCode.FRAGMENT_TRIGGER_NOT_SUNNY),
                Reason(ReasonCode.FRAGMENT_TRIGGER_LUX_BELOW),
            ],
            "pos_label": Reason(ReasonCode.FRAGMENT_DEFAULT_POSITION),
            "position": 25,
        },
    )
    assert render(reason, None) == render(reason) == render_en(reason)


def test_render_logs_debug_on_bad_params(caplog: pytest.LogCaptureFixture) -> None:
    """A template whose params can't format logs a debug line and returns the template."""
    import logging

    reason = Reason(ReasonCode.SOLAR_TRACKING, {})  # missing position/suffix
    with caplog.at_level(
        logging.DEBUG, logger="custom_components.adaptive_cover_pro.reason_i18n"
    ):
        result = render(reason, {})
    # Fallback behavior is unchanged: the raw template is returned.
    assert result == _REASON_TEMPLATES_EN[ReasonCode.SOLAR_TRACKING]
    assert any(
        ReasonCode.SOLAR_TRACKING in record.getMessage() for record in caplog.records
    )


def test_render_en_renders_via_overlay_fallback() -> None:
    """render(reason, labels) uses the overlay, falling back to EN per key."""
    reason = Reason(ReasonCode.ENGINE_DIRECT_SUN)
    assert render(reason, {ReasonCode.ENGINE_DIRECT_SUN: "Direkte Sonne"}) == (
        "Direkte Sonne"
    )
    # A key absent from the overlay falls back to English.
    assert render(reason, {"other.key": "x"}) == "Direct Sun"


# ---------------------------------------------------------------------------
# Step 3: reason_payload wire format on pipeline types + base handler
# ---------------------------------------------------------------------------
#
# ``PipelineResult`` / ``DecisionStep`` gain a ``reason_payload`` field so
# handlers can emit a stable ``Reason`` code+params. The canonical EN
# ``reason`` string is auto-derived from the payload when no explicit reason is
# passed, and the legacy explicit-string path is preserved unchanged.


from custom_components.adaptive_cover_pro.const import ControlMethod  # noqa: E402
from custom_components.adaptive_cover_pro.pipeline.handler import (  # noqa: E402
    OverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.types import (  # noqa: E402
    DecisionStep,
    PipelineResult,
)


def test_pipeline_result_payload_autoderives_reason() -> None:
    """A payload with no explicit reason auto-derives reason == render_en(payload)."""
    payload = Reason(ReasonCode.SOLAR_TRACKING, {"position": 50, "suffix": ""})
    result = PipelineResult(
        position=50,
        control_method=ControlMethod.SOLAR,
        reason_payload=payload,
    )
    assert result.reason == "sun within acceptance angle — position 50%"
    assert result.reason == render_en(payload)
    assert result.reason_payload is payload


def test_pipeline_result_explicit_reason_preserved_without_payload() -> None:
    """The legacy explicit-string path is unchanged (payload stays None)."""
    result = PipelineResult(
        position=50,
        control_method=ControlMethod.SOLAR,
        reason="legacy free text",
    )
    assert result.reason == "legacy free text"
    assert result.reason_payload is None


def test_pipeline_result_explicit_reason_wins_over_payload() -> None:
    """An explicit reason is kept even when a payload is also supplied."""
    payload = Reason(ReasonCode.SOLAR_TRACKING, {"position": 50, "suffix": ""})
    result = PipelineResult(
        position=50,
        control_method=ControlMethod.SOLAR,
        reason="explicit",
        reason_payload=payload,
    )
    assert result.reason == "explicit"
    assert result.reason_payload is payload


def test_decision_step_payload_autoderives_reason() -> None:
    payload = Reason(ReasonCode.REGISTRY_OUTPRIORITIZED, {"handler": "weather"})
    step = DecisionStep(
        handler="solar",
        matched=False,
        reason_payload=payload,
        position=50,
    )
    assert step.reason == "outprioritized by weather"
    assert step.reason_payload is payload


def test_decision_step_explicit_reason_preserved_without_payload() -> None:
    step = DecisionStep(handler="solar", matched=True, reason="won", position=50)
    assert step.reason == "won"
    assert step.reason_payload is None


def test_base_describe_skip_returns_not_active_reason() -> None:
    """The base handler's describe_skip returns Reason(SKIP_NOT_ACTIVE)."""

    class _Bare(OverrideHandler):
        name = "bare"
        priority = 5

        def evaluate(self, snapshot):  # noqa: ARG002
            return None

    skip = _Bare().describe_skip(object())  # type: ignore[arg-type]
    assert isinstance(skip, Reason)
    assert skip.code == ReasonCode.SKIP_NOT_ACTIVE
    assert render_en(skip) == "not active"
