"""Shared coordinator stub for behavioural ``build_diagnostic_data`` tests.

A ``MagicMock(spec=AdaptiveDataUpdateCoordinator)`` carrying every seam
``build_diagnostic_data`` reads, with the REAL method bound on top — so the
code under test is the shipped code, not a re-derivation of it. Mirrors the
full-method-bind pattern in
``tests/test_position_explanation.py``'s ``_make_coordinator_mock`` and the
single-method-bind pattern in ``tests/test_coordinator_solar_transmittance.py``'s
``_coordinator``.

Used by both the irradiance-unit-gate behavioural tests
(``tests/test_coordinator_solar_gain.py``, issue #1280) and the real-chain
estimate tests (``tests/test_sensor_solar_gain.py``) so neither file has to
hand-roll its own copy of this stub, or re-derive the coordinator's own
``irradiance_unit_ok`` gate formula to exercise it.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, PropertyMock

from custom_components.adaptive_cover_pro.const import (
    CONF_IRRADIANCE_ENTITY,
    ControlMethod,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.diagnostics.builder import DiagnosticsBuilder
from custom_components.adaptive_cover_pro.diagnostics.event_buffer import EventBuffer
from custom_components.adaptive_cover_pro.engine.solar_gain import GlassArea
from custom_components.adaptive_cover_pro.pipeline.types import PipelineResult

_DEFAULT_GLASS_AREA = GlassArea(3.0, "derived")


def _default_cover() -> SimpleNamespace:
    """Build a minimal cover stub carrying every attribute the builder reads directly.

    Mirrors ``tests/test_position_explanation.py``'s ``_make_cover()`` — the
    builder accesses several of these (``valid``, ``valid_elevation``,
    ``control_state_reason``) without a ``getattr`` default, so a bare
    ``SimpleNamespace()`` raises ``AttributeError`` partway through ``build()``.
    """
    return SimpleNamespace(
        gamma=10.0,
        valid=True,
        valid_elevation=True,
        is_sun_in_blind_spot=False,
        direct_sun_valid=True,
        sunset_valid=False,
        sunset_pos=None,
        default=50.0,
        control_state_reason="Sun in FOV",
    )


def make_diagnostic_coordinator(
    *,
    climate_provider: Any,
    weather_readings: Any,
    irradiance_entity: str | None,
    cover_type: str = "cover_blind",
    glass_area: GlassArea | None = None,
) -> MagicMock:
    """Build a coordinator stub that can run the REAL ``build_diagnostic_data``.

    ``climate_provider`` and ``weather_readings`` are the two seams a caller
    typically varies: a stubbed ``MagicMock`` climate provider (and a bare
    ``ClimateReadings``) for gate-boolean tests, or a real ``ClimateProvider``
    bound to a mock ``hass`` (and its own ``.read()`` output) for end-to-end
    tests that also exercise the HA state read.

    ``glass_area`` and ``solar_transmittance`` are pinned to a fixed derived
    area and "feature off" respectively — this stub is for exercising the
    irradiance-unit gate, not the area/transmittance resolution paths, which
    have their own dedicated coordinator tests.
    """
    coord = MagicMock(spec=AdaptiveDataUpdateCoordinator)
    coord._diagnostics_builder = DiagnosticsBuilder()
    coord._last_position_explanation = ""
    # Issue #1280 Fix 4's one-shot warning state, plus the real (bound) method
    # that reads/writes it — left auto-mocked, the spec'd MagicMock would
    # silently no-op the warning instead of running the shipped logic.
    coord._irradiance_unit_warned = None
    coord._warn_on_unsupported_irradiance_unit = (
        AdaptiveDataUpdateCoordinator._warn_on_unsupported_irradiance_unit.__get__(
            coord
        )
    )
    coord._reason_labels = None
    coord.logger = MagicMock()
    coord.pos_sun = [180.0, 45.0]
    coord._cover_data = _default_cover()
    coord._position_forecast = None
    coord._climate_mode = False
    coord._weather_readings = weather_readings
    coord._pipeline_result = PipelineResult(
        position=50,
        control_method=ControlMethod.SOLAR,
        reason="sun in FOV — position 50%",
        raw_calculated_position=50,
    )
    type(coord).check_adaptive_time = PropertyMock(return_value=True)
    type(coord).after_start_time = PropertyMock(return_value=True)
    type(coord).before_end_time = PropertyMock(return_value=True)
    coord._time_mgr = MagicMock()
    coord._time_mgr.start_time_value = None
    type(coord).automatic_control = PropertyMock(return_value=True)
    type(coord).last_cover_action = PropertyMock(return_value={})
    type(coord).last_skipped_action = PropertyMock(return_value={})
    coord.min_change = 5
    coord.time_threshold = 2
    coord._toggles = MagicMock()
    coord._toggles.switch_mode = False
    coord._inverse_state = False
    coord._use_interpolation = False
    type(coord).state = PropertyMock(return_value=50)
    coord.config_entry = MagicMock()
    coord.config_entry.options = (
        {CONF_IRRADIANCE_ENTITY: irradiance_entity}
        if irradiance_entity is not None
        else {}
    )
    coord._resolved_options = {}
    coord._climate_provider = climate_provider
    coord.hass = MagicMock()
    coord.hass.config_entries.async_entries.return_value = []
    coord.hass.states.get.return_value = None
    type(coord).is_motion_detected = PropertyMock(return_value=True)
    coord._motion_mgr = MagicMock()
    coord._motion_mgr._motion_timeout_active = False
    coord._event_buffer = EventBuffer(maxlen=50)
    coord.manager = MagicMock()
    coord.manager.covers = set()
    coord.manager.manual_control = {}
    coord.manager.manual_control_time = {}
    coord.manager.reset_duration = timedelta(hours=2)
    coord._cmd_svc = MagicMock()
    coord._cmd_svc.get_all_entity_state_snapshots.return_value = {}
    coord.entities = []
    coord._cover_provider = MagicMock()
    coord._cover_provider.read_positions.return_value = {}
    coord._cover_provider.read_all_capabilities.return_value = {}
    coord._cover_type = cover_type
    coord._policy = get_policy(cover_type)
    coord.last_update_success = True
    coord.last_exception = None
    coord._last_update_success_time = None
    coord.update_interval = None
    coord.glass_area = MagicMock(
        return_value=glass_area if glass_area is not None else _DEFAULT_GLASS_AREA
    )
    coord.solar_transmittance = MagicMock(return_value=None)

    coord.build_diagnostic_data = (
        AdaptiveDataUpdateCoordinator.build_diagnostic_data.__get__(coord)
    )
    return coord
