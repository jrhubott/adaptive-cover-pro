"""Tests for ``Coordinator.async_apply_user_position`` shared helper.

This helper is the single delegation point for any user-initiated cover
position command (the ``set_position`` integration service, the opt-in
proxy cover entity, future external triggers). It owns the min-mode floor
clamp, the pipeline preemption check, manual-override engagement, and the
``apply_position`` dispatch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_INTERP,
    CONF_INTERP_LIST,
    CONF_INTERP_LIST_NEW,
    CONF_INVERSE_STATE,
    CONF_MANUAL_OVERRIDE_PRIORITY,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.manual_override import (
    ManualOverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.weather import (
    WeatherOverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
    DecisionStep,
    PipelineResult,
)
from tests._helpers.priorities import ABOVE_HOLDER
from tests.ha_helpers import (
    bind_user_position_seam,
    make_mock_policy,
    wire_dispatch_frame,
)


def _slot(
    pos: int,
    *,
    is_on: bool,
    min_mode: bool,
    priority: int = DEFAULT_CUSTOM_POSITION_PRIORITY,
) -> CustomPositionSensorState:
    return CustomPositionSensorState(
        entity_ids=(f"binary_sensor.slot_{pos}",),
        is_on=is_on,
        position=pos,
        priority=priority,
        min_mode=min_mode,
        use_my=False,
    )


def _pipeline_result_with_winner(
    handler_name: str, priority: int, position: int = 50
) -> PipelineResult:
    """Build a synthetic ``PipelineResult`` whose decision_trace flags ``handler_name`` as the matched winner."""
    from custom_components.adaptive_cover_pro.const import ControlMethod

    return PipelineResult(
        position=position,
        control_method=ControlMethod.SOLAR,
        reason="test",
        decision_trace=[
            DecisionStep(
                handler=handler_name, matched=True, reason="test", position=position
            )
        ],
    )


def _make_coord(
    custom_states,
    *,
    default_options=None,
    winner_name: str = "solar",
    winner_priority: int = 40,
    winner_handler_instance=None,
    weather_override_active: bool = False,
    weather_override_position: int = 0,
    weather_override_min_mode: bool = False,
):
    """Build a coordinator-shaped mock that exposes async_apply_user_position.

    We import the *real* method off the Coordinator class and bind it onto a
    MagicMock so we can drive it without a full HA setup. By default the
    pipeline mock returns ``solar`` (priority 40) as the winner — strictly
    less than ``ManualOverrideHandler.priority`` (80), so the preemption
    check passes through to dispatch.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from tests.test_pipeline.conftest import make_snapshot

    coord = MagicMock(spec=AdaptiveDataUpdateCoordinator)
    coord.config_entry = MagicMock()
    entry_opts = default_options if default_options is not None else {}
    coord.config_entry.options = entry_opts
    wire_dispatch_frame(coord, entry_opts)
    # After fix #643, async_apply_user_position falls back to
    # _resolved_options (not config_entry.options).  Initialise it to the
    # same dict so existing tests keep working; specific tests that want to
    # exercise the resolved-vs-raw distinction set this attribute themselves.
    coord._resolved_options = entry_opts
    # PipelineSnapshotBuilder mock — Phase D moved HA reads + snapshot
    # assembly onto this collaborator.  Both call-sites of
    # async_apply_user_position route through it.
    coord._snapshot_builder = MagicMock()
    coord._snapshot_builder.read_custom_position_sensors.return_value = custom_states
    snapshot = make_snapshot(
        custom_position_sensors=custom_states or [],
        weather_override_active=weather_override_active,
        weather_override_position=weather_override_position,
        weather_override_min_mode=weather_override_min_mode,
    )
    coord._snapshot_builder.build = MagicMock(return_value=snapshot)
    # Coordinator state that the builder call needs as keyword args.  These
    # are instance attributes (set in __init__) so they aren't on the spec'd
    # MagicMock by default — preset them explicitly.
    coord._cover_data = MagicMock(name="cover_data")
    coord._cover_type = "cover_blind"
    coord._weather_readings = None
    # Cloud-suppression manager (issue #864): the ad-hoc build reads its
    # resolved bool as a pure property. Preset so the spec'd mock doesn't raise.
    coord._cloud_mgr = MagicMock()
    coord._climate_smoothing_mgr = MagicMock()
    coord._climate_smoothing_mgr.resolved_flags = None
    ctx = MagicMock(name="position_context")
    coord._build_position_context.return_value = ctx
    coord._cmd_svc = MagicMock()
    coord._cmd_svc.apply_position = AsyncMock(
        return_value=("sent", "set_cover_position")
    )
    coord._cmd_svc.record_preempted_skip = MagicMock()

    # Pipeline mock: returns a synthetic result with the named handler as winner.
    coord._pipeline = MagicMock()
    coord._pipeline.evaluate.return_value = _pipeline_result_with_winner(
        winner_name, winner_priority
    )

    # Handler lookup so the helper can resolve priority from the winner step.
    if winner_handler_instance is not None:
        coord._handler_by_name = {winner_name: winner_handler_instance}
    else:
        handler = MagicMock()
        handler.priority = winner_priority
        coord._handler_by_name = {winner_name: handler}

    # Manager for manual-override engagement.
    coord.manager = MagicMock()
    coord.manager.mark_user_command = MagicMock()

    # Bind the real method
    bind_user_position_seam(coord)
    return coord, ctx


