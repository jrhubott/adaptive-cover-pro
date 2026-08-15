"""Regression tests for issue #266: sunset position not sent when end_time < sunset+offset.

When the user's end_time fires before the astronomical sunset window opens,
_on_window_closed sends the daytime default (is_sunset_active=False at that moment).
Later, when is_sunset_active flips True, _check_sunset_window_transition must detect
the transition and dispatch the sunset position.
"""

from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_DEFAULT_HEIGHT,
    CONF_END_OF_WINDOW_POS,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    ControlMethod,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.managers.cover_command import PositionContext
from custom_components.adaptive_cover_pro.managers.toggles import ToggleManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coord(
    *,
    track_end_time: bool = True,
    automatic_control: bool = True,
    sunset_pos: int | None = 0,
    inverse_state: bool = False,
    n_entities: int = 1,
    pipeline_control_method: ControlMethod | None = None,
) -> AdaptiveDataUpdateCoordinator:
    """Minimal coordinator fixture for _check_sunset_window_transition tests.

    ``pipeline_control_method``, when given, seeds ``coord._pipeline_result``
    with that control method (issue #895 — a higher-priority handler, e.g.
    CUSTOM_POSITION, currently winning the pipeline must suppress the sunset
    dispatch). Left unset by default so existing callers keep exercising the
    startup/no-pipeline-result-yet case exactly as before.
    """
    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._toggles = ToggleManager()
    # The dispatch seam routes each target through the polymorphic
    # ``_entity_target`` → ``policy.resolve_entity_target`` (identity for every
    # non-dual-entity cover type). A blind policy supplies that identity.
    coord._policy = get_policy("cover_blind")
    coord.automatic_control = automatic_control
    coord._track_end_time = track_end_time
    coord._inverse_state = inverse_state
    if pipeline_control_method is not None:
        coord._pipeline_result = SimpleNamespace(control_method=pipeline_control_method)

    entities = [MagicMock() for _ in range(n_entities)]
    coord.entities = entities

    options = {}
    if sunset_pos is not None:
        options[CONF_SUNSET_POS] = sunset_pos
    config_entry = MagicMock()
    config_entry.options = options
    coord.config_entry = config_entry

    cmd_svc = MagicMock()
    cmd_svc.apply_position = AsyncMock(return_value=("sent", ""))
    coord._cmd_svc = cmd_svc

    coord.async_refresh = AsyncMock()

    manager = MagicMock()
    manager.is_cover_manual.return_value = False
    coord.manager = manager

    def _fake_build_ctx(entity, options, *, force=False, is_safety=False, **_):
        return PositionContext(
            auto_control=automatic_control,
            manual_override=False,
            sun_just_appeared=False,
            min_change=2,
            time_threshold=0,
            special_positions=[0, 100],
            force=force,
            is_safety=is_safety,
        )

    coord._build_position_context = _fake_build_ctx

    from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
        EventBuffer,
    )
    from custom_components.adaptive_cover_pro.state.window_transition_tracker import (
        WindowTransitionTracker,
    )

    coord._event_buffer = EventBuffer(maxlen=50)
    # Phase E: sunset-window state lives on the WindowTransitionTracker.  Each
    # test reseeds via _seed_sunset_state below.  ``_sunset_window_is_open`` is
    # the real coordinator seam (issue #1287) — blind to the end-of-window
    # override, unlike the old ``_compute_current_effective_default`` tuple.
    coord._sunset_window_is_open = MagicMock(return_value=False)
    coord._window_tracker = WindowTransitionTracker(
        hass=MagicMock(),
        logger=coord.logger,
        event_buffer=coord._event_buffer,
        sunset_window_open_fn=coord._sunset_window_is_open,
    )

    return coord


def _seed_sunset_state(
    coord, *, prev: bool | None, current_is_sunset: bool, pos: int = 0
):
    """Seed the tracker's prior-sunset state and what the next boundary lookup returns.

    ``pos`` is accepted for call-site compatibility with the pre-#1287 tuple
    shape but no longer feeds anything: the dispatched position always comes
    from the configured ``sunset_position`` option, not from this predicate.
    """
    del pos  # inert — see docstring
    coord._window_tracker._prev_sunset_active = prev
    coord._sunset_window_is_open = MagicMock(return_value=current_is_sunset)
    coord._window_tracker._sunset_window_open_fn = coord._sunset_window_is_open


