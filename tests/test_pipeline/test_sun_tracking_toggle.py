"""Tests for the enable_sun_tracking config toggle.

When CONF_ENABLE_SUN_TRACKING is False, only solar positioning must stop.
GlareZoneHandler is governed by its own CONF_ENABLE_GLARE_ZONES switch and
must keep running regardless of the sun-tracking flag.

Since issue #1167 the toggle no longer removes ``SolarHandler`` from the
pipeline: it folds into ``snapshot.enable_sun_tracking`` alongside the
sun-tracking gate, and the handler declines on that field. Composition-gating
could not express the gate, which is a per-cycle verdict rather than a
build-time value. The observable behaviour — solar does not win, the chain falls
through — is unchanged, and the tests below assert that rather than the
mechanism. The snapshots must therefore carry ``enable_sun_tracking=False``
alongside the option, which is exactly what ``PipelineSnapshotBuilder`` does in
production.
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
def test_sun_tracking_disabled_makes_solar_decline():
    """Solar produces nothing when tracking is off — it stays in the chain and skips.

    Pre-#1167 this asserted the handler was absent from the pipeline. The
    behaviour under test is "solar does not position the cover", which is what is
    asserted now; the handler's presence is an implementation detail, and keeping
    it present is what gives the decision trace a reason to show.
    """
    from tests.test_pipeline.conftest import make_snapshot

    coord = _make_coordinator({CONF_ENABLE_SUN_TRACKING: False})
    registry = coord._build_pipeline()
    handler_types = {type(h) for h in registry._handlers}
    assert SolarHandler in handler_types

    snap = make_snapshot(direct_sun_valid=True, enable_sun_tracking=False)
    assert SolarHandler().evaluate(snap) is None


@pytest.mark.unit
def test_sun_tracking_disabled_preserves_other_handlers():
    """Other handlers (Weather, Climate, Default, etc.) remain when flag is False."""
    from custom_components.adaptive_cover_pro.pipeline.handlers import (
        DefaultHandler,
        ClimateHandler,
        ManualOverrideHandler,
        WeatherOverrideHandler,
    )

    coord = _make_coordinator({CONF_ENABLE_SUN_TRACKING: False})
    registry = coord._build_pipeline()
    handler_types = {type(h) for h in registry._handlers}
    assert DefaultHandler in handler_types
    assert ClimateHandler in handler_types
    assert WeatherOverrideHandler in handler_types
    assert ManualOverrideHandler in handler_types


@pytest.mark.unit
def test_sun_tracking_disabled_preserves_glare_zone_handler():
    """GlareZoneHandler stays in the pipeline when sun tracking is off.

    Regression test for issue #238: CONF_ENABLE_SUN_TRACKING must gate only
    solar positioning. GlareZoneHandler is governed by CONF_ENABLE_GLARE_ZONES
    and self-gates on cover type / time window / zone presence.
    """
    from tests.test_pipeline.conftest import make_snapshot

    coord = _make_coordinator({CONF_ENABLE_SUN_TRACKING: False})
    registry = coord._build_pipeline()
    handler_types = {type(h) for h in registry._handlers}
    assert GlareZoneHandler in handler_types
    # Solar is present but inert — #238's point is that glare zones keep working,
    # not that a particular handler object is missing from the list.
    snap = make_snapshot(direct_sun_valid=True, enable_sun_tracking=False)
    assert SolarHandler().evaluate(snap) is None


@pytest.mark.unit
def test_glare_zone_handler_present_regardless_of_sun_tracking():
    """GlareZoneHandler presence is independent of CONF_ENABLE_SUN_TRACKING."""
    for flag in (True, False):
        coord = _make_coordinator({CONF_ENABLE_SUN_TRACKING: flag})
        registry = coord._build_pipeline()
        handler_types = {type(h) for h in registry._handlers}
        assert (
            GlareZoneHandler in handler_types
        ), f"GlareZoneHandler missing when CONF_ENABLE_SUN_TRACKING={flag}"


@pytest.mark.unit
def test_sun_tracking_disabled_pipeline_falls_through_to_default():
    """With sun tracking off and sun in FOV, pipeline result comes from DefaultHandler."""
    from custom_components.adaptive_cover_pro.const import ControlMethod
    from tests.test_pipeline.conftest import make_snapshot

    coord = _make_coordinator({CONF_ENABLE_SUN_TRACKING: False})
    registry = coord._build_pipeline()

    # Sun is valid — would normally trigger SolarHandler. GlareZoneHandler is
    # still present (issue #238) but self-gates on glare-zone config, so with
    # none configured it returns None and control falls through to DefaultHandler.
    # enable_sun_tracking mirrors the option onto the snapshot the way
    # PipelineSnapshotBuilder._resolve_sun_tracking does in production (#1167);
    # before that the option reached the pipeline by composition alone.
    snap = make_snapshot(
        direct_sun_valid=True,
        calculate_percentage_return=60.0,
        enable_sun_tracking=False,
    )
    result = registry.evaluate(snap)

    assert result is not None
    assert result.control_method == ControlMethod.DEFAULT


@pytest.mark.unit
def test_sun_tracking_disabled_pipeline_allows_glare_zone_to_win():
    """With Solar removed, active glare zones compare against Default."""
    from custom_components.adaptive_cover_pro.config_types import (
        GlareZone,
        GlareZonesConfig,
    )
    from custom_components.adaptive_cover_pro.const import ControlMethod
    from custom_components.adaptive_cover_pro.engine.covers.vertical import (
        AdaptiveVerticalCover,
    )
    from tests.test_pipeline.conftest import make_snapshot

    coord = _make_coordinator({CONF_ENABLE_SUN_TRACKING: False})
    registry = coord._build_pipeline()

    cover = MagicMock(spec=AdaptiveVerticalCover)
    cover.direct_sun_valid = True
    cover.distance = 0.0
    cover.gamma = 0.0
    cover.sol_elev = 45.0
    cover.calculate_percentage = MagicMock(return_value=25.0)
    cover.calculate_raw_percentage = MagicMock(return_value=25.0)
    cover.config = MagicMock()
    cover.config.min_pos = None
    cover.config.max_pos = None
    cover.config.min_pos_sun_only = False
    cover.config.max_pos_sun_only = False
    cover.config.min_pos_sun_tracking = None

    snap = make_snapshot(
        cover=cover,
        direct_sun_valid=True,
        default_position=100,
        glare_zones=GlareZonesConfig(
            zones=[GlareZone(name="desk", x=0.0, y=1.0, radius=0.0)],
            window_width=2.0,
        ),
        active_zone_names={"desk"},
        enable_sun_tracking=False,
    )
    result = registry.evaluate(snap)

    assert result is not None
    assert result.control_method == ControlMethod.GLARE_ZONE
    assert result.position == 25


@pytest.mark.unit
def test_safety_slot_min_mode_with_sun_tracking_off_uses_default():
    """A priority-100 safety slot (migrated force override, #563) defers in
    min_mode; with sun tracking off, the winner is DefaultHandler
    (position=100). The floor of 80 is below the default, so no clamp is
    applied. End-to-end regression test for #264 + #463.
    """
    from custom_components.adaptive_cover_pro.const import (
        CUSTOM_POSITION_SAFETY_PRIORITY,
        CUSTOM_POSITION_SLOTS,
        ControlMethod,
    )
    from custom_components.adaptive_cover_pro.pipeline.types import (
        CustomPositionSensorState,
    )
    from tests.test_pipeline.conftest import make_snapshot

    slot5 = CUSTOM_POSITION_SLOTS[5]
    coord = _make_coordinator(
        {
            CONF_ENABLE_SUN_TRACKING: False,
            slot5["sensors"]: ["binary_sensor.wind"],
            slot5["position"]: 80,
            slot5["priority"]: CUSTOM_POSITION_SAFETY_PRIORITY,
            slot5["min_mode"]: True,
        }
    )
    registry = coord._build_pipeline()

    snap = make_snapshot(
        direct_sun_valid=True,
        calculate_percentage_return=29.0,
        default_position=100,
        custom_position_sensors=[
            CustomPositionSensorState(
                entity_ids=("binary_sensor.wind",),
                is_on=True,
                position=80,
                priority=CUSTOM_POSITION_SAFETY_PRIORITY,
                min_mode=True,
                use_my=False,
                slot=5,
                active_entity_ids=("binary_sensor.wind",),
            )
        ],
        enable_sun_tracking=False,
    )
    result = registry.evaluate(snap)

    assert result is not None
    assert result.control_method == ControlMethod.DEFAULT
    assert result.position == 100  # default beats the floor 80
    # No clamp step because the floor is below the winner.
    assert not any(
        s.handler == "floor_clamp" and s.matched for s in result.decision_trace
    )