@pytest.mark.asyncio
async def test_async_apply_user_position_clamps_to_min_mode_floor() -> None:
    """Requested < highest active min-mode floor → clamped up to floor."""
    # Re-anchored for #472: a floor must be > ManualOverrideHandler.priority (80)
    # to clamp a user move.
    coord, ctx = _make_coord(
        [_slot(40, is_on=True, min_mode=True, priority=ABOVE_HOLDER)]
    )

    await coord.async_apply_user_position("cover.test", 10, trigger="set_position")

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 40, "set_position", ctx
    )


@pytest.mark.asyncio
async def test_async_apply_user_position_passes_above_floor_unchanged() -> None:
    """Requested > floor → passes through unchanged."""
    coord, ctx = _make_coord([_slot(40, is_on=True, min_mode=True)])

    await coord.async_apply_user_position("cover.test", 80, trigger="proxy_managed")

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 80, "proxy_managed", ctx
    )


@pytest.mark.asyncio
async def test_async_apply_user_position_no_floors_uses_requested() -> None:
    """No active min-mode slots → requested value passes through."""
    coord, ctx = _make_coord(
        [_slot(80, is_on=True, min_mode=False), _slot(20, is_on=False, min_mode=True)]
    )

    await coord.async_apply_user_position("cover.test", 5, trigger="set_position")

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 5, "set_position", ctx
    )


@pytest.mark.asyncio
async def test_async_apply_user_position_uses_force_context() -> None:
    """_build_position_context must be called with force=True."""
    coord, _ctx = _make_coord([])
    await coord.async_apply_user_position("cover.test", 50, trigger="proxy_open")

    coord._build_position_context.assert_called_once()
    _, kwargs = coord._build_position_context.call_args
    assert kwargs.get("force") is True


@pytest.mark.asyncio
async def test_async_apply_user_position_accepts_trigger_label() -> None:
    """The trigger label is forwarded verbatim to ``apply_position``."""
    coord, ctx = _make_coord([])
    await coord.async_apply_user_position("cover.test", 33, trigger="proxy_tilt")
    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 33, "proxy_tilt", ctx
    )


@pytest.mark.asyncio
async def test_async_apply_user_position_uses_passed_options_when_provided() -> None:
    """When ``options`` is passed, it overrides ``self.config_entry.options``."""
    entry_options = {"from": "entry"}
    coord, _ctx = _make_coord(
        [_slot(70, is_on=True, min_mode=True)], default_options=entry_options
    )
    custom_options = {"from": "override"}

    await coord.async_apply_user_position(
        "cover.test", 10, trigger="set_position", options=custom_options
    )

    coord._snapshot_builder.build.assert_called_once()
    args, kwargs = coord._snapshot_builder.build.call_args
    # First positional arg of build() is the options dict.
    assert args[0] is custom_options or kwargs.get("opts") is custom_options
    # And the override flowed into _build_position_context too
    args, kwargs = coord._build_position_context.call_args
    # signature: (entity, options, *, force=...)
    assert args[1] is custom_options or kwargs.get("options") is custom_options


