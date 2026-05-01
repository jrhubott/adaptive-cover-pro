"""Tests verifying that diagnostic events are recorded in the EventBuffer.

Each test confirms that a specific decision point writes the expected event type
to the ring buffer, so that diagnostics captures the relevant history for
troubleshooting (e.g. transit stalls, grace expiry, motion timeouts).
"""

from __future__ import annotations

import asyncio
import datetime as dt
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.diagnostics.event_buffer import EventBuffer
from custom_components.adaptive_cover_pro.managers.grace_period import (
    GracePeriodManager,
)
from custom_components.adaptive_cover_pro.managers.motion import MotionManager
from custom_components.adaptive_cover_pro.managers.toggles import ToggleManager

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _events(buf: EventBuffer) -> list[dict]:
    return buf.snapshot()


def _event_types(buf: EventBuffer) -> list[str]:
    return [e["event"] for e in _events(buf)]


def _make_state(position: int, state_str: str = "open") -> MagicMock:
    s = MagicMock()
    s.state = state_str
    s.attributes = {"current_position": position}
    s.last_updated = dt.datetime.now(dt.UTC)
    return s


def _make_event(
    entity_id: str, new_pos: int, old_pos: int, state: str = "open"
) -> MagicMock:
    ev = MagicMock()
    ev.entity_id = entity_id
    ev.new_state = _make_state(new_pos, state)
    ev.old_state = _make_state(old_pos, state)
    return ev


def _make_transit_coordinator(
    entity_id: str,
    *,
    target: int,
    new_pos: int,
    old_pos: int,
    state: str = "open",
    sent_seconds_ago: float = 10.0,
    last_progress_seconds_ago: float | None = None,
    transit_timeout: int = 45,
) -> MagicMock:
    """Minimal coordinator wired for process_entity_state_change transit tests."""
    from custom_components.adaptive_cover_pro.managers.cover_command import (
        CoverCommandService,
    )
    from custom_components.adaptive_cover_pro.managers.grace_period import (
        GracePeriodManager,
    )
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    buf = EventBuffer(maxlen=100)

    coord = MagicMock()
    coord._event_buffer = buf
    coord.state_change_data = _make_event(entity_id, new_pos, old_pos, state)
    coord.ignore_intermediate_states = False
    coord._target_just_reached = set()

    grace_mgr = GracePeriodManager(logger=MagicMock(), command_grace_seconds=5.0)
    coord._grace_mgr = grace_mgr

    cmd_svc = MagicMock(spec=CoverCommandService)
    cmd_svc.wait_for_target = {entity_id: True}
    cmd_svc.target_call = {entity_id: target}
    cmd_svc._position_tolerance = 5
    cmd_svc._wait_for_target_timeout_seconds = transit_timeout

    now = dt.datetime.now(dt.UTC)
    cmd_svc._sent_at = {entity_id: now - dt.timedelta(seconds=sent_seconds_ago)}

    _progress: dict[str, dt.datetime] = {}
    if last_progress_seconds_ago is not None:
        _progress[entity_id] = now - dt.timedelta(seconds=last_progress_seconds_ago)

    def _elapsed(eid, now_arg):
        ref = _progress.get(eid) or cmd_svc._sent_at.get(eid)
        return (now_arg - ref).total_seconds() if ref else None

    def _record_prog(eid, now_arg):
        _progress[eid] = now_arg

    cmd_svc._transit_elapsed_without_progress = MagicMock(side_effect=_elapsed)
    cmd_svc.record_progress = MagicMock(side_effect=_record_prog)
    cmd_svc.check_target_reached = MagicMock(return_value=False)
    cmd_svc.get_cover_capabilities = MagicMock(return_value={"has_set_position": True})

    def _read_pos(eid, caps, state_obj):
        return new_pos if state_obj is coord.state_change_data.new_state else old_pos

    cmd_svc.read_position_with_capabilities = MagicMock(side_effect=_read_pos)
    coord._cmd_svc = cmd_svc

    coord._is_in_grace_period = (
        lambda eid: AdaptiveDataUpdateCoordinator._is_in_grace_period(coord, eid)
    )
    coord._start_grace_period = (
        lambda eid: AdaptiveDataUpdateCoordinator._start_grace_period(coord, eid)
    )

    return coord


