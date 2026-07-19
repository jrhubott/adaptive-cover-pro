"""Render Building Profile overview / override views (markdown + records).

A Building Profile is a virtual config entry that holds shared sensor IDs and
copies them into every linked cover (see ``profile_link``). Under the
inherit/override model a linked cover inherits the profile's sensors unless it
locally overrides one. This module builds:

- the read-only **Overview** shown in the profile's options flow (shared sensors,
  linked-cover roster, behavioral settings comparison),
- the per-sensor **inherit/override breakdown** shown on a linked cover's own
  sensor steps (`profile_value_breakdown`), and
- structured **override records** (`build_override_records`) consumed by the
  profile's Local Overrides step and the overview's override notes.

English-only by design (a maintenance/diagnostic view, mirroring the
English-deferred ``summary_geometry_lines``); the markdown body is authored
through the ``_LABELS`` dict so a later ``acp-translate`` pass can lift it into
``summary_i18n`` without restructuring. Only option keys / values are read — this
never branches on cover-type strings (uses ``get_policy``).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    BUILDING_PROFILE_SENSOR_KEYS,
    CONF_CLIMATE_MODE,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_CLOUD_COVERAGE_THRESHOLD,
    CONF_CLOUD_SUPPRESSION,
    CONF_CLOUDY_POSITION,
    CONF_DAYTIME_GATE_SENSORS,
    CONF_DAYTIME_GATE_TEMPLATE,
    CONF_DEFAULT_HEIGHT,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_ENABLE_GLARE_ZONES,
    CONF_END_TIME,
    CONF_ENTITIES,
    CONF_IRRADIANCE_ENTITY,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_IS_SUNNY_SENSOR,
    CONF_IS_SUNNY_TEMPLATE,
    CONF_LUX_ENTITY,
    CONF_LUX_THRESHOLD,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_MANUAL_THRESHOLD,
    CONF_MAX_ELEVATION,
    CONF_MAX_POSITION,
    CONF_MIN_ELEVATION,
    CONF_MIN_POSITION,
    CONF_MOTION_TIMEOUT,
    CONF_OUTSIDE_THRESHOLD,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_SENSOR_TYPE,
    CONF_START_TIME,
    CONF_SUNRISE_TIME_ENTITY,
    CONF_SUNSET_POS,
    CONF_SUNSET_TIME_ENTITY,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_WEATHER_ENABLED,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_IS_RAINING_SENSOR,
    CONF_WEATHER_IS_WINDY_SENSOR,
    CONF_WEATHER_RAIN_SENSOR,
    CONF_WEATHER_RAIN_THRESHOLD,
    CONF_WEATHER_SEVERE_SENSORS,
    CONF_WEATHER_TIMEOUT,
    CONF_WEATHER_WIND_DIRECTION_SENSOR,
    CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    CONF_WEATHER_WIND_SPEED_THRESHOLD,
    CUSTOM_POSITION_SLOTS,
    DEFAULT_CLOUD_COVERAGE_THRESHOLD,
    DEFAULT_DELTA_POSITION,
    DEFAULT_DELTA_TIME,
    DEFAULT_MANUAL_OVERRIDE_DURATION,
    DEFAULT_MOTION_TIMEOUT,
    DEFAULT_WEATHER_ENABLED,
    DEFAULT_WEATHER_RAIN_THRESHOLD,
    DEFAULT_WEATHER_TIMEOUT,
    DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE,
    DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
)
from .cover_types import get_policy
from .helpers import (
    custom_position_slot_configured,
    is_template_string,
    motion_entities,
)
from .profile_link import classify_profile_sensor_source

_NONE = "—"
_NOT_SET = "(not set)"
# A local override that intentionally clears/disables a sensor the profile sets.
_NONE_LOCAL = "(none)"

# English labels for the markdown body. Keyed by dotted name so a future
# acp-translate pass can lift the whole block into summary_i18n unchanged.
_LABELS: dict[str, str] = {
    "title": "**Building Profile — Overview**",
    "linked_count": "{n} cover(s) linked to this profile.",
    "no_covers": (
        "No covers are linked to this profile yet. Link a cover from its own "
        "options (**Building Profile** step) to share these sensors."
    ),
    "shared_header": "**Shared sensors**",
    "shared_hint": "Sensors this profile defines and shares with linked covers.",
    "shared_none": "No shared sensors defined on this profile.",
    "override_note": (
        "⚠ {cover} — local override of **{label}**: `{local}` "
        "(profile: `{profile}`)."
    ),
    "local_note": ("ℹ {cover} — local **{label}**: `{local}` (profile: not set)."),
    "roster_header": "**Linked covers**",
    "matrix_header": "**Settings comparison** (differences only)",
    "matrix_all_same": "All comparable settings are identical across the {n} covers.",
    "identical_header": "**Identical across all covers**",
    # Inherit/override breakdown shown on a linked cover's sensor steps.
    "inherit_header": (
        'Inherited from profile "{title}" — change a field to set a local '
        "override, or reset from the profile's **Local Overrides** step:"
    ),
    "inherit_from_profile": "- {label}: `{value}` (from profile)",
    "inherit_overridden": "- {label}: `{value}` — overridden (profile: `{profile}`)",
    "inherit_local": "- {label}: `{value}` (profile not set — local)",
}

# Friendly labels for the shared-sensor keys. Falls back to a humanized key.
_SENSOR_LABELS: dict[str, str] = {
    CONF_WEATHER_ENTITY: "Weather entity",
    CONF_OUTSIDETEMP_ENTITY: "Outside temperature",
    CONF_LUX_ENTITY: "Illuminance (lux)",
    CONF_IRRADIANCE_ENTITY: "Irradiance",
    CONF_CLOUD_COVERAGE_ENTITY: "Cloud coverage",
    CONF_IS_SUNNY_SENSOR: "Is-sunny sensor",
    CONF_IS_SUNNY_TEMPLATE: "Is-sunny template",
    CONF_WEATHER_WIND_SPEED_SENSOR: "Wind speed",
    CONF_WEATHER_WIND_DIRECTION_SENSOR: "Wind direction",
    CONF_WEATHER_RAIN_SENSOR: "Rain rate",
    CONF_WEATHER_IS_RAINING_SENSOR: "Is-raining sensor",
    CONF_WEATHER_IS_WINDY_SENSOR: "Is-windy sensor",
    CONF_WEATHER_SEVERE_SENSORS: "Severe-weather sensors",
    CONF_DAYTIME_GATE_SENSORS: "Daytime gate sensors",
    CONF_DAYTIME_GATE_TEMPLATE: "Daytime gate template",
    CONF_SUNSET_TIME_ENTITY: "Sunset time entity",
    CONF_SUNRISE_TIME_ENTITY: "Sunrise time entity",
}

# Shared-sensor keys shown in the "Shared sensors" listing, in display order.
# The five *_template_mode combine-mode keys live in BUILDING_PROFILE_SENSOR_KEYS
# but are toggles, not sensors — they are excluded from these views.
_SHARED_DISPLAY_KEYS: tuple[str, ...] = (
    CONF_WEATHER_ENTITY,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_LUX_ENTITY,
    CONF_IRRADIANCE_ENTITY,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_IS_SUNNY_SENSOR,
    CONF_IS_SUNNY_TEMPLATE,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    CONF_WEATHER_WIND_DIRECTION_SENSOR,
    CONF_WEATHER_RAIN_SENSOR,
    CONF_WEATHER_IS_RAINING_SENSOR,
    CONF_WEATHER_IS_WINDY_SENSOR,
    CONF_WEATHER_SEVERE_SENSORS,
    CONF_DAYTIME_GATE_SENSORS,
    CONF_DAYTIME_GATE_TEMPLATE,
    CONF_SUNSET_TIME_ENTITY,
    CONF_SUNRISE_TIME_ENTITY,
)


def _is_set(value: Any) -> bool:
    return value not in (None, "", [])


def _sensor_label(key: str) -> str:
    return _SENSOR_LABELS.get(key) or key.replace("_", " ").capitalize()


def _entity_repr(value: Any) -> str:
    """Render a sensor value (entity id, template, or list) for display."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else _NONE
    if is_template_string(value):
        return "[template]"
    return str(value) if _is_set(value) else _NONE


