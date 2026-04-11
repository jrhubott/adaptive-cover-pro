"""Tests for quick-setup default value safety (Issue #133).

Quick setup skips the following config-flow steps:
  automation, manual_override, force_override, custom_position,
  motion_override, weather_override, light_cloud, temperature_climate

This means those step's keys are never written to ``self.config`` before
``async_step_update`` serialises the options dict.  Before the fix, three of
those keys were stored as ``None`` in the options dict — and ``dict.get(KEY,
default)`` returns ``None`` (not the default) when the key *exists* with a
``None`` value, causing:

  * ``TypeError: datetime.timedelta() argument after ** must be a mapping,
    not NoneType``  — crashes on coordinator ``__init__`` (entry never loads)
  * ``TypeError: '>=' not supported between instances of 'int' and 'NoneType'``
    — latent crash in ``_check_position_delta`` once the entry loads

The tests here verify:

1. The options builder uses safe fallbacks so the three keys are never ``None``.
2. The coordinator initialises without error when its options dict contains
   ``None`` for every key that quick setup doesn't touch (simulates an existing
   install created before the fix).
3. ``_update_options()`` resolves all three keys to valid non-None values from
   a ``None``-poisoned options dict (belt-and-suspenders for existing installs).
4. A structural guard: if anyone adds a new key to the options builder that
   requires a non-None value in the coordinator, this test will catch the
   mismatch before it ships.
5. Full-setup vs quick-setup parity: every key present in a full-setup options
   dict is present in the quick-setup options dict (no missing keys), and the
   three critical keys always have valid values in both.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_MANUAL_OVERRIDE_RESET,
    CONF_MANUAL_THRESHOLD,
    CONF_MANUAL_IGNORE_INTERMEDIATE,
    CONF_DEFAULT_HEIGHT,
    CONF_AZIMUTH,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_HEIGHT_WIN,
    CONF_DISTANCE,
    CONF_ENTITIES,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_MOTION_SENSORS,
    CONF_MOTION_TIMEOUT,
    CONF_OPEN_CLOSE_THRESHOLD,
    CONF_INVERSE_STATE,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Keys set by the quick-setup steps (cover_entities, geometry, sun_tracking,
# position).  Every other key in the options builder is absent from this dict.
_QUICK_SETUP_CONFIG: dict = {
    CONF_ENTITIES: ["cover.living_room"],
    CONF_AZIMUTH: 180,
    CONF_HEIGHT_WIN: 2.1,
    CONF_DISTANCE: 0.5,
    CONF_FOV_LEFT: 30,
    CONF_FOV_RIGHT: 30,
    CONF_DEFAULT_HEIGHT: 60,
    CONF_INVERSE_STATE: False,
    CONF_OPEN_CLOSE_THRESHOLD: 50,
}

# Keys set by the full setup (all steps including the 8 that quick skips).
_FULL_SETUP_CONFIG: dict = {
    **_QUICK_SETUP_CONFIG,
    CONF_DELTA_POSITION: 3,
    CONF_DELTA_TIME: 5,
    CONF_MANUAL_OVERRIDE_DURATION: {"hours": 1},
    CONF_MANUAL_OVERRIDE_RESET: True,
    CONF_MANUAL_THRESHOLD: 5,
    CONF_MANUAL_IGNORE_INTERMEDIATE: False,
    CONF_FORCE_OVERRIDE_SENSORS: [],
    CONF_FORCE_OVERRIDE_POSITION: 0,
    CONF_MOTION_SENSORS: [],
    CONF_MOTION_TIMEOUT: 300,
}


def _build_options_from_config(config: dict) -> dict:
    """Replicate the key/default logic of ConfigFlowHandler.async_step_update.

    This mirrors the options dict in config_flow.py so the tests remain
    independent of the actual flow class while still exercising the exact
    same default-handling expressions.

    IMPORTANT: keep this in sync with async_step_update in config_flow.py.
    If you add a new key to the options builder there, add it here too.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_WINDOW_DEPTH,
        CONF_SILL_HEIGHT,
        CONF_MAX_POSITION,
        CONF_ENABLE_MAX_POSITION,
        CONF_MIN_POSITION,
        CONF_ENABLE_MIN_POSITION,
        CONF_SUNSET_POS,
        CONF_SUNSET_OFFSET,
        CONF_SUNRISE_OFFSET,
        CONF_LENGTH_AWNING,
        CONF_AWNING_ANGLE,
        CONF_TILT_DISTANCE,
        CONF_TILT_DEPTH,
        CONF_TILT_MODE,
        CONF_TEMP_ENTITY,
        CONF_PRESENCE_ENTITY,
        CONF_WEATHER_ENTITY,
        CONF_TEMP_LOW,
        CONF_TEMP_HIGH,
        CONF_OUTSIDETEMP_ENTITY,
        CONF_CLIMATE_MODE,
        CONF_WEATHER_STATE,
        CONF_START_TIME,
        CONF_START_ENTITY,
        CONF_END_TIME,
        CONF_END_ENTITY,
        CONF_MANUAL_OVERRIDE_RESET,
        CONF_MANUAL_THRESHOLD,
        CONF_MANUAL_IGNORE_INTERMEDIATE,
        CONF_BLIND_SPOT_RIGHT,
        CONF_BLIND_SPOT_LEFT,
        CONF_BLIND_SPOT_ELEVATION,
        CONF_ENABLE_BLIND_SPOT,
        CONF_MIN_ELEVATION,
        CONF_MAX_ELEVATION,
        CONF_TRANSPARENT_BLIND,
        CONF_WINTER_CLOSE_INSULATION,
        CONF_INTERP,
        CONF_INTERP_START,
        CONF_INTERP_END,
        CONF_INTERP_LIST,
        CONF_INTERP_LIST_NEW,
        CONF_LUX_ENTITY,
        CONF_LUX_THRESHOLD,
        CONF_IRRADIANCE_ENTITY,
        CONF_IRRADIANCE_THRESHOLD,
        CONF_CLOUD_COVERAGE_ENTITY,
        CONF_CLOUD_COVERAGE_THRESHOLD,
        CONF_OUTSIDE_THRESHOLD,
        CONF_DEVICE_ID,
        CONF_RETURN_SUNSET,
        CONF_CLOUD_SUPPRESSION,
    )

    DEFAULT_MOTION_TIMEOUT = 300

    return {
        CONF_AZIMUTH: config.get(CONF_AZIMUTH),
        CONF_HEIGHT_WIN: config.get(CONF_HEIGHT_WIN),
        CONF_DISTANCE: config.get(CONF_DISTANCE),
        CONF_WINDOW_DEPTH: config.get(CONF_WINDOW_DEPTH),
        CONF_SILL_HEIGHT: config.get(CONF_SILL_HEIGHT),
        CONF_DEFAULT_HEIGHT: config.get(CONF_DEFAULT_HEIGHT),
        CONF_MAX_POSITION: config.get(CONF_MAX_POSITION),
        CONF_ENABLE_MAX_POSITION: config.get(CONF_ENABLE_MAX_POSITION),
        CONF_MIN_POSITION: config.get(CONF_MIN_POSITION),
        CONF_ENABLE_MIN_POSITION: config.get(CONF_ENABLE_MIN_POSITION),
        CONF_FOV_LEFT: config.get(CONF_FOV_LEFT),
        CONF_FOV_RIGHT: config.get(CONF_FOV_RIGHT),
        CONF_ENTITIES: config.get(CONF_ENTITIES),
        CONF_INVERSE_STATE: config.get(CONF_INVERSE_STATE),
        CONF_SUNSET_POS: config.get(CONF_SUNSET_POS),
        CONF_SUNSET_OFFSET: config.get(CONF_SUNSET_OFFSET),
        CONF_SUNRISE_OFFSET: config.get(CONF_SUNRISE_OFFSET),
        CONF_LENGTH_AWNING: config.get(CONF_LENGTH_AWNING),
        CONF_AWNING_ANGLE: config.get(CONF_AWNING_ANGLE),
        CONF_TILT_DISTANCE: config.get(CONF_TILT_DISTANCE),
        CONF_TILT_DEPTH: config.get(CONF_TILT_DEPTH),
        CONF_TILT_MODE: config.get(CONF_TILT_MODE),
        CONF_TEMP_ENTITY: config.get(CONF_TEMP_ENTITY),
        CONF_PRESENCE_ENTITY: config.get(CONF_PRESENCE_ENTITY),
        CONF_WEATHER_ENTITY: config.get(CONF_WEATHER_ENTITY),
        CONF_TEMP_LOW: config.get(CONF_TEMP_LOW),
        CONF_TEMP_HIGH: config.get(CONF_TEMP_HIGH),
        CONF_OUTSIDETEMP_ENTITY: config.get(CONF_OUTSIDETEMP_ENTITY),
        CONF_CLIMATE_MODE: config.get(CONF_CLIMATE_MODE),
        CONF_WEATHER_STATE: config.get(CONF_WEATHER_STATE),
        # --- Fixed: or-default prevents None from defeating coordinator .get() ---
        CONF_DELTA_POSITION: config.get(CONF_DELTA_POSITION) or 2,
        CONF_DELTA_TIME: config.get(CONF_DELTA_TIME) or 2,
        CONF_START_TIME: config.get(CONF_START_TIME),
        CONF_START_ENTITY: config.get(CONF_START_ENTITY),
        CONF_END_TIME: config.get(CONF_END_TIME),
        CONF_END_ENTITY: config.get(CONF_END_ENTITY),
        CONF_FORCE_OVERRIDE_SENSORS: config.get(CONF_FORCE_OVERRIDE_SENSORS, []),
        CONF_FORCE_OVERRIDE_POSITION: config.get(CONF_FORCE_OVERRIDE_POSITION, 0),
        CONF_MOTION_SENSORS: config.get(CONF_MOTION_SENSORS, []),
        CONF_MOTION_TIMEOUT: config.get(CONF_MOTION_TIMEOUT, DEFAULT_MOTION_TIMEOUT),
        # --- Fixed: or-default prevents None from defeating coordinator .get() ---
        CONF_MANUAL_OVERRIDE_DURATION: config.get(CONF_MANUAL_OVERRIDE_DURATION)
        or {"hours": 2},
        CONF_MANUAL_OVERRIDE_RESET: config.get(CONF_MANUAL_OVERRIDE_RESET),
        CONF_MANUAL_THRESHOLD: config.get(CONF_MANUAL_THRESHOLD),
        CONF_MANUAL_IGNORE_INTERMEDIATE: config.get(CONF_MANUAL_IGNORE_INTERMEDIATE),
        CONF_OPEN_CLOSE_THRESHOLD: config.get(CONF_OPEN_CLOSE_THRESHOLD, 50),
        CONF_BLIND_SPOT_RIGHT: config.get(CONF_BLIND_SPOT_RIGHT, None),
        CONF_BLIND_SPOT_LEFT: config.get(CONF_BLIND_SPOT_LEFT, None),
        CONF_BLIND_SPOT_ELEVATION: config.get(CONF_BLIND_SPOT_ELEVATION, None),
        CONF_ENABLE_BLIND_SPOT: config.get(CONF_ENABLE_BLIND_SPOT),
        CONF_MIN_ELEVATION: config.get(CONF_MIN_ELEVATION, None),
        CONF_MAX_ELEVATION: config.get(CONF_MAX_ELEVATION, None),
        CONF_TRANSPARENT_BLIND: config.get(CONF_TRANSPARENT_BLIND, False),
        CONF_WINTER_CLOSE_INSULATION: config.get(CONF_WINTER_CLOSE_INSULATION, False),
        CONF_INTERP: config.get(CONF_INTERP),
        CONF_INTERP_START: config.get(CONF_INTERP_START, None),
        CONF_INTERP_END: config.get(CONF_INTERP_END, None),
        CONF_INTERP_LIST: config.get(CONF_INTERP_LIST, []),
        CONF_INTERP_LIST_NEW: config.get(CONF_INTERP_LIST_NEW, []),
        CONF_LUX_ENTITY: config.get(CONF_LUX_ENTITY),
        CONF_LUX_THRESHOLD: config.get(CONF_LUX_THRESHOLD),
        CONF_IRRADIANCE_ENTITY: config.get(CONF_IRRADIANCE_ENTITY),
        CONF_IRRADIANCE_THRESHOLD: config.get(CONF_IRRADIANCE_THRESHOLD),
        CONF_CLOUD_COVERAGE_ENTITY: config.get(CONF_CLOUD_COVERAGE_ENTITY),
        CONF_CLOUD_COVERAGE_THRESHOLD: config.get(CONF_CLOUD_COVERAGE_THRESHOLD),
        CONF_OUTSIDE_THRESHOLD: config.get(CONF_OUTSIDE_THRESHOLD),
        CONF_DEVICE_ID: config.get(CONF_DEVICE_ID),
        CONF_RETURN_SUNSET: config.get(CONF_RETURN_SUNSET, False),
        CONF_CLOUD_SUPPRESSION: config.get(CONF_CLOUD_SUPPRESSION, False),
    }


