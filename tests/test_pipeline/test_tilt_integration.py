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

from custom_components.adaptive_cover_pro.const import DEFAULT_CUSTOM_POSITION_PRIORITY
from custom_components.adaptive_cover_pro.enums import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.custom_position import (
    CustomPositionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
    DefaultHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.solar import SolarHandler
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import (
    CustomPositionSensorState,
    PipelineResult,
)
from tests.test_pipeline.conftest import make_snapshot


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
        entity_id=entity_id,
        is_on=is_on,
        position=position,
        priority=priority,
        min_mode=False,
        use_my=False,
        tilt=tilt,
    )


def _registry_with_custom_tilt(tilt: int | None = None) -> PipelineRegistry:
    """One CustomPositionHandler that carries an explicit tilt (or None)."""
    return PipelineRegistry(
        [
            CustomPositionHandler(
                slot=1,
                entity_id="binary_sensor.scene",
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
