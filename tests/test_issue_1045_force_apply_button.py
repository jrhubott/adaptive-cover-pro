"""Tests for issue #1045: Apply Calculated Position button.

A per-config-entry button that recomputes the pipeline position and
force-dispatches it past the ``delta_position`` / ``delta_time`` gates.

What it bypasses
----------------
* ``delta_position`` and ``delta_time`` (via the existing ``PositionContext.force``)
* the ``auto_control`` gate — an explicit press is a user command (#430 precedent),
  reached via ``bypass_auto_control``, never ``is_safety`` (which would tag the
  target and regress #215/#216)
* the clock window — same argument as ``auto_control``

What it must NOT bypass
-----------------------
* the ``_enabled`` master kill switch
* the cover-unavailable boundary (#342)
* the same-position / relay-click short-circuit (#290/#507/#567/#779)

It also skips covers under a live manual override, pre-filtering them rather
than leaning on the manual-override gate that ``force=True`` disables.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro import const
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
    PositionContext,
)
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

# ---------------------------------------------------------------------------
# Harness — mirrors tests/test_reset_button_time_window.py::_make_coordinator
# ---------------------------------------------------------------------------


def _make_coordinator(
    *,
    automatic_control=True,
    clock_window_open=True,
    entities=None,
    manual_covers=(),
):
    """Minimal coordinator mock for _async_force_send_pipeline_position tests."""
    coordinator = MagicMock()
    coordinator.clock_window_open = clock_window_open
    coordinator.automatic_control = automatic_control
    coordinator.entities = list(entities) if entities is not None else ["cover.test"]
    coordinator.logger = MagicMock()
    coordinator._check_sun_validity_transition.return_value = False
    coordinator.manager.is_cover_manual.side_effect = (
        lambda cover: cover in manual_covers
    )
    coordinator._build_position_context.return_value = PositionContext(
        auto_control=True,
        manual_override=False,
        sun_just_appeared=False,
        min_change=2,
        time_threshold=2,
        special_positions=[0, 100],
        inverse_state=False,
        force=True,
    )
    coordinator._cmd_svc.apply_position = AsyncMock(
        return_value=("sent", "set_cover_position")
    )
    # The per-entity dispatch seam is identity for a blind (no rail remap).
    coordinator._entity_target = lambda cover, state: state
    # No hold in play by default; bind the real hold-aware dispatch seam so
    # honor_holds=True runs the production code rather than a bare MagicMock.
    coordinator._pipeline_result = None
    coordinator.position_axis_inverted = False
    coordinator._dispatch_to_cover = (
        AdaptiveDataUpdateCoordinator._dispatch_to_cover.__get__(coordinator)
    )
    return coordinator


def _force_send(coordinator, state=50, options=None, **kwargs):
    """Invoke the unbound shared force-dispatch helper."""
    return AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position(
        coordinator, state, {} if options is None else options, **kwargs
    )


def _context_entities(coordinator) -> list[str]:
    """Entity ids that _build_position_context was called for."""
    return [call.args[0] for call in coordinator._build_position_context.call_args_list]


# ---------------------------------------------------------------------------
# Step 3 — bypass_auto_control parameter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_send_respects_auto_control_by_default():
    """Safe-default lock: without the kwarg, auto-control OFF still blocks."""
    coordinator = _make_coordinator(automatic_control=False)

    result = await _force_send(coordinator)

    coordinator._cmd_svc.apply_position.assert_not_called()
    assert result == set()


@pytest.mark.asyncio
async def test_force_send_bypasses_auto_control_when_requested():
    """bypass_auto_control=True must dispatch even with auto-control OFF."""
    coordinator = _make_coordinator(automatic_control=False)

    result = await _force_send(coordinator, bypass_auto_control=True)

    coordinator._cmd_svc.apply_position.assert_called_once()
    assert result == {"cover.test"}


@pytest.mark.asyncio
async def test_force_send_threads_bypass_flag_into_position_context():
    """The coordinator guard and the context flag must move together.

    Skipping only the coordinator-level guard would leave apply_position
    skipping the command with reason ``auto_control_off``.
    """
    coordinator = _make_coordinator(automatic_control=False)

    await _force_send(coordinator, bypass_auto_control=True)

    coordinator._build_position_context.assert_called_once()
    assert (
        coordinator._build_position_context.call_args.kwargs["bypass_auto_control"]
        is True
    )


# ---------------------------------------------------------------------------
# Step 4 — ignore_clock_window parameter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_send_respects_clock_window_by_default():
    """Safe-default lock: without the kwarg, a closed clock window still blocks."""
    coordinator = _make_coordinator(clock_window_open=False)

    result = await _force_send(coordinator)

    coordinator._cmd_svc.apply_position.assert_not_called()
    assert result == set()


@pytest.mark.asyncio
async def test_force_send_ignores_clock_window_when_requested():
    """ignore_clock_window=True must dispatch outside the active-hours window."""
    coordinator = _make_coordinator(clock_window_open=False)

    result = await _force_send(coordinator, ignore_clock_window=True)

    coordinator._cmd_svc.apply_position.assert_called_once()
    assert result == {"cover.test"}


# ---------------------------------------------------------------------------
# Step 5 — respect_manual_override parameter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_send_commands_through_manual_override_by_default():
    """Safe-default lock: the auto-expiry caller still commands every cover.

    ``force=True`` disables the manual-override gate inside apply_position;
    without the new kwarg that pre-existing behaviour must be unchanged.
    """
    coordinator = _make_coordinator(
        entities=["cover.a", "cover.b"], manual_covers=("cover.a",)
    )

    result = await _force_send(coordinator)

    assert coordinator._cmd_svc.apply_position.call_count == 2
    assert result == {"cover.a", "cover.b"}


@pytest.mark.asyncio
async def test_force_send_skips_manually_overridden_covers_when_requested():
    """respect_manual_override=True must leave an overridden cover alone."""
    coordinator = _make_coordinator(
        entities=["cover.a", "cover.b"], manual_covers=("cover.a",)
    )

    result = await _force_send(coordinator, respect_manual_override=True)

    assert coordinator._cmd_svc.apply_position.call_count == 1
    assert coordinator._cmd_svc.apply_position.call_args.args[0] == "cover.b"
    assert result == {"cover.b"}


@pytest.mark.asyncio
async def test_force_send_filters_override_before_building_context():
    """The filter must run before dispatch, not lean on the force-disabled gate."""
    coordinator = _make_coordinator(
        entities=["cover.a", "cover.b"], manual_covers=("cover.a",)
    )

    await _force_send(coordinator, respect_manual_override=True)

    assert _context_entities(coordinator) == ["cover.b"]


@pytest.mark.asyncio
async def test_force_send_records_skip_for_manually_overridden_cover():
    """A pre-filtered cover must leave the same diagnostic trace as the gate.

    The non-forced path records ``manual_override`` from apply_position's gate,
    so ``last_skipped_action`` / diagnostics / the Lovelace card explain why the
    cover stayed put.  A silent ``continue`` here would make the button look
    broken for that cover.
    """
    coordinator = _make_coordinator(
        entities=["cover.a", "cover.b"], manual_covers=("cover.a",)
    )
    coordinator._inverse_state = False

    await _force_send(coordinator, respect_manual_override=True, trigger="press")

    recorded = coordinator._cmd_svc.record_skipped_action.call_args_list
    assert [call.args[0] for call in recorded] == ["cover.a"]
    # Same reason code the manual-override gate inside apply_position emits.
    assert recorded[0].args[1] == "manual_override"
    assert recorded[0].kwargs["trigger"] == "press"


# ---------------------------------------------------------------------------
# Step 6 — async_force_apply_calculated_position public entry point
# ---------------------------------------------------------------------------


def _make_apply_coordinator(**kwargs):
    """Coordinator mock wired for the public entry point.

    The shared helper is bound for real so the delegation actually runs;
    tests that only care about the call signature re-stub it themselves.
    """
    coordinator = _make_coordinator(**kwargs)
    coordinator.state = 42
    coordinator.config_entry.options = {}
    coordinator.async_refresh = AsyncMock()
    coordinator._async_force_send_pipeline_position = (
        AdaptiveDataUpdateCoordinator._async_force_send_pipeline_position.__get__(
            coordinator
        )
    )
    return coordinator


def _apply(coordinator, **kwargs):
    """Invoke the unbound public entry point."""
    return AdaptiveDataUpdateCoordinator.async_force_apply_calculated_position(
        coordinator, **kwargs
    )


@pytest.mark.asyncio
async def test_async_force_apply_calls_refresh_before_dispatch():
    """Recompute first so the forced position reflects current conditions."""
    coordinator = _make_apply_coordinator()
    order: list[str] = []

    async def _refresh():
        order.append("refresh")

    async def _apply_position(*args, **kwargs):
        order.append("apply_position")
        return ("sent", "set_cover_position")

    coordinator.async_refresh = AsyncMock(side_effect=_refresh)
    coordinator._cmd_svc.apply_position = AsyncMock(side_effect=_apply_position)

    await _apply(coordinator)

    coordinator.async_refresh.assert_awaited_once()
    assert order == ["refresh", "apply_position"]


@pytest.mark.asyncio
async def test_async_force_apply_delegates_with_all_three_bypasses():
    """All three bypasses ride the one shared helper — no second implementation."""
    coordinator = _make_apply_coordinator()
    coordinator._async_force_send_pipeline_position = AsyncMock(
        return_value={"cover.test"}
    )

    result = await _apply(coordinator)

    coordinator._async_force_send_pipeline_position.assert_awaited_once()
    kwargs = coordinator._async_force_send_pipeline_position.call_args.kwargs
    assert kwargs["bypass_auto_control"] is True
    assert kwargs["respect_manual_override"] is True
    assert kwargs["ignore_clock_window"] is True
    assert kwargs["entities"] is None
    assert result == {"cover.test"}


@pytest.mark.asyncio
async def test_async_force_apply_default_trigger_is_the_constant():
    """The trigger label is a named constant, not a literal."""
    coordinator = _make_apply_coordinator()
    coordinator._async_force_send_pipeline_position = AsyncMock(return_value=set())

    await _apply(coordinator)

    forwarded = coordinator._async_force_send_pipeline_position.call_args.kwargs[
        "trigger"
    ]
    assert forwarded == const.TRIGGER_FORCE_APPLY_CALCULATED
    assert const.TRIGGER_FORCE_APPLY_CALCULATED == "force_apply_calculated"


@pytest.mark.asyncio
async def test_async_force_apply_never_tags_safety():
    """is_safety=True would make reconciliation resend outside the window (#215/#216)."""
    coordinator = _make_apply_coordinator()
    coordinator._build_position_context = (
        AdaptiveDataUpdateCoordinator._build_position_context.__get__(coordinator)
    )
    coordinator._pipeline_bypasses_auto_control = False
    coordinator._pipeline_result = None
    coordinator.min_change = 2
    coordinator.time_threshold = 2
    coordinator._inverse_state = False
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    coordinator._policy = get_policy("cover_blind")

    await _apply(coordinator)

    calls = coordinator._cmd_svc.apply_position.call_args_list
    assert calls
    for call in calls:
        assert call.kwargs["context"].is_safety is False
        assert call.kwargs["context"].force is True


@pytest.mark.asyncio
async def test_async_force_apply_does_not_set_user_command():
    """user_command=True would bypass the same-position gate and click relays (#290)."""
    coordinator = _make_apply_coordinator()
    coordinator._build_position_context = (
        AdaptiveDataUpdateCoordinator._build_position_context.__get__(coordinator)
    )
    coordinator._pipeline_bypasses_auto_control = False
    coordinator._pipeline_result = None
    coordinator.min_change = 2
    coordinator.time_threshold = 2
    coordinator._inverse_state = False
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    coordinator._policy = get_policy("cover_blind")

    await _apply(coordinator)

    calls = coordinator._cmd_svc.apply_position.call_args_list
    assert calls
    for call in calls:
        assert call.kwargs["context"].user_command is False


# ---------------------------------------------------------------------------
# Step 7 — guard preservation against the REAL CoverCommandService
# ---------------------------------------------------------------------------


def _patch_caps():
    return patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
        return_value={
            "has_set_position": True,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
        },
    )


