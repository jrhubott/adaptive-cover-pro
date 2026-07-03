"""Issue #809 — venetian manual-override engage must not open a closed blind.

The reporter's exact scenario: a venetian cover physically closed at 0 (blackout
scene), a custom_position slot holding pos 0 / tilt 2, ``default_percentage=100``.
When manual override engages, the pipeline used to dispatch the *would-be default*
(100 → open_cover) while the log claimed it was "holding 0%".  The handler now
emits ``skip_command=True`` so the composed result holds the cover, and the
coordinator dispatch records a ``manual_override_hold`` skip instead of opening.

This exercises the real chain: ManualOverrideHandler → PipelineRegistry →
``_dispatch_to_cover``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers import ManualOverrideHandler
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
    DefaultHandler,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
)

from tests.test_pipeline.conftest import make_snapshot


def _venetian_engage_snapshot():
    """Build the reporter's engage snapshot: venetian at 0, slot 0/tilt 2, default 100."""
    return make_snapshot(
        cover_type="cover_venetian",
        manual_override_active=True,
        direct_sun_valid=False,
        current_cover_position=0,
        default_position=100,
        custom_position_sensors=[
            CustomPositionSensorState(
                entity_ids=("binary_sensor.blackout",),
                is_on=True,
                position=0,
                priority=78,
                min_mode=False,
                use_my=False,
                tilt=2,
                slot=2,
                active_entity_ids=("binary_sensor.blackout",),
            )
        ],
    )


def _make_dispatch_coordinator(pipeline_result):
    """Minimal coordinator with a pipeline result for _dispatch_to_cover."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._inverse_state = False
    coord._pipeline_result = pipeline_result
    cmd_svc = MagicMock()
    cmd_svc.apply_position = AsyncMock(return_value=("sent", None))
    cmd_svc.record_skipped_action = MagicMock()
    coord._cmd_svc = cmd_svc
    return coord


@pytest.mark.asyncio
async def test_override_engage_does_not_drive_venetian_open() -> None:
    """Engaging manual override on a closed venetian holds — it must not open it."""
    snap = _venetian_engage_snapshot()
    registry = PipelineRegistry(
        [
            ManualOverrideHandler(),
            CustomPositionHandler(slot=2, position=0, priority=78, tilt=2),
            DefaultHandler(),
        ]
    )
    result = registry.evaluate(snap)

    # The manual-override handler wins and produces a genuine hold.
    assert result.control_method is ControlMethod.MANUAL
    assert result.skip_command is True
    assert result.held_position == 0
    # The resolved-target signature reflects the hold so dispatch suppresses it.
    assert result.position == 100  # would-be shadow, never dispatched

    # Dispatch the resolved target: it must NOT drive the cover open (state=100).
    coord = _make_dispatch_coordinator(result)
    ctx = MagicMock()
    await coord._dispatch_to_cover("cover.bedroom_bedroom_cover", 100, "solar", ctx)

    coord._cmd_svc.apply_position.assert_not_called()
    coord._cmd_svc.record_skipped_action.assert_called_once()
    args, _ = coord._cmd_svc.record_skipped_action.call_args
    assert args[1] == "manual_override_hold"
