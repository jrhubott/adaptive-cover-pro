"""Proxy cover command routing tests (Phase D Step 16)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from homeassistant.components.cover import CoverEntityFeature
from homeassistant.const import ATTR_ENTITY_ID

from custom_components.adaptive_cover_pro.const import (
    CONF_DAY_NIGHT_CONTROL_MODEL,
    CONF_ENABLE_PROXY_COVER,
    CONF_ENTITIES,
    CONF_INTERP,
    CONF_INTERP_LIST,
    CONF_INTERP_LIST_NEW,
    CONF_INVERSE_STATE,
    CONF_INVERSE_TILT,
    CONF_SENSOR_TYPE,
    DAY_NIGHT_MODEL_POSITION_TILT,
    DOMAIN,
    CoverType,
)
from tests.ha_helpers import (
    VERTICAL_OPTIONS,
    TILT_OPTIONS,
    _patch_coordinator_refresh,
)

pytestmark = [
    pytest.mark.integration,
    # The venetian proxy tests drive the real dual-axis sequencer end-to-end;
    # zero its motor-lag sleeps so they don't wait on real asyncio timers.
    pytest.mark.usefixtures("neutralize_venetian_delays"),
]


async def _setup_proxy(
    hass,
    *,
    source: str = "cover.living_room",
    cover_type: str = CoverType.BLIND,
    entry_id: str = "proxy_cmd",
    options: dict | None = None,
    state: str = "open",
    attrs: dict | None = None,
):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from homeassistant.helpers import entity_registry as er

    base = (
        dict(options)
        if options is not None
        else (
            dict(TILT_OPTIONS)
            if cover_type == CoverType.TILT
            else dict(VERTICAL_OPTIONS)
        )
    )
    base[CONF_ENTITIES] = [source]
    base[CONF_ENABLE_PROXY_COVER] = True

    hass.states.async_set(
        source,
        state,
        attrs or {"current_position": 50, "supported_features": 143},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Proxy Cmd", CONF_SENSOR_TYPE: cover_type},
        options=base,
        entry_id=entry_id,
        title="Proxy Cmd",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    reg = er.async_get(hass)
    proxy_eid = next(
        e.entity_id
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id
        and e.unique_id.startswith(f"{entry.entry_id}_proxy_")
    )
    return entry, coordinator, proxy_eid


async def test_set_cover_position_routes_through_async_apply_user_position(
    hass,
) -> None:
    """A ``cover.set_cover_position`` service call delegates to the helper."""
    entry, coord, proxy_eid = await _setup_proxy(hass, entry_id="proxy_cmd_set")
    coord.async_apply_user_position = AsyncMock(
        return_value=("sent", "set_cover_position")
    )

    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": proxy_eid, "position": 42},
        blocking=True,
    )
    coord.async_apply_user_position.assert_awaited_once_with(
        "cover.living_room", 42, trigger="proxy_managed"
    )


async def test_set_cover_position_uses_proxy_managed_trigger(hass) -> None:
    """The trigger label for a managed cover command is ``proxy_managed``."""
    entry, coord, proxy_eid = await _setup_proxy(hass, entry_id="proxy_cmd_trig")
    coord.async_apply_user_position = AsyncMock(return_value=("sent", ""))

    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": proxy_eid, "position": 10},
        blocking=True,
    )
    args, kwargs = coord.async_apply_user_position.await_args
    assert kwargs.get("trigger") == "proxy_managed"


async def test_open_cover_calls_apply_user_position_with_100(hass) -> None:
    """``cover.open_cover`` sends 100 with trigger ``proxy_open``."""
    entry, coord, proxy_eid = await _setup_proxy(hass, entry_id="proxy_cmd_open")
    coord.async_apply_user_position = AsyncMock(return_value=("sent", ""))

    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": proxy_eid}, blocking=True
    )
    coord.async_apply_user_position.assert_awaited_once_with(
        "cover.living_room", 100, trigger="proxy_open"
    )


async def test_close_cover_calls_apply_user_position_with_0(hass) -> None:
    """``cover.close_cover`` sends 0 with trigger ``proxy_close`` (clamp applies)."""
    entry, coord, proxy_eid = await _setup_proxy(hass, entry_id="proxy_cmd_close")
    coord.async_apply_user_position = AsyncMock(return_value=("sent", ""))

    await hass.services.async_call(
        "cover", "close_cover", {"entity_id": proxy_eid}, blocking=True
    )
    coord.async_apply_user_position.assert_awaited_once_with(
        "cover.living_room", 0, trigger="proxy_close"
    )


async def test_proxy_tilt_routes_to_apply_user_tilt(hass) -> None:
    """Venetian proxy: ``set_cover_tilt_position`` delegates to ``async_apply_user_tilt``.

    Issue #684: the proxy tilt setter must drive the *tilt* axis, not the
    position parameter of ``async_apply_user_position``. The requested tilt
    flows into the dedicated tilt entry point with the ``proxy_tilt`` trigger,
    and the position helper is left untouched.
    """
    entry, coord, proxy_eid = await _setup_proxy(
        hass,
        cover_type=CoverType.VENETIAN,
        source="cover.venetian",
        entry_id="proxy_cmd_tilt",
        options=dict(VERTICAL_OPTIONS),
        attrs={
            "current_position": 50,
            "current_tilt_position": 80,
            "supported_features": 143 | 128,
        },
    )
    coord.async_apply_user_tilt = AsyncMock(return_value=("sent", ""))
    coord.async_apply_user_position = AsyncMock(return_value=("sent", ""))

    await hass.services.async_call(
        "cover",
        "set_cover_tilt_position",
        {"entity_id": proxy_eid, "tilt_position": 10},
        blocking=True,
    )
    coord.async_apply_user_tilt.assert_awaited_once_with(
        "cover.venetian", 10, trigger="proxy_tilt"
    )
    coord.async_apply_user_position.assert_not_awaited()


async def test_proxy_tilt_sends_real_tilt_service_leaves_carriage_unchanged(
    hass,
) -> None:
    """End-to-end: a proxy tilt request lands ``set_cover_tilt_position`` on the source.

    Core of issue #684 — the entry point is NOT mocked. A venetian dual-axis
    source at position 50 / tilt 80 receiving a tilt request of 10 must emit
    exactly one ``cover.set_cover_tilt_position`` (tilt_position == 10) on the
    source and ZERO ``cover.set_cover_position`` calls (carriage unchanged).
    """
    from homeassistant.const import EVENT_CALL_SERVICE

    entry, coord, proxy_eid = await _setup_proxy(
        hass,
        cover_type=CoverType.VENETIAN,
        source="cover.venetian",
        entry_id="proxy_cmd_tilt_real",
        options=dict(VERTICAL_OPTIONS),
        attrs={
            "current_position": 50,
            "current_tilt_position": 80,
            "supported_features": 143 | 128,
        },
    )

    tilt_calls: list[dict] = []
    position_calls: list[dict] = []

    def _on_event(event):
        if event.data.get("domain") != "cover":
            return
        data = dict(event.data.get("service_data") or {})
        if data.get("entity_id") != "cover.venetian":
            return
        if event.data.get("service") == "set_cover_tilt_position":
            tilt_calls.append(data)
        elif event.data.get("service") == "set_cover_position":
            position_calls.append(data)

    hass.bus.async_listen(EVENT_CALL_SERVICE, _on_event)

    await hass.services.async_call(
        "cover",
        "set_cover_tilt_position",
        {"entity_id": proxy_eid, "tilt_position": 10},
        blocking=True,
    )
    await hass.async_block_till_done()

    # At least one real tilt service call lands on the source, and every tilt
    # call requests the user value (10) — a static mock source never publishes
    # the new tilt, so the verify path may fire a bounded drift retry of the
    # SAME value (issue #500), which is correct production behavior.
    assert tilt_calls, f"no tilt service call reached the source: {tilt_calls}"
    assert all(c.get("tilt_position") == 10 for c in tilt_calls), tilt_calls
    # Core of #684: the carriage must NOT be commanded.
    assert position_calls == [], f"carriage moved unexpectedly: {position_calls}"


async def test_stop_cover_forwards_directly_no_clamp(hass) -> None:
    """``cover.stop_cover`` forwards to the source service, not through the helper."""
    from homeassistant.const import EVENT_CALL_SERVICE

    entry, coord, proxy_eid = await _setup_proxy(hass, entry_id="proxy_cmd_stop")
    coord.async_apply_user_position = AsyncMock(return_value=("sent", ""))

    # Track every cover.stop_cover service call via the event bus.
    calls: list[dict] = []

    def _on_event(event):
        if (
            event.data.get("domain") == "cover"
            and event.data.get("service") == "stop_cover"
        ):
            calls.append(dict(event.data.get("service_data") or {}))

    hass.bus.async_listen(EVENT_CALL_SERVICE, _on_event)

    await hass.services.async_call(
        "cover", "stop_cover", {"entity_id": proxy_eid}, blocking=True
    )
    await hass.async_block_till_done()
    # The helper must NOT have been called
    coord.async_apply_user_position.assert_not_called()
    # A forwarded stop_cover targets the source entity
    targeted = [c for c in calls if c.get("entity_id") == "cover.living_room"]
    assert targeted, f"no forwarded stop_cover; captured={calls}"


async def test_commands_dropped_when_source_unavailable(hass) -> None:
    """Source unavailable → helper never called, no exception raised."""
    entry, coord, proxy_eid = await _setup_proxy(
        hass,
        entry_id="proxy_cmd_unavail",
        state="unavailable",
        attrs={},
    )
    coord.async_apply_user_position = AsyncMock(return_value=("sent", ""))

    # set_position
    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": proxy_eid, "position": 50},
        blocking=True,
    )
    coord.async_apply_user_position.assert_not_called()


# ---------------------------------------------------------------------------
# Issue #1027: the proxy speaks the logical frame; the source gets cover frame
# ---------------------------------------------------------------------------


def _capture_cover_calls(hass, source: str) -> dict[str, list[dict]]:
    """Record every cover service call landing on *source*, keyed by service."""
    from homeassistant.const import EVENT_CALL_SERVICE

    captured: dict[str, list[dict]] = {}

    def _on_event(event):
        if event.data.get("domain") != "cover":
            return
        data = dict(event.data.get("service_data") or {})
        if data.get(ATTR_ENTITY_ID) != source:
            return
        captured.setdefault(event.data.get("service"), []).append(data)

    hass.bus.async_listen(EVENT_CALL_SERVICE, _on_event)
    return captured


async def test_proxy_slider_inverse_state_dispatches_inverted(hass) -> None:
    """Dragging the proxy slider to 30 commands the source to 70.

    The proxy publishes HA's standard frame, so its slider value is logical.
    On an ``inverse_state`` instance the integration owes the source the
    inverted number — the same one the automatic path sends (#1027).
    """
    options = dict(VERTICAL_OPTIONS)
    options[CONF_INVERSE_STATE] = True
    _entry, _coord, proxy_eid = await _setup_proxy(
        hass, entry_id="proxy_cmd_inv", options=options
    )
    calls = _capture_cover_calls(hass, "cover.living_room")

    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": proxy_eid, "position": 30},
        blocking=True,
    )
    await hass.async_block_till_done()

    sent = calls.get("set_cover_position", [])
    assert sent, f"no position command reached the source: {calls}"
    assert all(c["position"] == 70 for c in sent), sent


async def test_proxy_slider_interpolation_dispatches_motor_value(hass) -> None:
    """Dragging the proxy slider to linear 25 commands the source to motor 45."""
    options = dict(VERTICAL_OPTIONS)
    options.update(
        {
            CONF_INTERP: True,
            CONF_INTERP_LIST: [0, 25, 58, 100],
            CONF_INTERP_LIST_NEW: [0, 45, 58, 100],
        }
    )
    _entry, _coord, proxy_eid = await _setup_proxy(
        hass, entry_id="proxy_cmd_interp", options=options
    )
    calls = _capture_cover_calls(hass, "cover.living_room")

    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": proxy_eid, "position": 25},
        blocking=True,
    )
    await hass.async_block_till_done()

    sent = calls.get("set_cover_position", [])
    assert sent, f"no position command reached the source: {calls}"
    assert all(c["position"] == 45 for c in sent), sent


async def test_proxy_open_close_with_inverse_and_endpoint_open_close(hass) -> None:
    """Proxy Open/Close route through the endpoint services in the cover frame.

    With ``endpoint_use_open_close`` (default on) and ``has_open``/``has_close``
    capabilities, a logical endpoint is inverted first and then routed: logical
    Open (100) becomes cover-frame 0 and lands on ``close_cover``; logical Close
    becomes 100 and lands on ``open_cover``. That is byte-identical to what the
    automatic path already emits for the same logical values, which is the point
    of #1027 — the two paths stop disagreeing.
    """
    options = dict(VERTICAL_OPTIONS)
    options[CONF_INVERSE_STATE] = True
    _entry, _coord, proxy_eid = await _setup_proxy(
        hass, entry_id="proxy_cmd_endpoint_inv", options=options
    )
    calls = _capture_cover_calls(hass, "cover.living_room")

    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": proxy_eid}, blocking=True
    )
    await hass.async_block_till_done()

    assert "close_cover" in calls, f"logical Open must route to close_cover: {calls}"
    assert "open_cover" not in calls, calls

    calls.clear()
    await hass.services.async_call(
        "cover", "close_cover", {"entity_id": proxy_eid}, blocking=True
    )
    await hass.async_block_till_done()

    assert "open_cover" in calls, f"logical Close must route to open_cover: {calls}"
    assert "close_cover" not in calls, calls


async def test_proxy_slider_round_trip_inverse_state(hass) -> None:
    """Command and read agree: set the proxy to 30, read 30 back.

    The pair of #1027 (dispatch inverts) and #1028 (read un-inverts). Either
    half alone leaves the proxy entity self-inconsistent — a slider that
    settles on a different number than the one the user dragged it to.
    """
    options = dict(VERTICAL_OPTIONS)
    options[CONF_INVERSE_STATE] = True
    _entry, _coord, proxy_eid = await _setup_proxy(
        hass, entry_id="proxy_cmd_round_trip", options=options
    )
    calls = _capture_cover_calls(hass, "cover.living_room")

    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": proxy_eid, "position": 30},
        blocking=True,
    )
    await hass.async_block_till_done()

    sent = calls.get("set_cover_position", [])
    assert sent, f"no position command reached the source: {calls}"
    commanded = sent[0]["position"]
    assert commanded == 70

    # The source now reports what it was actually driven to.
    hass.states.async_set(
        "cover.living_room",
        "open",
        {"current_position": commanded, "supported_features": 143},
    )
    await hass.async_block_till_done()

    assert hass.states.get(proxy_eid).attributes.get("current_position") == 30


# ---------------------------------------------------------------------------
# Issue #1027: the tilt-PRIMARY cover types ride the position path, so their
# tilt read has to be re-framed with it.
# ---------------------------------------------------------------------------

# Everything: OPEN | CLOSE | SET_POSITION | STOP | SET_TILT_POSITION. Despite
# the name it carried until #1034, ``128 | 15`` includes SET_POSITION (bit 4),
# so it describes a FULLY capable cover, not a tilt-only one.
_POSITION_AND_TILT_FEATURES = 128 | 15

# A genuinely tilt-only cover. The load-bearing part is the bit that is NOT
# here: SET_POSITION. Without it ``should_use_tilt`` reroutes any policy's
# dispatch onto ``set_cover_tilt_position``. Spelled from the enum so the
# omission is visible rather than hidden inside a magic number.
_TILT_ONLY_FEATURES = int(
    CoverEntityFeature.OPEN
    | CoverEntityFeature.CLOSE
    | CoverEntityFeature.STOP
    | CoverEntityFeature.OPEN_TILT
    | CoverEntityFeature.CLOSE_TILT
    | CoverEntityFeature.STOP_TILT
    | CoverEntityFeature.SET_TILT_POSITION
)


async def test_proxy_tilt_round_trip_inverse_state_on_tilt_only_cover(hass) -> None:
    """Drag the proxy tilt to 30 on a ``cover_tilt`` instance and read 30 back.

    ``cover_tilt`` / ``cover_louvered_roof`` carry tilt as their ONLY axis, so
    ``async_apply_user_tilt`` falls back to ``async_apply_user_position`` and
    the value is re-framed by ``_to_cover_frame`` before it is dispatched onto
    ``set_cover_tilt_position``. The read side has to move with it, exactly as
    the position axis did — otherwise the slider settles on 70 after the user
    dragged it to 30.
    """
    options = dict(TILT_OPTIONS)
    options[CONF_INVERSE_STATE] = True
    _entry, _coord, proxy_eid = await _setup_proxy(
        hass,
        cover_type=CoverType.TILT,
        entry_id="proxy_cmd_tilt_round_trip",
        options=options,
        attrs={
            "current_tilt_position": 50,
            "supported_features": _POSITION_AND_TILT_FEATURES,
        },
    )
    calls = _capture_cover_calls(hass, "cover.living_room")

    await hass.services.async_call(
        "cover",
        "set_cover_tilt_position",
        {"entity_id": proxy_eid, "tilt_position": 30},
        blocking=True,
    )
    await hass.async_block_till_done()

    sent = calls.get("set_cover_tilt_position", [])
    assert sent, f"no tilt command reached the source: {calls}"
    commanded = sent[0]["tilt_position"]
    assert commanded == 70

    # The source now reports what it was actually driven to.
    hass.states.async_set(
        "cover.living_room",
        "open",
        {
            "current_tilt_position": commanded,
            "supported_features": _POSITION_AND_TILT_FEATURES,
        },
    )
    await hass.async_block_till_done()

    assert hass.states.get(proxy_eid).attributes.get("current_tilt_position") == 30


async def test_proxy_tilt_read_stays_verbatim_for_second_axis_tilt(hass) -> None:
    """``inverse_state`` does not reach a venetian's SECOND tilt axis.

    One specific negative, and only that one. The slats' wire conversion lives
    in the venetian sequencer's ``_to_wire``, which is keyed on ``inverse_tilt``
    — a separately-configured option — so nothing the ``inverse_state``
    dispatch path (#1027) does can land on this attribute, and mirroring it
    verbatim is correct.

    This does NOT say the venetian tilt read is never re-framed: under
    ``inverse_tilt`` it is, which is the positive half that
    ``test_proxy_tilt_round_trip_inverse_tilt_on_venetian`` pins (#1034). The
    two together are the whole boundary — the read follows ``inverse_tilt``
    here and ``inverse_state`` never.
    """
    options = dict(TILT_OPTIONS)
    options[CONF_INVERSE_STATE] = True
    _entry, _coord, proxy_eid = await _setup_proxy(
        hass,
        cover_type=CoverType.VENETIAN,
        entry_id="proxy_cmd_venetian_tilt_read",
        options=options,
        attrs={
            "current_position": 60,
            "current_tilt_position": 30,
            "supported_features": _POSITION_AND_TILT_FEATURES,
        },
    )

    assert hass.states.get(proxy_eid).attributes.get("current_tilt_position") == 30


# ---------------------------------------------------------------------------
# Issue #1034: every write-side tilt transform owes the read side its inverse
# ---------------------------------------------------------------------------


async def test_proxy_tilt_round_trip_inverse_tilt_on_venetian(hass) -> None:
    """Drag a venetian proxy's tilt to 30 under ``inverse_tilt`` and read 30 back.

    A venetian's slat dispatch runs through the dual-axis sequencer's
    ``_to_wire``, which flips on ``inverse_tilt`` and is never
    interpolation-gated. Before #1034 the read side asked a predicate keyed on
    the PRIMARY axis, which is the position axis here, so it answered "not
    inverted" and mirrored the wire value verbatim: the user dragged the slider
    to 30, the source was commanded 70, and the slider settled back on 70.
    """
    options = dict(TILT_OPTIONS)
    options[CONF_INVERSE_TILT] = True
    _entry, _coord, proxy_eid = await _setup_proxy(
        hass,
        cover_type=CoverType.VENETIAN,
        entry_id="proxy_cmd_venetian_inv_tilt",
        options=options,
        attrs={
            "current_position": 60,
            "current_tilt_position": 50,
            "supported_features": _POSITION_AND_TILT_FEATURES,
        },
    )
    calls = _capture_cover_calls(hass, "cover.living_room")

    await hass.services.async_call(
        "cover",
        "set_cover_tilt_position",
        {"entity_id": proxy_eid, "tilt_position": 30},
        blocking=True,
    )
    await hass.async_block_till_done()

    sent = calls.get("set_cover_tilt_position", [])
    assert sent, f"no tilt command reached the source: {calls}"
    # A static mock source never publishes the new tilt, so the verify path may
    # fire a bounded drift retry of the SAME value (#500) — assert the value,
    # not the call count.
    assert all(c["tilt_position"] == 70 for c in sent), sent

    # The source now reports what it was actually driven to.
    hass.states.async_set(
        "cover.living_room",
        "open",
        {
            "current_position": 60,
            "current_tilt_position": 70,
            "supported_features": _POSITION_AND_TILT_FEATURES,
        },
    )
    await hass.async_block_till_done()

    assert hass.states.get(proxy_eid).attributes.get("current_tilt_position") == 30


async def test_proxy_tilt_round_trip_inverse_tilt_on_day_night_shade(hass) -> None:
    """The day/night blend axis rides the same sequencer, so it owes the same inverse.

    ``cover_day_night_shade`` Model A declares ``(position, tilt)`` and forwards
    ``invert_tilt`` into the shared ``DualAxisSequencer``, and
    ``coordinator._inverse_tilt`` reads ``inverse_tilt`` for EVERY cover type —
    so the write side flips here exactly as it does for a venetian.

    ``inverse_tilt`` is not offered by the day/night geometry schema and
    ``adaptive_cover_pro.set_options`` rejects it, but
    ``adaptive_cover_pro.import_config`` merges every non-``_``-prefixed key
    wholesale (``services/import_service.py:117-144``) and validates only keys
    present in ``OPTION_RANGES``. A hand-edited export therefore reaches this
    config: not config-flow-offered, but not unreachable either.
    """
    options = dict(VERTICAL_OPTIONS)
    options[CONF_INVERSE_TILT] = True
    # Pinned explicitly rather than relying on the default staying Model A.
    options[CONF_DAY_NIGHT_CONTROL_MODEL] = DAY_NIGHT_MODEL_POSITION_TILT
    _entry, _coord, proxy_eid = await _setup_proxy(
        hass,
        cover_type=CoverType.DAY_NIGHT_SHADE,
        entry_id="proxy_cmd_day_night_inv_tilt",
        options=options,
        attrs={
            "current_position": 60,
            "current_tilt_position": 50,
            "supported_features": _POSITION_AND_TILT_FEATURES,
        },
    )
    calls = _capture_cover_calls(hass, "cover.living_room")

    await hass.services.async_call(
        "cover",
        "set_cover_tilt_position",
        {"entity_id": proxy_eid, "tilt_position": 30},
        blocking=True,
    )
    await hass.async_block_till_done()

    sent = calls.get("set_cover_tilt_position", [])
    assert sent, f"no tilt command reached the source: {calls}"
    assert all(c["tilt_position"] == 70 for c in sent), sent

    hass.states.async_set(
        "cover.living_room",
        "open",
        {
            "current_position": 60,
            "current_tilt_position": 70,
            "supported_features": _POSITION_AND_TILT_FEATURES,
        },
    )
    await hass.async_block_till_done()

    assert hass.states.get(proxy_eid).attributes.get("current_tilt_position") == 30


async def test_proxy_tilt_round_trip_inverse_state_on_tilt_only_hardware(hass) -> None:
    """A position-only cover type on tilt-only hardware re-frames on ``inverse_state``.

    ``should_use_tilt`` flips ANY policy's dispatch onto
    ``set_cover_tilt_position`` when the bound entity advertises
    ``set_tilt_position`` but not ``set_position``. A ``cover_blind`` on such an
    entity has no tilt hook, so ``async_apply_user_tilt`` falls back to
    ``async_apply_user_position``: the value is re-framed by ``_to_cover_frame``
    on ``inverse_state`` and then routed onto the tilt service by
    ``select_default_axis``. The read side has to follow it there.
    """
    options = dict(VERTICAL_OPTIONS)
    options[CONF_INVERSE_STATE] = True
    _entry, _coord, proxy_eid = await _setup_proxy(
        hass,
        cover_type=CoverType.BLIND,
        entry_id="proxy_cmd_blind_tilt_only_hw",
        options=options,
        attrs={
            "current_tilt_position": 50,
            "supported_features": _TILT_ONLY_FEATURES,
        },
    )
    calls = _capture_cover_calls(hass, "cover.living_room")

    await hass.services.async_call(
        "cover",
        "set_cover_tilt_position",
        {"entity_id": proxy_eid, "tilt_position": 30},
        blocking=True,
    )
    await hass.async_block_till_done()

    sent = calls.get("set_cover_tilt_position", [])
    assert sent, f"no tilt command reached the source: {calls}"
    assert all(c["tilt_position"] == 70 for c in sent), sent

    hass.states.async_set(
        "cover.living_room",
        "open",
        {
            "current_tilt_position": 70,
            "supported_features": _TILT_ONLY_FEATURES,
        },
    )
    await hass.async_block_till_done()

    assert hass.states.get(proxy_eid).attributes.get("current_tilt_position") == 30