def _make_real_service_coordinator(
    *,
    target=50,
    current=50,
    min_change=2,
    time_threshold=2,
    cover_state="open",
):
    """Coordinator wired to a real CoverCommandService, like a live press."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    state_obj = MagicMock()
    state_obj.state = cover_state
    hass.states.get = MagicMock(return_value=state_obj)

    svc = CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=MagicMock(),
        open_close_threshold=50,
        check_interval_minutes=1,
        position_tolerance=3,
        max_retries=3,
    )
    svc._get_current_position = MagicMock(return_value=current)

    coordinator = _make_apply_coordinator()
    coordinator.state = target
    coordinator._cmd_svc = svc
    coordinator.min_change = min_change
    coordinator.time_threshold = time_threshold
    coordinator._inverse_state = False
    coordinator._pipeline_bypasses_auto_control = False
    coordinator._pipeline_result = None
    coordinator._build_position_context = (
        AdaptiveDataUpdateCoordinator._build_position_context.__get__(coordinator)
    )
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    coordinator._policy = get_policy("cover_blind")
    return coordinator, svc, hass


async def _press(coordinator, *, last_updated=None):
    """Run the button's coordinator call, recording the apply_position outcome."""
    outcomes: list[tuple[str, str]] = []
    original = coordinator._cmd_svc.apply_position

    async def _record(*args, **kwargs):
        result = await original(*args, **kwargs)
        outcomes.append(result)
        return result

    coordinator._cmd_svc.apply_position = _record
    stamp = (
        last_updated
        if last_updated is not None
        else dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    )
    with (
        _patch_caps(),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=stamp,
        ),
    ):
        await _apply(coordinator)
    return outcomes


