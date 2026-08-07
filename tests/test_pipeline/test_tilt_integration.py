"""Wave G — End-to-end tilt integration tests (Steps 16 & 17).

Step 16: a CustomPositionHandler with an explicit tilt value produces a
         PipelineResult with that tilt, and VenetianPolicy.post_pipeline_resolve
         honors it (i.e., the resolved result keeps the handler tilt rather than
         letting the engine overwrite it).

Step 17: non-venetian cover-type policies (blind, awning, tilt) are pure
         identity functions — they return the PipelineResult unchanged, so
         any tilt field on the result passes through untouched.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    DEFAULT_CUSTOM_POSITION_PRIORITY,
    DEFAULT_TRACKING_SEASONS,
    ClimateStrategy,
    ControlMethod,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
    ClimateHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.cloud_suppression import (
    CloudSuppressionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
    DefaultHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.motion_timeout import (
    MotionTimeoutHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.solar import SolarHandler
from custom_components.adaptive_cover_pro.pipeline.helpers import (
    compute_default_position,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import (
    ClimateOptions,
    CustomPositionSensorState,
    PipelineResult,
)
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings
from tests.test_pipeline.conftest import make_snapshot

# ---------------------------------------------------------------------------
# Mock tilt-cover builder for issue #1214 finding 2's TILT_WITH_PRESENCE case
# ---------------------------------------------------------------------------
# The generic conftest mock cover has no `beta` attribute (only position-
# primary tests need it), and ClimateHandler._build_context reads
# `tilt_cover.beta` unconditionally for tilt-primary covers. Mirrors
# `test_climate_handler.py::_make_tilt_cover`.


def _make_low_light_solar_tilt_cover(*, percentage: float):
    from custom_components.adaptive_cover_pro.engine.covers import AdaptiveTiltCover

    cover = MagicMock(spec=AdaptiveTiltCover)
    cover.direct_sun_valid = True
    cover.valid = True
    cover.calculate_percentage = MagicMock(return_value=percentage)
    cover.calculate_raw_percentage = MagicMock(return_value=percentage)
    cover.gamma = 0.0
    cover.beta = 0.0
    cover.mode = "mode2"
    config = MagicMock()
    config.min_pos = None
    config.max_pos = None
    config.min_pos_sun_only = False
    config.max_pos_sun_only = False
    config.min_pos_sun_tracking = None
    cover.config = config
    return cover


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cps(
    entity_id: str,
    is_on: bool,
    position: int = 50,
    priority: int = 77,
    *,
    tilt: int | None = None,
) -> CustomPositionSensorState:
    return CustomPositionSensorState(
        entity_ids=(entity_id,),
        is_on=is_on,
        position=position,
        priority=priority,
        min_mode=False,
        use_my=False,
        tilt=tilt,
        slot=1,
        active_entity_ids=(entity_id,) if is_on else (),
    )


def _registry_with_custom_tilt(tilt: int | None = None) -> PipelineRegistry:
    """One CustomPositionHandler that carries an explicit tilt (or None)."""
    return PipelineRegistry(
        [
            CustomPositionHandler(
                slot=1,
                position=60,
                tilt=tilt,
                priority=DEFAULT_CUSTOM_POSITION_PRIORITY,
            ),
            SolarHandler(),
            DefaultHandler(),
        ]
    )


def _solar_kwargs():
    """Kwargs for VenetianPolicy.post_pipeline_resolve with a valid sun."""
    from tests.cover_helpers import (
        make_cover_config,
        make_tilt_config,
        make_vertical_config,
    )

    svc = MagicMock()
    svc.get_vertical_data.return_value = make_vertical_config()
    svc.get_tilt_data.return_value = make_tilt_config()

    cover = MagicMock()
    cover.direct_sun_valid = True

    sun_data = MagicMock()
    sun_data.timezone = "UTC"

    return {
        "cover": cover,
        "logger": MagicMock(),
        "sol_azi": 180.0,
        "sol_elev": 45.0,
        "sun_data": sun_data,
        "config": make_cover_config(),
        "config_service": svc,
        "options": {},
    }


# ---------------------------------------------------------------------------
# Step 16: CustomPositionHandler tilt → pipeline → VenetianPolicy honored
# ---------------------------------------------------------------------------


class TestCustomPositionTiltEndToEnd:
    """The explicit tilt from a CustomPositionHandler survives the full pipeline
    and is honored by VenetianPolicy.post_pipeline_resolve.

    The scenario: one custom-position slot is active (sensor is ON) and carries
    tilt=40.  The pipeline result therefore has tilt=40.  Because the pipeline
    winner is CUSTOM_POSITION (not SOLAR), the venetian policy's suppression
    check fires first and should clear the tilt — confirming the suppression
    path runs before the handler-tilt honor path.
    """

    def test_custom_handler_stamps_tilt_on_pipeline_result(self):
        """Registry result carries the handler's tilt when the slot is active."""
        registry = _registry_with_custom_tilt(tilt=40)
        snap = make_snapshot(
            custom_position_sensors=[_cps("binary_sensor.scene", is_on=True)],
            direct_sun_valid=False,
        )
        result = registry.evaluate(snap)
        assert result.control_method == ControlMethod.CUSTOM_POSITION
        assert result.tilt == 40

    def test_custom_handler_no_tilt_leaves_result_tilt_none(self):
        """If the handler has no tilt configured, result.tilt is None."""
        registry = _registry_with_custom_tilt(tilt=None)
        snap = make_snapshot(
            custom_position_sensors=[_cps("binary_sensor.scene", is_on=True)],
            direct_sun_valid=False,
        )
        result = registry.evaluate(snap)
        assert result.control_method == ControlMethod.CUSTOM_POSITION
        assert result.tilt is None

    def test_custom_handler_tilt_zero_stamps_zero(self):
        """tilt=0 is a valid explicit value — not treated as absent."""
        registry = _registry_with_custom_tilt(tilt=0)
        snap = make_snapshot(
            custom_position_sensors=[_cps("binary_sensor.scene", is_on=True)],
            direct_sun_valid=False,
        )
        result = registry.evaluate(snap)
        assert result.tilt == 0

    def test_custom_handler_tilt_honored_by_venetian_for_non_solar(self):
        """VenetianPolicy honors handler tilt for CUSTOM_POSITION (issue #369).

        The handler-tilt honor path runs before engine suppression, so a
        custom-position handler that supplies tilt=40 survives end-to-end even
        when ControlMethod is non-SOLAR.
        """
        from custom_components.adaptive_cover_pro.cover_types.venetian import (
            VenetianPolicy,
        )

        registry = _registry_with_custom_tilt(tilt=40)
        snap = make_snapshot(
            custom_position_sensors=[_cps("binary_sensor.scene", is_on=True)],
            direct_sun_valid=False,
        )
        pipeline_result = registry.evaluate(snap)
        assert pipeline_result.control_method == ControlMethod.CUSTOM_POSITION
        assert pipeline_result.tilt == 40

        policy = VenetianPolicy()
        resolved = policy.post_pipeline_resolve(pipeline_result, **_solar_kwargs())
        assert resolved.tilt == 40

    def test_solar_handler_tilt_honored_by_venetian(self):
        """A PipelineResult with SOLAR control_method + tilt → venetian honors it.

        This simulates a future scenario where a SolarHandler variant could stamp
        a tilt. We inject the tilt directly on a synthetic result to prove the
        venetian path works end-to-end without going through the registry.
        """
        from custom_components.adaptive_cover_pro.cover_types.venetian import (
            VenetianPolicy,
        )

        result = PipelineResult(
            position=55,
            control_method=ControlMethod.SOLAR,
            reason="solar",
            tilt=42,
        )
        policy = VenetianPolicy()
        resolved = policy.post_pipeline_resolve(result, **_solar_kwargs())
        assert resolved.tilt == 42

    def test_default_handler_tilt_honored_by_venetian(self):
        """DEFAULT control method with handler tilt → venetian honors it (issue #369)."""
        from custom_components.adaptive_cover_pro.cover_types.venetian import (
            VenetianPolicy,
        )

        result = PipelineResult(
            position=30,
            control_method=ControlMethod.DEFAULT,
            reason="default",
            tilt=70,
        )
        policy = VenetianPolicy()
        resolved = policy.post_pipeline_resolve(result, **_solar_kwargs())
        assert resolved.tilt == 70


