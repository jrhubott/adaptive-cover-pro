"""Constants for the Adaptive Cover Pro integration.

Every public symbol the rest of the package depends on lives here. The file is
organized into named sections (banner-style headers below) and every constant
carries an inline comment with its unit, range, default, or role.

Conventions
-----------
* ``CONF_*`` constants hold the wire-format key under which an option is
  persisted in ``config_entry.options``. These strings are stored in the user's
  Home Assistant config and **must stay byte-stable** across releases — renaming
  the Python name is fine, renaming the string value is not.
* ``DEFAULT_*`` constants are the value applied when the corresponding option is
  unset.
* Private ``_RANGE_*`` tuples are ``(min, max)`` bounds used only inside this
  module to build ``OPTION_RANGES`` — the single source of truth that
  ``config_flow.py`` selectors and ``services/options_service.py`` validators
  both consume.
* ``ATTR_*`` constants are HA service-call attribute keys.

Section index
-------------
 1. Module Identity & Logging
 2. Cover Type & Device
 3. Window / Vertical-Blind Geometry
 4. Awning Geometry
 5. Tilt / Venetian Slat Geometry
 6. Position Limits & Inverse State
 7. Sun Tracking
 8. Sunset & Sunrise Behavior
 9. Blind Spot
10. Glare Zones
11. Climate Strategy
12. Light & Cloud Sensing
13. Force Override
14. Custom Position Slots
15. Weather Override (Safety)
16. Automation Timing & Gating
17. Interpolation
18. Manual Override & Transit
19. Motion Control
20. Position Verification
21. Venetian Dual-Axis Sequencing
22. Debug & Diagnostics
23. Control Status (diagnostic enum)
24. Geometric Accuracy (calc engine)
25. UI Defaults & Validation Caps
26. Numeric Option Ranges (single source of truth)
"""

import logging

# =============================================================================
# 1. Module Identity & Logging
# =============================================================================
# Domain string, package-level loggers, and HA service-call attribute keys.

DOMAIN = "adaptive_cover_pro"  # HA integration domain; must match manifest.json
LOGGER = logging.getLogger(__package__)  # package-scoped logger
_LOGGER = logging.getLogger(__name__)  # module-scoped; also imported by button.py

ATTR_POSITION = "position"  # HA cover service attr: vertical position (0-100)
ATTR_TILT_POSITION = "tilt_position"  # HA cover service attr: slat tilt (0-100)

# Legacy carryover from the integration_blueprint template; kept for symbol
# stability (unused at runtime).
CONF_BLUEPRINT = "blueprint"


# =============================================================================
# 2. Cover Type & Device
# =============================================================================
# Identifies which cover type a config entry models and which HA device, if
# any, the entities should be linked to.

CONF_SENSOR_TYPE = "sensor_type"  # one of SensorType.* below
CONF_DEVICE_ID = "linked_device_id"  # HA device_id to link this instance to


class SensorType:
    """Cover-type identifiers stored in ``config_entry.data[CONF_SENSOR_TYPE]``.

    These values drive which ``CoverTypePolicy`` (under ``cover_types/``) is
    instantiated, which config-flow geometry step is shown, and which calc-engine
    cover module under ``engine/covers/`` is used.
    """

    BLIND = "cover_blind"  # vertical blind — up/down movement
    AWNING = "cover_awning"  # horizontal awning — in/out extension
    TILT = "cover_tilt"  # tilt-only — slat rotation, no carriage movement
    VENETIAN = "cover_venetian"  # dual-axis — position AND tilt


# =============================================================================
# 3. Window / Vertical-Blind Geometry
# =============================================================================
# Window-frame dimensions and sun-tracking field-of-view. Consumed by
# `engine/sun_geometry.py` and the vertical-blind calc path.

CONF_AZIMUTH = "set_azimuth"  # window azimuth, degrees 0-359 (south=180)
CONF_HEIGHT_WIN = "window_height"  # window height, metres (0.1-50.0)
CONF_WINDOW_WIDTH = "window_width"  # window width, metres (0.1-50.0)
CONF_WINDOW_DEPTH = "window_depth"  # window recess depth, metres (0.0-5.0)
CONF_SILL_HEIGHT = "sill_height"  # sill height above floor, metres (0.0-50.0)
CONF_DISTANCE = "distance_shaded_area"  # blind→shaded distance, m (0.1-50.0)
CONF_FOV_LEFT = "fov_left"  # left half-FOV from azimuth, degrees 0-180
CONF_FOV_RIGHT = "fov_right"  # right half-FOV from azimuth, degrees 0-180
CONF_ENTITIES = "group"  # list of HA cover entity_ids controlled
CONF_ENABLE_PROXY_COVER = "enable_proxy_cover"  # opt-in proxy cover platform
DEFAULT_ENABLE_PROXY_COVER = False
TRIGGER_PROXY_POSITION = "proxy_managed"
TRIGGER_PROXY_OPEN = "proxy_open"
TRIGGER_PROXY_CLOSE = "proxy_close"
TRIGGER_PROXY_TILT = "proxy_tilt"


# =============================================================================
# 4. Awning Geometry
# =============================================================================
# Horizontal awning dimensions (extension length and tilt).

CONF_HEIGHT_AWNING = "height_awning"  # mount height above ground, metres
CONF_LENGTH_AWNING = "length_awning"  # extension length, metres (0.3-6.0)
CONF_AWNING_ANGLE = "angle"  # tilt from horizontal, degrees (0-45)


# =============================================================================
# 5. Tilt / Venetian Slat Geometry
# =============================================================================
# Slat dimensions used to compute tilt angle, plus min/max tilt clamps.