def _call_process(coord) -> None:
    from custom_components.adaptive_cover_pro.coordinator import (
        AdaptiveDataUpdateCoordinator,
    )

    AdaptiveDataUpdateCoordinator.process_entity_state_change(coord)


# ===========================================================================
# Transit progress events
# ===========================================================================


class TestTransitProgressEvents:
    """process_entity_state_change records transit decision events into the buffer."""

    def test_forward_progress_records_transit_progress_forward(self) -> None:
        coord = _make_transit_coordinator(
            "cover.shade", target=0, new_pos=50, old_pos=60, sent_seconds_ago=10.0
        )
        _call_process(coord)
        assert "transit_progress_forward" in _event_types(coord._event_buffer)

    def test_forward_progress_event_contains_expected_fields(self) -> None:
        coord = _make_transit_coordinator(
            "cover.shade", target=0, new_pos=50, old_pos=60, sent_seconds_ago=10.0
        )
        _call_process(coord)
        ev = next(
            e
            for e in _events(coord._event_buffer)
            if e["event"] == "transit_progress_forward"
        )
        assert ev["entity_id"] == "cover.shade"
        assert ev["old_position"] == 60
        assert ev["new_position"] == 50
        assert ev["target"] == 0
        assert ev["old_distance"] == 60
        assert ev["new_distance"] == 50

    def test_timeout_exceeded_records_transit_timeout_cleared(self) -> None:
        coord = _make_transit_coordinator(
            "cover.shade",
            target=0,
            new_pos=80,
            old_pos=80,
            sent_seconds_ago=50.0,
            transit_timeout=45,
        )
        _call_process(coord)
        assert "transit_timeout_cleared" in _event_types(coord._event_buffer)

    def test_timeout_event_contains_elapsed_and_timeout(self) -> None:
        coord = _make_transit_coordinator(
            "cover.shade",
            target=0,
            new_pos=80,
            old_pos=80,
            sent_seconds_ago=50.0,
            transit_timeout=45,
        )
        _call_process(coord)
        ev = next(
            e
            for e in _events(coord._event_buffer)
            if e["event"] == "transit_timeout_cleared"
        )
        assert ev["entity_id"] == "cover.shade"
        assert ev["timeout_seconds"] == 45
        assert ev["elapsed_seconds"] > 45

    def test_startup_delay_records_transit_startup_delay(self) -> None:
        """Position unchanged, state transitioned from non-opening — motor startup."""
        coord = _make_transit_coordinator(
            "cover.shade",
            target=100,
            new_pos=0,
            old_pos=0,
            state="open",
            sent_seconds_ago=5.0,
        )
        coord.state_change_data.old_state.state = "closed"  # state changed
        coord.state_change_data.new_state.state = "open"
        _call_process(coord)
        assert "transit_startup_delay" in _event_types(coord._event_buffer)

    def test_transit_cleared_records_event_when_not_in_transit(self) -> None:
        """Cover moved away from target — wait_for_target cleared."""
        coord = _make_transit_coordinator(
            "cover.shade", target=0, new_pos=70, old_pos=60, sent_seconds_ago=10.0
        )
        coord.state_change_data.old_state.state = "open"
        _call_process(coord)
        assert "transit_cleared" in _event_types(coord._event_buffer)

    def test_transit_cleared_event_contains_entity_and_position(self) -> None:
        coord = _make_transit_coordinator(
            "cover.shade", target=0, new_pos=70, old_pos=60, sent_seconds_ago=10.0
        )
        coord.state_change_data.old_state.state = "open"
        _call_process(coord)
        ev = next(
            e for e in _events(coord._event_buffer) if e["event"] == "transit_cleared"
        )
        assert ev["entity_id"] == "cover.shade"
        assert ev["position"] == 70
        assert ev["target"] == 0