def _make_boundary_test_coord(
    *,
    end_of_window_pos: int,
    sunset_pos: int,
    sunset_hour: int,
    sunset_minute: int,
    sunset_offset: int,
    default_height: int = 100,
) -> AdaptiveDataUpdateCoordinator:
    """Coordinator fixture that drives the REAL sunset-boundary predicate (#1287).

    Unlike ``_make_coord``/``_seed_sunset_state`` above (which stub the
    tracker's edge-detector input directly with a canned value), this
    fixture wires ``coord._window_tracker`` to the coordinator's real
    ``_sunset_window_is_open`` — backed by a fake ``hass`` / ``_time_mgr`` /
    ``get_blind_data`` — so the False→True edge is driven by the actual
    ``read_sunset_window_open``/``resolve_sunset_verdict`` decision, exactly
    like production. ``coord._time_mgr.before_end_time`` is left for the
    caller to flip between ticks (real ``TimeWindowManager`` behavior,
    simplified to a plain attribute here).
    """
    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._toggles = ToggleManager()
    coord._policy = get_policy("cover_blind")
    coord.automatic_control = True
    coord._track_end_time = True
    coord._inverse_state = False
    coord.hass = MagicMock()
    coord.hass.states.get.return_value = None  # no sunset/sunrise time entities

    coord.entities = [MagicMock()]

    options = {
        CONF_DEFAULT_HEIGHT: default_height,
        CONF_SUNSET_POS: sunset_pos,
        CONF_END_OF_WINDOW_POS: end_of_window_pos,
        CONF_SUNSET_OFFSET: sunset_offset,
    }
    config_entry = MagicMock()
    config_entry.options = options
    coord.config_entry = config_entry

    cmd_svc = MagicMock()
    cmd_svc.apply_position = AsyncMock(return_value=("sent", ""))
    coord._cmd_svc = cmd_svc
    coord.async_refresh = AsyncMock()

    manager = MagicMock()
    manager.is_cover_manual.return_value = False
    coord.manager = manager

    def _fake_build_ctx(entity, options, *, force=False, is_safety=False, **_):
        return PositionContext(
            auto_control=True,
            manual_override=False,
            sun_just_appeared=False,
            min_change=2,
            time_threshold=0,
            special_positions=[0, 100],
            force=force,
            is_safety=is_safety,
        )

    coord._build_position_context = _fake_build_ctx

    today = _dt.date.today()
    sun_data = MagicMock()
    sun_data.sunset.return_value = _dt.datetime(
        today.year, today.month, today.day, sunset_hour, sunset_minute, 0
    )
    sun_data.sunrise.return_value = _dt.datetime(
        today.year, today.month, today.day, 6, 0, 0
    )
    cover_data = MagicMock()
    cover_data.sun_data = sun_data
    coord.get_blind_data = MagicMock(return_value=cover_data)

    time_mgr = MagicMock()
    time_mgr.effective_daytime_gate = None  # no gate — astral path (#742)
    time_mgr.window_explicitly_started = False
    time_mgr.before_end_time = True
    coord._time_mgr = time_mgr

    from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
        EventBuffer,
    )
    from custom_components.adaptive_cover_pro.state.window_transition_tracker import (
        WindowTransitionTracker,
    )

    coord._event_buffer = EventBuffer(maxlen=50)
    coord._window_tracker = WindowTransitionTracker(
        hass=coord.hass,
        logger=coord.logger,
        event_buffer=coord._event_buffer,
        sunset_window_open_fn=coord._sunset_window_is_open,
    )

    return coord