CONF_TILT_DEPTH = "slat_depth"  # slat depth, cm (range 0.1-15.0)
CONF_TILT_DISTANCE = "slat_distance"  # vertical slat spacing, cm (0.1-15.0)
CONF_TILT_MODE = "tilt_mode"  # tilt strategy identifier
CONF_MAX_TILT = "max_tilt"  # cap on sun-derived tilt %, 0-100
DEFAULT_MAX_TILT = 100  # default: no upper cap
CONF_MIN_TILT = "min_tilt"  # floor on sun-derived tilt %, 0-100
DEFAULT_MIN_TILT = 0  # default: no lower floor


# =============================================================================
# 6. Position Limits & Inverse State
# =============================================================================
# Hard min/max position clamps, default-when-not-tracking, the inverse-state
# flags (some covers report 0=open instead of 0=closed), and the two named
# fixed points POSITION_CLOSED / POSITION_OPEN used widely in calc code.

CONF_MAX_POSITION = "max_position"  # upper clamp on commanded position (1-100)
CONF_MIN_POSITION = "min_position"  # lower clamp on commanded position (0-99)
# If True, max_position is only enforced during active sun tracking.
CONF_ENABLE_MAX_POSITION = "enable_max_position"
# If True, min_position is only enforced during active sun tracking.
CONF_ENABLE_MIN_POSITION = "enable_min_position"
# Fallback position when no override applies, % (range 0-100).
CONF_DEFAULT_HEIGHT = "default_percentage"
CONF_INVERSE_STATE = "inverse_state"  # True if cover reports 0=open, 100=closed
CONF_INVERSE_TILT = "inverse_tilt"  # True if tilt reports 0=open, 100=closed

POSITION_CLOSED = 0  # canonical fully-closed position
POSITION_OPEN = 100  # canonical fully-open position


# =============================================================================
# 7. Sun Tracking
# =============================================================================
# Master enable flag and the elevation window outside of which sun tracking is
# suppressed (handlers fall through to the default position).

# Master switch — disable to run on overrides only.
CONF_ENABLE_SUN_TRACKING = "enable_sun_tracking"
CONF_MIN_ELEVATION = "min_elevation"  # sun must be at least this high, deg 0-90
CONF_MAX_ELEVATION = "max_elevation"  # tracking off above this elevation, 0-90
# True if blind passes some light even when closed (used by glare/climate).
CONF_TRANSPARENT_BLIND = "transparent_blind"


# =============================================================================
# 8. Sunset & Sunrise Behavior
# =============================================================================
# What position to take after sunset / before sunrise, plus offset windows that
# shift the activation moment.

CONF_SUNSET_POS = "sunset_position"  # post-sunset position 0-100; None=default
CONF_SUNSET_OFFSET = "sunset_offset"  # minutes ±120 from sunset to switch
CONF_SUNRISE_OFFSET = "sunrise_offset"  # minutes ±120 from sunrise to resume
CONF_RETURN_SUNSET = "return_sunset"  # True: force-send default at end_time
# If True, sunset position uses CONF_MY_POSITION_VALUE instead of CONF_SUNSET_POS.
CONF_SUNSET_USE_MY = "sunset_use_my"
# Explicit tilt for venetian covers (0-100). None = use solar-computed tilt.
CONF_DEFAULT_TILT = "default_tilt"  # tilt when no handler fires
CONF_SUNSET_TILT = (
    "sunset_tilt"  # tilt during sunset window (falls back to default_tilt)
)


# =============================================================================
# 9. Blind Spot
# =============================================================================
# Azimuth wedge inside which the sun is treated as "blocked" (e.g. by a tree),
# so direct-sun handling switches off even if geometry says otherwise.

CONF_ENABLE_BLIND_SPOT = "blind_spot"  # master enable
CONF_BLIND_SPOT_LEFT = "blind_spot_left"  # left edge, azimuth deg 0-359
CONF_BLIND_SPOT_RIGHT = "blind_spot_right"  # right edge, azimuth deg 0-360
# Sun elevation below which the blind-spot wedge applies, degrees 0-90.
CONF_BLIND_SPOT_ELEVATION = "blind_spot_elevation"


# =============================================================================
# 10. Glare Zones
# =============================================================================
# Optional glare-zone handler (priority 45 in the override pipeline).

CONF_ENABLE_GLARE_ZONES = "enable_glare_zones"  # activate glare-zone handler


# =============================================================================
# 11. Climate Strategy
# =============================================================================
# Climate-aware operation: temperature thresholds, presence, weather entity,
# winter-insulation override, and the strategy mode enum.

CONF_MODE = "mode"  # legacy strategy mode key (back-compat)
CONF_CLIMATE_MODE = "climate_mode"  # enable climate handler (priority 50)
CONF_TEMP_ENTITY = "temp_entity"  # indoor temp sensor entity_id
CONF_TEMP_LOW = "temp_low"  # "cold" threshold, sensor unit (0-90)
CONF_TEMP_HIGH = "temp_high"  # "hot" threshold, sensor unit (0-90)
CONF_OUTSIDETEMP_ENTITY = "outside_temp"  # outdoor temp sensor entity_id
# Outdoor temp threshold for summer/winter mode switch (range 0-100).
CONF_OUTSIDE_THRESHOLD = "outside_threshold"
CONF_PRESENCE_ENTITY = "presence_entity"  # presence/occupancy sensor entity_id
CONF_WEATHER_ENTITY = "weather_entity"  # weather. integration entity_id
CONF_WEATHER_STATE = "weather_state"  # states that trigger climate handler
# True to close covers at night in winter for added insulation.
CONF_WINTER_CLOSE_INSULATION = "winter_close_insulation"

STRATEGY_MODE_BASIC = "basic"  # geometry only, no climate inputs
STRATEGY_MODE_CLIMATE = "climate"  # climate-aware (temps/presence/weather)
STRATEGY_MODES = [
    STRATEGY_MODE_BASIC,
    STRATEGY_MODE_CLIMATE,
]  # ordered list used by config_flow selectors

