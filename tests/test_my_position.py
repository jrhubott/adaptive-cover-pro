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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
    build_special_positions,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
    DefaultHandler,
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
            "cover", "stop_cover", {"entity_id": "cover.somfy"}
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
            "cover", "stop_cover", {"entity_id": "cover.somfy"}
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
        custom_position_sensors=[(entity_id, is_on, position, priority, False, use_my)],
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
        snap = _snapshot_custom(is_on=True, position=50, use_my=True, my_position_value=30)
        result = self._handler(position=50).evaluate(snap)

        assert result is not None
        assert result.position == 30
        assert result.use_my_position is True

    def test_use_my_true_with_value_none_falls_back_to_slot_position(self):
        """Sensor on + use_my=True + my_position_value=None → falls back to slot position, use_my_position==False."""
        snap = _snapshot_custom(is_on=True, position=50, use_my=True, my_position_value=None)
        result = self._handler(position=50).evaluate(snap)

        assert result is not None
        assert result.position == 50
        assert result.use_my_position is False

    def test_use_my_false_normal_behavior(self):
        """Sensor on + use_my=False → slot's numeric position returned (existing behavior)."""
        snap = _snapshot_custom(is_on=True, position=45, use_my=False, my_position_value=30)
        result = self._handler(position=45).evaluate(snap)

        assert result is not None
        assert result.position == 45
        assert result.use_my_position is False

    def test_use_my_true_sensor_off_returns_none(self):
        """Sensor off + use_my=True → None (sensor not active, handler passes through)."""
        snap = _snapshot_custom(is_on=False, position=50, use_my=True, my_position_value=30)
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
            is_sunset_active=True, sunset_use_my=True, my_position_value=None, default_position=10
        )
        result = self._handler.evaluate(snap)

        assert result.use_my_position is False
        # Should still emit a result (the default/sunset position)
        assert result is not None

    def test_sunset_inactive_use_my_true_normal_behavior(self):
        """is_sunset_active=False + sunset_use_my=True → My path not taken (not sunset)."""
        snap = _snapshot_default(
            is_sunset_active=False, sunset_use_my=True, my_position_value=25, default_position=5
        )
        result = self._handler.evaluate(snap)

        assert result.use_my_position is False

    def test_sunset_active_use_my_false_normal_behavior(self):
        """is_sunset_active=True + sunset_use_my=False → My path not taken (opt-in required)."""
        snap = _snapshot_default(
            is_sunset_active=True, sunset_use_my=False, my_position_value=25, default_position=10
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
            "cover", "stop_cover", {"entity_id": "cover.awning"}
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
            "cover", "stop_cover", {"entity_id": "cover.awning"}
        )