# ===========================================================================
# Manual override gate closed event
# ===========================================================================


class TestManualGateClosedEvent:
    """_manual_gate_closed_log records an event with gate state details."""

    def _make_coord(self) -> MagicMock:
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        buf = EventBuffer(maxlen=50)
        coord = object.__new__(AdaptiveDataUpdateCoordinator)
        coord.logger = MagicMock()
        coord._toggles = ToggleManager()
        coord._event_buffer = buf
        return coord

    def test_gate_closed_records_event(self) -> None:
        coord = self._make_coord()
        coord.manual_toggle = False
        coord.automatic_control = True
        coord._manual_gate_closed_log("test_site", ["cover.test"])
        assert "manual_override_gate_closed" in _event_types(coord._event_buffer)

    def test_gate_closed_event_contains_where_and_flags(self) -> None:
        coord = self._make_coord()
        coord.manual_toggle = False
        coord.automatic_control = True
        coord._manual_gate_closed_log("handle_state_change", ["cover.living_room"])
        ev = next(
            e
            for e in _events(coord._event_buffer)
            if e["event"] == "manual_override_gate_closed"
        )
        assert ev["where"] == "handle_state_change"
        assert ev["manual_toggle"] is False
        assert ev["automatic_control"] is True
        assert "cover.living_room" in ev["entity_ids"]


# ===========================================================================
# Sun FOV transition events
# ===========================================================================


class TestSunFOVEvents:
    """_check_sun_validity_transition records sun_entered_fov / sun_left_fov."""

    def _make_coord(self, *, direct_sun_valid: bool, prev_state: bool) -> MagicMock:
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        buf = EventBuffer(maxlen=50)
        coord = object.__new__(AdaptiveDataUpdateCoordinator)
        coord.logger = MagicMock()
        coord._toggles = ToggleManager()
        coord._event_buffer = buf
        cover_data = MagicMock()
        cover_data.direct_sun_valid = direct_sun_valid
        coord._cover_data = cover_data
        coord._last_sun_validity_state = prev_state
        return coord

    def test_sun_entered_fov_records_event(self) -> None:
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = self._make_coord(direct_sun_valid=True, prev_state=False)
        AdaptiveDataUpdateCoordinator._check_sun_validity_transition(coord)
        assert "sun_entered_fov" in _event_types(coord._event_buffer)

    def test_sun_left_fov_records_event(self) -> None:
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = self._make_coord(direct_sun_valid=False, prev_state=True)
        AdaptiveDataUpdateCoordinator._check_sun_validity_transition(coord)
        assert "sun_left_fov" in _event_types(coord._event_buffer)

    def test_no_transition_records_no_fov_event(self) -> None:
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = self._make_coord(direct_sun_valid=True, prev_state=True)
        AdaptiveDataUpdateCoordinator._check_sun_validity_transition(coord)
        types = _event_types(coord._event_buffer)
        assert "sun_entered_fov" not in types
        assert "sun_left_fov" not in types


# ===========================================================================
# End-time default sent event
# ===========================================================================


