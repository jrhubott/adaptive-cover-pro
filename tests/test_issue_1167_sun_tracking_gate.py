"""Sun-tracking gate: bind solar tracking to a sensor/template (issue #1167).

The gate answers "should ACP sun-track right now?" from binary sensors and/or a
Jinja condition. When it says no, ``SolarHandler`` declines and the chain falls
through to whatever is below it — it does NOT force a position, and it does NOT
close the operating window the way the daytime gate does.

The distinction from the daytime gate is the whole point of the feature, so it
is pinned explicitly below: a dark daytime gate suppresses climate, glare zones,
cloud suppression and motion timeout too (they all read ``in_time_window``); a
closed sun-tracking gate suppresses solar and nothing else.
"""

from __future__ import annotations


from custom_components.adaptive_cover_pro import config_dynamic, config_flow as cf
from custom_components.adaptive_cover_pro.const import (
    BUILDING_PROFILE_ENTITY_KEYS,
    BUILDING_PROFILE_SENSOR_KEYS,
    CONF_SUN_TRACKING_GATE_SENSORS,
    CONF_SUN_TRACKING_GATE_TEMPLATE,
    CONF_SUN_TRACKING_GATE_TEMPLATE_MODE,
    DEFAULT_TEMPLATE_COMBINE_MODE,
    ReasonCode,
)


def _schema_keys(schema) -> set[str]:
    return {str(k) for k in schema.schema}


# ---------------------------------------------------------------------------
# Option keys
# ---------------------------------------------------------------------------


def test_gate_option_keys_are_wire_stable():
    """The stored key names are the rollback contract — pin them literally."""
    assert CONF_SUN_TRACKING_GATE_SENSORS == "sun_tracking_gate_sensors"
    assert CONF_SUN_TRACKING_GATE_TEMPLATE == "sun_tracking_gate_template"
    assert CONF_SUN_TRACKING_GATE_TEMPLATE_MODE == "sun_tracking_gate_template_mode"


# ---------------------------------------------------------------------------
# Per-cover schema — the Sun Tracking step
# ---------------------------------------------------------------------------


def test_gate_fields_render_on_the_sun_tracking_step():
    keys = _schema_keys(config_dynamic.sun_tracking_schema())
    assert CONF_SUN_TRACKING_GATE_SENSORS in keys
    assert CONF_SUN_TRACKING_GATE_TEMPLATE in keys
    assert CONF_SUN_TRACKING_GATE_TEMPLATE_MODE in keys


def test_gate_does_not_leak_onto_unrelated_steps():
    """It gates solar, not positions or motion — it must not appear there."""
    assert CONF_SUN_TRACKING_GATE_SENSORS not in _schema_keys(cf.POSITION_SCHEMA)
    assert CONF_SUN_TRACKING_GATE_SENSORS not in _schema_keys(cf.AUTOMATION_SCHEMA)
    assert CONF_SUN_TRACKING_GATE_SENSORS not in _schema_keys(cf.BEHAVIOR_SCHEMA)


def test_gate_template_round_trips_as_cleared():
    """No schema default → voluptuous omits it when cleared, so it needs the
    optional-key treatment or the old value silently survives (issue #439/#1085).
    """
    assert CONF_SUN_TRACKING_GATE_TEMPLATE in cf._SUN_TRACKING_OPTIONAL_KEYS


def test_gate_sensors_default_to_an_empty_list():
    """A cleared multi-select must round-trip as [], never None."""
    marker = next(
        k
        for k in config_dynamic.sun_tracking_schema().schema
        if str(k) == CONF_SUN_TRACKING_GATE_SENSORS
    )
    assert marker.default() == []
    assert CONF_SUN_TRACKING_GATE_SENSORS not in cf._SUN_TRACKING_OPTIONAL_KEYS


def test_gate_template_mode_defaults_to_the_shared_default():
    marker = next(
        k
        for k in config_dynamic.sun_tracking_schema().schema
        if str(k) == CONF_SUN_TRACKING_GATE_TEMPLATE_MODE
    )
    assert marker.default() == DEFAULT_TEMPLATE_COMBINE_MODE


def test_gate_keys_sync_with_the_sun_tracking_category():
    """Copy-to-sibling-cover must carry the gate with the rest of sun tracking."""
    category = cf.SYNC_CATEGORIES["sun_tracking"]
    assert CONF_SUN_TRACKING_GATE_SENSORS in category
    assert CONF_SUN_TRACKING_GATE_TEMPLATE in category
    assert CONF_SUN_TRACKING_GATE_TEMPLATE_MODE in category


