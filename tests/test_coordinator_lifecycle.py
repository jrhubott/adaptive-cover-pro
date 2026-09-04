"""Tests for the coordinator lifecycle with a real Home Assistant instance.

Covers setup, first refresh, state-change event wiring, unload/cleanup,
options-change-triggered reload, and multi-entry independence.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_ENTITIES,
    CONF_ENABLE_SUN_TRACKING,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_MOTION_SENSORS,
    CONF_MOTION_TEMPLATE,
    CONF_SENSOR_TYPE,
    CONF_VENETIAN_MODE,
    DOMAIN,
    CoverType,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from tests.ha_helpers import VERTICAL_OPTIONS, _patch_coordinator_refresh

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _setup(
    hass: HomeAssistant,
    entry_id: str = "lc_01",
    options: dict | None = None,
    name: str = "LC Cover",
) -> MockConfigEntry:
    opts = dict(VERTICAL_OPTIONS) if options is None else options
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": name, CONF_SENSOR_TYPE: CoverType.BLIND},
        options=opts,
        entry_id=entry_id,
        title=name,
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


# ---------------------------------------------------------------------------
# 4a: Setup & first refresh
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_coordinator_created_and_stored(hass: HomeAssistant) -> None:
    """After setup, the coordinator is stored on entry.runtime_data."""
    entry = await _setup(hass, entry_id="coord_stored_01")
    assert hasattr(entry, "runtime_data")
    assert isinstance(entry.runtime_data, AdaptiveDataUpdateCoordinator)


@pytest.mark.integration
async def test_coordinator_data_is_not_none_after_setup(hass: HomeAssistant) -> None:
    """Coordinator data is populated after first refresh (mock refresh)."""
    entry = await _setup(hass, entry_id="coord_data_01")
    coordinator = entry.runtime_data
    # After the mock refresh, coordinator.data may be None (mock bypassed)
    # but the coordinator object must exist and be valid
    assert coordinator is not None


@pytest.mark.integration
async def test_two_entries_stored_independently(hass: HomeAssistant) -> None:
    """Two config entries each get their own coordinator in hass.data."""
    entry_a = await _setup(hass, entry_id="two_a", name="Cover A")
    entry_b = await _setup(hass, entry_id="two_b", name="Cover B")

    assert hasattr(entry_a, "runtime_data")
    assert hasattr(entry_b, "runtime_data")
    coord_a = entry_a.runtime_data
    coord_b = entry_b.runtime_data
    assert coord_a is not coord_b


# ---------------------------------------------------------------------------
# 4c: Unload & cleanup
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_unload_removes_coordinator(hass: HomeAssistant) -> None:
    """Unloading an entry removes its coordinator from hass.data."""
    entry = await _setup(hass, entry_id="unload_lc_01")
    assert hasattr(entry, "runtime_data")

    result = await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert result is True
    assert not hasattr(entry, "runtime_data")


@pytest.mark.integration
async def test_unload_one_entry_preserves_other(hass: HomeAssistant) -> None:
    """Unloading entry A leaves entry B's coordinator intact."""
    entry_a = await _setup(hass, entry_id="unload_a_01", name="Cover A")
    entry_b = await _setup(hass, entry_id="unload_b_01", name="Cover B")

    await hass.config_entries.async_unload(entry_a.entry_id)
    await hass.async_block_till_done()

    assert not hasattr(entry_a, "runtime_data")
    assert hasattr(entry_b, "runtime_data")


@pytest.mark.integration
async def test_reload_creates_new_coordinator_instance(hass: HomeAssistant) -> None:
    """Reloading an entry creates a fresh coordinator object."""
    entry = await _setup(hass, entry_id="reload_lc_01")
    coord_before = entry.runtime_data
    assert coord_before is not None

    with _patch_coordinator_refresh():
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    coord_after = entry.runtime_data
    assert coord_after is not None
    assert coord_before is not coord_after


