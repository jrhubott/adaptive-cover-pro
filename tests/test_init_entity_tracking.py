"""Tests for entity state-change tracking registration in async_setup_entry.

Verifies that all sensor entities that should trigger an immediate pipeline
re-evaluation are registered with async_track_state_change_event.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_DAYTIME_GATE_SENSORS,
    CONF_DAYTIME_GATE_TEMPLATE,
    CONF_END_ENTITY,
    CONF_IRRADIANCE_ENTITY,
    CONF_LUX_ENTITY,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_SENSOR_TYPE,
    CONF_START_ENTITY,
    DOMAIN,
    CoverType,
)
from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

pytestmark = pytest.mark.integration


async def _setup_entry_capture_tracked(
    hass,
    extra_options: dict | None = None,
    entry_id: str = "track_test_01",
) -> tuple[MockConfigEntry, list[list[str]]]:
    """Set up a config entry and capture which entity IDs were tracked."""
    opts = {**VERTICAL_OPTIONS, **(extra_options or {})}

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Track Test", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=opts,
        entry_id=entry_id,
        title="Track Test",
    )
    entry.add_to_hass(hass)

    hass.states.async_set(
        "sun.sun", "above_horizon", {"azimuth": 180.0, "elevation": 45.0}
    )
    hass.states.async_set(
        "cover.test_blind", "open", {"current_position": 100, "supported_features": 143}
    )

    tracked_calls: list[list[str]] = []

    from homeassistant.helpers import event as ha_event

    original = ha_event.async_track_state_change_event

    def _capture(hass_, entity_ids, callback):
        if isinstance(entity_ids, list):
            tracked_calls.append(list(entity_ids))
        return original(hass_, entity_ids, callback)

    with (
        patch(
            "custom_components.adaptive_cover_pro.async_track_state_change_event",
            side_effect=_capture,
        ),
        _patch_coordinator_refresh(),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    return entry, tracked_calls


async def test_sun_always_tracked(hass) -> None:
    """sun.sun is always in the tracked entity list."""
    _, calls = await _setup_entry_capture_tracked(hass)
    all_tracked = [e for call in calls for e in call]
    assert "sun.sun" in all_tracked


async def test_cloud_coverage_entity_tracked(hass) -> None:
    """Cloud coverage entity triggers immediate pipeline refresh when it changes."""
    _, calls = await _setup_entry_capture_tracked(
        hass,
        extra_options={CONF_CLOUD_COVERAGE_ENTITY: "sensor.cloud_coverage"},
        entry_id="track_cloud_01",
    )
    all_tracked = [e for call in calls for e in call]
    assert "sensor.cloud_coverage" in all_tracked, (
        "CONF_CLOUD_COVERAGE_ENTITY must be registered for state-change tracking "
        "so cloud suppression fires immediately when the sensor crosses the threshold."
    )


async def test_lux_entity_tracked(hass) -> None:
    """Lux entity triggers immediate pipeline refresh when it changes."""
    _, calls = await _setup_entry_capture_tracked(
        hass,
        extra_options={CONF_LUX_ENTITY: "sensor.lux"},
        entry_id="track_lux_01",
    )
    all_tracked = [e for call in calls for e in call]
    assert "sensor.lux" in all_tracked, (
        "CONF_LUX_ENTITY must be registered for state-change tracking "
        "so cloud suppression fires immediately when lux drops below threshold."
    )


async def test_irradiance_entity_tracked(hass) -> None:
    """Irradiance entity triggers immediate pipeline refresh when it changes."""
    _, calls = await _setup_entry_capture_tracked(
        hass,
        extra_options={CONF_IRRADIANCE_ENTITY: "sensor.irradiance"},
        entry_id="track_irr_01",
    )
    all_tracked = [e for call in calls for e in call]
    assert "sensor.irradiance" in all_tracked, (
        "CONF_IRRADIANCE_ENTITY must be registered for state-change tracking "
        "so cloud suppression fires immediately when irradiance drops below threshold."
    )


async def test_outside_temp_entity_tracked(hass) -> None:
    """Outside temperature entity triggers immediate pipeline refresh when it changes."""
    _, calls = await _setup_entry_capture_tracked(
        hass,
        extra_options={CONF_OUTSIDETEMP_ENTITY: "sensor.outside_temp"},
        entry_id="track_otemp_01",
    )
    all_tracked = [e for call in calls for e in call]
    assert "sensor.outside_temp" in all_tracked, (
        "CONF_OUTSIDETEMP_ENTITY must be registered for state-change tracking "
        "so climate mode re-evaluates immediately when outside temperature changes."
    )


async def test_unset_climate_entities_not_in_tracked_list(hass) -> None:
    """When climate sensor options are not configured, they are not in the tracked list."""
    _, calls = await _setup_entry_capture_tracked(hass, entry_id="track_none_01")
    all_tracked = [e for call in calls for e in call]
    # None of these should appear when not configured
    for entity_id in [
        "sensor.cloud_coverage",
        "sensor.lux",
        "sensor.irradiance",
        "sensor.outside_temp",
    ]:
        assert entity_id not in all_tracked


async def test_start_entity_tracked(hass) -> None:
    """Start time entity (e.g. sensor.sun_next_rising) triggers pipeline refresh when it rolls over."""
    _, calls = await _setup_entry_capture_tracked(
        hass,
        extra_options={CONF_START_ENTITY: "sensor.sun_next_rising"},
        entry_id="track_start_01",
    )
    all_tracked = [e for call in calls for e in call]
    assert "sensor.sun_next_rising" in all_tracked, (
        "CONF_START_ENTITY must be registered for state-change tracking so the "
        "coordinator refreshes immediately when sensor.sun_next_rising rolls over "
        "to tomorrow after sunrise."
    )


async def test_end_entity_tracked(hass) -> None:
    """End time entity (e.g. sensor.sun_next_setting) triggers pipeline refresh when it rolls over."""
    _, calls = await _setup_entry_capture_tracked(
        hass,
        extra_options={CONF_END_ENTITY: "sensor.sun_next_setting"},
        entry_id="track_end_01",
    )
    all_tracked = [e for call in calls for e in call]
    assert "sensor.sun_next_setting" in all_tracked, (
        "CONF_END_ENTITY must be registered for state-change tracking so the "
        "coordinator refreshes immediately when sensor.sun_next_setting rolls over."
    )


async def test_daytime_gate_sensor_tracked(hass) -> None:
    """Daytime gate sensor triggers immediate reposition when the gate flips dark (issue #632).

    The gate short-circuits the astral sunset/sunrise boundary; when it flips OFF
    (dark) the cover must move to sunset_position immediately rather than waiting
    for the next sun.sun update (~1-2 min). Registering the sensor in the tracked
    entity list gives it the same response time as lux, irradiance, and other
    immediate-reaction sensors.
    """
    _, calls = await _setup_entry_capture_tracked(
        hass,
        extra_options={CONF_DAYTIME_GATE_SENSORS: ["binary_sensor.daytime_gate"]},
        entry_id="track_gate_01",
    )
    all_tracked = [e for call in calls for e in call]
    assert "binary_sensor.daytime_gate" in all_tracked, (
        "CONF_DAYTIME_GATE_SENSORS must be registered for state-change tracking "
        "so the cover reacts the instant the gate flips dark (issue #632)."
    )


async def test_unset_daytime_gate_not_tracked(hass) -> None:
    """When no gate sensors are configured, no gate entity leaks into the tracked list."""
    _, calls = await _setup_entry_capture_tracked(hass, entry_id="track_gate_none_01")
    all_tracked = [e for call in calls for e in call]
    assert (
        "binary_sensor.daytime_gate" not in all_tracked
    ), "Unconfigured gate sensors must not appear in the tracked entity list."


async def test_manual_override_input_entities_tracked(hass) -> None:
    """Configured input sensors are registered for state-change tracking (issue #688).

    An off→on edge on one of these sensors engages manual override, so they must
    have the same immediate-reaction registration as motion/gate sensors.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_MANUAL_OVERRIDE_INPUT_ENTITIES,
    )

    _, calls = await _setup_entry_capture_tracked(
        hass,
        extra_options={
            CONF_MANUAL_OVERRIDE_INPUT_ENTITIES: ["binary_sensor.cover_input_0"]
        },
        entry_id="track_mo_input_01",
    )
    all_tracked = [e for call in calls for e in call]
    assert "binary_sensor.cover_input_0" in all_tracked, (
        "CONF_MANUAL_OVERRIDE_INPUT_ENTITIES must be registered for state-change "
        "tracking so the off→on edge engages manual override immediately (#688)."
    )


async def test_unset_manual_override_input_entities_not_tracked(hass) -> None:
    """With no input sensors configured, none are registered (guarded subscription)."""
    _, calls = await _setup_entry_capture_tracked(
        hass, entry_id="track_mo_input_none_01"
    )
    all_tracked = [e for call in calls for e in call]
    assert "binary_sensor.cover_input_0" not in all_tracked


async def test_daytime_gate_template_registered(hass) -> None:
    """Daytime gate template is registered via async_track_template_result (issue #632).

    When the gate is expressed as a Jinja template instead of (or in addition to)
    a binary sensor, it must be tracked so the cover reacts the instant the
    template changes — same immediacy as the occupancy and weather templates.
    """
    from unittest.mock import patch as mock_patch

    gate_template = "{{ states('sensor.lux') | int > 100 }}"
    opts = {**{}, CONF_DAYTIME_GATE_TEMPLATE: gate_template}

    template_registered_calls: list[str] = []

    from homeassistant.helpers import event as ha_event

    original_template = ha_event.async_track_template_result

    def _capture_template(hass_, track_templates, callback):
        for tt in track_templates:
            template_registered_calls.append(tt.template.template)
        return original_template(hass_, track_templates, callback)

    with (
        mock_patch(
            "custom_components.adaptive_cover_pro.async_track_template_result",
            side_effect=_capture_template,
        ),
        _patch_coordinator_refresh(),
    ):
        _, _ = await _setup_entry_capture_tracked(
            hass,
            extra_options=opts,
            entry_id="track_gate_tmpl_01",
        )

    assert gate_template in template_registered_calls, (
        "CONF_DAYTIME_GATE_TEMPLATE must be registered via async_track_template_result "
        "so the cover reacts the instant the template flips (issue #632)."
    )


async def test_weather_severe_template_registered(hass) -> None:
    """Severe weather template is tracked via async_track_template_result (#974).

    A template-only severe override (no companion binary sensors) must engage
    and react the instant the template flips — same immediacy as the is-raining
    and is-windy weather templates.
    """
    from unittest.mock import patch as mock_patch

    from custom_components.adaptive_cover_pro.const import (
        CONF_WEATHER_SEVERE_TEMPLATE,
    )

    severe_template = "{{ is_state('sensor.storm', 'severe') }}"
    opts = {CONF_WEATHER_SEVERE_TEMPLATE: severe_template}

    template_registered_calls: list[str] = []

    from homeassistant.helpers import event as ha_event

    original_template = ha_event.async_track_template_result

    def _capture_template(hass_, track_templates, callback):
        for tt in track_templates:
            template_registered_calls.append(tt.template.template)
        return original_template(hass_, track_templates, callback)

    with (
        mock_patch(
            "custom_components.adaptive_cover_pro.async_track_template_result",
            side_effect=_capture_template,
        ),
        _patch_coordinator_refresh(),
    ):
        _, _ = await _setup_entry_capture_tracked(
            hass,
            extra_options=opts,
            entry_id="track_severe_tmpl_01",
        )

    assert severe_template in template_registered_calls, (
        "CONF_WEATHER_SEVERE_TEMPLATE must be registered via "
        "async_track_template_result so a template-only severe override reacts "
        "the instant the template flips (issue #974)."
    )


async def test_manual_override_input_template_registered(hass) -> None:
    """Manual-override input template is tracked via async_track_template_result (#974).

    A template flipping truthy must engage manual override the instant it does,
    with no polling — the same immediacy as the input-sensor edge path.
    """
    from unittest.mock import patch as mock_patch

    from custom_components.adaptive_cover_pro.const import (
        CONF_MANUAL_OVERRIDE_INPUT_TEMPLATE,
    )

    input_template = "{{ is_state('input_boolean.cover_touch', 'on') }}"
    opts = {CONF_MANUAL_OVERRIDE_INPUT_TEMPLATE: input_template}

    template_registered_calls: list[str] = []

    from homeassistant.helpers import event as ha_event

    original_template = ha_event.async_track_template_result

    def _capture_template(hass_, track_templates, callback):
        for tt in track_templates:
            template_registered_calls.append(tt.template.template)
        return original_template(hass_, track_templates, callback)

    with (
        mock_patch(
            "custom_components.adaptive_cover_pro.async_track_template_result",
            side_effect=_capture_template,
        ),
        _patch_coordinator_refresh(),
    ):
        _, _ = await _setup_entry_capture_tracked(
            hass,
            extra_options=opts,
            entry_id="track_input_tmpl_01",
        )

    assert input_template in template_registered_calls, (
        "CONF_MANUAL_OVERRIDE_INPUT_TEMPLATE must be registered via "
        "async_track_template_result so the override engages the instant the "
        "template flips truthy (issue #974)."
    )
