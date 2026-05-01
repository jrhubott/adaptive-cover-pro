"""Tests for Somfy "My" position support (Issue #199).

Covers:
- CoverCommandService.send_my_position()
- CoverCommandService._prepare_service_call() My-position routing
- build_special_positions() with my_position_value
- CustomPositionHandler.evaluate() use_my path
- DefaultHandler.evaluate() sunset_use_my path
- Regression: send_my_position works on stationary covers; stop_all still skips them
"""

from __future__ import annotations

from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
    build_special_positions,
)
from custom_components.adaptive_cover_pro.managers.manual_override import (
    AdaptiveCoverManager,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
    DefaultHandler,
)
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
)

from tests.test_pipeline.conftest import make_snapshot

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_hass():
    h = MagicMock()
    h.services.async_call = AsyncMock()
    return h


@pytest.fixture
def grace_mgr():
    return MagicMock()


@pytest.fixture
def svc(mock_hass, grace_mgr):
    return CoverCommandService(
        hass=mock_hass,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=grace_mgr,
        open_close_threshold=50,
        check_interval_minutes=1,
        position_tolerance=3,
        max_retries=3,
    )


def _patch_caps_my(*, has_set_position: bool = False, has_stop: bool = True):
    """Patch check_cover_features for My-position tests."""
    return patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
        return_value={
            "has_set_position": has_set_position,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
            "has_stop": has_stop,
        },
    )


def _stub_all_covers_state(mock_hass, state_str: str) -> None:
    state_obj = MagicMock()
    state_obj.state = state_str
    mock_hass.states.get.return_value = state_obj


# ---------------------------------------------------------------------------
# send_my_position — core behaviour
# ---------------------------------------------------------------------------


class TestSendMyPosition:
    """Unit tests for CoverCommandService.send_my_position()."""

    @pytest.mark.asyncio
    async def test_sends_stop_cover_service(self, svc, mock_hass):
        """send_my_position calls cover.stop_cover on the entity."""
        with _patch_caps_my(has_stop=True):
            result = await svc.send_my_position("cover.somfy", 35)

        assert result is True
        mock_hass.services.async_call.assert_called_once_with(
            "cover", "stop_cover", {"entity_id": "cover.somfy"}, context=ANY
        )

    @pytest.mark.asyncio
    async def test_sets_target_call(self, svc, mock_hass):
        """send_my_position records the target position in target_call."""
        with _patch_caps_my(has_stop=True):
            await svc.send_my_position("cover.somfy", 35)

        assert svc.target_call["cover.somfy"] == 35

    @pytest.mark.asyncio
    async def test_sets_wait_for_target_true(self, svc, mock_hass):
        """send_my_position sets wait_for_target=True for the entity."""
        with _patch_caps_my(has_stop=True):
            await svc.send_my_position("cover.somfy", 35)

        assert svc.wait_for_target["cover.somfy"] is True

    @pytest.mark.asyncio
    async def test_sets_sent_at(self, svc, mock_hass):
        """send_my_position records a non-None _sent_at timestamp."""
        with _patch_caps_my(has_stop=True):
            await svc.send_my_position("cover.somfy", 35)

        assert "cover.somfy" in svc._sent_at
        assert svc._sent_at["cover.somfy"] is not None

    @pytest.mark.asyncio
    async def test_resets_retry_counts(self, svc, mock_hass):
        """send_my_position clears _retry_counts for the entity."""
        svc._retry_counts["cover.somfy"] = 2
        with _patch_caps_my(has_stop=True):
            await svc.send_my_position("cover.somfy", 35)

        assert "cover.somfy" not in svc._retry_counts

    @pytest.mark.asyncio
    async def test_clears_gave_up(self, svc, mock_hass):
        """send_my_position removes the entity from _gave_up."""
        svc._gave_up.add("cover.somfy")
        with _patch_caps_my(has_stop=True):
            await svc.send_my_position("cover.somfy", 35)

        assert "cover.somfy" not in svc._gave_up

    @pytest.mark.asyncio
    async def test_dry_run_skips_async_call_but_returns_true(self, svc, mock_hass):
        """In dry-run mode, stop_cover is NOT called but send_my_position still returns True.

        Dry-run lets the full cycle (pipeline, diagnostics) run without hardware sends.
        target_call is still updated so downstream diagnostics reflect the decision.
        """
        svc._dry_run = True
        with _patch_caps_my(has_stop=True):
            result = await svc.send_my_position("cover.somfy", 35)

        assert result is True
        mock_hass.services.async_call.assert_not_called()
        # target_call is still set so reconciliation/diagnostics see the intent
        assert svc.target_call["cover.somfy"] == 35

    @pytest.mark.asyncio
    async def test_returns_false_when_has_stop_false(self, svc, mock_hass):
        """send_my_position returns False and does not call the service when has_stop=False."""
        with _patch_caps_my(has_stop=False):
            result = await svc.send_my_position("cover.somfy", 35)

        assert result is False
        mock_hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_works_on_stationary_cover(self, svc, mock_hass):
        """send_my_position sends stop_cover to a STATIONARY (closed) cover.

        This is intentional: unlike stop_in_flight which skips stationary covers to
        avoid accidentally triggering Somfy "My", send_my_position deliberately sends
        stop_cover while the cover is stationary — that is precisely what triggers the
        My preset on RTS motors.  _is_cover_in_motion() is NOT called in this path.
        """
        _stub_all_covers_state(mock_hass, "closed")  # stationary cover

        with _patch_caps_my(has_stop=True):
            result = await svc.send_my_position("cover.somfy", 35)

        # Must succeed and must have sent the stop_cover command
        assert result is True
        mock_hass.services.async_call.assert_called_once_with(
            "cover", "stop_cover", {"entity_id": "cover.somfy"}, context=ANY
        )


