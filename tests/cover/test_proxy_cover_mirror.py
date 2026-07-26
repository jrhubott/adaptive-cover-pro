"""Proxy cover mirroring tests (Phase D Step 14)."""

from __future__ import annotations

import pytest

from homeassistant.components.cover import CoverState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

from custom_components.adaptive_cover_pro.const import (
    CONF_ENABLE_PROXY_COVER,
    CONF_ENTITIES,
    CONF_INTERP,
    CONF_INTERP_LIST,
    CONF_INTERP_LIST_NEW,
    CONF_INVERSE_STATE,
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
)
from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

pytestmark = pytest.mark.integration


async def _setup_single(
    hass,
    *,
    source: str = "cover.living_room",
    entry_id: str = "proxy_mirror_01",
    state: str = "open",
    attrs: dict | None = None,
    extra_options: dict | None = None,
):
    """Set up a single-source proxy and return ``(entry, proxy_entity_id)``."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from homeassistant.helpers import entity_registry as er

    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_ENTITIES] = [source]
    opts[CONF_ENABLE_PROXY_COVER] = True
    if extra_options:
        opts.update(extra_options)

    hass.states.async_set(
        source,
        state,
        attrs or {"current_position": 60, "supported_features": 143},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Mirror Cover", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=opts,
        entry_id=entry_id,
        title="Mirror Cover",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    reg = er.async_get(hass)
    proxy_eid = next(
        e.entity_id
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id
        and e.unique_id.startswith(f"{entry.entry_id}_proxy_")
    )
    return entry, proxy_eid


async def test_proxy_mirrors_source_current_position(hass) -> None:
    """``current_position`` from source state appears on the proxy state."""
    _, proxy_eid = await _setup_single(
        hass,
        attrs={"current_position": 42, "supported_features": 143},
        entry_id="proxy_mirror_pos",
    )
    state = hass.states.get(proxy_eid)
    assert state.attributes.get("current_position") == 42


async def test_proxy_mirrors_source_current_tilt_position(hass) -> None:
    """``current_tilt_position`` from source state appears on the proxy state."""
    _, proxy_eid = await _setup_single(
        hass,
        attrs={
            "current_position": 60,
            "current_tilt_position": 30,
            "supported_features": 143 | 128,
        },
        entry_id="proxy_mirror_tilt",
    )
    state = hass.states.get(proxy_eid)
    assert state.attributes.get("current_tilt_position") == 30


async def test_proxy_mirrors_source_supported_features(hass) -> None:
    """``supported_features`` from source state appears on the proxy state."""
    _, proxy_eid = await _setup_single(
        hass,
        attrs={"current_position": 0, "supported_features": 15},
        entry_id="proxy_mirror_feats",
    )
    state = hass.states.get(proxy_eid)
    assert state.attributes.get("supported_features") == 15


async def test_proxy_unavailable_when_source_unavailable(hass) -> None:
    """``state == unavailable`` on source → proxy state is unavailable."""
    _, proxy_eid = await _setup_single(
        hass,
        state="unavailable",
        attrs={},
        entry_id="proxy_mirror_unavail",
    )
    state = hass.states.get(proxy_eid)
    assert state.state == "unavailable"


async def test_proxy_unavailable_when_source_unknown(hass) -> None:
    """``state == unknown`` on source → proxy state is unavailable."""
    _, proxy_eid = await _setup_single(
        hass,
        state="unknown",
        attrs={},
        entry_id="proxy_mirror_unknown",
    )
    state = hass.states.get(proxy_eid)
    assert state.state == "unavailable"


async def test_proxy_state_updates_on_source_state_change(hass) -> None:
    """Updating source state propagates to proxy state attribute."""
    _, proxy_eid = await _setup_single(
        hass,
        attrs={"current_position": 50, "supported_features": 143},
        entry_id="proxy_mirror_changes",
    )
    # Change source position
    hass.states.async_set(
        "cover.living_room",
        "open",
        {"current_position": 25, "supported_features": 143},
    )
    await hass.async_block_till_done()
    state = hass.states.get(proxy_eid)
    assert state.attributes.get("current_position") == 25


async def test_proxy_position_reports_logical_frame_under_inverse_state(hass) -> None:
    """With ``CONF_INVERSE_STATE=True`` the proxy un-inverts the source reading.

    Revised for #1027. This test previously asserted a verbatim mirror on the
    rationale that "the integration's set_position path inverts internally" —
    which was false at the time: the user path dispatched raw, so command and
    read were accidentally self-consistent. Now that the command path really
    does invert, a verbatim read would break the round trip: drag the proxy to
    30, the source is driven to 70, and the proxy would read back 70.

    The proxy is a normal HA cover, so it publishes the logical frame in both
    directions. Source reports 30 → proxy shows 70.
    """
    _, proxy_eid = await _setup_single(
        hass,
        attrs={"current_position": 30, "supported_features": 143},
        entry_id="proxy_mirror_logical_frame",
        extra_options={CONF_INVERSE_STATE: True},
    )
    state = hass.states.get(proxy_eid)
    assert state.attributes.get("current_position") == 70


async def test_proxy_position_verbatim_when_interpolation_active(hass) -> None:
    """Interpolation suppresses position inversion, so the read stays verbatim.

    Effective inversion is ``inverse_state AND NOT interpolation`` — the same
    predicate the dispatch uses — so with both configured the proxy must not
    invert. It must not rescale through the calibration curve either: mapping
    the motor reading back onto the linear scale is #925's opt-in
    ``CONF_PROXY_LINEAR_SCALE``, deliberately out of scope here.
    """
    _, proxy_eid = await _setup_single(
        hass,
        attrs={"current_position": 30, "supported_features": 143},
        entry_id="proxy_mirror_interp_verbatim",
        extra_options={
            CONF_INVERSE_STATE: True,
            CONF_INTERP: True,
            CONF_INTERP_LIST: [0, 25, 58, 100],
            CONF_INTERP_LIST_NEW: [0, 45, 58, 100],
        },
    )
    state = hass.states.get(proxy_eid)
    assert state.attributes.get("current_position") == 30


async def test_proxy_is_closed_logical_frame(hass) -> None:
    """``is_closed`` follows the logical position, not the raw source attribute.

    On an inverted cover a source reading of 100 means fully closed. The proxy
    reports position 0, and ``is_closed`` derives from that with no code of its
    own.
    """
    _, proxy_eid = await _setup_single(
        hass,
        attrs={"current_position": 100, "supported_features": 143},
        entry_id="proxy_mirror_is_closed_logical",
        extra_options={CONF_INVERSE_STATE: True},
    )
    entity_obj = _get_proxy_entity(hass, proxy_eid)
    assert entity_obj is not None
    assert entity_obj.current_cover_position == 0
    assert entity_obj.is_closed is True


# ---- transient-state tests (is_opening / is_closing) ------------------- #


def _get_proxy_entity(hass, proxy_eid):
    """Return the AdaptiveProxyCover entity object for the given entity_id."""
    cover_component = hass.data.get("entity_components", {}).get("cover")
    if cover_component is not None:
        return cover_component.get_entity(proxy_eid)
    return None


async def test_proxy_is_opening_when_source_is_opening(hass) -> None:
    """``is_opening`` is True and ``is_closing`` is False when source is opening."""
    _, proxy_eid = await _setup_single(
        hass,
        state=CoverState.OPENING,
        attrs={"current_position": 50, "supported_features": 143},
        entry_id="proxy_opening_test",
    )
    entity_obj = _get_proxy_entity(hass, proxy_eid)
    assert entity_obj is not None
    assert entity_obj.is_opening is True
    assert entity_obj.is_closing is False


async def test_proxy_is_closing_when_source_is_closing(hass) -> None:
    """``is_closing`` is True and ``is_opening`` is False when source is closing."""
    _, proxy_eid = await _setup_single(
        hass,
        state=CoverState.CLOSING,
        attrs={"current_position": 50, "supported_features": 143},
        entry_id="proxy_closing_test",
    )
    entity_obj = _get_proxy_entity(hass, proxy_eid)
    assert entity_obj is not None
    assert entity_obj.is_closing is True
    assert entity_obj.is_opening is False


async def test_proxy_is_not_opening_or_closing_when_source_is_open(hass) -> None:
    """Both transient properties are False when source is open (steady state)."""
    _, proxy_eid = await _setup_single(
        hass,
        state=CoverState.OPEN,
        attrs={"current_position": 100, "supported_features": 143},
        entry_id="proxy_open_not_transient",
    )
    entity_obj = _get_proxy_entity(hass, proxy_eid)
    assert entity_obj is not None
    assert entity_obj.is_opening is False
    assert entity_obj.is_closing is False


async def test_proxy_is_not_opening_or_closing_when_source_is_closed(hass) -> None:
    """Both transient properties are False when source is closed (steady state)."""
    _, proxy_eid = await _setup_single(
        hass,
        state=CoverState.CLOSED,
        attrs={"current_position": 0, "supported_features": 143},
        entry_id="proxy_closed_not_transient",
    )
    entity_obj = _get_proxy_entity(hass, proxy_eid)
    assert entity_obj is not None
    assert entity_obj.is_opening is False
    assert entity_obj.is_closing is False


async def test_proxy_state_transitions_through_opening(hass) -> None:
    """Proxy state string tracks source through closed → opening → open."""
    source_id = "cover.transition_source"
    _, proxy_eid = await _setup_single(
        hass,
        source=source_id,
        state=CoverState.CLOSED,
        attrs={"current_position": 0, "supported_features": 143},
        entry_id="proxy_transition_test",
    )
    entity_obj = _get_proxy_entity(hass, proxy_eid)
    assert entity_obj is not None
    assert hass.states.get(proxy_eid).state == CoverState.CLOSED

    # Transition to opening
    hass.states.async_set(
        source_id,
        CoverState.OPENING,
        {"current_position": 50, "supported_features": 143},
    )
    await hass.async_block_till_done()
    assert hass.states.get(proxy_eid).state == CoverState.OPENING

    # Transition to open
    hass.states.async_set(
        source_id,
        CoverState.OPEN,
        {"current_position": 100, "supported_features": 143},
    )
    await hass.async_block_till_done()
    assert hass.states.get(proxy_eid).state == CoverState.OPEN


async def test_proxy_is_opening_false_when_source_unavailable(hass) -> None:
    """Both transient properties are False when source is unavailable."""
    _, proxy_eid = await _setup_single(
        hass,
        state=STATE_UNAVAILABLE,
        attrs={},
        entry_id="proxy_unavail_transient",
    )
    entity_obj = _get_proxy_entity(hass, proxy_eid)
    assert entity_obj is not None
    assert entity_obj.is_opening is False
    assert entity_obj.is_closing is False


async def test_proxy_is_opening_false_when_source_unknown(hass) -> None:
    """Both transient properties are False when source is unknown."""
    _, proxy_eid = await _setup_single(
        hass,
        state=STATE_UNKNOWN,
        attrs={},
        entry_id="proxy_unknown_transient",
    )
    entity_obj = _get_proxy_entity(hass, proxy_eid)
    assert entity_obj is not None
    assert entity_obj.is_opening is False
    assert entity_obj.is_closing is False
