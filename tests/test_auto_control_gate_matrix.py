"""Control-gate matrix: every coordinator entry point that can produce a cover command.

Invariant under test
---------------------
When ``automatic_control=False``, any coordinator call-site that is NOT a
declared safety bypass must NOT invoke ``apply_position`` with
``context.force=True``.  Safety bypasses (ForceOverrideHandler,
WeatherOverrideHandler with ``bypass_auto_control=True``) are expected to call
``apply_position`` with ``force=True`` regardless of the toggle.

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

Decision table (all rows assume ``automatic_control=False``)
------------------------------------------------------------
+--------------------------------+------------------+--------------------------+
| id                             | entry point      | is_safety_bypass         |
+================================+==================+==========================+
| manual_override_expiry         | _async_send_…    | False (coord gates it)   |
| state_change_force_override    | async_handle_…   | True  (force=True ok)    |
| state_change_weather_bypass    | async_handle_…   | True  (force=True ok)    |
| first_refresh_safety           | async_handle_…   | True  (force=True ok)    |
| window_close_return_sunset     | _check_time_…    | False (coord gates it)   |
+--------------------------------+------------------+--------------------------+

Rows for state_change_solar and first_refresh_non_safety are omitted because
those paths call apply_position with ``force=False`` and rely on the *service*
gate (auto_control block in CoverCommandService.apply_position).  That layer
is covered by ``test_position_reconciliation.py``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from collections.abc import Callable, Awaitable
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from custom_components.adaptive_cover_pro.coordinator import AdaptiveDataUpdateCoordinator
from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.managers.cover_command import PositionContext
from custom_components.adaptive_cover_pro.managers.toggles import ToggleManager
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pipeline_result(bypass: bool) -> PipelineResult:
    return PipelineResult(
        position=50,
        control_method=ControlMethod.FORCE if bypass else ControlMethod.SOLAR,
        reason="force_override" if bypass else "solar",
        bypass_auto_control=bypass,
    )


def _base_coord() -> AdaptiveDataUpdateCoordinator:
    """Minimal coordinator with automatic_control=False and a captured apply_position mock."""
    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._toggles = ToggleManager()
    coord.automatic_control = False
    coord.entities = [MagicMock()]

    cmd_svc = MagicMock()
    cmd_svc.apply_position = AsyncMock(return_value=("sent", ""))
    coord._cmd_svc = cmd_svc

    # _build_position_context: preserve the force kwarg in the returned PositionContext
    # so callers can inspect which force value the coordinator actually passed.
    def _fake_build_ctx(entity, options, *, force=False, sun_just_appeared=False):
        return PositionContext(
            auto_control=False,  # reflects automatic_control=False
            manual_override=False,
            sun_just_appeared=sun_just_appeared,
            min_change=2,
            time_threshold=0,
            special_positions=[0, 100],
            force=force,
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
        ctx = call.kwargs.get("context") or (call.args[3] if len(call.args) > 3 else None)
        if ctx is not None and getattr(ctx, "force", False):
            result.append(call)
    return result


# ---------------------------------------------------------------------------
# Matrix definition
# ---------------------------------------------------------------------------


@dataclass
class MatrixCase:
    """One row of the control-gate decision table."""

    id: str
    is_safety_bypass: bool
    setup: Callable[[AdaptiveDataUpdateCoordinator], None]
    trigger: Callable[[AdaptiveDataUpdateCoordinator], Awaitable[None]]


async def _trigger_manual_override_expiry(coord):
    coord._time_mgr = MagicMock()
    coord._time_mgr.is_active = True  # inside time window
    coord._check_sun_validity_transition = MagicMock(return_value=False)
    await coord._async_send_after_override_clear(50, {})


async def _trigger_state_change_force_override(coord):
    coord._pipeline_result = _make_pipeline_result(bypass=True)
    coord._time_mgr = MagicMock()
    coord._time_mgr.is_active = True
    coord._check_sun_validity_transition = MagicMock(return_value=False)
    coord.state_change = True
    with patch.object(
        type(coord), "is_force_override_active", new_callable=PropertyMock, return_value=False
    ):
        await coord.async_handle_state_change(50, {}, prev_force_override=False)


async def _trigger_state_change_weather_bypass(coord):
    coord._pipeline_result = PipelineResult(
        position=50,
        control_method=ControlMethod.WEATHER,
        reason="weather_override",
        bypass_auto_control=True,
    )
    coord._time_mgr = MagicMock()
    coord._time_mgr.is_active = True
    coord._check_sun_validity_transition = MagicMock(return_value=False)
    coord.state_change = True
    with patch.object(
        type(coord), "is_force_override_active", new_callable=PropertyMock, return_value=False
    ):
        await coord.async_handle_state_change(50, {}, prev_force_override=False)


async def _trigger_first_refresh_safety(coord):
    coord._pipeline_result = _make_pipeline_result(bypass=True)
    coord._time_mgr = MagicMock()
    coord._time_mgr.is_active = True
    coord.first_refresh = True
    coord._is_reload = False
    coord._check_sun_validity_transition = MagicMock(return_value=False)
    await coord.async_handle_first_refresh(50, {})


async def _trigger_window_close_return_sunset(coord):
    coord._track_end_time = True

    async def _invoke(track_end_time, refresh_callback):
        await refresh_callback()

    coord._time_mgr = MagicMock()
    coord._time_mgr.check_transition = _invoke
    await coord._check_time_window_transition(dt.datetime.now(dt.UTC))


CONTROL_GATE_MATRIX: list[MatrixCase] = [
    MatrixCase(
        id="manual_override_expiry",
        is_safety_bypass=False,
        setup=lambda _: None,
        trigger=_trigger_manual_override_expiry,
    ),
    MatrixCase(
        id="state_change_force_override",
        is_safety_bypass=True,
        setup=lambda _: None,
        trigger=_trigger_state_change_force_override,
    ),
    MatrixCase(
        id="state_change_weather_bypass",
        is_safety_bypass=True,
        setup=lambda _: None,
        trigger=_trigger_state_change_weather_bypass,
    ),
    MatrixCase(
        id="first_refresh_safety",
        is_safety_bypass=True,
        setup=lambda _: None,
        trigger=_trigger_first_refresh_safety,
    ),
    MatrixCase(
        id="window_close_return_sunset",
        is_safety_bypass=False,
        setup=lambda _: None,
        trigger=_trigger_window_close_return_sunset,
    ),
]


# ---------------------------------------------------------------------------
# The matrix test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CONTROL_GATE_MATRIX, ids=lambda c: c.id)
@pytest.mark.asyncio
@pytest.mark.unit
async def test_auto_control_gate(case: MatrixCase):
    """Invariant: non-safety paths must not call apply_position(force=True) when auto_control=OFF.

    If this test fails on a non-safety row, a coordinator call site is missing
    an ``automatic_control`` gate.  Add the gate (matching the pattern in
    ``_async_send_after_override_clear``) and update ``test_force_apply_allowlist.py``.

    If this test fails on a safety row, a legitimate safety bypass stopped
    working — investigate the handler's ``bypass_auto_control`` flag.
    """
    coord = _base_coord()
    case.setup(coord)
    await case.trigger(coord)

    forced = _force_calls(coord)

    if case.is_safety_bypass:
        assert forced, (
            f"[{case.id}] Safety bypass must call apply_position(context.force=True) "
            f"when automatic_control=False, but no force=True call was recorded. "
            f"All calls: {coord._cmd_svc.apply_position.call_args_list}"
        )
    else:
        assert not forced, (
            f"[{case.id}] Non-safety path called apply_position(context.force=True) "
            f"with automatic_control=False — add an automatic_control gate before the "
            f"apply_position call (see _async_send_after_override_clear for the pattern). "
            f"Offending calls: {forced}"
        )