# ---------------------------------------------------------------------------
# _prepare_service_call — My-position routing
# ---------------------------------------------------------------------------


class TestPrepareServiceCallMyRouting:
    """Tests for the My-position branch inside _prepare_service_call."""

    def test_my_routing_non_position_capable_cover_with_stop(self, svc):
        """use_my_position=True + no has_set_position + has_stop → stop_cover returned."""
        caps = {
            "has_set_position": False,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
            "has_stop": True,
        }
        with patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
            return_value=caps,
        ):
            service, service_data, supports_position = svc._prepare_service_call(
                "cover.somfy", 35, use_my_position=True
            )

        assert service == "stop_cover"
        assert service_data == {"entity_id": "cover.somfy"}
        assert supports_position is False
        # target_call should record the My-position value
        assert svc.target_call["cover.somfy"] == 35

    def test_my_routing_skipped_when_position_capable(self, svc):
        """use_my_position=True but has_set_position=True → falls through to set_cover_position."""
        caps = {
            "has_set_position": True,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
            "has_stop": True,
        }
        with patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
            return_value=caps,
        ):
            service, service_data, supports_position = svc._prepare_service_call(
                "cover.somfy", 35, use_my_position=True
            )

        # Position-capable: My routing skipped; normal set_cover_position used
        assert service == "set_cover_position"
        assert supports_position is True

    def test_my_routing_skipped_when_has_stop_false(self, svc):
        """use_my_position=True but has_stop=False → falls through to open/close logic."""
        caps = {
            "has_set_position": False,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
            "has_stop": False,
        }
        with patch(
            "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
            return_value=caps,
        ):
            service, service_data, supports_position = svc._prepare_service_call(
                "cover.somfy", 80, use_my_position=True
            )

        # Fell through to open/close threshold logic (80 >= 50 → open_cover)
        assert service == "open_cover"
        assert supports_position is False


# ---------------------------------------------------------------------------
# build_special_positions
# ---------------------------------------------------------------------------


