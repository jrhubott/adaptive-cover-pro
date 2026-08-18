"""Tests for manual override persistence via RestoreEntity (Issue #192).

Manual override state must survive HA reboot/reload. The
AdaptiveCoverManualOverrideEndSensor inherits RestoreEntity and
rehydrates the coordinator's AdaptiveCoverManager from persisted
per_entity expiry attributes.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pytest
from homeassistant import config_entries as ha_config_entries
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.adaptive_cover_pro.const import (
    CONF_SENSOR_TYPE,
    DOMAIN,
    CoverType,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from tests.ha_helpers import VERTICAL_OPTIONS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager(reset_minutes: int = 60, covers: set | None = None):
    """Return a real AdaptiveCoverManager with a mock hass."""
    from custom_components.adaptive_cover_pro.managers.manual_override import (
        AdaptiveCoverManager,
    )

    hass = MagicMock()
    manager = AdaptiveCoverManager(
        hass=hass,
        reset_duration={"minutes": reset_minutes},
        logger=MagicMock(),
    )
    if covers:
        manager.covers.update(covers)
    return manager


def _make_sensor(manager):
    """Return an AdaptiveCoverManualOverrideEndSensor wired to *manager*.

    Avoids the full CoordinatorEntity / HA machinery by using a minimal mock
    coordinator.  Only coordinator.manager is used by the sensor's restore logic.
    """
    from custom_components.adaptive_cover_pro.sensor import (
        AdaptiveCoverManualOverrideEndSensor,
    )

    coordinator = MagicMock()
    coordinator.manager = manager

    config_entry = MagicMock()
    config_entry.data = {"name": "Test Cover", "sensor_type": "cover_blind"}
    config_entry.options = {}

    hass = MagicMock()

    # Bypass CoordinatorEntity.__init__ device-info lookups by calling __new__
    # and injecting the attributes we need directly.
    sensor = object.__new__(AdaptiveCoverManualOverrideEndSensor)
    sensor.coordinator = coordinator
    sensor.hass = hass
    sensor.config_entry = config_entry
    sensor._entry_id = "test_entry"
    sensor._sensor_name = "Manual Override End Time"
    sensor._write_ha_state_called = False

    def _fake_write_ha_state():
        sensor._write_ha_state_called = True

    sensor.async_write_ha_state = _fake_write_ha_state
    return sensor


# ---------------------------------------------------------------------------
# Test 1: Round-trip — per_entity attributes are restored into manager state
# ---------------------------------------------------------------------------


def test_restore_from_attributes_populates_manager_state():
    """Valid future expiry is restored: manual_control and manual_control_time set."""
    eid = "cover.living_room"
    reset_minutes = 60
    manager = _make_manager(reset_minutes=reset_minutes, covers={eid})
    sensor = _make_sensor(manager)

    # Expiry 30 minutes in the future
    expiry = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30)
    per_entity = {eid: expiry.isoformat()}

    sensor._restore_from_attributes(per_entity)

    assert manager.manual_control.get(eid) is True
    assert eid in manager.manual_control_time
    expected_started_at = expiry - dt.timedelta(minutes=reset_minutes)
    actual_started_at = manager.manual_control_time[eid]
    # Allow 1-second tolerance for floating-point / clock skew
    assert abs((actual_started_at - expected_started_at).total_seconds()) < 1


def test_restore_from_attributes_returns_true_on_successful_restore():
    """A successful restore returns True so the base writes HA state once.

    The write side-effect moved to _ACPRestorableDiagnosticSensor.async_added_to_hass,
    which calls async_write_ha_state() iff _restore_from_attributes returns True.
    """
    eid = "cover.living_room"
    manager = _make_manager(covers={eid})
    sensor = _make_sensor(manager)

    expiry = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30)
    result = sensor._restore_from_attributes({eid: expiry.isoformat()})

    assert result is True


# ---------------------------------------------------------------------------
# Test 2: Expired entries are dropped — no restore
# ---------------------------------------------------------------------------


def test_restore_from_attributes_drops_expired_entries():
    """Expiry already in the past: entity is NOT marked as manual."""
    eid = "cover.bedroom"
    manager = _make_manager(covers={eid})
    sensor = _make_sensor(manager)

    # Expiry 2 minutes in the past
    expiry = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=2)
    sensor._restore_from_attributes({eid: expiry.isoformat()})

    assert manager.manual_control.get(eid, False) is False
    assert eid not in manager.manual_control_time


def test_restore_from_attributes_returns_false_when_nothing_restored():
    """No valid restores → returns False so the base does not write HA state."""
    eid = "cover.bedroom"
    manager = _make_manager(covers={eid})
    sensor = _make_sensor(manager)

    expiry = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    result = sensor._restore_from_attributes({eid: expiry.isoformat()})

    assert result is False


# ---------------------------------------------------------------------------
# Test 3: Entity not in manager.covers is filtered out
# ---------------------------------------------------------------------------


def test_restore_from_attributes_filters_unknown_entities():
    """Entity in stored state but not in manager.covers is silently skipped."""
    known = "cover.known"
    unknown = "cover.removed_from_config"
    manager = _make_manager(covers={known})
    sensor = _make_sensor(manager)

    expiry = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30)
    sensor._restore_from_attributes(
        {
            known: expiry.isoformat(),
            unknown: expiry.isoformat(),
        }
    )

    # Known cover restored
    assert manager.manual_control.get(known) is True
    # Removed cover not restored
    assert manager.manual_control.get(unknown, False) is False
    assert unknown not in manager.manual_control_time


# ---------------------------------------------------------------------------
# Test 3b: A corrupted per_entity VALUE cannot break sensor setup (issue #1273)
# ---------------------------------------------------------------------------
#
# The shared base seam guards the per_entity MAPPING shape, so neither
# restorable subclass iterates a non-mapping. Nothing guarded the values, and
# the restore payload is whatever a prior build (possibly an older one, after a
# roll-back) happened to write. #1232 was a reboot-path wipe in this same code.


@pytest.mark.parametrize(
    "bad_value",
    [
        pytest.param("not-a-timestamp", id="unparseable-string"),
        pytest.param("", id="empty-string"),
        pytest.param(None, id="none"),
        pytest.param(12345, id="not-a-string"),
        pytest.param(["2026-01-01T00:00:00+00:00"], id="list"),
        # Parses cleanly, then explodes on the aware/naive comparison — the
        # shape a rolled-back build could have persisted.
        pytest.param("2099-01-01T00:00:00", id="naive-timestamp"),
    ],
)
def test_restore_skips_a_corrupt_entry_without_raising(bad_value):
    """A bad entry is dropped; it must not propagate out of the restore."""
    eid = "cover.living_room"
    manager = _make_manager(covers={eid})
    sensor = _make_sensor(manager)

    restored = sensor._restore_from_attributes({eid: bad_value})

    assert restored is False
    assert manager.manual_control.get(eid, False) is False
    assert eid not in manager.manual_control_time


def test_restore_keeps_good_entries_alongside_a_corrupt_one():
    """One bad cover must not cost the others their override (issue #1273)."""
    good = "cover.good"
    bad = "cover.bad"
    manager = _make_manager(covers={good, bad})
    sensor = _make_sensor(manager)

    expiry = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30)
    restored = sensor._restore_from_attributes(
        {bad: "not-a-timestamp", good: expiry.isoformat()}
    )

    assert restored is True
    assert manager.manual_control.get(good) is True
    assert manager.manual_control.get(bad, False) is False


