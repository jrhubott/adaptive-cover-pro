"""Boot-time performance regression guards for issue #437.

These tests pin down the contract that platform setup must NOT
recompute the position forecast inline. Before the fix:
- 14 switches per entry each `await coordinator.async_refresh()` in
  `async_added_to_hass`.
- Each refresh fired `_handle_coordinator_update → async_write_ha_state`
  on the `position_forecast` sensor.
- Every state-write ran `_safe_forecast` twice (value_fn + attrs_fn).

With 10 entries this added thousands of forecast computations on the
event loop during stage-2 bootstrap and triggered HA's "Setup of switch
platform adaptive_cover_pro is taking over 10 seconds" warning.

After the fix:
- The forecast lives on `coordinator.data.position_forecast`.
- The coordinator schedules **one** initial executor compute via
  `hass.async_create_background_task` and a periodic timer.
- Switch refreshes and sensor state writes never invoke the build helper.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
)
from tests.ha_helpers import VERTICAL_OPTIONS

pytestmark = pytest.mark.integration


@pytest.mark.integration
async def test_setup_schedules_at_most_one_initial_forecast(
    hass: HomeAssistant,
) -> None:
    """One initial background forecast task is scheduled — not one per switch.

    `_start_forecast_scheduler` runs in `async_config_entry_first_refresh`,
    which is itself called once per entry. The ~14 switch refreshes in
    `async_added_to_hass` MUST NOT each trigger another schedule.
    """
    recompute_mock = AsyncMock()

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "BootPerf", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="bootperf_01",
        title="BootPerf",
    )
    entry.add_to_hass(hass)

    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    with patch.object(
        AdaptiveDataUpdateCoordinator,
        "async_recompute_forecast",
        new=recompute_mock,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Exactly one schedule firing: the initial background task.
    # Periodic timer ticks should NOT have fired during the synchronous
    # part of setup (timer is on a 5-minute cadence; setup is < 1 s).
    assert recompute_mock.call_count == 1


@pytest.mark.integration
async def test_switch_async_added_to_hass_does_not_retrigger_forecast(
    hass: HomeAssistant,
) -> None:
    """Each switch's `async_added_to_hass` calls `coordinator.async_refresh()`.

    With ~14 switches per entry, the boot fan-out runs ~14 refreshes
    back-to-back. None of them must call `async_recompute_forecast` —
    that's the periodic timer's job.

    We assert "exactly 1" because `_start_forecast_scheduler` fires the
    initial recompute as a background task during
    `async_config_entry_first_refresh`. Any number greater than 1 means a
    switch refresh path has regressed and is recomputing the forecast.
    """
    recompute_mock = AsyncMock()

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "BootPerf2", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="bootperf_02",
        title="BootPerf2",
    )
    entry.add_to_hass(hass)

    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    with patch.object(
        AdaptiveDataUpdateCoordinator,
        "async_recompute_forecast",
        new=recompute_mock,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        # Simulate a few more refreshes — same as switches firing during
        # async_added_to_hass.
        coordinator = entry.runtime_data
        for _ in range(14):
            await coordinator.async_refresh()
        await hass.async_block_till_done()

    # Still exactly 1: refreshes do not retrigger recompute.
    assert recompute_mock.call_count == 1


@pytest.mark.integration
async def test_forecast_recompute_routes_through_executor(hass: HomeAssistant) -> None:
    """The recompute helper hands off `build_forecast_for_coord` to the executor.

    Even though the test harness intercepts `async_add_executor_job` and
    runs mock targets synchronously, the call IS recorded — so we can
    verify the helper went through the executor route rather than calling
    `build_forecast_for_coord` directly on the event loop.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "BootPerf3", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="bootperf_03",
        title="BootPerf3",
    )
    entry.add_to_hass(hass)

    # Wrap the executor job hook so we can observe what was offloaded.
    executor_calls: list = []
    orig = hass.async_add_executor_job

    def _spy(target, *args):
        executor_calls.append(
            target.__name__ if hasattr(target, "__name__") else target
        )
        return orig(target, *args)

    hass.async_add_executor_job = _spy

    try:
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    finally:
        hass.async_add_executor_job = orig

    # The initial forecast recompute went through the executor.
    assert "build_forecast_for_coord" in executor_calls