class TestBuildSpecialPositions:
    """Tests for build_special_positions()."""

    def test_my_position_value_included_when_set(self):
        """When my_position_value=35 is in options, 35 appears in the returned list."""
        result = build_special_positions({"my_position_value": 35})
        assert 35 in result

    def test_my_position_value_absent_when_none(self):
        """When my_position_value=None, it is NOT added to the list."""
        result = build_special_positions({"my_position_value": None})
        assert None not in result

    def test_my_position_value_absent_when_key_missing(self):
        """When my_position_value key is absent, default positions are unaffected."""
        result = build_special_positions({})
        assert 35 not in result  # no accidental inclusion of 35

    def test_zero_and_hundred_always_present(self):
        """0 and 100 are always in the list regardless of options."""
        assert 0 in build_special_positions({})
        assert 100 in build_special_positions({})
        assert 0 in build_special_positions({"my_position_value": 35})
        assert 100 in build_special_positions({"my_position_value": 35})


# ---------------------------------------------------------------------------
# CustomPositionHandler — use_my path
# ---------------------------------------------------------------------------

_ENTITY = "binary_sensor.scene_a"
_DEFAULT_PRIORITY = 77


def _snapshot_custom(
    *,
    entity_id: str = _ENTITY,
    is_on: bool,
    position: int = 50,
    priority: int = _DEFAULT_PRIORITY,
    use_my: bool = False,
    my_position_value: int | None = None,
):
    return make_snapshot(
        custom_position_sensors=[
            CustomPositionSensorState(
                entity_id=entity_id,
                is_on=is_on,
                position=position,
                priority=priority,
                min_mode=False,
                use_my=use_my,
            )
        ],
        my_position_value=my_position_value,
    )


class TestCustomPositionHandlerUseMy:
    """CustomPositionHandler.evaluate() with use_my flag."""

    def _handler(self, position: int = 50) -> CustomPositionHandler:
        return CustomPositionHandler(
            slot=1,
            entity_id=_ENTITY,
            position=position,
            priority=_DEFAULT_PRIORITY,
        )

    def test_use_my_true_with_value_returns_my_position(self):
        """Sensor on + use_my=True + my_position_value set → result.position == my_position_value, use_my_position==True."""
        snap = _snapshot_custom(
            is_on=True, position=50, use_my=True, my_position_value=30
        )
        result = self._handler(position=50).evaluate(snap)

        assert result is not None
        assert result.position == 30
        assert result.use_my_position is True

    def test_use_my_true_with_value_none_falls_back_to_slot_position(self):
        """Sensor on + use_my=True + my_position_value=None → falls back to slot position, use_my_position==False."""
        snap = _snapshot_custom(
            is_on=True, position=50, use_my=True, my_position_value=None
        )
        result = self._handler(position=50).evaluate(snap)

        assert result is not None
        assert result.position == 50
        assert result.use_my_position is False

    def test_use_my_false_normal_behavior(self):
        """Sensor on + use_my=False → slot's numeric position returned (existing behavior)."""
        snap = _snapshot_custom(
            is_on=True, position=45, use_my=False, my_position_value=30
        )
        result = self._handler(position=45).evaluate(snap)

        assert result is not None
        assert result.position == 45
        assert result.use_my_position is False

    def test_use_my_true_sensor_off_returns_none(self):
        """Sensor off + use_my=True → None (sensor not active, handler passes through)."""
        snap = _snapshot_custom(
            is_on=False, position=50, use_my=True, my_position_value=30
        )
        result = self._handler(position=50).evaluate(snap)

        assert result is None


# ---------------------------------------------------------------------------
# DefaultHandler — sunset_use_my path
# ---------------------------------------------------------------------------


def _snapshot_default(
    *,
    is_sunset_active: bool = False,
    sunset_use_my: bool = False,
    my_position_value: int | None = None,
    default_position: int = 0,
):
    snap = make_snapshot(
        is_sunset_active=is_sunset_active,
        default_position=default_position,
        my_position_value=my_position_value,
        sunset_use_my=sunset_use_my,
    )
    return snap


