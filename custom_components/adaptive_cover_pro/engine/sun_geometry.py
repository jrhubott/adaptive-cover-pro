"""Pure sun position analysis — no Home Assistant dependency.

Extracted from AdaptiveGeneralCover to enable standalone testing and reuse.
"""

from datetime import UTC, datetime, timedelta
from math import atan, degrees

import pandas as pd

from ..config_types import CoverConfig
from ..const import CONF_FOV_LEFT, DEFAULT_FOV_LEFT, DEGREES_IN_CIRCLE, OPTION_RANGES
from ..sun import SunData


def fov_from_reveal(width_m: float, depth_m: float) -> int:
    """Symmetric FOV half-angle (degrees) from reveal width and depth.

    Models the oblique-sun cutoff geometry of a cover mounted inside a reveal:
    the recess depth ``depth_m`` in front of the cover blocks sun arriving at a
    grazing azimuth, and the opening ``width_m`` sets how wide that gap is. The
    half-angle is ``atan((width/2) / depth)`` — the angle at which the sun first
    clears the reveal edge.

    A flush reveal (``depth_m <= 0``) or a degenerate opening (``width_m <= 0``)
    blocks nothing, so the FOV is the full hemisphere (the default half-angle).
    The result is clamped to the configured FOV range and rounded to an integer
    because ``fov_left``/``fov_right`` are stored as integer degrees.
    """
    if depth_m <= 0 or width_m <= 0:
        return DEFAULT_FOV_LEFT
    deg = degrees(atan((width_m / 2) / depth_m))
    lo, hi = OPTION_RANGES[CONF_FOV_LEFT]
    return int(round(min(max(deg, lo), hi)))


def computed_fov_line(
    width_m: float | None,
    depth_m: float | None,
    labels: dict[str, str] | None = None,
) -> str:
    """Read-only "Computed FOV ≈ 50°/50° (…)" line for Measurements mode (#565).

    The single formatter shared by the sun-tracking page placeholder and the
    config-flow summary. Delegates the angle to :func:`fov_from_reveal` so the
    arctan lives in exactly one place.

    ``labels`` overlays a translated ``geometry.fov.computed`` template on the
    English base (``GEOMETRY_LABELS_EN``); ``None`` or a missing key keeps
    English. The engine has 0 HA imports — ``labels`` is a plain dict, so this
    stays decoupled from ``config_flow``/``hass``.
    """
    # Local import keeps the cover_types → engine dependency one-directional at
    # module load (engine is imported by cover_types, not the reverse).
    from ..cover_types._summary_labels import GEOMETRY_LABELS_EN

    w = float(width_m or 0.0)
    d = float(depth_m or 0.0)
    deg = fov_from_reveal(w, d)
    L = {**GEOMETRY_LABELS_EN, **(labels or {})}
    return L["geometry.fov.computed"].format(deg=deg, w=f"{w:g}", d=f"{d:g}")


