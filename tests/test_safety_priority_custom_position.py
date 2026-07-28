"""Parity tests for the priority-100 (safety) custom position slot.

Issue #563 merged the force-override feature into the custom positions
system: a slot configured at ``CUSTOM_POSITION_SAFETY_PRIORITY`` (100)
inherits the full force-override semantics.  These tests preserve the old
force-override sensor scenarios:

- no sensors configured → slot inactive
- one active sensor of several → slot active (OR logic)
- all sensors off / unavailable / missing → slot inactive
- release transition: sensor flips off → lower-priority handler wins again
- priority 100 beats manual override
- reason/diagnostics reflect the active sensors
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.const import (
    CUSTOM_POSITION_SAFETY_PRIORITY,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
    ControlMethod,
)
from custom_components.adaptive_cover_pro.managers.cover_command import (
    CoverCommandService,
    PositionContext,
)
from custom_components.adaptive_cover_pro.pipeline.handlers import (
    CustomPositionHandler,
    DefaultHandler,
    ManualOverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.snapshot_builder import (
    PipelineSnapshotBuilder,
)
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
)

from tests.test_pipeline.conftest import make_snapshot

_SLOT = 5

# Slot-5 options for a safety-priority custom position (two trigger sensors).
_SAFETY_OPTIONS = {
    f"custom_position_sensors_{_SLOT}": [
        "binary_sensor.rain",
        "binary_sensor.wind",
    ],
    f"custom_position_{_SLOT}": 90,
    f"custom_position_priority_{_SLOT}": CUSTOM_POSITION_SAFETY_PRIORITY,
}


def _make_builder(mock_hass) -> PipelineSnapshotBuilder:
    """Snapshot builder bound to the mock hass — the real sensor-read surface."""
    return PipelineSnapshotBuilder(
        hass=mock_hass,
        logger=MagicMock(),
        climate_provider=MagicMock(),
        toggles=MagicMock(),
        policy=MagicMock(),
        config_service=MagicMock(),
    )


def _set_sensor_states(mock_hass, states: dict[str, str | None]) -> None:
    """Wire mock_hass.states.get to return the given per-entity states."""

    def get_state(entity_id):
        value = states.get(entity_id)
        if value is None:
            return None
        state_obj = MagicMock()
        state_obj.state = value
        state_obj.attributes = {}
        return state_obj

    mock_hass.states.get.side_effect = get_state


def _safety_state(
    is_on: bool,
    *,
    position: int = 90,
    active: tuple[str, ...] = (),
) -> CustomPositionSensorState:
    """Pre-built slot-5 safety state for pipeline-level tests."""
    return CustomPositionSensorState(
        entity_ids=("binary_sensor.rain", "binary_sensor.wind"),
        is_on=is_on,
        position=position,
        priority=CUSTOM_POSITION_SAFETY_PRIORITY,
        min_mode=False,
        use_my=False,
        slot=_SLOT,
        active_entity_ids=active,
    )


def _safety_registry(position: int = 90) -> PipelineRegistry:
    return PipelineRegistry(
        [
            CustomPositionHandler(
                slot=_SLOT,
                position=position,
                priority=CUSTOM_POSITION_SAFETY_PRIORITY,
            ),
            ManualOverrideHandler(),
            DefaultHandler(),
        ]
    )


def _non_safety_state(
    is_on: bool,
    *,
    position: int = 90,
    active: tuple[str, ...] = (),
    use_my: bool = False,
) -> CustomPositionSensorState:
    """Pre-built slot-5 state at the default (non-safety) priority 77."""
    return CustomPositionSensorState(
        entity_ids=("binary_sensor.rain", "binary_sensor.wind"),
        is_on=is_on,
        position=position,
        priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
        min_mode=False,
        use_my=use_my,
        slot=_SLOT,
        active_entity_ids=active,
    )


def _non_safety_registry(position: int = 90) -> PipelineRegistry:
    return PipelineRegistry(
        [
            CustomPositionHandler(
                slot=_SLOT,
                position=position,
                priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
            ),
            ManualOverrideHandler(),
            DefaultHandler(),
        ]
    )


def _command_service() -> tuple[CoverCommandService, MagicMock]:
    """Build a real CoverCommandService over a mock hass (mirrors the force-gate suite)."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    svc = CoverCommandService(
        hass=hass,
        logger=MagicMock(),
        cover_type="cover_blind",
        grace_mgr=MagicMock(),
        open_close_threshold=50,
    )
    svc._enabled = True
    return svc, hass


def _gate_context(result, *, auto_control: bool = False) -> PositionContext:
    """PositionContext mirroring how the coordinator forwards a pipeline result.

    The two auto-control bypass channels (is_safety / bypass_auto_control) are
    sourced straight from the pipeline result so the service gate sees exactly
    what the handler produced.
    """
    return PositionContext(
        auto_control=auto_control,
        manual_override=False,
        sun_just_appeared=False,
        min_change=1,
        time_threshold=0,
        special_positions=[0, 100],
        force=True,
        is_safety=result.is_safety,
        bypass_auto_control=result.bypass_auto_control,
    )