class TestDefaultHandlerSunsetUseMy:
    """DefaultHandler.evaluate() with sunset_use_my flag."""

    _handler = DefaultHandler()

    def test_sunset_active_use_my_true_with_value_returns_my(self):
        """is_sunset_active + sunset_use_my + my_position_value → position==my_position_value, use_my_position==True."""
        snap = _snapshot_default(
            is_sunset_active=True, sunset_use_my=True, my_position_value=25
        )
        result = self._handler.evaluate(snap)

        assert result.position == 25
        assert result.use_my_position is True

    def test_sunset_active_use_my_true_value_none_normal_behavior(self):
        """is_sunset_active + sunset_use_my + my_position_value=None → normal default, use_my_position==False."""
        snap = _snapshot_default(
            is_sunset_active=True,
            sunset_use_my=True,
            my_position_value=None,
            default_position=10,
        )
        result = self._handler.evaluate(snap)

        assert result.use_my_position is False
        # Should still emit a result (the default/sunset position)
        assert result is not None

    def test_sunset_inactive_use_my_true_normal_behavior(self):
        """is_sunset_active=False + sunset_use_my=True → My path not taken (not sunset)."""
        snap = _snapshot_default(
            is_sunset_active=False,
            sunset_use_my=True,
            my_position_value=25,
            default_position=5,
        )
        result = self._handler.evaluate(snap)

        assert result.use_my_position is False

    def test_sunset_active_use_my_false_normal_behavior(self):
        """is_sunset_active=True + sunset_use_my=False → My path not taken (opt-in required)."""
        snap = _snapshot_default(
            is_sunset_active=True,
            sunset_use_my=False,
            my_position_value=25,
            default_position=10,
        )
        result = self._handler.evaluate(snap)

        assert result.use_my_position is False


# ---------------------------------------------------------------------------
# Regression — PR #198 still intact
# send_my_position vs stop_all must have opposite stationary-cover behaviour
# ---------------------------------------------------------------------------


class TestStationaryCoverRegression:
    """Regression: send_my_position fires on stationary covers; stop_all does not."""

    @pytest.mark.asyncio
    async def test_send_my_position_fires_on_stationary_cover(self, svc, mock_hass):
        """send_my_position sends stop_cover to a stationary (closed) cover.

        Unlike stop_in_flight/stop_all — which deliberately skip stationary covers to
        avoid triggering "My" accidentally — send_my_position has the opposite intent:
        it IS trying to trigger My, so the stationary-cover gate must NOT apply here.
        """
        _stub_all_covers_state(mock_hass, "closed")  # definitely stationary

        with _patch_caps_my(has_stop=True):
            result = await svc.send_my_position("cover.awning", 35)

        assert result is True
        # stop_cover was sent even though the cover is stationary
        mock_hass.services.async_call.assert_called_once_with(
            "cover", "stop_cover", {"entity_id": "cover.awning"}, context=ANY
        )

    @pytest.mark.asyncio
    async def test_stop_all_skips_stationary_cover(self, svc, mock_hass):
        """stop_all does NOT send stop_cover to a stationary (closed) cover.

        This is the PR #198 regression guard: emergency stop must not accidentally
        trigger the "My" preset on a Somfy cover that is already at rest.
        """
        _stub_all_covers_state(mock_hass, "closed")  # stationary

        with _patch_caps_my(has_stop=True):
            stopped = await svc.stop_all(["cover.awning"])

        assert stopped == []
        mock_hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_two_behaviors_coexist(self, svc, mock_hass):
        """send_my_position and stop_all coexist correctly: same cover, opposite results when stationary."""
        _stub_all_covers_state(mock_hass, "closed")

        # stop_all: stationary → skipped
        with _patch_caps_my(has_stop=True):
            stopped = await svc.stop_all(["cover.awning"])
        assert stopped == []
        mock_hass.services.async_call.assert_not_called()

        # send_my_position: stationary → fires
        with _patch_caps_my(has_stop=True):
            result = await svc.send_my_position("cover.awning", 35)
        assert result is True
        mock_hass.services.async_call.assert_called_once_with(
            "cover", "stop_cover", {"entity_id": "cover.awning"}, context=ANY
        )