def _local_override_repr(value: Any) -> str:
    """Render a cover's local override value; an unset/cleared value reads "(none)"."""
    return _entity_repr(value) if _is_set(value) else _NONE_LOCAL


# ---------------------------------------------------------------------------
# Override enumeration (shared by the overview notes + Local Overrides step)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OverrideRecord:
    """One linked cover's local override of a shared sensor key."""

    entry_id: str
    cover_name: str
    key: str
    label: str
    local_text: str
    profile_text: str
    profile_sets_it: bool


def _iter_overrides(
    profile_options: Mapping, cover_options: Mapping
) -> list[tuple[str, str, Any, Any]]:
    """Yield ``(key, source, local_value, profile_value)`` for a cover's overrides.

    Only keys the cover diverges on: ``"override"`` (profile sets it, cover
    overrides) and ``"local"`` with a non-empty cover value (profile leaves it
    blank, cover sets its own). Inherited and fully-empty keys are skipped.
    """
    out: list[tuple[str, str, Any, Any]] = []
    for key in sorted(BUILDING_PROFILE_SENSOR_KEYS):
        source, _ = classify_profile_sensor_source(key, cover_options, profile_options)
        local = cover_options.get(key)
        profile = profile_options.get(key)
        if source == "override":
            out.append((key, source, local, profile))
        elif source == "local" and _is_set(local):
            out.append((key, source, local, profile))
    return out