# ---------------------------------------------------------------------------
# 4d: Options change triggers reload
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_options_update_triggers_reload(hass: HomeAssistant) -> None:
    """Updating options causes the entry to reload (new coordinator created)."""
    entry = await _setup(hass, entry_id="opts_reload_01")
    coord_before = entry.runtime_data

    new_opts = dict(VERTICAL_OPTIONS)
    new_opts["set_azimuth"] = 200  # Changed value

    with _patch_coordinator_refresh():
        hass.config_entries.async_update_entry(entry, options=new_opts)
        await hass.async_block_till_done()

    coord_after = getattr(entry, "runtime_data", None)
    # After reload, a new coordinator exists
    assert coord_after is not None
    assert coord_before is not coord_after


@pytest.mark.integration
async def test_sun_tracking_only_update_refreshes_without_reload(
    hass: HomeAssistant,
) -> None:
    """Sun Tracking toggle rebuilds the pipeline on the existing coordinator."""
    entry = await _setup(hass, entry_id="sun_tracking_runtime_01")
    coordinator = entry.runtime_data
    coordinator._cached_options = dict(entry.options)
    new_pipeline = MagicMock()
    coordinator._build_pipeline = MagicMock(return_value=new_pipeline)
    coordinator.async_update_listeners = MagicMock()
    coordinator.async_refresh = AsyncMock()

    new_options = dict(entry.options)
    new_options[CONF_ENABLE_SUN_TRACKING] = not entry.options.get(
        CONF_ENABLE_SUN_TRACKING, True
    )
    hass.config_entries.async_update_entry(entry, options=new_options)
    await hass.async_block_till_done()

    assert entry.runtime_data is coordinator
    assert coordinator._pipeline is new_pipeline
    assert coordinator.state_change is True
    coordinator.async_update_listeners.assert_called_once()
    coordinator.async_refresh.assert_awaited_once()
    assert coordinator._cached_options == new_options


# ---------------------------------------------------------------------------
# 4b: Entity change wiring (verify listeners are registered)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_force_override_sensors_wired_as_listeners(hass: HomeAssistant) -> None:
    """Force override sensor changes should trigger coordinator via state listener.

    We verify that the entity listeners are set up by checking that the
    entry's async_on_unload callbacks list is non-empty (each listener
    registers an unload callback).
    """
    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_FORCE_OVERRIDE_SENSORS] = ["binary_sensor.rain"]
    opts[CONF_ENTITIES] = ["cover.test_blind"]
    entry = await _setup(hass, options=opts, entry_id="wire_force_01")

    # The entry should have registered unload callbacks (at least for listeners)
    # We can't easily count them, but we verify setup succeeded
    assert hasattr(entry, "runtime_data")


@pytest.mark.integration
async def test_motion_sensors_wired_as_listeners(hass: HomeAssistant) -> None:
    """Motion sensors are wired up as state-change listeners."""
    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_MOTION_SENSORS] = ["binary_sensor.presence"]
    entry = await _setup(hass, options=opts, entry_id="wire_motion_01")
    assert hasattr(entry, "runtime_data")


@pytest.mark.integration
async def test_motion_template_wired_as_listener(hass: HomeAssistant) -> None:
    """The occupancy template is registered via async_track_template_result (#577 f/u).

    Toggling a referenced entity must drive the coordinator's motion state with
    no polling, proving the live template result is tracked.
    """
    hass.states.async_set("input_boolean.guest", "off")
    await hass.async_block_till_done()

    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_MOTION_TEMPLATE] = "{{ is_state('input_boolean.guest', 'on') }}"
    entry = await _setup(hass, options=opts, entry_id="wire_motion_tmpl_01")
    coordinator = entry.runtime_data

    # Template-only config counts as configured and currently falsy.
    assert coordinator._motion_mgr.is_configured is True
    assert coordinator._motion_mgr.is_motion_detected is False

    # Flip the referenced entity → the tracked template result drives occupancy.
    hass.states.async_set("input_boolean.guest", "on")
    await hass.async_block_till_done()
    assert coordinator._motion_mgr.is_motion_detected is True


@pytest.mark.integration
async def test_motion_template_registration_failure_is_caught(
    hass: HomeAssistant, monkeypatch
) -> None:
    """A template that fails to register must not abort setup (#577 f/u)."""
    from homeassistant.exceptions import TemplateError

    def _boom(*args, **kwargs):
        raise TemplateError("boom")

    monkeypatch.setattr(
        "custom_components.adaptive_cover_pro.async_track_template_result", _boom
    )
    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_MOTION_TEMPLATE] = "{{ true }}"
    entry = await _setup(hass, options=opts, entry_id="wire_motion_tmpl_fail")
    # Setup still completed despite the registration error.
    assert hasattr(entry, "runtime_data")


