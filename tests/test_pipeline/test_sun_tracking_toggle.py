"""Tests for the enable_sun_tracking config toggle.

When CONF_ENABLE_SUN_TRACKING is False, only SolarHandler must be absent.
GlareZoneHandler is governed by its own CONF_ENABLE_GLARE_ZONES switch and
must remain in the pipeline regardless of the sun-tracking flag.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import CONF_ENABLE_SUN_TRACKING
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.solar import SolarHandler
from custom_components.adaptive_cover_pro.pipeline.handlers.glare_zone import (
    GlareZoneHandler,
)


def _make_coordinator(options: dict) -> AdaptiveDataUpdateCoordinator:
    """Construct a bare coordinator instance wired with the given options."""
    coord = object.__new__(AdaptiveDataUpdateCoordinator)
    coord.logger = MagicMock()
    coord._toggles = MagicMock()
    config_entry = MagicMock()
    config_entry.options = options
    coord.config_entry = config_entry
    return coord


@pytest.mark.unit
def test_sun_tracking_enabled_by_default():
    """SolarHandler and GlareZoneHandler present when flag is absent (default True)."""
    coord = _make_coordinator({})
    registry = coord._build_pipeline()
    handler_types = {type(h) for h in registry._handlers}
    assert SolarHandler in handler_types
    assert GlareZoneHandler in handler_types


@pytest.mark.unit
def test_sun_tracking_enabled_explicitly():
    """SolarHandler and GlareZoneHandler present when flag is explicitly True."""
    coord = _make_coordinator({CONF_ENABLE_SUN_TRACKING: True})
    registry = coord._build_pipeline()
    handler_types = {type(h) for h in registry._handlers}
    assert SolarHandler in handler_types
    assert GlareZoneHandler in handler_types


@pytest.mark.unit
def test_sun_tracking_disabled_removes_solar_handler():
    """SolarHandler absent when CONF_ENABLE_SUN_TRACKING is False."""
    coord = _make_coordinator({CONF_ENABLE_SUN_TRACKING: False})
    registry = coord._build_pipeline()
    handler_types = {type(h) for h in registry._handlers}
    assert SolarHandler not in handler_types


@pytest.mark.unit
def test_sun_tracking_disabled_preserves_other_handlers():
    """Other handlers (ForceOverride, Climate, Default, etc.) remain when flag is False."""
    from custom_components.adaptive_cover_pro.pipeline.handlers import (
        DefaultHandler,
        ClimateHandler,
        ForceOverrideHandler,
        ManualOverrideHandler,
    )

    coord = _make_coordinator({CONF_ENABLE_SUN_TRACKING: False})
    registry = coord._build_pipeline()
    handler_types = {type(h) for h in registry._handlers}
    assert DefaultHandler in handler_types
    assert ClimateHandler in handler_types
    assert ForceOverrideHandler in handler_types
    assert ManualOverrideHandler in handler_types


@pytest.mark.unit
def test_sun_tracking_disabled_preserves_glare_zone_handler():
    """GlareZoneHandler stays in the pipeline when sun tracking is off.

    Regression test for issue #238: CONF_ENABLE_SUN_TRACKING must gate only
    SolarHandler. GlareZoneHandler is governed by CONF_ENABLE_GLARE_ZONES
    and self-gates on cover type / time window / zone presence.
    """
    coord = _make_coordinator({CONF_ENABLE_SUN_TRACKING: False})
    registry = coord._build_pipeline()
    handler_types = {type(h) for h in registry._handlers}
    assert GlareZoneHandler in handler_types
    assert SolarHandler not in handler_types


@pytest.mark.unit
def test_glare_zone_handler_present_regardless_of_sun_tracking():
    """GlareZoneHandler presence is independent of CONF_ENABLE_SUN_TRACKING."""
    for flag in (True, False):
        coord = _make_coordinator({CONF_ENABLE_SUN_TRACKING: flag})
        registry = coord._build_pipeline()
        handler_types = {type(h) for h in registry._handlers}
        assert GlareZoneHandler in handler_types, (
            f"GlareZoneHandler missing when CONF_ENABLE_SUN_TRACKING={flag}"
        )


@pytest.mark.unit
def test_sun_tracking_disabled_pipeline_falls_through_to_default():
    """With sun tracking off and sun in FOV, pipeline result comes from DefaultHandler."""
    from custom_components.adaptive_cover_pro.enums import ControlMethod
    from tests.test_pipeline.conftest import make_snapshot

    coord = _make_coordinator({CONF_ENABLE_SUN_TRACKING: False})
    registry = coord._build_pipeline()

    # Sun is valid — would normally trigger SolarHandler. GlareZoneHandler is
    # still present (issue #238) but self-gates on glare-zone config, so with
    # none configured it returns None and control falls through to DefaultHandler.
    snap = make_snapshot(direct_sun_valid=True, calculate_percentage_return=60.0)
    result = registry.evaluate(snap)

    assert result is not None
    assert result.control_method == ControlMethod.DEFAULT


@pytest.mark.unit
def test_force_override_min_mode_with_sun_tracking_off_uses_default():
    """Force override min-mode floors against default position when sun tracking is off.

    End-to-end regression test for #264: with tracking off (default=100) and
    a geometrically valid sun (solar=29), the min-mode floor of 80 must resolve
    to max(80, 100)=100, not max(80, 29)=80.
    """
    from custom_components.adaptive_cover_pro.enums import ControlMethod
    from tests.test_pipeline.conftest import make_snapshot

    coord = _make_coordinator({CONF_ENABLE_SUN_TRACKING: False})
    registry = coord._build_pipeline()

    snap = make_snapshot(
        direct_sun_valid=True,
        calculate_percentage_return=29.0,
        default_position=100,
        force_override_sensors={"binary_sensor.wind": True},
        force_override_position=80,
        force_override_min_mode=True,
        enable_sun_tracking=False,
    )
    result = registry.evaluate(snap)

    assert result is not None
    assert result.control_method == ControlMethod.FORCE
    assert result.position == 100  # max(80, default=100) = 100