def build_override_records(
    profile_entry: ConfigEntry, linked_cover_entries: Iterable[ConfigEntry]
) -> list[OverrideRecord]:
    """Build override records across every cover linked to a profile."""
    profile_options = profile_entry.options or {}
    records: list[OverrideRecord] = []
    for entry in linked_cover_entries:
        options = entry.options or {}
        name = entry.title or (entry.data or {}).get("name") or "Cover"
        for key, source, local, profile in _iter_overrides(profile_options, options):
            records.append(
                OverrideRecord(
                    entry_id=entry.entry_id,
                    cover_name=name,
                    key=key,
                    label=_sensor_label(key),
                    local_text=_local_override_repr(local),
                    profile_text=_entity_repr(profile),
                    profile_sets_it=source == "override",
                )
            )
    return records


def profile_value_breakdown(
    profile_options: Mapping,
    cover_options: Mapping,
    keys: Iterable[str],
    profile_title: str = "",
) -> str:
    """Markdown breakdown of the profile's value per profile-owned key on a step.

    For each sensor key (combine-mode toggles excluded), shows whether the
    profile assigns a value and the cover's inherit/override status. Empty when
    no key on the step has a profile value or a local value.
    """
    keys = [
        k
        for k in keys
        if k in BUILDING_PROFILE_SENSOR_KEYS and not k.endswith("_template_mode")
    ]
    lines: list[str] = []
    for key in sorted(keys):
        source, _ = classify_profile_sensor_source(key, cover_options, profile_options)
        profile = profile_options.get(key)
        local = cover_options.get(key)
        label = _sensor_label(key)
        if source == "profile":
            lines.append(
                _LABELS["inherit_from_profile"].format(
                    label=label, value=_entity_repr(profile)
                )
            )
        elif source == "override":
            lines.append(
                _LABELS["inherit_overridden"].format(
                    label=label,
                    value=_local_override_repr(local),
                    profile=_entity_repr(profile),
                )
            )
        elif _is_set(local):
            lines.append(
                _LABELS["inherit_local"].format(label=label, value=_entity_repr(local))
            )
    if not lines:
        return ""
    return "\n".join([_LABELS["inherit_header"].format(title=profile_title), *lines])


