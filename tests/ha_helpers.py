"""Shared helpers and fixtures for real Home Assistant integration tests.

These helpers build minimal but valid config entries and HA states that
satisfy async_setup_entry for Adaptive Cover Pro.

Import in test files that use the real ``hass`` fixture from
pytest-homeassistant-custom-component.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.adaptive_cover_pro.const import (
    CONF_AZIMUTH,
    CONF_DEFAULT_HEIGHT,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_DISTANCE,
    CONF_ENTITIES,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_HEIGHT_WIN,
    CONF_INVERSE_STATE,
    CONF_MANUAL_IGNORE_INTERMEDIATE,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_MANUAL_OVERRIDE_RESET,
    CONF_MANUAL_THRESHOLD,
    CONF_MAX_ELEVATION,
    CONF_MAX_POSITION,
    CONF_MIN_ELEVATION,
    CONF_MIN_POSITION,
    CONF_ENABLE_MAX_POSITION,
    CONF_ENABLE_MIN_POSITION,
    CONF_RETURN_SUNSET,
    CONF_SENSOR_TYPE,
    CONF_SILL_HEIGHT,
    CONF_START_TIME,
    CONF_END_TIME,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_WINDOW_DEPTH,
    DOMAIN,
    CoverType,
)

# ---------------------------------------------------------------------------
# Minimal valid options for each cover type
# ---------------------------------------------------------------------------

VERTICAL_OPTIONS: dict[str, Any] = {
    CONF_ENTITIES: ["cover.test_blind"],
    CONF_AZIMUTH: 180,
    CONF_FOV_LEFT: 45,
    CONF_FOV_RIGHT: 45,
    CONF_HEIGHT_WIN: 2.1,
    CONF_DISTANCE: 0.5,
    CONF_DEFAULT_HEIGHT: 50,
    CONF_WINDOW_DEPTH: 0.0,
    CONF_SILL_HEIGHT: 0.0,
    CONF_MIN_ELEVATION: None,
    CONF_MAX_ELEVATION: None,
    CONF_MIN_POSITION: 0,
    CONF_MAX_POSITION: 100,
    CONF_ENABLE_MIN_POSITION: False,
    CONF_ENABLE_MAX_POSITION: False,
    CONF_SUNSET_POS: None,
    CONF_SUNSET_OFFSET: 0,
    CONF_SUNRISE_OFFSET: 0,
    CONF_RETURN_SUNSET: False,
    CONF_INVERSE_STATE: False,
    CONF_DELTA_POSITION: 5,
    CONF_DELTA_TIME: 2,
    CONF_MANUAL_OVERRIDE_DURATION: {"hours": 1, "minutes": 0, "seconds": 0},
    CONF_MANUAL_OVERRIDE_RESET: False,
    CONF_MANUAL_THRESHOLD: 5,
    CONF_MANUAL_IGNORE_INTERMEDIATE: False,
    CONF_START_TIME: "08:00:00",
    CONF_END_TIME: "20:00:00",
}

HORIZONTAL_OPTIONS: dict[str, Any] = {
    **VERTICAL_OPTIONS,
    "length_awning": 2.1,
    "angle": 0,
}

TILT_OPTIONS: dict[str, Any] = {
    **VERTICAL_OPTIONS,
    "slat_depth": 0.02,
    "slat_distance": 0.03,
    "tilt_mode": "mode1",
}


# ---------------------------------------------------------------------------
# Config entry factories
# ---------------------------------------------------------------------------


def make_config_entry(
    name: str = "Test Cover",
    cover_type: str = CoverType.BLIND,
    options: dict[str, Any] | None = None,
    entry_id: str = "test_entry_01",
) -> MagicMock:
    """Build a minimal mock ConfigEntry (does NOT register with HA).

    For real HA setup use ``setup_integration`` below.
    """
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = entry_id
    entry.domain = DOMAIN
    entry.data = {"name": name, CONF_SENSOR_TYPE: cover_type}
    entry.options = options if options is not None else dict(VERTICAL_OPTIONS)
    if cover_type == CoverType.AWNING:
        entry.options = options if options is not None else dict(HORIZONTAL_OPTIONS)
    elif cover_type == CoverType.TILT:
        entry.options = options if options is not None else dict(TILT_OPTIONS)
    entry.title = name
    return entry


async def setup_integration(
    hass: HomeAssistant,
    name: str = "Test Cover",
    cover_type: str = CoverType.BLIND,
    options: dict[str, Any] | None = None,
    entry_id: str = "test_entry_01",
) -> ConfigEntry:
    """Register a config entry and call async_setup_entry.

    Mocks the coordinator's first refresh to avoid real sun/cover queries.
    Returns the ConfigEntry.
    """
    opts = options if options is not None else dict(VERTICAL_OPTIONS)
    if cover_type == CoverType.AWNING and options is None:
        opts = dict(HORIZONTAL_OPTIONS)
    elif cover_type == CoverType.TILT and options is None:
        opts = dict(TILT_OPTIONS)

    hass.states.async_set(
        "sun.sun",
        "above_horizon",
        {
            "azimuth": 180.0,
            "elevation": 45.0,
            "rising": True,
            "next_rising": datetime.datetime.now(datetime.UTC).isoformat(),
            "next_setting": datetime.datetime.now(datetime.UTC).isoformat(),
        },
    )
    hass.states.async_set(
        "cover.test_blind",
        "open",
        {"current_position": 100, "supported_features": 143},
    )

    # Use HA's own MockConfigEntry for proper registration
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": name, CONF_SENSOR_TYPE: cover_type},
        options=opts,
        entry_id=entry_id,
        title=name,
    )
    mock_entry.add_to_hass(hass)

    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    return mock_entry


def _patch_coordinator_refresh():
    """Patch the coordinator's first refresh so tests don't need real sun data."""
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    return patch.object(
        AdaptiveDataUpdateCoordinator,
        "async_config_entry_first_refresh",
        new_callable=AsyncMock,
    )


# ---------------------------------------------------------------------------
# Coordinator-shaped mock wiring
# ---------------------------------------------------------------------------


def wire_dispatch_frame(
    coord: Any, options: dict[str, Any], *, cover_type: str = CoverType.BLIND
) -> Any:
    """Give a coordinator-shaped mock the inputs the dispatch boundary reads.

    ``async_apply_user_position`` maps its logical input into the cover's
    dispatch frame before handing it to ``CoverCommandService`` (#1027), which
    means every fixture that binds the real method also needs the frame inputs
    the coordinator's own ``__init__`` / ``_update_options`` would have set.
    Everything here is derived from *options* exactly the way the coordinator
    derives it, and ``position_axis_inverted`` is evaluated through the real
    property, so no fixture ever hand-rolls the inversion formula it is meant
    to be exercising.

    The min-mode floor clamp is bound for the same reason (#1118). It runs
    before the frame mapping on every user command and is shared with
    ``user_dispatch_position``, the accessor the fan-out seams name their
    dispatched target through — so a mock that binds the real
    ``async_apply_user_position`` but leaves the clamp mocked dispatches
    ``int(MagicMock())`` instead of a position.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_INTERP,
        CONF_INTERP_END,
        CONF_INTERP_LIST,
        CONF_INTERP_LIST_NEW,
        CONF_INTERP_START,
    )
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )
    from custom_components.adaptive_cover_pro.cover_types import get_policy

    coord._policy = get_policy(cover_type)
    coord._inverse_state = bool(options.get(CONF_INVERSE_STATE, False))
    coord._use_interpolation = bool(options.get(CONF_INTERP, False))
    coord.start_value = options.get(CONF_INTERP_START)
    coord.end_value = options.get(CONF_INTERP_END)
    coord.normal_list = options.get(CONF_INTERP_LIST)
    coord.new_list = options.get(CONF_INTERP_LIST_NEW)
    coord.logger = MagicMock()
    coord.position_axis_inverted = (
        AdaptiveDataUpdateCoordinator.position_axis_inverted.fget(coord)
    )
    coord._to_cover_frame = AdaptiveDataUpdateCoordinator._to_cover_frame.__get__(coord)
    coord._entity_target = AdaptiveDataUpdateCoordinator._entity_target.__get__(coord)
    for name in (
        "_build_user_command_snapshot",
        "_clamp_to_active_floor",
        "user_dispatch_position",
    ):
        setattr(
            coord, name, getattr(AdaptiveDataUpdateCoordinator, name).__get__(coord)
        )
    return coord


