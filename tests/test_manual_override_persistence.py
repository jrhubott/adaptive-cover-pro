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


def test_restore_from_attributes_triggers_ha_state_write():
    """async_write_ha_state is called after a successful restore."""
    eid = "cover.living_room"
    manager = _make_manager(covers={eid})
    sensor = _make_sensor(manager)

    expiry = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30)
    sensor._restore_from_attributes({eid: expiry.isoformat()})

    assert sensor._write_ha_state_called is True


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


def test_restore_from_attributes_does_not_write_ha_state_when_nothing_restored():
    """No valid restores → async_write_ha_state is not called."""
    eid = "cover.bedroom"
    manager = _make_manager(covers={eid})
    sensor = _make_sensor(manager)

    expiry = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    sensor._restore_from_attributes({eid: expiry.isoformat()})

    assert sensor._write_ha_state_called is False


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
    coordinator._build_position_context = MagicMock(return_value=MagicMock())
    coordinator.logger = MagicMock()

    # Call the real method with our mock coordinator as self
    await AdaptiveDataUpdateCoordinator.async_handle_first_refresh(
        coordinator, state=50, options={}
    )

    assert eid_manual not in apply_calls, (
        "apply_position should NOT be called for a manually-overridden cover"
    )
    assert eid_auto in apply_calls, (
        "apply_position SHOULD be called for a non-manual cover"
    )


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