class TestEndTimeDefaultSentEvent:
    """_check_time_window_transition records end_time_default_sent when end-time fires."""

    def _make_end_time_coord(
        self,
        buf: EventBuffer,
        *,
        n_entities: int = 1,
        effective_pos: int = 0,
        is_sunset: bool = False,
    ) -> MagicMock:
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = object.__new__(AdaptiveDataUpdateCoordinator)
        coord.logger = MagicMock()
        coord._toggles = ToggleManager()
        coord._event_buffer = buf
        coord.automatic_control = True
        coord._track_end_time = True
        coord._inverse_state = False
        coord.entities = [MagicMock() for _ in range(n_entities)]

        cmd_svc = MagicMock()
        cmd_svc.apply_position = AsyncMock(return_value=("sent", ""))
        coord._cmd_svc = cmd_svc
        coord.async_refresh = AsyncMock()
        coord._build_position_context = MagicMock(return_value=MagicMock(force=True))
        coord._compute_current_effective_default = MagicMock(
            return_value=(effective_pos, is_sunset)
        )

        config_entry = MagicMock()
        config_entry.options = {}
        coord.config_entry = config_entry

        async def _invoke_close(track_end_time, refresh_callback, on_window_open=None):
            await refresh_callback()

        time_mgr = MagicMock()
        time_mgr.check_transition = _invoke_close
        coord._time_mgr = time_mgr

        return coord

    @pytest.mark.asyncio
    async def test_end_time_default_sent_records_event(self) -> None:
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        buf = EventBuffer(maxlen=50)
        coord = self._make_end_time_coord(buf)
        await AdaptiveDataUpdateCoordinator._check_time_window_transition(
            coord, dt.datetime.now(dt.UTC)
        )
        assert "end_time_default_sent" in _event_types(buf)

    @pytest.mark.asyncio
    async def test_end_time_event_contains_position_and_cover_count(self) -> None:
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        buf = EventBuffer(maxlen=50)
        coord = self._make_end_time_coord(
            buf, n_entities=2, effective_pos=30, is_sunset=True
        )
        await AdaptiveDataUpdateCoordinator._check_time_window_transition(
            coord, dt.datetime.now(dt.UTC)
        )
        ev = next(e for e in _events(buf) if e["event"] == "end_time_default_sent")
        assert ev["cover_count"] == 2
        assert ev["sunset_active"] is True


# ===========================================================================
# Sunset window opened event
# ===========================================================================


class TestSunsetWindowOpenedEvent:
    """_check_sunset_window_transition records sunset_window_opened."""

    def _make_coord(self, *, sunset_pos: int = 25) -> MagicMock:
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )
        from custom_components.adaptive_cover_pro.const import CONF_SUNSET_POS
        from custom_components.adaptive_cover_pro.managers.cover_command import (
            PositionContext,
        )

        buf = EventBuffer(maxlen=50)
        coord = object.__new__(AdaptiveDataUpdateCoordinator)
        coord.logger = MagicMock()
        coord._toggles = ToggleManager()
        coord._event_buffer = buf
        coord.automatic_control = True
        coord._track_end_time = True
        coord._inverse_state = False
        coord._prev_sunset_active = False

        entities = [MagicMock()]
        coord.entities = entities
        config_entry = MagicMock()
        config_entry.options = {CONF_SUNSET_POS: sunset_pos}
        coord.config_entry = config_entry

        cmd_svc = MagicMock()
        cmd_svc.apply_position = AsyncMock(return_value=("sent", ""))
        coord._cmd_svc = cmd_svc
        coord.async_refresh = AsyncMock()

        manager = MagicMock()
        manager.is_cover_manual.return_value = False
        coord.manager = manager

        coord._build_position_context = MagicMock(
            return_value=PositionContext(
                auto_control=True,
                manual_override=False,
                sun_just_appeared=False,
                min_change=2,
                time_threshold=0,
                special_positions=[0, 100],
                force=False,
                is_safety=False,
            )
        )
        coord._compute_current_effective_default = MagicMock(return_value=(0, True))
        return coord

    @pytest.mark.asyncio
    async def test_sunset_window_opened_records_event(self) -> None:
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = self._make_coord(sunset_pos=25)
        await AdaptiveDataUpdateCoordinator._check_sunset_window_transition(coord)
        assert "sunset_window_opened" in _event_types(coord._event_buffer)

    @pytest.mark.asyncio
    async def test_sunset_window_event_contains_position_and_count(self) -> None:
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = self._make_coord(sunset_pos=25)
        coord.entities = [MagicMock(), MagicMock()]
        await AdaptiveDataUpdateCoordinator._check_sunset_window_transition(coord)
        ev = next(
            e
            for e in _events(coord._event_buffer)
            if e["event"] == "sunset_window_opened"
        )
        assert ev["position"] == 25
        assert ev["cover_count"] == 2

    @pytest.mark.asyncio
    async def test_no_sunset_window_event_when_already_open(self) -> None:
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = self._make_coord()
        coord._prev_sunset_active = True  # already open — no transition
        await AdaptiveDataUpdateCoordinator._check_sunset_window_transition(coord)
        assert "sunset_window_opened" not in _event_types(coord._event_buffer)