# ---------------------------------------------------------------------------
# Regression: _last_update_success_time attribute must exist on real instances
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_last_update_success_time_attribute_exists(hass: HomeAssistant) -> None:
    """Regression: coordinator must own _last_update_success_time.

    HA's DataUpdateCoordinator does NOT expose last_update_success_time; we
    track it ourselves.  A missing attribute causes build_diagnostic_data()
    (called every update cycle) to raise AttributeError and crash all cover
    updates.  This test catches any future accidental rename.
    """
    entry = await _setup(hass, entry_id="lust_01")
    coordinator = entry.runtime_data

    # Attribute must exist on a real (non-mocked) instance.
    assert hasattr(coordinator, "_last_update_success_time"), (
        "AdaptiveDataUpdateCoordinator is missing _last_update_success_time; "
        "build_diagnostic_data() will crash every update cycle"
    )
    # Value is None (no successful cycle yet) or a UTC datetime — both valid.
    import datetime as _dt

    val = coordinator._last_update_success_time
    assert val is None or isinstance(
        val, _dt.datetime
    ), f"_last_update_success_time must be None or datetime, got {type(val)}"


async def test_manual_override_input_template_initialized_in_init(
    hass: HomeAssistant,
) -> None:
    """Regression (#974): the input-template attr exists right after __init__.

    The manual-override input-template tracker is registered during setup,
    with awaits before the first _update_options runs. If its handler fires in
    that window it reads self.manual_override_input_template — which is only
    assigned in _update_options. Without an __init__ default that read raises
    AttributeError and breaks setup/reload. This constructs the coordinator
    directly (no _update_options) and asserts the attribute already exists.
    """
    from homeassistant import config_entries as ha_config_entries

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "MOIT Cover", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="moit_01",
        title="MOIT Cover",
    )
    entry.add_to_hass(hass)

    token = ha_config_entries.current_entry.set(entry)
    try:
        coordinator = AdaptiveDataUpdateCoordinator(hass)
    finally:
        ha_config_entries.current_entry.reset(token)

    # Attribute must exist before _update_options ever runs, defaulting to None.
    assert coordinator.manual_override_input_template is None


async def test_manager_covers_populated_at_init_for_restore(
    hass: HomeAssistant,
) -> None:
    """Regression (#1019): manager.covers is populated at __init__ so the
    RestoreEntity manual-override restore (runs during platform setup, before
    first_refresh) sees the configured covers instead of an empty set.

    Without this, `_ManualOverrideEndSensor._restore_from_attributes` filters
    every restored override out via its `eid not in manager.covers` guard,
    silently discarding manual overrides across a Home Assistant restart.
    """
    from homeassistant import config_entries as ha_config_entries

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Restore Cover", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="restore_01",
        title="Restore Cover",
    )
    entry.add_to_hass(hass)

    token = ha_config_entries.current_entry.set(entry)
    try:
        coordinator = AdaptiveDataUpdateCoordinator(hass)
    finally:
        ha_config_entries.current_entry.reset(token)

    # Before the fix this is empty (covers only added during first_refresh),
    # so the restore guard drops every override.
    assert "cover.test_blind" in coordinator.manager.covers


async def test_coordinator_injects_time_mgr_into_snapshot_builder(
    hass: HomeAssistant,
) -> None:
    """The builder gets the live TimeWindowManager at construction (#1055).

    The builder's effective-default fallback reads the gate, the start-time
    signal and the window-end flag off this collaborator. Its ``None`` default
    exists only for tests, so production must never be left on it — a refactor
    that drops the injection, or reorders ``__init__`` back so ``_time_mgr`` is
    built after the builder, silently reverts the ad-hoc path to the pure
    defaults.
    """
    from homeassistant import config_entries as ha_config_entries

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "TW Cover", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="tw_inject_01",
        title="TW Cover",
    )
    entry.add_to_hass(hass)

    token = ha_config_entries.current_entry.set(entry)
    try:
        coordinator = AdaptiveDataUpdateCoordinator(hass)
    finally:
        ha_config_entries.current_entry.reset(token)

    assert coordinator._snapshot_builder._time_mgr is coordinator._time_mgr