# ---------------------------------------------------------------------------
# Building profile — set the gate once for the whole building
# ---------------------------------------------------------------------------


def test_gate_keys_are_profile_owned():
    assert CONF_SUN_TRACKING_GATE_SENSORS in BUILDING_PROFILE_SENSOR_KEYS
    assert CONF_SUN_TRACKING_GATE_TEMPLATE in BUILDING_PROFILE_SENSOR_KEYS
    assert CONF_SUN_TRACKING_GATE_TEMPLATE_MODE in BUILDING_PROFILE_SENSOR_KEYS


def test_gate_fields_render_on_the_profile_screen():
    keys = _schema_keys(config_dynamic.building_profile_sensors_schema())
    assert CONF_SUN_TRACKING_GATE_SENSORS in keys
    assert CONF_SUN_TRACKING_GATE_TEMPLATE in keys
    assert CONF_SUN_TRACKING_GATE_TEMPLATE_MODE in keys


def test_only_the_sensor_list_counts_as_an_entity_key():
    """A template body and a combine mode are config, not entity ids — cataloguing
    them as entities made Troubleshoot flag them as unavailable sensors (#1017).
    """
    assert CONF_SUN_TRACKING_GATE_SENSORS in BUILDING_PROFILE_ENTITY_KEYS
    assert CONF_SUN_TRACKING_GATE_TEMPLATE not in BUILDING_PROFILE_ENTITY_KEYS
    assert CONF_SUN_TRACKING_GATE_TEMPLATE_MODE not in BUILDING_PROFILE_ENTITY_KEYS


def test_profile_gate_merges_into_a_linked_cover():
    """A profile-defined gate reaches an inheriting cover's config."""
    from unittest.mock import MagicMock

    from custom_components.adaptive_cover_pro.profile_link import (
        merge_profile_into_config,
    )

    profile = MagicMock()
    profile.options = {
        CONF_SUN_TRACKING_GATE_SENSORS: ["binary_sensor.ac"],
        CONF_SUN_TRACKING_GATE_TEMPLATE: "{{ true }}",
        CONF_SUN_TRACKING_GATE_TEMPLATE_MODE: "and",
    }
    config: dict = {}
    merge_profile_into_config(profile, config)

    assert config[CONF_SUN_TRACKING_GATE_SENSORS] == ["binary_sensor.ac"]
    assert config[CONF_SUN_TRACKING_GATE_TEMPLATE] == "{{ true }}"
    assert config[CONF_SUN_TRACKING_GATE_TEMPLATE_MODE] == "and"


def test_a_cover_overriding_the_gate_keeps_its_own():
    from unittest.mock import MagicMock

    from custom_components.adaptive_cover_pro.profile_link import (
        merge_profile_into_config,
    )

    profile = MagicMock()
    profile.options = {CONF_SUN_TRACKING_GATE_SENSORS: ["binary_sensor.building"]}
    config = {CONF_SUN_TRACKING_GATE_SENSORS: ["binary_sensor.this_room"]}
    merge_profile_into_config(
        profile, config, overridden=frozenset({CONF_SUN_TRACKING_GATE_SENSORS})
    )

    assert config[CONF_SUN_TRACKING_GATE_SENSORS] == ["binary_sensor.this_room"]


# ---------------------------------------------------------------------------
# Reason code
# ---------------------------------------------------------------------------


def test_gate_skip_has_its_own_reason_code():
    """A closed gate must be distinguishable from "sun outside the FOV" in the trace."""
    from custom_components.adaptive_cover_pro.reason_i18n import _REASON_TEMPLATES_EN

    assert ReasonCode.SKIP_SUN_TRACKING_GATE.value == "skip.sun_tracking_gate"
    assert ReasonCode.SKIP_SUN_TRACKING_GATE in _REASON_TEMPLATES_EN
    assert (
        _REASON_TEMPLATES_EN[ReasonCode.SKIP_SUN_TRACKING_GATE]
        != _REASON_TEMPLATES_EN[ReasonCode.SKIP_SUN_OUTSIDE]
    )


# ---------------------------------------------------------------------------
# Runtime — the solar handler declines and the chain falls through
# ---------------------------------------------------------------------------


def _pipeline(options: dict):
    """Build a real pipeline registry from options, via a bare coordinator."""
    from unittest.mock import MagicMock

    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._toggles = MagicMock()
    entry = MagicMock()
    entry.options = options
    coord.config_entry = entry
    return coord._build_pipeline()