CLIMATE_SUMMER_TILT_ANGLE = 45  # degrees — slat tilt under summer cooling
CLIMATE_DEFAULT_TILT_ANGLE = 80  # degrees — tilt when no climate signal


# =============================================================================
# 12. Light & Cloud Sensing
# =============================================================================
# Optional inputs for the cloud-suppression handler (priority 60): direct
# illuminance/irradiance sensors, a precomputed "is sunny" boolean, and cloud-
# coverage suppression that switches to CONF_CLOUDY_POSITION when overcast.

CONF_LUX_ENTITY = "lux_entity"  # illuminance sensor entity_id, lx
# Below this lux value the sun is treated as too weak to track.
CONF_LUX_THRESHOLD = "lux_threshold"
CONF_IRRADIANCE_ENTITY = "irradiance_entity"  # irradiance sensor, W/m²
# Below this irradiance the sun is treated as too weak to track.
CONF_IRRADIANCE_THRESHOLD = "irradiance_threshold"
CONF_IS_SUNNY_SENSOR = "is_sunny_sensor"  # precomputed binary "is sunny"
CONF_CLOUD_COVERAGE_ENTITY = "cloud_coverage_entity"  # cloud-cover % sensor
# % cloud cover above which the suppression handler activates.
CONF_CLOUD_COVERAGE_THRESHOLD = "cloud_coverage_threshold"
CONF_CLOUD_SUPPRESSION = "cloud_suppression"  # master enable
CONF_CLOUDY_POSITION = "cloudy_position"  # position while suppressed (0-100)

DEFAULT_CLOUD_COVERAGE_THRESHOLD = 75  # default: 75% cover = overcast


# =============================================================================
# 13. Force Override
# =============================================================================
# Highest-priority handler (100). When any of the listed binary sensors is on,
# command the configured position.

CONF_FORCE_OVERRIDE_SENSORS = "force_override_sensors"  # binary_sensor list
CONF_FORCE_OVERRIDE_POSITION = "force_override_position"  # position 0-100
# If True, force-override is only enforced as a min position (won't close more).
CONF_FORCE_OVERRIDE_MIN_MODE = "force_override_min_mode"


# =============================================================================
# 14. Custom Position Slots
# =============================================================================
# Up to four independently-configurable position slots, each with its own
# trigger sensor, position, priority (1-99), min-mode flag, and "use my
# position" flag. Each slot has five wire-format keys; they are generated
# below to keep them DRY. The numbered per-slot CONF_* aliases are retained
# for callers that prefer named constants over dict lookup.

CUSTOM_POSITION_SLOT_NUMBERS: tuple[int, ...] = (1, 2, 3, 4)  # supported indices


def _custom_position_slot_keys(n: int) -> dict[str, str]:
    """Return the six wire-format option keys for slot *n*."""
    return {
        "sensor": f"custom_position_sensor_{n}",
        "position": f"custom_position_{n}",
        "priority": f"custom_position_priority_{n}",
        "min_mode": f"custom_position_min_mode_{n}",
        "use_my": f"custom_position_use_my_{n}",
        "tilt": f"custom_position_tilt_{n}",
    }


# {slot_number: {sub_key: wire_key}}
CUSTOM_POSITION_SLOTS: dict[int, dict[str, str]] = {
    n: _custom_position_slot_keys(n) for n in CUSTOM_POSITION_SLOT_NUMBERS
}

# Slot 1 — named aliases for each of the five sub-keys.
CONF_CUSTOM_POSITION_SENSOR_1 = CUSTOM_POSITION_SLOTS[1]["sensor"]  # trigger
CONF_CUSTOM_POSITION_1 = CUSTOM_POSITION_SLOTS[1]["position"]  # 0-100
CONF_CUSTOM_POSITION_PRIORITY_1 = CUSTOM_POSITION_SLOTS[1]["priority"]  # 1-99
CONF_CUSTOM_POSITION_MIN_MODE_1 = CUSTOM_POSITION_SLOTS[1]["min_mode"]  # min-only
CONF_CUSTOM_POSITION_USE_MY_1 = CUSTOM_POSITION_SLOTS[1]["use_my"]  # use my-pos

# Slot 2.
CONF_CUSTOM_POSITION_SENSOR_2 = CUSTOM_POSITION_SLOTS[2]["sensor"]
CONF_CUSTOM_POSITION_2 = CUSTOM_POSITION_SLOTS[2]["position"]
CONF_CUSTOM_POSITION_PRIORITY_2 = CUSTOM_POSITION_SLOTS[2]["priority"]
CONF_CUSTOM_POSITION_MIN_MODE_2 = CUSTOM_POSITION_SLOTS[2]["min_mode"]
CONF_CUSTOM_POSITION_USE_MY_2 = CUSTOM_POSITION_SLOTS[2]["use_my"]

# Slot 3.
CONF_CUSTOM_POSITION_SENSOR_3 = CUSTOM_POSITION_SLOTS[3]["sensor"]
CONF_CUSTOM_POSITION_3 = CUSTOM_POSITION_SLOTS[3]["position"]
CONF_CUSTOM_POSITION_PRIORITY_3 = CUSTOM_POSITION_SLOTS[3]["priority"]
CONF_CUSTOM_POSITION_MIN_MODE_3 = CUSTOM_POSITION_SLOTS[3]["min_mode"]
CONF_CUSTOM_POSITION_USE_MY_3 = CUSTOM_POSITION_SLOTS[3]["use_my"]