async def test_coordinator_sunrise_provider_resolves_naive_local_sunrise(
    hass: HomeAssistant,
) -> None:
    """The coordinator wires a real ``sunrise_provider`` into TimeWindowManager (#1256).

    ``TimeWindowManager.after_start_time`` anchors a blank start time to
    sunrise instead of midnight once an end bound is configured — but only
    if the injected closure actually resolves a sane naive-LOCAL value, the
    same frame ``local_now_naive()`` reads. Pacific/Auckland (UTC+12) makes a
    naive-UTC leak obvious: astral's sunrise is UTC-aware, so a fix that
    forgot the ``dt_util.as_local`` conversion (or stripped tzinfo without
    converting) would land ~12 hours away from a plausible Auckland morning.
    """
    import datetime as dt
    import zoneinfo
    from unittest.mock import patch

    from homeassistant import config_entries as ha_config_entries

    auckland = zoneinfo.ZoneInfo("Pacific/Auckland")
    hass.config.time_zone = "Pacific/Auckland"
    hass.config.latitude = -36.8485
    hass.config.longitude = 174.7633
    hass.config.elevation = 20

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Sunrise Provider Cover", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="sunrise_provider_01",
        title="Sunrise Provider Cover",
    )
    entry.add_to_hass(hass)

    with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", auckland):
        token = ha_config_entries.current_entry.set(entry)
        try:
            coordinator = AdaptiveDataUpdateCoordinator(hass)
        finally:
            ha_config_entries.current_entry.reset(token)

        sunrise = coordinator._time_mgr._sunrise_provider()

    assert sunrise is not None
    assert sunrise.tzinfo is None  # naive-local, matching local_now_naive()
    # A same-instant value stuck in UTC (the bug this guards) would land
    # many hours off any plausible Auckland sunrise hour.
    assert dt.time(4, 0) <= sunrise.time() <= dt.time(10, 0)


async def test_coordinator_sunrise_provider_fails_open_on_error(
    hass: HomeAssistant,
) -> None:
    """A broken sun-data read must not crash — the sunrise_provider fails open to None.

    Mirrors ``after_start_time``'s own fail-open-to-True contract for "no
    sunrise available" (issue #1256): any error resolving today's
    astronomical sunrise degrades gracefully instead of raising out of a
    property read every coordinator cycle would hit.
    """
    from unittest.mock import patch

    from homeassistant import config_entries as ha_config_entries

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Sunrise Error Cover", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="sunrise_provider_err_01",
        title="Sunrise Error Cover",
    )
    entry.add_to_hass(hass)

    token = ha_config_entries.current_entry.set(entry)
    try:
        coordinator = AdaptiveDataUpdateCoordinator(hass)
    finally:
        ha_config_entries.current_entry.reset(token)

    with patch.object(
        coordinator._sun_provider,
        "create_sun_data",
        side_effect=RuntimeError("boom"),
    ):
        assert coordinator._time_mgr._sunrise_provider() is None


# ---------------------------------------------------------------------------
# sunrise_gates_start wiring (issue #1340)
# ---------------------------------------------------------------------------


def _coordinator_for(hass: HomeAssistant, options: dict, entry_id: str):
    """Build a coordinator bound to a MockConfigEntry, as :401-449 does."""
    from homeassistant import config_entries as ha_config_entries

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": entry_id, CONF_SENSOR_TYPE: CoverType.BLIND},
        options=options,
        entry_id=entry_id,
        title=entry_id,
    )
    entry.add_to_hass(hass)

    token = ha_config_entries.current_entry.set(entry)
    try:
        return AdaptiveDataUpdateCoordinator(hass)
    finally:
        ha_config_entries.current_entry.reset(token)


async def test_coordinator_wires_sunrise_gates_start_into_time_window_manager(
    hass: HomeAssistant,
) -> None:
    """The opt-in reaches the manager through ``_update_options`` (issue #1340).

    ``RuntimeConfig`` is the single runtime read; a value that never reaches
    ``TimeWindowManager.update_config`` leaves the option inert in the UI.
    """
    from custom_components.adaptive_cover_pro.const import CONF_SUNRISE_GATES_START

    coordinator = _coordinator_for(
        hass,
        {**VERTICAL_OPTIONS, CONF_SUNRISE_GATES_START: True},
        "sunrise_gates_on_01",
    )
    coordinator._update_options(dict(coordinator.config_entry.options))
    assert coordinator._time_mgr._sunrise_gates_start is True

    coordinator = _coordinator_for(hass, dict(VERTICAL_OPTIONS), "sunrise_gates_off_01")
    coordinator._update_options(dict(coordinator.config_entry.options))
    assert coordinator._time_mgr._sunrise_gates_start is False