class TestLosingCustomPositionTiltDoesNotSuppressSolarEngine:
    """Full-stack regression for issue #1153.

    Reproduces the reporter's exact configuration end-to-end: a
    CustomPositionHandler slot is triggered but loses on priority (1) to
    SolarHandler (40), which wins with tilt=None (a venetian resolves its
    slat angle after the pipeline). Before the fix, the registry's
    ``_MERGEABLE`` fill copied the *losing* slot's tilt onto the winner, and
    ``VenetianPolicy.post_pipeline_resolve`` treated that non-None tilt as
    explicit user intent — honoring it and never running the solar tilt
    engine, even though solar unambiguously won the decision trace.
    """

    def _resolved(self):
        from custom_components.adaptive_cover_pro.cover_types.venetian import (
            VenetianPolicy,
        )

        registry = PipelineRegistry(
            [
                SolarHandler(),
                DefaultHandler(),
                CustomPositionHandler(slot=1, position=0, priority=1, tilt=100),
            ]
        )
        snap = make_snapshot(
            custom_position_sensors=[
                _cps(
                    "binary_sensor.slot1",
                    is_on=True,
                    position=0,
                    priority=1,
                    tilt=100,
                )
            ],
            direct_sun_valid=True,
        )
        pipeline_result = registry.evaluate(snap)
        assert pipeline_result.control_method == ControlMethod.SOLAR
        assert pipeline_result.tilt is None

        policy = VenetianPolicy()
        return policy.post_pipeline_resolve(pipeline_result, **_solar_kwargs())

    def test_resolved_tilt_is_the_engine_tilt_not_the_losing_slot(self):
        """The resolved tilt comes from the solar engine, never the loser's 100."""
        resolved = self._resolved()
        assert resolved.tilt is not None
        assert resolved.tilt != 100

    def test_handler_tilt_honored_step_is_absent(self):
        """'venetian_handler_tilt' must not appear — the loser never won the tilt."""
        resolved = self._resolved()
        handler_names = [s.handler for s in resolved.decision_trace]
        assert "venetian_handler_tilt" not in handler_names

    def test_engine_step_is_present(self):
        """The solar tilt engine step must run instead."""
        resolved = self._resolved()
        handler_names = [s.handler for s in resolved.decision_trace]
        assert "venetian_engine" in handler_names


