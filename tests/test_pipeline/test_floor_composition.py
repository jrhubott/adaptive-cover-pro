"""Tests for cross-handler floor-mode composition (issue #463).

The pipeline registry composes floor (min-mode) clamps from custom-position,
weather-override, and force-override sources as a post-decision pass. A floor
no longer wins the priority chain — it raises the winner's position when the
winner is below the highest active floor.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.const import (
    DEFAULT_CUSTOM_POSITION_PRIORITY,
)
from custom_components.adaptive_cover_pro.pipeline.handlers import (
    ClimateHandler,
    DefaultHandler,
    ForceOverrideHandler,
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
    slot: int = 0,
) -> CustomPositionSensorState:
    return CustomPositionSensorState(
        entity_id=entity_id,
        is_on=is_on,
        position=position,
        priority=priority,
        min_mode=min_mode,
        use_my=use_my,
        sensor_name=sensor_name,
        slot=slot,
    )


def _cp_handler(
    slot: int,
    entity_id: str,
    position: int,
    *,
    priority: int = DEFAULT_CUSTOM_POSITION_PRIORITY,
) -> CustomPositionHandler:
    return CustomPositionHandler(
        slot=slot,
        entity_id=entity_id,
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
    handlers = [_cp_handler(1, "binary_sensor.cp1", 60)]
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
    handlers = [_cp_handler(1, "binary_sensor.cp1", 60)]
    registry = _registry_with_custom(handlers)
    result = registry.evaluate(snap)
    assert result.position == 80
    assert not any(
        s.handler == "floor_clamp" and s.matched for s in result.decision_trace
    )
    cp_step = next(s for s in result.decision_trace if s.handler == "custom_position_1")
    assert cp_step.matched is False


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
            ),
        ],
    )
    handlers = [
        _cp_handler(1, "binary_sensor.cp1", 40),
        _cp_handler(2, "binary_sensor.cp2", 60),
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
        _cp_handler(4, "binary_sensor.cp4", 80),
        _cp_handler(3, "binary_sensor.cp3", 100),
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
            ),
        ],
    )
    handlers = [
        _cp_handler(1, "binary_sensor.cp1", 30),
        _cp_handler(2, "binary_sensor.cp2", 60),
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


def test_force_override_min_mode_clamps_solar() -> None:
    """Force-override floor 60% clamps a Solar winner at 25%."""
    cover = _climate_cover(direct_sun_valid=True, calculate_percentage_return=25.0)
    snap = make_snapshot(
        cover=cover,
        direct_sun_valid=True,
        calculate_percentage_return=25.0,
        force_override_sensors={"binary_sensor.s": True},
        force_override_position=60,
        force_override_min_mode=True,
    )
    registry = _registry_with_custom([ForceOverrideHandler()])
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
    assert "force override" in clamp_steps[0].reason


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
        _cp_handler(1, "binary_sensor.cp1", 50),
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
    handlers = [_cp_handler(1, "binary_sensor.cp1", 60)]
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
    handlers = [_cp_handler(1, "binary_sensor.cp1", 60)]
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
    handlers = [_cp_handler(1, "binary_sensor.cp1", 60)]
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
        _cp_handler(1, "binary_sensor.cp1", 80),
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
        _cp_handler(1, "binary_sensor.cp1", 80),
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
    assert not any(
        s.handler == "floor_clamp" and s.matched for s in result.decision_trace
    )


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
    handlers = [_cp_handler(1, "binary_sensor.cp1", 60)]
    registry = _registry_with_custom(handlers)
    result = registry.evaluate(snap)
    # Exactly one matched=True step that isn't floor_clamp.
    non_clamp_matched = [
        s for s in result.decision_trace if s.matched and s.handler != "floor_clamp"
    ]
    assert len(non_clamp_matched) == 1
    assert non_clamp_matched[0].handler == "climate"