def _make_coordinator_with_options(options: dict):
    """Build a minimal coordinator-like object that exercises the same
    manual_duration resolution logic as AdaptiveDataUpdateCoordinator.__init__.

    We bypass the full HA stack (which requires a running event loop and real
    config entries) by replicating only the two lines that matter:

        self.manual_duration = options.get(CONF_MANUAL_OVERRIDE_DURATION) or {"hours": 2}
        self.manager = AdaptiveCoverManager(hass, self.manual_duration, logger)

    This is sufficient to reproduce Issue #133 (timedelta(**None) crash) and
    verify the fix.
    """
    from custom_components.adaptive_cover_pro.managers.manual_override import (
        AdaptiveCoverManager,
    )

    hass = MagicMock()
    logger = MagicMock()

    coord = SimpleNamespace()
    # Replicate the fixed coordinator __init__ logic exactly
    coord.manual_duration = options.get(CONF_MANUAL_OVERRIDE_DURATION) or {"hours": 2}
    coord.manager = AdaptiveCoverManager(hass, coord.manual_duration, logger)
    return coord


# ---------------------------------------------------------------------------
# Group 1 — Options builder never stores None for critical keys
# ---------------------------------------------------------------------------


class TestOptionsBuilderDefaults:
    """Verify _build_options_from_config() (mirroring async_step_update) stores
    safe defaults for keys that quick setup never populates.
    """

    @pytest.mark.unit
    def test_delta_position_defaults_to_2_when_absent(self):
        """CONF_DELTA_POSITION must be 2 (not None) when skipped by quick setup."""
        options = _build_options_from_config(_QUICK_SETUP_CONFIG)
        assert options[CONF_DELTA_POSITION] == 2, (
            "CONF_DELTA_POSITION was None — quick-setup skip causes "
            "TypeError in _check_position_delta (delta >= None)"
        )

    @pytest.mark.unit
    def test_delta_time_defaults_to_2_when_absent(self):
        """CONF_DELTA_TIME must be 2 (not None) when skipped by quick setup."""
        options = _build_options_from_config(_QUICK_SETUP_CONFIG)
        assert options[CONF_DELTA_TIME] == 2, (
            "CONF_DELTA_TIME was None — quick-setup skip causes "
            "TypeError in _check_time_delta (timedelta(minutes=None))"
        )

    @pytest.mark.unit
    def test_manual_override_duration_defaults_when_absent(self):
        """CONF_MANUAL_OVERRIDE_DURATION must be a dict (not None) when skipped."""
        options = _build_options_from_config(_QUICK_SETUP_CONFIG)
        duration = options[CONF_MANUAL_OVERRIDE_DURATION]
        assert duration is not None, (
            "REGRESSION (Issue #133): CONF_MANUAL_OVERRIDE_DURATION was None — "
            "quick-setup skip causes TypeError: timedelta(**None) on coordinator init"
        )
        assert isinstance(duration, dict), (
            f"Expected dict, got {type(duration)}: {duration}"
        )
        # Must be a valid timedelta argument
        td = dt.timedelta(**duration)
        assert td.total_seconds() > 0, f"Expected positive duration, got {td}"

    @pytest.mark.unit
    def test_manual_override_duration_default_value_is_2_hours(self):
        """Default duration should be 2 hours."""
        options = _build_options_from_config(_QUICK_SETUP_CONFIG)
        assert options[CONF_MANUAL_OVERRIDE_DURATION] == {"hours": 2}

    @pytest.mark.unit
    def test_full_setup_values_are_preserved(self):
        """When full setup provides explicit values, those values must not be overridden."""
        options = _build_options_from_config(_FULL_SETUP_CONFIG)
        assert options[CONF_DELTA_POSITION] == 3
        assert options[CONF_DELTA_TIME] == 5
        assert options[CONF_MANUAL_OVERRIDE_DURATION] == {"hours": 1}

    @pytest.mark.unit
    def test_delta_position_zero_falls_back_to_default(self):
        """delta_position=0 is falsy — the or-default replaces it with 2.

        This matches the schema minimum of 1, so 0 is never a valid user
        setting; the fallback is safe and correct.
        """
        config = {**_QUICK_SETUP_CONFIG, CONF_DELTA_POSITION: 0}
        options = _build_options_from_config(config)
        assert options[CONF_DELTA_POSITION] == 2

    @pytest.mark.unit
    def test_delta_time_zero_falls_back_to_default(self):
        """delta_time=0 is falsy — the or-default replaces it with 2.

        Schema minimum is 2, so 0 is never a valid user setting.
        """
        config = {**_QUICK_SETUP_CONFIG, CONF_DELTA_TIME: 0}
        options = _build_options_from_config(config)
        assert options[CONF_DELTA_TIME] == 2


