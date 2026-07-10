"""Tests for per-slot tilt-only override mode (issue #514).

A custom-position slot with ``tilt_only=True`` fixes the slat angle (tilt)
but does NOT claim the position axis. Solar (or whatever wins position) drives
the carriage; the slot's tilt is overlaid onto the winner by a dedicated
tilt-axis pass in the registry (modeled on the floor pass).
"""

from __future__ import annotations

from unittest.mock import MagicMock


from custom_components.adaptive_cover_pro.const import (
    DEFAULT_CUSTOM_POSITION_PRIORITY,
    ControlMethod,
)
from custom_components.adaptive_cover_pro.pipeline.handlers import (
    DefaultHandler,
    SolarHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
)

from tests.test_pipeline.conftest import make_snapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cp_state(
    entity_id: str,
    *,
    is_on: bool,
    position: int = 50,
    tilt: int | None = None,
    tilt_only: bool = False,
    min_mode: bool = False,
    use_my: bool = False,
    priority: int = DEFAULT_CUSTOM_POSITION_PRIORITY,
    slot: int = 1,
    sensor_name: str | None = None,
    custom_name: str | None = None,
) -> CustomPositionSensorState:
    return CustomPositionSensorState(
        entity_ids=(entity_id,),
        is_on=is_on,
        position=position,
        priority=priority,
        min_mode=min_mode,
        use_my=use_my,
        tilt=tilt,
        tilt_only=tilt_only,
        sensor_name=sensor_name,
        slot=slot,
        active_entity_ids=(entity_id,) if is_on else (),
        custom_name=custom_name,
    )


def _cp_handler(
    slot: int,
    position: int = 50,
    *,
    priority: int = DEFAULT_CUSTOM_POSITION_PRIORITY,
    tilt: int | None = None,
) -> CustomPositionHandler:
    return CustomPositionHandler(
        slot=slot,
        position=position,
        priority=priority,
        tilt=tilt,
    )


def _solar_cover(*, calculate_percentage_return: float = 50.0) -> MagicMock:
    """Build a mock cover that lets SolarHandler win position."""
    cover = MagicMock(
        spec=[
            "direct_sun_valid",
            "calculate_percentage",
            "distance",
            "gamma",
            "config",
            "valid",
            "valid_elevation",
            "is_sun_in_blind_spot",
            "sunset_valid",
            "calculate_position",
            "control_state_reason",
            "sun_data",
        ]
    )
    cover.direct_sun_valid = True
    cover.calculate_percentage = MagicMock(return_value=calculate_percentage_return)
    cover.distance = 3.0
    cover.gamma = 0.0
    config = MagicMock()
    config.min_pos = None
    config.max_pos = None
    config.min_pos_sun_only = False
    config.max_pos_sun_only = False
    config.min_pos_sun_tracking = None
    cover.config = config
    return cover


# ---------------------------------------------------------------------------
# Step 1 — dataclass field
# ---------------------------------------------------------------------------


class TestSensorStateTiltOnlyField:
    """``CustomPositionSensorState`` carries a ``tilt_only`` flag."""

    def test_tilt_only_defaults_false(self) -> None:
        state = CustomPositionSensorState(
            entity_ids=("binary_sensor.a",),
            is_on=True,
            position=50,
            priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
            min_mode=False,
            use_my=False,
        )
        assert state.tilt_only is False

    def test_tilt_only_set_true(self) -> None:
        state = CustomPositionSensorState(
            entity_ids=("binary_sensor.a",),
            is_on=True,
            position=50,
            priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
            min_mode=False,
            use_my=False,
            tilt_only=True,
        )
        assert state.tilt_only is True


# ---------------------------------------------------------------------------
# Step 2 — handler defers in tilt-only mode
# ---------------------------------------------------------------------------


class TestHandlerDefersInTiltOnly:
    """A tilt-only slot does not claim the position axis from evaluate()."""

    def test_active_tilt_only_returns_none(self) -> None:
        """Sensor on + tilt_only → evaluate() returns None (defer like min_mode)."""
        snap = make_snapshot(
            custom_position_sensors=[
                _cp_state(
                    "binary_sensor.t", is_on=True, position=80, tilt=30, tilt_only=True
                )
            ]
        )
        handler = _cp_handler(1, 80, tilt=30)
        assert handler.evaluate(snap) is None

    def test_active_without_tilt_only_still_claims(self) -> None:
        """Sensor on + tilt_only False → handler still claims position."""
        snap = make_snapshot(
            custom_position_sensors=[
                _cp_state("binary_sensor.t", is_on=True, position=80, tilt=30)
            ]
        )
        handler = _cp_handler(1, 80, tilt=30)
        result = handler.evaluate(snap)
        assert result is not None
        assert result.position == 80
        assert result.control_method == ControlMethod.CUSTOM_POSITION


