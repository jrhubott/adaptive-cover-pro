"""Tests for ``RuntimeConfig.from_options``.

The runtime config aggregates every option that ``coordinator._update_options``
used to read inline. Each ``CONF_*`` here used to be a separate ``options.get``
call in the coordinator, so a regression that drops a field is easy to make —
this file pins the contract.
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.config_types import RuntimeConfig
from custom_components.adaptive_cover_pro.const import (
    CONF_AZIMUTH,
    CONF_DEBUG_EVENT_BUFFER_SIZE,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_ENABLE_POSITION_MATCHING,
    CONF_END_ENTITY,
    CONF_END_TIME,
    CONF_ENTITIES,
    CONF_INTERP_END,
    CONF_INTERP_LIST,
    CONF_INTERP_LIST_NEW,
    CONF_INTERP_START,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_MANUAL_OVERRIDE_RESET,
    CONF_MANUAL_THRESHOLD,
    CONF_MOTION_SENSORS,
    CONF_MOTION_TEMPLATE_MODE,
    CONF_MOTION_TIMEOUT,
    CONF_OPEN_CLOSE_THRESHOLD,
    CONF_POSITION_TOLERANCE,
    CONF_START_ENTITY,
    CONF_START_TIME,
    CONF_WEATHER_IS_RAINING_SENSOR,
    CONF_WEATHER_IS_WINDY_SENSOR,
    CONF_WEATHER_RAIN_SENSOR,
    CONF_WEATHER_RAIN_THRESHOLD,
    CONF_WEATHER_SEVERE_SENSORS,
    CONF_WEATHER_TIMEOUT,
    CONF_WEATHER_WIND_DIRECTION_SENSOR,
    CONF_WEATHER_WIND_DIRECTION_TOLERANCE,
    CONF_WEATHER_WIND_SPEED_SENSOR,
    CONF_WEATHER_WIND_SPEED_THRESHOLD,
    DEFAULT_DEBUG_EVENT_BUFFER_SIZE,
    DEFAULT_MOTION_TIMEOUT,
    DEFAULT_WEATHER_RAIN_THRESHOLD,
    DEFAULT_WEATHER_TIMEOUT,
    DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE,
    DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
)


@pytest.mark.unit
def test_from_options_uses_const_defaults_for_empty_input() -> None:
    rc = RuntimeConfig.from_options({})

    assert rc.entities == []
    assert rc.open_close_threshold == 50
    assert rc.event_buffer_size == DEFAULT_DEBUG_EVENT_BUFFER_SIZE

    assert rc.tracking.min_change == 1
    assert rc.tracking.time_threshold == 2
    assert rc.tracking.manual_threshold is None
    assert rc.tracking.interp_start is None
    assert rc.tracking.interp_end is None
    assert rc.tracking.interp_list is None
    assert rc.tracking.interp_list_new is None

    assert rc.manual_override.reset is False
    assert rc.manual_override.duration == {"hours": 2}
    assert rc.manual_override.ignore_external is False

    assert rc.time_window.start_time is None
    assert rc.time_window.start_time_entity is None
    assert rc.time_window.end_time is None
    assert rc.time_window.end_time_entity is None

    assert rc.motion.sensors == []
    assert rc.motion.timeout_seconds == DEFAULT_MOTION_TIMEOUT
    # Combine mode defaults to OR (additive) for back-compat with old entries.
    assert rc.motion.template_mode == "or"

    assert rc.weather.wind_speed_threshold == DEFAULT_WEATHER_WIND_SPEED_THRESHOLD
    assert (
        rc.weather.wind_direction_tolerance == DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE
    )
    assert rc.weather.win_azi == 180
    assert rc.weather.rain_threshold == DEFAULT_WEATHER_RAIN_THRESHOLD
    assert rc.weather.timeout_seconds == DEFAULT_WEATHER_TIMEOUT
    assert rc.weather.severe_sensors == []
    assert rc.weather.wind_speed_sensor is None
    assert rc.weather.wind_direction_sensor is None
    assert rc.weather.rain_sensor is None
    assert rc.weather.is_raining_sensor is None
    assert rc.weather.is_windy_sensor is None


@pytest.mark.unit
def test_position_tolerance_defaults_to_three() -> None:
    """Empty options → tolerance falls back to POSITION_TOLERANCE_PERCENT (issue #507)."""
    rc = RuntimeConfig.from_options({})
    assert rc.tracking.position_tolerance == 3


def test_position_tolerance_reads_provided_value() -> None:
    """A configured tolerance flows through to the tracking slice (issue #507)."""
    rc = RuntimeConfig.from_options({CONF_POSITION_TOLERANCE: 8})
    assert rc.tracking.position_tolerance == 8


def test_enable_position_matching_defaults_false() -> None:
    """Empty options → position matching is off by default (issue #591)."""
    rc = RuntimeConfig.from_options({})
    assert rc.tracking.enable_position_matching is False


def test_enable_position_matching_reads_provided_value() -> None:
    """The enable toggle flows through to the tracking slice (issue #591)."""
    rc = RuntimeConfig.from_options({CONF_ENABLE_POSITION_MATCHING: True})
    assert rc.tracking.enable_position_matching is True


def test_from_options_reads_every_field_from_provided_dict() -> None:
    options = {
        CONF_ENTITIES: ["cover.test"],
        CONF_OPEN_CLOSE_THRESHOLD: 70,
        CONF_DEBUG_EVENT_BUFFER_SIZE: 500,
        CONF_DELTA_POSITION: 5,
        CONF_DELTA_TIME: 10,
        CONF_MANUAL_THRESHOLD: 20,
        CONF_INTERP_START: "07:00",
        CONF_INTERP_END: "19:00",
        CONF_INTERP_LIST: [1, 2, 3],
        CONF_INTERP_LIST_NEW: [4, 5, 6],
        CONF_MANUAL_OVERRIDE_RESET: True,
        CONF_MANUAL_OVERRIDE_DURATION: {"hours": 4},
        "manual_ignore_external": True,
        CONF_START_TIME: "08:00",
        CONF_START_ENTITY: "input_datetime.s",
        CONF_END_TIME: "20:00",
        CONF_END_ENTITY: "input_datetime.e",
        CONF_MOTION_SENSORS: ["binary_sensor.m"],
        CONF_MOTION_TEMPLATE_MODE: "and",
        CONF_MOTION_TIMEOUT: 600,
        CONF_WEATHER_WIND_SPEED_SENSOR: "sensor.wind",
        CONF_WEATHER_WIND_DIRECTION_SENSOR: "sensor.dir",
        CONF_WEATHER_WIND_SPEED_THRESHOLD: 25.0,
        CONF_WEATHER_WIND_DIRECTION_TOLERANCE: 60,
        CONF_AZIMUTH: 200,
        CONF_WEATHER_RAIN_SENSOR: "sensor.rain",
        CONF_WEATHER_RAIN_THRESHOLD: 1.5,
        CONF_WEATHER_IS_RAINING_SENSOR: "binary_sensor.r",
        CONF_WEATHER_IS_WINDY_SENSOR: "binary_sensor.w",
        CONF_WEATHER_SEVERE_SENSORS: ["binary_sensor.severe"],
        CONF_WEATHER_TIMEOUT: 900,
    }
    rc = RuntimeConfig.from_options(options)

    assert rc.entities == ["cover.test"]
    assert rc.open_close_threshold == 70
    assert rc.event_buffer_size == 500
    assert rc.tracking.min_change == 5
    assert rc.tracking.time_threshold == 10
    assert rc.tracking.manual_threshold == 20
    assert rc.tracking.interp_start == "07:00"
    assert rc.tracking.interp_list_new == [4, 5, 6]
    assert rc.manual_override.reset is True
    assert rc.manual_override.duration == {"hours": 4}
    assert rc.manual_override.ignore_external is True
    assert rc.time_window.start_time == "08:00"
    assert rc.time_window.end_time_entity == "input_datetime.e"
    assert rc.motion.sensors == ["binary_sensor.m"]
    assert rc.motion.template_mode == "and"
    assert rc.motion.timeout_seconds == 600
    assert rc.weather.wind_speed_threshold == 25.0
    assert rc.weather.win_azi == 200
    assert rc.weather.rain_threshold == 1.5
    assert rc.weather.severe_sensors == ["binary_sensor.severe"]
    assert rc.weather.timeout_seconds == 900


@pytest.mark.unit
def test_zero_or_falsy_values_falling_back_to_defaults() -> None:
    """``min_change`` and ``time_threshold`` use ``or`` with literal defaults.

    Pin that contract — a future change to use ``options.get(key, default)``
    would silently swap "missing → default" for "falsy → falsy", which is
    a different fall-through path.
    """
    rc = RuntimeConfig.from_options({CONF_DELTA_POSITION: 0, CONF_DELTA_TIME: 0})
    assert rc.tracking.min_change == 1  # 0 or 1 → 1
    assert rc.tracking.time_threshold == 2  # 0 or 2 → 2


@pytest.mark.unit
def test_runtime_config_is_frozen() -> None:
    """``RuntimeConfig`` is immutable so a manager can't accidentally mutate it."""
    rc = RuntimeConfig.from_options({})
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        rc.entities = ["cover.never"]  # type: ignore[misc]


@pytest.mark.unit
def test_runtime_config_venetian_slice_defaults() -> None:
    """Empty options dict → VenetianSlice uses DEFAULT_* constants from const.py."""
    from custom_components.adaptive_cover_pro.const import (
        DEFAULT_VENETIAN_MODE,
        DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS,
        DEFAULT_VENETIAN_TILT_SKIP_ABOVE,
        VENETIAN_MODE_POSITION_AND_TILT,
    )

    rc = RuntimeConfig.from_options({})
    assert (
        rc.venetian.post_settle_hold_seconds
        == DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS
    )
    assert rc.venetian.post_settle_hold_seconds == 3.0
    assert rc.venetian.tilt_skip_above == DEFAULT_VENETIAN_TILT_SKIP_ABOVE
    assert rc.venetian.tilt_skip_above == 95
    assert rc.venetian.venetian_mode == DEFAULT_VENETIAN_MODE
    assert rc.venetian.venetian_mode == VENETIAN_MODE_POSITION_AND_TILT


@pytest.mark.unit
def test_runtime_config_venetian_slice_reads_options() -> None:
    """Custom values round-trip through VenetianSlice correctly."""
    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_MODE,
        CONF_VENETIAN_POST_SETTLE_HOLD,
        CONF_VENETIAN_TILT_SKIP_ABOVE,
        VENETIAN_MODE_TILT_ONLY,
    )

    options = {
        CONF_VENETIAN_POST_SETTLE_HOLD: 5.5,
        CONF_VENETIAN_TILT_SKIP_ABOVE: 80,
        CONF_VENETIAN_MODE: VENETIAN_MODE_TILT_ONLY,
    }
    rc = RuntimeConfig.from_options(options)
    assert rc.venetian.post_settle_hold_seconds == 5.5
    assert rc.venetian.tilt_skip_above == 80
    assert rc.venetian.venetian_mode == VENETIAN_MODE_TILT_ONLY


@pytest.mark.unit
def test_runtime_config_threads_publish_lag_into_sequencer() -> None:
    """Issue #33: ``backrotate_publish_lag_seconds`` plumbs through ``VenetianSlice``.

    A user setting ``CONF_VENETIAN_BACKROTATE_PUBLISH_LAG`` in options must
    surface on ``rc.venetian.backrotate_publish_lag_seconds`` so the
    coordinator can pass it into ``DualAxisSequencer.__init__``. Empty
    options must fall back to ``DEFAULT_VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS``.
    """
    from custom_components.adaptive_cover_pro.const import (
        CONF_VENETIAN_BACKROTATE_PUBLISH_LAG,
        DEFAULT_VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS,
    )

    rc_default = RuntimeConfig.from_options({})
    assert (
        rc_default.venetian.backrotate_publish_lag_seconds
        == DEFAULT_VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS
    )
    assert rc_default.venetian.backrotate_publish_lag_seconds == 45.0

    rc_custom = RuntimeConfig.from_options({CONF_VENETIAN_BACKROTATE_PUBLISH_LAG: 75.0})
    assert rc_custom.venetian.backrotate_publish_lag_seconds == 75.0