# ===========================================================================
# Grace period expiry events
# ===========================================================================


class TestGracePeriodExpiryEvents:
    """GracePeriodManager records grace_period_expired and startup_grace_expired."""

    @pytest.mark.asyncio
    async def test_command_grace_expired_records_event(self) -> None:
        buf = EventBuffer(maxlen=50)
        mgr = GracePeriodManager(
            logger=MagicMock(),
            command_grace_seconds=0.01,
            event_buffer=buf,
        )
        mgr.start_command_grace_period("cover.test")
        await asyncio.sleep(0.05)
        assert "grace_period_expired" in _event_types(buf)

    @pytest.mark.asyncio
    async def test_command_grace_expired_event_contains_entity_and_duration(
        self,
    ) -> None:
        buf = EventBuffer(maxlen=50)
        mgr = GracePeriodManager(
            logger=MagicMock(),
            command_grace_seconds=0.01,
            event_buffer=buf,
        )
        mgr.start_command_grace_period("cover.living_room")
        await asyncio.sleep(0.05)
        ev = next(e for e in _events(buf) if e["event"] == "grace_period_expired")
        assert ev["entity_id"] == "cover.living_room"
        assert ev["duration_seconds"] == pytest.approx(0.01)

    @pytest.mark.asyncio
    async def test_startup_grace_expired_records_event(self) -> None:
        buf = EventBuffer(maxlen=50)
        mgr = GracePeriodManager(
            logger=MagicMock(),
            command_grace_seconds=5.0,
            startup_grace_seconds=0.01,
            event_buffer=buf,
        )
        mgr.start_startup_grace_period()
        await asyncio.sleep(0.05)
        assert "startup_grace_expired" in _event_types(buf)

    @pytest.mark.asyncio
    async def test_startup_grace_expired_event_contains_duration(self) -> None:
        buf = EventBuffer(maxlen=50)
        mgr = GracePeriodManager(
            logger=MagicMock(),
            command_grace_seconds=5.0,
            startup_grace_seconds=0.01,
            event_buffer=buf,
        )
        mgr.start_startup_grace_period()
        await asyncio.sleep(0.05)
        ev = next(e for e in _events(buf) if e["event"] == "startup_grace_expired")
        assert ev["duration_seconds"] == pytest.approx(0.01)

    @pytest.mark.asyncio
    async def test_no_event_when_grace_period_cancelled(self) -> None:
        buf = EventBuffer(maxlen=50)
        mgr = GracePeriodManager(
            logger=MagicMock(),
            command_grace_seconds=10.0,
            event_buffer=buf,
        )
        mgr.start_command_grace_period("cover.test")
        mgr.cancel_command_grace_period("cover.test")
        await asyncio.sleep(0.01)
        assert "grace_period_expired" not in _event_types(buf)

    @pytest.mark.asyncio
    async def test_no_event_when_no_buffer_configured(self) -> None:
        """GracePeriodManager without event_buffer does not raise."""
        mgr = GracePeriodManager(
            logger=MagicMock(),
            command_grace_seconds=0.01,
        )
        mgr.start_command_grace_period("cover.test")
        await asyncio.sleep(0.05)  # must not raise


# ===========================================================================
# Motion timeout events
# ===========================================================================