# Slot 4.
CONF_CUSTOM_POSITION_SENSOR_4 = CUSTOM_POSITION_SLOTS[4]["sensor"]
CONF_CUSTOM_POSITION_4 = CUSTOM_POSITION_SLOTS[4]["position"]
CONF_CUSTOM_POSITION_PRIORITY_4 = CUSTOM_POSITION_SLOTS[4]["priority"]
CONF_CUSTOM_POSITION_MIN_MODE_4 = CUSTOM_POSITION_SLOTS[4]["min_mode"]
CONF_CUSTOM_POSITION_USE_MY_4 = CUSTOM_POSITION_SLOTS[4]["use_my"]

CONF_MY_POSITION_VALUE = "my_position_value"  # user's "my" position, 1-99
DEFAULT_CUSTOM_POSITION_PRIORITY = 77  # default priority for a new slot


# =============================================================================
# 15. Weather Override (Safety)
# =============================================================================
# Weather-priority safety handler (priority 90). Retracts/closes covers when
# wind, rain, or other severe conditions warrant it. Threshold units must
# match the configured sensor unit — no conversion is applied.

CONF_WEATHER_WIND_SPEED_SENSOR = "weather_wind_speed_sensor"  # wind-speed entity
CONF_WEATHER_WIND_DIRECTION_SENSOR = "weather_wind_direction_sensor"  # deg entity
# Wind speed above which override fires (range 0-200, in the sensor's unit).
CONF_WEATHER_WIND_SPEED_THRESHOLD = "weather_wind_speed_threshold"
# ± degrees from window azimuth that counts as on-axis wind (range 5-180).
CONF_WEATHER_WIND_DIRECTION_TOLERANCE = "weather_wind_direction_tolerance"
CONF_WEATHER_RAIN_SENSOR = "weather_rain_sensor"  # rain-rate sensor entity_id
# Rain rate above which override fires (range 0-100, in the sensor's unit).
CONF_WEATHER_RAIN_THRESHOLD = "weather_rain_threshold"
CONF_WEATHER_IS_RAINING_SENSOR = "weather_is_raining_sensor"  # binary entity_id
CONF_WEATHER_IS_WINDY_SENSOR = "weather_is_windy_sensor"  # binary entity_id
CONF_WEATHER_SEVERE_SENSORS = "weather_severe_sensors"  # severe-weather list
# Position commanded during weather override (range 0-100).
CONF_WEATHER_OVERRIDE_POSITION = "weather_override_position"
# If True, weather override is only enforced as a min position.
CONF_WEATHER_OVERRIDE_MIN_MODE = "weather_override_min_mode"
CONF_WEATHER_TIMEOUT = "weather_timeout"  # resume delay after clear, s (0-3600)
# If True, weather override fires even when auto control is off.
CONF_WEATHER_BYPASS_AUTO_CONTROL = "weather_bypass_auto_control"

# Threshold unit must match the sensor (no conversion applied).
DEFAULT_WEATHER_WIND_SPEED_THRESHOLD = 50.0
# Degrees each side of window azimuth that counts as on-axis wind.
DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE = 45
# Threshold unit must match the sensor (no conversion applied).
DEFAULT_WEATHER_RAIN_THRESHOLD = 1.0
DEFAULT_WEATHER_TIMEOUT = 300  # seconds before resuming after clear


# =============================================================================
# 16. Automation Timing & Gating
# =============================================================================
# Delta thresholds (position / time) that gate command emission, plus the
# active time-of-day window.

CONF_DELTA_POSITION = "delta_position"  # min % change to emit, range 1-90
CONF_DELTA_TIME = "delta_time"  # min seconds between commands, range 2-60
CONF_START_TIME = "start_time"  # active-window start "HH:MM:SS"
CONF_START_ENTITY = "start_entity"  # input_datetime overriding start_time
CONF_END_TIME = "end_time"  # active-window end "HH:MM:SS"
CONF_END_ENTITY = "end_entity"  # input_datetime overriding end_time


# =============================================================================
# 17. Interpolation
# =============================================================================
# Maps the calc-engine raw position output through a user-defined interpolation
# curve before commanding the cover.

CONF_INTERP = "interp"  # master enable for interpolation
CONF_INTERP_START = "interp_start"  # start of interp domain, % (0-100)
CONF_INTERP_END = "interp_end"  # end of interp domain, % (0-100)
CONF_INTERP_LIST = "interp_list"  # legacy list of control points
CONF_INTERP_LIST_NEW = "interp_list_new"  # new-format control points


# =============================================================================
# 18. Manual Override & Transit
# =============================================================================
# How the integration detects, ignores, and recovers from manual user input on
# the cover. Includes the in-flight transit timeout that suppresses false
# manual-override detection during normal motor travel.

# How long a manual override stays active before automation resumes.
CONF_MANUAL_OVERRIDE_DURATION = "manual_override_duration"
# If True, the manual override is reset when end_time is reached.
CONF_MANUAL_OVERRIDE_RESET = "manual_override_reset"
CONF_MANUAL_THRESHOLD = "manual_threshold"  # % delta = manual touch, 0-99
# If True, intermediate positions don't count as manual touches.
CONF_MANUAL_IGNORE_INTERMEDIATE = "manual_ignore_intermediate"
# Position threshold separating "open" vs "closed" classification, % (1-99).
CONF_OPEN_CLOSE_THRESHOLD = "open_close_threshold"

# Manual override detection grace periods (fixed values, not configurable).
COMMAND_GRACE_PERIOD_SECONDS = 5.0  # ignore position changes after a command
STARTUP_GRACE_PERIOD_SECONDS = 30.0  # disable manual-override after HA startup