def _patch_caps():
    return patch(
        "custom_components.adaptive_cover_pro.managers.cover_command.check_cover_features",
        return_value={
            "has_set_position": True,
            "has_set_tilt_position": False,
            "has_open": True,
            "has_close": True,
            "has_stop": True,
        },
    )


# ---------------------------------------------------------------------------
# Snapshot builder: sensor OR logic (old is_force_override_active scenarios)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_sensors_slot_not_read(mock_hass):
    """No sensors configured → the slot does not participate at all."""
    builder = _make_builder(mock_hass)
    result = builder.read_custom_position_sensors({})
    assert result == []


@pytest.mark.unit
def test_single_sensor_active_slot_on(mock_hass):
    """One sensor on → slot is_on=True with that sensor in active_entity_ids."""
    _set_sensor_states(
        mock_hass, {"binary_sensor.rain": "on", "binary_sensor.wind": "off"}
    )
    builder = _make_builder(mock_hass)

    (state,) = builder.read_custom_position_sensors(_SAFETY_OPTIONS)

    assert state.is_on is True
    assert state.active_entity_ids == ("binary_sensor.rain",)
    assert state.priority == CUSTOM_POSITION_SAFETY_PRIORITY
    assert state.slot == _SLOT


@pytest.mark.unit
def test_multiple_sensors_or_logic(mock_hass):
    """OR across the sensor list — any single on sensor activates the slot."""
    _set_sensor_states(
        mock_hass, {"binary_sensor.rain": "off", "binary_sensor.wind": "on"}
    )
    builder = _make_builder(mock_hass)

    (state,) = builder.read_custom_position_sensors(_SAFETY_OPTIONS)

    assert state.is_on is True
    assert state.active_entity_ids == ("binary_sensor.wind",)


@pytest.mark.unit
def test_all_sensors_off_slot_inactive(mock_hass):
    """All sensors off → slot inactive."""
    _set_sensor_states(
        mock_hass, {"binary_sensor.rain": "off", "binary_sensor.wind": "off"}
    )
    builder = _make_builder(mock_hass)

    (state,) = builder.read_custom_position_sensors(_SAFETY_OPTIONS)

    assert state.is_on is False
    assert state.active_entity_ids == ()


@pytest.mark.unit
def test_unavailable_sensor_treated_as_inactive(mock_hass):
    """'unavailable' state is not 'on' — slot stays inactive."""
    _set_sensor_states(
        mock_hass,
        {"binary_sensor.rain": "unavailable", "binary_sensor.wind": "off"},
    )
    builder = _make_builder(mock_hass)

    (state,) = builder.read_custom_position_sensors(_SAFETY_OPTIONS)

    assert state.is_on is False


@pytest.mark.unit
def test_missing_entity_treated_as_inactive(mock_hass):
    """Entities that do not exist in HA are treated as inactive."""
    _set_sensor_states(mock_hass, {})  # states.get returns None for everything
    builder = _make_builder(mock_hass)

    (state,) = builder.read_custom_position_sensors(_SAFETY_OPTIONS)

    assert state.is_on is False


@pytest.mark.unit
def test_legacy_single_sensor_key_fallback(mock_hass):
    """The legacy custom_position_sensor_N key still drives the slot."""
    _set_sensor_states(mock_hass, {"binary_sensor.rain": "on"})
    builder = _make_builder(mock_hass)
    options = {
        f"custom_position_sensor_{_SLOT}": "binary_sensor.rain",
        f"custom_position_{_SLOT}": 90,
        f"custom_position_priority_{_SLOT}": CUSTOM_POSITION_SAFETY_PRIORITY,
    }

    (state,) = builder.read_custom_position_sensors(options)

    assert state.entity_ids == ("binary_sensor.rain",)
    assert state.is_on is True


# ---------------------------------------------------------------------------
# Pipeline precedence (old FORCE_OVERRIDE_ACTIVE precedence scenarios)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_safety_slot_beats_manual_override():
    """Priority 100 beats manual override (80) — force-override parity."""
    registry = _safety_registry(position=90)
    snapshot = make_snapshot(
        manual_override_active=True,
        custom_position_sensors=[_safety_state(True, active=("binary_sensor.rain",))],
    )

    result = registry.evaluate(snapshot)

    assert result.control_method == ControlMethod.CUSTOM_POSITION
    assert result.is_safety is True
    assert result.bypass_auto_control is True
    assert result.position == 90


@pytest.mark.unit
def test_manual_override_wins_when_slot_inactive():
    """When the safety slot is off, manual override takes over again."""
    registry = _safety_registry()
    snapshot = make_snapshot(
        manual_override_active=True,
        custom_position_sensors=[_safety_state(False)],
    )

    result = registry.evaluate(snapshot)

    assert result.control_method == ControlMethod.MANUAL
    assert result.is_safety is False