# ---------------------------------------------------------------------------
# gather_tilt_only_contributions — the sibling collector helper
# ---------------------------------------------------------------------------


class TestGatherTiltOnlyContributions:
    """``gather_tilt_only_contributions`` lists every active tilt-only slot."""

    def test_collects_active_tilt_only_slots(self) -> None:
        from custom_components.adaptive_cover_pro.pipeline.tilt_axis import (
            gather_tilt_only_contributions,
        )

        snap = make_snapshot(
            custom_position_sensors=[
                _cp_state("binary_sensor.a", is_on=True, tilt=42, tilt_only=True),
                _cp_state(
                    "binary_sensor.b",
                    is_on=False,
                    tilt=10,
                    tilt_only=True,
                    slot=2,
                ),
            ]
        )
        contributions = gather_tilt_only_contributions(snap)
        assert [c.source for c in contributions] == ["custom_position_1"]
        assert contributions[0].tilt == 42

    def test_label_uses_custom_name_when_set(self) -> None:
        """A configured custom_name wins over sensor_name (issue #867)."""
        from custom_components.adaptive_cover_pro.pipeline.tilt_axis import (
            gather_tilt_only_contributions,
        )

        snap = make_snapshot(
            custom_position_sensors=[
                _cp_state(
                    "binary_sensor.a",
                    is_on=True,
                    tilt=42,
                    tilt_only=True,
                    sensor_name="Glare slot",
                    custom_name="Evening tilt",
                )
            ]
        )
        (contribution,) = gather_tilt_only_contributions(snap)
        assert contribution.label == "Evening tilt"

    def test_label_falls_back_to_entity_id_when_unnamed(self) -> None:
        from custom_components.adaptive_cover_pro.pipeline.tilt_axis import (
            gather_tilt_only_contributions,
        )

        snap = make_snapshot(
            custom_position_sensors=[
                _cp_state("binary_sensor.unnamed", is_on=True, tilt=30, tilt_only=True)
            ]
        )
        (contribution,) = gather_tilt_only_contributions(snap)
        assert contribution.label == "binary_sensor.unnamed"


# ---------------------------------------------------------------------------
# Step 3 — pure tilt-axis resolution module
# ---------------------------------------------------------------------------