# Maximum time (seconds) to suppress manual override detection after sending a
# position command.  Once this threshold is crossed, wait_for_target is cleared
# even if the cover still reports a transitional state ("opening"/"closing").
#
# Purpose: covers that do not report a final state ("stopped"/"open"/"closed")
# when the user stops them mid-transit — only emitting position updates — would
# otherwise keep wait_for_target=True indefinitely, preventing manual override
# detection until the reconciliation timer fired.  This constant caps that
# window at a value that accommodates most motorized blinds and awnings, which
# typically complete a full traverse in 20–40 seconds.  The timeout resets
# whenever the cover makes forward progress toward target, so slow-but-moving
# covers get an extended window proportional to when they last moved.
DEFAULT_TRANSIT_TIMEOUT_SECONDS = 45  # seconds — module-level default
TRANSIT_TIMEOUT_SECONDS = DEFAULT_TRANSIT_TIMEOUT_SECONDS  # back-compat alias

# User-configurable transit timeout (exposed in manual-override config step).
CONF_TRANSIT_TIMEOUT = "transit_timeout"  # per-instance override, seconds
MIN_TRANSIT_TIMEOUT = 15  # seconds — UI lower bound
MAX_TRANSIT_TIMEOUT = 600  # seconds — UI upper bound


# =============================================================================
# 19. Motion Control
# =============================================================================
# Optional motion-triggered handler (priority 75). After motion ceases for
# CONF_MOTION_TIMEOUT seconds, behavior depends on CONF_MOTION_TIMEOUT_MODE.

CONF_MOTION_SENSORS = "motion_sensors"  # binary_sensor list; empty=disabled
CONF_MOTION_TIMEOUT = "motion_timeout"  # no-motion window, s (30-3600)
CONF_MOTION_TIMEOUT_MODE = "motion_timeout_mode"  # one of MOTION_TIMEOUT_MODE_*

MOTION_TIMEOUT_MODE_RETURN = "return_to_default"  # return to default height
MOTION_TIMEOUT_MODE_HOLD = "hold_position"  # hold current position

DEFAULT_MOTION_TIMEOUT = 300  # 5 minutes — default no-motion window
DEFAULT_MOTION_TIMEOUT_MODE = MOTION_TIMEOUT_MODE_RETURN  # default mode


# =============================================================================
# 20. Position Verification
# =============================================================================
# Fixed (non-configurable) constants that drive the periodic check ensuring
# the cover actually reached the commanded position.

POSITION_CHECK_INTERVAL_MINUTES = 1  # minutes — recheck cadence
POSITION_TOLERANCE_PERCENT = 3  # % — "position matches" tolerance
MAX_POSITION_RETRIES = 3  # maximum re-send attempts before giving up


# =============================================================================
# 21. Venetian Dual-Axis Sequencing
# =============================================================================
# Venetian covers move both vertical position AND tilt. The dual-axis sequencer
# (cover_types/venetian/sequencer.py) issues the position command, waits for the
# carriage to settle, then issues the tilt command. Constants in this section
# govern that handshake and the venetian-specific mode/clamp options.

# After a position command lands, the service polls current_position every
# poll-interval seconds, declares the cover "settled" when the position matches
# the target within the standard tolerance OR has not changed for three
# consecutive samples, and proceeds to the tilt command.  Hard cap at the
# timeout so a stuck cover does not block the rest of the update cycle.
VENETIAN_POSITION_SETTLE_POLL_SECONDS = 0.5  # poll interval while settling
VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS = 60.0  # hard cap on settle wait
VENETIAN_POSITION_SETTLE_NO_CHANGE_SAMPLES = 3  # samples → "settled"

# Suppress tilt-axis manual override detection for this many seconds after a
# venetian position command. Real motors back-rotate the slats while moving
# vertically, and that drift would otherwise read as a user touch.
VENETIAN_TILT_SUPPRESSION_SECONDS = 90.0  # tilt-axis override-suppression window

# Refuse to absorb post-tilt drift larger than this many percent. Real-motor
# back-drive is single-digit percent; a large delta (e.g. user opened the blind
# during settle) must not be silently adopted as the new commanded target.
VENETIAN_REBASE_MAX_DRIFT_PERCENT = 15

# Cap the tilt delta the back-rotate suppression window will swallow. Slat
# geometry bounds mechanical back-rotation; a delta above this is a user move,
# so the manual-override path runs even inside the suppression window.
VENETIAN_BACKROTATE_MAX_DELTA_PERCENT = 30

# After set_cover_tilt_position returns, real motors keep back-driving the
# vertical axis briefly. Wait this many seconds before reading current_position
# for the post-tilt rebase so the rebase captures the actual settled position
# rather than the pre-back-drive snapshot.
VENETIAN_POST_TILT_REBASE_DELAY_SECONDS = 1.5  # wait before post-tilt rebase

# Drift tolerance for tilt verification: if actual tilt differs from the sent
# target by more than this many percent after the post-tilt delay, the recorded
# target is cleared so the next update_tilt_only cycle retries the command.
VENETIAN_TILT_VERIFY_TOLERANCE = 5  # percent — tilt-verification tolerance

# Verify-with-retry budget. _verify_and_record_tilt reads current_tilt_position
# up to MAX_SAMPLES times, POLL_SECONDS apart, accepting on the first
# in-tolerance sample. KNX/Shelly actuators publish post-tilt state via state
# updates that can lag 1–3 s past VENETIAN_POST_TILT_REBASE_DELAY_SECONDS; a
# single-shot read misreads that lag as drift and triggers a phantom retry
# next cycle (issue #33).
VENETIAN_TILT_VERIFY_MAX_SAMPLES = 4  # total reads (1 immediate + 3 retries)
VENETIAN_TILT_VERIFY_POLL_SECONDS = 1.0  # sleep between retry reads

# Hold delay between position settle and the tilt command. Some actuators
# perform a firmware tilt-reassert after the carriage reports closed/open
# (e.g. FGR223): firing the tilt command immediately races that reassert.
# The value is configurable per-instance; the module-level default is consumed
# only when no instance config is available (e.g. unit tests).
CONF_VENETIAN_POST_SETTLE_HOLD = "venetian_post_settle_hold"  # s, 0.0-10.0
DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS = 3.0  # default post-settle hold

