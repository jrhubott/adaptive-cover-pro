"""Issue #1063: the coordinator is the source of truth for non-option switches.

``AdaptiveCoverSwitch.is_on`` used to return a local ``_attr_is_on`` that only
the entity's own ``async_turn_on``/``async_turn_off`` ever wrote. Any caller
that set the coordinator attribute directly (the group's bulk controls, the
``integration_enable``/``integration_disable``/``emergency_stop`` services)
changed behavior while the entity kept reporting its old state — and because
the switch is a ``RestoreEntity``, that stale entity value was pushed back into
the coordinator at the next restart, silently undoing the change.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.helpers.restore_state import RestoreEntity
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_DEFAULT_HEIGHT,
    CONF_ENABLE_SUN_TRACKING,
    CONF_ENTITIES,
    CONF_MEMBER_COVERS,
    CONF_MEMBER_ENTRIES,
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
)
from custom_components.adaptive_cover_pro.group_coordinator import GroupCoordinator
from custom_components.adaptive_cover_pro.group_entities import (
    GroupAutomationSwitch,
    GroupClimateSwitch,
    GroupLockSwitch,
)
from custom_components.adaptive_cover_pro.services import async_setup_services
from custom_components.adaptive_cover_pro.switch import (
    _SWITCH_SPECS,
    AdaptiveCoverSwitch,
)

# Every spec whose truth lives on the coordinator rather than in
# ``config_entry.options`` — the switches exposed to the defect.
NON_OPTION_SPECS = [spec for spec in _SWITCH_SPECS if spec.option_key is None]


class _FakeCoordinator:
    """Coordinator stand-in with real attribute semantics.

    A ``MagicMock`` auto-vivifies any attribute into a truthy child mock, which
    would mask both the "toggle is unset" and the "glare-zone attribute does
    not exist yet" cases this module has to pin. A plain object raises
    ``AttributeError`` for a missing attribute, exactly like the real
    coordinator.
    """

    def __init__(self, **toggles: object) -> None:
        self.logger = MagicMock()
        self.hass = MagicMock()
        self.async_add_listener = MagicMock(return_value=lambda: None)
        self.async_refresh = AsyncMock()
        self.async_update_listeners = MagicMock()
        for key, value in toggles.items():
            setattr(self, key, value)


def _make_config_entry(options: dict | None = None):
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"name": "Test", CONF_SENSOR_TYPE: CoverType.BLIND}
    entry.options = options if options is not None else {CONF_DEFAULT_HEIGHT: 60}
    return entry


def _make_switch(
    coordinator: _FakeCoordinator,
    key: str,
    *,
    initial_state: bool = True,
    option_key: str | None = None,
    config_entry=None,
) -> AdaptiveCoverSwitch:
    entry = config_entry or _make_config_entry()
    switch = AdaptiveCoverSwitch(
        entry_id="test_entry",
        hass=coordinator.hass,
        config_entry=entry,
        coordinator=coordinator,
        switch_name=key,
        initial_state=initial_state,
        key=key,
        option_key=option_key,
    )
    switch.schedule_update_ha_state = MagicMock()
    return switch


# ---------------------------------------------------------------------------
# is_on derives from the coordinator
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("spec", NON_OPTION_SPECS, ids=lambda s: s.key)
@pytest.mark.parametrize("value", [True, False])
def test_non_option_switch_reflects_direct_coordinator_set(spec, value):
    """A toggle set straight on the coordinator is what the switch reports."""
    coord = _FakeCoordinator(**{spec.key: value})
    switch = _make_switch(coord, spec.key, initial_state=spec.initial_state)

    assert switch.is_on is value


@pytest.mark.unit
@pytest.mark.parametrize("value", [True, False])
def test_glare_zone_switch_reflects_direct_coordinator_set(value):
    """Dynamic ``glare_zone_N`` attributes follow the same rule."""
    coord = _FakeCoordinator(glare_zone_0=value)
    switch = _make_switch(coord, "glare_zone_0")

    assert switch.is_on is value


@pytest.mark.unit
def test_glare_zone_switch_falls_back_when_attribute_absent():
    """Glare-zone attributes do not exist until a switch first writes them.

    ``coordinator._is_glare_zone_enabled`` reads them with a ``getattr``
    default for the same reason; the switch must not raise on a render that
    lands before ``async_added_to_hass``.
    """
    coord = _FakeCoordinator()
    switch = _make_switch(coord, "glare_zone_0", initial_state=True)

    assert switch.is_on is True


@pytest.mark.unit
@pytest.mark.parametrize("initial_state", [True, False])
def test_unset_toggle_falls_back_to_initial_state(initial_state):
    """``ToggleManager`` seeds most toggles to ``None`` until a switch restores.

    This pins the fallback, not a claim about runtime semantics: for
    ``automatic_control`` the coordinator reads ``None`` as falsy while the
    spec declares ``initial_state=True``, so during the sliver before
    ``async_added_to_hass`` the switch renders ``on`` over a closed gate. See
    the ``is_on`` docstring for why one shared default is preferred over a
    per-toggle ``None``-semantics map.
    """
    coord = _FakeCoordinator(automatic_control=None)
    switch = _make_switch(coord, "automatic_control", initial_state=initial_state)

    assert switch.is_on is initial_state


@pytest.mark.unit
@pytest.mark.parametrize("enabled", [True, False])
def test_option_backed_switch_still_reads_options(enabled):
    """Sun Tracking keeps reading ``config_entry.options``, not the coordinator."""
    coord = _FakeCoordinator(sun_tracking=not enabled)
    switch = _make_switch(
        coord,
        "sun_tracking",
        option_key=CONF_ENABLE_SUN_TRACKING,
        config_entry=_make_config_entry({CONF_ENABLE_SUN_TRACKING: enabled}),
    )

    assert switch.is_on is enabled


# ---------------------------------------------------------------------------
# The restart round-trip — the damaging half of the defect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_restart_restores_the_coordinator_set_value_not_the_stale_one():
    """A directly-set toggle survives a restart instead of being undone by it.

    The entity's rendered state is what the recorder persists and what
    ``RestoreEntity`` hands back, so deriving ``is_on`` from the coordinator is
    what makes the restore write the *correct* value back at startup.
    """
    coord = _FakeCoordinator(automatic_control=True)
    switch = _make_switch(coord, "automatic_control")
    assert switch.is_on is True

    # A caller (group bulk control, service) flips the coordinator directly.
    coord.automatic_control = False
    assert switch.is_on is False

    # Restart: fresh coordinator, entity restores whatever it last rendered.
    restored = STATE_ON if switch.is_on else STATE_OFF
    new_coord = _FakeCoordinator(automatic_control=None)
    new_switch = _make_switch(new_coord, "automatic_control")
    new_switch.async_get_last_state = AsyncMock(return_value=MagicMock(state=restored))

    await new_switch.async_added_to_hass()

    assert new_coord.automatic_control is False
    assert new_switch.is_on is False


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    ("restored", "expected"), [(STATE_ON, True), (STATE_OFF, False)]
)
async def test_restore_still_wins_at_add_time(restored, expected):
    """Restored state seeds the coordinator when the entity is added.

    The reconciliation direction stays as-is on purpose: several toggles carry
    a static non-``None`` default (``switch_mode`` from ``CONF_CLIMATE_MODE``,
    ``motion_control``), so "coordinator wins at add time" would make those two
    ignore the user's last toggle after every restart.
    """
    coord = _FakeCoordinator(automatic_control=None)
    switch = _make_switch(coord, "automatic_control")
    switch.async_get_last_state = AsyncMock(return_value=MagicMock(state=restored))

    await switch.async_added_to_hass()

    assert coord.automatic_control is expected
    assert switch.is_on is expected


# ---------------------------------------------------------------------------
# The callers that set toggles directly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "key", "enabled"),
    [
        ("async_set_automation", "automatic_control", False),
        ("async_set_automation", "automatic_control", True),
        ("async_set_climate_mode", "switch_mode", False),
        ("async_set_climate_mode", "switch_mode", True),
    ],
)
async def test_group_bulk_control_updates_the_member_switch(method, key, enabled):
    """The group's bulk controls no longer leave the member switch behind."""
    member = _FakeCoordinator(**{key: not enabled})
    switch = _make_switch(member, key)

    group = object.__new__(GroupCoordinator)
    group.resolved_members = MagicMock(return_value=[(MagicMock(), member)])

    await getattr(GroupCoordinator, method)(group, enabled)

    assert getattr(member, key) is enabled
    assert switch.is_on is enabled