class SunGeometry:
    """Analyse sun position relative to a window's field of view.

    All inputs are plain data (floats, dataclasses, SunData).
    No Home Assistant imports are needed.
    """

    def __init__(
        self,
        sol_azi: float,
        sol_elev: float,
        sun_data: SunData,
        config: CoverConfig,
        logger: object,
        eval_time: datetime | None = None,
    ) -> None:
        """Initialise with sun position, solar data, cover config, and logger.

        ``eval_time`` is the moment the time-dependent gates (sunset/sunrise
        offset) should be evaluated against. The live pipeline leaves it
        ``None`` so those gates use wall-clock now. The forecast passes the
        timestamp of the sample it is projecting so each point on the
        full-day strip is evaluated at *its own* time rather than the moment
        the forecast happens to be recomputed (issue #516).
        """
        self.sol_azi = sol_azi
        self.sol_elev = sol_elev
        self.sun_data = sun_data
        self.config = config
        self.logger = logger
        self.eval_time = eval_time

    # ------------------------------------------------------------------
    # Azimuth helpers
    # ------------------------------------------------------------------

    @property
    def azi_min_abs(self) -> int:
        """Calculate absolute minimum azimuth of window's field of view.

        Returns:
            Minimum azimuth angle in degrees (0-360).

        """
        return (
            self.config.win_azi - self.config.fov_left + DEGREES_IN_CIRCLE
        ) % DEGREES_IN_CIRCLE

    @property
    def azi_max_abs(self) -> int:
        """Calculate absolute maximum azimuth of window's field of view.

        Returns:
            Maximum azimuth angle in degrees (0-360).

        """
        return (
            self.config.win_azi + self.config.fov_right + DEGREES_IN_CIRCLE
        ) % DEGREES_IN_CIRCLE

    @property
    def gamma(self) -> float:
        """Calculate gamma (surface solar azimuth).

        Gamma is the horizontal angle between the window's perpendicular and the
        sun's position, normalized to -180 to +180 degrees. Positive values indicate
        sun to the left of window normal (looking outward), negative to the right.

        Returns:
            Gamma angle in degrees (-180 to +180).

        """
        return (self.config.win_azi - self.sol_azi + 180) % DEGREES_IN_CIRCLE - 180

    # ------------------------------------------------------------------
    # Validity checks
    # ------------------------------------------------------------------

    def _elevation_within_bounds(self, elev):
        """Return whether *elev* is within the configured min/max bounds.

        Works for scalar floats and pandas Series alike (uses bitwise ``&`` and
        comparison operators that both support).

        **Caller** must handle the "neither bound configured" case separately —
        the scalar property defaults to ``elev >= 0`` while the Series-based
        ``solar_times_with_position`` defaults to ``elev > 0``. That ½°
        difference at the horizon is intentional and predates this helper.
        """
        min_e, max_e = self.config.min_elevation, self.config.max_elevation
        if min_e is None:
            return elev <= max_e
        if max_e is None:
            return elev >= min_e
        return (elev >= min_e) & (elev <= max_e)

    @property
    def valid_elevation(self) -> bool:
        """Check if sun elevation is within configured limits.

        Returns:
            True if sun elevation within configured min/max range (or no limits set).
            False if sun below horizon or outside configured limits.

        """
        if self.config.min_elevation is None and self.config.max_elevation is None:
            return self.sol_elev >= 0
        within_range = bool(self._elevation_within_bounds(self.sol_elev))
        self.logger.debug("elevation within range? %s", within_range)
        return within_range

    @property
    def valid(self) -> bool:
        """Check if sun is in front of window within field of view.

        Returns:
            True if sun within configured azimuth field of view and valid elevation.
            False if sun behind window, outside FOV, or elevation invalid.

        """
        azi_min = self.config.fov_left
        azi_max = self.config.fov_right
        valid = bool(
            (self.gamma < azi_min) & (self.gamma > -azi_max) & (self.valid_elevation)
        )
        self.logger.debug("Sun in front of window (ignoring blindspot)? %s", valid)
        return valid

    @property
    def in_fov(self) -> bool:
        """Check if sun azimuth is within the window's field of view (elevation ignored).

        Returns True when the sun's azimuth projects into the FOV regardless of
        whether the elevation is valid. Used by the companion card to distinguish
        "outside FOV" from "in FOV but blocked by elevation/sunset/blind-spot".
        """
        azi_min = self.config.fov_left
        azi_max = self.config.fov_right
        return bool((self.gamma < azi_min) & (self.gamma > -azi_max))

    @property
    def sunset_valid(self) -> bool:
        """Check if current time is within sunset/sunrise offset period.

        Returns:
            True if current time is after (sunset + offset) or before (sunrise + offset).
            False during normal daytime operation.

        """
        sunset = self.sun_data.sunset().replace(tzinfo=None)
        sunrise = self.sun_data.sunrise().replace(tzinfo=None)
        ref = self.eval_time.astimezone(UTC) if self.eval_time else datetime.now(UTC)
        now_naive = ref.replace(tzinfo=None)
        after_sunset = now_naive > (sunset + timedelta(minutes=self.config.sunset_off))
        before_sunrise = now_naive < (
            sunrise + timedelta(minutes=self.config.sunrise_off)
        )
        self.logger.debug(
            "After sunset plus offset? %s", (after_sunset or before_sunrise)
        )
        return after_sunset or before_sunrise

    @property
    def is_sun_in_blind_spot(self) -> bool:
        """Check if sun is currently within configured blind spot area.

        Returns:
            True if sun is within blind spot area and blind spot enabled.
            False if blind spot not configured, disabled, or sun outside area.

        """
        if (
            self.config.blind_spot_left is not None
            and self.config.blind_spot_right is not None
            and self.config.blind_spot_on
        ):
            left_edge = self.config.fov_left - self.config.blind_spot_left
            right_edge = self.config.fov_left - self.config.blind_spot_right
            blindspot = (self.gamma <= left_edge) & (self.gamma >= right_edge)
            if self.config.blind_spot_elevation is not None:
                blindspot = blindspot & (
                    self.sol_elev <= self.config.blind_spot_elevation
                )
            self.logger.debug("Is sun in blind spot? %s", blindspot)
            return bool(blindspot)
        return False

    @property
    def direct_sun_valid(self) -> bool:
        """Check if sun is directly in front with no exclusions.

        Returns:
            True if sun in FOV, not in blind spot, and not in sunset/sunrise offset.
            False otherwise.

        """
        result = self.valid and not self.sunset_valid and not self.is_sun_in_blind_spot
        self.logger.debug(
            "direct_sun_valid=%s (valid=%s, sunset_valid=%s, in_blind_spot=%s)",
            result,
            self.valid,
            self.sunset_valid,
            self.is_sun_in_blind_spot,
        )
        return result

    @property
    def control_state_reason(self) -> str:
        """Determine why the cover is tracking the sun or using the default position.

        Returns:
            Reason string: "Direct Sun", "Default: FOV Exit", "Default: Elevation Limit",
            "Default: Sunset Offset", or "Default: Blind Spot".

        """
        if self.direct_sun_valid:
            return "Direct Sun"
        if self.sunset_valid:
            return "Default: Sunset Offset"
        if not self.valid:
            if not self.valid_elevation:
                return "Default: Elevation Limit"
            return "Default: FOV Exit"
        if self.is_sun_in_blind_spot:
            return "Default: Blind Spot"
        return "Default"

    # ------------------------------------------------------------------
    # FOV helpers
    # ------------------------------------------------------------------

    @property
    def _get_azimuth_edges(self) -> tuple[int, int]:
        """Get absolute azimuth boundaries of window's field of view.

        Returns:
            Tuple of (min_azimuth, max_azimuth) in degrees (0-360).

        """
        return (self.azi_min_abs, self.azi_max_abs)

    def fov(self) -> list[int]:
        """Get absolute azimuth boundaries of field of view.

        Returns:
            List of [min_azimuth, max_azimuth] in degrees (0-360).

        """
        return [self.azi_min_abs, self.azi_max_abs]

    # ------------------------------------------------------------------
    # Solar times
    # ------------------------------------------------------------------

    def solar_times(self) -> tuple[datetime | None, datetime | None]:
        """Calculate when sun enters and exits window's field of view today.

        Uses today's solar position data to determine the time window when the sun
        is within the configured azimuth field of view, elevation limits, and outside
        the sunset/sunrise offset periods.

        Returns:
            Tuple of (start_time, end_time) as datetime objects.
            Returns (None, None) if sun never enters the field of view today.

        """
        start, end = self.solar_times_with_position()
        if start is None or end is None:
            return None, None
        return start[0], end[0]

    def solar_times_with_position(
        self,
    ) -> tuple[
        tuple[datetime, float, float] | None,
        tuple[datetime, float, float] | None,
    ]:
        """Like solar_times() but also returns sun azimuth/elevation at entry/exit.

        Returns:
            Tuple (start, end). Each element is either None (sun never enters FOV
            today) or a (time, azimuth, elevation) tuple.

        """
        df_today = pd.DataFrame(
            {
                "azimuth": self.sun_data.solar_azimuth,
                "elevation": self.sun_data.solar_elevation,
            }
        )
        solpos = df_today.set_index(self.sun_data.times)

        alpha = solpos["azimuth"]
        elev = solpos["elevation"]

        # Azimuth in FOV
        in_fov = (alpha - self.azi_min_abs) % DEGREES_IN_CIRCLE <= (
            self.azi_max_abs - self.azi_min_abs
        ) % DEGREES_IN_CIRCLE

        # Elevation check — matches valid_elevation property logic, except the
        # "no bounds set" default here is `elev > 0` (strictly above horizon)
        # while the scalar property uses `>= 0` to include the horizon line.
        if self.config.min_elevation is None and self.config.max_elevation is None:
            valid_elev = elev > 0
        else:
            valid_elev = self._elevation_within_bounds(elev)

        # Sunset/sunrise offset — exclude times within the offset windows.
        sunset_utc = self.sun_data.sunset().replace(tzinfo=None)
        sunrise_utc = self.sun_data.sunrise().replace(tzinfo=None)
        offset_sunset = sunset_utc + timedelta(minutes=self.config.sunset_off)
        offset_sunrise = sunrise_utc + timedelta(minutes=self.config.sunrise_off)
        times_utc = solpos.index.tz_convert("UTC").tz_localize(None)
        in_sun_window = (times_utc >= offset_sunrise) & (times_utc <= offset_sunset)

        frame = in_fov & valid_elev & in_sun_window
        rows = solpos[frame]

        if rows.empty:
            return None, None

        first = rows.iloc[0]
        last = rows.iloc[-1]
        return (
            (
                rows.index[0].to_pydatetime(),
                float(first["azimuth"]),
                float(first["elevation"]),
            ),
            (
                rows.index[-1].to_pydatetime(),
                float(last["azimuth"]),
                float(last["elevation"]),
            ),
        )
