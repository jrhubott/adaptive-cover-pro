"""Control-gate matrix: every coordinator entry point that can produce a cover command.

Invariant under test
---------------------
When ``automatic_control=False``, any coordinator call-site that is NOT a
declared force bypass must NOT invoke ``apply_position`` with
``context.force=True``.  Force bypasses come in two distinct sub-types:

* **Safety targets** (safety-priority CustomPositionHandler, WeatherOverrideHandler):
  ``context.force=True`` AND ``context.is_safety=True``.
  Reconciliation resends their targets even when auto_control=OFF or outside
  the time window.

* **Non-safety force bypasses** (safety custom-position released, manual_override_clear):
  ``context.force=True`` AND ``context.is_safety=False``.
  Gate checks (delta, time, manual override) are bypassed so the command
  goes out immediately, but the target is NOT persisted across window
  boundaries.  The cover gets one best-effort attempt; reconciliation stops
  once the window closes (fix for issue #223).

Why this test exists
--------------------
The per-feature test template is a "happy-path" test with ``auto_control=True``.
This means a new ``force=True`` call-site can be added without anyone noticing
it forgot an upstream ``automatic_control`` gate.  This matrix keeps the full
decision table in one place so:

1. A new call-site must either be added as a row here or break the AST
   allowlist test (see ``test_force_apply_allowlist.py``).
2. Any missing gate fails the ``assert_not_called_with_force`` helper rather
   than silently passing.
3. The ``is_safety_target`` column documents whether the target should persist
   across window boundaries — making the force vs. safety distinction explicit.

Decision table (all rows assume ``automatic_control=False``)
------------------------------------------------------------
+----------------------------------+------------------+------------------+-----------------+
| id                               | entry point      | is_safety_bypass | is_safety_target|
+==================================+==================+==================+=================+
| manual_override_expiry           | _async_send_…    | False (gated)    | False           |
| state_change_safety_custom_pos   | async_handle_…   | True             | True            |
| state_change_weather_bypass      | async_handle_…   | True             | True            |
| first_refresh_safety             | async_handle_…   | True             | True            |
| window_close_return_sunset       | _check_time_…    | False (gated)    | False           |
| sunset_window_opened             | _check_sunset_…  | False (gated)    | False           |
| state_change_safety_released     | async_handle_…   | True (force=True)| False (no tag)  |
+----------------------------------+------------------+------------------+-----------------+

``state_change_safety_released``: when a safety-priority custom position releases,
the coordinator must bypass gate checks (force=True) so the cover returns to the
calculated position immediately, but the resulting target is NOT a safety target
(is_safety=False) — it should not be resent by reconciliation outside the time window.

Rows for state_change_solar and first_refresh_non_safety are omitted because
those paths call apply_position with ``force=False`` and rely on the *service*
gate (auto_control block in CoverCommandService.apply_position).  That layer
is covered by ``test_position_reconciliation.py``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from collections.abc import Callable, Awaitable
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.managers.cover_command import PositionContext
from custom_components.adaptive_cover_pro.managers.toggles import ToggleManager
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pipeline_result(
    bypass: bool, *, is_safety: bool | None = None
) -> PipelineResult:
    """SOLAR result, or a custom-position result when it bypasses or is safety.

    ``is_safety`` defaults to ``bypass`` so existing call sites keep modeling a
    safety-priority custom position (both flags set together). Pass it
    explicitly to decouple the two — e.g. a non-safety slot (issue #767) that
    bypasses nothing, or a safety target that does not set ``bypass``.
    """
    if is_safety is None:
        is_safety = bypass
    is_custom = bypass or is_safety
    return PipelineResult(
        position=50,
        control_method=(
            ControlMethod.CUSTOM_POSITION if is_custom else ControlMethod.SOLAR
        ),
        reason="custom position #5 active" if is_custom else "solar",
        bypass_auto_control=bypass,
        is_safety=is_safety,
    )


def _base_coord() -> AdaptiveDataUpdateCoordinator:
    """Minimal coordinator with automatic_control=False and a captured apply_position mock."""
    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._toggles = ToggleManager()
    coord.automatic_control = False
    coord.entities = [MagicMock()]
    coord._group_intents = {}

    cmd_svc = MagicMock()
    cmd_svc.apply_position = AsyncMock(return_value=("sent", ""))
    coord._cmd_svc = cmd_svc

    # _build_position_context: preserve force and is_safety kwargs in the returned
    # PositionContext so callers can inspect which values the coordinator passed.
    def _fake_build_ctx(
        entity,
        options,
        *,
        force=False,
        is_safety=False,
        bypass_auto_control=False,
        sun_just_appeared=False,
        use_my_position=False,
        user_command=False,
    ):
        return PositionContext(
            auto_control=False,  # reflects automatic_control=False
            manual_override=False,
            sun_just_appeared=sun_just_appeared,
            min_change=2,
            time_threshold=0,
            special_positions=[0, 100],
            force=force,
            is_safety=is_safety,
            bypass_auto_control=bypass_auto_control,
            use_my_position=use_my_position,
            user_command=user_command,
        )

    coord._build_position_context = _fake_build_ctx

    manager = MagicMock()
    manager.is_cover_manual.return_value = False
    coord.manager = manager

    return coord


def _force_calls(coord: AdaptiveDataUpdateCoordinator) -> list:
    """Return every apply_position call that passed context.force=True."""
    result = []
    for call in coord._cmd_svc.apply_position.call_args_list:
        ctx = call.kwargs.get("context") or (
            call.args[3] if len(call.args) > 3 else None
        )
        if ctx is not None and getattr(ctx, "force", False):
            result.append(call)
    return result


def _safety_target_calls(coord: AdaptiveDataUpdateCoordinator) -> list:
    """Return every apply_position call that passed context.is_safety=True."""
    result = []
    for call in coord._cmd_svc.apply_position.call_args_list:
        ctx = call.kwargs.get("context") or (
            call.args[3] if len(call.args) > 3 else None
        )
        if ctx is not None and getattr(ctx, "is_safety", False):
            result.append(call)
    return result


# ---------------------------------------------------------------------------
# Matrix definition
# ---------------------------------------------------------------------------


@dataclass
class MatrixCase:
    """One row of the control-gate decision table.

    is_safety_bypass: True if this path calls apply_position(force=True) even
        when automatic_control=False (gate bypass).  False if the coordinator
        guards the call behind automatic_control / check_adaptive_time.

    is_safety_target: True if the resulting PositionContext should have
        is_safety=True (entity added to _safety_targets, persists across window
        boundaries).  Meaningful only when is_safety_bypass=True.  Genuine
        safety handlers (force override, weather) set this True; transitional
        paths (safety custom-position released, override_clear) set this False — they
        bypass gates for a one-shot send but do not persist the target.
    """

    id: str
    is_safety_bypass: bool
    is_safety_target: bool
    setup: Callable[[AdaptiveDataUpdateCoordinator], None]
    trigger: Callable[[AdaptiveDataUpdateCoordinator], Awaitable[None]]


async def _trigger_manual_override_expiry(coord):
    coord._time_mgr = MagicMock()
    coord._time_mgr.is_active = True  # inside time window
    coord._check_sun_validity_transition = MagicMock(return_value=False)
    await coord._async_send_after_override_clear(50, {})


async def _trigger_state_change_safety_custom_position(coord):
    coord._pipeline_result = _make_pipeline_result(bypass=True)
    coord._time_mgr = MagicMock()
    coord._time_mgr.is_active = True
    coord._check_sun_validity_transition = MagicMock(return_value=False)
    coord.state_change = True
    coord._last_state_change_entity = None
    coord._custom_position_template_trigger = False
    await coord.async_handle_state_change(50, {})


async def _trigger_state_change_non_safety_custom_position(coord):
    """Trigger: a default-priority (77) custom position is the active winner.

    Issue #767: a non-safety custom position must respect Automatic Control.
    With no fresh sensor edge and is_safety=False, the coordinator must NOT
    force the command through when automatic_control=False — the slot's
    bypass_auto_control flag does not earn it coordinator-level force treatment.
    """
    coord._pipeline_result = _make_pipeline_result(bypass=True, is_safety=False)
    coord._time_mgr = MagicMock()
    coord._time_mgr.is_active = True
    coord._check_sun_validity_transition = MagicMock(return_value=False)
    coord.state_change = True
    coord._last_state_change_entity = None
    coord._custom_position_template_trigger = False
    await coord.async_handle_state_change(50, {})


async def _trigger_state_change_weather_bypass(coord):
    coord._pipeline_result = PipelineResult(
        position=50,
        control_method=ControlMethod.WEATHER,
        reason="weather_override",
        bypass_auto_control=True,
        is_safety=True,
    )
    coord._time_mgr = MagicMock()
    coord._time_mgr.is_active = True
    coord._check_sun_validity_transition = MagicMock(return_value=False)
    coord.state_change = True
    coord._last_state_change_entity = None
    coord._custom_position_template_trigger = False
    await coord.async_handle_state_change(50, {})


async def _trigger_first_refresh_safety(coord):
    coord._pipeline_result = _make_pipeline_result(bypass=True)
    coord._time_mgr = MagicMock()
    coord._time_mgr.is_active = True
    coord.first_refresh = True
    coord._is_reload = False
    coord._check_sun_validity_transition = MagicMock(return_value=False)
    coord._weather_mgr = MagicMock()
    coord._weather_mgr.configured_sensors = []  # disabled — no recovery needed
    await coord.async_handle_first_refresh(50, {})


async def _trigger_window_close_return_sunset(coord):
    from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
        EventBuffer,
    )
    from custom_components.adaptive_cover_pro.state.window_transition_tracker import (
        WindowTransitionTracker,
    )

    coord._track_end_time = True
    coord.config_entry = MagicMock()
    coord.config_entry.options = {}
    coord._inverse_state = False
    coord.entities = []
    coord.manager = MagicMock()
    coord._build_position_context = MagicMock()
    event_buffer = getattr(coord, "_event_buffer", None) or EventBuffer(maxlen=10)
    coord._event_buffer = event_buffer
    # Phase E: _check_time_window_transition awaits the sunset-window tracker
    # after running the closed-window callback.  Seed prev_sunset_active=True
    # so it no-ops without redispatching.
    tracker = WindowTransitionTracker(
        hass=MagicMock(),
        logger=coord.logger,
        event_buffer=event_buffer,
        effective_default_fn=lambda _opts: (0, False),
    )
    tracker._prev_sunset_active = True
    coord._window_tracker = tracker

    async def _invoke(track_end_time, refresh_callback, on_window_open=None):
        await refresh_callback()

    coord._time_mgr = MagicMock()
    coord._time_mgr.check_transition = _invoke
    await coord._check_time_window_transition(dt.datetime.now(dt.UTC))


async def _trigger_sunset_window_opened(coord):
    """Trigger: astronomical sunset window opens after end_time (issue #266).

    _check_sunset_window_transition is called directly with the coordinator
    gated by automatic_control=False.  The method must return early without
    dispatching (non-bypass, gated path).
    """
    from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
        EventBuffer,
    )
    from custom_components.adaptive_cover_pro.state.window_transition_tracker import (
        WindowTransitionTracker,
    )

    coord._track_end_time = True
    coord.config_entry = MagicMock()
    coord.config_entry.options = {"sunset_position": 0}
    coord._inverse_state = False
    coord._compute_current_effective_default = MagicMock(return_value=(0, True))
    coord.async_refresh = AsyncMock()
    event_buffer = getattr(coord, "_event_buffer", None) or EventBuffer(maxlen=10)
    coord._event_buffer = event_buffer
    tracker = WindowTransitionTracker(
        hass=MagicMock(),
        logger=coord.logger,
        event_buffer=event_buffer,
        effective_default_fn=coord._compute_current_effective_default,
    )
    tracker._prev_sunset_active = False
    coord._window_tracker = tracker
    await coord._check_sunset_window_transition()


async def _trigger_safety_custom_position_released(coord):
    """Trigger: a safety-priority custom-position slot just transitioned on → off.

    Pipeline result is now solar (bypass_auto_control=False) but
    safety_release=True.  The coordinator must send with force=True so
    gate checks don't block the return to calculated position (#177), but the
    resulting context must have is_safety=False so the target is NOT persisted
    across window boundaries (#223).
    """
    coord._pipeline_result = _make_pipeline_result(bypass=False)
    coord._time_mgr = MagicMock()
    coord._time_mgr.is_active = True
    coord._check_sun_validity_transition = MagicMock(return_value=False)
    coord.state_change = True
    coord._last_state_change_entity = None
    coord._custom_position_template_trigger = False
    await coord.async_handle_state_change(50, {}, safety_release=True)


async def _trigger_switch_auto_control_off_return(coord):
    """Trigger: AdaptiveCoverSwitch.async_turn_off with return_to_default_toggle on.

    Issue #293: this is the only sanctioned caller that bypasses the
    auto_control gate via bypass_auto_control=True (rather than is_safety=True).
    """
    from custom_components.adaptive_cover_pro.switch import AdaptiveCoverSwitch

    coord.return_to_default_toggle = True
    coord.config_entry = MagicMock()
    coord.config_entry.options = {"default_height": 60}
    coord.manager.manual_controlled = []
    coord.async_refresh = AsyncMock()

    switch = object.__new__(AdaptiveCoverSwitch)
    switch.coordinator = coord
    switch._key = "automatic_control"
    switch._name = "automatic_control"
    switch._initial_state = True
    switch._attr_is_on = True
    switch.schedule_update_ha_state = MagicMock()

    await switch.async_turn_off()


async def _trigger_async_apply_user_position(coord):
    """Trigger: ``set_position`` service called with ``force=True``.

    ``async_apply_user_position(force=True)`` builds a force=True context so
    the slider move bypasses delta/time/manual_override gates. Not a safety
    target — the move should not persist across window boundaries. The new
    ``force=False`` default contract (pipeline preemption + manual-override
    engagement) is covered in ``test_coordinator_apply_user_position.py``.
    """
    coord.config_entry = MagicMock()
    coord.config_entry.options = {}
    # After fix #643, async_apply_user_position falls back to _resolved_options.
    coord._resolved_options = {}
    coord._snapshot_builder = MagicMock()
    coord._snapshot_builder.read_custom_position_sensors = MagicMock(return_value=[])
    # Floor composition reads from a real PipelineSnapshot (#463).
    from tests.test_pipeline.conftest import make_snapshot  # noqa: PLC0415
    from unittest.mock import patch as _patch  # noqa: PLC0415

    snapshot = make_snapshot()
    coord._snapshot_builder.build = MagicMock(return_value=snapshot)
    # Snapshot builder consumes these coordinator attrs via kwargs — present
    # them as plain mocks so the call signature resolves.  The mock returns
    # the pre-built snapshot regardless of inputs.
    coord._cover_data = MagicMock()
    coord._cover_type = "cover_blind"
    coord._weather_readings = None
    coord._cloud_mgr = MagicMock()
    coord._compute_mean_cover_position = MagicMock(return_value=None)
    # is_motion_timeout_active / is_weather_override_active / check_adaptive_time
    # are properties — patch them at the class level for this call.
    with (
        _patch.object(
            AdaptiveDataUpdateCoordinator,
            "is_motion_timeout_active",
            new_callable=lambda: False,
        ),
        _patch.object(
            AdaptiveDataUpdateCoordinator,
            "is_weather_override_active",
            new_callable=lambda: False,
        ),
        _patch.object(
            AdaptiveDataUpdateCoordinator,
            "check_adaptive_time",
            new_callable=lambda: True,
        ),
        _patch.object(
            AdaptiveDataUpdateCoordinator,
            "_is_glare_zone_enabled",
            new=MagicMock(return_value=False),
        ),
    ):
        await coord.async_apply_user_position(
            "cover.test", 42, trigger="set_position", force=True
        )


CONTROL_GATE_MATRIX: list[MatrixCase] = [
    MatrixCase(
        id="manual_override_expiry",
        is_safety_bypass=False,
        is_safety_target=False,
        setup=lambda _: None,
        trigger=_trigger_manual_override_expiry,
    ),
    MatrixCase(
        id="state_change_safety_custom_position",
        is_safety_bypass=True,
        is_safety_target=True,
        setup=lambda _: None,
        trigger=_trigger_state_change_safety_custom_position,
    ),
    MatrixCase(
        # Issue #767: a default-priority (77) custom position is non-safety.
        # When automatic_control=False and no fresh sensor edge fired, the
        # coordinator must NOT force it through and must NOT tag it as a safety
        # target — it follows the Automatic Control switch like any other slot.
        id="state_change_non_safety_custom_position",
        is_safety_bypass=False,
        is_safety_target=False,
        setup=lambda _: None,
        trigger=_trigger_state_change_non_safety_custom_position,
    ),
    MatrixCase(
        id="state_change_weather_bypass",
        is_safety_bypass=True,
        is_safety_target=True,
        setup=lambda _: None,
        trigger=_trigger_state_change_weather_bypass,
    ),
    MatrixCase(
        id="first_refresh_safety",
        is_safety_bypass=True,
        is_safety_target=True,
        setup=lambda _: None,
        trigger=_trigger_first_refresh_safety,
    ),
    MatrixCase(
        id="window_close_return_sunset",
        is_safety_bypass=False,
        is_safety_target=False,
        setup=lambda _: None,
        trigger=_trigger_window_close_return_sunset,
    ),
    MatrixCase(
        id="sunset_window_opened",
        is_safety_bypass=False,
        is_safety_target=False,
        setup=lambda _: None,
        trigger=_trigger_sunset_window_opened,
    ),
    MatrixCase(
        id="state_change_safety_custom_position_released",
        is_safety_bypass=True,  # force=True bypasses auto_control gate
        is_safety_target=False,  # but is NOT a persistent safety target (#223)
        setup=lambda _: None,
        trigger=_trigger_safety_custom_position_released,
    ),
    MatrixCase(
        # Issue #293: switch toggles auto_control off → return-to-default fires
        # one-shot via the new bypass_auto_control=True channel.  Calls with
        # force=True (so the matrix invariant 1 marks it as a bypass row), but
        # is_safety=False — the target should NOT persist across window
        # boundaries.  The bypass_auto_control flag is what actually lets the
        # command through the auto_control gate after the issue #293 fix.
        id="switch_auto_control_off_return_to_default",
        is_safety_bypass=True,
        is_safety_target=False,
        setup=lambda _: None,
        trigger=_trigger_switch_auto_control_off_return,
    ),
    MatrixCase(
        # User-initiated single entry point (set_position service + opt-in
        # proxy cover entity). Bypasses gates via force=True so the slider
        # move lands immediately, but the target is NOT persisted.
        id="async_apply_user_position",
        is_safety_bypass=True,
        is_safety_target=False,
        setup=lambda _: None,
        trigger=_trigger_async_apply_user_position,
    ),
]


# ---------------------------------------------------------------------------
# The matrix test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CONTROL_GATE_MATRIX, ids=lambda c: c.id)
@pytest.mark.asyncio
@pytest.mark.unit
async def test_auto_control_gate(case: MatrixCase):
    """Two invariants checked for every coordinator entry point.

    Invariant 1 — force gate:
        Non-safety-bypass paths must NOT call apply_position(force=True) when
        automatic_control=False.  The coordinator must guard the call.  Safety
        bypasses (safety custom position, weather override, safety release) ARE allowed
        to call with force=True regardless of the toggle.

        Failure on a non-safety row → missing automatic_control gate.
        Failure on a safety row → legitimate bypass stopped working.

    Invariant 2 — safety-target classification:
        Calls with force=True must use is_safety=True only for genuine safety
        handlers (safety-priority CustomPosition, Weather).  Transitional bypasses
        (safety custom-position released, override_clear) must use is_safety=False so
        the target is not persisted across window boundaries (fix #223).

        Failure on a safety-target row → handler lost its safety classification.
        Failure on a non-safety-target force-bypass row → target is incorrectly
        persisted; reconciliation will resend it outside the time window.
    """
    coord = _base_coord()
    case.setup(coord)
    await case.trigger(coord)

    forced = _force_calls(coord)
    safety_tagged = _safety_target_calls(coord)

    # --- Invariant 1: force gate ---
    if case.is_safety_bypass:
        assert forced, (
            f"[{case.id}] Force bypass must call apply_position(context.force=True) "
            f"when automatic_control=False, but no force=True call was recorded. "
            f"All calls: {coord._cmd_svc.apply_position.call_args_list}"
        )
    else:
        assert not forced, (
            f"[{case.id}] Non-bypass path called apply_position(context.force=True) "
            f"with automatic_control=False — add an automatic_control gate before the "
            f"apply_position call (see _async_send_after_override_clear for the pattern). "
            f"Offending calls: {forced}"
        )

    # --- Invariant 2: safety-target classification ---
    if case.is_safety_bypass:
        if case.is_safety_target:
            assert safety_tagged, (
                f"[{case.id}] Genuine safety handler must pass is_safety=True so "
                f"reconciliation persists the target across window boundaries. "
                f"All calls: {coord._cmd_svc.apply_position.call_args_list}"
            )
        else:
            assert not safety_tagged, (
                f"[{case.id}] Transitional force bypass must NOT pass is_safety=True — "
                f"the target should not persist across window boundaries (fix #223). "
                f"Offending calls: {safety_tagged}"
            )
