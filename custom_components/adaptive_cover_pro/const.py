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
CONF_SUNSET_POS = "sunset_position"
CONF_SUNSET_OFFSET = "sunset_offset"
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
CONF_OUTSIDETEMP_ENTITY = "outside_temp"
CONF_FORCE_OVERRIDE_SENSORS = "force_override_sensors"
CONF_FORCE_OVERRIDE_POSITION = "force_override_position"
CONF_MOTION_SENSORS = "motion_sensors"
CONF_MOTION_TIMEOUT = "motion_timeout"
CONF_ENABLE_BLIND_SPOT = "blind_spot"
CONF_BLIND_SPOT_RIGHT = "blind_spot_right"
CONF_BLIND_SPOT_LEFT = "blind_spot_left"
CONF_BLIND_SPOT_ELEVATION = "blind_spot_elevation"
CONF_MIN_ELEVATION = "min_elevation"
CONF_MAX_ELEVATION = "max_elevation"
CONF_TRANSPARENT_BLIND = "transparent_blind"
CONF_INTERP_START = "interp_start"
CONF_INTERP_END = "interp_end"
CONF_INTERP_LIST = "interp_list"
CONF_INTERP_LIST_NEW = "interp_list_new"
CONF_INTERP = "interp"
CONF_LUX_ENTITY = "lux_entity"
CONF_LUX_THRESHOLD = "lux_threshold"
CONF_IRRADIANCE_ENTITY = "irradiance_entity"
CONF_IRRADIANCE_THRESHOLD = "irradiance_threshold"
CONF_OUTSIDE_THRESHOLD = "outside_threshold"


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
CONF_ENABLE_DIAGNOSTICS = "enable_diagnostics"

# Position verification constants (fixed values, not configurable)
POSITION_CHECK_INTERVAL_MINUTES = 1  # Fixed interval for position verification
POSITION_TOLERANCE_PERCENT = 3  # Fixed tolerance for position matching
MAX_POSITION_RETRIES = 3  # Maximum retry attempts before giving up

# Manual override detection grace period (fixed values, not configurable)
COMMAND_GRACE_PERIOD_SECONDS = 5.0  # Time to ignore position changes after command
STARTUP_GRACE_PERIOD_SECONDS = 30.0  # Time to disable manual override detection on startup

# Motion control constants
DEFAULT_MOTION_TIMEOUT = 300  # 5 minutes default timeout for no-motion detection

# Import flow constants
LEGACY_DOMAIN = "adaptive_cover"

# All fields that map 1:1 from old to new
DIRECT_MAPPING_FIELDS = [
    CONF_AZIMUTH,
    CONF_HEIGHT_WIN,
    CONF_DISTANCE,
    CONF_DEFAULT_HEIGHT,
    CONF_MAX_POSITION,
    CONF_MIN_POSITION,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_ENTITIES,
    CONF_INVERSE_STATE,
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
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_START_TIME,
    CONF_START_ENTITY,
    CONF_MANUAL_OVERRIDE_DURATION,
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
    CONF_INTERP,
    CONF_INTERP_START,
    CONF_INTERP_END,
    CONF_INTERP_LIST,
    CONF_INTERP_LIST_NEW,
    CONF_LUX_ENTITY,
    CONF_LUX_THRESHOLD,
    CONF_IRRADIANCE_ENTITY,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_OUTSIDE_THRESHOLD,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_MOTION_SENSORS,
    CONF_MOTION_TIMEOUT,
    CONF_END_TIME,
    CONF_END_ENTITY,
    CONF_RETURN_SUNSET,
    CONF_ENABLE_MAX_POSITION,
    CONF_ENABLE_MIN_POSITION,
    CONF_MODE,
]

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
    MOTION_TIMEOUT = "motion_timeout"


# Geometric accuracy constants (used in calculation.py for safety margins and edge cases)
# Edge case thresholds for extreme sun positions
EDGE_CASE_LOW_ELEVATION = 2.0  # degrees - minimum elevation for normal calculation
EDGE_CASE_HIGH_ELEVATION = 88.0  # degrees - maximum elevation before using simplified calculation
EDGE_CASE_EXTREME_GAMMA = 85  # degrees - maximum horizontal angle deviation

# Safety margin thresholds and multipliers
SAFETY_MARGIN_GAMMA_THRESHOLD = 45  # degrees - angle where gamma-based margins start
SAFETY_MARGIN_GAMMA_MAX = 0.2  # 20% increase at extreme horizontal angles (>45°)
SAFETY_MARGIN_LOW_ELEV_THRESHOLD = 10  # degrees - elevation where low-angle margins apply
SAFETY_MARGIN_LOW_ELEV_MAX = 0.15  # 15% increase at low sun elevation (<10°)
SAFETY_MARGIN_HIGH_ELEV_THRESHOLD = 75  # degrees - elevation where high-angle margins apply
SAFETY_MARGIN_HIGH_ELEV_MAX = 0.1  # 10% increase at high sun elevation (>75°)

# Window depth calculation threshold
WINDOW_DEPTH_GAMMA_THRESHOLD = 10  # degrees - minimum gamma for window depth contribution

# Climate mode constants
CLIMATE_SUMMER_TILT_ANGLE = 45  # degrees - tilt angle for summer cooling strategy
CLIMATE_DEFAULT_TILT_ANGLE = 80  # degrees - default tilt angle when not present

# Cover position constants
POSITION_CLOSED = 0  # Fully closed position
POSITION_OPEN = 100  # Fully open position