@pytest.mark.asyncio
async def test_press_bypasses_position_delta_gate():
    """Headline feature: a 1% move lands even with delta_position=10."""
    coordinator, _svc, hass = _make_real_service_coordinator(
        target=51, current=50, min_change=10
    )

    outcomes = await _press(coordinator)

    assert outcomes == [("sent", "set_cover_position")]
    hass.services.async_call.assert_awaited()


@pytest.mark.asyncio
async def test_press_bypasses_time_delta_gate():
    """A command 10s after the last one lands even with delta_time=30."""
    coordinator, _svc, hass = _make_real_service_coordinator(
        target=80, current=20, time_threshold=30
    )
    recent = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=10)

    outcomes = await _press(coordinator, last_updated=recent)

    assert outcomes == [("sent", "set_cover_position")]
    hass.services.async_call.assert_awaited()


@pytest.mark.asyncio
async def test_press_still_blocked_by_kill_switch():
    """The master kill switch is not bypassed — not even by is_safety."""
    coordinator, svc, hass = _make_real_service_coordinator(target=80, current=20)
    svc._enabled = False

    outcomes = await _press(coordinator)

    assert outcomes == [("skipped", "integration_disabled")]
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_press_still_blocked_when_cover_unavailable():
    """Cover-unavailable boundary survives the press (#342)."""
    coordinator, _svc, hass = _make_real_service_coordinator(
        target=80, current=20, cover_state="unavailable"
    )

    outcomes = await _press(coordinator)

    assert outcomes == [("skipped", "cover_unavailable")]
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_press_still_deduped_by_same_position_gate():
    """No relay click when the cover already sits at the calculated position."""
    coordinator, _svc, hass = _make_real_service_coordinator(target=50, current=50)

    outcomes = await _press(coordinator)

    assert outcomes == [("skipped", "same_position")]
    hass.services.async_call.assert_not_awaited()