def _make_service_coordinator() -> MagicMock:
    coord = MagicMock()
    coord.entities = ["cover.a"]
    coord.enabled_toggle = True
    coord.logger = MagicMock()
    coord._cmd_svc = MagicMock()
    coord._cmd_svc.stop_in_flight = AsyncMock(return_value=[])
    coord._cmd_svc.stop_all = AsyncMock(return_value=[])
    coord._cancel_motion_timeout = MagicMock()
    coord._cancel_weather_timeout = MagicMock()
    return coord


def _make_service_hass(coord: MagicMock) -> MagicMock:
    from homeassistant.config_entries import ConfigEntryState

    entry = MagicMock()
    entry.entry_id = "entry_a"
    entry.runtime_data = coord
    entry.state = ConfigEntryState.LOADED

    hass = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[entry])
    hass.services = MagicMock()
    hass.services.has_service = MagicMock(return_value=False)
    hass.services.async_register = MagicMock()
    return hass


def _get_handler(hass: MagicMock, service_name: str):
    for call in hass.services.async_register.call_args_list:
        if call[0][1] == service_name:
            return call[0][2]
    raise ValueError(f"Service {service_name!r} was never registered")


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    ("service", "expected"),
    [
        ("integration_enable", True),
        ("integration_disable", False),
        ("emergency_stop", False),
    ],
)
async def test_kill_switch_services_push_the_new_state_to_the_entity(service, expected):
    """These services never refresh, so they must notify listeners themselves.

    The coordinator sets no ``update_interval``; refreshes are state-change
    driven. Without an explicit notification the switch would keep rendering
    its old value until some unrelated cycle happened to run — and that stale
    render is what a restart would restore.
    """
    coord = _make_service_coordinator()
    coord.enabled_toggle = not expected
    hass = _make_service_hass(coord)

    await async_setup_services(hass)
    call = MagicMock()
    call.data = {}
    await _get_handler(hass, service)(call)

    assert coord.enabled_toggle is expected
    coord.async_update_listeners.assert_called_once()