# ---------------------------------------------------------------------------
# New contract: pipeline preemption check + manual-override engagement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_engages_manual_override_when_no_preemption() -> None:
    """Default path: solar winner (40) < ManualOverride priority → command dispatched and manual override engaged."""
    coord, ctx = _make_coord([], winner_name="solar", winner_priority=40)

    outcome = await coord.async_apply_user_position(
        "cover.test", 50, trigger="proxy_managed"
    )

    coord.manager.mark_user_command.assert_called_once_with(
        "cover.test", reason="proxy_managed"
    )
    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 50, "proxy_managed", ctx
    )
    assert outcome == ("sent", "set_cover_position")


@pytest.mark.asyncio
async def test_default_skipped_when_safety_custom_position_active() -> None:
    """Safety custom position (priority 100) wins → command dropped, manual override not engaged."""
    coord, _ctx = _make_coord([], winner_name="custom_position_5", winner_priority=100)

    outcome = await coord.async_apply_user_position(
        "cover.test", 50, trigger="proxy_managed"
    )

    assert outcome == ("skipped", "preempted_by_custom_position_5")
    coord._cmd_svc.apply_position.assert_not_awaited()
    coord.manager.mark_user_command.assert_not_called()
    coord._cmd_svc.record_preempted_skip.assert_called_once_with(
        "cover.test", 50, trigger="proxy_managed", winner_name="custom_position_5"
    )


@pytest.mark.asyncio
async def test_default_skipped_when_weather_active() -> None:
    """Weather (90) wins → command dropped, manual override not engaged."""
    coord, _ctx = _make_coord([], winner_name="weather", winner_priority=90)

    outcome = await coord.async_apply_user_position(
        "cover.test", 30, trigger="set_position"
    )

    assert outcome == ("skipped", "preempted_by_weather")
    coord._cmd_svc.apply_position.assert_not_awaited()
    coord.manager.mark_user_command.assert_not_called()
    coord._cmd_svc.record_preempted_skip.assert_called_once_with(
        "cover.test", 30, trigger="set_position", winner_name="weather"
    )


@pytest.mark.asyncio
async def test_default_not_blocked_by_cloud_suppression() -> None:
    """cloud_suppression (60) does NOT preempt — command dispatched + manual override engaged."""
    coord, ctx = _make_coord([], winner_name="cloud_suppression", winner_priority=60)

    await coord.async_apply_user_position("cover.test", 50, trigger="proxy_managed")

    coord.manager.mark_user_command.assert_called_once_with(
        "cover.test", reason="proxy_managed"
    )
    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 50, "proxy_managed", ctx
    )


@pytest.mark.asyncio
async def test_default_not_blocked_by_solar() -> None:
    """Solar (40) does NOT preempt — command dispatched + manual override engaged."""
    coord, ctx = _make_coord([], winner_name="solar", winner_priority=40)

    await coord.async_apply_user_position("cover.test", 50, trigger="proxy_managed")

    coord.manager.mark_user_command.assert_called_once_with(
        "cover.test", reason="proxy_managed"
    )
    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 50, "proxy_managed", ctx
    )


@pytest.mark.asyncio
async def test_force_true_bypasses_pipeline_check() -> None:
    """force=True with safety custom-position winner → command still dispatches."""
    coord, ctx = _make_coord([], winner_name="custom_position_5", winner_priority=100)

    outcome = await coord.async_apply_user_position(
        "cover.test", 50, trigger="set_position", force=True
    )

    assert outcome == ("sent", "set_cover_position")
    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 50, "set_position", ctx
    )
    # Pipeline must NOT have been consulted on the force=True path
    coord._pipeline.evaluate.assert_not_called()
    coord._cmd_svc.record_preempted_skip.assert_not_called()


