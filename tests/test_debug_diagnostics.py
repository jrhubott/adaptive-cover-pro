"""Tests for Debug & Diagnostics feature (ring buffer, diagnostics surface, cover command getters)."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import MagicMock


from custom_components.adaptive_cover_pro.const import (
    DEFAULT_DEBUG_EVENT_BUFFER_SIZE,
    MAX_DEBUG_EVENT_BUFFER_SIZE,
)
from custom_components.adaptive_cover_pro.diagnostics.builder import (
    DiagnosticContext,
    DiagnosticsBuilder,
)
from custom_components.adaptive_cover_pro.managers.manual_override import (
    AdaptiveCoverManager,
)
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult
from custom_components.adaptive_cover_pro.enums import ControlMethod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager() -> AdaptiveCoverManager:
    """Return an AdaptiveCoverManager with a minimal mock hass and logger."""
    hass = MagicMock()
    logger = MagicMock()
    mgr = AdaptiveCoverManager(hass, {"hours": 2}, logger)
    mgr.add_covers({"cover.test"})
    return mgr


def _make_state_event(entity_id: str, new_pos: int, old_pos: int = 50):
    """Create a minimal StateChangedData-like object."""
    new_state = MagicMock()
    new_state.state = "stopped"
    new_state.attributes = {"current_position": new_pos}
    new_state.last_updated = dt.datetime.now(dt.UTC)

    old_state = MagicMock()
    old_state.state = "stopped"
    old_state.attributes = {"current_position": old_pos}

    event = MagicMock()
    event.entity_id = entity_id
    event.new_state = new_state
    event.old_state = old_state
    return event


def _base_ctx(**overrides) -> DiagnosticContext:
    """Return a DiagnosticContext with sensible defaults."""
    pr = PipelineResult(
        position=50,
        control_method=ControlMethod.SOLAR,
        reason="sun in FOV",
        raw_calculated_position=50,
        climate_state=None,
        climate_strategy=None,
        climate_data=None,
        default_position=0,
        is_sunset_active=False,
        configured_default=0,
        configured_sunset_pos=None,
        bypass_auto_control=False,
    )
    cover = SimpleNamespace(
        gamma=10.0,
        valid=True,
        valid_elevation=True,
        is_sun_in_blind_spot=False,
        direct_sun_valid=True,
        sunset_valid=False,
        control_state_reason="Sun in FOV",
    )
    defaults = {
        "pos_sun": [180.0, 45.0],
        "cover": cover,
        "pipeline_result": pr,
        "climate_mode": False,
        "check_adaptive_time": True,
        "after_start_time": True,
        "before_end_time": True,
        "start_time": None,
        "end_time": None,
        "automatic_control": True,
        "last_cover_action": {},
        "last_skipped_action": {},
        "min_change": 1,
        "time_threshold": 2,
        "switch_mode": False,
        "inverse_state": False,
        "use_interpolation": False,
        "final_state": 50,
        "config_options": {},
        "motion_detected": True,
        "motion_timeout_active": False,
        "force_override_sensors": [],
        "force_override_position": 0,
    }
    defaults.update(overrides)
    return DiagnosticContext(**defaults)


# ---------------------------------------------------------------------------
# AdaptiveCoverManager — ring buffer defaults
# ---------------------------------------------------------------------------


class TestRingBufferDefaults:
    """Verify ring buffer initialises correctly."""

    def test_buffer_starts_empty(self):
        """Buffer is empty on initialisation."""
        mgr = _make_manager()
        assert mgr.get_event_buffer() == []

    def test_buffer_maxlen_equals_default(self):
        """Buffer maxlen matches DEFAULT_DEBUG_EVENT_BUFFER_SIZE."""
        mgr = _make_manager()
        assert mgr._event_buffer.maxlen == DEFAULT_DEBUG_EVENT_BUFFER_SIZE

    def test_get_event_buffer_returns_list_copy(self):
        """get_event_buffer returns a list copy, not a reference to the deque."""
        mgr = _make_manager()
        buf = mgr.get_event_buffer()
        assert isinstance(buf, list)
        # Mutating the returned list must not mutate the internal deque
        buf.append({"fake": True})
        assert len(mgr._event_buffer) == 0


# ---------------------------------------------------------------------------
# AdaptiveCoverManager — ring buffer records correct actions
# ---------------------------------------------------------------------------


class TestRingBufferEvents:
    """Verify _record_event is called at the right decision points."""

    def test_threshold_breach_records_set(self):
        """Manual override detection records 'set' when delta >= threshold."""
        mgr = _make_manager()
        event = _make_state_event("cover.test", new_pos=80, old_pos=50)
        # our_state=50, new_pos=80 → delta=30 >> threshold
        mgr.handle_state_change(
            states_data=event,
            our_state=50,
            blind_type="cover_blind",
            allow_reset=True,
            wait_target_call={},
            manual_threshold=3,
        )
        buf = mgr.get_event_buffer()
        set_events = [e for e in buf if e["action"] == "set"]
        assert len(set_events) == 1
        ev = set_events[0]
        assert ev["entity_id"] == "cover.test"
        assert ev["our_state"] == 50
        assert ev["new_position"] == 80

    def test_within_threshold_records_rejection(self):
        """Delta below threshold records 'rejected_within_threshold'."""
        mgr = _make_manager()
        event = _make_state_event("cover.test", new_pos=51, old_pos=50)
        mgr.handle_state_change(
            states_data=event,
            our_state=50,
            blind_type="cover_blind",
            allow_reset=True,
            wait_target_call={},
            manual_threshold=5,
        )
        buf = mgr.get_event_buffer()
        rejected = [e for e in buf if e["action"] == "rejected_within_threshold"]
        assert len(rejected) == 1

    def test_wait_for_target_records_rejection(self):
        """Event during wait_for_target records 'rejected_wait_for_target'."""
        mgr = _make_manager()
        event = _make_state_event("cover.test", new_pos=80)
        mgr.handle_state_change(
            states_data=event,
            our_state=50,
            blind_type="cover_blind",
            allow_reset=True,
            wait_target_call={"cover.test": True},
            manual_threshold=3,
        )
        buf = mgr.get_event_buffer()
        rejected = [e for e in buf if e["action"] == "rejected_wait_for_target"]
        assert len(rejected) == 1

    def test_position_unavailable_records_rejection(self):
        """None position records 'rejected_position_unavailable'."""
        mgr = _make_manager()
        event = _make_state_event("cover.test", new_pos=80)
        event.new_state.attributes = {}  # no current_position key
        # Mock get_open_close_state to return None
        from unittest.mock import patch
        with patch(
            "custom_components.adaptive_cover_pro.managers.manual_override.get_open_close_state",
            return_value=None,
        ):
            mgr.handle_state_change(
                states_data=event,
                our_state=50,
                blind_type="cover_blind",
                allow_reset=True,
                wait_target_call={},
                manual_threshold=3,
            )
        buf = mgr.get_event_buffer()
        rejected = [e for e in buf if e["action"] == "rejected_position_unavailable"]
        assert len(rejected) == 1

    def test_reset_records_reset_event(self):
        """reset() records a 'reset' event in the buffer."""
        mgr = _make_manager()
        mgr.manual_control["cover.test"] = True
        mgr.reset("cover.test")
        buf = mgr.get_event_buffer()
        reset_events = [e for e in buf if e["action"] == "reset"]
        assert len(reset_events) == 1
        assert reset_events[0]["entity_id"] == "cover.test"

    def test_event_has_required_keys(self):
        """Every recorded event has the required keys."""
        mgr = _make_manager()
        event = _make_state_event("cover.test", new_pos=80)
        mgr.handle_state_change(
            states_data=event,
            our_state=50,
            blind_type="cover_blind",
            allow_reset=True,
            wait_target_call={},
            manual_threshold=3,
        )
        required_keys = {"ts", "entity_id", "action", "our_state", "new_position", "reason"}
        for ev in mgr.get_event_buffer():
            assert required_keys.issubset(ev.keys()), f"Missing keys in: {ev}"

    def test_event_ts_is_iso_string(self):
        """Event timestamp is an ISO-format string."""
        mgr = _make_manager()
        event = _make_state_event("cover.test", new_pos=80)
        mgr.handle_state_change(
            states_data=event,
            our_state=50,
            blind_type="cover_blind",
            allow_reset=True,
            wait_target_call={},
            manual_threshold=3,
        )
        ev = mgr.get_event_buffer()[0]
        # Should parse without error
        dt.datetime.fromisoformat(ev["ts"])


# ---------------------------------------------------------------------------
# AdaptiveCoverManager — resize_event_buffer
# ---------------------------------------------------------------------------


class TestResizeEventBuffer:
    """Verify resize_event_buffer works correctly."""

    def test_resize_to_larger_preserves_events(self):
        """Resizing to larger capacity preserves all existing events."""
        mgr = _make_manager()
        # Populate 5 events
        for i in range(5):
            mgr._record_event(
                f"cover.{i}",
                "set",
                our_state=50,
                new_position=80,
                reason="test",
            )
        mgr.resize_event_buffer(100)
        assert len(mgr.get_event_buffer()) == 5
        assert mgr._event_buffer.maxlen == 100

    def test_resize_to_smaller_keeps_most_recent(self):
        """Resizing to smaller capacity keeps the most recent events."""
        mgr = _make_manager()
        # Populate 10 events with distinguishable entity IDs
        for i in range(10):
            mgr._record_event(
                f"cover.{i}",
                "set",
                our_state=50,
                new_position=80,
                reason="test",
            )
        mgr.resize_event_buffer(3)
        buf = mgr.get_event_buffer()
        assert len(buf) == 3
        assert mgr._event_buffer.maxlen == 3
        # Most recent 3 should be covers 7, 8, 9
        entity_ids = [e["entity_id"] for e in buf]
        assert entity_ids == ["cover.7", "cover.8", "cover.9"]

    def test_resize_to_max_config_value(self):
        """Resizing to MAX_DEBUG_EVENT_BUFFER_SIZE is accepted."""
        mgr = _make_manager()
        mgr.resize_event_buffer(MAX_DEBUG_EVENT_BUFFER_SIZE)
        assert mgr._event_buffer.maxlen == MAX_DEBUG_EVENT_BUFFER_SIZE

    def test_ring_buffer_overwrites_oldest_when_full(self):
        """Ring buffer overwrites the oldest event when at capacity."""
        mgr = _make_manager()
        mgr.resize_event_buffer(3)
        for i in range(5):
            mgr._record_event(
                f"cover.{i}",
                "set",
                our_state=50,
                new_position=80,
                reason="test",
            )
        buf = mgr.get_event_buffer()
        assert len(buf) == 3
        entity_ids = [e["entity_id"] for e in buf]
        assert entity_ids == ["cover.2", "cover.3", "cover.4"]


# ---------------------------------------------------------------------------
# DiagnosticsBuilder — debug info section
# ---------------------------------------------------------------------------


class TestDiagnosticsBuilderDebugInfo:
    """Verify _build_debug_info emits and omits fields correctly."""

    def test_debug_section_omitted_when_all_none(self):
        """All debug fields are absent when context fields are None."""
        builder = DiagnosticsBuilder()
        ctx = _base_ctx()  # manual_override_events=None, cover_command_state=None, debug_config=None
        result, _ = builder.build(ctx)
        assert "debug_config" not in result
        assert "manual_override_history" not in result
        # cover_commands is always present (empty dict when no state)
        assert result.get("cover_commands") == {}

    def test_debug_config_emitted_when_provided(self):
        """debug_config is included in output when provided."""
        builder = DiagnosticsBuilder()
        debug_config = {"debug_mode": True, "debug_categories": ["manual_override"], "debug_event_buffer_size": 50}
        ctx = _base_ctx(debug_config=debug_config)
        result, _ = builder.build(ctx)
        assert result["debug_config"] == debug_config

    def test_debug_config_dry_run_field_flows_through(self):
        """dry_run key in debug_config is preserved in the diagnostics payload."""
        builder = DiagnosticsBuilder()
        debug_config = {"dry_run": True, "debug_mode": False, "debug_categories": [], "debug_event_buffer_size": 50}
        ctx = _base_ctx(debug_config=debug_config)
        result, _ = builder.build(ctx)
        assert result["debug_config"]["dry_run"] is True

    def test_manual_override_history_emitted_when_populated(self):
        """manual_override_history is included in output when events exist."""
        builder = DiagnosticsBuilder()
        events = [{"ts": "2024-01-01T00:00:00+00:00", "action": "set", "entity_id": "cover.test"}]
        ctx = _base_ctx(manual_override_events=events)
        result, _ = builder.build(ctx)
        assert result["manual_override_history"] == events

    def test_manual_override_history_omitted_when_empty_list(self):
        """manual_override_history is absent when the event list is empty."""
        builder = DiagnosticsBuilder()
        ctx = _base_ctx(manual_override_events=[])
        result, _ = builder.build(ctx)
        assert "manual_override_history" not in result

    def test_cover_commands_emitted_when_provided(self):
        """cover_commands is populated when cover_command_state is provided."""
        builder = DiagnosticsBuilder()
        state = {"cover.test": {"target_call": 50, "wait_for_target": False}}
        ctx = _base_ctx(cover_command_state=state)
        result, _ = builder.build(ctx)
        assert result["cover_commands"] == state

    def test_cover_commands_empty_when_empty_dict(self):
        """cover_commands is an empty dict when cover_command_state is empty."""
        builder = DiagnosticsBuilder()
        ctx = _base_ctx(cover_command_state={})
        result, _ = builder.build(ctx)
        assert result["cover_commands"] == {}

    def test_all_three_sections_present_together(self):
        """All debug sections appear together when all are populated."""
        builder = DiagnosticsBuilder()
        events = [{"action": "set"}]
        state = {"cover.test": {"target_call": 50}}
        config = {"debug_mode": True}
        ctx = _base_ctx(
            manual_override_events=events,
            cover_command_state=state,
            debug_config=config,
        )
        result, _ = builder.build(ctx)
        assert "manual_override_history" in result
        assert result["cover_commands"] == state
        assert "debug_config" in result


# ---------------------------------------------------------------------------
# CoverCommandService — get_entity_state_snapshot
# ---------------------------------------------------------------------------


class TestCoverCommandServiceSnapshots:
    """Verify public snapshot accessors return correct structure."""

    def _make_svc(self):
        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
        )
        from custom_components.adaptive_cover_pro.managers.grace_period import (
            GracePeriodManager,
        )

        hass = MagicMock()
        logger = MagicMock()
        grace_mgr = GracePeriodManager(logger=logger, command_grace_seconds=5.0)
        svc = CoverCommandService(
            hass=hass,
            logger=logger,
            cover_type="cover_blind",
            grace_mgr=grace_mgr,
        )
        return svc

    def test_snapshot_for_unknown_entity_has_defaults(self):
        """Snapshot for an entity with no tracked state returns safe defaults."""
        svc = self._make_svc()
        snap = svc.get_entity_state_snapshot("cover.unknown")
        assert snap["target_call"] is None
        assert snap["wait_for_target"] is False
        assert snap["retry_count"] == 0
        assert snap["gave_up"] is False
        assert snap["last_command_sent_at"] is None
        assert snap["in_manual_override_set"] is False
        assert snap["safety_target"] is False
        assert snap["last_reconcile_time"] is None

    def test_snapshot_reflects_set_values(self):
        """Snapshot correctly reflects all per-entity state values."""
        svc = self._make_svc()
        svc.target_call["cover.test"] = 75
        svc.wait_for_target["cover.test"] = True
        svc._retry_counts["cover.test"] = 2
        svc._gave_up.add("cover.test")
        svc._manual_override_entities.add("cover.test")
        svc._safety_targets.add("cover.test")

        snap = svc.get_entity_state_snapshot("cover.test")
        assert snap["target_call"] == 75
        assert snap["wait_for_target"] is True
        assert snap["retry_count"] == 2
        assert snap["gave_up"] is True
        assert snap["in_manual_override_set"] is True
        assert snap["safety_target"] is True

    def test_get_all_snapshots_covers_all_tracked_entities(self):
        """get_all_entity_state_snapshots includes every tracked entity."""
        svc = self._make_svc()
        svc.target_call["cover.a"] = 30
        svc.wait_for_target["cover.b"] = False
        snaps = svc.get_all_entity_state_snapshots()
        assert "cover.a" in snaps
        assert "cover.b" in snaps

    def test_get_all_snapshots_returns_empty_when_no_entities(self):
        """get_all_entity_state_snapshots returns empty dict when no entities tracked."""
        svc = self._make_svc()
        snaps = svc.get_all_entity_state_snapshots()
        assert snaps == {}

    def test_snapshot_last_command_sent_at_is_isoformat(self):
        """last_command_sent_at is serialised as an ISO-format string."""
        svc = self._make_svc()
        now = dt.datetime.now(dt.UTC)
        svc._sent_at["cover.test"] = now
        svc.target_call["cover.test"] = 50
        snap = svc.get_entity_state_snapshot("cover.test")
        assert snap["last_command_sent_at"] == now.isoformat()

    def test_snapshot_last_reconcile_time_is_isoformat(self):
        """last_reconcile_time is serialised as an ISO-format string."""
        svc = self._make_svc()
        now = dt.datetime.now(dt.UTC)
        svc._last_reconcile_time["cover.test"] = now
        svc.target_call["cover.test"] = 50
        snap = svc.get_entity_state_snapshot("cover.test")
        assert snap["last_reconcile_time"] == now.isoformat()
