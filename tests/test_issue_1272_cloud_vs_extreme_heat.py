"""Issue #1272 — cloud suppression must defer to an active extreme-heat hold.

``CloudSuppressionHandler`` (priority 60) and ``ClimateHandler`` (priority 50)
evaluate independently every cycle. While the sun is still in the window's FOV
and a cloud/low-light trigger is latched, cloud suppression used to win the
pipeline and command the default/cloudy position — opening the cover — even
though ``ClimateCoverData.is_extreme_heat`` was also true that same cycle; the
EXTREME_HEAT rule was only marked ``REGISTRY_OUTPRIORITIZED`` in the trace,
never consulted. ~15 minutes later the sun leaves the FOV, cloud suppression's
own ``direct_sun_valid`` guard (#417) makes it return ``None``, and
``ClimateHandler`` becomes the top match — its extreme-heat force-hold closes
the cover again. Net effect: a spurious open window at the hottest part of the
day, right when the room is still taking direct sun.

The fix (see ``PipelineSnapshot.climate_extreme_heat_active`` and
``CloudSuppressionHandler.evaluate``) is a narrow carve-out: cloud suppression
defers — returns ``None`` — only when climate would independently produce
EXTREME_HEAT this same cycle, so ``ClimateHandler`` wins on both sides of the
FOV boundary and the commanded position no longer flip-flops.

Modeled on ``tests/test_issue_1238_cloud_hold_vs_climate.py``.
"""

from __future__ import annotations

from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers import (
    ClimateHandler,
    DefaultHandler,
    SolarHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.cloud_suppression import (
    CloudSuppressionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import ClimateOptions
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings
from tests.test_pipeline.conftest import make_snapshot

DEFAULT_POS = 80  # what cloud suppression would command (opening the cover)
EXTREME_HEAT_POS = 0  # the configured extreme-heat hold position
SOLAR_POS = 45


def _full_pipeline() -> PipelineRegistry:
    """Build the reporter's install: cloud → climate → solar → default, in order."""
    return PipelineRegistry(
        [
            CloudSuppressionHandler(),
            ClimateHandler(),
            SolarHandler(),
            DefaultHandler(),
        ]
    )


def _readings() -> ClimateReadings:
    """Hot outside, intermediate inside (neither summer nor winter), not sunny.

    ``is_sunny=False`` is the cloud-suppression trigger; ``outside_temperature``
    is above the extreme-heat threshold.
    """
    return ClimateReadings(
        outside_temperature=40.0,
        inside_temperature=22.0,
        is_presence=True,
        is_sunny=False,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        cloud_coverage_above_threshold=False,
    )


def _options() -> ClimateOptions:
    return ClimateOptions(
        temp_low=18.0,
        temp_high=26.0,
        temp_switch=False,
        transparent_blind=False,
        temp_summer_outside=None,
        cloud_suppression_enabled=True,
        winter_close_insulation=False,
        temp_extreme_heat=35.0,
        extreme_heat_position=EXTREME_HEAT_POS,
    )


def _snapshot(*, direct_sun_valid: bool):
    return make_snapshot(
        direct_sun_valid=direct_sun_valid,
        calculate_percentage_return=SOLAR_POS,
        default_position=DEFAULT_POS,
        climate_mode_enabled=True,
        climate_readings=_readings(),
        climate_options=_options(),
        cloud_suppression_active=True,
        climate_extreme_heat_active=True,
    )


class TestCloudDefersToExtremeHeat:
    """The reporter's scenario: extreme heat active on both sides of the FOV edge."""

    def test_climate_wins_while_sun_is_still_in_fov(self) -> None:
        """Sun in FOV + cloud suppression latched + extreme heat active → EXTREME_HEAT wins.

        Before the fix this resolved to ControlMethod.CLOUD (the default/open
        position) — exactly the reported bug.
        """
        result = _full_pipeline().evaluate(_snapshot(direct_sun_valid=True))
        assert result.control_method == ControlMethod.EXTREME_HEAT
        assert result.position == EXTREME_HEAT_POS

    def test_climate_still_wins_once_sun_leaves_fov_with_the_same_position(
        self,
    ) -> None:
        """Sun outside FOV (#417 makes cloud return None) → same winner, same position.

        This is the actual user-visible complaint: the commanded position must
        not flip-flop across the FOV boundary. Before the fix, the FOV-in case
        above commanded ``DEFAULT_POS`` while this FOV-out case already
        commanded ``EXTREME_HEAT_POS`` — a spurious ~15-minute open window.
        """
        result = _full_pipeline().evaluate(_snapshot(direct_sun_valid=False))
        assert result.control_method == ControlMethod.EXTREME_HEAT
        assert result.position == EXTREME_HEAT_POS

    def test_position_is_identical_across_the_fov_boundary(self) -> None:
        """Direct pin: the two FOV states must agree, closing the reported gap."""
        in_fov = _full_pipeline().evaluate(_snapshot(direct_sun_valid=True))
        out_fov = _full_pipeline().evaluate(_snapshot(direct_sun_valid=False))
        assert (
            in_fov.control_method
            == out_fov.control_method
            == ControlMethod.EXTREME_HEAT
        )
        assert in_fov.position == out_fov.position == EXTREME_HEAT_POS