# ---------------------------------------------------------------------------
# Step 17: Non-venetian policy — identity passthrough preserves tilt field
# ---------------------------------------------------------------------------


class TestNonVenetianPolicyTiltPassthrough:
    """Blind, awning, and tilt cover-type policies do not override post_pipeline_resolve;
    they inherit the base CoverTypePolicy identity implementation.

    A PipelineResult with any tilt value must be returned unchanged.
    """

    @pytest.mark.parametrize(
        "policy_cls",
        [
            pytest.param(
                "custom_components.adaptive_cover_pro.cover_types.blind.BlindPolicy",
                id="blind",
            ),
            pytest.param(
                "custom_components.adaptive_cover_pro.cover_types.awning.AwningPolicy",
                id="awning",
            ),
            pytest.param(
                "custom_components.adaptive_cover_pro.cover_types.tilt.TiltPolicy",
                id="tilt",
            ),
        ],
    )
    def test_tilt_passes_through_unchanged(self, policy_cls: str):
        """post_pipeline_resolve is identity — tilt field not touched."""
        import importlib

        module_path, cls_name = policy_cls.rsplit(".", 1)
        mod = importlib.import_module(module_path)
        policy = getattr(mod, cls_name)()

        result = PipelineResult(
            position=50,
            control_method=ControlMethod.SOLAR,
            reason="solar",
            tilt=33,
        )
        resolved = policy.post_pipeline_resolve(
            result,
            logger=MagicMock(),
            sol_azi=180.0,
            sol_elev=45.0,
            sun_data=MagicMock(),
            config=MagicMock(),
            config_service=MagicMock(),
            options={},
        )
        assert resolved is result  # strict identity — same object returned
        assert resolved.tilt == 33

    @pytest.mark.parametrize(
        "policy_cls",
        [
            pytest.param(
                "custom_components.adaptive_cover_pro.cover_types.blind.BlindPolicy",
                id="blind",
            ),
            pytest.param(
                "custom_components.adaptive_cover_pro.cover_types.awning.AwningPolicy",
                id="awning",
            ),
            pytest.param(
                "custom_components.adaptive_cover_pro.cover_types.tilt.TiltPolicy",
                id="tilt",
            ),
        ],
    )
    def test_tilt_none_passes_through_unchanged(self, policy_cls: str):
        """Tilt=None also passes through — not converted to zero."""
        import importlib

        module_path, cls_name = policy_cls.rsplit(".", 1)
        mod = importlib.import_module(module_path)
        policy = getattr(mod, cls_name)()

        result = PipelineResult(
            position=50,
            control_method=ControlMethod.DEFAULT,
            reason="default",
            tilt=None,
        )
        resolved = policy.post_pipeline_resolve(
            result,
            logger=MagicMock(),
            sol_azi=180.0,
            sol_elev=45.0,
            sun_data=MagicMock(),
            config=MagicMock(),
            config_service=MagicMock(),
            options={},
        )
        assert resolved is result
        assert resolved.tilt is None

    def test_blind_none_result_returns_none(self):
        """Blind policy must return None unchanged when result is None (cold-start guard)."""
        from custom_components.adaptive_cover_pro.cover_types.blind import BlindPolicy

        policy = BlindPolicy()
        resolved = policy.post_pipeline_resolve(
            None,
            logger=MagicMock(),
            sol_azi=0.0,
            sol_elev=-10.0,
            sun_data=MagicMock(),
            config=MagicMock(),
            config_service=MagicMock(),
            options={},
        )
        assert resolved is None