# ---------------------------------------------------------------------------
# Context tracking — _call_stop_cover records ACP-originated contexts
# ---------------------------------------------------------------------------


class TestAcpStopContextTracking:
    """_call_stop_cover records context ids so the service-call listener
    can distinguish ACP-originated stops from user-initiated ones.
    """

    @pytest.mark.asyncio
    async def test_send_my_position_records_context(self, svc, mock_hass):
        """send_my_position adds a context id to _acp_stop_contexts."""
        assert len(svc._acp_stop_contexts) == 0
        with _patch_caps_my(has_stop=True):
            await svc.send_my_position("cover.somfy", 50)
        assert len(svc._acp_stop_contexts) == 1

    @pytest.mark.asyncio
    async def test_stop_all_records_context(self, svc, mock_hass):
        """stop_all records context ids for in-flight covers."""
        _stub_all_covers_state(mock_hass, "opening")
        with _patch_caps_my(has_stop=True):
            await svc.stop_all(["cover.somfy"])
        assert len(svc._acp_stop_contexts) == 1

    @pytest.mark.asyncio
    async def test_stop_in_flight_records_context(self, svc, mock_hass):
        """stop_in_flight records context ids for in-flight covers."""
        svc.wait_for_target["cover.somfy"] = True
        _stub_all_covers_state(mock_hass, "opening")
        with _patch_caps_my(has_stop=True):
            await svc.stop_in_flight({"cover.somfy"})
        assert len(svc._acp_stop_contexts) == 1

    @pytest.mark.asyncio
    async def test_context_ids_are_unique(self, svc, mock_hass):
        """Each stop_cover call gets a distinct context id."""
        _stub_all_covers_state(mock_hass, "opening")
        with _patch_caps_my(has_stop=True):
            await svc.stop_all(["cover.somfy"])
            await svc.stop_all(["cover.somfy"])
        assert len(svc._acp_stop_contexts) == 2
        assert len(set(svc._acp_stop_contexts)) == 2

    @pytest.mark.asyncio
    async def test_context_deque_bounded(self, svc, mock_hass):
        """_acp_stop_contexts deque is capped at 16 entries."""
        _stub_all_covers_state(mock_hass, "opening")
        with _patch_caps_my(has_stop=True):
            for _ in range(20):
                await svc.stop_all(["cover.somfy"])
        assert len(svc._acp_stop_contexts) == 16


# ---------------------------------------------------------------------------
# handle_stop_service_call — manual override manager
# ---------------------------------------------------------------------------


class TestHandleStopServiceCall:
    """AdaptiveCoverManager.handle_stop_service_call() marks manual override
    when a user-initiated cover.stop_cover is detected.
    """

    @pytest.fixture
    def mgr(self, mock_hass):
        from custom_components.adaptive_cover_pro.diagnostics.event_buffer import (
            EventBuffer,
        )

        event_buffer = EventBuffer(maxlen=50)
        m = AdaptiveCoverManager(
            hass=mock_hass,
            reset_duration={"minutes": 15},
            logger=MagicMock(),
            event_buffer=event_buffer,
        )
        m.add_covers(["cover.somfy"])
        m._test_event_buffer = event_buffer
        return m

    def test_marks_manual_control(self, mgr):
        """handle_stop_service_call sets manual_control[entity_id] = True."""
        mgr.handle_stop_service_call("cover.somfy", 50, {})
        assert mgr.manual_control.get("cover.somfy") is True

    def test_records_event(self, mgr):
        """handle_stop_service_call appends a 'manual_override_set' event to the ring buffer."""
        mgr.handle_stop_service_call("cover.somfy", 50, {})
        events = mgr._test_event_buffer.snapshot()
        assert len(events) == 1
        assert events[0]["event"] == "manual_override_set"
        assert "My position" in events[0]["reason"]

    def test_noop_when_entity_not_tracked(self, mgr):
        """handle_stop_service_call ignores entities not in self.covers."""
        mgr.handle_stop_service_call("cover.unknown", 50, {})
        assert mgr.manual_control.get("cover.unknown") is None

    def test_noop_when_wait_for_target_active(self, mgr):
        """handle_stop_service_call skips when wait_for_target is True (ACP command in flight)."""
        mgr.handle_stop_service_call("cover.somfy", 50, {"cover.somfy": True})
        assert mgr.manual_control.get("cover.somfy") is None

    def test_sets_manual_control_time(self, mgr):
        """handle_stop_service_call records a timestamp for override duration tracking."""
        mgr.handle_stop_service_call("cover.somfy", 50, {})
        assert "cover.somfy" in mgr.manual_control_time

    def test_no_crash_when_entity_id_wrong_type(self, mgr):
        """handle_stop_service_call is tolerant of unexpected entity ids."""
        mgr.handle_stop_service_call("cover.other_entity", 50, {})  # not in covers


