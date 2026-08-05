"""Verify CoverTypePolicy.build_calc_engine returns the right concrete engine.

After PR #33's refactor, the coordinator no longer branches on cover-type
strings — it routes through ``self._policy.build_calc_engine(...)``. These
tests pin the dispatch table so a future policy refactor can't silently
mis-route a cover type to the wrong calc engine.
"""

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.config_types import (
    CoverConfig,
    HorizontalConfig,
    TiltConfig,
    VerticalConfig,
)
from custom_components.adaptive_cover_pro.cover_types import (
    POLICY_REGISTRY,
    AwningPolicy,
    BlindPolicy,
    CoverTypePolicy,
    TiltPolicy,
    VenetianPolicy,
    get_policy,
)
from custom_components.adaptive_cover_pro.engine.covers import (
    AdaptiveHorizontalCover,
    AdaptiveTiltCover,
    AdaptiveVerticalCover,
)
from custom_components.adaptive_cover_pro.const import ControlMethod, TiltMode
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult


def _common_cover_config() -> CoverConfig:
    return CoverConfig(
        win_azi=180,
        fov_left=90,
        fov_right=90,
        h_def=0,
        sunset_pos=None,
        sunset_off=0,
        sunrise_off=0,
        max_pos=100,
        min_pos=0,
        max_pos_sun_only=False,
        min_pos_sun_only=False,
        blind_spot_left=None,
        blind_spot_right=None,
        blind_spot_elevation=None,
        blind_spot_on=False,
        min_elevation=None,
        max_elevation=None,
    )


@pytest.fixture
def fake_config_service():
    svc = MagicMock()
    svc.get_vertical_data.return_value = VerticalConfig(distance=0.5, h_win=2.0)
    svc.get_horizontal_data.return_value = HorizontalConfig(
        awn_length=2.0, awn_angle=0.0
    )
    svc.get_tilt_data.return_value = TiltConfig(
        slat_distance=0.03, depth=0.02, mode=TiltMode.MODE1
    )
    svc.get_glare_zones_config.return_value = None
    return svc


@pytest.fixture
def calc_kwargs(mock_sun_data, mock_logger, fake_config_service):
    return {
        "logger": mock_logger,
        "sol_azi": 180.0,
        "sol_elev": 45.0,
        "sun_data": mock_sun_data,
        "config": _common_cover_config(),
        "config_service": fake_config_service,
        "options": {},
    }


@pytest.mark.unit
class TestRegistry:
    """Verify the policy registry maps cover-type strings to the right class."""

    def test_blind_policy_registered(self):
        assert POLICY_REGISTRY["cover_blind"] is BlindPolicy

    def test_awning_policy_registered(self):
        assert POLICY_REGISTRY["cover_awning"] is AwningPolicy

    def test_tilt_policy_registered(self):
        assert POLICY_REGISTRY["cover_tilt"] is TiltPolicy

    def test_venetian_policy_registered(self):
        assert POLICY_REGISTRY["cover_venetian"] is VenetianPolicy

    def test_get_policy_returns_instance(self):
        assert isinstance(get_policy("cover_blind"), BlindPolicy)
        assert isinstance(get_policy("cover_awning"), AwningPolicy)
        assert isinstance(get_policy("cover_tilt"), TiltPolicy)
        assert isinstance(get_policy("cover_venetian"), VenetianPolicy)

    def test_get_policy_raises_for_unknown(self):
        with pytest.raises(ValueError, match="Unsupported cover type"):
            get_policy("cover_nonexistent")

    def test_get_policy_raises_for_none(self):
        with pytest.raises(ValueError, match="Unsupported cover type"):
            get_policy(None)

    def test_all_subclasses_implement_required_methods(self):
        for policy_cls in POLICY_REGISTRY.values():
            assert issubclass(policy_cls, CoverTypePolicy)
            assert hasattr(policy_cls, "cover_type")
            assert hasattr(policy_cls, "build_calc_engine")


