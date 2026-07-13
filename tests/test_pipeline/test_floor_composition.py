"""Tests for cross-handler floor-mode composition (issue #463).

The pipeline registry composes floor (min-mode) clamps from custom-position
and weather-override sources as a post-decision pass. A floor no longer wins
the priority chain — it raises the winner's position when the winner is below
the highest active floor. A priority-100 (safety) slot's floor composes
exactly like any other custom-position floor (issue #563).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.const import (
    CUSTOM_POSITION_SAFETY_PRIORITY,
    DEFAULT_CUSTOM_POSITION_PRIORITY,
)
from custom_components.adaptive_cover_pro.pipeline.handlers import (
    ClimateHandler,
    DefaultHandler,
    SolarHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.manual_override import (
    ManualOverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.weather import (
    WeatherOverrideHandler,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import (
    ClimateOptions,
    CustomPositionSensorState,
)
from custom_components.adaptive_cover_pro.state.climate_provider import (
    ClimateReadings,
)

from tests.test_pipeline.conftest import make_snapshot

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _climate_cover(
    *, direct_sun_valid: bool = True, calculate_percentage_return: float = 50.0
) -> MagicMock:
    """Build a mock cover usable by ClimateHandler (needs valid, logger)."""
    cover = MagicMock()
    cover.direct_sun_valid = direct_sun_valid
    cover.valid = direct_sun_valid
    cover.calculate_percentage = MagicMock(return_value=calculate_percentage_return)
    cover.logger = MagicMock()
    config = MagicMock()
    config.min_pos = None
    config.max_pos = None
    config.min_pos_sun_only = False
    config.max_pos_sun_only = False
    cover.config = config
    return cover


def _summer_readings(inside: float = 30.0) -> ClimateReadings:
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=inside,
        is_presence=False,  # no presence -> normal_without_presence -> SUMMER closes
        is_sunny=True,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        cloud_coverage_above_threshold=False,
    )


def _summer_options() -> ClimateOptions:
    return ClimateOptions(
        temp_low=18.0,
        temp_high=26.0,
        temp_switch=False,  # use inside temp
        transparent_blind=True,
        temp_summer_outside=None,
        cloud_suppression_enabled=False,
        winter_close_insulation=False,
    )


def _cp_state(
    entity_id: str,
    *,
    is_on: bool,
    position: int,
    min_mode: bool,
    sensor_name: str | None = None,
    use_my: bool = False,
    priority: int = DEFAULT_CUSTOM_POSITION_PRIORITY,
    slot: int = 1,
) -> CustomPositionSensorState:
    return CustomPositionSensorState(
        entity_ids=(entity_id,),
        is_on=is_on,
        position=position,
        priority=priority,
        min_mode=min_mode,
        use_my=use_my,
        sensor_name=sensor_name,
        slot=slot,
        active_entity_ids=(entity_id,) if is_on else (),
    )


def _cp_handler(
    slot: int,
    position: int,
    *,
    priority: int = DEFAULT_CUSTOM_POSITION_PRIORITY,
) -> CustomPositionHandler:
    return CustomPositionHandler(
        slot=slot,
        position=position,
        priority=priority,
    )


def _registry_with_custom(handlers: list) -> PipelineRegistry:
    return PipelineRegistry(
        [*handlers, ClimateHandler(), SolarHandler(), DefaultHandler()]
    )


# ---------------------------------------------------------------------------
# Composition tests
# ---------------------------------------------------------------------------


def test_custom_position_floor_clamps_climate_winner() -> None:
    """Climate winner at 30% is raised to 60% by an active custom floor."""
    cover = _climate_cover(direct_sun_valid=False)
    snap = make_snapshot(
        cover=cover,
        climate_mode_enabled=True,
        climate_readings=_summer_readings(),
        climate_options=_summer_options(),
        direct_sun_valid=False,
        custom_position_sensors=[
            _cp_state(
                "binary_sensor.cp1",
                is_on=True,
                position=60,
                min_mode=True,
                sensor_name="Table",
            )
        ],
    )
    handlers = [_cp_handler(1, 60)]
    registry = _registry_with_custom(handlers)
    # ClimateHandler in summer-without-presence returns position_for_intent
    # (sun_through=False) — for a blind that's 0; but with config min_pos=None
    # we get 0. Override calculate_percentage so SolarHandler isn't relevant
    # here: we explicitly check climate is the winner.
    result = registry.evaluate(snap)
    assert result.position == 60
    winner_step = next(
        s for s in result.decision_trace if s.matched and s.handler != "floor_clamp"
    )
    assert winner_step.handler == "climate"
    clamp_steps = [
        s for s in result.decision_trace if s.handler == "floor_clamp" and s.matched
    ]
    assert len(clamp_steps) == 1


def test_custom_position_floor_above_climate_is_inert() -> None:
    """Climate at 80% above floor of 60% — no clamp applied."""
    # We need climate to compute 80%. Easiest path: climate is GLARE_CONTROL
    # (defers) or LOW_LIGHT (returns default). Instead, use a Winter heating
    # path: position_for_intent(sun_through=True) -> 100 for blind. We pick
    # a configuration where the climate handler returns 80 directly via
    # is_sunny=False low-light path with default_position=80.
    cover = _climate_cover(direct_sun_valid=False)
    snap = make_snapshot(
        cover=cover,
        climate_mode_enabled=True,
        climate_readings=ClimateReadings(
            outside_temperature=None,
            inside_temperature=22.0,  # between low/high → not summer/winter
            is_presence=False,
            is_sunny=False,  # forces LOW_LIGHT → default_position
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        ),
        climate_options=_summer_options(),
        default_position=80,
        direct_sun_valid=False,
        custom_position_sensors=[
            _cp_state(
                "binary_sensor.cp1",
                is_on=True,
                position=60,
                min_mode=True,
                sensor_name="Table",
            )
        ],
    )
    handlers = [_cp_handler(1, 60)]
    registry = _registry_with_custom(handlers)
    result = registry.evaluate(snap)
    assert result.position == 80
    assert not any(
        s.handler == "floor_clamp" and s.matched for s in result.decision_trace
    )
    cp_step = next(s for s in result.decision_trace if s.handler == "custom_position_1")
    assert cp_step.matched is False


def test_floor_raised_step_carries_reason_payload_code() -> None:
    """The floor_clamp step carries a registry.floor_raised code + params,
    while its English reason stays byte-identical to the legacy f-string.
    """
    from custom_components.adaptive_cover_pro.const import ReasonCode

    cover = _climate_cover(direct_sun_valid=False)
    snap = make_snapshot(
        cover=cover,
        climate_mode_enabled=True,
        climate_readings=_summer_readings(),
        climate_options=_summer_options(),
        direct_sun_valid=False,
        custom_position_sensors=[
            _cp_state(
                "binary_sensor.cp1",
                is_on=True,
                position=60,
                min_mode=True,
                sensor_name="Table",
            )
        ],
    )
    registry = _registry_with_custom([_cp_handler(1, 60)])
    result = registry.evaluate(snap)
    clamp = next(
        s for s in result.decision_trace if s.handler == "floor_clamp" and s.matched
    )
    from_pos = clamp.reason_payload.params["from_pos"]
    assert clamp.reason_payload is not None
    assert clamp.reason_payload.code == ReasonCode.REGISTRY_FLOOR_RAISED
    assert clamp.reason_payload.params == {
        "from_pos": from_pos,
        "to_pos": 60,
        "label": "Table",
    }
    assert clamp.reason == f"floor raised winner from {from_pos}% to 60% by Table"


def test_floor_inactive_step_carries_reason_payload_code() -> None:
    """The inactive-floor step carries a registry.floor_inactive code + params,
    while its English reason stays byte-identical to the legacy f-string.
    """
    from custom_components.adaptive_cover_pro.const import ReasonCode

    cover = _climate_cover(direct_sun_valid=False)
    snap = make_snapshot(
        cover=cover,
        climate_mode_enabled=True,
        climate_readings=ClimateReadings(
            outside_temperature=None,
            inside_temperature=22.0,
            is_presence=False,
            is_sunny=False,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        ),
        climate_options=_summer_options(),
        default_position=80,
        direct_sun_valid=False,
        custom_position_sensors=[
            _cp_state(
                "binary_sensor.cp1",
                is_on=True,
                position=60,
                min_mode=True,
                sensor_name="Table",
            )
        ],
    )
    registry = _registry_with_custom([_cp_handler(1, 60)])
    result = registry.evaluate(snap)
    cp_step = next(s for s in result.decision_trace if s.handler == "custom_position_1")
    assert cp_step.reason_payload is not None
    assert cp_step.reason_payload.code == ReasonCode.REGISTRY_FLOOR_INACTIVE
    assert cp_step.reason_payload.params == {"floor_pos": 60, "winner_pos": 80}
    assert cp_step.reason == "floor 60% inactive (winner 80% above floor)"


def test_two_custom_floors_pick_highest() -> None:
    """Two active floors at 40 and 60 — winner clamped to max (60)."""
    cover = _climate_cover(direct_sun_valid=False)
    snap = make_snapshot(
        cover=cover,
        climate_mode_enabled=True,
        climate_readings=_summer_readings(),
        climate_options=_summer_options(),
        direct_sun_valid=False,
        custom_position_sensors=[
            _cp_state(
                "binary_sensor.cp1",
                is_on=True,
                position=40,
                min_mode=True,
                sensor_name="Floor40",
            ),
            _cp_state(
                "binary_sensor.cp2",
                is_on=True,
                position=60,
                min_mode=True,
                sensor_name="Floor60",
                slot=2,
            ),
        ],
    )
    handlers = [
        _cp_handler(1, 40),
        _cp_handler(2, 60),
    ]
    registry = _registry_with_custom(handlers)
    result = registry.evaluate(snap)
    assert result.position == 60
    clamp_steps = [
        s for s in result.decision_trace if s.handler == "floor_clamp" and s.matched
    ]
    assert len(clamp_steps) == 1
    assert "Floor60" in clamp_steps[0].reason


def test_gapped_custom_floor_slots_label_real_slot() -> None:
    """Gapped slots (#3/#4 active, #1/#2 empty) label the real slot, not list index.

    Mirrors the issue #496 reporter's config: only custom-position slots 3 and 4
    are configured. The compacted ``custom_position_sensors`` list has two
    entries, so an ``enumerate(start=1)`` index would mislabel them as
    ``custom_position_1`` / ``custom_position_2``. The trace must instead carry
    the real slot names and the registry dedup must remove the deferred handlers'
    "sensor not active" skip steps for both active slots.
    """
    cover = _climate_cover(direct_sun_valid=False)
    snap = make_snapshot(
        cover=cover,
        climate_mode_enabled=True,
        climate_readings=_summer_readings(),
        climate_options=_summer_options(),
        default_position=90,
        direct_sun_valid=False,
        # Compacted list in CUSTOM_POSITION_SLOTS order: slot 3 then slot 4.
        custom_position_sensors=[
            _cp_state(
                "binary_sensor.cp3",
                is_on=True,
                position=100,
                min_mode=True,
                sensor_name="Mode aeration",
                slot=3,
            ),
            _cp_state(
                "binary_sensor.cp4",
                is_on=True,
                position=80,
                min_mode=True,
                sensor_name="Mode shade",
                slot=4,
            ),
        ],
    )
    handlers = [
        _cp_handler(4, 80),
        _cp_handler(3, 100),
    ]
    registry = _registry_with_custom(handlers)
    result = registry.evaluate(snap)

    # (a) winner clamped to the highest floor (max(80, 100) = 100).
    assert result.position == 100

    # (b) the inactive 80% floor step is labelled custom_position_4 (never 1/2),
    #     and the matched floor_clamp references the slot-3 active source.
    inactive_floor_steps = [
        s
        for s in result.decision_trace
        if s.handler.startswith("custom_position_")
        and not s.matched
        and s.position == 80
    ]
    assert len(inactive_floor_steps) == 1
    assert inactive_floor_steps[0].handler == "custom_position_4"
    clamp_steps = [
        s for s in result.decision_trace if s.handler == "floor_clamp" and s.matched
    ]
    assert len(clamp_steps) == 1
    assert "Mode aeration" in clamp_steps[0].reason

    # (c) no "sensor not active" reason survives for an active slot.
    for s in result.decision_trace:
        if s.handler in ("custom_position_3", "custom_position_4"):
            assert "sensor not active" not in s.reason

    # (d) each real slot is represented exactly once and there are zero phantom
    #     slots 1/2. Slot 3 is the winning floor, so it surfaces only as the
    #     floor_clamp step (no redundant custom_position_3 entry — the registry
    #     dedup deliberately omits the winner's inactive step). Slot 4 is the
    #     non-winning floor, surfacing as exactly one custom_position_4 step.
    cp3_steps = [s for s in result.decision_trace if s.handler == "custom_position_3"]
    cp4_steps = [s for s in result.decision_trace if s.handler == "custom_position_4"]
    assert len(cp3_steps) == 0  # represented by floor_clamp instead
    assert len(cp4_steps) == 1
    assert "Mode aeration" in clamp_steps[0].reason  # slot 3 == the floor_clamp
    assert not any(
        s.handler in ("custom_position_1", "custom_position_2")
        for s in result.decision_trace
    )


def test_real_position_custom_slot_clamped_by_floor_slot() -> None:
    """A real-position custom slot (30) is clamped by another slot's floor (60)."""
    cover = _climate_cover(direct_sun_valid=False)
    snap = make_snapshot(
        cover=cover,
        direct_sun_valid=False,
        custom_position_sensors=[
            _cp_state(
                "binary_sensor.cp1",
                is_on=True,
                position=30,
                min_mode=False,  # real position
                sensor_name="Real",
            ),
            _cp_state(
                "binary_sensor.cp2",
                is_on=True,
                position=60,
                min_mode=True,  # floor
                sensor_name="Floor",
                slot=2,
            ),
        ],
    )
    handlers = [
        _cp_handler(1, 30),
        _cp_handler(2, 60),
    ]
    registry = _registry_with_custom(handlers)
    result = registry.evaluate(snap)
    assert result.position == 60
    winner_step = next(
        s for s in result.decision_trace if s.matched and s.handler != "floor_clamp"
    )
    assert winner_step.handler == "custom_position_1"
    clamp_steps = [
        s for s in result.decision_trace if s.handler == "floor_clamp" and s.matched
    ]
    assert len(clamp_steps) == 1
    assert "Floor" in clamp_steps[0].reason


def test_weather_override_min_mode_clamps_climate() -> None:
    """Weather override floor 60% clamps climate winner at 30%."""
    cover = _climate_cover(direct_sun_valid=False)
    snap = make_snapshot(
        cover=cover,
        climate_mode_enabled=True,
        climate_readings=_summer_readings(),
        climate_options=_summer_options(),
        direct_sun_valid=False,
        weather_override_active=True,
        weather_override_position=60,
        weather_override_min_mode=True,
    )
    registry = _registry_with_custom([WeatherOverrideHandler()])
    result = registry.evaluate(snap)
    assert result.position == 60
    winner_step = next(
        s for s in result.decision_trace if s.matched and s.handler != "floor_clamp"
    )
    assert winner_step.handler == "climate"
    clamp_steps = [
        s for s in result.decision_trace if s.handler == "floor_clamp" and s.matched
    ]
    assert len(clamp_steps) == 1
    assert "weather override" in clamp_steps[0].reason


def test_safety_slot_min_mode_clamps_solar() -> None:
    """A priority-100 (safety) slot floor 60% clamps a Solar winner at 25%.

    Parity with the old force-override min-mode floor (issue #563): the floor
    from a safety-priority slot composes exactly like any custom-position floor.
    """
    cover = _climate_cover(direct_sun_valid=True, calculate_percentage_return=25.0)
    snap = make_snapshot(
        cover=cover,
        direct_sun_valid=True,
        calculate_percentage_return=25.0,
        custom_position_sensors=[
            _cp_state(
                "binary_sensor.s",
                is_on=True,
                position=60,
                min_mode=True,
                sensor_name="Wind alarm",
                priority=CUSTOM_POSITION_SAFETY_PRIORITY,
                slot=5,
            )
        ],
    )
    registry = _registry_with_custom(
        [_cp_handler(5, 60, priority=CUSTOM_POSITION_SAFETY_PRIORITY)]
    )
    result = registry.evaluate(snap)
    assert result.position == 60
    winner_step = next(
        s for s in result.decision_trace if s.matched and s.handler != "floor_clamp"
    )
    assert winner_step.handler == "solar"
    clamp_steps = [
        s for s in result.decision_trace if s.handler == "floor_clamp" and s.matched
    ]
    assert len(clamp_steps) == 1
    assert "Wind alarm" in clamp_steps[0].reason


def test_floor_sources_combined_picks_max() -> None:
    """Custom floor 50 + weather floor 60 + climate 20 → result=60, labelled 'weather override'."""
    cover = _climate_cover(direct_sun_valid=False)
    snap = make_snapshot(
        cover=cover,
        climate_mode_enabled=True,
        climate_readings=_summer_readings(),
        climate_options=_summer_options(),
        direct_sun_valid=False,
        custom_position_sensors=[
            _cp_state(
                "binary_sensor.cp1",
                is_on=True,
                position=50,
                min_mode=True,
                sensor_name="CustomFloor",
            )
        ],
        weather_override_active=True,
        weather_override_position=60,
        weather_override_min_mode=True,
    )
    handlers = [
        _cp_handler(1, 50),
        WeatherOverrideHandler(),
    ]
    registry = _registry_with_custom(handlers)
    result = registry.evaluate(snap)
    assert result.position == 60
    clamp_steps = [
        s for s in result.decision_trace if s.handler == "floor_clamp" and s.matched
    ]
    assert len(clamp_steps) == 1
    assert "weather override" in clamp_steps[0].reason


def test_no_active_floors_unchanged() -> None:
    """No min-mode handler active — climate winner emerges untouched."""
    cover = _climate_cover(direct_sun_valid=False)
    snap = make_snapshot(
        cover=cover,
        climate_mode_enabled=True,
        climate_readings=_summer_readings(),
        climate_options=_summer_options(),
        direct_sun_valid=False,
    )
    registry = _registry_with_custom([])
    result = registry.evaluate(snap)
    # In summer without presence, transparent_blind=True, the policy returns
    # position_for_intent(sun_through=False)=0 for a blind. We just verify
    # no clamp step appears, and result.position is whatever climate yields.
    assert not any(s.handler == "floor_clamp" for s in result.decision_trace)


def test_floor_inactive_when_sensor_off() -> None:
    """A min_mode slot whose sensor is off must not contribute a floor."""
    cover = _climate_cover(direct_sun_valid=True, calculate_percentage_return=30.0)
    snap = make_snapshot(
        cover=cover,
        direct_sun_valid=True,
        calculate_percentage_return=30.0,
        custom_position_sensors=[
            _cp_state(
                "binary_sensor.cp1",
                is_on=False,
                position=60,
                min_mode=True,
                sensor_name="Off",
            )
        ],
    )
    handlers = [_cp_handler(1, 60)]
    registry = _registry_with_custom(handlers)
    result = registry.evaluate(snap)
    assert result.position == 30  # solar
    assert not any(s.handler == "floor_clamp" for s in result.decision_trace)


def test_floor_clamp_sets_floor_clamp_applied_flag() -> None:
    """Active floor that raises the winner sets ``floor_clamp_applied=True`` (issue #469)."""
    cover = _climate_cover(direct_sun_valid=False)
    snap = make_snapshot(
        cover=cover,
        climate_mode_enabled=True,
        climate_readings=_summer_readings(),
        climate_options=_summer_options(),
        direct_sun_valid=False,
        custom_position_sensors=[
            _cp_state(
                "binary_sensor.cp1",
                is_on=True,
                position=60,
                min_mode=True,
                sensor_name="Table",
            )
        ],
    )
    handlers = [_cp_handler(1, 60)]
    registry = _registry_with_custom(handlers)
    result = registry.evaluate(snap)
    assert result.floor_clamp_applied is True


def test_no_clamp_keeps_floor_clamp_applied_false() -> None:
    """With no active floors, ``floor_clamp_applied`` stays False (issue #469)."""
    cover = _climate_cover(direct_sun_valid=False)
    snap = make_snapshot(
        cover=cover,
        climate_mode_enabled=True,
        climate_readings=_summer_readings(),
        climate_options=_summer_options(),
        direct_sun_valid=False,
    )
    registry = _registry_with_custom([])
    result = registry.evaluate(snap)
    assert result.floor_clamp_applied is False


def test_inactive_floor_below_winner_keeps_flag_false() -> None:
    """Floor present but below winner — clamp not applied, flag stays False (issue #469)."""
    cover = _climate_cover(direct_sun_valid=False)
    snap = make_snapshot(
        cover=cover,
        climate_mode_enabled=True,
        climate_readings=ClimateReadings(
            outside_temperature=None,
            inside_temperature=22.0,  # not summer/winter → LOW_LIGHT → default_position
            is_presence=False,
            is_sunny=False,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        ),
        climate_options=_summer_options(),
        default_position=80,
        direct_sun_valid=False,
        custom_position_sensors=[
            _cp_state(
                "binary_sensor.cp1",
                is_on=True,
                position=60,
                min_mode=True,
                sensor_name="Table",
            )
        ],
    )
    handlers = [_cp_handler(1, 60)]
    registry = _registry_with_custom(handlers)
    result = registry.evaluate(snap)
    assert result.position == 80
    assert result.floor_clamp_applied is False


def test_floor_raises_manual_override_held_below_floor() -> None:
    """A floor raises the cover when manual override holds it below the floor (#534).

    Manual override wins with ``position`` = theoretical default (90) but
    ``held_position`` = the cover's actual physical position (50).  A min-mode
    floor at 80% must be measured against the *physical* 50%, not the
    theoretical 90% — so the floor fires and raises the cover to 80%.
    """
    cover = _climate_cover(direct_sun_valid=False)
    snap = make_snapshot(
        cover=cover,
        manual_override_active=True,
        current_cover_position=50,
        default_position=90,
        direct_sun_valid=False,
        custom_position_sensors=[
            _cp_state(
                "binary_sensor.cp1",
                is_on=True,
                position=80,
                min_mode=True,
                sensor_name="Table",
            )
        ],
    )
    handlers = [
        _cp_handler(1, 80),
        ManualOverrideHandler(),
    ]
    registry = _registry_with_custom(handlers)
    result = registry.evaluate(snap)
    winner_step = next(
        s for s in result.decision_trace if s.matched and s.handler != "floor_clamp"
    )
    assert winner_step.handler == "manual_override"
    assert result.position == 80
    assert result.floor_clamp_applied is True
    # The floor raise must reach the cover: a manual-override hold sets
    # skip_command=True, but the composed floor result clears it (#809/#534).
    assert result.skip_command is False
    clamp_steps = [
        s for s in result.decision_trace if s.handler == "floor_clamp" and s.matched
    ]
    assert len(clamp_steps) == 1


def test_floor_above_held_position_is_inert_under_manual_override() -> None:
    """Floor below the cover's physical position does NOT clamp (#534 inert branch).

    Manual override holds the cover at 85% (physical), floor is 80% — the cover
    already sits above the floor, so no clamp is applied.
    """
    cover = _climate_cover(direct_sun_valid=False)
    snap = make_snapshot(
        cover=cover,
        manual_override_active=True,
        current_cover_position=85,
        default_position=90,
        direct_sun_valid=False,
        custom_position_sensors=[
            _cp_state(
                "binary_sensor.cp1",
                is_on=True,
                position=80,
                min_mode=True,
                sensor_name="Table",
            )
        ],
    )
    handlers = [
        _cp_handler(1, 80),
        ManualOverrideHandler(),
    ]
    registry = _registry_with_custom(handlers)
    result = registry.evaluate(snap)
    winner_step = next(
        s for s in result.decision_trace if s.matched and s.handler != "floor_clamp"
    )
    assert winner_step.handler == "manual_override"
    # No clamp: the held physical position (85) is already above the floor (80).
    assert result.floor_clamp_applied is False
    # The manual-override hold is inert-floor: it genuinely holds the cover, so
    # skip_command stays set (no floor raise to clear it)  (#809).
    assert result.skip_command is True
    assert not any(
        s.handler == "floor_clamp" and s.matched for s in result.decision_trace
    )


def test_floor_equal_to_would_be_but_above_held_still_raises() -> None:
    """Alignment edge: floor == would-be position but still above the held one.

    Manual override holds the cover at 0 (physical) while its would-be shadow is
    the default (100).  A min-mode floor at 100 equals the would-be position but
    sits far above the held 0 — the floor must still fire (raise the cover to
    100) and clear the hold, or the blind stays closed forever  (#809/#534).
    """
    cover = _climate_cover(direct_sun_valid=False)
    snap = make_snapshot(
        cover=cover,
        manual_override_active=True,
        current_cover_position=0,
        default_position=100,
        direct_sun_valid=False,
        custom_position_sensors=[
            _cp_state(
                "binary_sensor.cp1",
                is_on=True,
                position=100,
                min_mode=True,
                sensor_name="Table",
            )
        ],
    )
    handlers = [
        _cp_handler(1, 100),
        ManualOverrideHandler(),
    ]
    registry = _registry_with_custom(handlers)
    result = registry.evaluate(snap)
    winner_step = next(
        s for s in result.decision_trace if s.matched and s.handler != "floor_clamp"
    )
    assert winner_step.handler == "manual_override"
    assert result.position == 100
    assert result.floor_clamp_applied is True
    assert result.skip_command is False


def test_floor_label_falls_back_to_entity_id_then_template() -> None:
    """Floor label: sensor_name, else first bound entity_id, else 'template'."""
    from custom_components.adaptive_cover_pro.pipeline.floors import (
        gather_active_floors,
    )

    snap = make_snapshot(
        custom_position_sensors=[
            # No sensor_name → label falls back to the first bound entity_id.
            _cp_state("binary_sensor.cp1", is_on=True, position=60, min_mode=True),
            # Template-only slot (no sensors) → label is "template".
            CustomPositionSensorState(
                entity_ids=(),
                is_on=True,
                position=40,
                priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
                min_mode=True,
                use_my=False,
                slot=2,
                template_active=True,
            ),
        ]
    )
    floors = gather_active_floors(snap)
    assert [f.source for f in floors] == ["custom_position_1", "custom_position_2"]
    assert floors[0].label == "binary_sensor.cp1"
    assert floors[1].label == "template"


def test_floor_label_uses_custom_name_when_set() -> None:
    """A configured custom_name wins over sensor_name for the floor label (issue #867)."""
    from custom_components.adaptive_cover_pro.pipeline.floors import (
        gather_active_floors,
    )

    snap = make_snapshot(
        custom_position_sensors=[
            CustomPositionSensorState(
                entity_ids=("binary_sensor.cp1",),
                is_on=True,
                position=60,
                priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
                min_mode=True,
                use_my=False,
                sensor_name="Table extension",
                slot=1,
                active_entity_ids=("binary_sensor.cp1",),
                custom_name="Movie night floor",
            ),
        ]
    )
    floors = gather_active_floors(snap)
    assert floors[0].label == "Movie night floor"


def test_decision_trace_does_not_mislabel_winner() -> None:
    """Only the underlying handler is marked as the non-clamp winner (no double-winner)."""
    cover = _climate_cover(direct_sun_valid=False)
    snap = make_snapshot(
        cover=cover,
        climate_mode_enabled=True,
        climate_readings=_summer_readings(),
        climate_options=_summer_options(),
        direct_sun_valid=False,
        custom_position_sensors=[
            _cp_state(
                "binary_sensor.cp1",
                is_on=True,
                position=60,
                min_mode=True,
                sensor_name="Table",
            )
        ],
    )
    handlers = [_cp_handler(1, 60)]
    registry = _registry_with_custom(handlers)
    result = registry.evaluate(snap)
    # Exactly one matched=True step that isn't floor_clamp.
    non_clamp_matched = [
        s for s in result.decision_trace if s.matched and s.handler != "floor_clamp"
    ]
    assert len(non_clamp_matched) == 1
    assert non_clamp_matched[0].handler == "climate"


# ---------------------------------------------------------------------------
# Issue #472: FloorClampInfo carries the contributing floor's priority so the
# user-move clamp can gate on it (floor must outrank manual override to clamp).
# ---------------------------------------------------------------------------


def test_floor_clamp_info_carries_priority() -> None:
    """FloorClampInfo exposes a ``priority`` field."""
    from custom_components.adaptive_cover_pro.pipeline.floors import FloorClampInfo

    info = FloorClampInfo(
        source="custom_position_1", label="Table", position=60, priority=82
    )
    assert info.priority == 82


def test_gather_active_floors_populates_custom_slot_priority() -> None:
    """gather_active_floors copies each custom slot's priority onto the floor."""
    from custom_components.adaptive_cover_pro.pipeline.floors import (
        gather_active_floors,
    )

    snap = make_snapshot(
        custom_position_sensors=[
            _cp_state(
                "binary_sensor.cp1",
                is_on=True,
                position=60,
                min_mode=True,
                priority=82,
                slot=1,
            ),
            _cp_state(
                "binary_sensor.cp2",
                is_on=True,
                position=40,
                min_mode=True,
                priority=77,
                slot=2,
            ),
        ]
    )
    floors = gather_active_floors(snap)
    assert [(f.source, f.priority) for f in floors] == [
        ("custom_position_1", 82),
        ("custom_position_2", 77),
    ]


def test_gather_active_floors_weather_uses_handler_priority() -> None:
    """The weather floor takes WeatherOverrideHandler's declared priority (90)."""
    from custom_components.adaptive_cover_pro.pipeline.floors import (
        gather_active_floors,
    )

    snap = make_snapshot(
        weather_override_active=True,
        weather_override_position=60,
        weather_override_min_mode=True,
    )
    floors = gather_active_floors(snap)
    assert len(floors) == 1
    assert floors[0].source == "weather"
    assert floors[0].priority == WeatherOverrideHandler.priority


def test_effective_floor_returns_winning_floor_priority() -> None:
    """effective_floor returns the highest floor's FloorClampInfo with its priority."""
    from custom_components.adaptive_cover_pro.pipeline.floors import (
        effective_floor,
        gather_active_floors,
    )

    snap = make_snapshot(
        custom_position_sensors=[
            _cp_state(
                "binary_sensor.cp1",
                is_on=True,
                position=40,
                min_mode=True,
                priority=77,
                slot=1,
            ),
            _cp_state(
                "binary_sensor.cp2",
                is_on=True,
                position=65,
                min_mode=True,
                priority=82,
                slot=2,
            ),
        ]
    )
    pos, info = effective_floor(gather_active_floors(snap))
    assert pos == 65
    assert info is not None
    assert info.priority == 82