# ---------------------------------------------------------------------------
# The render gate — the link that carries a coordinator flip to the state machine
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_coordinator_flip_reaches_the_state_machine():
    """``is_on`` only helps if the render-signature gate lets the write through.

    ``entity_base._handle_coordinator_update`` suppresses a write whenever
    ``_acp_render_signature()`` is unchanged, and that signature is built from
    ``self.state`` — which resolves through the new ``is_on``. Without this the
    entity would hold its old state despite reading the right value, and that
    old state is what ``RestoreEntity`` hands back.
    """
    coord = _FakeCoordinator(automatic_control=True)
    coord.data = MagicMock()
    coord.last_update_success = True
    switch = _make_switch(coord, "automatic_control")
    switch.async_write_ha_state = MagicMock()

    switch._handle_coordinator_update()
    assert switch.async_write_ha_state.call_count == 1

    # An unchanged cycle is still gated — the fix must not defeat the dedup.
    switch._handle_coordinator_update()
    assert switch.async_write_ha_state.call_count == 1

    coord.automatic_control = False
    switch._handle_coordinator_update()
    assert switch.async_write_ha_state.call_count == 2


# ---------------------------------------------------------------------------
# The group side: members now persist, so the group's own switches must too
# ---------------------------------------------------------------------------


def _make_group_config_entry():
    entry = MagicMock()
    entry.entry_id = "group_01"
    entry.data = {"name": "Living Room", CONF_SENSOR_TYPE: CoverType.GROUP}
    entry.options = {}
    return entry


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    ("cls", "default"),
    [(GroupAutomationSwitch, True), (GroupClimateSwitch, False)],
)
@pytest.mark.parametrize(
    ("restored", "expected"), [(STATE_ON, True), (STATE_OFF, False)]
)
async def test_group_bulk_switch_restores_its_last_command(
    cls, default, restored, expected
):
    """A group bulk switch must not snap back to its default after a restart.

    Its members now persist what the bulk command did to them (that is the
    #1063 fix), so a group switch that reinitializes to its default would
    permanently contradict them: the user turns group automation off, restarts,
    and sees the group reading ``on`` while every member reads ``off``.
    """
    coord = _FakeCoordinator()
    switch = cls("group_01", coord.hass, _make_group_config_entry(), coord)
    switch.async_write_ha_state = MagicMock()
    assert switch.is_on is default

    switch.async_get_last_state = AsyncMock(return_value=MagicMock(state=restored))
    await switch.async_added_to_hass()

    assert switch.is_on is expected