@pytest.mark.unit
class TestBuildCalcEngine:
    """``build_calc_engine`` returns the right concrete cover for each policy."""

    def test_blind_returns_vertical_cover(self, calc_kwargs):
        engine = BlindPolicy().build_calc_engine(**calc_kwargs)
        assert isinstance(engine, AdaptiveVerticalCover)

    def test_blind_threads_glare_zones_when_set(self, calc_kwargs, fake_config_service):
        from custom_components.adaptive_cover_pro.config_types import GlareZonesConfig

        zones = GlareZonesConfig(zones=[], window_width=1.5)
        fake_config_service.get_glare_zones_config.return_value = zones
        engine = BlindPolicy().build_calc_engine(**calc_kwargs)
        assert engine.vert_config.glare_zones is zones

    def test_awning_returns_horizontal_cover(self, calc_kwargs):
        engine = AwningPolicy().build_calc_engine(**calc_kwargs)
        assert isinstance(engine, AdaptiveHorizontalCover)

    def test_tilt_returns_tilt_cover(self, calc_kwargs):
        engine = TiltPolicy().build_calc_engine(**calc_kwargs)
        assert isinstance(engine, AdaptiveTiltCover)

    def test_venetian_uses_vertical_cover_for_position(self, calc_kwargs):
        # Position is resolved with the same vertical math as cover_blind;
        # tilt is filled in post_pipeline_resolve (step 4).
        engine = VenetianPolicy().build_calc_engine(**calc_kwargs)
        assert isinstance(engine, AdaptiveVerticalCover)


@pytest.mark.unit
class TestDefaultHooks:
    """Default hook implementations on ``CoverTypePolicy`` are no-ops."""

    @pytest.mark.parametrize("policy_cls", [BlindPolicy, AwningPolicy, TiltPolicy])
    def test_post_pipeline_resolve_is_identity(self, policy_cls, calc_kwargs):
        result = MagicMock()
        out = policy_cls().post_pipeline_resolve(result, **calc_kwargs)
        assert out is result

    @pytest.mark.parametrize("policy_cls", [BlindPolicy, AwningPolicy])
    @pytest.mark.parametrize(
        ("position", "expect"),
        [(0, True), (100, True), (50, False), (30, False)],
    )
    def test_single_axis_position_endpoint_sets_full_endpoint_flag(
        self, policy_cls, position, expect
    ):
        """Single-axis position covers flag 0/100 as a full mechanical endpoint.

        Issue #897 — the base policy generalizes the #755 venetian bypass to any
        position-capable cover so ``endpoint_use_open_close`` routes 0→close_cover
        and 100→open_cover instead of being swallowed as same_position.
        """
        result = PipelineResult(
            position=position, control_method=ControlMethod.SOLAR, reason="t"
        )
        assert (
            policy_cls()
            .position_context_overrides(result)
            .get("full_endpoint_target", False)
            is expect
        )

    @pytest.mark.parametrize("position", [0, 100, 50])
    def test_tilt_only_never_sets_full_endpoint_flag(self, position):
        """Tilt-only covers have no position axis, so never flag a full endpoint."""
        result = PipelineResult(
            position=position, control_method=ControlMethod.SOLAR, reason="t"
        )
        assert (
            TiltPolicy()
            .position_context_overrides(result)
            .get("full_endpoint_target", False)
            is False
        )

    def test_targets_full_mechanical_endpoint_predicate(self):
        """The predicate is True at 0/100 for position covers, always False for tilt."""
        blind = BlindPolicy()
        assert (
            blind.targets_full_mechanical_endpoint(
                PipelineResult(
                    position=0, control_method=ControlMethod.SOLAR, reason="t"
                )
            )
            is True
        )
        assert (
            blind.targets_full_mechanical_endpoint(
                PipelineResult(
                    position=100, control_method=ControlMethod.SOLAR, reason="t"
                )
            )
            is True
        )
        assert (
            blind.targets_full_mechanical_endpoint(
                PipelineResult(
                    position=50, control_method=ControlMethod.SOLAR, reason="t"
                )
            )
            is False
        )
        tilt = TiltPolicy()
        for position in (0, 100, 50):
            assert (
                tilt.targets_full_mechanical_endpoint(
                    PipelineResult(
                        position=position,
                        control_method=ControlMethod.SOLAR,
                        reason="t",
                    )
                )
                is False
            )

    @pytest.mark.parametrize("policy_cls", [BlindPolicy, AwningPolicy, TiltPolicy])
    def test_secondary_axis_check_none(self, policy_cls):
        assert policy_cls().secondary_axis_check(MagicMock(), MagicMock()) is None

    @pytest.mark.parametrize("policy_cls", [BlindPolicy, AwningPolicy, TiltPolicy])
    @pytest.mark.asyncio
    async def test_after_position_command_returns_none(self, policy_cls):
        out = await policy_cls().after_position_command(
            MagicMock(),
            "cover.x",
            service="set_cover_position",
            position=50,
            context=MagicMock(),
            reason="test",
        )
        assert out is None

    @pytest.mark.asyncio
    async def test_venetian_after_position_command_no_op_without_attach(self):
        """Without ``attach()``, VenetianPolicy.after_position_command is a no-op."""
        out = await VenetianPolicy().after_position_command(
            MagicMock(),
            "cover.x",
            service="set_cover_position",
            position=50,
            context=MagicMock(tilt=80),
            reason="test",
        )
        assert out is None