# ---------------------------------------------------------------------------
# Read-only profile Overview
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CoverRecord:
    """A linked cover's identity + options, decoupled from ConfigEntry for tests."""

    name: str
    sensor_type: str | None
    options: dict
    entities: list[str] = field(default_factory=list)

    @classmethod
    def from_entry(cls, entry: ConfigEntry) -> _CoverRecord:
        data = entry.data or {}
        options = dict(entry.options or {})
        return cls(
            name=entry.title or data.get("name") or "Cover",
            sensor_type=data.get(CONF_SENSOR_TYPE),
            options=options,
            entities=list(options.get(CONF_ENTITIES, []) or []),
        )


@dataclass(frozen=True)
class _DiffSpec:
    """One comparable behavioral setting: a label and a per-cover formatter.

    The formatter must resolve an unset option to its default so an explicit
    default and an unset value compare equal (differences-only comparison).
    """

    label: str
    extract: Callable[[_CoverRecord], str]


def _eff(options: Mapping, key: str, default: Any) -> Any:
    """Effective value: the option, or ``default`` when unset (None/""/[])."""
    value = options.get(key)
    return default if not _is_set(value) else value


def _num(value: Any) -> str:
    """Render a number by magnitude so an int and an equal float match.

    HA number selectors store values as floats, while ``DEFAULT_*`` constants are
    often ints — without this, ``75.0`` and ``75`` would compare/display as
    different. Integer-valued floats drop the ``.0``; real fractions are kept.
    """
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _fmt(value: Any, suffix: str = "") -> str:
    if not _is_set(value):
        return _NONE
    return f"{_num(value)}{suffix}"


def _onoff(value: Any) -> str:
    return "on" if value else "off"


def _fmt_duration(value: Any) -> str:
    if not isinstance(value, dict):
        return _fmt(value)
    parts = [
        f"{value[u]}{u[0]}" for u in ("hours", "minutes", "seconds") if value.get(u)
    ]
    return " ".join(parts) if parts else _NONE


def _count_custom_slots(options: Mapping) -> int:
    return sum(
        custom_position_slot_configured(options, slot_keys)
        for slot_keys in CUSTOM_POSITION_SLOTS.values()
    )