@pytest.mark.asyncio
@pytest.mark.unit
async def test_group_lock_switch_does_not_restore():
    """The lock is deliberately the exception — its effect does not survive.

    ``async_set_lock`` writes ``group_locked`` and a per-member ``GroupIntent``,
    both of which are runtime-only. Restoring the switch to ``on`` would claim
    a lock that no member is actually holding.
    """
    coord = _FakeCoordinator()
    switch = GroupLockSwitch("group_01", coord.hass, _make_group_config_entry(), coord)
    switch.async_write_ha_state = MagicMock()

    assert not isinstance(switch, RestoreEntity)
    assert switch.is_on is False


# ---------------------------------------------------------------------------
# The group's member resolution must not hand back a half-built coordinator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolved_members_skips_an_entry_that_has_not_finished_setup(hass):
    """``runtime_data`` is assigned before platform setup, so LOADED is the gate.

    A member mid-reload already exposes its new coordinator while its switch
    entities are still being added. Writing a toggle onto it is undone moments
    later when those switches restore their previous state — the #1063 failure
    mode all over again, on the one direct writer that does not filter on
    ``ConfigEntryState``.
    """
    ready = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "ready", CONF_SENSOR_TYPE: CoverType.BLIND},
        options={CONF_ENTITIES: ["cover.ready"]},
        entry_id="member_ready",
        state=ConfigEntryState.LOADED,
    )
    ready.add_to_hass(hass)
    ready.runtime_data = _FakeCoordinator(automatic_control=True)

    setting_up = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "setting_up", CONF_SENSOR_TYPE: CoverType.BLIND},
        options={CONF_ENTITIES: ["cover.setting_up"]},
        entry_id="member_setting_up",
        state=ConfigEntryState.SETUP_IN_PROGRESS,
    )
    setting_up.add_to_hass(hass)
    setting_up.runtime_data = _FakeCoordinator(automatic_control=True)

    group_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "G", CONF_SENSOR_TYPE: CoverType.GROUP},
        options={
            CONF_MEMBER_ENTRIES: ["member_ready", "member_setting_up"],
            CONF_MEMBER_COVERS: [],
        },
        entry_id="group_01",
    )
    group_entry.add_to_hass(hass)

    group = GroupCoordinator(hass, group_entry)

    assert [entry.entry_id for entry, _ in group.resolved_members()] == ["member_ready"]

    await group.async_set_automation(False)
    assert ready.runtime_data.automatic_control is False
    assert setting_up.runtime_data.automatic_control is True
