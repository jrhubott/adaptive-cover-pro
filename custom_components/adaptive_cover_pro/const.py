"""Constants for integration_blueprint."""

import logging

DOMAIN = "adaptive_cover_pro"
LOGGER = logging.getLogger(__package__)
_LOGGER = logging.getLogger(__name__)

ATTR_POSITION = "position"
ATTR_TILT_POSITION = "tilt_position"

CONF_AZIMUTH = "set_azimuth"
CONF_BLUEPRINT = "blueprint"
CONF_HEIGHT_WIN = "window_height"
CONF_DISTANCE = "distance_shaded_area"
CONF_WINDOW_DEPTH = "window_depth"
CONF_SILL_HEIGHT = "sill_height"
CONF_DEFAULT_HEIGHT = "default_percentage"
CONF_FOV_LEFT = "fov_left"
CONF_FOV_RIGHT = "fov_right"
CONF_ENTITIES = "group"
CONF_HEIGHT_AWNING = "height_awning"
CONF_LENGTH_AWNING = "length_awning"
CONF_AWNING_ANGLE = "angle"
CONF_SENSOR_TYPE = "sensor_type"
CONF_INVERSE_STATE = "inverse_state"
CONF_SUNSET_POS = "sunset_position"
CONF_SUNSET_OFFSET = "sunset_offset"
CONF_TILT_DEPTH = "slat_depth"
CONF_TILT_DISTANCE = "slat_distance"
CONF_TILT_MODE = "tilt_mode"
CONF_SUNRISE_OFFSET = "sunrise_offset"
CONF_TEMP_ENTITY = "temp_entity"
CONF_PRESENCE_ENTITY = "presence_entity"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_TEMP_LOW = "temp_low"
CONF_TEMP_HIGH = "temp_high"
CONF_MODE = "mode"
CONF_CLIMATE_MODE = "climate_mode"
CONF_WEATHER_STATE = "weather_state"
CONF_MAX_POSITION = "max_position"
CONF_MIN_POSITION = "min_position"
CONF_ENABLE_MAX_POSITION = "enable_max_position"
CONF_ENABLE_MIN_POSITION = "enable_min_position"
CONF_ENABLE_SUN_TRACKING = "enable_sun_tracking"
CONF_OUTSIDETEMP_ENTITY = "outside_temp"
CONF_FORCE_OVERRIDE_SENSORS = "force_override_sensors"
CONF_FORCE_OVERRIDE_POSITION = "force_override_position"
CONF_FORCE_OVERRIDE_MIN_MODE = "force_override_min_mode"

CONF_CUSTOM_POSITION_SENSOR_1 = "custom_position_sensor_1"
CONF_CUSTOM_POSITION_1 = "custom_position_1"
CONF_CUSTOM_POSITION_PRIORITY_1 = "custom_position_priority_1"
CONF_CUSTOM_POSITION_MIN_MODE_1 = "custom_position_min_mode_1"
CONF_CUSTOM_POSITION_USE_MY_1 = "custom_position_use_my_1"
CONF_CUSTOM_POSITION_SENSOR_2 = "custom_position_sensor_2"
CONF_CUSTOM_POSITION_2 = "custom_position_2"
CONF_CUSTOM_POSITION_PRIORITY_2 = "custom_position_priority_2"
CONF_CUSTOM_POSITION_MIN_MODE_2 = "custom_position_min_mode_2"
CONF_CUSTOM_POSITION_USE_MY_2 = "custom_position_use_my_2"
CONF_CUSTOM_POSITION_SENSOR_3 = "custom_position_sensor_3"
CONF_CUSTOM_POSITION_3 = "custom_position_3"
CONF_CUSTOM_POSITION_PRIORITY_3 = "custom_position_priority_3"
CONF_CUSTOM_POSITION_MIN_MODE_3 = "custom_position_min_mode_3"
CONF_CUSTOM_POSITION_USE_MY_3 = "custom_position_use_my_3"
CONF_CUSTOM_POSITION_SENSOR_4 = "custom_position_sensor_4"
CONF_CUSTOM_POSITION_4 = "custom_position_4"
CONF_CUSTOM_POSITION_PRIORITY_4 = "custom_position_priority_4"
CONF_CUSTOM_POSITION_MIN_MODE_4 = "custom_position_min_mode_4"
CONF_CUSTOM_POSITION_USE_MY_4 = "custom_position_use_my_4"
CONF_MY_POSITION_VALUE = "my_position_value"
CONF_SUNSET_USE_MY = "sunset_use_my"
DEFAULT_CUSTOM_POSITION_PRIORITY = 77
CONF_MOTION_SENSORS = "motion_sensors"
CONF_MOTION_TIMEOUT = "motion_timeout"
CONF_ENABLE_BLIND_SPOT = "blind_spot"
CONF_BLIND_SPOT_RIGHT = "blind_spot_right"
CONF_BLIND_SPOT_LEFT = "blind_spot_left"
CONF_BLIND_SPOT_ELEVATION = "blind_spot_elevation"
CONF_MIN_ELEVATION = "min_elevation"
CONF_MAX_ELEVATION = "max_elevation"
CONF_TRANSPARENT_BLIND = "transparent_blind"
CONF_WINTER_CLOSE_INSULATION = "winter_close_insulation"
CONF_CLOUD_SUPPRESSION = "cloud_suppression"
CONF_INTERP_START = "interp_start"
CONF_INTERP_END = "interp_end"
CONF_INTERP_LIST = "interp_list"
CONF_INTERP_LIST_NEW = "interp_list_new"
CONF_INTERP = "interp"
CONF_LUX_ENTITY = "lux_entity"
CONF_LUX_THRESHOLD = "lux_threshold"
CONF_IRRADIANCE_ENTITY = "irradiance_entity"
CONF_IRRADIANCE_THRESHOLD = "irradiance_threshold"
CONF_CLOUD_COVERAGE_ENTITY = "cloud_coverage_entity"
CONF_CLOUD_COVERAGE_THRESHOLD = "cloud_coverage_threshold"
CONF_OUTSIDE_THRESHOLD = "outside_threshold"
CONF_DEVICE_ID = "linked_device_id"
CONF_ENABLE_GLARE_ZONES = "enable_glare_zones"
CONF_WINDOW_WIDTH = "window_width"