# ---------------------------------------------------------------------------
# async_check_cover_service_call — coordinator-level integration
# ---------------------------------------------------------------------------


class TestCoverServiceCallHandler:
    """Integration test for coordinator.async_check_cover_service_call.

    Uses a minimal stub coordinator to verify end-to-end: EVENT_CALL_SERVICE
    fires → manual override is set (or not) based on context id and config.
    """

    def _make_event(self, entity_id, context_id=None):
        """Build a mock EVENT_CALL_SERVICE event for cover.stop_cover."""
        from homeassistant.core import Context

        ctx = Context(id=context_id) if context_id else Context()
        event = MagicMock()
        event.data = {
            "domain": "cover",
            "service": "stop_cover",
            "service_data": {"entity_id": entity_id},
        }
        event.context = ctx
        return event

    @pytest.mark.asyncio
    async def test_user_stop_sets_manual_override(self, mock_hass):
        """A user stop_cover event (context not in ACP set) sets manual override."""
        from custom_components.adaptive_cover_pro.managers.manual_override import (
            AdaptiveCoverManager,
        )

        mgr = AdaptiveCoverManager(
            hass=mock_hass,
            reset_duration={"minutes": 15},
            logger=MagicMock(),
        )
        mgr.add_covers(["cover.somfy"])

        # Build a minimal coordinator stub
        coord = MagicMock()
        coord.manual_toggle = True
        coord.automatic_control = True
        coord.entities = ["cover.somfy"]
        coord.wait_for_target = {}
        coord.manager = mgr
        coord.config_entry.options = {"my_position_value": 50}
        coord._cmd_svc._acp_stop_contexts = []
        coord.logger = MagicMock()
        coord._cmd_svc.target_call = {}

        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        event = self._make_event("cover.somfy")
        await AdaptiveDataUpdateCoordinator.async_check_cover_service_call(coord, event)

        assert mgr.manual_control.get("cover.somfy") is True
        assert coord._cmd_svc.target_call.get("cover.somfy") == 50

    @pytest.mark.asyncio
    async def test_acp_originated_stop_does_not_set_override(self, mock_hass):
        """A stop_cover with ACP's own context id does not flag manual override."""
        from custom_components.adaptive_cover_pro.managers.manual_override import (
            AdaptiveCoverManager,
        )

        mgr = AdaptiveCoverManager(
            hass=mock_hass,
            reset_duration={"minutes": 15},
            logger=MagicMock(),
        )
        mgr.add_covers(["cover.somfy"])

        acp_ctx_id = "acp-ctx-001"
        coord = MagicMock()
        coord.manual_toggle = True
        coord.automatic_control = True
        coord.entities = ["cover.somfy"]
        coord.wait_for_target = {}
        coord.manager = mgr
        coord.config_entry.options = {"my_position_value": 50}
        coord._cmd_svc._acp_stop_contexts = [acp_ctx_id]
        coord.logger = MagicMock()
        coord._cmd_svc.target_call = {}

        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        event = self._make_event("cover.somfy", context_id=acp_ctx_id)
        await AdaptiveDataUpdateCoordinator.async_check_cover_service_call(coord, event)

        assert mgr.manual_control.get("cover.somfy") is None

    @pytest.mark.asyncio
    async def test_no_my_position_value_skips_override(self, mock_hass):
        """Without my_position_value configured, stop_cover event is a no-op."""
        from custom_components.adaptive_cover_pro.managers.manual_override import (
            AdaptiveCoverManager,
        )

        mgr = AdaptiveCoverManager(
            hass=mock_hass,
            reset_duration={"minutes": 15},
            logger=MagicMock(),
        )
        mgr.add_covers(["cover.somfy"])

        coord = MagicMock()
        coord.manual_toggle = True
        coord.automatic_control = True
        coord.entities = ["cover.somfy"]
        coord.wait_for_target = {}
        coord.manager = mgr
        coord.config_entry.options = {}  # no my_position_value
        coord._cmd_svc._acp_stop_contexts = []
        coord.logger = MagicMock()
        coord._cmd_svc.target_call = {}

        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        event = self._make_event("cover.somfy")
        await AdaptiveDataUpdateCoordinator.async_check_cover_service_call(coord, event)

        assert mgr.manual_control.get("cover.somfy") is None

    @pytest.mark.asyncio
    async def test_non_stop_service_is_ignored(self, mock_hass):
        """Events for services other than stop_cover are ignored."""
        mgr = AdaptiveCoverManager(
            hass=mock_hass,
            reset_duration={"minutes": 15},
            logger=MagicMock(),
        )
        mgr.add_covers(["cover.somfy"])

        coord = MagicMock()
        coord.manual_toggle = True
        coord.automatic_control = True
        coord.entities = ["cover.somfy"]
        coord.wait_for_target = {}
        coord.manager = mgr
        coord.config_entry.options = {"my_position_value": 50}
        coord._cmd_svc._acp_stop_contexts = []
        coord.logger = MagicMock()
        coord._cmd_svc.target_call = {}

        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        # Simulate a open_cover service event — should be a no-op
        event = MagicMock()
        event.data = {
            "domain": "cover",
            "service": "open_cover",
            "service_data": {"entity_id": "cover.somfy"},
        }
        event.context = MagicMock()
        event.context.id = "some-id"
        await AdaptiveDataUpdateCoordinator.async_check_cover_service_call(coord, event)
        assert mgr.manual_control.get("cover.somfy") is None

    @pytest.mark.asyncio
    async def test_list_entity_id_in_service_data(self, mock_hass):
        """entity_id as a list in service_data is handled correctly."""
        mgr = AdaptiveCoverManager(
            hass=mock_hass,
            reset_duration={"minutes": 15},
            logger=MagicMock(),
        )
        mgr.add_covers(["cover.somfy", "cover.other"])

        coord = MagicMock()
        coord.manual_toggle = True
        coord.automatic_control = True
        coord.entities = ["cover.somfy", "cover.other"]
        coord.wait_for_target = {}
        coord.manager = mgr
        coord.config_entry.options = {"my_position_value": 60}
        coord._cmd_svc._acp_stop_contexts = []
        coord.logger = MagicMock()
        coord._cmd_svc.target_call = {}

        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        event = MagicMock()
        event.data = {
            "domain": "cover",
            "service": "stop_cover",
            "service_data": {"entity_id": ["cover.somfy", "cover.other"]},
        }
        from homeassistant.core import Context

        event.context = Context()
        await AdaptiveDataUpdateCoordinator.async_check_cover_service_call(coord, event)

        assert mgr.manual_control.get("cover.somfy") is True
        assert mgr.manual_control.get("cover.other") is True
        assert coord._cmd_svc.target_call.get("cover.somfy") == 60
        assert coord._cmd_svc.target_call.get("cover.other") == 60