@pytest.mark.asyncio
async def test_force_true_does_not_engage_manual_override() -> None:
    """force=True must NOT call mark_user_command, regardless of pipeline winner."""
    coord, _ctx = _make_coord([], winner_name="solar", winner_priority=40)

    await coord.async_apply_user_position(
        "cover.test", 50, trigger="set_position", force=True
    )

    coord.manager.mark_user_command.assert_not_called()


@pytest.mark.asyncio
async def test_snapshot_passed_to_pipeline_has_manual_override_false() -> None:
    """Meta-bug guard: preemption snapshot must force manual_override_active=False.

    Otherwise a stale manual_control flag would self-claim priority 80 and
    let the cover move anyway.
    """
    coord, _ctx = _make_coord([], winner_name="solar", winner_priority=40)
    coord.manager.binary_cover_manual = True  # stale flag set

    await coord.async_apply_user_position("cover.test", 50, trigger="proxy_managed")

    coord._snapshot_builder.build.assert_called_once()
    _, kwargs = coord._snapshot_builder.build.call_args
    assert kwargs.get("manual_override_active") is False


@pytest.mark.asyncio
async def test_preempted_skip_recorded_in_last_skipped_action() -> None:
    """When preempted, record_preempted_skip is called with the winner name."""
    coord, _ctx = _make_coord([], winner_name="custom_position_5", winner_priority=100)

    await coord.async_apply_user_position("cover.test", 42, trigger="proxy_managed")

    coord._cmd_svc.record_preempted_skip.assert_called_once_with(
        "cover.test", 42, trigger="proxy_managed", winner_name="custom_position_5"
    )


def test_manual_override_priority_constant_unchanged() -> None:
    """Locks in ManualOverrideHandler.priority=80 so the preemption cutoff stays correct."""
    assert ManualOverrideHandler.priority == 80


# ---------------------------------------------------------------------------
# Custom-position min-mode floor interaction with async_apply_user_position
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_position_min_mode_does_not_preempt_request_above_floor() -> None:
    """When a min-mode floor is active and the user requests a position at or
    above the floor, the command must NOT be preempted — floors defer to the
    floor-clamp composition pass (#463), they no longer act as priority winners.
    """
    state = CustomPositionSensorState(
        entity_ids=("binary_sensor.cp1",),
        is_on=True,
        position=60,
        priority=95,
        min_mode=True,
        use_my=False,
    )
    coord, ctx = _make_coord(
        [state],
        winner_name="solar",
        winner_priority=40,
    )

    outcome = await coord.async_apply_user_position(
        "cover.test", 90, trigger="set_position"
    )

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 90, "set_position", ctx
    )
    coord._cmd_svc.record_preempted_skip.assert_not_called()
    assert outcome == ("sent", "set_cover_position")


@pytest.mark.asyncio
async def test_custom_position_min_mode_clamps_and_dispatches_request_below_floor() -> (
    None
):
    """When a min-mode floor is active and the user requests below the floor,
    the command is clamped to the floor and dispatched — NOT preempted.
    """
    state = CustomPositionSensorState(
        entity_ids=("binary_sensor.cp1",),
        is_on=True,
        position=60,
        priority=95,
        min_mode=True,
        use_my=False,
    )
    coord, ctx = _make_coord(
        [state],
        winner_name="solar",
        winner_priority=40,
    )

    outcome = await coord.async_apply_user_position(
        "cover.test", 40, trigger="set_position"
    )

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 60, "set_position", ctx
    )
    coord._cmd_svc.record_preempted_skip.assert_not_called()
    assert outcome == ("sent", "set_cover_position")


