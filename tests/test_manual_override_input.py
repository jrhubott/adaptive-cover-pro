"""Tests for input-sensor manual-override engagement (issue #688).

A configured input binary sensor (e.g. a Shelly wall-switch input) that
transitions off→on means the user physically operated the cover. ACP engages
manual override on every cover in the instance, drops the latched target, and
pauses auto-control for the configured duration.
"""

from __future__ import annotations

import datetime as dt
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.managers.manual_override import (
    AdaptiveCoverManager,
)

pytestmark = pytest.mark.unit


def _make_manager(covers: list[str]) -> AdaptiveCoverManager:
    manager = AdaptiveCoverManager(
        hass=MagicMock(),
        reset_duration={"hours": 1},
        logger=MagicMock(),
    )
    manager.add_covers(covers)
    return manager


# ---------------------------------------------------------------------------
# Step 1: manager engages override across every tracked cover
# ---------------------------------------------------------------------------


def test_engage_from_external_marks_all_covers() -> None:
    """Every tracked cover is flagged manual, timestamped, and fires on_engaged."""
    covers = ["cover.living_room", "cover.bedroom"]
    manager = _make_manager(covers)
    on_engaged = MagicMock()
    manager.set_transition_callbacks(on_engaged=on_engaged)

    before = dt.datetime.now(dt.UTC)
    manager.engage_manual_override_from_external(reason="input_sensor")

    assert manager.binary_cover_manual is True
    for cover in covers:
        assert manager.is_cover_manual(cover) is True
        assert cover in manager.manual_control_time
        assert manager.manual_control_time[cover] >= before
    # on_engaged fires once per cover so the command service discards each
    # latched target.
    assert on_engaged.call_count == len(covers)
    engaged = {call.args[0] for call in on_engaged.call_args_list}
    assert engaged == set(covers)


# ---------------------------------------------------------------------------
# Step 2: each press re-arms the timer (overwrite, not setdefault) and does
# not re-fire the engaged edge once already manual.
# ---------------------------------------------------------------------------


def test_engage_from_external_rearms_timer() -> None:
    """A second press overwrites the timestamp (fresh duration) but the
    already-manual cover does not fire on_engaged again.
    """
    cover = "cover.living_room"
    manager = _make_manager([cover])
    on_engaged = MagicMock()
    manager.set_transition_callbacks(on_engaged=on_engaged)

    manager.engage_manual_override_from_external(reason="input_sensor")
    first_ts = manager.manual_control_time[cover]
    assert on_engaged.call_count == 1

    time.sleep(0.01)  # guarantee a measurable clock advance

    manager.engage_manual_override_from_external(reason="input_sensor")
    second_ts = manager.manual_control_time[cover]

    assert second_ts > first_ts
    # Already manual → edge does not re-fire.
    assert on_engaged.call_count == 1


# ---------------------------------------------------------------------------
# Step 3: coordinator handler engages only on the off→on edge
# ---------------------------------------------------------------------------


def _make_coordinator():
    """Bind the real input-change handler onto a MagicMock coordinator."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coordinator = MagicMock()
    coordinator.logger = MagicMock()
    coordinator.manager = MagicMock()
    coordinator.state_change = False
    coordinator.async_refresh = AsyncMock()
    coordinator.async_check_manual_override_input_change = (
        AdaptiveDataUpdateCoordinator.async_check_manual_override_input_change.__get__(
            coordinator
        )
    )
    return coordinator


def _make_event(entity_id: str, old: str | None, new: str | None) -> MagicMock:
    def _state(value: str | None):
        if value is None:
            return None
        st = MagicMock()
        st.state = value
        return st

    event = MagicMock()
    event.data = {
        "entity_id": entity_id,
        "old_state": _state(old),
        "new_state": _state(new),
    }
    return event


@pytest.mark.asyncio
async def test_handler_engages_on_off_to_on_edge() -> None:
    """off→on engages override and triggers a refresh."""
    coordinator = _make_coordinator()
    event = _make_event("binary_sensor.cover_input_0", old="off", new="on")

    await coordinator.async_check_manual_override_input_change(event)

    coordinator.manager.engage_manual_override_from_external.assert_called_once()
    assert coordinator.state_change is True
    coordinator.async_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_handler_ignores_on_to_on() -> None:
    """on→on is no rising edge — nothing engages."""
    coordinator = _make_coordinator()
    event = _make_event("binary_sensor.cover_input_0", old="on", new="on")

    await coordinator.async_check_manual_override_input_change(event)

    coordinator.manager.engage_manual_override_from_external.assert_not_called()
    coordinator.async_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_ignores_none_to_on() -> None:
    """A sensor restored already-on at startup (None→on) does NOT engage."""
    coordinator = _make_coordinator()
    event = _make_event("binary_sensor.cover_input_0", old=None, new="on")

    await coordinator.async_check_manual_override_input_change(event)

    coordinator.manager.engage_manual_override_from_external.assert_not_called()
    coordinator.async_refresh.assert_not_awaited()


# ---------------------------------------------------------------------------
# Step 6: config-flow schema placement + options-flow round-trip
# ---------------------------------------------------------------------------


def test_input_entities_in_manual_override_schema() -> None:
    """The new key lives on MANUAL_OVERRIDE_SCHEMA as a multi entity selector."""
    from homeassistant.helpers import selector

    from custom_components.adaptive_cover_pro import config_flow as cf
    from custom_components.adaptive_cover_pro.const import (
        CONF_MANUAL_OVERRIDE_INPUT_ENTITIES,
    )

    match = next(
        (
            val
            for key, val in cf.MANUAL_OVERRIDE_SCHEMA.schema.items()
            if str(key) == CONF_MANUAL_OVERRIDE_INPUT_ENTITIES
        ),
        None,
    )
    assert match is not None, "input-entities key missing from MANUAL_OVERRIDE_SCHEMA"
    assert isinstance(match, selector.EntitySelector)
    assert match.config.get("multiple") is True


def test_input_entities_in_sync_category() -> None:
    """The key is in the manual_override sync category so options-flow copy works."""
    from custom_components.adaptive_cover_pro import config_flow as cf
    from custom_components.adaptive_cover_pro.const import (
        CONF_MANUAL_OVERRIDE_INPUT_ENTITIES,
    )

    assert CONF_MANUAL_OVERRIDE_INPUT_ENTITIES in cf.SYNC_CATEGORIES["manual_override"]


@pytest.mark.asyncio
async def test_handler_ignores_unavailable() -> None:
    """unavailable/unknown are not 'on' so they don't engage."""
    coordinator = _make_coordinator()
    for new in ("unavailable", "unknown", "off"):
        coordinator.manager.engage_manual_override_from_external.reset_mock()
        coordinator.async_refresh.reset_mock()
        event = _make_event("binary_sensor.cover_input_0", old="off", new=new)

        await coordinator.async_check_manual_override_input_change(event)

        coordinator.manager.engage_manual_override_from_external.assert_not_called()
        coordinator.async_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_ignores_missing_new_state() -> None:
    """A removed entity (new_state None) does nothing."""
    coordinator = _make_coordinator()
    event = _make_event("binary_sensor.cover_input_0", old="on", new=None)

    await coordinator.async_check_manual_override_input_change(event)

    coordinator.manager.engage_manual_override_from_external.assert_not_called()
    coordinator.async_refresh.assert_not_awaited()