class TestResolveTiltAxis:
    """``resolve_tilt_axis`` gathers active tilt-only slots and picks the winner."""

    def test_none_when_no_tilt_only_active(self) -> None:
        from custom_components.adaptive_cover_pro.pipeline.tilt_axis import (
            resolve_tilt_axis,
        )

        snap = make_snapshot(
            custom_position_sensors=[
                _cp_state("binary_sensor.a", is_on=True, tilt=30, tilt_only=False)
            ]
        )
        assert resolve_tilt_axis(snap) is None

    def test_none_when_tilt_only_sensor_off(self) -> None:
        from custom_components.adaptive_cover_pro.pipeline.tilt_axis import (
            resolve_tilt_axis,
        )

        snap = make_snapshot(
            custom_position_sensors=[
                _cp_state("binary_sensor.a", is_on=False, tilt=30, tilt_only=True)
            ]
        )
        assert resolve_tilt_axis(snap) is None

    def test_picks_single_active_tilt_only(self) -> None:
        from custom_components.adaptive_cover_pro.pipeline.tilt_axis import (
            resolve_tilt_axis,
        )

        snap = make_snapshot(
            custom_position_sensors=[
                _cp_state(
                    "binary_sensor.a",
                    is_on=True,
                    tilt=42,
                    tilt_only=True,
                    slot=2,
                    sensor_name="Glare slot",
                )
            ]
        )
        info = resolve_tilt_axis(snap)
        assert info is not None
        assert info.tilt == 42
        assert info.source == "custom_position_2"
        assert info.label == "Glare slot"
        assert info.slot == 2

    def test_highest_priority_wins(self) -> None:
        from custom_components.adaptive_cover_pro.pipeline.tilt_axis import (
            resolve_tilt_axis,
        )

        snap = make_snapshot(
            custom_position_sensors=[
                _cp_state(
                    "binary_sensor.low",
                    is_on=True,
                    tilt=10,
                    tilt_only=True,
                    priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
                    slot=1,
                    sensor_name="Low slot",
                ),
                _cp_state(
                    "binary_sensor.high",
                    is_on=True,
                    tilt=90,
                    tilt_only=True,
                    priority=DEFAULT_CUSTOM_POSITION_PRIORITY + 10,
                    slot=2,
                    sensor_name="High slot",
                ),
            ]
        )
        info = resolve_tilt_axis(snap)
        assert info is not None
        assert info.tilt == 90
        assert info.source == "custom_position_2"

    def test_tilt_none_slot_ignored(self) -> None:
        """A tilt-only slot with no configured tilt value contributes nothing."""
        from custom_components.adaptive_cover_pro.pipeline.tilt_axis import (
            resolve_tilt_axis,
        )

        snap = make_snapshot(
            custom_position_sensors=[
                _cp_state("binary_sensor.a", is_on=True, tilt=None, tilt_only=True)
            ]
        )
        assert resolve_tilt_axis(snap) is None

    def test_label_falls_back_to_entity_id_when_unnamed(self) -> None:
        """Unnamed tilt-only slot: label should fall back to the entity_id."""
        from custom_components.adaptive_cover_pro.pipeline.tilt_axis import (
            resolve_tilt_axis,
        )

        snap = make_snapshot(
            custom_position_sensors=[
                _cp_state("binary_sensor.unnamed", is_on=True, tilt=30, tilt_only=True)
            ]
        )
        info = resolve_tilt_axis(snap)
        assert info is not None
        assert info.label == "binary_sensor.unnamed"

    def test_label_uses_custom_name_when_set(self) -> None:
        """A configured custom_name wins over sensor_name for the tilt-axis label (issue #867)."""
        from custom_components.adaptive_cover_pro.pipeline.tilt_axis import (
            resolve_tilt_axis,
        )

        snap = make_snapshot(
            custom_position_sensors=[
                _cp_state(
                    "binary_sensor.a",
                    is_on=True,
                    tilt=42,
                    tilt_only=True,
                    slot=2,
                    sensor_name="Glare slot",
                    custom_name="Evening tilt",
                )
            ]
        )
        info = resolve_tilt_axis(snap)
        assert info is not None
        assert info.label == "Evening tilt"


# ---------------------------------------------------------------------------
# Step 4 — registry overlays tilt onto the position winner
# ---------------------------------------------------------------------------


def _registry(handlers: list) -> PipelineRegistry:
    return PipelineRegistry([*handlers, SolarHandler(), DefaultHandler()])


class TestRegistryOverlaysTilt:
    """The registry tilt-axis pass overlays a tilt-only slot onto the winner."""

    def test_solar_wins_position_tilt_overlaid(self) -> None:
        cover = _solar_cover(calculate_percentage_return=60.0)
        snap = make_snapshot(
            cover=cover,
            custom_position_sensors=[
                _cp_state(
                    "binary_sensor.t",
                    is_on=True,
                    position=80,
                    tilt=25,
                    tilt_only=True,
                    slot=1,
                    sensor_name="Slot one",
                )
            ],
        )
        handler = _cp_handler(1, 80, tilt=25)
        result = _registry([handler]).evaluate(snap)
        # Solar drives position; tilt-only slot fixes the slat.
        assert result.control_method == ControlMethod.SOLAR
        assert result.position == 60
        assert result.tilt == 25
        assert result.tilt_only_contribution_active is True
        # The applied tilt-only slot identity is recorded for diagnostics (#667).
        assert result.tilt_only_slot == 1

    def test_trace_has_matched_tilt_step_no_stale_skip(self) -> None:
        cover = _solar_cover(calculate_percentage_return=60.0)
        snap = make_snapshot(
            cover=cover,
            custom_position_sensors=[
                _cp_state(
                    "binary_sensor.t",
                    is_on=True,
                    position=80,
                    tilt=25,
                    tilt_only=True,
                    slot=1,
                    sensor_name="Slot one",
                )
            ],
        )
        handler = _cp_handler(1, 80, tilt=25)
        result = _registry([handler]).evaluate(snap)
        cp_steps = [
            s for s in result.decision_trace if s.handler == "custom_position_1"
        ]
        # Exactly one custom_position_1 step, matched, carrying the tilt, and
        # NOT the stale "sensor not active" describe_skip text.
        assert len(cp_steps) == 1
        step = cp_steps[0]
        assert step.matched is True
        assert step.tilt == 25
        assert "not active" not in step.reason