@pytest.mark.asyncio
async def test_custom_position_exact_mode_still_preempts() -> None:
    """When the winning custom-position handler is NOT in min-mode, it preempts
    as before — the new exception must not fire.
    """
    handler = CustomPositionHandler(slot=1, position=60, priority=95)
    state = CustomPositionSensorState(
        entity_ids=("binary_sensor.cp1",),
        is_on=True,
        position=60,
        priority=95,
        min_mode=False,
        use_my=False,
        slot=1,
    )
    coord, _ctx = _make_coord(
        [state],
        winner_name="custom_position_1",
        winner_priority=95,
        winner_handler_instance=handler,
    )
    coord._pipeline.evaluate.return_value = _pipeline_result_with_winner(
        "custom_position_1", 95, position=60
    )

    outcome = await coord.async_apply_user_position(
        "cover.test", 70, trigger="set_position"
    )

    assert outcome == ("skipped", "preempted_by_custom_position_1")
    coord._cmd_svc.record_preempted_skip.assert_called_once()
    coord._cmd_svc.apply_position.assert_not_awaited()


@pytest.mark.asyncio
async def test_custom_position_min_mode_preempts_when_request_below_floor_and_handler_is_different_slot() -> (
    None
):
    """A real-position custom-position handler (different slot) wins with
    priority 95 > 80 → preempts. The floor clamp on the requested value
    is irrelevant once preemption fires.
    """
    handler = CustomPositionHandler(slot=2, position=60, priority=95)
    # Only slot 1 is the min-mode floor — slot 2 is the priority-95 winner.
    state = CustomPositionSensorState(
        entity_ids=("binary_sensor.cp1",),
        is_on=True,
        position=60,
        priority=95,
        min_mode=True,
        use_my=False,
    )
    coord, _ctx = _make_coord(
        [state],
        winner_name="custom_position_2",
        winner_priority=95,
        winner_handler_instance=handler,
    )
    coord._pipeline.evaluate.return_value = _pipeline_result_with_winner(
        "custom_position_2", 95, position=60
    )

    outcome = await coord.async_apply_user_position(
        "cover.test", 90, trigger="set_position"
    )

    assert outcome == ("skipped", "preempted_by_custom_position_2")
    coord._cmd_svc.record_preempted_skip.assert_called_once()
    coord._cmd_svc.apply_position.assert_not_awaited()


# ---------------------------------------------------------------------------
# Issue #643: async_apply_user_position must use resolved options, not raw
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_user_position_renders_string_thresholds() -> None:
    """apply_user_position must pass _resolved_options (floats) to build(), not
    config_entry.options (raw strings).

    Before the fix, line 2094 used ``self.config_entry.options`` as the
    fallback, so a plain numeric string such as ``temp_low="21"`` flowed into
    ClimateOptions.temp_low and then into ``is_winter``'s float < str
    comparison → TypeError. After the fix, ``_resolved_options`` (already
    float-normalized) is used instead.

    This test confirms the contract: build() receives the float-resolved dict,
    not the raw-string entry options, when options=None (the default path for
    all user-action callers: set_position service, cover.py, button.py).
    """
    raw_entry_options = {CONF_TEMP_LOW: "21", CONF_TEMP_HIGH: "25"}
    resolved_options = {CONF_TEMP_LOW: 21.0, CONF_TEMP_HIGH: 25.0}

    coord, _ctx = _make_coord([], default_options=raw_entry_options)
    # Simulate coordinator having already resolved options (normal update cycle).
    coord._resolved_options = resolved_options

    await coord.async_apply_user_position("cover.test", 50, trigger="set_position")

    # build() must have been called with the float-resolved dict, NOT the raw entry opts.
    coord._snapshot_builder.build.assert_called_once()
    call_args, _call_kwargs = coord._snapshot_builder.build.call_args
    opts_passed = call_args[0]
    assert opts_passed is resolved_options, (
        f"build() was called with {opts_passed!r} (raw config_entry.options) "
        f"instead of the float-resolved _resolved_options {resolved_options!r}. "
        "async_apply_user_position must fall back to self._resolved_options, not "
        "self.config_entry.options."
    )


