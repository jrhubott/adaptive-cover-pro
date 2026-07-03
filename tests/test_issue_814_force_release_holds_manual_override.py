"""Regression guard: safety-release force-path must honor a MANUAL hold (#814).

Issue #814 reported a manual-override hold silently jumping from a held
position (e.g. 100%) to the solar would-be position when a priority-100
custom-position slot released (``safety_release=True``). Root cause: in
v2.29.0 ``ManualOverrideHandler`` did not set ``skip_command``, so
``_dispatch_to_cover`` fell through and force-dispatched the solar would-be
position — already fixed by PR #810 (issue #809), which makes the handler set
``skip_command=held_position is not None``.

No existing test ties the **safety-release edge** to the **MANUAL-hold
skip**:

- ``tests/test_manual_override_hold_skip.py`` exercises ``_dispatch_to_cover``
  directly, never the ``safety_release=True`` release cycle.
- ``tests/test_safety_priority_custom_position.py`` exercises the pipeline
  registry only, with no dispatch assertion.
- ``TestForceOverrideRelease`` / PR #366's release-edge tests assert
  ``force=True`` reaches the release path but never assert that a MANUAL-hold
  winner suppresses the resulting command.

This test locks that intersection: a ``safety_release=True`` cycle through
``coordinator.async_handle_state_change`` whose pipeline winner is a MANUAL
hold must not call ``apply_position`` and must record a
``manual_override_hold`` skip. The pipeline result is produced by the real
``ManualOverrideHandler`` (not hand-rolled) so a regression that reverts
``skip_command`` to unconditional False fails this test, not just a
fixture.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers import ManualOverrideHandler

from tests.test_pipeline.conftest import make_snapshot


def _make_release_coordinator(pipeline_result):
    """Build a minimal coordinator for the safety-release force-path.

    Construction mirrors ``tests/test_manual_override_hold_skip.py`` (a bare
    instance via ``object.__new__``), but here ``async_handle_state_change``
    is exercised end-to-end rather than ``_dispatch_to_cover`` directly, so
    the release force-path's own skip_command gate is under test.
    ``_pipeline_is_safety_handler`` / ``_pipeline_bypasses_auto_control`` are
    real properties computed from ``pipeline_result`` — they read False here
    because the MO handler leaves ``is_safety``/``bypass_auto_control`` at
    their dataclass defaults, so no separate stubbing is needed for them.
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._inverse_state = False
    coord.entities = ["cover.bedroom"]
    coord.state_change = True
    coord._last_state_change_entity = None
    coord._custom_position_template_trigger = False
    coord._time_mgr = MagicMock(clock_window_open=True)
    coord._check_sun_validity_transition = MagicMock(return_value=False)
    coord._build_position_context = MagicMock(return_value=MagicMock())
    coord._pipeline_result = pipeline_result

    cmd_svc = MagicMock()
    cmd_svc.apply_position = AsyncMock(return_value=("sent", None))
    cmd_svc.record_skipped_action = MagicMock()
    coord._cmd_svc = cmd_svc

    return coord


@pytest.mark.unit
@pytest.mark.asyncio
async def test_safety_release_with_manual_hold_winner_does_not_dispatch():
    """A safety-release cycle whose winner is a MANUAL hold must not move the cover.

    Reproduces the reporter's scenario: a priority-100 custom-position slot
    (e.g. a contact sensor) flips off, the coordinator marks the cycle
    ``safety_release=True``, but manual override is holding the cover at
    100%. The release force-path must honor ``skip_command`` and record a
    ``manual_override_hold`` skip instead of dispatching the solar would-be
    position (11%).
    """
    snap = make_snapshot(
        manual_override_active=True,
        direct_sun_valid=False,
        current_cover_position=100,
        default_position=11,
    )
    result = ManualOverrideHandler().evaluate(snap)
    assert result is not None
    assert result.control_method is ControlMethod.MANUAL
    assert result.skip_command is True
    assert result.held_position == 100
    assert result.position == 11

    coord = _make_release_coordinator(result)

    await coord.async_handle_state_change(
        11,
        {},
        {"binary_sensor.window_contact"},
        safety_release=True,
        target_changed=True,
    )

    coord._cmd_svc.apply_position.assert_not_called()
    coord._cmd_svc.record_skipped_action.assert_called_once()
    args, kwargs = coord._cmd_svc.record_skipped_action.call_args
    assert args[1] == "manual_override_hold"
    extras = kwargs.get("extras", {})
    assert extras["held_position"] == 100
    assert extras["would_be_position"] == 11