class TestMotionTimeoutEvents:
    """MotionManager records motion_timeout_started, _expired, _canceled, _detected_during."""

    def _make_mgr(
        self, buf: EventBuffer, *, sensors: list[str] | None = None
    ) -> MotionManager:
        hass = MagicMock()
        hass.states.get.return_value = None
        mgr = MotionManager(hass=hass, logger=MagicMock(), event_buffer=buf)
        mgr.update_config(
            sensors=sensors or ["binary_sensor.motion"], timeout_seconds=60
        )
        return mgr

    @pytest.mark.asyncio
    async def test_timeout_started_records_event(self) -> None:
        buf = EventBuffer(maxlen=50)
        mgr = self._make_mgr(buf)
        mgr.start_motion_timeout(AsyncMock())
        mgr.cancel_motion_timeout()
        assert "motion_timeout_started" in _event_types(buf)

    @pytest.mark.asyncio
    async def test_timeout_started_event_contains_timeout_seconds(self) -> None:
        buf = EventBuffer(maxlen=50)
        mgr = self._make_mgr(buf)
        mgr.update_config(sensors=["binary_sensor.motion"], timeout_seconds=300)
        mgr.start_motion_timeout(AsyncMock())
        mgr.cancel_motion_timeout()
        ev = next(e for e in _events(buf) if e["event"] == "motion_timeout_started")
        assert ev["timeout_seconds"] == 300

    @pytest.mark.asyncio
    async def test_timeout_canceled_records_event(self) -> None:
        buf = EventBuffer(maxlen=50)
        mgr = self._make_mgr(buf)
        mgr.start_motion_timeout(AsyncMock())
        buf._buf.clear()  # clear started event so we only see canceled
        mgr.cancel_motion_timeout()
        assert "motion_timeout_canceled" in _event_types(buf)

    def test_no_canceled_event_when_no_task_running(self) -> None:
        buf = EventBuffer(maxlen=50)
        mgr = self._make_mgr(buf)
        mgr.cancel_motion_timeout()  # nothing running — must not record
        assert "motion_timeout_canceled" not in _event_types(buf)

    @pytest.mark.asyncio
    async def test_timeout_expired_records_event(self) -> None:
        buf = EventBuffer(maxlen=50)
        hass = MagicMock()
        off_state = MagicMock()
        off_state.state = "off"
        hass.states.get.return_value = off_state  # sensor explicitly off → no motion
        mgr = MotionManager(hass=hass, logger=MagicMock(), event_buffer=buf)
        mgr.update_config(sensors=["binary_sensor.motion"], timeout_seconds=0)
        refresh = AsyncMock()
        mgr.start_motion_timeout(refresh)
        await asyncio.sleep(0.05)
        assert "motion_timeout_expired" in _event_types(buf)

    @pytest.mark.asyncio
    async def test_motion_detected_during_timeout_records_event(self) -> None:
        buf = EventBuffer(maxlen=50)
        hass = MagicMock()
        # Motion active when the timeout handler double-checks
        active_state = MagicMock()
        active_state.state = "on"
        hass.states.get.return_value = active_state
        mgr = MotionManager(hass=hass, logger=MagicMock(), event_buffer=buf)
        mgr.update_config(sensors=["binary_sensor.motion"], timeout_seconds=0)
        mgr.start_motion_timeout(AsyncMock())
        await asyncio.sleep(0.05)
        assert "motion_detected_during_timeout" in _event_types(buf)

    @pytest.mark.asyncio
    async def test_no_events_when_no_buffer_configured(self) -> None:
        """MotionManager without event_buffer does not raise."""
        hass = MagicMock()
        hass.states.get.return_value = None
        mgr = MotionManager(hass=hass, logger=MagicMock())
        mgr.update_config(sensors=["binary_sensor.motion"], timeout_seconds=60)
        mgr.start_motion_timeout(AsyncMock())
        mgr.cancel_motion_timeout()  # must not raise