@pytest.mark.asyncio
async def test_apply_user_position_explicit_options_not_overridden_by_resolved() -> (
    None
):
    """When options is supplied explicitly, it takes priority over _resolved_options.

    This is a regression guard: the fix to issue #643 must not accidentally
    discard the ``options`` kwarg that callers supply. The existing contract
    (explicit options win) must be preserved.
    """
    raw_entry_options = {CONF_TEMP_LOW: "21"}
    resolved_options = {CONF_TEMP_LOW: 21.0}
    explicit_options = {CONF_TEMP_LOW: 18.0, "from": "explicit"}

    coord, _ctx = _make_coord([], default_options=raw_entry_options)
    coord._resolved_options = resolved_options

    await coord.async_apply_user_position(
        "cover.test", 50, trigger="set_position", options=explicit_options
    )

    coord._snapshot_builder.build.assert_called_once()
    call_args, _call_kwargs = coord._snapshot_builder.build.call_args
    opts_passed = call_args[0]
    assert (
        opts_passed is explicit_options
    ), "build() must use the explicitly supplied options when options != None."


# ---------------------------------------------------------------------------
# async_apply_user_tilt — issue #684 dedicated tilt-axis entry point
# ---------------------------------------------------------------------------


def _make_tilt_coord(*, policy):
    """Build a coordinator-shaped mock that exposes the real ``async_apply_user_tilt``.

    Binds the real method off the class so the manual-override engagement and
    the policy-hook dispatch / position fall-back can be exercised against
    mocked collaborators.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = MagicMock(spec=AdaptiveDataUpdateCoordinator)
    coord._policy = policy
    coord.manager = MagicMock()
    coord.manager.mark_user_command = MagicMock()
    coord.async_apply_user_position = AsyncMock(return_value=("sent", "fallback"))
    coord.async_apply_user_tilt = (
        AdaptiveDataUpdateCoordinator.async_apply_user_tilt.__get__(coord)
    )
    return coord


@pytest.mark.asyncio
async def test_async_apply_user_tilt_engages_override_and_delegates_to_policy() -> None:
    """Venetian: the tilt entry point engages manual override and delegates to the policy."""
    policy = make_mock_policy()
    policy.apply_user_tilt = AsyncMock(return_value=True)
    coord = _make_tilt_coord(policy=policy)

    outcome = await coord.async_apply_user_tilt(
        "cover.venetian", 10, trigger="proxy_tilt"
    )

    coord.manager.mark_user_command.assert_called_once_with(
        "cover.venetian", reason="proxy_tilt"
    )
    policy.apply_user_tilt.assert_awaited_once_with(
        "cover.venetian", tilt=10, reason="proxy_tilt"
    )
    coord.async_apply_user_position.assert_not_awaited()
    assert outcome == ("sent", "")


@pytest.mark.asyncio
async def test_async_apply_user_tilt_falls_back_to_position_for_non_venetian() -> None:
    """Non-venetian: the base hook returns False → fall back to async_apply_user_position."""
    from custom_components.adaptive_cover_pro.cover_types import BlindPolicy

    coord = _make_tilt_coord(policy=BlindPolicy())

    outcome = await coord.async_apply_user_tilt("cover.blind", 33, trigger="proxy_tilt")

    coord.async_apply_user_position.assert_awaited_once_with(
        "cover.blind", 33, trigger="proxy_tilt", force=False
    )
    assert outcome == ("sent", "fallback")


@pytest.mark.asyncio
async def test_async_apply_user_tilt_force_false_engages_manual_override() -> None:
    """force=False (default) engages manual override before delegating."""
    from custom_components.adaptive_cover_pro.cover_types import BlindPolicy

    coord = _make_tilt_coord(policy=BlindPolicy())

    await coord.async_apply_user_tilt("cover.blind", 33, trigger="set_tilt")

    coord.manager.mark_user_command.assert_called_once_with(
        "cover.blind", reason="set_tilt"
    )


@pytest.mark.asyncio
async def test_async_apply_user_tilt_force_true_skips_override_and_threads_force() -> (
    None
):
    """force=True does NOT engage manual override and threads force into the
    non-venetian position fallback.
    """
    from custom_components.adaptive_cover_pro.cover_types import BlindPolicy

    coord = _make_tilt_coord(policy=BlindPolicy())

    await coord.async_apply_user_tilt("cover.blind", 33, trigger="set_tilt", force=True)

    coord.manager.mark_user_command.assert_not_called()
    coord.async_apply_user_position.assert_awaited_once_with(
        "cover.blind", 33, trigger="set_tilt", force=True
    )


# ---------------------------------------------------------------------------
# Issue #1027: a user-supplied position is logical and must be mapped into the
# cover's dispatch frame at the same boundary the automatic path uses.
# ---------------------------------------------------------------------------

_INTERP_OPTIONS = {
    CONF_INTERP: True,
    CONF_INTERP_LIST: [0, 25, 58, 100],
    CONF_INTERP_LIST_NEW: [0, 45, 58, 100],
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("logical", "dispatched"),
    [(0, 100), (30, 70), (100, 0)],
)
async def test_user_position_inverse_state_dispatches_inverted(
    logical: int, dispatched: int
) -> None:
    """``inverse_state`` on → the user's logical value is inverted before dispatch.

    Issue #1027: the automatic path inverts (``coordinator.state``) while the
    user path dispatched raw, so the same logical number drove the cover in
    opposite directions depending on which path produced it.
    """
    coord, ctx = _make_coord([], default_options={CONF_INVERSE_STATE: True})

    await coord.async_apply_user_position("cover.test", logical, trigger="set_position")

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", dispatched, "set_position", ctx
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("linear", "motor"),
    [(25, 45), (0, 0), (100, 100)],
)
async def test_user_position_interpolation_dispatches_motor_value(
    linear: int, motor: int
) -> None:
    """Interpolation configured → the user's linear value maps onto the motor curve."""
    coord, ctx = _make_coord([], default_options=dict(_INTERP_OPTIONS))

    await coord.async_apply_user_position("cover.test", linear, trigger="set_position")

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", motor, "set_position", ctx
    )