# Skip the tilt command when the commanded position exceeds this threshold —
# at high positions the slats are retracted into the housing and tilting is
# physically meaningless. The value is configurable per-instance.
CONF_VENETIAN_TILT_SKIP_ABOVE = "venetian_tilt_skip_above"  # %, 50-100
DEFAULT_VENETIAN_TILT_SKIP_ABOVE = 95  # percent — default skip-tilt threshold
MIN_VENETIAN_TILT_SKIP_ABOVE = 50  # UI lower bound
MAX_VENETIAN_TILT_SKIP_ABOVE = 100  # UI upper bound

# Venetian cover operating mode.  position_and_tilt tracks both axes with solar
# geometry; tilt_only closes the cover to 0% and tracks only the slat angle.
CONF_VENETIAN_MODE = "venetian_mode"  # one of VENETIAN_MODES
VENETIAN_MODE_POSITION_AND_TILT = "position_and_tilt"  # track position AND tilt
VENETIAN_MODE_TILT_ONLY = "tilt_only"  # hold at 0%, track tilt only
DEFAULT_VENETIAN_MODE = VENETIAN_MODE_POSITION_AND_TILT  # default mode
VENETIAN_MODES = (VENETIAN_MODE_POSITION_AND_TILT, VENETIAN_MODE_TILT_ONLY)


# =============================================================================
# 22. Debug & Diagnostics
# =============================================================================
# Debug-mode toggle, per-category logging filters, the rolling event buffer,
# and dry-run mode (suppresses outgoing cover commands).

CONF_DEBUG_MODE = "debug_mode"  # master debug switch
CONF_DEBUG_CATEGORIES = "debug_categories"  # list of DEBUG_CATEGORY_* strings
CONF_DEBUG_EVENT_BUFFER_SIZE = "debug_event_buffer_size"  # see MAX_ below
CONF_DRY_RUN = "dry_run"  # log commands without sending them

DEBUG_CATEGORY_MANUAL_OVERRIDE = "manual_override"  # manual-override events
DEBUG_CATEGORY_RECONCILIATION = "reconciliation"  # reconciliation logic
DEBUG_CATEGORY_PIPELINE = "pipeline"  # pipeline handler trace
DEBUG_CATEGORY_MOTION = "motion"  # motion handler events
DEBUG_CATEGORIES_ALL = [
    DEBUG_CATEGORY_MANUAL_OVERRIDE,
    DEBUG_CATEGORY_RECONCILIATION,
    DEBUG_CATEGORY_PIPELINE,
    DEBUG_CATEGORY_MOTION,
]  # full set of categories, used as the config_flow selector source

DEFAULT_DEBUG_EVENT_BUFFER_SIZE = 250  # default rolling-buffer size
MAX_DEBUG_EVENT_BUFFER_SIZE = 1000  # hard upper bound on rolling buffer


# =============================================================================
# 23. Control Status (diagnostic enum)
# =============================================================================
# String identifiers exposed by the diagnostic "control_status" sensor so
# users can see which pipeline branch is currently active.


class ControlStatus:
    """Control-status values reported by the diagnostic sensor.

    Each value reflects the reason the integration is (or is not) currently
    commanding the cover. Surfaced verbatim in diagnostics and the Lovelace
    card; do not rename without updating downstream consumers.
    """

    ACTIVE = "active"  # actively tracking the sun / running automation
    OUTSIDE_TIME_WINDOW = "outside_time_window"  # outside start/end window
    POSITION_DELTA_TOO_SMALL = "position_delta_too_small"  # < CONF_DELTA_POSITION
    TIME_DELTA_TOO_SMALL = "time_delta_too_small"  # < CONF_DELTA_TIME
    MANUAL_OVERRIDE = "manual_override"  # manual override active
    AUTOMATIC_CONTROL_OFF = "automatic_control_off"  # auto-control toggled off
    SUN_NOT_VISIBLE = "sun_not_visible"  # sun outside elevation/FOV
    FORCE_OVERRIDE_ACTIVE = "force_override_active"  # priority-100 handler
    WEATHER_OVERRIDE_ACTIVE = "weather_override_active"  # priority-90 handler
    MOTION_TIMEOUT = "motion_timeout"  # priority-75 handler fired


# =============================================================================
# 24. Geometric Accuracy (calc engine)
# =============================================================================
# Edge-case thresholds and safety-margin multipliers used in calculation.py
# and engine/sun_geometry.py. Tuning these affects how aggressively the
# integration retreats from extreme sun geometries.

# Edge-case thresholds for extreme sun positions.
EDGE_CASE_LOW_ELEVATION = 2.0  # deg — below this, use low-elev path
EDGE_CASE_HIGH_ELEVATION = 88.0  # deg — above this, use high-elev path
EDGE_CASE_EXTREME_GAMMA = 85  # deg — max horizontal angle considered

# Safety margin thresholds and multipliers.
SAFETY_MARGIN_GAMMA_THRESHOLD = 45  # deg — angle where gamma margins start
SAFETY_MARGIN_GAMMA_MAX = 0.2  # +20% at extreme horizontal angles (>45°)
SAFETY_MARGIN_LOW_ELEV_THRESHOLD = 10  # deg — low-angle margin threshold
SAFETY_MARGIN_LOW_ELEV_MAX = 0.15  # +15% at low sun elevation (<10°)
SAFETY_MARGIN_HIGH_ELEV_THRESHOLD = 75  # deg — high-angle margin threshold
SAFETY_MARGIN_HIGH_ELEV_MAX = 0.1  # +10% at high sun elevation (>75°)

# Window depth calculation threshold.
WINDOW_DEPTH_GAMMA_THRESHOLD = 10  # deg — min gamma for depth contribution


