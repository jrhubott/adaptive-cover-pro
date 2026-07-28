"""Pure-function tests for the Building Profile overview / override views.

These read only ``entry.data`` / ``entry.options`` (never live HA state), so they
drive the builders with lightweight stub entries — no ``hass`` required.
"""

from __future__ import annotations

from custom_components.adaptive_cover_pro.building_overview import (
    build_building_overview,
    build_override_records,
    profile_value_breakdown,
)
from custom_components.adaptive_cover_pro.const import (
    CONF_CLIMATE_MODE,
    CONF_CLOUD_COVERAGE_THRESHOLD,
    CONF_DELTA_POSITION,
    CONF_ENTITIES,
    CONF_LUX_ENTITY,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PROFILE_SENSOR_OVERRIDES,
    CONF_SENSOR_TYPE,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    CONF_WEATHER_WIND_SPEED_THRESHOLD,
    DEFAULT_DELTA_POSITION,
    CoverType,
)


class _Entry:
    """Minimal ConfigEntry stand-in: entry_id + title + data + options."""

    def __init__(self, title, data, options, entry_id=None):
        self.title = title
        self.data = data
        self.options = options
        self.entry_id = entry_id or title.lower().replace(" ", "_")


def _profile(options=None):
    return _Entry(
        "My Building",
        {"name": "My Building", CONF_SENSOR_TYPE: CoverType.BUILDING_PROFILE},
        options or {},
        entry_id="profile_1",
    )


def _cover(name, sensor_type=CoverType.BLIND, options=None):
    opts = {CONF_ENTITIES: [f"cover.{name.lower()}"]}
    opts.update(options or {})
    return _Entry(name, {"name": name, CONF_SENSOR_TYPE: sensor_type}, opts)


def test_zero_linked_covers_renders_hint():
    text = build_building_overview(_profile({CONF_WEATHER_ENTITY: "weather.home"}), [])
    assert "No covers are linked to this profile yet" in text
    assert "Shared sensors" in text
    assert "weather.home" in text


def test_shared_sensors_only_lists_defined():
    profile = _profile(
        {CONF_WEATHER_ENTITY: "weather.home", CONF_OUTSIDETEMP_ENTITY: "sensor.out"}
    )
    cover = _cover("Living", options={CONF_WEATHER_ENTITY: "weather.home"})
    text = build_building_overview(profile, [cover])
    assert "Weather entity: weather.home" in text
    assert "Outside temperature: sensor.out" in text
    # Sensors the profile does NOT define are omitted entirely (no "(not set)").
    assert "(not set)" not in text
    assert "Illuminance (lux):" not in text


def test_shared_sensors_none_defined():
    profile = _profile({})
    cover = _cover("Living")
    text = build_building_overview(profile, [cover])
    assert "No shared sensors defined on this profile." in text


def test_local_override_note():
    """A cover that overrides a profile-defined sensor is flagged (no jargon)."""
    profile = _profile({CONF_WEATHER_ENTITY: "weather.home"})
    cover = _cover(
        "Bedroom",
        options={
            CONF_WEATHER_ENTITY: "weather.upstairs",
            CONF_PROFILE_SENSOR_OVERRIDES: [CONF_WEATHER_ENTITY],
        },
    )
    text = build_building_overview(profile, [cover])
    assert "local override of **Weather entity**" in text
    assert "weather.upstairs" in text
    assert "weather.home" in text
    assert "Q2" not in text


def test_local_sensor_note():
    """Profile blank but cover has its own value → a 'Local sensor' note."""
    profile = _profile({})
    cover = _cover("Office", options={CONF_LUX_ENTITY: "sensor.office_lux"})
    text = build_building_overview(profile, [cover])
    assert "local **Illuminance (lux)**" in text
    assert "sensor.office_lux" in text
    assert "profile: not set" in text