# ===========================================================================
# Reconcile gave up event
# ===========================================================================


class TestReconcileGaveUpEvent:
    """CoverCommandService._reconcile records reconcile_gave_up when max retries exceeded."""

    def _make_svc(self) -> tuple:
        from custom_components.adaptive_cover_pro.managers.cover_command import (
            CoverCommandService,
        )

        buf = EventBuffer(maxlen=50)
        hass = MagicMock()
        hass.states.get.return_value = None
        hass.services.async_call = AsyncMock()
        svc = CoverCommandService(
            hass=hass,
            logger=MagicMock(),
            cover_type="cover_blind",
            grace_mgr=MagicMock(),
            open_close_threshold=50,
            check_interval_minutes=1,
            position_tolerance=3,
            max_retries=2,
            event_buffer=buf,
        )
        return svc, buf

    def _prime_svc_for_reconcile(
        self, svc, entity_id: str, target: int, actual: int
    ) -> dt.datetime:
        """Set up svc state so a single _reconcile call reaches the gave-up branch.

        Seeds _retry_counts to max_retries so the very first reconcile tick triggers
        gave_up without calling _execute_command (which would reset wait_for_target).
        """
        now = dt.datetime.now(dt.UTC)
        svc.wait_for_target[entity_id] = False
        svc.target_call[entity_id] = target
        svc._sent_at[entity_id] = now - dt.timedelta(seconds=5)
        svc._enabled = True
        svc._auto_control_enabled = True
        svc._in_time_window = True
        svc._retry_counts[entity_id] = svc._max_retries  # already exhausted

        actual_state = MagicMock()
        actual_state.state = "open"
        actual_state.attributes = {"current_position": actual}
        svc._hass.states.get.return_value = actual_state
        return now

    @pytest.mark.asyncio
    async def test_reconcile_gave_up_records_event_after_max_retries(self) -> None:
        from unittest.mock import patch

        svc, buf = self._make_svc()
        entity_id = "cover.test"
        now = self._prime_svc_for_reconcile(svc, entity_id, target=80, actual=50)

        with patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
            return_value={
                "has_set_position": True,
                "has_set_tilt_position": False,
                "has_open": True,
                "has_close": True,
                "has_stop": True,
            },
        ):
            await svc._reconcile(now)

        assert "reconcile_gave_up" in _event_types(buf)

    @pytest.mark.asyncio
    async def test_reconcile_gave_up_event_contains_entity_and_positions(self) -> None:
        from unittest.mock import patch

        svc, buf = self._make_svc()
        entity_id = "cover.living_room"
        now = self._prime_svc_for_reconcile(svc, entity_id, target=80, actual=50)

        with patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
            return_value={
                "has_set_position": True,
                "has_set_tilt_position": False,
                "has_open": True,
                "has_close": True,
                "has_stop": True,
            },
        ):
            await svc._reconcile(now)

        ev = next(e for e in _events(buf) if e["event"] == "reconcile_gave_up")
        assert ev["entity_id"] == entity_id
        assert ev["target_position"] == 80
        assert ev["max_retries"] == 2

    @pytest.mark.asyncio
    async def test_reconcile_gave_up_recorded_only_once(self) -> None:
        """gave_up is only logged once per target — buffer must not duplicate it."""
        from unittest.mock import patch

        svc, buf = self._make_svc()
        entity_id = "cover.test"
        now = self._prime_svc_for_reconcile(svc, entity_id, target=80, actual=50)

        with patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
            return_value={
                "has_set_position": True,
                "has_set_tilt_position": False,
                "has_open": True,
                "has_close": True,
                "has_stop": True,
            },
        ):
            # Three ticks — gave_up triggers on first, subsequent ticks are silent
            for _ in range(3):
                await svc._reconcile(now)

        gave_up_events = [e for e in _events(buf) if e["event"] == "reconcile_gave_up"]
        assert len(gave_up_events) == 1