@pytest.mark.unit
def test_release_transition_returns_to_default():
    """Sensor on → off release: the next evaluation hands control back.

    The coordinator-level release edge (force=True, reason
    'custom_position_released', outside-window bypass) is covered in
    test_coordinator_integration.py; this locks the pipeline-level handover.
    """
    registry = _safety_registry(position=90)

    active = registry.evaluate(
        make_snapshot(
            default_position=50,
            custom_position_sensors=[
                _safety_state(True, active=("binary_sensor.rain",))
            ],
        )
    )
    released = registry.evaluate(
        make_snapshot(
            default_position=50,
            custom_position_sensors=[_safety_state(False)],
        )
    )

    assert active.control_method == ControlMethod.CUSTOM_POSITION
    assert active.position == 90
    assert released.control_method == ControlMethod.DEFAULT
    assert released.position == 50
    assert released.is_safety is False


# ---------------------------------------------------------------------------
# Reason / diagnostics reflect the active sensors
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reason_lists_active_sensors():
    """Reason names every active sensor (old force-override reason format)."""
    registry = _safety_registry(position=90)
    snapshot = make_snapshot(
        custom_position_sensors=[
            _safety_state(True, active=("binary_sensor.rain", "binary_sensor.wind"))
        ],
    )

    result = registry.evaluate(snapshot)

    assert "binary_sensor.rain" in result.reason
    assert "binary_sensor.wind" in result.reason
    assert "[bypasses automatic control]" in result.reason


@pytest.mark.unit
def test_decision_trace_names_safety_slot():
    """The winning decision-trace step is custom_position_5."""
    registry = _safety_registry()
    snapshot = make_snapshot(
        custom_position_sensors=[_safety_state(True, active=("binary_sensor.rain",))],
    )

    result = registry.evaluate(snapshot)

    matched = [s for s in result.decision_trace if s.matched]
    assert len(matched) == 1
    assert matched[0].handler == f"custom_position_{_SLOT}"


# ---------------------------------------------------------------------------
# Issue #767: only the priority-100 safety slot bypasses Automatic-Control-OFF
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_non_safety_slot_does_not_bypass_auto_control():
    """A default-priority (77) slot wins but must NOT bypass automatic control."""
    registry = _non_safety_registry(position=90)
    snapshot = make_snapshot(
        custom_position_sensors=[
            _non_safety_state(True, active=("binary_sensor.rain",))
        ],
    )

    result = registry.evaluate(snapshot)

    assert result.control_method == ControlMethod.CUSTOM_POSITION
    assert result.position == 90
    assert result.is_safety is False
    assert result.bypass_auto_control is False
    assert "[bypasses automatic control]" not in result.reason


@pytest.mark.unit
def test_use_my_non_safety_slot_does_not_bypass_auto_control():
    """The use-My branch of a non-safety slot must also respect automatic control."""
    registry = _non_safety_registry(position=90)
    snapshot = make_snapshot(
        custom_position_sensors=[
            _non_safety_state(True, use_my=True, active=("binary_sensor.rain",))
        ],
        my_position_value=42,
    )

    result = registry.evaluate(snapshot)

    assert result.use_my_position is True
    assert result.is_safety is False
    assert result.bypass_auto_control is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_safety_slot_skipped_by_auto_control_gate():
    """End-to-end: a non-safety slot is blocked by the auto-control-off gate."""
    registry = _non_safety_registry(position=90)
    snapshot = make_snapshot(
        custom_position_sensors=[
            _non_safety_state(True, active=("binary_sensor.rain",))
        ],
    )
    result = registry.evaluate(snapshot)

    svc, hass = _command_service()
    with _patch_caps():
        outcome, detail = await svc.apply_position(
            "cover.test",
            result.position,
            "custom_position",
            _gate_context(result),
        )

    assert outcome == "skipped"
    assert detail == "auto_control_off"
    hass.services.async_call.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_safety_slot_still_bypasses_auto_control_gate():
    """Regression guard: the priority-100 safety slot still bypasses the gate."""
    registry = _safety_registry(position=0)
    snapshot = make_snapshot(
        custom_position_sensors=[
            _safety_state(True, position=0, active=("binary_sensor.rain",))
        ],
    )
    result = registry.evaluate(snapshot)
    assert result.bypass_auto_control is True

    svc, hass = _command_service()
    with _patch_caps(), patch.object(svc, "_get_current_position", return_value=50):
        outcome, _detail = await svc.apply_position(
            "cover.test",
            0,
            "custom_position",
            _gate_context(result),
        )

    assert outcome == "sent"
    hass.services.async_call.assert_awaited_once()


# ---------------------------------------------------------------------------
# Constants lock
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_safety_priority_constant():
    """The safety threshold is 100 — matches the old force-override priority."""
    assert CUSTOM_POSITION_SAFETY_PRIORITY == 100