def _freeze_helpers_now(naive_utc: _dt.datetime):
    """Patch ``helpers.dt.datetime.now()`` so ``compute_effective_default`` sees a fixed instant."""
    aware = naive_utc.replace(tzinfo=_dt.UTC)
    return patch(
        "custom_components.adaptive_cover_pro.helpers.dt.datetime",
        **{"now.return_value": aware},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_sunset_window_opens_after_end_time_dispatches_sunset_pos():
    """Core regression: transition False→True dispatches sunset_pos to all covers."""
    coord = _make_coord(sunset_pos=0, n_entities=2)
    _seed_sunset_state(coord, prev=False, current_is_sunset=True, pos=0)

    await coord._check_sunset_window_transition()

    assert coord._cmd_svc.apply_position.call_count == 2
    for call in coord._cmd_svc.apply_position.call_args_list:
        trigger = call.args[2] if len(call.args) > 2 else call.kwargs.get("trigger", "")
        assert (
            trigger == "sunset_window_opened"
        ), f"Expected trigger 'sunset_window_opened', got {trigger!r}"
    coord.async_refresh.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_no_dispatch_when_sunset_already_active_in_previous_cycle():
    """No dispatch when is_sunset_active was already True (no transition)."""
    coord = _make_coord(sunset_pos=0)
    _seed_sunset_state(coord, prev=True, current_is_sunset=True)

    await coord._check_sunset_window_transition()

    coord._cmd_svc.apply_position.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_no_dispatch_when_return_sunset_disabled():
    """No dispatch when return_sunset (track_end_time) is False."""
    coord = _make_coord(track_end_time=False, sunset_pos=0)
    _seed_sunset_state(coord, prev=False, current_is_sunset=True)

    await coord._check_sunset_window_transition()

    coord._cmd_svc.apply_position.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_no_dispatch_when_automatic_control_off():
    """No dispatch when automatic_control is False (non-bypass gate)."""
    coord = _make_coord(automatic_control=False, sunset_pos=0)
    _seed_sunset_state(coord, prev=False, current_is_sunset=True)

    await coord._check_sunset_window_transition()

    coord._cmd_svc.apply_position.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_no_dispatch_when_sunset_pos_not_configured():
    """No dispatch when sunset_pos is not configured (None)."""
    coord = _make_coord(sunset_pos=None)
    # _compute_current_effective_default won't be called because we return early
    coord._window_tracker._prev_sunset_active = False
    # No sunset_pos in options → return early before checking transition

    await coord._check_sunset_window_transition()

    coord._cmd_svc.apply_position.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_skips_covers_with_active_manual_override():
    """Cover with active manual override is skipped; others proceed."""
    coord = _make_coord(sunset_pos=0, n_entities=2)
    _seed_sunset_state(coord, prev=False, current_is_sunset=True)

    # First entity has manual override active
    def _manual_for_first(entity):
        return entity is coord.entities[0]

    coord.manager.is_cover_manual.side_effect = _manual_for_first

    await coord._check_sunset_window_transition()

    # Only second entity gets a command
    assert coord._cmd_svc.apply_position.call_count == 1
    called_entity = coord._cmd_svc.apply_position.call_args.args[0]
    assert called_entity is coord.entities[1]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_transition_detected_only_once_per_day():
    """apply_position called exactly once even when invoked multiple times with is_sunset=True."""
    coord = _make_coord(sunset_pos=0)
    _seed_sunset_state(coord, prev=False, current_is_sunset=True)

    await coord._check_sunset_window_transition()
    # Second call: _prev_sunset_active is now True, is_sunset still True → no transition
    await coord._check_sunset_window_transition()

    assert coord._cmd_svc.apply_position.call_count == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_prev_sunset_active_initial_none_does_not_dispatch():
    """On HA restart mid-sunset (_prev_sunset_active=None), no spurious dispatch.

    Mirrors the _last_sun_validity_state=None pattern: first call initializes
    state without dispatching, so covers aren't blindly repositioned on startup.
    """
    coord = _make_coord(sunset_pos=0)
    # _prev_sunset_active starts as None (fresh coordinator)
    assert coord._window_tracker._prev_sunset_active is None
    coord._sunset_window_is_open = MagicMock(return_value=True)
    coord._window_tracker._sunset_window_open_fn = coord._sunset_window_is_open

    await coord._check_sunset_window_transition()

    coord._cmd_svc.apply_position.assert_not_called()
    # State should be initialized
    assert coord._window_tracker._prev_sunset_active is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_inverse_state_position_is_inverted():
    """With inverse_state=True, sunset_pos=0 is sent as 100."""
    coord = _make_coord(sunset_pos=0, inverse_state=True)
    _seed_sunset_state(coord, prev=False, current_is_sunset=True, pos=0)

    await coord._check_sunset_window_transition()

    assert coord._cmd_svc.apply_position.call_count == 1
    sent_pos = coord._cmd_svc.apply_position.call_args.args[1]
    assert sent_pos == 100


@pytest.mark.asyncio
@pytest.mark.unit
async def test_end_time_after_sunset_does_not_double_dispatch():
    """When end_time fires after sunset is already active, no follow-up dispatch.

    If end_time > sunset+offset, _on_window_closed already sent sunset_pos.
    _prev_sunset_active should be True when _check_sunset_window_transition runs,
    so the transition guard prevents a second send.
    """
    coord = _make_coord(sunset_pos=0)
    # Simulate: sunset window was already active when the last update ran
    _seed_sunset_state(coord, prev=True, current_is_sunset=True)

    await coord._check_sunset_window_transition()

    coord._cmd_svc.apply_position.assert_not_called()


# ---------------------------------------------------------------------------
# Issue #895: priority inversion — sunset dispatch must not override a
# currently-active higher-priority pipeline handler (e.g. CUSTOM_POSITION).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_skips_dispatch_when_custom_position_currently_active():
    """No dispatch when a higher-priority handler currently owns the pipeline.

    Regression for issue #895: the astronomical-sunset dispatch bypassed the
    pipeline entirely and force-sent the raw sunset position even when a
    higher-priority CustomPositionHandler slot (e.g. a user's sleep-mode
    floor) was the pipeline's current winner. That overwrote the custom
    position until the next refresh cycle silently corrected it back — a
    spurious double-move.
    """
    coord = _make_coord(
        sunset_pos=0, pipeline_control_method=ControlMethod.CUSTOM_POSITION
    )
    _seed_sunset_state(coord, prev=False, current_is_sunset=True)

    await coord._check_sunset_window_transition()

    coord._cmd_svc.apply_position.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_dispatch_still_occurs_when_pipeline_control_method_is_default():
    """Happy path: an explicit DEFAULT control_method does not suppress dispatch.

    Locks in that the issue #895 override guard only blocks *non-DEFAULT*
    control methods — when the pipeline's own winner is the DEFAULT handler
    (which sunset itself is a variant of), the sunset dispatch still fires.
    """
    coord = _make_coord(sunset_pos=0, pipeline_control_method=ControlMethod.DEFAULT)
    _seed_sunset_state(coord, prev=False, current_is_sunset=True)

    await coord._check_sunset_window_transition()


# ---------------------------------------------------------------------------
# Issue #1287: end-of-window position (issue #625) must not open the
# sunset-window latch — the configured sunset BOUNDARY must own the edge,
# not the "a night-type default is in effect" flag ``compute_effective_default``
# returns for both the end-of-window AND the sunset position.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_no_dispatch_at_end_time_when_end_of_window_position_holds():
    """Regression for issue #1287.

    Reporter's config: end_time clock-closes the window at 19:00, an
    end-of-window position of 60 holds until astral sunset (21:04 local),
    and ``sunset_offset=60`` pushes the real sunset BOUNDARY to 22:04. At
    18:59 (window still open) and 19:00 (window just clock-closed) the
    astral sunset boundary has NOT passed, so the tracker's False→True
    latch must not open at end_time — dispatching the raw configured
    sunset position (16) three hours early is exactly the bug:
    ``compute_effective_default`` pairs the end-of-window position with
    ``is_sunset_active=True`` (a "night-type default is in effect" flag),
    and the tracker mis-reads that as "the configured sunset boundary has
    passed."
    """
    coord = _make_boundary_test_coord(
        end_of_window_pos=60,
        sunset_pos=16,
        sunset_hour=21,
        sunset_minute=4,
        sunset_offset=60,
    )
    today = _dt.date.today()

    coord._time_mgr.before_end_time = True
    now = _dt.datetime(today.year, today.month, today.day, 18, 59, 0)
    with _freeze_helpers_now(now):
        await coord._check_sunset_window_transition()

    coord._time_mgr.before_end_time = False
    now = _dt.datetime(today.year, today.month, today.day, 19, 0, 0)
    with _freeze_helpers_now(now):
        await coord._check_sunset_window_transition()

    coord._cmd_svc.apply_position.assert_not_called()
    assert "sunset_window_opened" not in [
        e["event"] for e in coord._event_buffer.snapshot()
    ]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_dispatch_at_configured_sunset_boundary_after_end_of_window_hold():
    """Regression for issue #1287: the real 22:04 boundary must not be swallowed.

    Same config as the sibling test above. Under the bug, the False→True
    edge is consumed the moment end-of-window position first reports
    ``is_sunset_active=True`` (at/after 19:00), so by 22:05 — the moment the
    configured sunset boundary has genuinely passed — ``_prev_sunset_active``
    is already True and no transition is left to detect: the sunset
    position never dispatches at all. Ticking 19:00 → 21:00 → 22:05 must
    produce exactly one dispatch, of the configured sunset position (16),
    on the 22:05 tick.
    """
    coord = _make_boundary_test_coord(
        end_of_window_pos=60,
        sunset_pos=16,
        sunset_hour=21,
        sunset_minute=4,
        sunset_offset=60,
    )
    today = _dt.date.today()
    coord._time_mgr.before_end_time = False  # window already clock-closed

    for hour, minute in ((19, 0), (21, 0), (22, 5)):
        now = _dt.datetime(today.year, today.month, today.day, hour, minute, 0)
        with _freeze_helpers_now(now):
            await coord._check_sunset_window_transition()

    coord._cmd_svc.apply_position.assert_called_once()
    sent_pos = coord._cmd_svc.apply_position.call_args.args[1]
    assert sent_pos == 16

    coord._cmd_svc.apply_position.assert_called_once()