# ---------------------------------------------------------------------------
# Step 7b — honor_holds: pipeline holds must survive the press
#
# GroupLockHandler and MotionTimeoutHandler's hold_position mode both emit
# skip_command=True with position = snapshot.current_cover_position, which for
# a multi-cover instance is the arithmetic MEAN of every cover's position.
# Dispatching that value punches through the hold AND sends a number that is
# not the calculated position for any single cover.
# ---------------------------------------------------------------------------


def _make_hold_coordinator(*, positions: dict[str, int], control_method, state: int):
    """Real CoverCommandService + a hold-mode (skip_command) pipeline result."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    state_obj = MagicMock()
    state_obj.state = "open"
    hass.states.get = MagicMock(return_value=state_obj)

    svc = CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=MagicMock(),
        open_close_threshold=50,
        check_interval_minutes=1,
        position_tolerance=3,
        max_retries=3,
    )
    svc._get_current_position = MagicMock(side_effect=lambda entity: positions[entity])
    svc.apply_position = AsyncMock(wraps=svc.apply_position)
    svc.record_skipped_action = MagicMock(wraps=svc.record_skipped_action)

    coordinator = _make_apply_coordinator(entities=list(positions))
    coordinator.state = state
    coordinator._cmd_svc = svc
    coordinator.min_change = 2
    coordinator.time_threshold = 2
    coordinator._inverse_state = False
    coordinator.position_axis_inverted = False
    coordinator._pipeline_bypasses_auto_control = False
    coordinator._pipeline_result = PipelineResult(
        position=state,
        control_method=control_method,
        skip_command=True,
    )
    return coordinator, svc, hass


async def _press_raw(coordinator):
    """Press the button with the real service wired, no apply_position spy."""
    with (
        _patch_caps(),
        patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.get_last_updated",
            return_value=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
        ),
    ):
        await _apply(coordinator)


@pytest.mark.asyncio
async def test_press_honors_group_lock_hold():
    """A live group lock outranks everything — the press must not break it.

    GroupLockHandler sits at CUSTOM_POSITION_SAFETY_PRIORITY and is documented
    as outranking every other handler, weather included.  Its position is the
    mean of the members' positions, so punching through would drive a two-cover
    instance at 20/80 to 50/50.
    """
    coordinator, svc, hass = _make_hold_coordinator(
        positions={"cover.a": 20, "cover.b": 80},
        control_method=const.ControlMethod.GROUP_LOCK,
        state=50,
    )

    await _press_raw(coordinator)

    svc.apply_position.assert_not_called()
    hass.services.async_call.assert_not_awaited()
    assert [call.args[0] for call in svc.record_skipped_action.call_args_list] == [
        "cover.a",
        "cover.b",
    ]


@pytest.mark.asyncio
async def test_press_honors_motion_hold():
    """MotionTimeoutHandler hold_position mode is the same shape as group lock."""
    coordinator, svc, hass = _make_hold_coordinator(
        positions={"cover.a": 20, "cover.b": 80},
        control_method=const.ControlMethod.MOTION,
        state=50,
    )

    await _press_raw(coordinator)

    svc.apply_position.assert_not_called()
    hass.services.async_call.assert_not_awaited()
    assert [call.args[1] for call in svc.record_skipped_action.call_args_list] == [
        "motion_hold",
        "motion_hold",
    ]


@pytest.mark.asyncio
async def test_force_send_bypasses_holds_by_default():
    """Safe-default lock: forced transitions still ignore hold mode.

    _dispatch_to_cover's docstring records that forced transitions, override
    clears and window events deliberately bypass it.  Without ``honor_holds``
    the two pre-existing callers must keep that behaviour exactly.
    """
    coordinator = _make_coordinator()
    coordinator._pipeline_result = PipelineResult(
        position=50,
        control_method=const.ControlMethod.GROUP_LOCK,
        skip_command=True,
    )

    result = await _force_send(coordinator)

    coordinator._cmd_svc.apply_position.assert_called_once()
    coordinator._cmd_svc.record_skipped_action.assert_not_called()
    assert result == {"cover.test"}


@pytest.mark.asyncio
async def test_async_force_apply_delegates_with_honor_holds():
    """The public entry point must ask the shared helper to honour holds."""
    coordinator = _make_apply_coordinator()
    coordinator._async_force_send_pipeline_position = AsyncMock(return_value=set())

    await _apply(coordinator)

    kwargs = coordinator._async_force_send_pipeline_position.call_args.kwargs
    assert kwargs["honor_holds"] is True


# ---------------------------------------------------------------------------
# Step 8 — the button entity
# ---------------------------------------------------------------------------


async def _setup_buttons(*, options, sensor_type="cover_blind"):
    """Run the button platform's async_setup_entry and collect the entities."""
    from custom_components.adaptive_cover_pro.button import async_setup_entry

    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.options = options
    config_entry.data = {"name": "Test Cover", "sensor_type": sensor_type}
    config_entry.runtime_data = MagicMock()

    added: list = []
    await async_setup_entry(
        hass, config_entry, lambda entities, **kw: added.extend(entities)
    )
    return added