@pytest.mark.asyncio
async def test_user_position_inverse_with_interpolation_interpolates_only() -> None:
    """Both configured → interpolation wins, no inversion, and the info log fires.

    Mutual exclusion mirrors ``coordinator.state`` exactly: the combination is
    unsupported, logged, and resolves to interpolation alone.
    """
    coord, ctx = _make_coord(
        [], default_options={CONF_INVERSE_STATE: True, **_INTERP_OPTIONS}
    )

    await coord.async_apply_user_position("cover.test", 25, trigger="set_position")

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 45, "set_position", ctx
    )
    logged = [str(c.args[0]) for c in coord.logger.info.call_args_list]
    assert any(
        "inverse" in msg.lower() and "interpolation" in msg.lower() for msg in logged
    ), f"expected the unsupported-combination info log, got {logged}"


@pytest.mark.asyncio
async def test_user_position_floor_clamped_runs_the_frame_transform() -> None:
    """A floor that raised the request still runs the frame transform (#1036).

    The requirement is the one #469/#1027 actually needed: the same configured
    floor must dispatch identically on the user and automatic paths. That now
    holds because BOTH transform, not because both skip. A user-typed floor is a
    logical value (0 = closed, 100 = open), so on an inverse install the raised
    request of 25 reaches the cover as wire 75 — under the old carve-out it
    dispatched 25, driving a "minimum 25% open" floor to 75% open.
    """
    coord, ctx = _make_coord(
        [_slot(25, is_on=True, min_mode=True, priority=82)],
        default_options={CONF_INVERSE_STATE: True},
    )

    await coord.async_apply_user_position("cover.test", 10, trigger="set_position")

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 75, "set_position", ctx
    )