def _bare_coordinator(**overrides: Any) -> Any:
    """Build an ``object.__new__`` coordinator stub wired for ``async_shutdown``.

    ``async_shutdown`` touches a fixed set of collaborators regardless of
    which handle a given test cares about: ``_grace_mgr.cancel_all()``,
    ``_cancel_motion_timeout()``, ``_cancel_weather_timeout()``,
    ``_sensor_health.shutdown()``, ``_repair.shutdown()``, ``_cmd_svc.stop()``,
    and five ``if self._X_unsub is not None: ...`` cancel blocks
    (``_forecast_unsub``, ``_forecast_max_unsub``, ``_gate_fallback_unsub``,
    ``_refresh_after_unsub``, ``_custom_position_hold_unsub``). Every test
    that exercises ``async_shutdown`` against a from-scratch stub needs all of
    these stubbed or it raises ``AttributeError`` — this was hand-mirrored
    across ``tests/test_issue_632_daytime_gate.py`` and
    ``tests/test_coordinator_healthchecks.py`` and broke both files the last
    time ``async_shutdown`` gained a new handle (issue #1012's per-input hold
    wake). Pass keyword overrides (e.g. ``gate_fallback_unsub=my_cancel_spy``)
    to replace any default before returning; unprefixed attribute names are
    used (``gate_fallback_unsub``, not ``_gate_fallback_unsub``).
    """
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._grace_mgr = MagicMock()
    coord._cancel_motion_timeout = MagicMock()
    coord._cancel_weather_timeout = MagicMock()
    coord._sensor_health = MagicMock()
    coord._repair = MagicMock()
    coord._cmd_svc = MagicMock()
    coord._forecast_unsub = None
    coord._forecast_max_unsub = None
    coord._gate_fallback_unsub = None
    coord._refresh_after_unsub = None
    coord._custom_position_hold_unsub = None
    for name, value in overrides.items():
        setattr(coord, f"_{name}", value)
    return coord


# ---------------------------------------------------------------------------
# Convenience assertions
# ---------------------------------------------------------------------------


def get_entity_ids_for_entry(
    hass: HomeAssistant, entry: ConfigEntry, platform: str
) -> list[str]:
    """Return all entity_ids for a config entry on a given platform."""
    reg = er.async_get(hass)
    return [
        e.entity_id
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id and e.domain == platform
    ]


def assert_entities_registered(
    hass: HomeAssistant, entry: ConfigEntry, platform: str, min_count: int
) -> list[str]:
    """Assert at least min_count entities are registered for the entry/platform."""
    ids = get_entity_ids_for_entry(hass, entry, platform)
    assert (
        len(ids) >= min_count
    ), f"Expected >= {min_count} {platform} entities, got {len(ids)}: {ids}"
    return ids