@pytest.mark.asyncio
async def test_apply_calculated_button_created_when_entities_configured():
    """async_setup_entry must yield exactly one Apply Calculated Position button."""
    from custom_components.adaptive_cover_pro.button import (
        AdaptiveCoverApplyCalculatedPositionButton,
    )

    added = await _setup_buttons(options={const.CONF_ENTITIES: ["cover.test1"]})

    matches = [
        e for e in added if isinstance(e, AdaptiveCoverApplyCalculatedPositionButton)
    ]
    assert len(matches) == 1


@pytest.mark.asyncio
async def test_apply_calculated_button_not_created_without_entities():
    """No configured covers → no buttons at all."""
    added = await _setup_buttons(options={const.CONF_ENTITIES: []})

    assert added == []


@pytest.mark.asyncio
async def test_apply_calculated_button_not_created_for_cover_group():
    """Cover-group orchestrator entries expose only the group buttons."""
    from custom_components.adaptive_cover_pro.button import (
        AdaptiveCoverApplyCalculatedPositionButton,
    )

    added = await _setup_buttons(
        options={const.CONF_ENTITIES: ["cover.test1"]}, sensor_type="cover_group"
    )

    assert not any(
        isinstance(e, AdaptiveCoverApplyCalculatedPositionButton) for e in added
    )