def test_fully_inherited_cover_has_no_notes():
    profile = _profile(
        {
            CONF_WEATHER_ENTITY: "weather.home",
            CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind",
        }
    )
    cover = _cover(
        "Living",
        options={
            CONF_WEATHER_ENTITY: "weather.home",
            CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind",
        },
    )
    text = build_building_overview(profile, [cover])
    assert "⚠" not in text
    assert "ℹ" not in text


def test_roster_lists_type_label_and_entities():
    profile = _profile({})
    covers = [_cover("Living", CoverType.BLIND), _cover("Patio", CoverType.AWNING)]
    text = build_building_overview(profile, covers)
    assert "**Living** — Vertical Blind — cover.living" in text
    assert "**Patio** — Horizontal Awning — cover.patio" in text


def test_comparison_shows_differences_and_identicals():
    profile = _profile({})
    living = _cover("Living", options={CONF_CLIMATE_MODE: True})
    bedroom = _cover("Bedroom", options={CONF_CLIMATE_MODE: False})
    text = build_building_overview(profile, [living, bedroom])
    lines = text.splitlines()
    # Differences render as a per-setting line (no wide table) naming each cover.
    assert "| Setting |" not in text
    climate_row = next(line for line in lines if "Climate mode" in line)
    assert "Living" in climate_row and "on" in climate_row
    assert "Bedroom" in climate_row and "off" in climate_row
    # Behavioral-only: physical settings are never compared.
    assert "azimuth" not in text.lower()
    assert "Field of view" not in text
    assert "Geometry" not in text
    # Identical settings are now listed explicitly, not collapsed into a count.
    assert "Identical across all covers" in text
    assert "Delta position / time" in text


def test_comparison_all_identical():
    profile = _profile({})
    covers = [_cover("A"), _cover("B")]
    text = build_building_overview(profile, covers)
    assert "All comparable settings are identical" in text


def test_unset_equals_default_in_comparison():
    """A cover at the explicit default and an unset cover do not show as different."""
    profile = _profile({})
    a = _cover("A", options={CONF_DELTA_POSITION: DEFAULT_DELTA_POSITION})
    b = _cover("B")  # delta_position unset → normalizes to the same default
    text = build_building_overview(profile, [a, b])
    assert "All comparable settings are identical" in text
    # Delta position is identical (not a difference) → it appears as a list entry
    # under the identical section, never as a "most …, except …" difference line.
    delta_line = next(line for line in text.splitlines() if "Delta position" in line)
    assert delta_line.startswith("- Delta position")


def test_int_and_equal_float_compare_identical():
    """75.0 (HA selector float) and 75 (int default) are the same magnitude."""
    profile = _profile({})
    # Float-stored value, int-stored value, and an unset cover (uses int default).
    a = _cover("A", options={CONF_CLOUD_COVERAGE_THRESHOLD: 75.0})
    b = _cover("B", options={CONF_CLOUD_COVERAGE_THRESHOLD: 75})
    c = _cover("C")  # unset → default 75
    text = build_building_overview(profile, [a, b, c])
    # Not a difference → no "most …, except …" line for cloud coverage.
    cloud_line = next(
        line for line in text.splitlines() if "Cloud coverage threshold" in line
    )
    assert cloud_line.startswith("- Cloud coverage threshold")
    # The ".0" never leaks into the rendered value.
    assert "75.0" not in text


def test_wind_speed_threshold_is_compared():
    """The weather wind-speed threshold now appears in the comparison."""
    profile = _profile({})
    breezy = _cover("Breezy", options={CONF_WEATHER_WIND_SPEED_THRESHOLD: 30})
    calm = _cover("Calm", options={CONF_WEATHER_WIND_SPEED_THRESHOLD: 60})
    text = build_building_overview(profile, [breezy, calm])
    wind_row = next(
        line for line in text.splitlines() if "Wind speed threshold" in line
    )
    assert "Breezy" in wind_row and "30" in wind_row
    assert "Calm" in wind_row and "60" in wind_row


