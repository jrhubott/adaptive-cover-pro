"""Tracked-template immediacy and ``acp`` context wiring (issue #1159).

``_register_template_tracker`` exists to give a template-only override
sensor-grade immediacy — the cover reacts the instant the template flips, with
no companion binary sensor and no polling (#577/#563/#639/#632/#974). That
property is invisible to every other test in the suite: they assert the
manager-level fold, not the tracker's listener set. This file guards it
directly, for plain templates and for templates written against the ``acp``
namespace, plus the coordinator-side wiring that hands every render site the
same context.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_DAYTIME_GATE_TEMPLATE,
    CONF_MOTION_TEMPLATE,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from tests._helpers.acp_namespace import make_acp_entry, seed_sun_infront
from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

pytestmark = pytest.mark.integration


def _make_entry(hass: HomeAssistant, entry_id: str, options: dict) -> MockConfigEntry:
    entry = make_acp_entry(hass, entry_id, options={**VERTICAL_OPTIONS, **options})
    hass.states.async_set(
        "sun.sun", "above_horizon", {"azimuth": 180.0, "elevation": 45.0}
    )
    hass.states.async_set(
        "cover.test_blind", "open", {"current_position": 100, "supported_features": 143}
    )
    return entry


def _preseed_sun_infront(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """Create the Sun Infront registry row ahead of setup.

    Mirrors the common real-world case (HA restart / options save): the entity
    registry is loaded early and already holds this entry's rows, so the
    namespace resolves at tracker-registration time. The unique_id matches what
    ``binary_sensor.py`` builds, so the real entity adopts this exact row.
    """
    return seed_sun_infront(hass, entry)


async def test_plain_template_tracker_fires_on_dependency_change(
    hass: HomeAssistant,
) -> None:
    """A template over an ordinary entity still reaches the coordinator callback.

    The invariant guard for the whole #1159 change: passing a render context and
    a rate limit to ``TrackTemplate`` must not cost an ordinary template its
    immediacy.
    """
    entry = _make_entry(
        hass,
        "tt_plain_01",
        {CONF_MOTION_TEMPLATE: "{{ is_state('input_boolean.guests', 'on') }}"},
    )
    hass.states.async_set("input_boolean.guests", "off")

    with (
        patch.object(
            AdaptiveDataUpdateCoordinator,
            "async_check_motion_template_change",
            new_callable=AsyncMock,
        ) as fired,
        _patch_coordinator_refresh(),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        fired.reset_mock()
        hass.states.async_set("input_boolean.guests", "on")
        await hass.async_block_till_done()

        assert fired.called, (
            "A plain motion template must still fire its tracked-result callback "
            "when its dependency flips (immediacy contract, #577)."
        )


async def test_acp_namespace_template_tracker_fires_on_own_entity_change(
    hass: HomeAssistant,
) -> None:
    """A namespace self-reference records a real state listener (#1159).

    ``acp.sun_infront`` resolves to an entity_id *string*, so ``is_state()``
    still records the dependency in ``RenderInfo`` — which is the only reason
    the tracker fires at all.
    """
    entry = _make_entry(
        hass,
        "tt_acp_01",
        {CONF_MOTION_TEMPLATE: "{{ is_state(acp.sun_infront, 'on') }}"},
    )
    entity_id = _preseed_sun_infront(hass, entry)
    hass.states.async_set(entity_id, "off")

    with (
        patch.object(
            AdaptiveDataUpdateCoordinator,
            "async_check_motion_template_change",
            new_callable=AsyncMock,
        ) as fired,
        _patch_coordinator_refresh(),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        fired.reset_mock()
        hass.states.async_set(entity_id, "on")
        await hass.async_block_till_done()

        assert fired.called, (
            "A template using the acp namespace must be tracked through the "
            "resolved entity_id, so flipping that entity fires the callback."
        )


async def test_rate_limit_only_on_namespace_templates(hass: HomeAssistant) -> None:
    """Only namespace templates carry the loop-guard rate limit.

    A self-reference can drive its own input and the coordinator has no
    debouncer, so those get a 1 s trailing-render cap. Everything else keeps the
    existing byte-for-byte behaviour: no rate limit at all.
    """
    acp_template = "{{ is_state(acp.sun_infront, 'on') }}"
    plain_template = "{{ states('sensor.lux') | int > 100 }}"
    entry = _make_entry(
        hass,
        "tt_rate_01",
        {
            CONF_MOTION_TEMPLATE: acp_template,
            CONF_DAYTIME_GATE_TEMPLATE: plain_template,
        },
    )
    _preseed_sun_infront(hass, entry)

    captured: dict[str, float | None] = {}

    from homeassistant.helpers import event as ha_event

    original = ha_event.async_track_template_result

    def _capture(hass_, track_templates, action, **kwargs):
        for tt in track_templates:
            captured[tt.template.template] = tt.rate_limit
        return original(hass_, track_templates, action, **kwargs)

    with (
        patch(
            "custom_components.adaptive_cover_pro.async_track_template_result",
            side_effect=_capture,
        ),
        _patch_coordinator_refresh(),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert (
        captured[acp_template] == 1.0
    ), "A template using the acp namespace must carry the loop-guard rate limit."
    assert captured[plain_template] is None, (
        "A template that does not use the acp namespace must keep its unthrottled "
        "immediacy — no rate limit."
    )


async def test_namespace_context_is_passed_to_every_tracker(
    hass: HomeAssistant,
) -> None:
    """Every registration gets the same context, namespace-using or not.

    One context for the tracker's render and the manager's cycle render is what
    keeps the two from ever disagreeing.
    """
    plain_template = "{{ states('sensor.lux') | int > 100 }}"
    entry = _make_entry(hass, "tt_ctx_01", {CONF_DAYTIME_GATE_TEMPLATE: plain_template})

    captured: dict[str, object] = {}

    from homeassistant.helpers import event as ha_event

    original = ha_event.async_track_template_result

    def _capture(hass_, track_templates, action, **kwargs):
        for tt in track_templates:
            captured[tt.template.template] = tt.variables
        return original(hass_, track_templates, action, **kwargs)

    with (
        patch(
            "custom_components.adaptive_cover_pro.async_track_template_result",
            side_effect=_capture,
        ),
        _patch_coordinator_refresh(),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert set(captured[plain_template]) == {"acp", "acp_entity"}, (
        "Tracked templates get the entity forms only — never acp_state, whose "
        "value reads are invisible to RenderInfo."
    )


async def test_coordinator_threads_one_context_to_every_render_site(
    hass: HomeAssistant,
) -> None:
    """Every collaborator that renders an option template shares the coordinator's context.

    The no-duplication guard for #1159: one factory, one context per flavour, no
    render site building its own. ``acp_state`` reaches only the untracked
    numeric resolver.
    """
    entry = _make_entry(hass, "tt_wiring_01", {})

    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    entity_ctx = coordinator._template_variables
    assert set(entity_ctx) == {"acp", "acp_entity"}
    assert set(coordinator._template_variables_with_state) == {
        "acp",
        "acp_entity",
        "acp_state",
    }

    for owner, attr in (
        (coordinator._motion_mgr, "_template_variables"),
        (coordinator._weather_mgr, "_template_variables"),
        (coordinator._time_mgr, "_template_variables"),
        (coordinator._climate_provider, "_template_variables"),
        (coordinator._snapshot_builder, "_template_variables"),
    ):
        assert getattr(owner, attr) is entity_ctx, (
            f"{type(owner).__name__} must render against the coordinator's one "
            "entity-form context, not its own."
        )

    assert (
        coordinator._template_resolver._variables
        is coordinator._template_variables_with_state
    ), "The numeric resolver is the one render site that also gets acp_state."