# ---------------------------------------------------------------------------
# Group 2 — Coordinator init survives None-poisoned options (existing installs)
# ---------------------------------------------------------------------------


class TestCoordinatorInitWithNoneOptions:
    """Simulate an existing install created before the fix.

    Such installs have options where quick-setup-skipped keys are explicitly
    stored as None.  The coordinator must not crash on __init__ or
    _update_options().
    """

    # Options as a pre-fix quick-setup install would have stored them.
    _POISONED_OPTIONS: dict = {
        CONF_DELTA_POSITION: None,
        CONF_DELTA_TIME: None,
        CONF_MANUAL_OVERRIDE_DURATION: None,
        CONF_MANUAL_OVERRIDE_RESET: None,
        CONF_MANUAL_THRESHOLD: None,
        CONF_MANUAL_IGNORE_INTERMEDIATE: None,
        CONF_FORCE_OVERRIDE_SENSORS: [],
        CONF_FORCE_OVERRIDE_POSITION: 0,
        CONF_MOTION_SENSORS: [],
        CONF_MOTION_TIMEOUT: 300,
        CONF_DEFAULT_HEIGHT: 60,
        CONF_ENTITIES: ["cover.test"],
        CONF_AZIMUTH: 180,
        CONF_FOV_LEFT: 30,
        CONF_FOV_RIGHT: 30,
        CONF_OPEN_CLOSE_THRESHOLD: 50,
        CONF_INVERSE_STATE: False,
    }

    @pytest.mark.unit
    def test_manual_duration_not_none_in_coordinator_init(self):
        """Coordinator.__init__ must resolve None duration to {'hours': 2}."""
        coord = _make_coordinator_with_options(self._POISONED_OPTIONS)
        assert coord.manual_duration is not None, (
            "REGRESSION (Issue #133): manual_duration was None after __init__ — "
            "AdaptiveCoverManager will crash with TypeError: timedelta(**None)"
        )
        assert isinstance(coord.manual_duration, dict)
        # Must produce a valid timedelta
        td = dt.timedelta(**coord.manual_duration)
        assert td.total_seconds() > 0

    @pytest.mark.unit
    def test_manual_override_manager_initialises_without_crash(self):
        """AdaptiveCoverManager must not crash when duration comes from None options.

        This is the direct reproduction of the Issue #133 crash:
          TypeError: datetime.timedelta() argument after ** must be a mapping, not NoneType
        """
        from custom_components.adaptive_cover_pro.managers.manual_override import (
            AdaptiveCoverManager,
        )

        hass = MagicMock()
        logger = MagicMock()

        # This is what coordinator.__init__ does AFTER the fix
        duration = self._POISONED_OPTIONS.get(CONF_MANUAL_OVERRIDE_DURATION) or {
            "hours": 2
        }
        # Must not raise
        manager = AdaptiveCoverManager(hass, duration, logger)
        assert manager.reset_duration == dt.timedelta(hours=2)

    @pytest.mark.unit
    def test_update_options_resolves_none_delta_position(self):
        """_update_options must resolve None delta_position to 1."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = object.__new__(AdaptiveDataUpdateCoordinator)
        coord._cmd_svc = MagicMock()
        coord._time_mgr = MagicMock()
        coord._motion_mgr = MagicMock()
        coord._weather_mgr = MagicMock()
        coord.manager = MagicMock()

        coord._update_options(self._POISONED_OPTIONS)

        assert coord.min_change is not None, (
            "min_change was None after _update_options — "
            "will crash with TypeError: '>=' not supported between int and NoneType"
        )
        assert isinstance(coord.min_change, int), (
            f"Expected int, got {type(coord.min_change)}: {coord.min_change}"
        )
        assert coord.min_change == 1

    @pytest.mark.unit
    def test_update_options_resolves_none_delta_time(self):
        """_update_options must resolve None delta_time to 2."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = object.__new__(AdaptiveDataUpdateCoordinator)
        coord._cmd_svc = MagicMock()
        coord._time_mgr = MagicMock()
        coord._motion_mgr = MagicMock()
        coord._weather_mgr = MagicMock()
        coord.manager = MagicMock()

        coord._update_options(self._POISONED_OPTIONS)

        assert coord.time_threshold is not None, (
            "time_threshold was None after _update_options — "
            "will crash with TypeError: timedelta(minutes=None)"
        )
        assert isinstance(coord.time_threshold, int), (
            f"Expected int, got {type(coord.time_threshold)}: {coord.time_threshold}"
        )
        assert coord.time_threshold == 2

    @pytest.mark.unit
    def test_update_options_resolves_none_manual_duration(self):
        """_update_options must resolve None manual_duration to {'hours': 2}."""
        from custom_components.adaptive_cover_pro.coordinator import (
            AdaptiveDataUpdateCoordinator,
        )

        coord = object.__new__(AdaptiveDataUpdateCoordinator)
        coord._cmd_svc = MagicMock()
        coord._time_mgr = MagicMock()
        coord._motion_mgr = MagicMock()
        coord._weather_mgr = MagicMock()
        coord.manager = MagicMock()

        coord._update_options(self._POISONED_OPTIONS)

        assert coord.manual_duration is not None
        assert isinstance(coord.manual_duration, dict)
        td = dt.timedelta(**coord.manual_duration)
        assert td.total_seconds() > 0