# =============================================================================
# 25. UI Defaults & Validation Caps
# =============================================================================
# Default values shown in the config-flow UI and hard caps not derived from
# OPTION_RANGES (used for legacy schema validation).

DEFAULT_WINDOW_HEIGHT = 2.1  # metres — config-flow default
DEFAULT_AWNING_LENGTH = 2.1  # metres — config-flow default
DEFAULT_WINDOW_AZIMUTH = 180  # degrees — config-flow default (south-facing)
MAX_WINDOW_DEPTH = 5.0  # metres — UI cap for window depth
MAX_AWNING_ANGLE = 45  # degrees — UI cap for awning tilt
DEGREES_IN_CIRCLE = 360  # used for azimuth/wind-direction wrap-around math


# =============================================================================
# 26. Numeric Option Ranges (single source of truth)
# =============================================================================
# Each ``_RANGE_*`` tuple is ``(min, max)`` for the named CONF_* option.
# ``OPTION_RANGES`` (built at module import) is the dict consumed by both
# ``config_flow.py`` selectors and ``services/options_service.FIELD_VALIDATORS``
# — keep ranges defined here, not duplicated at the call sites.

# Geometry — vertical blind.
_RANGE_HEIGHT_WIN = (0.1, 50.0)  # CONF_HEIGHT_WIN, metres
_RANGE_WINDOW_WIDTH = (0.1, 50.0)  # CONF_WINDOW_WIDTH, metres
_RANGE_WINDOW_DEPTH = (0.0, 5.0)  # CONF_WINDOW_DEPTH, metres
_RANGE_SILL_HEIGHT = (0.0, 50.0)  # CONF_SILL_HEIGHT, metres

# Geometry — awning.
_RANGE_LENGTH_AWNING = (0.3, 6.0)  # CONF_LENGTH_AWNING, metres
_RANGE_AWNING_ANGLE = (0, 45)  # CONF_AWNING_ANGLE, degrees

# Geometry — tilt / venetian slats.
_RANGE_TILT_DEPTH = (0.1, 15.0)  # CONF_TILT_DEPTH, cm
_RANGE_TILT_DISTANCE = (0.1, 15.0)  # CONF_TILT_DISTANCE, cm
_RANGE_MAX_TILT = (0, 100)  # CONF_MAX_TILT, percent
_RANGE_MIN_TILT = (0, 100)  # CONF_MIN_TILT, percent

# Sun tracking.
_RANGE_AZIMUTH = (0, 359)  # CONF_AZIMUTH, degrees
_RANGE_FOV = (0, 180)  # CONF_FOV_LEFT / CONF_FOV_RIGHT, degrees
_RANGE_ELEVATION = (0, 90)  # min/max elevation, degrees
_RANGE_DISTANCE = (0.1, 50.0)  # CONF_DISTANCE, metres

# Blind spot.
# Asymmetric LEFT vs RIGHT bounds are a historical quirk; preserved for compat.
_RANGE_BLIND_SPOT_LEFT = (0, 359)  # CONF_BLIND_SPOT_LEFT, degrees
_RANGE_BLIND_SPOT_RIGHT = (0, 360)  # CONF_BLIND_SPOT_RIGHT, degrees
_RANGE_BLIND_SPOT_ELEVATION = (0, 90)  # CONF_BLIND_SPOT_ELEVATION, degrees

# Position limits & sunset.
_RANGE_DEFAULT_HEIGHT = (0, 100)  # CONF_DEFAULT_HEIGHT, percent
_RANGE_MAX_POSITION = (1, 100)  # CONF_MAX_POSITION, percent
_RANGE_MIN_POSITION = (0, 99)  # CONF_MIN_POSITION, percent
_RANGE_SUNSET_POS = (0, 100)  # CONF_SUNSET_POS, percent
_RANGE_MY_POSITION = (1, 99)  # CONF_MY_POSITION_VALUE, percent
_RANGE_OFFSET_MINUTES = (-120, 120)  # sunset/sunrise offsets, minutes
_RANGE_OPEN_CLOSE_THRESHOLD = (1, 99)  # CONF_OPEN_CLOSE_THRESHOLD, percent

# Interpolation.
_RANGE_INTERP_VALUE = (0, 100)  # interp start/end, percent

# Automation timing.
_RANGE_DELTA_POSITION = (1, 90)  # CONF_DELTA_POSITION, percent
_RANGE_DELTA_TIME = (2, 60)  # CONF_DELTA_TIME, seconds

# Manual override.
_RANGE_MANUAL_THRESHOLD = (0, 99)  # CONF_MANUAL_THRESHOLD, percent

# Force override / custom positions.
_RANGE_FORCE_POSITION = (0, 100)  # CONF_FORCE_OVERRIDE_POSITION, percent
_RANGE_CUSTOM_POSITION = (0, 100)  # per-slot custom position, percent
_RANGE_CUSTOM_PRIORITY = (1, 99)  # per-slot custom priority
_RANGE_TILT = (0, 100)  # per-slot/default/sunset tilt, percent

# Motion.
_RANGE_MOTION_TIMEOUT = (30, 3600)  # CONF_MOTION_TIMEOUT, seconds

# Climate.
_RANGE_TEMPERATURE = (0, 90)  # temp_low / temp_high (sensor unit)
_RANGE_OUTSIDE_THRESHOLD = (0, 100)  # CONF_OUTSIDE_THRESHOLD (sensor unit)

# Weather safety.
_RANGE_WEATHER_WIND_SPEED = (0, 200)  # wind-speed threshold (sensor unit)
_RANGE_WEATHER_WIND_DIRECTION_TOLERANCE = (5, 180)  # wind-direction tol, deg
_RANGE_WEATHER_RAIN = (0, 100)  # rain threshold (sensor unit)
_RANGE_WEATHER_OVERRIDE_POSITION = (0, 100)  # weather-override pos, percent
_RANGE_WEATHER_TIMEOUT = (0, 3600)  # weather-resume timeout, seconds

