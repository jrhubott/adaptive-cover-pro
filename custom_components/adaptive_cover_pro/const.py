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
 8a. Forecast Timeline
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
27. Enumerations (semantic identifiers)
"""

import logging
from dataclasses import dataclass
from enum import Enum, StrEnum

# =============================================================================
# 1. Module Identity & Logging
# =============================================================================
# Domain string, package-level loggers, and HA service-call attribute keys.

DOMAIN = "adaptive_cover_pro"  # HA integration domain; must match manifest.json
# hass.data slot holding the last-good diagnostics snapshot per entry_id. Lives
# outside the coordinator so it survives a reload (when entry.runtime_data is
# briefly unset) and can be served by the diagnostics download as a stale fallback.
DIAG_CACHE_KEY = f"{DOMAIN}_last_diagnostics"
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

CONF_SENSOR_TYPE = "sensor_type"  # one of CoverType.* (see section 27)
CONF_DEVICE_ID = "linked_device_id"  # HA device_id to link this instance to
CONF_BUILDING_PROFILE_ID = (
    "building_profile_id"  # entry_id of a linked Building Profile
)
# Shared-sensor keys a linked cover has overridden locally (inherit/override
# model). Keys NOT in this list track the profile; keys in it keep the cover's
# own value and are skipped by profile propagation. Absent = no overrides.
CONF_PROFILE_SENSOR_OVERRIDES = "profile_sensor_overrides"
# Transient OptionsFlow field for the "Copy to Other Covers" sync step (#772).
# Never persisted to config_entry.options. When True, the sync targets every
# same-type other cover except those checked in ``target_entries`` (the
# multi-select becomes an exclude list instead of an include list).
CONF_SYNC_SELECT_ALL = "select_all_targets"

# Cover-group membership rosters, stored on the GROUP entry only (issue #790).
# Two lists because the member kinds are structural: ACP members are config
# entries (orchestrated through their own coordinator — an ACP instance may
# expose no cover entity at all when the proxy is off), generic members are
# plain ``cover.*`` entity_ids (adopt mode, commanded directly).
CONF_MEMBER_ENTRIES = "member_entries"  # ACP member config-entry ids
CONF_MEMBER_COVERS = "member_covers"  # generic cover entity_ids

# Optional area-based membership source (issue #790, Phase 4). LIVE, not a
# one-shot seed: the effective rosters are the static lists ∪ the area's
# resolved members (ACP entries with a controlled cover in the area; free
# ``cover.*`` entities in the area), re-resolved on registry changes.
CONF_GROUP_AREA = "group_area"

# Per-member scene opt-out, stored on the GROUP entry keyed by member
# entry_id so a member removal prunes cleanly: {member_id: [scene, ...]}.
# The sentinel OPT_OUT_ALL_SCENES ("*") opts a member out of every scene.
# Enforced group-side at intent-push time (the member handler stays dumb);
# the group lock ignores opt-out — it is a safety claim on every member.
CONF_GROUP_MEMBER_OPT_OUT = "group_member_opt_out"
OPT_OUT_ALL_SCENES = "*"

# Seconds between successive member commands during a group fan-out
# (scene activation). 0 = simultaneous. Issue #790 Phase 2.
CONF_GROUP_STAGGER_DELAY = "group_stagger_delay"
DEFAULT_GROUP_STAGGER_DELAY = 0.0
_RANGE_GROUP_STAGGER = (0.0, 30.0)

# The scene select's "no scene" option (wire-stable select state). Not a
# GroupScene member — it is the absence of a scene: choosing it clears the
# group's claim so members return to their own pipelines.
GROUP_SCENE_SELECT_AUTO = "auto"

# Entity-exposure toggles for a group entry (issue #790, Phase 3). The
# aggregate cover entity defaults ON so a group is controllable as a single
# cover out of the box; users opt out per-group to reduce dashboard/voice
# clutter. The sensors also default ON. The active-scene sensor, scene
# controls, and bulk switches are always created (primary control surface,
# no toggle).
CONF_GROUP_ENABLE_COVER_ENTITY = "group_enable_cover_entity"
DEFAULT_GROUP_ENABLE_COVER_ENTITY = True
CONF_GROUP_ENABLE_POSITION_SENSOR = "group_enable_position_sensor"
CONF_GROUP_ENABLE_STATE_SENSOR = "group_enable_state_sensor"
CONF_GROUP_ENABLE_CLIMATE_SENSOR = "group_enable_climate_sensor"
CONF_GROUP_ENABLE_WHO_WON_SENSOR = "group_enable_who_won_sensor"
DEFAULT_GROUP_ENABLE_SENSOR = True  # shared default for the four sensor toggles

# Default name prefix used by the config flow when auto-naming a cover from its
# entity's friendly name (no linked device available) — see config_flow.py's
# cover_entities auto-fill and async_step_update finalization fallback (#771).
ADAPTIVE_NAME_PREFIX = "Adaptive"


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
CONF_DISTANCE = "distance_shaded_area"  # blind→shaded distance, m (0.0-50.0)
# Roof / skylight window geometry (#212). A roof window is a vertical-style
# blind travelling down-slope across pitched glass; it reuses the window
# width/height/depth/sill/distance fields above and adds these two.
CONF_ROOF_PITCH = "roof_pitch"  # glass pitch from horizontal, deg (0=flat, 90=vertical)
CONF_ROOF_HEIGHT_ABOVE = (
    "roof_height_above"  # along-slope roof above window, m (0=no ridge gate)
)
DEFAULT_ROOF_PITCH = 40  # degrees — typical Velux roof window pitch
DEFAULT_ROOF_HEIGHT_ABOVE = 0.0  # metres — 0 disables the ridge occlusion gate
# Sliding-curtain shade-area geometry (#829, Part 2). A horizontal sliding
# curtain can close a continuous fraction across the window from a two-point
# shade area given in floor Cartesian coordinates (x = along the wall, signed,
# positive = right facing the window from inside; y = depth into the room, > 0)
# — the SAME frame as glare zones. When the shade area is disabled the curtain
# falls back to the Part 1 binary open/close. ``SlideDirection`` / the default
# live in the enums block near ``CoverType`` (needed before the config_fields
# import); the flat point/width keys reuse the shared ``window_width`` field.
CONF_SLIDING_ENABLE_SHADE_AREA = "sliding_enable_shade_area"  # bool master toggle
CONF_SLIDING_SLIDE_DIRECTION = (
    "sliding_slide_direction"  # one of SLIDING_SLIDE_DIRECTIONS
)
CONF_SLIDING_POINT1_X = "sliding_point1_x"  # shade point 1, along-wall x, m (signed)
CONF_SLIDING_POINT1_Y = "sliding_point1_y"  # shade point 1, depth into room, m (>0)
CONF_SLIDING_POINT2_X = "sliding_point2_x"  # shade point 2, along-wall x, m (signed)
CONF_SLIDING_POINT2_Y = "sliding_point2_y"  # shade point 2, depth into room, m (>0)
DEFAULT_SLIDING_ENABLE_SHADE_AREA = False
DEFAULT_SLIDING_POINT_X = 0.0  # metres
DEFAULT_SLIDING_POINT_Y = 0.0  # metres — y <= 0 → curtain fully open (guard)
# Louvered (lamella) roof plane pitch default (#830). A louvered roof reuses
# CONF_ROOF_PITCH / _RANGE_ROOF_PITCH but defaults to a FLAT plane (0° from
# horizontal), unlike the roof window's 40° glass default.
DEFAULT_LOUVERED_ROOF_PITCH = 0  # degrees from horizontal — 0 = flat roof
# Louvered-roof physical max slat angle (#830 follow-up). Optional override of
# the tilt-mode's 90°/180° ceiling for pergola drives whose mechanical travel is
# neither (e.g. a 0–160° motor). 0 (the sentinel default) = use the tilt mode's
# max. When set it becomes BOTH the clamp ceiling AND the tilt%→angle denominator.
CONF_MAX_SLAT_ANGLE = "max_slat_angle"  # degrees; 0 = use tilt-mode max
DEFAULT_MAX_SLAT_ANGLE = 0  # 0 sentinel → tilt mode's 90/180
# Louvered-roof slat geometry defaults (#830 follow-up). Pergola lamellae are
# far larger than interior venetian slats (whose defaults stay 3.0/2.0 cm), so
# the louvered schema overrides these two markers only. Values are the #830
# reference rig (17 cm deep, 15 cm apart → 0.88 spacing ratio), in canonical cm.
DEFAULT_LOUVERED_SLAT_DEPTH_CM = 17.0  # CONF_TILT_DEPTH default for louvered roofs
DEFAULT_LOUVERED_SLAT_DISTANCE_CM = 15.0  # CONF_TILT_DISTANCE default, louvered roofs
CONF_FOV_LEFT = "fov_left"  # left half-FOV from azimuth, degrees 0-180
CONF_FOV_RIGHT = "fov_right"  # right half-FOV from azimuth, degrees 0-180
DEFAULT_FOV_LEFT = 90  # degrees; matches config flow default
DEFAULT_FOV_RIGHT = 90  # degrees; matches config flow default
CONF_FOV_COMPUTE = "fov_compute"  # transient form button: derive fov_left/right
# from window width + reveal depth (#565). Never persisted. Legacy ``fov_mode``
# values left in older entries' options are inert — the engine reads
# ``fov_left``/``fov_right`` only — and are dropped on the next sun-tracking save.
CONF_ENTITIES = "group"  # list of HA cover entity_ids controlled
CONF_ENABLE_PROXY_COVER = "enable_proxy_cover"  # opt-in proxy cover platform
DEFAULT_ENABLE_PROXY_COVER = False
TRIGGER_PROXY_POSITION = "proxy_managed"
TRIGGER_PROXY_OPEN = "proxy_open"
TRIGGER_PROXY_CLOSE = "proxy_close"
TRIGGER_PROXY_TILT = "proxy_tilt"
# Cover-group aggregate cover entity commands (issue #790, Phase 3).
TRIGGER_GROUP_COVER = "group_cover"
TRIGGER_GROUP_COVER_TILT = "group_cover_tilt"


# =============================================================================
# 4. Awning Geometry
# =============================================================================
# Horizontal awning dimensions (extension length and tilt).

CONF_HEIGHT_AWNING = "height_awning"  # mount height above ground, metres
CONF_LENGTH_AWNING = "length_awning"  # extension length, metres (0.3-6.0)
CONF_AWNING_ANGLE = "angle"  # tilt from horizontal, degrees (0-45)

# Oscillating (drop-arm / pivoting) awning geometry. Unlike the fixed-angle
# awning, the arm sweeps through an arc as it opens, so the fabric angle is a
# function of the open percentage rather than a configured constant. See #412.
CONF_ARM_LENGTH = "arm_length"  # pivot-arm length, metres (0.1-6.0)
CONF_AWNING_MIN_ANGLE = "awning_min_angle"  # arm angle when closed, deg (0-180)
CONF_AWNING_MAX_ANGLE = "awning_max_angle"  # arm angle when fully open, deg (0-180)
# Vertical offset of the arm pivot above the window top, metres (0-1). Used with
# window height and arm length to locate the pivot.
CONF_AWNING_HOUSING_OFFSET = "awning_housing_offset"
# Horizontal distance from the arm pivot / fabric plane to the window glass,
# metres (0-2). At low sun the dropped fabric stands off the pane by this much,
# so its shadow projects lower on the glass. See #586 follow-up.
CONF_AWNING_PIVOT_OFFSET = "awning_pivot_offset"
DEFAULT_ARM_LENGTH = 0.8  # metres
DEFAULT_AWNING_MIN_ANGLE = 0  # degrees — arm vertical / fully retracted
DEFAULT_AWNING_MAX_ANGLE = 175  # degrees — reporter's full sweep (#412)
DEFAULT_AWNING_HOUSING_OFFSET = 0.0  # metres
DEFAULT_AWNING_PIVOT_OFFSET = 0.0  # metres

# Vertical-drop (lip-height) shade model for the oscillating awning (#586).
# The drop-arm's fabric lip descends as the arm sweeps past horizontal, shading
# the window face down to a protected boundary. The solver scans the arm-sweep
# arc and selects the smallest angle whose lip shadow reaches the boundary.
#
# Default/fallback protected boundary on the window face (window-bottom datum,
# metres). The LIVE boundary is derived from the inherited vertical sill/depth/
# distance solve (the exposed-glass height); this constant is only the fallback
# when that solve leaves the whole face exposed.
OSCILLATING_PROTECTED_BOUNDARY_DEFAULT = 0.0  # metres (window bottom)
# Arc-scan resolution: number of arm-angle samples across the [min, max] sweep.
# 0.1° steps over a 180° sweep — fine enough that the pinned positions are
# stable to <0.1%.
OSCILLATING_ARC_SCAN_SAMPLES = 1801


# =============================================================================
# 5. Tilt / Venetian Slat Geometry
# =============================================================================
# Slat dimensions used to compute tilt angle, plus min/max tilt clamps.

CONF_TILT_DEPTH = "slat_depth"  # slat depth, cm (range 0.1-30.0)
CONF_TILT_DISTANCE = "slat_distance"  # vertical slat spacing, cm (0.1-30.0)
CONF_TILT_MODE = "tilt_mode"  # tilt strategy identifier
CONF_TILT_ANGLE_0 = "tilt_angle_0"  # raw slat angle at 0% tilt, degrees
CONF_TILT_ANGLE_100 = "tilt_angle_100"  # raw slat angle at 100% tilt, degrees
DEFAULT_TILT_ANGLE_0 = 0  # degrees — downward closed endpoint at 0% tilt
DEFAULT_TILT_ANGLE_100 = 180  # degrees — upward closed endpoint at 100% tilt
CONF_MAX_TILT = "max_tilt"  # cap on sun-derived tilt %, 0-100
DEFAULT_MAX_TILT = 100  # default: no upper cap
CONF_MIN_TILT = "min_tilt"  # floor on sun-derived tilt %, 0-100
DEFAULT_MIN_TILT = 0  # default: no lower floor
# If True, min_tilt/max_tilt are only enforced during active sun tracking —
# mirroring CONF_ENABLE_MIN/MAX_POSITION. False (default) = always enforce,
# including the sun-invalid default_tilt path (issue #503/#629). Sunset and
# custom-position tilts are deliberate carve-outs and are never affected.
CONF_MIN_TILT_SUN_ONLY = "min_tilt_sun_only"
DEFAULT_MIN_TILT_SUN_ONLY = False
CONF_MAX_TILT_SUN_ONLY = "max_tilt_sun_only"
DEFAULT_MAX_TILT_SUN_ONLY = False

# Slat angle (degrees) at which the slats sit horizontal — the geometric pivot
# the safety-margin transform closes away from. MODE1: 90° = fully open.
TILT_HORIZONTAL_DEG = 90

# Configurable venetian tilt safety margin (issue #783): scales the automatic
# angle-dependent geometry margin (0.0 = no-op = today's exact grazing angle,
# 1.0 = full geometry margin applied in the closing direction).
CONF_VENETIAN_TILT_SAFETY_MARGIN = "venetian_tilt_safety_margin"
DEFAULT_VENETIAN_TILT_SAFETY_MARGIN = 0.0
MIN_VENETIAN_TILT_SAFETY_MARGIN = 0.0
MAX_VENETIAN_TILT_SAFETY_MARGIN = 1.0


# =============================================================================
# 6. Position Limits & Inverse State
# =============================================================================
# Hard min/max position clamps, default-when-not-tracking, the inverse-state
# flags (some covers report 0=open instead of 0=closed), and the two named
# fixed points POSITION_CLOSED / POSITION_OPEN used widely in calc code.

CONF_MAX_POSITION = "max_position"  # upper clamp on commanded position (0-100)
CONF_MIN_POSITION = "min_position"  # lower clamp on commanded position (0-99)
# Optional separate floor that applies only during sun tracking (0-99, optional).
# When set, overrides CONF_MIN_POSITION for sun-tracking paths only.
# None (unset) means fall back to CONF_MIN_POSITION.
CONF_MIN_POSITION_SUN_TRACKING = "min_position_sun_tracking"
# If True, max_position is only enforced during active sun tracking.
CONF_ENABLE_MAX_POSITION = "enable_max_position"
# If True, min_position is only enforced during active sun tracking.
CONF_ENABLE_MIN_POSITION = "enable_min_position"
# If True, the position/tilt delta (min_change) gate is also enforced for the
# fully-open (100) and fully-closed (0) endpoints. Default False preserves the
# issue #629 "always send to 0/100" guarantee. Useful on mechanically coupled
# covers where commanding a full endpoint disturbs tilt (issue #679).
CONF_ENFORCE_DELTA_AT_ENDPOINTS = "enforce_delta_at_endpoints"
DEFAULT_ENFORCE_DELTA_AT_ENDPOINTS = False
# If True, a final post-inverse target of 100 is sent via cover.open_cover and a
# final target of 0 via cover.close_cover, instead of set_cover_position(100/0).
# Targets 1-99 still use set_cover_position. Falls back to set_cover_position
# when the cover lacks open/close, and never applies to a tilt-only axis.
# Default True (issue #697): open/close drives a full traverse more reliably on
# many actuators than set_position to an endpoint.
CONF_ENDPOINT_USE_OPEN_CLOSE = "endpoint_use_open_close"
DEFAULT_ENDPOINT_USE_OPEN_CLOSE = True
# Fallback position when no override applies, % (range 0-100).
CONF_DEFAULT_HEIGHT = "default_percentage"
# Effective default position when no `default_percentage` is configured.
# 0 % = closed; matches the historical fallback in coordinator.get_blind_data.
DEFAULT_DEFAULT_HEIGHT = 0
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
# Opt-in movement minimization: quantize the sun-tracked position into at most
# N evenly-spaced coverage levels, rounding TOWARD full coverage so protection
# is never reduced. N=1 snaps straight to full coverage while the sun is in FOV.
CONF_MINIMIZE_MOVEMENTS = "minimize_movements"  # opt-in toggle
CONF_MAX_COVERAGE_STEPS = "max_coverage_steps"  # discrete coverage levels, 1-10
DEFAULT_MINIMIZE_MOVEMENTS = False
DEFAULT_MAX_COVERAGE_STEPS = 1
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
# Optional end-of-window position 0-100 (issue #625); None=disabled. Applied at the
# operating-window end time (gated by CONF_RETURN_SUNSET) regardless of astral sunset.
CONF_END_OF_WINDOW_POS = "end_of_window_position"
# If True, sunset position uses CONF_MY_POSITION_VALUE instead of CONF_SUNSET_POS.
CONF_SUNSET_USE_MY = "sunset_use_my"
# Optional entity whose state is a datetime; replaces astral-computed sunset/sunrise.
CONF_SUNSET_TIME_ENTITY = "sunset_time_entity"
CONF_SUNRISE_TIME_ENTITY = "sunrise_time_entity"
# Optional "daytime gate" (issue #632): a binary-sensor list and/or a Jinja
# condition template that answers "is it daytime — should ACP sun-track now?".
# When configured it OWNS the day/night boundary (replacing the astral sunset/
# sunrise decision); when unconfigured ACP falls back to the astronomical calc
# (zero regression). Gate on/active = daytime/track; off = dark → apply sunset
# position. Clones the motion gate (§19): same evaluation helpers, no new code.
# The combine mode reuses TemplateCombineMode / DEFAULT_TEMPLATE_COMBINE_MODE.
CONF_DAYTIME_GATE_SENSORS = "daytime_gate_sensors"  # on/active = daytime/track
CONF_DAYTIME_GATE_TEMPLATE = "daytime_gate_template"  # truthy = daytime/track
CONF_DAYTIME_GATE_TEMPLATE_MODE = "daytime_gate_template_mode"  # TemplateCombineMode
# Grace window (seconds) for which the gate holds its last-known daytime/dark
# verdict when every gate source goes indeterminate (sensors unavailable/unknown/
# missing, template unrenderable) — issue #742. After this elapses with no usable
# source the gate falls back to the astronomical sunset/sunrise window (resolves to
# ``daytime_gate=None``). Fixed, not user-configurable.
DEFAULT_DAYTIME_GATE_GRACE_SECONDS = 120.0
# Explicit tilt for venetian covers (0-100). None = use solar-computed tilt.
CONF_DEFAULT_TILT = "default_tilt"  # tilt when no handler fires
CONF_SUNSET_TILT = (
    "sunset_tilt"  # tilt during sunset window (falls back to default_tilt)
)


# =============================================================================
# 8a. Forecast Timeline
# =============================================================================
# Sampling cadence and boundary-event vocabulary for the dashboard forecast
# strip built by ``forecast.build_forecast``. 10-minute steps over the full
# local calendar day (00:00 → 24:00) are dense enough to read smoothly and
# cheap enough to compute in well under a second on a Pi 4.

FORECAST_STEP_MINUTES = 10  # cadence between forecast samples, minutes

EVENT_SUNRISE = "sunrise"  # boundary event: sun rises above horizon
EVENT_SUNSET = "sunset"  # boundary event: sun sets below horizon
EVENT_FOV_ENTER = "fov_enter"  # boundary event: sun enters window FOV
EVENT_FOV_EXIT = "fov_exit"  # boundary event: sun leaves window FOV


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

# --- Multiple blind-spot slots (issue #701) ---------------------------------
# The single wedge above generalizes to UP TO 3 independent slots. The sun is
# treated as blocked when it falls inside ANY active slot. A slot is *active*
# when its left AND right are both set; the master CONF_ENABLE_BLIND_SPOT still
# gates the whole feature. Slot 1 REUSES the legacy unsuffixed keys above so
# existing installs need no migration; slots 2/3 use ``_2``/``_3`` suffixes.

# Per-slot elevation modes (issue #702). "below" (default) keeps today's
# ``sol_elev <= elevation`` — an obstacle that blocks LOW sun (tree, overhang).
# "above" flips to ``sol_elev >= elevation`` — an overhead obstacle that blocks
# HIGH sun (balcony, deep recess). The vocabulary is slot-independent: it lives
# here ONCE and is never re-suffixed per slot.
BLIND_SPOT_ELEV_MODE_BELOW = "below"  # wedge applies when sun is at/below elev
BLIND_SPOT_ELEV_MODE_ABOVE = "above"  # wedge applies when sun is at/above elev
DEFAULT_BLIND_SPOT_ELEVATION_MODE = BLIND_SPOT_ELEV_MODE_BELOW
BLIND_SPOT_ELEVATION_MODES: tuple[str, ...] = (
    BLIND_SPOT_ELEV_MODE_BELOW,
    BLIND_SPOT_ELEV_MODE_ABOVE,
)
# Slot-1 flat wire key for the elevation mode (mirrors CONF_BLIND_SPOT_ELEVATION).
CONF_BLIND_SPOT_ELEVATION_MODE = "blind_spot_elevation_mode"

BLIND_SPOT_SLOT_NUMBERS: tuple[int, ...] = (1, 2, 3)  # slot 1 = legacy keys


@dataclass(frozen=True, slots=True)
class BlindSpot:
    """One blind-spot wedge.

    ``elevation`` (None = applies at all elevations). ``elevation_mode``
    (issue #702) selects which side of ``elevation`` the wedge applies to:
    ``BLIND_SPOT_ELEV_MODE_BELOW`` (default) blocks LOW sun
    (``sol_elev <= elevation``); ``BLIND_SPOT_ELEV_MODE_ABOVE`` blocks HIGH sun
    (``sol_elev >= elevation``). The single comparison lives in
    ``SunGeometry._sun_in_blind_spot``.
    """

    left: int
    right: int
    elevation: int | None = None
    elevation_mode: str = BLIND_SPOT_ELEV_MODE_BELOW


def _blind_spot_slot_keys(n: int) -> dict[str, str]:
    """Return the wire-format option keys for blind-spot slot *n*.

    Slot 1 keeps the legacy unsuffixed keys (no data migration); slots 2+ are
    suffixed ``_<n>``.
    """
    s = "" if n == 1 else f"_{n}"
    return {
        "left": f"blind_spot_left{s}",
        "right": f"blind_spot_right{s}",
        "elevation": f"blind_spot_elevation{s}",
        # Per-slot below/above elevation selector (issue #702).
        "elevation_mode": f"blind_spot_elevation_mode{s}",
    }


# {slot_number: {sub_key: wire_key}}
BLIND_SPOT_SLOTS: dict[int, dict[str, str]] = {
    n: _blind_spot_slot_keys(n) for n in BLIND_SPOT_SLOT_NUMBERS
}


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
# Which outdoor-temperature reading climate mode uses (issue #547): one of
# OutsideTempSource — live (default), forecast_max, or max_of_live_and_forecast.
# Reuses the configured CONF_WEATHER_ENTITY as the forecast source.
CONF_OUTSIDE_TEMP_SOURCE = "outside_temp_source"
# Outdoor temp threshold for summer/winter mode switch (range 0-100).
CONF_OUTSIDE_THRESHOLD = "outside_threshold"
CONF_PRESENCE_ENTITY = "presence_entity"  # presence/occupancy sensor entity_id
# Optional Jinja condition template + combine mode for presence (issue #639):
# truthy = occupied. Folds with the presence_entity via TemplateCombineMode
# (OR default). Render failure / empty → no opinion → existing entity logic.
CONF_PRESENCE_TEMPLATE = "presence_template"  # truthy = occupied
CONF_PRESENCE_TEMPLATE_MODE = "presence_template_mode"  # TemplateCombineMode
CONF_WEATHER_ENTITY = "weather_entity"  # weather. integration entity_id
CONF_WEATHER_STATE = "weather_state"  # states that trigger climate handler
# True to close covers at night in winter for added insulation.
CONF_WINTER_CLOSE_INSULATION = "winter_close_insulation"
# True to let summer climate-close ignore the sun-in-FOV min floor
# (min_position_sun_tracking) and reach the global min_position instead.
CONF_SUMMER_CLOSE_BYPASS_SUN_FLOOR = "summer_close_bypass_sun_floor"
# When no explicit CONF_TEMP_ENTITY is set, fall back to the cover's HA area
# configured temperature entity (issue #786). Defaults True (opt-out).
CONF_AUTO_RESOLVE_TEMP_FROM_AREA = "auto_resolve_temp_from_area"
DEFAULT_AUTO_RESOLVE_TEMP_FROM_AREA = True

# Repair issue id (issue_registry) raised when the effective indoor temperature
# sensor stays unavailable/missing beyond the debounce window (issue #786).
ISSUE_TEMP_SENSOR_UNAVAILABLE = "temp_sensor_unavailable"
# Generous debounce so integration restarts / device re-adds don't nag before
# a genuinely dead sensor is flagged.
DEFAULT_SENSOR_HEALTH_DEBOUNCE_SECONDS = 900.0

# Extreme-heat mode (issue #766): an all-day, sun-independent force-hold that
# pre-empts every other climate strategy (including winter heating) whenever the
# OUTSIDE temperature exceeds this threshold. None/unset = feature off.
CONF_TEMP_EXTREME_HEAT = "temp_extreme_heat"  # outside-temp threshold, sensor unit
# Position the cover holds during extreme heat. None/unset defaults to
# DEFAULT_EXTREME_HEAT_POSITION (fully closed); an explicit 0 is honored.
CONF_EXTREME_HEAT_POSITION = "extreme_heat_position"
# Fully closed by default — the whole point of extreme-heat mode is to block
# heat. A literal default is a constant declaration, per coding guidelines.
DEFAULT_EXTREME_HEAT_POSITION = POSITION_CLOSED

# Season-scope control for glare tracking. Glare tracking (Priority 4) is only
# reachable when the current season is one of the selected seasons; in any
# deselected season the cover returns to its default position instead of
# tracking the sun. See the ``TrackingSeason`` enum and DEFAULT_TRACKING_SEASONS
# (defined with the other enums below) for the season values and the
# all-seasons default that preserves existing behaviour.
CONF_TRACKING_SEASONS = "tracking_seasons"

# Climate-mode temperature smoothing (issue #917) — the temperature analogue of
# the cloud-suppression smoothing (issue #864). All default to today's
# instantaneous, single-crossing behaviour so an absent key changes nothing on
# upgrade/rollback (additive-only, no migration).
# Aggregate hold-time (seconds): a changed season-flag must persist this long
# before it takes effect. 0 = flip immediately (no debounce).
CONF_CLIMATE_TEMP_HOLD_TIME = "climate_temp_hold_time"
# Per-crossing hysteresis release edges (number-or-template, blank = off). When
# set they widen each temperature crossing into a Schmitt band: the season flag
# engages at the existing threshold and only clears once the value passes the
# release edge. temp_low/temp_high release edges compare the current temperature;
# the outside/extreme-heat edges compare the outdoor temperature.
CONF_TEMP_LOW_RELEASE_THRESHOLD = "temp_low_release_threshold"
CONF_TEMP_HIGH_RELEASE_THRESHOLD = "temp_high_release_threshold"
CONF_OUTSIDE_THRESHOLD_RELEASE = "outside_threshold_release"
CONF_TEMP_EXTREME_HEAT_RELEASE_THRESHOLD = "temp_extreme_heat_release_threshold"

DEFAULT_CLIMATE_TEMP_HOLD_TIME = 0  # seconds; 0 = instantaneous (back-compat)

STRATEGY_MODE_BASIC = "basic"  # geometry only, no climate inputs
STRATEGY_MODE_CLIMATE = "climate"  # climate-aware (temps/presence/weather)
STRATEGY_MODES = [
    STRATEGY_MODE_BASIC,
    STRATEGY_MODE_CLIMATE,
]  # ordered list used by config_flow selectors

CLIMATE_SUMMER_TILT_ANGLE = 45  # degrees — slat tilt under summer cooling
CLIMATE_DEFAULT_TILT_ANGLE = 80  # degrees — tilt when no climate signal

# Tilt MODE2 (0–180° range) uses the same percentage scale for both
# closed-one-way (0%) and closed-other-way (100%); the open horizontal
# slat angle (90°) maps to 50%. The negative-gamma branch flips the angle
# by subtracting an offset of 90° before scaling so that the result lands
# in the OTHER closed hemisphere. See engine/covers/tilt.py:120-121 for
# the geometry-side scale derivation.
MODE2_OPEN_HORIZONTAL_PERCENT = 50  # MODE2: 50% == horizontal/open slat
CLIMATE_TILT_PCT_NEGATIVE_HEMISPHERE_OFFSET = 90  # MODE2 hemisphere-flip offset


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
# Optional Jinja condition template + combine mode for is_sunny (issue #639):
# truthy = sunny. Folds with the is_sunny_sensor via TemplateCombineMode (OR
# default). Render failure / empty → no opinion → existing weather fallback.
CONF_IS_SUNNY_TEMPLATE = "is_sunny_template"  # truthy = sunny
CONF_IS_SUNNY_TEMPLATE_MODE = "is_sunny_template_mode"  # TemplateCombineMode
CONF_CLOUD_COVERAGE_ENTITY = "cloud_coverage_entity"  # cloud-cover % sensor
# % cloud cover above which the suppression handler activates.
CONF_CLOUD_COVERAGE_THRESHOLD = "cloud_coverage_threshold"
CONF_CLOUD_SUPPRESSION = "cloud_suppression"  # master enable
CONF_CLOUDY_POSITION = "cloudy_position"  # position while suppressed (0-100)

# Smoothing controls (issue #864). All default to today's instantaneous,
# single-crossing behaviour so an absent key changes nothing on upgrade/rollback.
# Aggregate hold-time (seconds): a changed suppression state must persist this
# long before it takes effect. 0 = flip immediately (no debounce).
CONF_CLOUD_SUPPRESSION_HOLD_TIME = "cloud_suppression_hold_time"
# Per-trigger hysteresis release edges (number-or-template, blank = off). When
# set they widen each numeric trigger into a Schmitt band: suppression activates
# at the existing threshold and only clears once the value passes the release
# edge (lux/irradiance must rise above it; cloud coverage must fall below it).
CONF_LUX_RELEASE_THRESHOLD = "lux_release_threshold"
CONF_IRRADIANCE_RELEASE_THRESHOLD = "irradiance_release_threshold"
CONF_CLOUD_COVERAGE_RELEASE_THRESHOLD = "cloud_coverage_release_threshold"

DEFAULT_CLOUD_COVERAGE_THRESHOLD = 75  # default: 75% cover = overcast
DEFAULT_CLOUD_SUPPRESSION_HOLD_TIME = 0  # seconds; 0 = instantaneous (back-compat)


# =============================================================================
# 13. Force Override (legacy — merged into Custom Position slots, issue #563)
# =============================================================================
# The standalone force-override feature is gone: its config migrates into
# custom-position slot 5 at priority 100 (v3.2 migration). These keys are kept
# ONLY so the migration can read them and so a rollback to the previous
# integration version still finds its config intact — never write new values.

CONF_FORCE_OVERRIDE_SENSORS = "force_override_sensors"  # binary_sensor list
CONF_FORCE_OVERRIDE_POSITION = "force_override_position"  # position 0-100
# If True, force-override is only enforced as a min position (won't close more).
CONF_FORCE_OVERRIDE_MIN_MODE = "force_override_min_mode"


# =============================================================================
# 14. Custom Position Slots
# =============================================================================
# Up to ten independently-configurable position slots, each with its own
# trigger sensors (OR logic), optional condition template, position, priority
# (1-100), min-mode flag, and "use my position" flag. Each slot's wire-format
# keys are generated below to keep them DRY. The numbered per-slot CONF_*
# aliases are retained for callers that prefer named constants over dict
# lookup.

CUSTOM_POSITION_SLOT_NUMBERS: tuple[int, ...] = tuple(
    range(1, 11)
)  # slots 1–10 (issue #703)

# Slots at (or above) this priority inherit the old force-override safety
# semantics: they command the cover outside the start/end time window and
# bypass the delta-position/delta-time send gates (issue #563).
CUSTOM_POSITION_SAFETY_PRIORITY = 100

# Cover-group scene intents claim the pipeline at this priority (issue #790,
# Phase 2): above manual_override (80) so "put the room in Privacy" wins over
# a stale per-cover manual state, below weather (90) so wind/rain safety on an
# outdoor member still overrides a group scene. Declared on GroupSceneHandler;
# the const exists so the config-flow chain summary and tests import it. Not
# user-overridable (group-injected handler, not one of HANDLER_PRIORITY_CONF).
# The group lock reuses CUSTOM_POSITION_SAFETY_PRIORITY (100); a member's own
# safety slot wins ties via handler build order.
GROUP_SCENE_PRIORITY = 85


def _custom_position_slot_keys(n: int) -> dict[str, str]:
    """Return the wire-format option keys for slot *n*."""
    return {
        # Optional user-configured label (issue #867). When set, overrides the
        # slot's label everywhere it's surfaced (reason string, decision_trace
        # attribute, floor/tilt-axis traces, card snapshot). Absent = no name
        # (fully backward-compatible; the sensor's friendly_name is used as
        # before).
        "name": f"custom_position_name_{n}",
        # Legacy single-sensor key. Still read as a fallback when the `sensors`
        # list key is absent, and mirrored (first list element) on every save
        # so a rollback to the previous integration version keeps working.
        "sensor": f"custom_position_sensor_{n}",
        # Trigger sensors, OR logic across the list (issue #563). Wins over
        # the legacy `sensor` key whenever present.
        "sensors": f"custom_position_sensors_{n}",
        # Optional Jinja2 condition template; folded with the sensors via
        # `template_mode` (TemplateCombineMode, default OR).
        "template": f"custom_position_template_{n}",
        "template_mode": f"custom_position_template_mode_{n}",
        "position": f"custom_position_{n}",
        "priority": f"custom_position_priority_{n}",
        "min_mode": f"custom_position_min_mode_{n}",
        "use_my": f"custom_position_use_my_{n}",
        "tilt": f"custom_position_tilt_{n}",
        # When True, the slot fixes only the slat angle (tilt) — solar drives
        # position. Reuses the slot's existing `tilt` value as the slat angle
        # (issue #514). Venetian-only; gated on custom_position_includes_tilt.
        "tilt_only": f"custom_position_tilt_only_{n}",
        # `enabled` is opt-out: existing entries lack the key and behave as
        # enabled. Set to False to silence a slot without clearing its
        # configuration — used by the companion card's slot toggle UI.
        "enabled": f"custom_position_enabled_{n}",
    }


# Default for an absent custom_position_enabled_<N> option — backwards-compatible
# with entries configured before the enabled key existed.
DEFAULT_CUSTOM_POSITION_ENABLED = True


# {slot_number: {sub_key: wire_key}}
CUSTOM_POSITION_SLOTS: dict[int, dict[str, str]] = {
    n: _custom_position_slot_keys(n) for n in CUSTOM_POSITION_SLOT_NUMBERS
}


def custom_position_handler_name(slot: int) -> str:
    """Return the canonical decision-trace handler name for a custom slot.

    Single source of truth for the ``custom_position_N`` name (issue #496).
    Both ``CustomPositionHandler.name`` and the floor-composition trace source
    delegate here so the two can never drift to different numbering schemes.
    """
    return f"custom_position_{slot}"


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

# Slot 5 (issue #563 — the migration target for legacy force-override config).
CONF_CUSTOM_POSITION_SENSOR_5 = CUSTOM_POSITION_SLOTS[5]["sensor"]
CONF_CUSTOM_POSITION_5 = CUSTOM_POSITION_SLOTS[5]["position"]
CONF_CUSTOM_POSITION_PRIORITY_5 = CUSTOM_POSITION_SLOTS[5]["priority"]
CONF_CUSTOM_POSITION_MIN_MODE_5 = CUSTOM_POSITION_SLOTS[5]["min_mode"]
CONF_CUSTOM_POSITION_USE_MY_5 = CUSTOM_POSITION_SLOTS[5]["use_my"]

CONF_MY_POSITION_VALUE = "my_position_value"  # user's "my" position, 1-99
# Opt-in toggle: when False, the "Managed My Position" button and
# "Managed My Position Value" number entity are NOT created. Off by default
# for new installs; the v2 → v3 migration sets it to True for every
# pre-existing entry to preserve current behavior.
CONF_ENABLE_MY_POSITION_ENTITIES = "enable_my_position_entities"
DEFAULT_ENABLE_MY_POSITION_ENTITIES = False
DEFAULT_CUSTOM_POSITION_PRIORITY = 77  # default priority for a new slot
# Default for an absent custom_position_tilt_only_<N> option (issue #514).
DEFAULT_CUSTOM_POSITION_TILT_ONLY = False

# Configurable priorities for the built-in pipeline handlers. Each key holds an
# integer (1-99) that overrides the handler's class-default priority, letting the
# user re-order the decision chain. An absent/cleared key falls back to the class
# default (read in pipeline.handlers via HANDLER_PRIORITY_DEFAULTS). The `default`
# handler (priority 0) is deliberately not configurable — it is the chain floor.
# 100 stays reserved for the custom-slot safety semantic, so built-ins cap at 99.
CONF_WEATHER_PRIORITY = "weather_priority"
CONF_MANUAL_OVERRIDE_PRIORITY = "manual_override_priority"
CONF_MOTION_TIMEOUT_PRIORITY = "motion_timeout_priority"
CONF_CLOUD_SUPPRESSION_PRIORITY = "cloud_suppression_priority"
CONF_CLIMATE_PRIORITY = "climate_priority"
CONF_GLARE_ZONE_PRIORITY = "glare_zone_priority"
CONF_SOLAR_PRIORITY = "solar_priority"


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
# Optional Jinja condition templates + combine modes for the is-raining / is-windy
# weather overrides (issue #639): truthy = raining / windy. Each folds with its
# companion binary sensor via TemplateCombineMode (OR default). A template-only
# override (no companion sensor) engages and reacts the instant the template
# flips, tracked via async_track_template_result.
CONF_WEATHER_IS_RAINING_TEMPLATE = "weather_is_raining_template"  # truthy = raining
CONF_WEATHER_IS_RAINING_TEMPLATE_MODE = "weather_is_raining_template_mode"
CONF_WEATHER_IS_WINDY_TEMPLATE = "weather_is_windy_template"  # truthy = windy
CONF_WEATHER_IS_WINDY_TEMPLATE_MODE = "weather_is_windy_template_mode"
CONF_WEATHER_SEVERE_SENSORS = "weather_severe_sensors"  # severe-weather list
# Position commanded during weather override (range 0-100).
CONF_WEATHER_OVERRIDE_POSITION = "weather_override_position"
# If True, weather override is only enforced as a min position.
CONF_WEATHER_OVERRIDE_MIN_MODE = "weather_override_min_mode"
CONF_WEATHER_TIMEOUT = "weather_timeout"  # resume delay after clear, s (0-3600)
# If True, weather override fires even when auto control is off.
CONF_WEATHER_BYPASS_AUTO_CONTROL = "weather_bypass_auto_control"
# Master on/off toggle for the whole weather-override feature (issue #719). When
# False, every configured weather sensor/template is ignored — both the
# priority-90 override handler and the min-mode floor are disabled at the single
# WeatherManager.is_feature_configured chokepoint. New covers default OFF via the
# config-flow schema; pre-existing covers are migrated to ON (v3.5 → v3.6).
CONF_WEATHER_ENABLED = "weather_enabled"

# Threshold unit must match the sensor (no conversion applied).
DEFAULT_WEATHER_WIND_SPEED_THRESHOLD = 50.0
# Degrees each side of window azimuth that counts as on-axis wind.
DEFAULT_WEATHER_WIND_DIRECTION_TOLERANCE = 45
# Threshold unit must match the sensor (no conversion applied).
DEFAULT_WEATHER_RAIN_THRESHOLD = 1.0
DEFAULT_WEATHER_TIMEOUT = 300  # seconds before resuming after clear
# New covers start with the weather override OFF (issue #719). Pre-existing
# covers are migrated to True so upgrades keep firing weather safety overrides.
DEFAULT_WEATHER_ENABLED = False


# =============================================================================
# 16. Automation Timing & Gating
# =============================================================================
# Delta thresholds (position / time) that gate command emission, plus the
# active time-of-day window.

CONF_DELTA_POSITION = "delta_position"  # min % change to emit, range 1-90
CONF_DELTA_TIME = "delta_time"  # min minutes between commands, range 2-60
DEFAULT_DELTA_POSITION = 2  # minimum percentage change threshold
DEFAULT_DELTA_TIME = 2  # minimum minutes between commands

# Anticipatory solar positioning (issue #616): when CONF_DELTA_TIME (the
# minimum interval between position changes, in minutes) is the look-ahead
# horizon, the solar handler samples this many future sun positions across
# (now, now + delta_time] and commands the most-protective one so coverage is
# guaranteed until the next allowed move. Bounded and small because the sun
# moves slowly and covers step coarsely; the SunData table is only 5-min
# resolution, so finer sampling buys nothing.
SOLAR_ANTICIPATION_SAMPLES = 4
# Allowed gap between commanded and reported position before the periodic
# reconciliation pass treats the cover as "not arrived" and resends the
# command. Distinct from CONF_DELTA_POSITION (movement hysteresis). Default
# is POSITION_TOLERANCE_PERCENT (see section 20). Range 0-20. Issue #507.
# NOTE: the command-emission same-position gate uses this tolerance ONLY
# for the hard endpoints (target 0 or 100) where the delta gate is bypassed
# (issue #507/#629); for all other targets it keys off exact equality so
# mid-range tracking moves are unaffected (issue #567).  Movement hysteresis
# for non-endpoint targets is owned by CONF_DELTA_POSITION.
CONF_POSITION_TOLERANCE = "position_tolerance"
# When True, the periodic reconciliation pass actively resends a command on a
# position mismatch until the cover reaches the target. When False (the
# default), the cover is commanded once and left where it lands; a settled
# landing-delta then surfaces as a manual override instead of a retry
# (issue #591). Default is DEFAULT_ENABLE_POSITION_MATCHING (section 20).
CONF_ENABLE_POSITION_MATCHING = "enable_position_matching"
CONF_START_TIME = "start_time"  # active-window start "HH:MM:SS"
CONF_START_ENTITY = "start_entity"  # input_datetime overriding start_time
CONF_END_TIME = "end_time"  # active-window end "HH:MM:SS"
CONF_END_ENTITY = "end_entity"  # input_datetime overriding end_time
# Blank/unset sentinel for start/end times: HA's TimeSelector cannot emit a
# true None, so a cleared field coerces to midnight. Treated as "no time set"
# everywhere (see issue #492).
BLANK_TIME = "00:00:00"


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
DEFAULT_MANUAL_OVERRIDE_DURATION: dict = {"hours": 2}  # default hold duration
# If True, the manual override is reset when end_time is reached.
CONF_MANUAL_OVERRIDE_RESET = "manual_override_reset"
CONF_MANUAL_THRESHOLD = "manual_threshold"  # % delta = manual touch, 0-99
# If True, intermediate positions don't count as manual touches.
CONF_MANUAL_IGNORE_INTERMEDIATE = "manual_ignore_intermediate"
# If True, only commands routed through ACP (proxy entity or set_position
# service) engage manual override; all other position changes are ignored.
CONF_MANUAL_IGNORE_EXTERNAL = "manual_ignore_external"
# Binary-sensor-like entities whose off→on edge engages manual override on every
# cover in the instance (issue #688). Lets a physical wall switch wired to an
# input (e.g. Shelly binary_sensor.*_cover_input_0) act as the manual-override
# trigger instead of inferring intent from cover state/position changes.
CONF_MANUAL_OVERRIDE_INPUT_ENTITIES = "manual_override_input_entities"
# Which manual-override detection strategy to use. Maps to a registered
# OverrideDetector via managers.manual_override.get_detector. Changing this
# selects a different detection pattern; takes effect on config-entry reload.
CONF_MANUAL_OVERRIDE_STRATEGY = "manual_override_strategy"
DEFAULT_MANUAL_OVERRIDE_STRATEGY = "position_delta"
# Position threshold separating "open" vs "closed" classification, % (1-99).
CONF_OPEN_CLOSE_THRESHOLD = "open_close_threshold"

# Manual override detection grace periods (fixed values, not configurable).
COMMAND_GRACE_PERIOD_SECONDS = 5.0  # ignore position changes after a command
STARTUP_GRACE_PERIOD_SECONDS = 30.0  # disable manual-override after HA startup

# Position-forecast recompute cadence (issue #437). The forecast is a 12-hour
# outlook at 15-minute granularity; recomputing more often than every few
# minutes adds no information and pointlessly burns CPU. 5 minutes is the
# sweet spot — fresh enough that the dashboard reflects sunrise/sunset
# transitions promptly, cheap enough that even on a Pi 4 the executor job
# completes in well under a second.
FORECAST_RECOMPUTE_INTERVAL_MIN = 5
# Physical step between consecutive SunData timeline entries (seconds).
# Matches the "5min" freq passed to pd.date_range in sun.py.
SUN_DATA_STEP_SECONDS: int = 300

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
CONF_MOTION_MEDIA_PLAYERS = (
    "motion_media_players"  # media_player list; non-off=occupied
)
CONF_MOTION_TEMPLATE = "motion_template"  # optional Jinja2 condition; truthy=occupied
CONF_MOTION_TEMPLATE_MODE = (
    "motion_template_mode"  # how the template combines; one of TemplateCombineMode
)
CONF_MOTION_TIMEOUT = "motion_timeout"  # no-motion window, s (30-3600)
CONF_MOTION_TIMEOUT_MODE = "motion_timeout_mode"  # one of MOTION_TIMEOUT_MODE_*

MOTION_TIMEOUT_MODE_RETURN = "return_to_default"  # return to default height
MOTION_TIMEOUT_MODE_HOLD = "hold_position"  # hold current position

DEFAULT_MOTION_TIMEOUT = 300  # 5 minutes — default no-motion window
DEFAULT_MOTION_TIMEOUT_MODE = MOTION_TIMEOUT_MODE_RETURN  # default mode
# DEFAULT_MOTION_TEMPLATE_MODE lives with the TemplateCombineMode enum (defined
# above the config_fields import, since config_fields reads it at import time).


# =============================================================================
# 20. Position Verification
# =============================================================================
# Fixed (non-configurable) constants that drive the periodic check ensuring
# the cover actually reached the commanded position.

POSITION_CHECK_INTERVAL_MINUTES = 1  # minutes — recheck cadence
# Default for the now-configurable CONF_POSITION_TOLERANCE (issue #507). Still
# the fixed floor for the manual-override threshold (effective_manual_threshold
# in managers/manual_override.py reads this constant directly, NOT the option).
POSITION_TOLERANCE_PERCENT = 3  # % — "position matches" tolerance (default)
MAX_POSITION_RETRIES = 3  # maximum re-send attempts before giving up
# Default for CONF_ENABLE_POSITION_MATCHING (issue #591). False = matching off:
# command once, no resend; a settle past tolerance becomes a manual override.
DEFAULT_ENABLE_POSITION_MATCHING = False


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

# Grace tail after stamp_position_command: even when cover.state has already
# settled to "open"/"closed", bypass the backrotate cap for this many seconds.
# Real actuators publish their tilt-walk burst AFTER the carriage reports open,
# so the cap must stay suspended until the HA state machine has fully drained.
VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS = 5.0

# Bypass the back-rotate cap for this many seconds after the sequencer observes
# the cover transition out of the moving state (i.e. anchored to the actual
# moving→settled boundary inside ``_wait_for_position_settle``, NOT to
# ``stamp_position_command``). Somfy IO actuators in issue #33 publish their
# back-rotate tilt burst ~27 s after settle; 45 s leaves ~18 s headroom for
# slower bus republish on KNX/Z2M while staying well under
# VENETIAN_TILT_SUPPRESSION_SECONDS=90.0. Anchored to the real settle
# transition (not stamp_position_command), so a premature-stall on a slow-start
# actuator cannot start this clock early.
#
# Behavioural-impact note: fast actuators (KNX sub-second publish, Shelly 2PM)
# settle and publish back-rotate inside VENETIAN_POST_SETTLE_CAP_GRACE_SECONDS=5.0
# today, so extending publish-lag to 45 s does change behaviour: a user
# twisting slats by hand at +10 s post-settle would, under the new code, be
# classed as motor back-drive and not trip override — if their delta exceeds
# 30 (the cap). Risk bounded by physics: real motor back-drive is
# single-digit-percent (VENETIAN_REBASE_MAX_DRIFT_PERCENT=15), so a delta of
# 30-95% during the 5-45 s window is implausible on any actuator. A user
# twisting slats during the window almost always lands within the existing cap
# (delta ≤ 30) which has always suppressed regardless of actuator speed.
#
# Configurable per-instance (issue #33 Phase 5 cross-axis). The module-level
# default below is consumed when no instance config is available (unit tests,
# legacy callers); production reads ``CONF_VENETIAN_BACKROTATE_PUBLISH_LAG``
# from options and threads it through ``RuntimeConfig.venetian`` →
# ``VenetianPolicy.attach()`` → ``DualAxisSequencer.__init__``. The legacy
# name ``VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS`` is retained as an alias of
# the default so older tests / re-exports keep working without churn.
CONF_VENETIAN_BACKROTATE_PUBLISH_LAG = (
    "venetian_backrotate_publish_lag"  # s, 15.0-180.0
)
DEFAULT_VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS = 45.0  # default publish-lag
MIN_VENETIAN_BACKROTATE_PUBLISH_LAG = 15.0  # UI lower bound, seconds
MAX_VENETIAN_BACKROTATE_PUBLISH_LAG = 180.0  # UI upper bound, seconds
# Backward-compat alias — the old module-level constant the sequencer used to
# read directly. Tests and any other downstream caller that imports the legacy
# name continue to see the same float value; new code should pull the value
# from ``DualAxisSequencer._backrotate_publish_lag_seconds`` so a per-instance
# override actually flows through.
VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS = (
    DEFAULT_VENETIAN_BACKROTATE_PUBLISH_LAG_SECONDS
)

# Refuse to count unchanged-position samples as a "stall" during the first N
# seconds of _wait_for_position_settle UNLESS the cover has been observed in
# opening/closing at least once. Justification: Somfy IO motors take 3-5 s to
# begin physical travel after the service call; 6 s covers observed delay with
# 1 s headroom. VENETIAN_POSITION_SETTLE_TIMEOUT_SECONDS=60.0 still hard-caps
# so a dead motor doesn't block the rest of the update cycle.
VENETIAN_POSITION_SETTLE_STARTUP_GRACE_SECONDS = 6.0

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

# After _verify_and_record_tilt records a drift, sleep this many seconds before
# the single bounded retry through _send_tilt_command. Short enough that the
# user does not see the wrong tilt for long; long enough that the actuator's
# carriage-move back-rotate has fully published before the retry reads back.
# Issue #500.
VENETIAN_DRIFT_RETRY_DELAY_SECONDS = 2.0

# Hold delay between position settle and the tilt command. Some actuators
# perform a firmware tilt-reassert after the carriage reports closed/open
# (e.g. FGR223): firing the tilt command immediately races that reassert.
# The value is configurable per-instance; the module-level default is consumed
# only when no instance config is available (e.g. unit tests).
CONF_VENETIAN_POST_SETTLE_HOLD = "venetian_post_settle_hold"  # s, 0.0-10.0
DEFAULT_VENETIAN_POST_SETTLE_HOLD_SECONDS = 3.0  # default post-settle hold

# How the post-settle wait is performed (issue #801). ``fixed_delay`` (default,
# back-compat) always sleeps ``post_settle_hold_seconds`` before the tilt
# command. ``entity_state`` instead polls the cover entity's ``cover.state``
# and proceeds the moment it is no longer opening/closing — better for
# actuators (e.g. Shelly 2PM) with reliable motion states. Falls back to the
# fixed-delay sleep when the entity state is unavailable, or when the
# ``post_settle_hold_seconds`` budget elapses without the carriage going
# stationary. Venetian-only enum.
CONF_VENETIAN_POST_SETTLE_MODE = "venetian_post_settle_mode"  # one of below
VENETIAN_POST_SETTLE_MODE_FIXED = "fixed_delay"  # always sleep the fixed hold (default)
VENETIAN_POST_SETTLE_MODE_ENTITY_STATE = "entity_state"  # poll cover.state instead
DEFAULT_VENETIAN_POST_SETTLE_MODE = VENETIAN_POST_SETTLE_MODE_FIXED  # back-compat
VENETIAN_POST_SETTLE_MODES = (
    VENETIAN_POST_SETTLE_MODE_FIXED,
    VENETIAN_POST_SETTLE_MODE_ENTITY_STATE,
)

# Skip the tilt command when the commanded position exceeds this threshold —
# at high positions the slats are retracted into the housing and tilting is
# physically meaningless. The value is configurable per-instance.
CONF_VENETIAN_TILT_SKIP_ABOVE = "venetian_tilt_skip_above"  # %, 50-100
DEFAULT_VENETIAN_TILT_SKIP_ABOVE = 95  # percent — default skip-tilt threshold
MIN_VENETIAN_TILT_SKIP_ABOVE = 50  # UI lower bound
MAX_VENETIAN_TILT_SKIP_ABOVE = 100  # UI upper bound

# Accumulated commanded tilt-% change that triggers a mechanical drift reset
# (issue #663). Each real (non-deduped, non-dry-run, non-gated) tilt send adds
# ``abs(new_target - prior_anchor)`` to a per-entity accumulator; when it
# crosses this threshold the sequencer drives the slats fully open and back to
# the target to flush accumulated actuator drift. 0 disables the feature. The
# value is configurable per-instance; venetian-only.
CONF_VENETIAN_TILT_RESET_THRESHOLD = "venetian_tilt_reset_threshold"  # % accum
DEFAULT_VENETIAN_TILT_RESET_THRESHOLD = 0  # 0 = disabled (no reset)
MIN_VENETIAN_TILT_RESET_THRESHOLD = 0  # UI lower bound (0 disables)
MAX_VENETIAN_TILT_RESET_THRESHOLD = 5000  # UI upper bound (accumulated %)

# Direction the drift-reset drives the slats before re-sending the target
# (issue #686). ``open`` keeps the original behaviour (drive to the fully-open
# mechanical endpoint); ``close`` drives to the fully-closed endpoint instead —
# useful on covers that sit near-closed during tracking (faster, quieter reset)
# or whose actuator re-zeroes the slats on a close command. Venetian-only.
CONF_VENETIAN_TILT_RESET_DIRECTION = "venetian_tilt_reset_direction"  # one of below
VENETIAN_TILT_RESET_OPEN = "open"  # drive to POSITION_OPEN then back (default)
VENETIAN_TILT_RESET_CLOSE = "close"  # drive to POSITION_CLOSED then back
DEFAULT_VENETIAN_TILT_RESET_DIRECTION = VENETIAN_TILT_RESET_OPEN  # back-compat
VENETIAN_TILT_RESET_DIRECTIONS = (
    VENETIAN_TILT_RESET_OPEN,
    VENETIAN_TILT_RESET_CLOSE,
)

# Scope that decides which tilt commands are eligible to accumulate drift and
# trigger a reset (issue #808). ``all_tilt_commands`` keeps the original
# behaviour (every real tilt send counts). ``sun_tracking_only`` restricts the
# accumulator to solar-tracking commands (winning ControlMethod == SOLAR), so
# custom-position / manual / climate-discrete tilts no longer trigger the
# full-open-then-return reset cycle. Venetian-only enum.
CONF_VENETIAN_TILT_RESET_SCOPE = "venetian_tilt_reset_scope"  # one of below
VENETIAN_TILT_RESET_SCOPE_ALL = "all_tilt_commands"  # every tilt send (default)
VENETIAN_TILT_RESET_SCOPE_SOLAR = "sun_tracking_only"  # solar tracking only
DEFAULT_VENETIAN_TILT_RESET_SCOPE = VENETIAN_TILT_RESET_SCOPE_ALL  # back-compat
VENETIAN_TILT_RESET_SCOPES = (
    VENETIAN_TILT_RESET_SCOPE_ALL,
    VENETIAN_TILT_RESET_SCOPE_SOLAR,
)

# How the tilt-skip-above guard behaves once the carriage is commanded above
# ``venetian_tilt_skip_above`` (issue #748). ``neutral`` (default, back-compat)
# sends a benign POSITION_OPEN tilt to overwrite the actuator's cache (the #33
# behaviour KNX/Shelly need); ``suppress`` emits NO tilt command at all so
# mechanically-coupled exterior venetians are not dragged off the open endpoint.
# Venetian-only enum.
CONF_VENETIAN_TILT_SKIP_MODE = "venetian_tilt_skip_mode"  # one of below
VENETIAN_TILT_SKIP_NEUTRAL = "neutral"  # send neutral POSITION_OPEN tilt (default)
VENETIAN_TILT_SKIP_SUPPRESS = "suppress"  # send no tilt command at all
DEFAULT_VENETIAN_TILT_SKIP_MODE = VENETIAN_TILT_SKIP_NEUTRAL  # back-compat
VENETIAN_TILT_SKIP_MODES = (
    VENETIAN_TILT_SKIP_NEUTRAL,
    VENETIAN_TILT_SKIP_SUPPRESS,
)

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
    # Deprecated (issue #563): no longer produced — force override merged into
    # custom-position slot 5. Kept for card/diagnostics value-set stability.
    FORCE_OVERRIDE_ACTIVE = "force_override_active"
    WEATHER_OVERRIDE_ACTIVE = "weather_override_active"  # priority-90 handler
    MOTION_TIMEOUT = "motion_timeout"  # priority-75 handler fired


class ClimateInactiveReason:
    """Machine-readable slugs for why the climate handler is not driving.

    Exposed as the ``inactive_reason`` attribute on the ``sensor.climate_status``
    entity so downstream consumers (Lovelace card, automations) can branch on
    structured values rather than parsing human-readable prose.

    The card localises the slugs; do not rename without updating downstream
    consumers.
    """

    ACTIVE = "active"  # climate handler is the winning pipeline handler
    MODE_OFF = "mode_off"  # climate mode switch is disabled
    OUTSIDE_TIME_WINDOW = (
        "outside_time_window"  # reuses ControlStatus value — same concept
    )
    THRESHOLDS_NOT_MET = (
        "thresholds_not_met"  # climate active, no season threshold hit (deferred)
    )
    OTHER_MODE_ACTIVE = "other_mode_active"  # outprioritized by a higher handler
    READINGS_UNAVAILABLE = "readings_unavailable"  # sensors misconfigured/unavailable


class ReasonCode(StrEnum):
    """Stable identifiers for every human-readable reason/explanation string.

    Each value is the join key into the ``reason_i18n/`` template bundle
    (mirrors the ``summary_i18n/`` mechanism): the integration renders the
    English template on the ``sensor.*`` reason attributes and the Lovelace
    card localizes the same codes+params into the user's language.

    These are FROZEN identifiers exposed in diagnostics and consumed by the
    card; do not rename without updating both ``reason_i18n/*.json`` and the
    downstream card templates. Dotted values group by emitter
    (``solar.*``, ``manual.*``, ``registry.*``, ``builder.*``, ``engine.*``,
    ``skip.*``, ``fragment.*``).
    """

    # -- fragments: composed sub-phrases rendered inline into a parent template
    FRAGMENT_SUNSET_POSITION = "fragment.sunset_position"
    FRAGMENT_DEFAULT_POSITION = "fragment.default_position"
    FRAGMENT_CLOUDY_POSITION = "fragment.cloudy_position"
    FRAGMENT_COVERAGE_STEP = "fragment.coverage_step"
    FRAGMENT_Z_ADJUSTED = "fragment.z_adjusted"
    FRAGMENT_BYPASS_NOTE = "fragment.bypass_note"
    FRAGMENT_SEASON_EXTREME_HEAT = "fragment.season_extreme_heat"
    FRAGMENT_SEASON_TRACKING_OFF = "fragment.season_tracking_off"
    FRAGMENT_SEASON_SUMMER = "fragment.season_summer"
    FRAGMENT_SEASON_WINTER = "fragment.season_winter"
    FRAGMENT_SEASON_GLARE_LOW_LIGHT = "fragment.season_glare_low_light"
    FRAGMENT_SEASON_GLARE = "fragment.season_glare"
    FRAGMENT_TRIGGER_NOT_SUNNY = "fragment.trigger_not_sunny"
    FRAGMENT_TRIGGER_LUX_BELOW = "fragment.trigger_lux_below"
    FRAGMENT_TRIGGER_IRRADIANCE_BELOW = "fragment.trigger_irradiance_below"
    FRAGMENT_TRIGGER_CLOUD_ABOVE = "fragment.trigger_cloud_above"
    FRAGMENT_TRIGGER_SMOOTHING_HOLD = "fragment.trigger_smoothing_hold"
    FRAGMENT_TRIGGER_TEMPLATE = "fragment.trigger_template"
    FRAGMENT_TRIGGER_FALLBACK = "fragment.trigger_fallback"

    # -- solar handler
    SOLAR_TRACKING = "solar.tracking"

    # -- manual override handler
    MANUAL_HOLDING_SOLAR = "manual.holding_solar"
    MANUAL_SOLAR_ONLY = "manual.solar_only"
    MANUAL_HOLDING_LABEL = "manual.holding_label"
    MANUAL_LABEL_ONLY = "manual.label_only"

    # -- motion/occupancy timeout handler (#881 wording)
    OCCUPANCY_HOLDING = "occupancy.holding"
    OCCUPANCY_LABEL = "occupancy.label"

    # -- climate handler
    CLIMATE_ACTIVE = "climate.active"

    # -- glare zone handler
    GLARE_PROTECTION = "glare.protection"

    # -- cloud suppression handler
    CLOUD_SUPPRESSION = "cloud.suppression"

    # -- weather override handler
    WEATHER_ACTIVE = "weather.active"

    # -- custom position handler
    CUSTOM_HEAD_NAMED = "custom.head_named"
    CUSTOM_HEAD_SLOT = "custom.head_slot"
    CUSTOM_USE_MY = "custom.use_my"
    CUSTOM_POSITION = "custom.position"

    # -- default handler
    DEFAULT_SUNSET_USE_MY = "default.sunset_use_my"
    DEFAULT_NO_CONDITION = "default.no_condition"

    # -- group handlers
    GROUP_LOCK = "group.lock"
    GROUP_SCENE = "group.scene"

    # -- registry composition (outprioritized / floor / tilt-axis passes)
    REGISTRY_OUTPRIORITIZED = "registry.outprioritized"
    REGISTRY_FLOOR_RAISED = "registry.floor_raised"
    REGISTRY_FLOOR_INACTIVE = "registry.floor_inactive"
    REGISTRY_TILT_APPLIED = "registry.tilt_applied"
    REGISTRY_TILT_DEFERRED = "registry.tilt_deferred"

    # -- diagnostics builder (control-state reason + position explanation)
    BUILDER_UNKNOWN = "builder.unknown"
    BUILDER_CONTROL_OCCUPANCY_TIMEOUT = "builder.control_occupancy_timeout"
    BUILDER_CONTROL_MANUAL_OVERRIDE = "builder.control_manual_override"
    BUILDER_CONTROL_TRACKING_OFF_SEASON = "builder.control_tracking_off_season"
    BUILDER_CONTROL_TILT_FIXED = "builder.control_tilt_fixed"
    BUILDER_OUTSIDE_WINDOW = "builder.outside_window"
    BUILDER_MANUAL_DIVERGENCE = "builder.manual_divergence"
    BUILDER_TILT_FIXED = "builder.tilt_fixed"
    BUILDER_INTERPOLATED = "builder.interpolated"
    BUILDER_INVERSED = "builder.inversed"

    # -- engine control_state_reason (pure calc layer — codes resolved at boundary)
    ENGINE_DIRECT_SUN = "engine.direct_sun"
    ENGINE_DEFAULT_SUNSET_OFFSET = "engine.default_sunset_offset"
    ENGINE_DEFAULT_ELEVATION_LIMIT = "engine.default_elevation_limit"
    ENGINE_DEFAULT_ACCEPTANCE_ANGLE_EXIT = "engine.default_acceptance_angle_exit"
    ENGINE_DEFAULT_BLIND_SPOT = "engine.default_blind_spot"
    ENGINE_DEFAULT = "engine.default"

    # -- describe_skip / inactive-reason prose
    SKIP_OUTSIDE_WINDOW = "skip.outside_window"
    SKIP_SUN_OUTSIDE = "skip.sun_outside"
    SKIP_MANUAL_NOT_ACTIVE = "skip.manual_not_active"
    SKIP_OCCUPANCY_DISABLED = "skip.occupancy_disabled"
    SKIP_OCCUPANCY_NOT_ACTIVE = "skip.occupancy_not_active"
    SKIP_CLIMATE_MODE_OFF = "skip.climate_mode_off"
    SKIP_CLIMATE_READINGS_UNAVAILABLE = "skip.climate_readings_unavailable"
    SKIP_CLIMATE_DEFERRED = "skip.climate_deferred"
    SKIP_NO_GLARE_ZONES = "skip.no_glare_zones"
    SKIP_CLOUD_SKIPPED = "skip.cloud_skipped"
    SKIP_CLOUD_INACTIVE = "skip.cloud_inactive"
    SKIP_WEATHER_NOT_ACTIVE = "skip.weather_not_active"
    SKIP_CUSTOM_NOT_ACTIVE = "skip.custom_not_active"
    SKIP_ALWAYS_MATCHES = "skip.always_matches"
    SKIP_GROUP_SCENE_NOT_LOCK = "skip.group_scene_not_lock"
    SKIP_NO_GROUP_LOCK = "skip.no_group_lock"
    SKIP_GROUP_LOCK_NOT_SCENE = "skip.group_lock_not_scene"
    SKIP_NO_GROUP_SCENE = "skip.no_group_scene"
    SKIP_NOT_ACTIVE = "skip.not_active"


# =============================================================================
# 24. Geometric Accuracy (calc engine)
# =============================================================================
# Edge-case thresholds and safety-margin multipliers used in calculation.py
# and engine/sun_geometry.py. Tuning these affects how aggressively the
# integration retreats from extreme sun geometries.

# Low-sun edge-case threshold. The former extreme-gamma (85°) and very-high-
# elevation (88°) thresholds were removed in issue #600 once the projection
# formula gained its own numerical guards; only the horizon-sun floor remains.
EDGE_CASE_LOW_ELEVATION = 2.0  # deg — below this, force full coverage (closed)

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
# Solar-calculation trace keys (issue #682)
# =============================================================================
# Stable key names for the per-cycle raw geometric solar-position trace that
# each calc engine records in ``_last_calc_details``. The new
# ``solar_calculation`` diagnostic sensor and the diagnostics download both read
# this single dict. Suffix convention: ``_deg`` = degrees, ``_m`` = metres,
# ``_pct`` = percent (0-100), raw ratios carry no suffix.
#
# Common to every cover type (stamped by the engine; ``cover_type`` stamped by
# the builder from config — engines never branch on their own type string).
TRACE_KEY_COVER_TYPE = "cover_type"
TRACE_KEY_SOL_ELEV_DEG = "sol_elev_deg"
TRACE_KEY_GAMMA_DEG = "gamma_deg"
TRACE_KEY_POSITION_PCT = "position_pct"
# Venetian dual-axis: the tilt sub-trace nests under this key.
TRACE_KEY_TILT = "tilt"


# =============================================================================
# 25. UI Defaults & Validation Caps
# =============================================================================
# Default values shown in the config-flow UI and hard caps not derived from
# OPTION_RANGES (used for legacy schema validation).

DEFAULT_WINDOW_HEIGHT = 2.1  # metres — config-flow default
DEFAULT_DISTANCE = 1.0  # metres — shaded distance default for vertical blinds
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

# Glare zones — per-zone X/Y/Radius/Z bounds. Mirror the selector ranges in
# config_flow._build_glare_zones_schema so changes stay in sync.
_RANGE_GLARE_ZONE_X = (-5.0, 5.0)  # along the wall, metres
_RANGE_GLARE_ZONE_Y = (0.0, 10.0)  # into the room, metres
_RANGE_GLARE_ZONE_RADIUS = (0.1, 2.0)  # zone radius, metres
_RANGE_GLARE_ZONE_Z = (0.0, 3.0)  # target height above floor, metres
DEFAULT_GLARE_ZONE_Z = 0.0  # default — protects a floor disk (current behaviour)

# Geometry — awning.
_RANGE_LENGTH_AWNING = (0.3, 6.0)  # CONF_LENGTH_AWNING, metres
_RANGE_AWNING_ANGLE = (0, 45)  # CONF_AWNING_ANGLE, degrees
_RANGE_TILT_ANGLE_0 = (-180, 180)  # CONF_TILT_ANGLE_0, degrees
_RANGE_TILT_ANGLE_100 = (0, 360)  # CONF_TILT_ANGLE_100, degrees

# Geometry — oscillating (drop-arm) awning.
_RANGE_ARM_LENGTH = (0.1, 6.0)  # CONF_ARM_LENGTH, metres
_RANGE_AWNING_SWEEP_ANGLE = (0, 180)  # CONF_AWNING_MIN/MAX_ANGLE, degrees
_RANGE_AWNING_HOUSING_OFFSET = (0.0, 1.0)  # CONF_AWNING_HOUSING_OFFSET, metres
_RANGE_AWNING_PIVOT_OFFSET = (0.0, 2.0)  # CONF_AWNING_PIVOT_OFFSET, metres

# Geometry — roof / skylight window (#212).
_RANGE_ROOF_PITCH = (0, 90)  # CONF_ROOF_PITCH, degrees (0=flat, 90=vertical)
_RANGE_ROOF_HEIGHT_ABOVE = (0.0, 10.0)  # CONF_ROOF_HEIGHT_ABOVE, metres
# Geometry — louvered roof (#830 follow-up).
_RANGE_MAX_SLAT_ANGLE = (0, 180)  # CONF_MAX_SLAT_ANGLE, degrees (0 = use tilt mode)

# Geometry — tilt / venetian slats.
_RANGE_TILT_DEPTH = (0.1, 30.0)  # CONF_TILT_DEPTH, cm (raised to 300 mm for #830)
_RANGE_TILT_DISTANCE = (0.1, 30.0)  # CONF_TILT_DISTANCE, cm (raised to 300 mm, #830)
_RANGE_MAX_TILT = (0, 100)  # CONF_MAX_TILT, percent
_RANGE_MIN_TILT = (0, 100)  # CONF_MIN_TILT, percent
_RANGE_VENETIAN_TILT_SAFETY_MARGIN = (
    MIN_VENETIAN_TILT_SAFETY_MARGIN,
    MAX_VENETIAN_TILT_SAFETY_MARGIN,
)  # CONF_VENETIAN_TILT_SAFETY_MARGIN, 0.0-1.0 scale factor

# Sun tracking.
_RANGE_AZIMUTH = (0, 359)  # CONF_AZIMUTH, degrees
_RANGE_FOV = (0, 180)  # CONF_FOV_LEFT / CONF_FOV_RIGHT, degrees
_RANGE_ELEVATION = (0, 90)  # min/max elevation, degrees
_RANGE_DISTANCE = (0.0, 50.0)  # CONF_DISTANCE, metres

# Geometry — sliding-curtain shade area (#829, Part 2). y (depth into the room)
# reuses the 0–50 m distance span; x (along the wall) is signed — no existing
# signed range spans a wide window's half-width (glare-zone x is only ±5 m), so a
# dedicated ±25 m bound (half the 50 m max window width) is added here.
_RANGE_SLIDING_POINT_X = (-25.0, 25.0)  # CONF_SLIDING_POINT{1,2}_X, metres (signed)
_RANGE_SLIDING_POINT_Y = _RANGE_DISTANCE  # CONF_SLIDING_POINT{1,2}_Y, metres (0–50)

# Blind spot.
# Asymmetric LEFT vs RIGHT bounds are a historical quirk; preserved for compat.
_RANGE_BLIND_SPOT_LEFT = (0, 359)  # CONF_BLIND_SPOT_LEFT, degrees
_RANGE_BLIND_SPOT_RIGHT = (0, 360)  # CONF_BLIND_SPOT_RIGHT, degrees
_RANGE_BLIND_SPOT_ELEVATION = (0, 90)  # CONF_BLIND_SPOT_ELEVATION, degrees

# Position limits & sunset.
_RANGE_DEFAULT_HEIGHT = (0, 100)  # CONF_DEFAULT_HEIGHT, percent
_RANGE_MAX_POSITION = (0, 100)  # CONF_MAX_POSITION, percent (0 = always closed, #806)
_RANGE_MIN_POSITION = (0, 99)  # CONF_MIN_POSITION, percent
_RANGE_SUNSET_POS = (0, 100)  # CONF_SUNSET_POS, percent
_RANGE_END_OF_WINDOW_POS = (0, 100)  # CONF_END_OF_WINDOW_POS, percent
_RANGE_MY_POSITION = (1, 99)  # CONF_MY_POSITION_VALUE, percent
_RANGE_OFFSET_MINUTES = (-120, 120)  # sunset/sunrise offsets, minutes
_RANGE_OPEN_CLOSE_THRESHOLD = (1, 99)  # CONF_OPEN_CLOSE_THRESHOLD, percent

# Interpolation.
_RANGE_INTERP_VALUE = (0, 100)  # interp start/end, percent

# Sun-tracking movement minimization.
_RANGE_MAX_COVERAGE_STEPS = (1, 10)  # CONF_MAX_COVERAGE_STEPS, discrete levels

# Automation timing.
_RANGE_DELTA_POSITION = (1, 90)  # CONF_DELTA_POSITION, percent
_RANGE_DELTA_TIME = (2, 60)  # CONF_DELTA_TIME, minutes
_RANGE_POSITION_TOLERANCE = (0, 20)  # CONF_POSITION_TOLERANCE, percent

# Manual override.
_RANGE_MANUAL_THRESHOLD = (0, 99)  # CONF_MANUAL_THRESHOLD, percent

# Force override / custom positions.
_RANGE_FORCE_POSITION = (0, 100)  # CONF_FORCE_OVERRIDE_POSITION, percent
_RANGE_CUSTOM_POSITION = (0, 100)  # per-slot custom position, percent
_RANGE_CUSTOM_PRIORITY = (1, 100)  # per-slot custom priority (100 = safety)
_RANGE_HANDLER_PRIORITY = (1, 99)  # built-in handler priority (100 reserved=safety)
_RANGE_TILT = (0, 100)  # per-slot/default/sunset tilt, percent

# Motion.
_RANGE_MOTION_TIMEOUT = (30, 3600)  # CONF_MOTION_TIMEOUT, seconds

# Climate. Range is interpreted in the **sensor's** unit, not HA's locale —
# wide enough to span Celsius and Fahrenheit comfort thresholds (e.g.
# 78°F warm threshold sits in this range).
_RANGE_TEMPERATURE = (0, 150)  # temp_low / temp_high (sensor unit)
_RANGE_OUTSIDE_THRESHOLD = (0, 150)  # CONF_OUTSIDE_THRESHOLD (sensor unit)
_RANGE_EXTREME_HEAT_POSITION = (0, 100)  # CONF_EXTREME_HEAT_POSITION, percent

# Weather safety.
_RANGE_WEATHER_WIND_SPEED = (0, 200)  # wind-speed threshold (sensor unit)
_RANGE_WEATHER_WIND_DIRECTION_TOLERANCE = (5, 180)  # wind-direction tol, deg
_RANGE_WEATHER_RAIN = (0, 100)  # rain threshold (sensor unit)
_RANGE_WEATHER_OVERRIDE_POSITION = (0, 100)  # weather-override pos, percent
_RANGE_WEATHER_TIMEOUT = (0, 3600)  # weather-resume timeout, seconds

# Cloud-suppression smoothing (issue #864).
_RANGE_CLOUD_SUPPRESSION_HOLD_TIME = (0, 3600)  # hold-time, seconds

# Climate-mode temperature smoothing (issue #917).
_RANGE_CLIMATE_TEMP_HOLD_TIME = (0, 3600)  # hold-time, seconds

# Venetian sequencing.
_RANGE_VENETIAN_POST_SETTLE_HOLD = (0.0, 10.0)  # post-settle hold, seconds
_RANGE_VENETIAN_TILT_SKIP_ABOVE = (
    MIN_VENETIAN_TILT_SKIP_ABOVE,
    MAX_VENETIAN_TILT_SKIP_ABOVE,
)  # CONF_VENETIAN_TILT_SKIP_ABOVE, percent
_RANGE_VENETIAN_TILT_RESET_THRESHOLD = (
    MIN_VENETIAN_TILT_RESET_THRESHOLD,
    MAX_VENETIAN_TILT_RESET_THRESHOLD,
)  # CONF_VENETIAN_TILT_RESET_THRESHOLD, accumulated percent
_RANGE_VENETIAN_BACKROTATE_PUBLISH_LAG = (
    MIN_VENETIAN_BACKROTATE_PUBLISH_LAG,
    MAX_VENETIAN_BACKROTATE_PUBLISH_LAG,
)  # CONF_VENETIAN_BACKROTATE_PUBLISH_LAG, seconds


# Defined here (above the ``config_fields`` import) so it exists before the
# ``from .config_fields import OPTION_RANGES`` line. Generic on purpose — any
# template-based condition
# field can reuse this enum (and the shared ``template_combine_mode`` selector
# translation key) to offer the same OR/AND choice; the occupancy template
# (#577 follow-up) is the first consumer.
class TemplateCombineMode(StrEnum):
    """How a condition template combines with the screen's other conditions.

    ``OR`` (default) is additive — the source is occupied/active when the
    template is truthy **or** any other condition is met. ``AND`` makes the
    template a gate — it must be truthy **and** at least one other condition
    must be met. When only one source exists (template-only or others-only),
    ``AND`` degenerates to that single source so a lone template is never
    permanently false. Absent from a config entry → treated as ``OR``.
    """

    OR = "or"
    AND = "and"


# Shared default for every condition-template combine-mode option (motion,
# custom-position slots, future consumers): additive OR (back-compat).
DEFAULT_TEMPLATE_COMBINE_MODE = TemplateCombineMode.OR.value
DEFAULT_MOTION_TEMPLATE_MODE = DEFAULT_TEMPLATE_COMBINE_MODE


class OutsideTempSource(StrEnum):
    """Which outdoor-temperature reading feeds climate mode (issue #547).

    ``LIVE`` (default) preserves today's behaviour — the outdoor sensor state,
    or the configured weather entity's ``temperature`` attribute (the *current*
    temp). ``FORECAST_MAX`` uses today's forecast daily high fetched from the
    configured weather entity via ``weather.get_forecasts`` (falling back to the
    live read when no forecast is available). ``MAX_OF_LIVE_AND_FORECAST`` takes
    the larger of the two so a cool morning that will heat up still trips summer
    close. Wire-stable identifiers stored in the cover's options.
    """

    LIVE = "live"
    FORECAST_MAX = "forecast_max"
    MAX_OF_LIVE_AND_FORECAST = "max_of_live_and_forecast"


# Default outdoor-temp source: LIVE, so upgrades are inert (issue #547).
DEFAULT_OUTSIDE_TEMP_SOURCE = OutsideTempSource.LIVE.value


# Defined here (above the ``config_fields`` import at the bottom of this module)
# because ``config_fields`` reads ``SLIDING_SLIDE_DIRECTIONS`` at its own import
# time for the slide-direction ``FieldSpec`` select options.
class SlideDirection(StrEnum):
    """Which way a horizontal sliding curtain draws its fabric (#829, Part 2).

    Wire-stable identifiers stored in the cover's options. ``LEFT`` / ``RIGHT``
    are single-slide leaves anchored to one window edge; ``BI_PART`` parts from
    the centre (both edges covered, a central gap opens). Governs how a covered
    along-wall interval maps to an open percentage.
    """

    LEFT = "left"
    RIGHT = "right"
    BI_PART = "bi_part"


# Default slide direction: centre-parting, the most common curtain layout.
DEFAULT_SLIDING_SLIDE_DIRECTION = SlideDirection.BI_PART.value
SLIDING_SLIDE_DIRECTIONS = tuple(d.value for d in SlideDirection)


# ``OPTION_RANGES`` is now assembled from the single field registry in
# ``config_fields`` (each ``FieldSpec`` carries its own ``rng``). It is
# re-exported here so the many ``from .const import OPTION_RANGES`` call sites
# stay unchanged. The ``_RANGE_*`` tuples above remain the home of the bounds
# values — ``config_fields`` references them by name.
#
# Import-ordering note: this runs at the bottom of ``const`` import, by which
# point every ``_RANGE_*``/``CONF_*``/``CUSTOM_POSITION_SLOTS``/``VENETIAN_MODES``
# name ``config_fields`` needs is already defined above. ``config_fields`` does
# ``from . import const`` (the partially-initialised module is fine — it only
# reads names defined before this line).
from .config_fields import OPTION_RANGES  # noqa: E402, F401

# =============================================================================
# 27. Enumerations (semantic identifiers)
# =============================================================================
# StrEnum / Enum types used across the package. Kept here so that every named
# identifier — config-wire strings, mode names, diagnostic categories, control
# methods — lives in one file. Values are stored in HA's config entries and the
# decision trace; **must stay byte-stable** across releases for the same reason
# as CONF_* keys.


class CoverType(StrEnum):
    """Cover type identifier stored in ``config_entry.data[CONF_SENSOR_TYPE]``.

    Drives which ``CoverTypePolicy`` (under ``cover_types/``) is instantiated,
    which config-flow geometry step is shown, and which calc-engine cover
    module under ``engine/covers/`` is used.
    """

    BLIND = "cover_blind"
    AWNING = "cover_awning"
    TILT = "cover_tilt"
    VENETIAN = "cover_venetian"
    OSCILLATING_AWNING = "cover_oscillating_awning"
    ROOF_WINDOW = "cover_roof_window"
    SLIDING_CURTAIN = "cover_sliding_curtain"
    LOUVERED_ROOF = "cover_louvered_roof"
    # Virtual entry type — not a physical cover. Holds shared building-level
    # sensor entity IDs that linked covers copy into their own options. Its
    # policy registers no platforms (``controls_cover = False``).
    BUILDING_PROFILE = "cover_building_profile"
    # Virtual entry type — orchestrates a roster of member covers (ACP
    # entries + generic ``cover.*`` entities). Controls covers
    # (``controls_cover = True``) but is not geometry-driven: setup branches
    # on ``is_orchestrator`` to a ``GroupCoordinator`` (issue #790).
    GROUP = "cover_group"

    @property
    def display_name(self) -> str:
        """Return human-readable display name (no "Cover" suffix).

        Callers that want "<type> Cover" should append it explicitly. Returning
        the bare adjective here lets `entity_base.device_info` produce
        "Adaptive Vertical Cover" without doubling the word.
        """
        return {
            self.BLIND: "Vertical",
            self.AWNING: "Horizontal",
            self.TILT: "Tilt",
            self.VENETIAN: "Venetian",
            self.OSCILLATING_AWNING: "Oscillating Awning",
            self.ROOF_WINDOW: "Roof Window",
            self.SLIDING_CURTAIN: "Sliding Curtain",
            self.LOUVERED_ROOF: "Louvered Roof",
            self.BUILDING_PROFILE: "Building Profile",
            self.GROUP: "Cover Group",
        }[self]


class GroupScene(StrEnum):
    """Built-in cover-group scenes (issue #790, Phase 1).

    Wire-stable identifiers: stored as the group's active-scene state and the
    scene ``select``/``button`` option values. Each scene is a semantic intent
    resolved per member cover type via ``CoverTypePolicy.position_for_scene``
    — never an absolute position shared across a mixed group.
    """

    ALL_OPEN = "all_open"
    ALL_CLOSED = "all_closed"
    PRIVACY = "privacy"


class GroupIntentKind(StrEnum):
    """What a cover-group intent asks of a member's pipeline (issue #790).

    ``SCENE`` — claim the position axis at ``GROUP_SCENE_PRIORITY`` with the
    member policy's resolution of the intent's scene. ``LOCK`` — freeze the
    member at ``CUSTOM_POSITION_SAFETY_PRIORITY``: the handler wins the
    pipeline but sends no command, so the cover holds its position.
    """

    SCENE = "scene"
    LOCK = "lock"


class GroupState(StrEnum):
    """Aggregate open/closed classification of a cover group's members.

    Wire-stable: the group state sensor reports these values. ``MIXED`` covers
    every non-uniform roster (members disagree, or sit between endpoints);
    ``UNKNOWN`` means no member position was readable.
    """

    OPEN = "open"
    CLOSED = "closed"
    MIXED = "mixed"
    UNKNOWN = "unknown"


# =============================================================================
# 28. Building-profile sensor key sets
# =============================================================================
# Canonical frozensets of the sensor-picker option keys. ``config_flow``'s
# ``SYNC_CATEGORIES`` references these (it used to inline the same membership),
# so they live here — ``const`` cannot import from ``config_flow`` (circular).
# ``BUILDING_PROFILE_SENSOR_KEYS`` is the set of option keys a Building Profile
# owns and copies into each linked cover. Threshold/reaction keys, presence,
# and the sunrise/sunset OFFSETS are deliberately excluded — they stay per-cover.
# The four ``*_template_mode`` keys are profile-owned (moved from per-cover in
# issue #720): they render in the profile screen, are copied to linked covers,
# and are hidden on the per-cover weather/light/behavior forms.

LIGHT_CLOUD_SENSOR_KEYS = frozenset(
    {
        CONF_WEATHER_ENTITY,
        CONF_LUX_ENTITY,
        CONF_IRRADIANCE_ENTITY,
        CONF_CLOUD_COVERAGE_ENTITY,
        CONF_IS_SUNNY_SENSOR,
        CONF_IS_SUNNY_TEMPLATE,
        CONF_IS_SUNNY_TEMPLATE_MODE,
    }
)

WEATHER_OVERRIDE_SENSOR_KEYS = frozenset(
    {
        CONF_WEATHER_WIND_SPEED_SENSOR,
        CONF_WEATHER_WIND_DIRECTION_SENSOR,
        CONF_WEATHER_RAIN_SENSOR,
        CONF_WEATHER_IS_RAINING_SENSOR,
        CONF_WEATHER_IS_RAINING_TEMPLATE,
        CONF_WEATHER_IS_RAINING_TEMPLATE_MODE,
        CONF_WEATHER_IS_WINDY_SENSOR,
        CONF_WEATHER_IS_WINDY_TEMPLATE,
        CONF_WEATHER_IS_WINDY_TEMPLATE_MODE,
        CONF_WEATHER_SEVERE_SENSORS,
    }
)

BUILDING_PROFILE_SENSOR_KEYS = (
    LIGHT_CLOUD_SENSOR_KEYS
    | WEATHER_OVERRIDE_SENSOR_KEYS
    | frozenset(
        {
            CONF_OUTSIDETEMP_ENTITY,
            CONF_DAYTIME_GATE_SENSORS,
            CONF_DAYTIME_GATE_TEMPLATE,
            CONF_DAYTIME_GATE_TEMPLATE_MODE,
            CONF_SUNSET_TIME_ENTITY,
            CONF_SUNRISE_TIME_ENTITY,
        }
    )
)


class TiltMode(StrEnum):
    """Tilt mode for venetian blinds (slat travel range)."""

    MODE1 = "mode1"  # Single direction (0-90°)
    MODE2 = "mode2"  # Bi-directional (0-180°)
    SPECIFY_ANGLES = "specify_angles"  # Explicit physical endpoint angles

    @property
    def max_degrees(self) -> int:
        """Return maximum degrees for this mode."""
        return 90 if self == self.MODE1 else 180


class TemperatureSource(Enum):
    """Temperature source for climate mode."""

    INSIDE = "inside"
    OUTSIDE = "outside"


class PresenceDomain(StrEnum):
    """Supported presence entity domains."""

    DEVICE_TRACKER = "device_tracker"
    ZONE = "zone"
    BINARY_SENSOR = "binary_sensor"
    INPUT_BOOLEAN = "input_boolean"


class TrackingSeason(StrEnum):
    """Seasons in which glare tracking may run (season-scope control).

    Derived from the climate temperatures: ``SUMMER`` when above the high
    threshold (and outside high), ``WINTER`` when below the low threshold,
    ``INTERMEDIATE`` otherwise. Stored as a list of these string values under
    ``CONF_TRACKING_SEASONS``; the all-seasons default preserves the original
    always-track behaviour.
    """

    WINTER = "winter"
    INTERMEDIATE = "intermediate"
    SUMMER = "summer"


# Ordered all-seasons default (member order). Selecting every season is the
# backward-compatible "track in every season" behaviour.
DEFAULT_TRACKING_SEASONS: list[str] = [s.value for s in TrackingSeason]


class ClimateStrategy(Enum):
    """Climate control strategies (winter/summer/glare/low-light branches)."""

    EXTREME_HEAT = "extreme_heat"  # All-day force-hold above outside-temp threshold
    WINTER_HEATING = "winter_heating"  # Open for solar heating
    WINTER_INSULATION = "winter_insulation"  # Close for heat retention
    SUMMER_COOLING = "summer_cooling"  # Close for heat blocking
    LOW_LIGHT = "low_light"  # Use default position
    GLARE_CONTROL = "glare_control"  # Use calculated position
    TRACKING_SEASON_GATE = (
        "tracking_season_gate"  # season-scope gate suppressed tracking
    )


class ControlMethod(StrEnum):
    """What is currently driving the cover position.

    Priority order (highest to lowest):
    FORCE > WEATHER > MANUAL > CUSTOM_POSITION > MOTION > CLOUD > SUMMER/WINTER > SOLAR > DEFAULT
    """

    SOLAR = "solar"
    """Sun is within the FOV; cover follows the calculated sun-position."""

    SUMMER = "summer"
    """Climate mode: temperature above max threshold; cover closes to block heat."""

    WINTER = "winter"
    """Climate mode: temperature below min threshold; cover opens for solar heat gain."""

    EXTREME_HEAT = "extreme_heat"
    """Climate mode: outside temperature exceeds the extreme-heat threshold; cover
    force-holds a fixed position all day, pre-empting every other season strategy."""

    DEFAULT = "default"
    """Sun is outside FOV, elevation limits, blind spot, or sunset offset window."""

    MANUAL = "manual_override"
    """User manually moved the cover; automatic control is paused."""

    CUSTOM_POSITION = "custom_position"
    """A custom position binary sensor is active; cover moves to the configured position."""

    MOTION = "motion_timeout"
    """No occupancy detected after timeout; cover returns to default position."""

    FORCE = "force_override"
    """Deprecated (issue #563): no longer produced — force override merged into
    custom-position slot 5 (CUSTOM_POSITION at safety priority). Kept for
    card/diagnostics value-set stability."""

    WEATHER = "weather_override"
    """Weather conditions (wind/rain/storm) exceed thresholds; covers retract for safety."""

    CLOUD = "cloud_suppression"
    """Cloud coverage suppresses solar radiation; covers use default position."""

    GLARE_ZONE = "glare_zone"
    """Glare zone protection active; cover extends to shield a floor zone."""

    GROUP_SCENE = "group_scene"
    """A cover-group scene intent claims the position (issue #790, Phase 2)."""

    GROUP_LOCK = "group_lock"
    """A cover-group lock freezes the cover in place (issue #790, Phase 2)."""


class SunState(StrEnum):
    """Authoritative sun classification for the companion Lovelace card.

    Matches the card's three legend states (polar-chart dot colour).
    Values are part of the diagnostics wire format — must stay byte-stable.
    """

    OUTSIDE_FOV = "outside_fov"
    """Sun azimuth is outside the window's field of view."""

    IN_FOV_NOT_VALID = "in_fov_not_valid"
    """Sun azimuth is inside FOV but direct-sun is blocked (elevation, sunset offset, or blind spot)."""

    HITTING = "hitting"
    """Sun is directly hitting the window (direct_sun_valid is True)."""