# ---------------------------------------------------------------------------
# Test 3c: restore_override records its own event (issue #1273)
# ---------------------------------------------------------------------------


def test_restore_override_records_its_own_event():
    """The manager owns its diagnostics, not the sensor calling _record_event."""
    eid = "cover.living_room"
    manager = _make_manager(covers={eid})

    expiry = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30)
    manager.restore_override(eid, expiry)

    events = [e for e in manager.get_event_buffer() if e.get("event") == "restored"]
    assert len(events) == 1
    assert events[0]["entity_id"] == eid


def test_restore_path_does_not_double_record():  # noqa: D103
    """The sensor must not add a second ``restored`` event on top of the manager's."""
    eid = "cover.living_room"
    manager = _make_manager(covers={eid})
    sensor = _make_sensor(manager)

    expiry = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30)
    sensor._restore_from_attributes({eid: expiry.isoformat()})

    events = [e for e in manager.get_event_buffer() if e.get("event") == "restored"]
    assert len(events) == 1


# ---------------------------------------------------------------------------
# Test 4: Startup skip — async_handle_first_refresh skips manual covers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_refresh_skips_apply_position_for_manual_cover():
    """apply_position is NOT called for a cover under manual override."""

    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    eid_manual = "cover.manual"
    eid_auto = "cover.auto"

    coordinator = MagicMock()
    coordinator.entities = [eid_manual, eid_auto]
    # Real policy: the startup loop asks it for the entity order (#1115).
    coordinator._policy = get_policy("cover_blind")
    coordinator.check_adaptive_time = True
    coordinator._is_reload = False
    coordinator.first_refresh = True
    coordinator._pipeline_bypasses_auto_control = False
    coordinator._check_sun_validity_transition = MagicMock(return_value=False)

    # Manual manager: eid_manual is under override, eid_auto is not
    coordinator.manager = _make_manager(covers={eid_manual, eid_auto})
    coordinator.manager.manual_control[eid_manual] = True
    coordinator.manager.manual_control_time[eid_manual] = dt.datetime.now(dt.UTC)

    apply_calls = []

    async def _fake_apply(cover, state, reason, context=None):
        apply_calls.append(cover)

    coordinator._cmd_svc = MagicMock()
    coordinator._cmd_svc.apply_position = _fake_apply
    coordinator._pipeline_result = None
    coordinator._build_position_context = MagicMock(return_value=MagicMock())
    coordinator.logger = MagicMock()

    # _dispatch_to_cover delegates to apply_position; wire it up so the
    # manual-cover skip logic is exercised through the real dispatch path.
    async def _fake_dispatch(cover, state, reason, ctx):
        await _fake_apply(cover, state, reason, context=ctx)

    coordinator._dispatch_to_cover = _fake_dispatch

    # Call the real method with our mock coordinator as self
    await AdaptiveDataUpdateCoordinator.async_handle_first_refresh(
        coordinator, state=50, options={}
    )

    assert (
        eid_manual not in apply_calls
    ), "apply_position should NOT be called for a manually-overridden cover"
    assert (
        eid_auto in apply_calls
    ), "apply_position SHOULD be called for a non-manual cover"