# ---------------------------------------------------------------------------
# Step 5 — fill-when-unset: winner with explicit tilt is not overwritten
# ---------------------------------------------------------------------------


class TestFillWhenUnset:
    """A position-winner that already set tilt keeps its tilt (decision Q1b)."""

    def test_winner_tilt_not_overwritten(self) -> None:
        cover = _solar_cover(calculate_percentage_return=60.0)
        # A higher-priority NON-tilt-only custom slot wins position AND sets
        # an explicit tilt of 70%. A lower-priority tilt-only slot is active.
        snap = make_snapshot(
            cover=cover,
            custom_position_sensors=[
                _cp_state(
                    "binary_sensor.winner",
                    is_on=True,
                    position=40,
                    tilt=70,
                    tilt_only=False,
                    priority=DEFAULT_CUSTOM_POSITION_PRIORITY + 10,
                    slot=1,
                ),
                _cp_state(
                    "binary_sensor.tiltonly",
                    is_on=True,
                    position=80,
                    tilt=25,
                    tilt_only=True,
                    priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
                    slot=2,
                    sensor_name="Tilt-only slot",
                ),
            ],
        )
        winner_handler = _cp_handler(
            1,
            40,
            tilt=70,
            priority=DEFAULT_CUSTOM_POSITION_PRIORITY + 10,
        )
        tiltonly_handler = _cp_handler(
            2,
            80,
            tilt=25,
            priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
        )
        result = _registry([winner_handler, tiltonly_handler]).evaluate(snap)
        # Winner's explicit tilt survives; tilt-only does not overwrite it.
        assert result.control_method == ControlMethod.CUSTOM_POSITION
        assert result.position == 40
        assert result.tilt == 70
        # The contribution is still flagged active (it fired), and its trace
        # step records it deferred.
        deferred = [
            s for s in result.decision_trace if s.handler == "custom_position_2"
        ]
        assert len(deferred) == 1
        assert deferred[0].matched is False
        # Deferred (not applied) → no slot recorded for the Control Status surface (#667).
        assert result.tilt_only_slot is None


# ---------------------------------------------------------------------------
# Step 11 — end-to-end: options → snapshot_builder → registry overlay
# ---------------------------------------------------------------------------


class TestEndToEndTiltOnly:
    """Wire keys → snapshot_builder read → registry overlay, solar drives pos."""

    def test_full_cycle_solar_position_fixed_tilt(self) -> None:
        from unittest.mock import MagicMock

        from custom_components.adaptive_cover_pro.const import (
            CUSTOM_POSITION_SLOTS,
        )
        from custom_components.adaptive_cover_pro.pipeline.snapshot_builder import (
            PipelineSnapshotBuilder,
        )
        from custom_components.adaptive_cover_pro.state.climate_provider import (
            ClimateProvider,
        )

        slot1 = CUSTOM_POSITION_SLOTS[1]
        options = {
            slot1["sensor"]: "binary_sensor.glare",
            slot1["position"]: 80,
            slot1["tilt"]: 25,
            slot1["tilt_only"]: True,
        }

        on_state = MagicMock()
        on_state.state = "on"
        on_state.attributes = {"friendly_name": "Glare"}
        hass = MagicMock()
        hass.states.get.side_effect = lambda eid: (
            on_state if eid == "binary_sensor.glare" else None
        )
        toggles = MagicMock()
        builder = PipelineSnapshotBuilder(
            hass=hass,
            logger=MagicMock(),
            climate_provider=MagicMock(spec=ClimateProvider),
            toggles=toggles,
            policy=MagicMock(),
            config_service=MagicMock(),
        )

        states = builder.read_custom_position_sensors(options)
        assert len(states) == 1
        assert states[0].tilt_only is True

        cover = _solar_cover(calculate_percentage_return=60.0)
        snap = make_snapshot(cover=cover, custom_position_sensors=states)
        handler = _cp_handler(1, 80, tilt=25)
        result = _registry([handler]).evaluate(snap)

        assert result.control_method == ControlMethod.SOLAR
        assert result.position == 60
        assert result.tilt == 25
        assert result.tilt_only_contribution_active is True