# Behavioral comparison specs only — automation, climate, weather, motion, and
# time-window thresholds. Physical/geometry settings (cover type, azimuth, FOV,
# window dimensions) are expected to differ per window and are deliberately
# excluded; sun-elevation tracking limits are behavioral and are included.
_COMPARISON_SPECS: tuple[_DiffSpec, ...] = (
    _DiffSpec(
        "Climate mode", lambda r: _onoff(_eff(r.options, CONF_CLIMATE_MODE, False))
    ),
    _DiffSpec(
        "Lux threshold", lambda r: _fmt(_eff(r.options, CONF_LUX_THRESHOLD, None))
    ),
    _DiffSpec(
        "Irradiance threshold",
        lambda r: _fmt(_eff(r.options, CONF_IRRADIANCE_THRESHOLD, None)),
    ),
    _DiffSpec(
        "Cloud coverage threshold",
        lambda r: _fmt(
            _eff(
                r.options,
                CONF_CLOUD_COVERAGE_THRESHOLD,
                DEFAULT_CLOUD_COVERAGE_THRESHOLD,
            ),
            "%",
        ),
    ),
    _DiffSpec(
        "Position limits",
        lambda r: f"{_fmt(_eff(r.options, CONF_MIN_POSITION, None))}"
        f"–{_fmt(_eff(r.options, CONF_MAX_POSITION, None))}",
    ),
    _DiffSpec(
        "Default position", lambda r: _fmt(_eff(r.options, CONF_DEFAULT_HEIGHT, None))
    ),
    _DiffSpec(
        "Sunset position", lambda r: _fmt(_eff(r.options, CONF_SUNSET_POS, None))
    ),
    _DiffSpec(
        "Custom positions", lambda r: f"{_count_custom_slots(r.options)} slot(s)"
    ),
    _DiffSpec(
        "Glare zones",
        lambda r: "enabled" if r.options.get(CONF_ENABLE_GLARE_ZONES) else "off",
    ),
    _DiffSpec(
        "Motion",
        lambda r: (
            f"enabled / {_fmt(_eff(r.options, CONF_MOTION_TIMEOUT, DEFAULT_MOTION_TIMEOUT), 's')}"
            if motion_entities(r.options)
            else "off"
        ),
    ),
    _DiffSpec(
        "Manual override",
        lambda r: f"{_fmt_duration(_eff(r.options, CONF_MANUAL_OVERRIDE_DURATION, DEFAULT_MANUAL_OVERRIDE_DURATION))}"
        f" / {_fmt(_eff(r.options, CONF_MANUAL_THRESHOLD, None))}%",
    ),
    _DiffSpec(
        "Delta position / time",
        lambda r: f"{_num(_eff(r.options, CONF_DELTA_POSITION, DEFAULT_DELTA_POSITION))}%"
        f" / {_num(_eff(r.options, CONF_DELTA_TIME, DEFAULT_DELTA_TIME))} min",
    ),
    _DiffSpec(
        "Sun elevation range",
        lambda r: f"{_fmt(_eff(r.options, CONF_MIN_ELEVATION, None))}"
        f"–{_fmt(_eff(r.options, CONF_MAX_ELEVATION, None))}",
    ),
    _DiffSpec(
        "Active time window",
        lambda r: f"{_fmt(_eff(r.options, CONF_START_TIME, None))}"
        f"–{_fmt(_eff(r.options, CONF_END_TIME, None))}",
    ),
    _DiffSpec(
        "Indoor temp range",
        lambda r: f"{_fmt(_eff(r.options, CONF_TEMP_LOW, None))}"
        f"–{_fmt(_eff(r.options, CONF_TEMP_HIGH, None))}",
    ),
    _DiffSpec(
        "Outdoor temp threshold",
        lambda r: _fmt(_eff(r.options, CONF_OUTSIDE_THRESHOLD, None)),
    ),
    _DiffSpec(
        "Cloud suppression",
        lambda r: (
            f"on / {_fmt(_eff(r.options, CONF_CLOUDY_POSITION, None))}"
            if r.options.get(CONF_CLOUD_SUPPRESSION)
            else "off"
        ),
    ),
    _DiffSpec(
        "Weather protection",
        lambda r: _onoff(
            _eff(r.options, CONF_WEATHER_ENABLED, DEFAULT_WEATHER_ENABLED)
        ),
    ),
    _DiffSpec(
        "Wind speed threshold",
        lambda r: _fmt(
            _eff(
                r.options,
                CONF_WEATHER_WIND_SPEED_THRESHOLD,
                DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
            )
        ),
    ),
    _DiffSpec(
        "Wind direction tolerance",
        lambda r: _fmt(
            _eff(
                r.options,
                CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
                DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE,
            ),
            "°",
        ),
    ),
    _DiffSpec(
        "Rain threshold",
        lambda r: _fmt(
            _eff(
                r.options,
                CONF_WEATHER_RAIN_THRESHOLD,
                DEFAULT_WEATHER_RAIN_THRESHOLD,
            )
        ),
    ),
    _DiffSpec(
        "Weather resume delay",
        lambda r: _fmt(
            _eff(r.options, CONF_WEATHER_TIMEOUT, DEFAULT_WEATHER_TIMEOUT), "s"
        ),
    ),
)