def test_custom_positions_compared_as_count():
    profile = _profile({})
    living = _cover(
        "Living",
        options={
            "custom_position_sensors_1": ["binary_sensor.x"],
            "custom_position_1": 50,
        },
    )
    bedroom = _cover("Bedroom")
    text = build_building_overview(profile, [living, bedroom])
    custom_row = next(line for line in text.splitlines() if "Custom positions" in line)
    assert "1 slot(s)" in custom_row
    assert "0 slot(s)" in custom_row


def test_many_covers_render_without_table():
    profile = _profile({})
    covers = [
        _cover(f"Cover{i}", options={CONF_CLIMATE_MODE: i % 2 == 0}) for i in range(6)
    ]
    text = build_building_overview(profile, covers)
    assert "| Setting |" not in text  # no wide table, even with many covers
    # Even split → every cover named on the single per-setting line.
    climate_row = next(line for line in text.splitlines() if "Climate mode" in line)
    assert "**Cover0**" in climate_row and "**Cover1**" in climate_row
    assert "on" in climate_row and "off" in climate_row


def test_profile_value_breakdown_statuses():
    profile = {
        CONF_WEATHER_ENTITY: "weather.home",
        CONF_OUTSIDETEMP_ENTITY: "sensor.out",
    }
    cover = {
        CONF_WEATHER_ENTITY: "weather.upstairs",
        CONF_PROFILE_SENSOR_OVERRIDES: [CONF_WEATHER_ENTITY],
        CONF_OUTSIDETEMP_ENTITY: "sensor.out",
        CONF_LUX_ENTITY: "sensor.bed_lux",
    }
    out = profile_value_breakdown(
        profile,
        cover,
        [CONF_WEATHER_ENTITY, CONF_OUTSIDETEMP_ENTITY, CONF_LUX_ENTITY],
        profile_title="My Building",
    )
    assert 'profile "My Building"' in out
    assert (
        "Weather entity: `weather.upstairs` — overridden (profile: `weather.home`)"
        in out
    )
    assert "Outside temperature: `sensor.out` (from profile)" in out
    assert "Illuminance (lux): `sensor.bed_lux` (profile not set — local)" in out


def test_profile_value_breakdown_empty_when_nothing_relevant():
    out = profile_value_breakdown({}, {}, [CONF_WEATHER_ENTITY], profile_title="X")
    assert out == ""


def test_build_override_records():
    profile = _profile({CONF_WEATHER_ENTITY: "weather.home"})
    bedroom = _cover(
        "Bedroom",
        options={
            CONF_WEATHER_ENTITY: "weather.upstairs",
            CONF_PROFILE_SENSOR_OVERRIDES: [CONF_WEATHER_ENTITY],
            CONF_LUX_ENTITY: "sensor.bed_lux",
        },
    )
    records = build_override_records(profile, [bedroom])
    by_key = {r.key: r for r in records}
    assert by_key[CONF_WEATHER_ENTITY].profile_sets_it is True
    assert by_key[CONF_WEATHER_ENTITY].local_text == "weather.upstairs"
    assert by_key[CONF_WEATHER_ENTITY].profile_text == "weather.home"
    assert by_key[CONF_LUX_ENTITY].profile_sets_it is False
    assert by_key[CONF_LUX_ENTITY].entry_id == "bedroom"


def test_overridden_empty_value_reads_none():
    """A cover that overrides a profile sensor by clearing it reads "(none)"."""
    profile = _profile({CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind"})
    cover = _cover(
        "Bedroom",
        options={
            CONF_WEATHER_WIND_SPEED_SENSOR: "",
            CONF_PROFILE_SENSOR_OVERRIDES: [CONF_WEATHER_WIND_SPEED_SENSOR],
        },
    )
    record = build_override_records(profile, [cover])[0]
    assert record.profile_sets_it is True
    assert record.local_text == "(none)"
    # The cover's own sensor-step breakdown shows the same wording.
    out = profile_value_breakdown(
        profile.options, cover.options, [CONF_WEATHER_WIND_SPEED_SENSOR]
    )
    assert "Wind speed: `(none)` — overridden (profile: `sensor.wind`)" in out