# ---------------------------------------------------------------------------
# Issue #1214 — default/sunset tilt reaches higher-priority DEFAULT winners
# ---------------------------------------------------------------------------
#
# #1153 correctly closed the _MERGEABLE tilt leak (a losing DefaultHandler
# could no longer stamp its tilt onto a winning handler), but ClimateHandler
# (LOW_LIGHT), CloudSuppressionHandler, and MotionTimeoutHandler all answer
# with the *effective default position* via compute_default_position and
# never supplied a tilt of their own -- so a venetian's sunset_tilt/default_tilt
# became unreachable whenever one of these three outranked DefaultHandler.
# These full-stack registry tests lock the fix: each handler supplies its OWN
# tilt (via compute_default_tilt), so _MERGEABLE stays untouched.


def _low_light_climate_readings(
    *, inside_temperature: float = 22.0, is_sunny: bool = False
) -> ClimateReadings:
    """Build readings that drive ClimateStrategy.LOW_LIGHT: presence, no sun, intermediate temp.

    ``inside_temperature`` drives the season predicates (``_climate_options``
    uses temp_low 18 / temp_high 26 and temp_switch False, so this reading is
    the one ``get_current_temperature`` resolves to). ``is_sunny=True`` turns
    the low-light predicate off so a table's later branches become reachable.
    """
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=inside_temperature,
        is_presence=True,
        is_sunny=is_sunny,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        cloud_coverage_above_threshold=False,
    )