# Weather override
CONF_WEATHER_WIND_SPEED_SENSOR = "weather_wind_speed_sensor"
CONF_WEATHER_WIND_DIRECTION_SENSOR = "weather_wind_direction_sensor"
CONF_WEATHER_WIND_SPEED_THRESHOLD = "weather_wind_speed_threshold"
CONF_WEATHER_WIND_DIRECTION_TOLERANCE = "weather_wind_direction_tolerance"
CONF_WEATHER_RAIN_SENSOR = "weather_rain_sensor"
CONF_WEATHER_RAIN_THRESHOLD = "weather_rain_threshold"
CONF_WEATHER_IS_RAINING_SENSOR = "weather_is_raining_sensor"
CONF_WEATHER_IS_WINDY_SENSOR = "weather_is_windy_sensor"
CONF_WEATHER_SEVERE_SENSORS = "weather_severe_sensors"
CONF_WEATHER_OVERRIDE_POSITION = "weather_override_position"
CONF_WEATHER_OVERRIDE_MIN_MODE = "weather_override_min_mode"
CONF_WEATHER_TIMEOUT = "weather_timeout"
CONF_WEATHER_BYPASS_AUTO_CONTROL = "weather_bypass_auto_control"


CONF_DELTA_POSITION = "delta_position"
CONF_DELTA_TIME = "delta_time"
CONF_START_TIME = "start_time"
CONF_START_ENTITY = "start_entity"
CONF_END_TIME = "end_time"
CONF_END_ENTITY = "end_entity"
CONF_RETURN_SUNSET = "return_sunset"
CONF_MANUAL_OVERRIDE_DURATION = "manual_override_duration"
CONF_MANUAL_OVERRIDE_RESET = "manual_override_reset"
CONF_MANUAL_THRESHOLD = "manual_threshold"
CONF_MANUAL_IGNORE_INTERMEDIATE = "manual_ignore_intermediate"
CONF_OPEN_CLOSE_THRESHOLD = "open_close_threshold"

# Debug & Diagnostics
CONF_DEBUG_MODE = "debug_mode"
CONF_DEBUG_CATEGORIES = "debug_categories"
CONF_DEBUG_EVENT_BUFFER_SIZE = "debug_event_buffer_size"
CONF_DRY_RUN = "dry_run"

DEBUG_CATEGORY_MANUAL_OVERRIDE = "manual_override"
DEBUG_CATEGORY_RECONCILIATION = "reconciliation"
DEBUG_CATEGORY_PIPELINE = "pipeline"
DEBUG_CATEGORY_MOTION = "motion"
DEBUG_CATEGORIES_ALL = [
    DEBUG_CATEGORY_MANUAL_OVERRIDE,
    DEBUG_CATEGORY_RECONCILIATION,
    DEBUG_CATEGORY_PIPELINE,
    DEBUG_CATEGORY_MOTION,
]

DEFAULT_DEBUG_EVENT_BUFFER_SIZE = 50
MAX_DEBUG_EVENT_BUFFER_SIZE = 200