# ---------------------------------------------------------------------------
# Test 5: Timezone round-trip
# ---------------------------------------------------------------------------


def test_expiry_isoformat_round_trips_as_utc_aware():
    """ISO-serialized UTC datetime parses back tz-aware and compares with now(UTC)."""
    expiry = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30)
    iso = expiry.isoformat()

    restored = dt.datetime.fromisoformat(iso)

    assert restored.tzinfo is not None, "Restored datetime must be tz-aware"
    # Should compare cleanly against now(UTC) without TypeError
    diff = restored - dt.datetime.now(dt.UTC)
    assert diff.total_seconds() > 0, "Future expiry should be after now"


def test_restore_preserves_started_at_math_with_timezone():
    """started_at computed from expiry is tz-aware UTC and within 1s of expected."""
    eid = "cover.test"
    reset_minutes = 45
    manager = _make_manager(reset_minutes=reset_minutes, covers={eid})
    sensor = _make_sensor(manager)

    # Simulate a real expiry stored from a previous HA run — anchored 1 hour from now
    original_started_at = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    expiry = original_started_at + dt.timedelta(minutes=reset_minutes)

    sensor._restore_from_attributes({eid: expiry.isoformat()})

    restored_started_at = manager.manual_control_time.get(eid)
    assert restored_started_at is not None
    assert restored_started_at.tzinfo is not None
    assert abs((restored_started_at - original_started_at).total_seconds()) < 1


# ---------------------------------------------------------------------------
# Test 6: Issue #1232 — the destructive tri-state gate at
# coordinator._update_manager_and_covers wipes a restore that just landed.
# ---------------------------------------------------------------------------


