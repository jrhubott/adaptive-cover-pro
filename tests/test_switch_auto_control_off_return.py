"""Issue #293 — switch return-to-default uses bypass_auto_control=True.

When the auto_control switch is toggled OFF and return_to_default_toggle is
enabled, the integration moves covers to the default position as a one-shot
transition action.  Without an explicit bypass channel, the auto_control gate
fix (issue #293) would skip this legitimate caller too.

This test asserts that the switch path passes bypass_auto_control=True so
return-to-default still works after the gate fix.
"""

from __future__ import annotations

import datetime as dt
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_DEFAULT_HEIGHT,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
    PositionContext,
)
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
)
from custom_components.adaptive_cover_pro.switch import AdaptiveCoverSwitch
from tests.test_pipeline.conftest import make_snapshot


def _patch_caps(
    *,
    has_set_position=True,
    has_set_tilt_position=False,
    has_open=True,
    has_close=True,
    has_stop=True,
):
    return patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
        return_value={
            "has_set_position": has_set_position,
            "has_set_tilt_position": has_set_tilt_position,
            "has_open": has_open,
            "has_close": has_close,
            "has_stop": has_stop,
        },
    )


def _make_coord_with_real_cmd_svc(
    hass,
    *,
    cover_type="cover_blind",
    inverse_state=False,
    default_height=60,
    current_position=0,
    constraint_snapshot=None,
):
    """Build a coordinator stub backed by a real CoverCommandService.

    ``constraint_snapshot``: when given a :class:`PipelineSnapshot` (build one
    with ``tests.test_pipeline.conftest.make_snapshot``), wires the REAL
    ``_clamp_to_outside_window_bounds`` / ``_build_user_command_snapshot``
    instead of the identity-patch every other test in this module uses —
    issue #1376 audit MUST-FIX: nothing previously exercised this seam's
    clamp against a real active constraint, only against an identity stub
    that hides whatever the clamp does.
    """
    coord = MagicMock()
    coord.logger = MagicMock()
    coord.entities = ["cover.test"]
    coord.return_to_default_toggle = True
    coord.automatic_control = False  # toggle just flipped to off
    coord.manager.manual_controlled = []
    coord.config_entry.options = {CONF_DEFAULT_HEIGHT: default_height}
    coord.async_refresh = AsyncMock()
    # Issue #1376: the auto-off return-to-default broadcast now shares
    # coordinator._broadcast_default_position with the end-of-window/sunset
    # broadcasts, which resolve their wire frame from ``self._inverse_state``.
    # A MagicMock stub must state that explicitly — a bare MagicMock attribute
    # is truthy, so an unstated ``_inverse_state`` would silently invert.
    coord._inverse_state = inverse_state
    # The return-to-default loop routes each target through the polymorphic
    # ``_entity_target`` (identity for every non-dual-entity cover type).
    coord._entity_target = lambda _entity, position, *, inverted=None: position
    # Real policy: the return-to-default loop asks it for the entity order (#1115).
    coord._policy = get_policy(cover_type)
    if constraint_snapshot is None:
        # No outside-window bounds configured in these tests — identity clamp.
        coord._clamp_to_outside_window_bounds = lambda position, _options: position
    else:
        # Real clamp, fed a real PipelineSnapshot through the same
        # ``_snapshot_builder.build`` seam production reads (mocked only at
        # the HA-boundary edge, not at the clamp itself).
        coord._cover_data = MagicMock()
        coord._snapshot_builder = MagicMock()
        coord._snapshot_builder.build = MagicMock(return_value=constraint_snapshot)
        coord._build_user_command_snapshot = types.MethodType(
            AdaptiveDataUpdateCoordinator._build_user_command_snapshot, coord
        )
        coord._clamp_to_outside_window_bounds = types.MethodType(
            AdaptiveDataUpdateCoordinator._clamp_to_outside_window_bounds, coord
        )
    coord._resolve_broadcast_dispatch = types.MethodType(
        AdaptiveDataUpdateCoordinator._resolve_broadcast_dispatch, coord
    )
    coord._broadcast_default_position = types.MethodType(
        AdaptiveDataUpdateCoordinator._broadcast_default_position, coord
    )

    cmd_svc = CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type=cover_type,
        grace_mgr=MagicMock(),
        open_close_threshold=50,
    )
    cmd_svc._enabled = True
    # Stub current position so abs(current - 60) is computable and outside
    # the tolerance band (cover is at 0, target will be 60 — far apart).
    cmd_svc._get_current_position = MagicMock(return_value=current_position)
    coord._cmd_svc = cmd_svc

    def _build_ctx(
        entity,
        options,
        *,
        force=False,
        is_safety=False,
        bypass_auto_control=False,
        sun_just_appeared=False,
    ):
        return PositionContext(
            auto_control=False,
            manual_override=False,
            sun_just_appeared=sun_just_appeared,
            min_change=1,
            time_threshold=0,
            special_positions=[0, 100],
            force=force,
            is_safety=is_safety,
            bypass_auto_control=bypass_auto_control,
        )

    coord._build_position_context = _build_ctx
    return coord