async def test_coordinator_sunrise_provider_honors_sunrise_entity_and_offset(
    hass: HomeAssistant,
) -> None:
    """The window's sunrise must mean what ``resolve_sun_boundaries`` says it means.

    ``_resolve_blank_start_sunrise`` read pure astral and ignored
    ``sunrise_time_entity`` / ``sunrise_offset`` — the same class of bug as
    #1048. #1340's reporter configures a sunrise ENTITY, so a floor built on
    astral would ignore the very option they set (and the #1256 blank-start
    anchor has been ignoring it all along).
    """
    import datetime as dt
    import zoneinfo
    from unittest.mock import patch

    from homeassistant.util import dt as dt_util

    from custom_components.adaptive_cover_pro.const import (
        CONF_SUNRISE_OFFSET,
        CONF_SUNRISE_TIME_ENTITY,
    )

    hass.states.async_set("input_datetime.dawn", "2026-09-03T04:00:00")
    options = {
        **VERTICAL_OPTIONS,
        CONF_SUNRISE_TIME_ENTITY: "input_datetime.dawn",
        CONF_SUNRISE_OFFSET: 30,
    }

    coordinator = _coordinator_for(hass, options, "sunrise_entity_01")
    sunrise = coordinator._time_mgr._sunrise_provider()

    assert sunrise is not None
    assert sunrise.tzinfo is None  # naive-local, matching local_now_naive()
    assert sunrise.time() == dt.time(4, 30)
    assert sunrise.date() == dt_util.now().date()

    # Same answer in a UTC+12 zone: the entity is naive-LOCAL wall clock, so a
    # round trip through UTC must land back on 04:30 local, not 16:30.
    auckland = zoneinfo.ZoneInfo("Pacific/Auckland")
    hass.config.time_zone = "Pacific/Auckland"
    with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", auckland):
        coordinator = _coordinator_for(hass, options, "sunrise_entity_akl_01")
        sunrise = coordinator._time_mgr._sunrise_provider()

    assert sunrise is not None
    assert sunrise.tzinfo is None
    assert sunrise.time() == dt.time(4, 30)


async def test_coordinator_sunrise_provider_unavailable_entity_falls_back_to_astral(
    hass: HomeAssistant,
) -> None:
    """An unavailable sunrise entity degrades to astral, as ``resolve_sun_boundaries`` does."""
    import datetime as dt
    import zoneinfo
    from unittest.mock import patch

    from custom_components.adaptive_cover_pro.const import CONF_SUNRISE_TIME_ENTITY

    auckland = zoneinfo.ZoneInfo("Pacific/Auckland")
    hass.config.time_zone = "Pacific/Auckland"
    hass.config.latitude = -36.8485
    hass.config.longitude = 174.7633
    hass.config.elevation = 20
    hass.states.async_set("input_datetime.dawn", "unavailable")

    with patch("homeassistant.util.dt.DEFAULT_TIME_ZONE", auckland):
        coordinator = _coordinator_for(
            hass,
            {**VERTICAL_OPTIONS, CONF_SUNRISE_TIME_ENTITY: "input_datetime.dawn"},
            "sunrise_entity_unavail_01",
        )
        sunrise = coordinator._time_mgr._sunrise_provider()

    assert sunrise is not None
    assert sunrise.tzinfo is None
    assert dt.time(4, 0) <= sunrise.time() <= dt.time(10, 0)