def _make_limited_cover(
    *,
    direct_sun_valid: bool = False,
    min_pos: int | None = None,
    max_pos: int | None = None,
    min_pos_sun_only: bool = False,
    max_pos_sun_only: bool = False,
    min_pos_sun_tracking: int | None = None,
):
    """Mock cover whose ``CoverConfig`` carries real min/max position limits.

    ``make_snapshot`` sources ``snapshot.config`` from ``cover.config`` and the
    conftest mock leaves every limit unset, so a limit-sensitive test has to
    build its own. Mirrors ``conftest._make_mock_cover``'s spec list.
    """
    cover = MagicMock(
        spec=[
            "direct_sun_valid",
            "calculate_percentage",
            "calculate_raw_percentage",
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
    cover.direct_sun_valid = direct_sun_valid
    cover.valid = True
    cover.calculate_percentage = MagicMock(return_value=50.0)
    cover.calculate_raw_percentage = MagicMock(return_value=50.0)
    cover.distance = 3.0
    cover.gamma = 0.0
    config = MagicMock()
    config.min_pos = min_pos
    config.max_pos = max_pos
    config.min_pos_sun_only = min_pos_sun_only
    config.max_pos_sun_only = max_pos_sun_only
    config.min_pos_sun_tracking = min_pos_sun_tracking
    cover.config = config
    return cover


def _climate_options(**overrides) -> ClimateOptions:
    kwargs = {
        "temp_low": 18.0,
        "temp_high": 26.0,
        "temp_switch": False,
        "transparent_blind": False,
        "temp_summer_outside": None,
        "cloud_suppression_enabled": False,
        "winter_close_insulation": False,
        "temp_extreme_heat": None,
        "extreme_heat_position": None,
        "tracking_seasons": frozenset(DEFAULT_TRACKING_SEASONS),
    }
    kwargs.update(overrides)
    return ClimateOptions(**kwargs)


class TestDefaultTiltReachesClimateWinner:
    """A LOW_LIGHT climate winner must carry the same tilt DefaultHandler would.

    Reproduces the reporter's own trace (issue #1214): climate mode active,
    no direct sun, presence, intermediate temperature -- ClimateStrategy.LOW_LIGHT
    labels its branch ControlMethod.DEFAULT and outranks DefaultHandler
    (priority 50 vs 0), so it must supply the effective default/sunset tilt
    itself.
    """

    def _registry(self) -> PipelineRegistry:
        return PipelineRegistry([ClimateHandler(), SolarHandler(), DefaultHandler()])

    def test_sunset_active_carries_sunset_tilt(self):
        snap = make_snapshot(
            direct_sun_valid=False,
            climate_mode_enabled=True,
            in_time_window=True,
            climate_readings=_low_light_climate_readings(),
            climate_options=_climate_options(),
            is_sunset_active=True,
            sunset_tilt=0,
            default_tilt=50,
        )
        result = self._registry().evaluate(snap)

        assert result.control_method is ControlMethod.DEFAULT
        winning_step = next(s for s in result.decision_trace if s.matched)
        assert winning_step.handler == "climate"
        assert winning_step.priority == ClimateHandler.priority
        assert result.tilt == 0

    def test_not_sunset_carries_default_tilt(self):
        snap = make_snapshot(
            direct_sun_valid=False,
            climate_mode_enabled=True,
            in_time_window=True,
            climate_readings=_low_light_climate_readings(),
            climate_options=_climate_options(),
            is_sunset_active=False,
            sunset_tilt=0,
            default_tilt=50,
        )
        result = self._registry().evaluate(snap)

        assert result.control_method is ControlMethod.DEFAULT
        winning_step = next(s for s in result.decision_trace if s.matched)
        assert winning_step.handler == "climate"
        assert winning_step.priority == ClimateHandler.priority
        assert result.tilt == 50

    def test_tilt_primary_low_light_via_solar_stays_untilted(self):
        """Issue #1214 finding 2: ControlMethod.DEFAULT labels the branch, not
        the position -- it is not a promise the cover sits at its default.

        On a tilt-primary cover (``cover_tilt``/``cover_louvered_roof``),
        ``ClimateStrategy.LOW_LIGHT`` resolves via ``TILT_WITH_PRESENCE``'s
        ``_solar`` rule (climate_modes.py:346), not ``_default`` -- the same
        ``ControlMethod.DEFAULT`` label the NORMAL-table LOW_LIGHT branch
        carries, but a solar-tracked position, not the effective default one.
        Pairing that with the configured default tilt would silently
        overwrite a genuine solar-tracked slat angle with a stale default.
        The class docstring says exactly this; the tilt is gated on the
        matched rule's PROVENANCE (``ClimateRule.resolves_default_position``,
        False for ``_solar``), not on the DEFAULT label -- so this must stay
        untilted even though default_tilt is configured and the branch is
        labelled DEFAULT, and it stays untilted regardless of what number the
        solar position happens to land on.

        default_tilt/sunset_tilt are only exposed via the UI for
        position-primary policies (VenetianPolicy, DayNightShadePolicy), but
        nothing at the pipeline/snapshot layer or in ``set_option`` gates
        them by cover type -- so this combination, while not reachable
        through the UI today, must not silently misbehave if it occurs.
        """
        snap = make_snapshot(
            cover=_make_low_light_solar_tilt_cover(percentage=70.0),
            cover_type="cover_tilt",
            climate_mode_enabled=True,
            in_time_window=True,
            climate_readings=_low_light_climate_readings(),
            climate_options=_climate_options(),
            is_sunset_active=False,
            default_position=0,
            default_tilt=40,
        )
        result = self._registry().evaluate(snap)

        assert result.control_method is ControlMethod.DEFAULT
        winning_step = next(s for s in result.decision_trace if s.matched)
        assert winning_step.handler == "climate"
        assert winning_step.priority == ClimateHandler.priority
        # Sanity: the winning position is the solar-tracked one (70), not the
        # effective default (0) -- otherwise this test would not exercise the
        # gate it claims to.
        assert result.position != 0
        assert result.tilt is None

    def test_clamped_sunset_position_still_carries_sunset_tilt(self):
        """Round-2 finding 1: limit clamping must not disqualify the tilt.

        ``compute_default_position`` bypasses the min/max limits during sunset
        (#128), but ``ClimateCoverState.get_state`` runs every answer through
        ``apply_snapshot_limits`` — so with ``min_pos=20`` and
        ``sunset_position=0`` the climate winner sits at 20 while the effective
        default reads 0. The position was still RESOLVED FROM the default
        (``climate_modes._default``); a clamp applied afterwards does not make
        it something else, so the sunset tilt must still ride along. This is
        the originally reported defect (#1214) for every install whose limits
        move the sunset position.
        """
        snap = make_snapshot(
            cover=_make_limited_cover(min_pos=20),
            climate_mode_enabled=True,
            in_time_window=True,
            climate_readings=_low_light_climate_readings(),
            climate_options=_climate_options(),
            is_sunset_active=True,
            default_position=0,
            sunset_tilt=0,
            default_tilt=50,
        )
        result = self._registry().evaluate(snap)

        assert result.control_method is ControlMethod.DEFAULT
        winning_step = next(s for s in result.decision_trace if s.matched)
        assert winning_step.handler == "climate"
        # The clamp genuinely moved the answer off the effective default —
        # otherwise this test would not exercise what it claims to.
        assert compute_default_position(snap) == 0
        assert result.position == 20
        assert result.tilt == 0

    def test_sun_tracking_min_floor_still_carries_default_tilt(self):
        """Round-2 finding 1, non-sunset twin: the sun-only floor is a clamp too.

        ``get_state`` clamps with ``sun_valid = direct_sun_valid and is_summer``
        while ``compute_default_position`` always uses ``sun_valid=False``. On a
        hot, cloudy, in-FOV day with ``min_position_sun_tracking=30`` the climate
        LOW_LIGHT answer (still ``_default``) lands at 30 against an effective
        default of 0 — and must still carry ``default_tilt``.
        """
        snap = make_snapshot(
            cover=_make_limited_cover(direct_sun_valid=True, min_pos_sun_tracking=30),
            climate_mode_enabled=True,
            in_time_window=True,
            climate_readings=_low_light_climate_readings(inside_temperature=30.0),
            climate_options=_climate_options(),
            is_sunset_active=False,
            default_position=0,
            default_tilt=50,
        )
        result = self._registry().evaluate(snap)

        assert result.control_method is ControlMethod.DEFAULT
        winning_step = next(s for s in result.decision_trace if s.matched)
        assert winning_step.handler == "climate"
        assert compute_default_position(snap) == 0
        assert result.position == 30
        assert result.tilt == 50

    def test_tracking_season_gate_carries_default_tilt(self):
        """The other DEFAULT-labelled branch resolves from ``_default`` too.

        With the current season deselected the season-scope gate replaces glare
        tracking with the default position (``_SEASON_GATE`` → ``_default``), so
        it earns the default tilt on the same provenance grounds as LOW_LIGHT.
        """
        snap = make_snapshot(
            direct_sun_valid=True,
            climate_mode_enabled=True,
            in_time_window=True,
            climate_readings=_low_light_climate_readings(is_sunny=True),
            climate_options=_climate_options(tracking_seasons=frozenset()),
            is_sunset_active=False,
            default_position=0,
            default_tilt=50,
        )
        result = self._registry().evaluate(snap)

        assert result.climate_strategy is ClimateStrategy.TRACKING_SEASON_GATE
        assert result.control_method is ControlMethod.DEFAULT
        assert result.tilt == 50


class TestDefaultTiltReachesCloudSuppressionWinner:
    """CloudSuppressionHandler must carry the effective default/sunset tilt too."""

    def _registry(self) -> PipelineRegistry:
        return PipelineRegistry([CloudSuppressionHandler(), DefaultHandler()])

    def test_sunset_active_carries_sunset_tilt(self):
        snap = make_snapshot(
            direct_sun_valid=True,
            in_time_window=True,
            climate_readings=_low_light_climate_readings(),
            climate_options=_climate_options(cloud_suppression_enabled=True),
            is_sunset_active=True,
            sunset_tilt=0,
            default_tilt=50,
        )
        result = self._registry().evaluate(snap)

        assert result.control_method is ControlMethod.CLOUD
        winning_step = next(s for s in result.decision_trace if s.matched)
        assert winning_step.handler == "cloud_suppression"
        assert winning_step.priority == CloudSuppressionHandler.priority
        assert result.tilt == 0

    def test_cloudy_position_not_sunset_stays_untilted(self):
        """A cloudy_position winner did NOT resolve from the default position.

        The position here (25) came from the configured cloudy_position, so
        the branch states ``tilt=None``. Pairing it with default_tilt would
        snap a venetian's slats to default_tilt during a cloudy in-FOV
        daytime hold instead of letting them hold -- exactly what #1153
        removed for non-default winners. See
        ``test_cloudy_position_equal_to_default_stays_untilted`` for the same
        rule when the override's *value* coincides with the default.
        """
        snap = make_snapshot(
            direct_sun_valid=True,
            in_time_window=True,
            climate_readings=_low_light_climate_readings(),
            climate_options=_climate_options(
                cloud_suppression_enabled=True, cloudy_position=25
            ),
            is_sunset_active=False,
            sunset_tilt=0,
            default_tilt=50,
        )
        result = self._registry().evaluate(snap)

        assert result.control_method is ControlMethod.CLOUD
        winning_step = next(s for s in result.decision_trace if s.matched)
        assert winning_step.handler == "cloud_suppression"
        assert winning_step.priority == CloudSuppressionHandler.priority
        assert result.position == 25
        assert result.tilt is None

    def test_cloudy_position_equal_to_default_stays_untilted(self):
        """Round-2 finding 2: a coincidental value match is not provenance.

        ``cloudy_position=0`` alongside a venetian's ``default_percentage=0``
        (the #1214 reporter's own value) makes the cloudy answer numerically
        equal to the effective default. It is still a configured override, so
        the slats must hold — 2026.8.0's behaviour and what #1153 established
        for hold-type winners.
        """
        snap = make_snapshot(
            direct_sun_valid=True,
            in_time_window=True,
            climate_readings=_low_light_climate_readings(),
            climate_options=_climate_options(
                cloud_suppression_enabled=True, cloudy_position=0
            ),
            is_sunset_active=False,
            default_position=0,
            default_tilt=50,
        )
        result = self._registry().evaluate(snap)

        assert result.control_method is ControlMethod.CLOUD
        # The coincidence this test is about: same number, different provenance.
        assert result.position == compute_default_position(snap) == 0
        assert result.tilt is None

    def test_no_cloudy_position_carries_default_tilt(self):
        """With no ``cloudy_position`` configured the branch IS the default."""
        snap = make_snapshot(
            direct_sun_valid=True,
            in_time_window=True,
            climate_readings=_low_light_climate_readings(),
            climate_options=_climate_options(cloud_suppression_enabled=True),
            is_sunset_active=False,
            default_position=0,
            default_tilt=50,
        )
        result = self._registry().evaluate(snap)

        assert result.control_method is ControlMethod.CLOUD
        assert result.tilt == 50


class TestDefaultTiltReachesMotionTimeoutWinner:
    """MotionTimeoutHandler's return_to_default branch must carry the tilt too."""

    def _registry(self) -> PipelineRegistry:
        return PipelineRegistry([MotionTimeoutHandler(), DefaultHandler()])

    def test_return_to_default_carries_default_tilt(self):
        snap = make_snapshot(
            motion_control_enabled=True,
            motion_timeout_active=True,
            motion_timeout_mode="return_to_default",
            default_tilt=50,
        )
        result = self._registry().evaluate(snap)

        assert result.control_method is ControlMethod.MOTION
        winning_step = next(s for s in result.decision_trace if s.matched)
        assert winning_step.handler == "motion_timeout"
        assert winning_step.priority == MotionTimeoutHandler.priority
        assert result.tilt == 50

    def test_hold_position_mode_stays_untilted(self):
        """Regression lock: the hold branch must NOT gain a tilt (skip_command=True).

        Must pass both before and after the fix -- it locks the hold
        carve-out that must never be given a tilt.
        """
        snap = make_snapshot(
            motion_control_enabled=True,
            motion_timeout_active=True,
            motion_timeout_mode="hold_position",
            in_time_window=True,
            direct_sun_valid=True,
            current_cover_position=42,
            default_tilt=50,
        )
        result = self._registry().evaluate(snap)

        assert result.control_method is ControlMethod.MOTION
        assert result.skip_command is True
        assert result.tilt is None