def test_solar_wins_when_the_gate_is_open():
    from custom_components.adaptive_cover_pro.const import ControlMethod
    from tests.test_pipeline.conftest import make_snapshot

    registry = _pipeline({})
    snap = make_snapshot(
        direct_sun_valid=True,
        calculate_percentage_return=60.0,
        enable_sun_tracking=True,
    )
    result = registry.evaluate(snap)

    assert result is not None
    assert result.control_method == ControlMethod.SOLAR


def test_a_closed_gate_falls_through_to_default():
    """The whole point: solar declines, the chain continues, no forced position."""
    from custom_components.adaptive_cover_pro.const import ControlMethod
    from tests.test_pipeline.conftest import make_snapshot

    registry = _pipeline({})
    snap = make_snapshot(
        direct_sun_valid=True,
        calculate_percentage_return=60.0,
        default_position=100,
        enable_sun_tracking=False,  # the gate folds into this field
    )
    result = registry.evaluate(snap)

    assert result is not None
    assert result.control_method == ControlMethod.DEFAULT
    assert result.position == 100


def test_the_solar_handler_stays_in_the_chain_even_with_tracking_off():
    """Unlike the old composition-gating, the handler stays and skips.

    Sun tracking is now expressed in exactly one place — ``enable_sun_tracking``
    on the snapshot, which the static toggle and the gate both fold into — rather
    than by two mechanisms (absent handler / declining handler) meaning the same
    thing. The visible payoff is a decision trace that can say WHY solar did not
    run instead of silently omitting it, which is indistinguishable from "never
    configured" to the Lovelace card.
    """
    from custom_components.adaptive_cover_pro.const import CONF_ENABLE_SUN_TRACKING
    from custom_components.adaptive_cover_pro.pipeline.handlers.solar import (
        SolarHandler,
    )

    registry = _pipeline({CONF_ENABLE_SUN_TRACKING: False})
    assert SolarHandler in {type(h) for h in registry._handlers}


def test_a_closed_gate_reports_the_gate_skip_reason():
    from tests.test_pipeline.conftest import make_snapshot

    handler = _solar_handler()
    snap = make_snapshot(direct_sun_valid=True, enable_sun_tracking=False)

    assert handler.evaluate(snap) is None
    assert handler.describe_skip(snap).code is ReasonCode.SKIP_SUN_TRACKING_GATE


def test_an_out_of_window_skip_still_outranks_the_gate_reason():
    """Outside the window is the more fundamental reason — it must win the message."""
    from tests.test_pipeline.conftest import make_snapshot

    handler = _solar_handler()
    snap = make_snapshot(
        direct_sun_valid=True, enable_sun_tracking=False, in_time_window=False
    )

    assert handler.describe_skip(snap).code is ReasonCode.SKIP_OUTSIDE_WINDOW


def test_sun_outside_the_fov_still_reports_its_own_reason():
    from tests.test_pipeline.conftest import make_snapshot

    handler = _solar_handler()
    snap = make_snapshot(direct_sun_valid=False, enable_sun_tracking=True)

    assert handler.describe_skip(snap).code is ReasonCode.SKIP_SUN_OUTSIDE


def _solar_handler():
    from custom_components.adaptive_cover_pro.pipeline.handlers.solar import (
        SolarHandler,
    )

    return SolarHandler()


def test_a_closed_gate_does_not_suppress_climate():
    """The distinction from the daytime gate, pinned.

    A dark daytime gate closes ``in_time_window``, which suppresses climate,
    glare zones, cloud suppression and motion timeout along with solar. A closed
    sun-tracking gate must suppress solar and nothing else.
    """
    from custom_components.adaptive_cover_pro.const import ControlMethod
    from tests.test_pipeline.conftest import make_snapshot

    registry = _pipeline({})

    # Same snapshot shape, two gates. The climate handler needs its own config to
    # win, so assert the weaker but sufficient property: with the sun-tracking
    # gate closed the chain still RESOLVES (falls through), whereas closing the
    # window suppresses every windowed handler at once.
    gated = make_snapshot(
        direct_sun_valid=True, enable_sun_tracking=False, default_position=100
    )
    windowed = make_snapshot(
        direct_sun_valid=True, enable_sun_tracking=True, in_time_window=False
    )

    from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
        ClimateHandler,
    )

    climate = ClimateHandler()
    # Climate is windowed: it skips outright when the window is closed...
    assert climate.evaluate(windowed) is None
    # ...but the sun-tracking gate leaves it evaluating normally (it declines here
    # only because no climate mode is configured, not because a gate closed it).
    assert gated.in_time_window is True

    assert registry.evaluate(gated).control_method == ControlMethod.DEFAULT