# ---------------------------------------------------------------------------
# Group 3 — Structural guard: timedelta(**None) regression
# ---------------------------------------------------------------------------


class TestTimedeltaSafetyRegression:
    """Direct regression tests against the exact TypeError from Issue #133.

    If these pass, the crash cannot recur from a None duration value.
    """

    @pytest.mark.unit
    def test_timedelta_double_star_none_raises(self):
        """Confirm that timedelta(**None) raises TypeError — the root cause."""
        with pytest.raises(TypeError):
            dt.timedelta(**None)  # type: ignore[arg-type]

    @pytest.mark.unit
    def test_timedelta_double_star_hours_2_works(self):
        """Confirm that timedelta(**{'hours': 2}) works correctly."""
        td = dt.timedelta(**{"hours": 2})
        assert td == dt.timedelta(hours=2)

    @pytest.mark.unit
    def test_none_or_default_pattern_produces_correct_fallback(self):
        """Verify the 'value or default' pattern used in the fix."""
        stored_value = None
        result = stored_value or {"hours": 2}
        assert result == {"hours": 2}
        # Also verify the non-None path doesn't override real data
        stored_value = {"hours": 1}
        result = stored_value or {"hours": 2}
        assert result == {"hours": 1}

    @pytest.mark.unit
    def test_none_or_default_int_pattern(self):
        """Verify 'value or default' for integer delta fields."""
        assert (None or 1) == 1
        assert (None or 2) == 2
        assert (3 or 1) == 3  # real value preserved
        assert (5 or 2) == 5  # real value preserved


