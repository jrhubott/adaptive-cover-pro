"""Pure sun position analysis — no Home Assistant dependency.

Extracted from AdaptiveGeneralCover to enable standalone testing and reuse.
"""

from datetime import UTC, datetime, timedelta

import pandas as pd

from ..config_types import CoverConfig
from ..sun import SunData


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
    ) -> None:
        """Initialise with sun position, solar data, cover config, and logger."""
        self.sol_azi = sol_azi
        self.sol_elev = sol_elev
        self.sun_data = sun_data
        self.config = config
        self.logger = logger

    # ------------------------------------------------------------------
    # Azimuth helpers
    # ------------------------------------------------------------------

    @property
    def azi_min_abs(self) -> int:
        """Calculate absolute minimum azimuth of window's field of view.

        Returns:
            Minimum azimuth angle in degrees (0-360).

        """
        return (self.config.win_azi - self.config.fov_left + 360) % 360

    @property
    def azi_max_abs(self) -> int:
        """Calculate absolute maximum azimuth of window's field of view.

        Returns:
            Maximum azimuth angle in degrees (0-360).

        """
        return (self.config.win_azi + self.config.fov_right + 360) % 360

    @property
    def gamma(self) -> float:
        """Calculate gamma (surface solar azimuth).

        Gamma is the horizontal angle between the window's perpendicular and the
        sun's position, normalized to -180 to +180 degrees. Positive values indicate
        sun to the right of window normal, negative to the left.

        Returns:
            Gamma angle in degrees (-180 to +180).

        """
        return (self.config.win_azi - self.sol_azi + 180) % 360 - 180

    # ------------------------------------------------------------------
    # Validity checks
    # ------------------------------------------------------------------

    @property
    def valid_elevation(self) -> bool:
        """Check if sun elevation is within configured limits.

        Returns:
            True if sun elevation within configured min/max range (or no limits set).
            False if sun below horizon or outside configured limits.

        """
        if self.config.min_elevation is None and self.config.max_elevation is None:
            return self.sol_elev >= 0
        if self.config.min_elevation is None:
            return self.sol_elev <= self.config.max_elevation
        if self.config.max_elevation is None:
            return self.sol_elev >= self.config.min_elevation
        within_range = (
            self.config.min_elevation <= self.sol_elev <= self.config.max_elevation
        )
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
        valid = (
            (self.gamma < azi_min) & (self.gamma > -azi_max) & (self.valid_elevation)
        )
        self.logger.debug("Sun in front of window (ignoring blindspot)? %s", valid)
        return valid

    @property
    def sunset_valid(self) -> bool:
        """Check if current time is within sunset/sunrise offset period.

        Returns:
            True if current time is after (sunset + offset) or before (sunrise + offset).
            False during normal daytime operation.

        """
        sunset = self.sun_data.sunset().replace(tzinfo=None)
        sunrise = self.sun_data.sunrise().replace(tzinfo=None)
        now_naive = datetime.now(UTC).replace(tzinfo=None)
        after_sunset = now_naive > (
            sunset + timedelta(minutes=self.config.sunset_off)
        )
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
            return blindspot
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

    @property
    def default(self) -> float:
        """Get default position considering sunset offset.

        Returns:
            Sunset position if within sunset/sunrise offset period, otherwise
            normal default position.

        """
        default = self.config.h_def
        if self.sunset_valid and self.config.sunset_pos is not None:
            default = self.config.sunset_pos
        return default

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
        in_fov = (alpha - self.azi_min_abs) % 360 <= (
            self.azi_max_abs - self.azi_min_abs
        ) % 360

        # Elevation check — matches valid_elevation property logic
        if self.config.min_elevation is None and self.config.max_elevation is None:
            valid_elev = elev > 0
        elif self.config.min_elevation is None:
            valid_elev = elev <= self.config.max_elevation
        elif self.config.max_elevation is None:
            valid_elev = elev >= self.config.min_elevation
        else:
            valid_elev = (elev >= self.config.min_elevation) & (
                elev <= self.config.max_elevation
            )

        # Sunset/sunrise offset — exclude times within the offset windows.
        sunset_utc = self.sun_data.sunset().replace(tzinfo=None)
        sunrise_utc = self.sun_data.sunrise().replace(tzinfo=None)
        offset_sunset = sunset_utc + timedelta(minutes=self.config.sunset_off)
        offset_sunrise = sunrise_utc + timedelta(minutes=self.config.sunrise_off)
        times_utc = solpos.index.tz_convert("UTC").tz_localize(None)
        in_sun_window = (times_utc >= offset_sunrise) & (times_utc <= offset_sunset)

        frame = in_fov & valid_elev & in_sun_window

        if solpos[frame].empty:
            return None, None
        return (
            solpos[frame].index[0].to_pydatetime(),
            solpos[frame].index[-1].to_pydatetime(),
        )