@pytest.mark.asyncio
async def test_user_position_untransformed_when_neither_configured() -> None:
    """Neither inverse nor interpolation → the logical value dispatches unchanged."""
    coord, ctx = _make_coord([], default_options={})

    await coord.async_apply_user_position("cover.test", 30, trigger="set_position")

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 30, "set_position", ctx
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "requested", "dispatched", "expected_inverted", "expected_interp"),
    [
        ({CONF_INVERSE_STATE: True}, 30, 70, True, False),
        (dict(_INTERP_OPTIONS), 25, 45, False, True),
        ({}, 30, 30, False, False),
    ],
)
async def test_user_position_routes_through_entity_target_with_explicit_frame(
    options: dict,
    requested: int,
    dispatched: int,
    expected_inverted: bool,
    expected_interp: bool,
) -> None:
    """The per-entity remap runs, and is told the full frame it is handed.

    Issue #993: a seam dispatching off the main pipeline's cycle must name its
    space explicitly — ``None`` would make a dual-panel / day-night policy
    resolve against a flag cached for a different value. Issue #1027: naming
    only the *inversion* is not enough, because "interpolated, not inverted"
    and "neither" are different frames that both read as ``inverted=False``.
    """
    coord, _ctx = _make_coord([], default_options=options)
    policy = make_mock_policy()
    policy.resolve_entity_target = MagicMock(return_value=dispatched)
    coord._policy = policy

    await coord.async_apply_user_position(
        "cover.test", requested, trigger="set_position"
    )

    policy.resolve_entity_target.assert_called_once_with(
        "cover.test",
        dispatched,
        inverted=expected_inverted,
        interpolated=expected_interp,
    )


@pytest.mark.asyncio
async def test_user_position_entity_target_frame_names_both_halves_after_a_floor_raise() -> (
    None
):
    """A floor-raised dispatch names both halves of its frame, never ``None``.

    The requirement (#1027) is unchanged: the seam must state the frame the
    value is actually in, and ``inverted`` alone cannot express "interpolated,
    not inverted". What changed is the frame itself — a floor raise no longer
    skips the transform (#1036), so with inverse + interpolation configured
    (interpolation suppresses inversion) the raised 25 is calibrated to 45 and
    the seam is told ``inverted=False, interpolated=True``.
    """
    coord, _ctx = _make_coord(
        [_slot(25, is_on=True, min_mode=True, priority=82)],
        default_options={CONF_INVERSE_STATE: True, **_INTERP_OPTIONS},
    )
    policy = make_mock_policy()
    policy.resolve_entity_target = MagicMock(return_value=45)
    coord._policy = policy

    await coord.async_apply_user_position("cover.test", 10, trigger="set_position")

    policy.resolve_entity_target.assert_called_once_with(
        "cover.test", 45, inverted=False, interpolated=True
    )


# ---------------------------------------------------------------------------
# Issue #1170 — one predicate, sourced from the live handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outranking_floor_applies_even_when_a_lower_one_is_higher() -> None:
    """Filter by priority BEFORE composing, not after (#1170).

    ``effective_floor`` takes the max across every active floor and the gate
    then asked whether *that* floor outranks manual override. A sub-priority
    slot with the higher position therefore won the max, failed the gate, and
    took a legitimately-outranking lower floor down with it — so no clamp
    applied at all.
    """
    coord, ctx = _make_coord(
        [
            _slot(60, is_on=True, min_mode=True),  # 77 — yields to the user
            _slot(
                50, is_on=True, min_mode=True, priority=WeatherOverrideHandler.priority
            ),  # 90 — outranks
        ]
    )

    await coord.async_apply_user_position("cover.test", 10, trigger="set_position")

    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 50, "set_position", ctx
    )


@pytest.mark.asyncio
async def test_clamp_respects_a_reconfigured_manual_override_priority() -> None:
    """The threshold is the handler's EFFECTIVE priority, not the class default.

    Manual Override's priority is configurable per cover — the 🔀 Handler
    Priorities step writes ``manual_override_priority``. Reading
    ``ManualOverrideHandler.priority`` off the class always returns 80 and
    silently ignores that setting.
    """
    coord, ctx = _make_coord(
        [_slot(40, is_on=True, min_mode=True, priority=ABOVE_HOLDER)],
        default_options={CONF_MANUAL_OVERRIDE_PRIORITY: ABOVE_HOLDER + 3},
    )

    await coord.async_apply_user_position("cover.test", 10, trigger="set_position")

    # The floor no longer outranks the raised threshold, so it yields and the
    # user's 10 passes through untouched.
    coord._cmd_svc.apply_position.assert_awaited_once_with(
        "cover.test", 10, "set_position", ctx
    )