# Position verification constants (fixed values, not configurable)
POSITION_CHECK_INTERVAL_MINUTES = 1  # Fixed interval for position verification
POSITION_TOLERANCE_PERCENT = 3  # Fixed tolerance for position matching
MAX_POSITION_RETRIES = 3  # Maximum retry attempts before giving up

# Manual override detection grace period (fixed values, not configurable)
COMMAND_GRACE_PERIOD_SECONDS = 5.0  # Time to ignore position changes after command
STARTUP_GRACE_PERIOD_SECONDS = (
    30.0  # Time to disable manual override detection on startup
)

# Maximum time (seconds) to suppress manual override detection after sending a
# position command.  Once this threshold is crossed, wait_for_target is cleared
# even if the cover still reports a transitional state ("opening"/"closing").
#
# Purpose: covers that do not report a final state ("stopped"/"open"/"closed")
# when the user stops them mid-transit — only emitting position updates — would
# otherwise keep wait_for_target=True indefinitely, preventing manual override
# detection until the reconciliation timer fired.  This constant caps that
# window at a value that accommodates most motorized blinds and awnings, which
# typically complete a full traverse in 20–40 seconds.
TRANSIT_TIMEOUT_SECONDS = 45

# Motion control constants
DEFAULT_MOTION_TIMEOUT = 300  # 5 minutes default timeout for no-motion detection

# Weather override constants
DEFAULT_WEATHER_WIND_SPEED_THRESHOLD = (
    50.0  # threshold unit must match sensor (no conversion applied)
)
DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE = 45  # degrees each side of window azimuth
DEFAULT_WEATHER_RAIN_THRESHOLD = (
    1.0  # threshold unit must match sensor (no conversion applied)
)
DEFAULT_WEATHER_TIMEOUT = 300  # seconds before resuming after conditions clear

# Cloud coverage constants
DEFAULT_CLOUD_COVERAGE_THRESHOLD = 75  # 75% cloud coverage = overcast

STRATEGY_MODE_BASIC = "basic"
STRATEGY_MODE_CLIMATE = "climate"
STRATEGY_MODES = [
    STRATEGY_MODE_BASIC,
    STRATEGY_MODE_CLIMATE,
]


class SensorType:
    """Possible modes for a number selector."""

    BLIND = "cover_blind"
    AWNING = "cover_awning"
    TILT = "cover_tilt"


class ControlStatus:
    """Control status options for diagnostic sensor."""

    ACTIVE = "active"
    OUTSIDE_TIME_WINDOW = "outside_time_window"
    POSITION_DELTA_TOO_SMALL = "position_delta_too_small"
    TIME_DELTA_TOO_SMALL = "time_delta_too_small"
    MANUAL_OVERRIDE = "manual_override"
    AUTOMATIC_CONTROL_OFF = "automatic_control_off"
    SUN_NOT_VISIBLE = "sun_not_visible"
    FORCE_OVERRIDE_ACTIVE = "force_override_active"
    WEATHER_OVERRIDE_ACTIVE = "weather_override_active"
    MOTION_TIMEOUT = "motion_timeout"


# Geometric accuracy constants (used in calculation.py for safety margins and edge cases)
# Edge case thresholds for extreme sun positions
EDGE_CASE_LOW_ELEVATION = 2.0  # degrees - minimum elevation for normal calculation
EDGE_CASE_HIGH_ELEVATION = (
    88.0  # degrees - maximum elevation before using simplified calculation
)
EDGE_CASE_EXTREME_GAMMA = 85  # degrees - maximum horizontal angle deviation

# Safety margin thresholds and multipliers
SAFETY_MARGIN_GAMMA_THRESHOLD = 45  # degrees - angle where gamma-based margins start
SAFETY_MARGIN_GAMMA_MAX = 0.2  # 20% increase at extreme horizontal angles (>45°)
SAFETY_MARGIN_LOW_ELEV_THRESHOLD = (
    10  # degrees - elevation where low-angle margins apply
)
SAFETY_MARGIN_LOW_ELEV_MAX = 0.15  # 15% increase at low sun elevation (<10°)
SAFETY_MARGIN_HIGH_ELEV_THRESHOLD = (
    75  # degrees - elevation where high-angle margins apply
)
SAFETY_MARGIN_HIGH_ELEV_MAX = 0.1  # 10% increase at high sun elevation (>75°)

# Window depth calculation threshold
WINDOW_DEPTH_GAMMA_THRESHOLD = (
    10  # degrees - minimum gamma for window depth contribution
)

# Climate mode constants
CLIMATE_SUMMER_TILT_ANGLE = 45  # degrees - tilt angle for summer cooling strategy
CLIMATE_DEFAULT_TILT_ANGLE = 80  # degrees - default tilt angle when not present

# Cover position constants
POSITION_CLOSED = 0  # Fully closed position
POSITION_OPEN = 100  # Fully open position