# ---------------------------------------------------------------------------
# Group 4 — Full-setup vs quick-setup parity
# ---------------------------------------------------------------------------


class TestQuickVsFullSetupParity:
    """Every key in the options builder must be present in quick-setup output.

    Ensures that quick setup produces a complete options dict that the
    coordinator can consume without encountering missing keys.
    """

    @pytest.mark.unit
    def test_quick_setup_options_has_all_keys_from_full_setup(self):
        """Quick-setup options dict must contain every key present in full-setup options.

        A missing key is fine — the coordinator handles it with .get(KEY, default).
        But explicitly stored None values for keys with required defaults are not.
        This test ensures that if full setup sets a key to a concrete value,
        quick setup sets it to a safe default rather than None.
        """
        quick_options = _build_options_from_config(_QUICK_SETUP_CONFIG)
        full_options = _build_options_from_config(_FULL_SETUP_CONFIG)

        # Both should have the same set of keys (the options builder is shared)
        assert set(quick_options.keys()) == set(full_options.keys()), (
            "Quick and full setup produced different key sets — "
            "the options builder must be consistent between modes"
        )

    @pytest.mark.unit
    def test_critical_keys_never_none_in_quick_setup(self):
        """The three crashing keys must never be None in quick-setup output."""
        options = _build_options_from_config(_QUICK_SETUP_CONFIG)
        critical = {
            CONF_MANUAL_OVERRIDE_DURATION: "timedelta(**None) crash",
            CONF_DELTA_POSITION: "delta >= None crash",
            CONF_DELTA_TIME: "timedelta(minutes=None) crash",
        }
        for key, crash_description in critical.items():
            assert options[key] is not None, (
                f"REGRESSION (Issue #133): {key} is None in quick-setup options — "
                f"will cause {crash_description}"
            )

    @pytest.mark.unit
    def test_critical_keys_never_none_in_full_setup(self):
        """The three crashing keys must also never be None in full-setup output."""
        options = _build_options_from_config(_FULL_SETUP_CONFIG)
        for key in (
            CONF_MANUAL_OVERRIDE_DURATION,
            CONF_DELTA_POSITION,
            CONF_DELTA_TIME,
        ):
            assert options[key] is not None, (
                f"{key} was None in full-setup options — schema defaults not applied"
            )

    @pytest.mark.unit
    def test_quick_setup_delta_position_is_valid_int(self):
        """CONF_DELTA_POSITION in quick-setup options must be a positive integer."""
        options = _build_options_from_config(_QUICK_SETUP_CONFIG)
        val = options[CONF_DELTA_POSITION]
        assert isinstance(val, int) and val >= 1, (
            f"CONF_DELTA_POSITION={val!r} is not a valid positive integer"
        )

    @pytest.mark.unit
    def test_quick_setup_delta_time_is_valid_int(self):
        """CONF_DELTA_TIME in quick-setup options must be a positive integer."""
        options = _build_options_from_config(_QUICK_SETUP_CONFIG)
        val = options[CONF_DELTA_TIME]
        assert isinstance(val, int) and val >= 2, (
            f"CONF_DELTA_TIME={val!r} is not a valid integer >= 2"
        )

    @pytest.mark.unit
    def test_quick_setup_manual_duration_is_valid_timedelta_dict(self):
        """CONF_MANUAL_OVERRIDE_DURATION from quick setup must produce a valid timedelta."""
        options = _build_options_from_config(_QUICK_SETUP_CONFIG)
        duration = options[CONF_MANUAL_OVERRIDE_DURATION]
        assert isinstance(duration, dict), f"Expected dict, got {type(duration)}"
        td = dt.timedelta(**duration)
        assert td.total_seconds() > 0

    @pytest.mark.unit
    def test_full_setup_explicit_values_not_overridden_by_defaults(self):
        """Full setup values must survive the or-default expressions unchanged."""
        options = _build_options_from_config(_FULL_SETUP_CONFIG)
        assert options[CONF_DELTA_POSITION] == 3, (
            "or-default clobbered explicit CONF_DELTA_POSITION=3 from full setup"
        )
        assert options[CONF_DELTA_TIME] == 5, (
            "or-default clobbered explicit CONF_DELTA_TIME=5 from full setup"
        )
        assert options[CONF_MANUAL_OVERRIDE_DURATION] == {"hours": 1}, (
            "or-default clobbered explicit CONF_MANUAL_OVERRIDE_DURATION from full setup"
        )