def build_building_overview(
    profile_entry: ConfigEntry,
    linked_cover_entries: list[ConfigEntry],
    hass: HomeAssistant | None = None,  # noqa: ARG001 — reserved for future live state
) -> str:
    """Build the markdown overview for one Building Profile and its linked covers."""
    profile_options = dict(profile_entry.options or {})
    records = [_CoverRecord.from_entry(e) for e in linked_cover_entries]

    blocks: list[str] = [_LABELS["title"]]
    if not records:
        blocks.append(_LABELS["no_covers"])
        blocks.append("\n".join(_build_shared_sensors_section(profile_options, [])))
        return "\n\n".join(blocks)

    blocks.append(_LABELS["linked_count"].format(n=len(records)))
    blocks.append("\n".join(_build_shared_sensors_section(profile_options, records)))
    blocks.append("\n".join(_build_linked_covers_section(records)))
    blocks.append("\n".join(_build_comparison_section(records)))
    return "\n\n".join(blocks)


def _build_shared_sensors_section(
    profile_options: dict, records: list[_CoverRecord]
) -> list[str]:
    lines = [_LABELS["shared_header"], "", _LABELS["shared_hint"], ""]
    defined = [k for k in _SHARED_DISPLAY_KEYS if _is_set(profile_options.get(k))]
    if defined:
        for key in defined:
            lines.append(
                f"- {_sensor_label(key)}: {_entity_repr(profile_options[key])}"
            )
    else:
        lines.append(_LABELS["shared_none"])

    notes = _override_notes(profile_options, records)
    if notes:
        lines.append("")
        lines.extend(notes)
    return lines


def _override_notes(profile_options: dict, records: list[_CoverRecord]) -> list[str]:
    """Flag each linked cover's local overrides / local sensors (no jargon)."""
    notes: list[str] = []
    for record in records:
        for key, source, local, profile in _iter_overrides(
            profile_options, record.options
        ):
            if source == "override":
                notes.append(
                    _LABELS["override_note"].format(
                        cover=record.name,
                        label=_sensor_label(key),
                        local=_local_override_repr(local),
                        profile=_entity_repr(profile),
                    )
                )
            else:
                notes.append(
                    _LABELS["local_note"].format(
                        cover=record.name,
                        label=_sensor_label(key),
                        local=_entity_repr(local),
                    )
                )
    return notes


def _build_linked_covers_section(records: list[_CoverRecord]) -> list[str]:
    lines = [_LABELS["roster_header"], ""]
    for record in records:
        type_label = (
            get_policy(record.sensor_type).display_label()
            if record.sensor_type
            else _NONE
        )
        entities = ", ".join(record.entities) if record.entities else _NONE
        lines.append(f"- **{record.name}** — {type_label} — {entities}")
    return lines


def _build_comparison_section(records: list[_CoverRecord]) -> list[str]:
    differing: list[tuple[_DiffSpec, list[str]]] = []
    identical: list[tuple[str, str]] = []
    for spec in _COMPARISON_SPECS:
        values = [spec.extract(r) for r in records]
        if len(set(values)) > 1:
            differing.append((spec, values))
        else:
            identical.append((spec.label, values[0]))

    lines = [_LABELS["matrix_header"], ""]
    if not differing:
        lines.append(_LABELS["matrix_all_same"].format(n=len(records)))
    else:
        lines += [_format_diff_line(spec.label, records, v) for spec, v in differing]

    if identical:
        lines += ["", _LABELS["identical_header"]]
        lines += [f"- {label}: `{value}`" for label, value in identical]
    return lines


def _format_diff_line(
    label: str, records: list[_CoverRecord], values: list[str]
) -> str:
    """One differing setting: a majority value plus the covers that deviate.

    When a strict majority of covers share one value, name only the exceptions;
    otherwise (even split / all distinct) list every cover's value.
    """
    top_value, top_count = Counter(values).most_common(1)[0]
    if top_count * 2 > len(records):
        exceptions = ", ".join(
            f"**{r.name}** `{v}`" for r, v in zip(records, values) if v != top_value
        )
        return f"- {label} — most `{top_value}`, except {exceptions}"
    parts = " · ".join(f"**{r.name}** `{v}`" for r, v in zip(records, values))
    return f"- {label} — {parts}"