async def test_restored_override_survives_full_entry_setup(
    hass: HomeAssistant,
) -> None:
    """Regression (#1232): a restored override must survive real entry setup.

    `PLATFORMS` forwards SENSOR before SWITCH (`__init__.py:107-108`), so
    `_ManualOverrideEndSensor._restore_from_attributes` (sensor.py:304) rehydrates
    the manager before any switch is added. But the very first `_SwitchSpec`
    (`Integration Enabled`, `switch.py:104-109`) calls `async_turn_on(added=True)`
    from its own `async_added_to_hass`, which awaits `coordinator.async_refresh()`
    (`switch.py:358`) — three specs before `Manual Override` (`key="manual_toggle"`)
    restores `_toggles.manual_toggle` out of its `None` default. That refresh
    reaches `_update_manager_and_covers`, which used to truthiness-test the
    tri-state (`not None` is `True`) and reset every cover the sensor had just
    restored.

    Deliberately does NOT use `_patch_coordinator_refresh` (`tests/ha_helpers.py:186`)
    — that replaces `async_config_entry_first_refresh` wholesale, which is a
    *different* refresh than the one this test exercises (the switch-triggered
    `async_refresh()` fires earlier, during platform setup, while `first_refresh`
    is still `False`) and would mask the destructive path entirely.
    """
    expiry = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=45)
    mock_restore_cache(
        hass,
        (
            State(
                "sensor.test_cover_manual_override_end_time",
                expiry.isoformat(),
                {"per_entity": {"cover.test_blind": expiry.isoformat()}},
            ),
        ),
    )

    hass.states.async_set(
        "cover.test_blind",
        "open",
        {"current_position": 100, "supported_features": 143},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Test Cover", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="restore_1232_e2e",
        title="Test Cover",
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.manager.manual_controlled == ["cover.test_blind"]


async def test_update_manager_and_covers_preserves_override_while_toggle_unset(
    hass: HomeAssistant,
) -> None:
    """Regression (#1232), unit-level: an unset `manual_toggle` must not reset.

    Pins the tri-state directly at `_update_manager_and_covers` without going
    through the full switch-platform setup dance, so a future change to
    `_update_manager_and_covers` fails fast here rather than only in the slower
    end-to-end test above.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Restore Cover", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="restore_1232_unit_unset",
        title="Restore Cover",
    )
    entry.add_to_hass(hass)

    token = ha_config_entries.current_entry.set(entry)
    try:
        coordinator = AdaptiveDataUpdateCoordinator(hass)
    finally:
        ha_config_entries.current_entry.reset(token)

    expiry = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=45)
    coordinator.manager.restore_override("cover.test_blind", expiry)

    # Unset default: no switch entity has restored `manual_toggle` yet.
    assert coordinator._toggles.manual_toggle is None

    coordinator._update_manager_and_covers()

    assert coordinator.manager.manual_controlled == ["cover.test_blind"]


async def test_update_manager_and_covers_resets_override_when_detection_disabled(
    hass: HomeAssistant,
) -> None:
    """Contract guard: an explicit `manual_toggle = False` must still reset.

    `_update_manager_and_covers`'s docstring promises the reset happens "if
    manual override detection is disabled" — that is `manual_toggle is False`,
    not merely falsy. This pins the other half of the tri-state fix so a future
    refactor cannot over-correct #1232 into "never reset": the detection-disabled
    case must keep destroying stale override state.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Restore Cover", CONF_SENSOR_TYPE: CoverType.BLIND},
        options=dict(VERTICAL_OPTIONS),
        entry_id="restore_1232_unit_disabled",
        title="Restore Cover",
    )
    entry.add_to_hass(hass)

    token = ha_config_entries.current_entry.set(entry)
    try:
        coordinator = AdaptiveDataUpdateCoordinator(hass)
    finally:
        ha_config_entries.current_entry.reset(token)

    expiry = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=45)
    coordinator.manager.restore_override("cover.test_blind", expiry)

    coordinator.manual_toggle = False

    coordinator._update_manager_and_covers()

    assert coordinator.manager.manual_controlled == []