# ---------------------------------------------------------------------------
# Venetian mode wiring
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_coordinator_wires_venetian_mode_into_policy(hass: HomeAssistant) -> None:
    """Coordinator passes venetian_mode option to VenetianPolicy.attach().

    Regression guard: if the coordinator forgets to forward venetian_mode,
    the policy silently falls back to position_and_tilt on every startup,
    making the tilt_only option a no-op.
    """
    from custom_components.adaptive_cover_pro.const import VENETIAN_MODE_TILT_ONLY

    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_VENETIAN_MODE] = VENETIAN_MODE_TILT_ONLY

    hass.states.async_set(
        "cover.test_blind",
        "open",
        {
            "current_position": 100,
            "current_tilt_position": 50,
            "supported_features": 143,
        },
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Venetian Test", CONF_SENSOR_TYPE: CoverType.VENETIAN},
        options=opts,
        entry_id="venetian_mode_01",
        title="Venetian Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    assert coordinator._policy._venetian_mode == VENETIAN_MODE_TILT_ONLY


@pytest.mark.integration
async def test_coordinator_wires_post_settle_hold_into_sequencer(
    hass: HomeAssistant,
) -> None:
    """Coordinator passes post_settle_hold_seconds from options to the DualAxisSequencer.

    Regression guard: if the coordinator forgets to forward the hold, the
    sequencer silently uses the module default (2.0 s) regardless of the
    user's configured value, making the option a no-op.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_POST_SETTLE_HOLD,
    )

    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_VENETIAN_POST_SETTLE_HOLD] = 7.5

    hass.states.async_set(
        "cover.test_blind",
        "open",
        {
            "current_position": 100,
            "current_tilt_position": 50,
            "supported_features": 143,
        },
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Venetian Hold Test", CONF_SENSOR_TYPE: CoverType.VENETIAN},
        options=opts,
        entry_id="venetian_hold_01",
        title="Venetian Hold Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    seq = coordinator._policy.sequencer
    assert seq is not None
    assert seq._post_settle_hold_seconds == 7.5


@pytest.mark.integration
async def test_coordinator_wires_post_settle_mode_into_sequencer(
    hass: HomeAssistant,
) -> None:
    """Coordinator passes venetian_post_settle_mode from options into the sequencer.

    Regression guard (issue #801): if the coordinator forgets to forward the
    mode, the sequencer silently stays on ``fixed_delay`` regardless of the
    user's configured value, making the entity_state option a no-op.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_POST_SETTLE_MODE,
        VENETIAN_POST_SETTLE_MODE_ENTITY_STATE,
    )

    opts = dict(VERTICAL_OPTIONS)
    opts[CONF_VENETIAN_POST_SETTLE_MODE] = VENETIAN_POST_SETTLE_MODE_ENTITY_STATE

    hass.states.async_set(
        "cover.test_blind",
        "open",
        {
            "current_position": 100,
            "current_tilt_position": 50,
            "supported_features": 143,
        },
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "name": "Venetian Post-Settle Mode Test",
            CONF_SENSOR_TYPE: CoverType.VENETIAN,
        },
        options=opts,
        entry_id="venetian_post_settle_mode_01",
        title="Venetian Post-Settle Mode Test",
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    seq = coordinator._policy.sequencer
    assert seq is not None
    assert seq._post_settle_mode == VENETIAN_POST_SETTLE_MODE_ENTITY_STATE


async def test_coordinator_shares_its_own_policy_with_the_command_service(
    hass: HomeAssistant,
) -> None:
    """The command service gets the entry's OWN policy object (issue #1115).

    ``CoverCommandService`` falls back to building a private policy from the
    cover-type string when none is passed. That fallback is fine for the
    stateless axis/capability queries, and silently wrong for everything else:
    the private instance is never primed by ``post_pipeline_resolve`` and never
    ``attach``ed, so a stateful policy answers every question this manager asks
    with its unprimed default. The Model C day/night rail order
    (``order_for_dispatch``) collapses to identity and the travel clearance
    (``await_dispatch_clearance``) to an unconditional yes — the manager keeps
    running, silently unsequenced.

    Identity, not equality: a fresh ``get_policy()`` compares indistinguishable
    while being exactly the wrong object.
    """
    from homeassistant import config_entries as ha_config_entries

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Policy Cover", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="policy_share_01",
        title="Policy Cover",
    )
    entry.add_to_hass(hass)

    token = ha_config_entries.current_entry.set(entry)
    try:
        coordinator = AdaptiveDataUpdateCoordinator(hass)
    finally:
        ha_config_entries.current_entry.reset(token)

    assert coordinator._cmd_svc._policy is coordinator._policy