# Venetian sequencing.
_RANGE_VENETIAN_POST_SETTLE_HOLD = (0.0, 10.0)  # post-settle hold, seconds
_RANGE_VENETIAN_TILT_SKIP_ABOVE = (
    MIN_VENETIAN_TILT_SKIP_ABOVE,
    MAX_VENETIAN_TILT_SKIP_ABOVE,
)  # CONF_VENETIAN_TILT_SKIP_ABOVE, percent


def _build_option_ranges() -> dict[str, tuple[float, float]]:
    """Map every numeric option to its ``(min, max)`` range.

    Built lazily in a function so the module-level dict ordering stays sane
    (constants above are grouped by domain). Consumers should treat the
    returned dict as immutable.
    """
    ranges: dict[str, tuple[float, float]] = {
        CONF_HEIGHT_WIN: _RANGE_HEIGHT_WIN,
        CONF_WINDOW_WIDTH: _RANGE_WINDOW_WIDTH,
        CONF_WINDOW_DEPTH: _RANGE_WINDOW_DEPTH,
        CONF_SILL_HEIGHT: _RANGE_SILL_HEIGHT,
        CONF_LENGTH_AWNING: _RANGE_LENGTH_AWNING,
        CONF_AWNING_ANGLE: _RANGE_AWNING_ANGLE,
        CONF_TILT_DEPTH: _RANGE_TILT_DEPTH,
        CONF_TILT_DISTANCE: _RANGE_TILT_DISTANCE,
        CONF_AZIMUTH: _RANGE_AZIMUTH,
        CONF_FOV_LEFT: _RANGE_FOV,
        CONF_FOV_RIGHT: _RANGE_FOV,
        CONF_MIN_ELEVATION: _RANGE_ELEVATION,
        CONF_MAX_ELEVATION: _RANGE_ELEVATION,
        CONF_DISTANCE: _RANGE_DISTANCE,
        CONF_BLIND_SPOT_LEFT: _RANGE_BLIND_SPOT_LEFT,
        CONF_BLIND_SPOT_RIGHT: _RANGE_BLIND_SPOT_RIGHT,
        CONF_BLIND_SPOT_ELEVATION: _RANGE_BLIND_SPOT_ELEVATION,
        CONF_DEFAULT_HEIGHT: _RANGE_DEFAULT_HEIGHT,
        CONF_MAX_POSITION: _RANGE_MAX_POSITION,
        CONF_MIN_POSITION: _RANGE_MIN_POSITION,
        CONF_SUNSET_POS: _RANGE_SUNSET_POS,
        CONF_MY_POSITION_VALUE: _RANGE_MY_POSITION,
        CONF_SUNSET_OFFSET: _RANGE_OFFSET_MINUTES,
        CONF_SUNRISE_OFFSET: _RANGE_OFFSET_MINUTES,
        CONF_OPEN_CLOSE_THRESHOLD: _RANGE_OPEN_CLOSE_THRESHOLD,
        CONF_INTERP_START: _RANGE_INTERP_VALUE,
        CONF_INTERP_END: _RANGE_INTERP_VALUE,
        CONF_DELTA_POSITION: _RANGE_DELTA_POSITION,
        CONF_DELTA_TIME: _RANGE_DELTA_TIME,
        CONF_MANUAL_THRESHOLD: _RANGE_MANUAL_THRESHOLD,
        CONF_FORCE_OVERRIDE_POSITION: _RANGE_FORCE_POSITION,
        CONF_MOTION_TIMEOUT: _RANGE_MOTION_TIMEOUT,
        CONF_TEMP_LOW: _RANGE_TEMPERATURE,
        CONF_TEMP_HIGH: _RANGE_TEMPERATURE,
        CONF_OUTSIDE_THRESHOLD: _RANGE_OUTSIDE_THRESHOLD,
        CONF_WEATHER_WIND_SPEED_THRESHOLD: _RANGE_WEATHER_WIND_SPEED,
        CONF_WEATHER_WIND_DIRECTION_TOLERANCE: _RANGE_WEATHER_WIND_DIRECTION_TOLERANCE,
        CONF_WEATHER_RAIN_THRESHOLD: _RANGE_WEATHER_RAIN,
        CONF_WEATHER_OVERRIDE_POSITION: _RANGE_WEATHER_OVERRIDE_POSITION,
        CONF_WEATHER_TIMEOUT: _RANGE_WEATHER_TIMEOUT,
        CONF_MAX_TILT: _RANGE_MAX_TILT,
        CONF_MIN_TILT: _RANGE_MIN_TILT,
        CONF_VENETIAN_POST_SETTLE_HOLD: _RANGE_VENETIAN_POST_SETTLE_HOLD,
        CONF_VENETIAN_TILT_SKIP_ABOVE: _RANGE_VENETIAN_TILT_SKIP_ABOVE,
    }
    # Custom-position slots: per-slot position (0–100), priority (1–99), tilt (0–100).
    for slot_keys in CUSTOM_POSITION_SLOTS.values():
        ranges[slot_keys["position"]] = _RANGE_CUSTOM_POSITION
        ranges[slot_keys["priority"]] = _RANGE_CUSTOM_PRIORITY
        ranges[slot_keys["tilt"]] = _RANGE_TILT
    # Global default and sunset tilt (venetian only).
    ranges[CONF_DEFAULT_TILT] = _RANGE_TILT
    ranges[CONF_SUNSET_TILT] = _RANGE_TILT
    return ranges


# Exported single source of truth — built at module import.
OPTION_RANGES: dict[str, tuple[float, float]] = _build_option_ranges()