@pytest.mark.asyncio
async def test_return_to_default_fires_when_auto_control_toggled_off():
    """The sanctioned one-shot return-to-default still fires after #293 fix."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    coord = _make_coord_with_real_cmd_svc(hass)

    switch = object.__new__(AdaptiveCoverSwitch)
    switch.coordinator = coord
    switch._key = "automatic_control"
    switch._name = "test_switch"
    switch._initial_state = True
    switch.schedule_update_ha_state = MagicMock()

    with _patch_caps():
        await switch.async_turn_off()

    # The return-to-default command must have been sent.
    hass.services.async_call.assert_awaited()
    assert coord._cmd_svc.get_target("cover.test") == 60


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cover_type", "expected_service", "expected_attr"),
    [
        ("cover_blind", "set_cover_position", "position"),
        ("cover_tilt", "set_cover_tilt_position", "tilt_position"),
    ],
)
async def test_return_to_default_sends_axis_correct_service(
    cover_type: str, expected_service: str, expected_attr: str
) -> None:
    """Issue #1349 — the dispatch mechanism is axis-polymorphic per policy.

    Both caps are True here — ``has_set_position=True`` AND
    ``has_set_tilt_position=True`` — which is the realistic install shape
    for a tilt-capable cover (most report both features) and, critically,
    is the one configuration where capability-based fallback
    (``should_use_tilt``) cannot explain the routing: with both features
    present, a position-primary policy (``cover_blind``) still routes
    through the position axis while a tilt-primary policy (``cover_tilt``)
    still routes through the tilt axis. Parametrizing over both proves the
    two policies actively diverge under identical capabilities — the axis
    choice is driven by ``select_default_axis``'s policy-declared primary
    axis, not by which services the entity happens to expose.
    """
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    coord = _make_coord_with_real_cmd_svc(hass, cover_type=cover_type)

    switch = object.__new__(AdaptiveCoverSwitch)
    switch.coordinator = coord
    switch._key = "automatic_control"
    switch._name = "test_switch"
    switch._initial_state = True
    switch.schedule_update_ha_state = MagicMock()

    with _patch_caps(
        has_set_position=True,
        has_set_tilt_position=True,
        has_open=True,
        has_close=True,
        has_stop=True,
    ):
        await switch.async_turn_off()

    hass.services.async_call.assert_awaited()
    call_args = hass.services.async_call.await_args
    assert call_args.args[1] == expected_service
    assert call_args.args[2] == {"entity_id": "cover.test", expected_attr: 60}


@pytest.mark.asyncio
async def test_return_to_default_skipped_without_bypass_flag():
    """Sanity: the same call pattern WITHOUT bypass_auto_control would be skipped.

    This proves the bypass_auto_control flag is the load-bearing mechanism.
    """
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    cmd_svc = CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=MagicMock(),
        open_close_threshold=50,
    )
    cmd_svc._enabled = True

    ctx = PositionContext(
        auto_control=False,
        manual_override=False,
        sun_just_appeared=False,
        min_change=1,
        time_threshold=0,
        special_positions=[0, 100],
        force=True,
        is_safety=False,
        bypass_auto_control=False,  # MISSING — should be skipped
    )

    with _patch_caps():
        outcome, detail = await cmd_svc.apply_position(
            "cover.test", 60, "auto_control_off", context=ctx
        )

    assert outcome == "skipped"
    assert detail == "auto_control_off"
    hass.services.async_call.assert_not_called()


# ---------------------------------------------------------------------------
# Issue #1376 — the auto-off return-to-default broadcast must share the same
# logical→wire frame as the end-of-window/sunset broadcasts
# (coordinator._inverse_state), not dispatch the raw configured default.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_return_to_default_inverts_on_inverse_install() -> None:
    """#1376 repro: an inverse awning with default 0 must be sent to wire 100.

    ``inverse_state=True`` + ``default_percentage=0`` is the reporter's exact
    config (Luifel, cover_awning). The main pipeline maps configured 0 to wire
    100 (open_cover, retracted). Before this fix the switch seam dispatched
    the raw logical 0 — ``close_cover`` — driving the awning to the physical
    mirror image of its configured default.
    """
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    coord = _make_coord_with_real_cmd_svc(
        hass,
        cover_type="cover_awning",
        inverse_state=True,
        default_height=0,
        # Deliberately neither the raw default (0, the pre-fix bug's target)
        # nor the corrected wire value (100) — so neither frame's dispatch is
        # masked by the same-position gate, and the test isolates the
        # direction/service the seam picks rather than whether it dispatches
        # at all.
        current_position=50,
    )

    switch = object.__new__(AdaptiveCoverSwitch)
    switch.coordinator = coord
    switch._key = "automatic_control"
    switch._name = "test_switch"
    switch._initial_state = True
    switch.schedule_update_ha_state = MagicMock()

    with _patch_caps(has_set_position=True, has_open=True, has_close=True):
        await switch.async_turn_off()

    hass.services.async_call.assert_awaited()
    call_args = hass.services.async_call.await_args
    # endpoint_use_open_close defaults True (issue #697/#819) and wire 100 is
    # a full mechanical endpoint, so this routes through open_cover rather
    # than set_cover_position(position=100) — never close_cover / position 0.
    assert call_args.args[1] == "open_cover"
    assert call_args.args[2] == {"entity_id": "cover.test"}


@pytest.mark.asyncio
async def test_return_to_default_is_deduped_when_already_at_default_on_inverse_install() -> (
    None
):
    """#1376: once the frame is fixed, the reporter's case is a true no-op.

    Cover already sits at wire 100 (HA state "open") when auto_control turns
    off with a configured default of 0 on an inverse install. The corrected
    wire target (100) matches ``_current`` exactly, so the same-position gate
    that already exists in ``apply_position`` (issues #290/#567) swallows the
    command — no second, parallel dedupe is added at this seam.
    """
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    open_state = MagicMock()
    open_state.state = "open"
    hass.states.get = MagicMock(return_value=open_state)
    coord = _make_coord_with_real_cmd_svc(
        hass,
        cover_type="cover_awning",
        inverse_state=True,
        default_height=0,
        current_position=100,
    )

    switch = object.__new__(AdaptiveCoverSwitch)
    switch.coordinator = coord
    switch._key = "automatic_control"
    switch._name = "test_switch"
    switch._initial_state = True
    switch.schedule_update_ha_state = MagicMock()

    with _patch_caps(has_set_position=True, has_open=True, has_close=True):
        await switch.async_turn_off()

    hass.services.async_call.assert_not_awaited()
    assert coord._cmd_svc.last_skipped_action["reason"] == "same_position"


@pytest.mark.asyncio
async def test_default_broadcast_frame_parity_between_seams() -> None:
    """Issue #1376: auto-control-OFF and end-of-window must dispatch the SAME wire number.

    Both seams broadcast the same configured ``CONF_DEFAULT_HEIGHT`` option,
    bypassing the main pipeline. Two seams disagreeing about a bypassing
    broadcast's frame is the defect — the fix (routing both through
    ``coordinator._broadcast_default_position``) makes disagreement
    impossible. Fails today: 100 (end-of-window) vs 0 (switch).
    """
    entity = "cover.test"
    default_height = 0
    end_of_window_targets: dict[str, int] = {}
    auto_off_targets: dict[str, int] = {}

    def _shared_coord(target_store: dict[str, int]) -> MagicMock:
        coord = MagicMock()
        coord.logger = MagicMock()
        coord.entities = [entity]
        coord._policy = get_policy("cover_awning")
        coord._inverse_state = True
        coord._clamp_to_outside_window_bounds = lambda position, _o: position
        coord._resolve_broadcast_dispatch = types.MethodType(
            AdaptiveDataUpdateCoordinator._resolve_broadcast_dispatch, coord
        )
        coord._broadcast_default_position = types.MethodType(
            AdaptiveDataUpdateCoordinator._broadcast_default_position, coord
        )
        coord._entity_target = lambda _e, position, *, inverted=None: position
        coord._build_position_context = MagicMock(return_value=MagicMock())

        async def _apply(ent, position, _reason, context=None):  # noqa: ARG001
            target_store[ent] = position
            return ("sent", "")

        coord._cmd_svc = MagicMock()
        coord._cmd_svc.apply_position = _apply
        coord._cmd_svc.clear_non_safety_targets = MagicMock()
        return coord

    # --- end-of-window seam ---
    eow_coord = _shared_coord(end_of_window_targets)
    eow_coord._track_end_time = True
    eow_coord.automatic_control = True
    eow_coord._pipeline_has_active_override = MagicMock(return_value=False)
    eow_coord.async_refresh = AsyncMock()
    eow_coord.config_entry.options = {}
    eow_coord._compute_current_effective_default = MagicMock(
        return_value=(default_height, False)
    )
    eow_coord._check_sunset_window_transition = AsyncMock()

    async def _fire_closed(
        *, track_end_time, refresh_callback, on_window_open
    ):  # noqa: ARG001
        await refresh_callback()

    eow_coord._time_mgr = MagicMock()
    eow_coord._time_mgr.check_transition = _fire_closed

    await AdaptiveDataUpdateCoordinator._check_time_window_transition(
        eow_coord, dt.datetime.now(dt.UTC)
    )

    # --- auto-control-OFF seam ---
    off_coord = _shared_coord(auto_off_targets)
    off_coord.return_to_default_toggle = True
    off_coord.automatic_control = False
    off_coord.manager.manual_controlled = []
    off_coord.config_entry.options = {CONF_DEFAULT_HEIGHT: default_height}
    off_coord.async_refresh = AsyncMock()

    switch = object.__new__(AdaptiveCoverSwitch)
    switch.coordinator = off_coord
    switch._key = "automatic_control"
    switch._name = "test_switch"
    switch._initial_state = True
    switch.schedule_update_ha_state = MagicMock()

    await switch.async_turn_off()

    assert end_of_window_targets[entity] == auto_off_targets[entity]


# ---------------------------------------------------------------------------
# Issue #1376 audit MUST-FIX — the seam's newly-shared outside-window clamp
# (coordinator._clamp_to_outside_window_bounds, reached via
# _broadcast_default_position -> _resolve_broadcast_dispatch) has zero test
# coverage: every test above (and every other seam test across the suite)
# identity-patches it. These tests exercise the REAL clamp — a real
# PipelineSnapshot with a real active constraint, fed through the real
# ``_build_user_command_snapshot`` / ``_clamp_to_outside_window_bounds``.
#
# Investigation finding (see the run's report for the full trace): the
# hypothesis that ``enable_min_position`` / ``enable_max_position`` ("only
# during sun tracking" vs "always enforce") governs this clamp does not hold
# — that option never reaches ``pipeline.axis_constraints`` at all (grep
# confirms it) and this seam bypasses the calculation engine entirely, which
# is the only place that option is read. The constraint gathered here is a
# custom-position-slot MIN-mode floor (or the weather floor), whose OWN
# per-slot opt-in is ``outside_window`` (issue #943 item B) — and that flag
# only changes anything once the clock window is CLOSED. At clock_window_open
# =True (the "toggled off at midday" scenario), ``gather_axis_constraints``
# returns every active claim unfiltered BY DESIGN — the same rule a live user
# command's clamp and the end-of-window seam's own dark-gate-close case both
# already follow (coordinator.py's "in-window semantics for an in-window
# moment... keeps both cases correct without a branch"). So a floor clamping
# this seam at midday is the established, already-tested #943-item-B
# behaviour extended uniformly to a third caller, not a new divergent one —
# and narrowing it for just this seam would re-fork the two-seam duplication
# c7b244a5 was written to remove. These tests pin the CORRECT (clamped)
# behaviour rather than the hypothesis's predicted (unclamped) one; no
# production change was made for this finding.
# ---------------------------------------------------------------------------


def _make_min_floor(
    *, position: int = 30, outside_window: bool = False
) -> CustomPositionSensorState:
    """Build a single active MIN-mode custom-position floor, slot 1."""
    return CustomPositionSensorState(
        entity_ids=("binary_sensor.floor_trigger",),
        is_on=True,
        position=position,
        priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
        min_mode=True,
        use_my=False,
        slot=1,
        outside_window=outside_window,
    )


@pytest.mark.asyncio
async def test_return_to_default_is_clamped_by_an_active_floor_when_window_open() -> (
    None
):
    """#1376 audit MUST-FIX: the seam's real clamp is reached, not identity-stubbed.

    Auto control toggled off at midday (clock window genuinely open) with an
    active position floor of 30 and a configured default of 10. The floor
    must raise the dispatched value to 30 — the same #943-item-B rule the
    end-of-window broadcast and a live user command already follow when the
    window is open. Before c7b244a5 this seam had no clamp at all and would
    have sent the raw 10 regardless of the floor.
    """
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    snapshot = make_snapshot(
        clock_window_open=True,
        custom_position_sensors=[_make_min_floor(position=30)],
    )
    coord = _make_coord_with_real_cmd_svc(
        hass,
        default_height=10,
        current_position=50,
        constraint_snapshot=snapshot,
    )

    switch = object.__new__(AdaptiveCoverSwitch)
    switch.coordinator = coord
    switch._key = "automatic_control"
    switch._name = "test_switch"
    switch._initial_state = True
    switch.schedule_update_ha_state = MagicMock()

    with _patch_caps():
        await switch.async_turn_off()

    hass.services.async_call.assert_awaited()
    call_args = hass.services.async_call.await_args
    assert call_args.args[1] == "set_cover_position"
    assert call_args.args[2] == {"entity_id": "cover.test", "position": 30}


@pytest.mark.asyncio
async def test_return_to_default_floor_dropped_when_window_closed_and_not_opted_in() -> (
    None
):
    """#943-item-B parity: a non-opted-in floor stops binding once the window closes.

    Same floor as above, but the clock window is closed and the slot did NOT
    opt in (``outside_window=False``, the default). The gather drops it —
    matching the end-of-window seam's own behaviour for the identical
    option — so the configured default (10) reaches the cover unclamped.
    """
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    snapshot = make_snapshot(
        clock_window_open=False,
        custom_position_sensors=[_make_min_floor(position=30, outside_window=False)],
    )
    coord = _make_coord_with_real_cmd_svc(
        hass,
        default_height=10,
        current_position=50,
        constraint_snapshot=snapshot,
    )

    switch = object.__new__(AdaptiveCoverSwitch)
    switch.coordinator = coord
    switch._key = "automatic_control"
    switch._name = "test_switch"
    switch._initial_state = True
    switch.schedule_update_ha_state = MagicMock()

    with _patch_caps():
        await switch.async_turn_off()

    hass.services.async_call.assert_awaited()
    call_args = hass.services.async_call.await_args
    assert call_args.args[1] == "set_cover_position"
    assert call_args.args[2] == {"entity_id": "cover.test", "position": 10}


@pytest.mark.asyncio
async def test_return_to_default_floor_survives_closed_window_when_opted_in() -> None:
    """#943-item-B parity: an opted-in floor keeps binding once the window closes.

    Same shape as the "dropped" case above except ``outside_window=True`` —
    the per-slot opt-in that keeps a bounded claim binding past the clock
    (issue #943 item B). The end-of-window broadcast already honours this for
    the identical option; this seam must not diverge from it.
    """
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    snapshot = make_snapshot(
        clock_window_open=False,
        custom_position_sensors=[_make_min_floor(position=30, outside_window=True)],
    )
    coord = _make_coord_with_real_cmd_svc(
        hass,
        default_height=10,
        current_position=50,
        constraint_snapshot=snapshot,
    )

    switch = object.__new__(AdaptiveCoverSwitch)
    switch.coordinator = coord
    switch._key = "automatic_control"
    switch._name = "test_switch"
    switch._initial_state = True
    switch.schedule_update_ha_state = MagicMock()

    with _patch_caps():
        await switch.async_turn_off()

    hass.services.async_call.assert_awaited()
    call_args = hass.services.async_call.await_args
    assert call_args.args[1] == "set_cover_position"
    assert call_args.args[2] == {"entity_id": "cover.test", "position": 30}


@pytest.mark.asyncio
async def test_return_to_default_clamps_in_logical_frame_before_flipping_on_inverse_install() -> (
    None
):
    """BINDING_GUIDELINES §1: clamp in the logical frame, THEN flip — never the reverse.

    Reporter's exact shape (cover_awning, inverse_state=True) plus an active
    position floor of 30. ``AxisConstraint.low`` is pre-inversion canonical
    space by contract, so the clamp must raise the LOGICAL default (0 -> 30)
    before the seam flips it for the wire (30 -> 70 = 100 - 30). Flipping
    first (0 -> 100) and then applying the same bound number in wire space
    would leave 100 unclamped (100 > 30) and dispatch the wrong value — this
    is the discriminating case: clamp-then-flip gives 70, flip-then-clamp
    would give 100.
    """
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    snapshot = make_snapshot(
        clock_window_open=True,
        custom_position_sensors=[_make_min_floor(position=30)],
    )
    coord = _make_coord_with_real_cmd_svc(
        hass,
        cover_type="cover_awning",
        inverse_state=True,
        default_height=0,
        current_position=50,
        constraint_snapshot=snapshot,
    )

    switch = object.__new__(AdaptiveCoverSwitch)
    switch.coordinator = coord
    switch._key = "automatic_control"
    switch._name = "test_switch"
    switch._initial_state = True
    switch.schedule_update_ha_state = MagicMock()

    with _patch_caps(has_set_position=True, has_open=True, has_close=True):
        await switch.async_turn_off()

    hass.services.async_call.assert_awaited()
    call_args = hass.services.async_call.await_args
    assert call_args.args[1] == "set_cover_position"
    assert call_args.args[2] == {"entity_id": "cover.test", "position": 70}