def _make_apply_button(entry_id="test_entry"):
    """Instantiate the button without HA infrastructure."""
    from custom_components.adaptive_cover_pro.button import (
        AdaptiveCoverApplyCalculatedPositionButton,
    )

    button = AdaptiveCoverApplyCalculatedPositionButton.__new__(
        AdaptiveCoverApplyCalculatedPositionButton
    )
    button.coordinator = MagicMock()
    button.coordinator.async_force_apply_calculated_position = AsyncMock(
        return_value=set()
    )
    button._attr_unique_id = f"{entry_id}_apply_calculated_position"
    return button


@pytest.mark.asyncio
async def test_apply_calculated_button_press_delegates_to_coordinator():
    """The button is a one-liner over the coordinator entry point."""
    button = _make_apply_button()

    await button.async_press()

    button.coordinator.async_force_apply_calculated_position.assert_awaited_once_with()


def test_apply_calculated_button_identity():
    """Translation key, unique_id, and no name override (HA resolves the name)."""
    from custom_components.adaptive_cover_pro.button import (
        AdaptiveCoverApplyCalculatedPositionButton,
    )

    assert (
        "name" not in AdaptiveCoverApplyCalculatedPositionButton.__dict__
    ), "must not override name — HA resolves the translated name"

    entry_id = "abc123"
    config_entry = MagicMock()
    config_entry.entry_id = entry_id
    config_entry.options = {const.CONF_ENTITIES: ["cover.test1"]}
    config_entry.data = {"name": "Test Cover", "sensor_type": "cover_blind"}
    button = AdaptiveCoverApplyCalculatedPositionButton(
        entry_id, MagicMock(), config_entry, MagicMock()
    )
    # HA's CachedProperties metaclass rewrites the class-level _attr_ into a
    # descriptor, so the value is only readable off an instance.
    assert button._attr_translation_key == "apply_calculated_position"
    assert button.translation_key == "apply_calculated_position"
    assert button.unique_id == f"{entry_id}_apply_calculated_position"
