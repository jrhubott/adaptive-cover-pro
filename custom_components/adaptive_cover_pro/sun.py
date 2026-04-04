"""Fetch sun data."""

from datetime import date, datetime, timedelta

import pandas as pd


class SunData:
    """Access local sun data."""

    def __init__(self, timezone, location, elevation) -> None:  # noqa: D107
        self.location = location  # astral.location.Location
        self.elevation = elevation
        self.timezone = timezone

    @property
    def times(self) -> pd.DatetimeIndex:
        """Define time interval."""
        start_date = date.today()
        end_date = start_date + timedelta(days=1)

        times = pd.date_range(
            start=start_date, end=end_date, freq="5min", tz=self.timezone, name="time"
        )
        return times

    @property
    def solar_azimuth(self) -> list:
        """Create list with solar azimuth data per 5 minutes."""
        index = 0
        azi_list = []
        for _i in self.times:
            azi_list.append(
                self.location.solar_azimuth(self.times[index], self.elevation)
            )
            index += 1
        return azi_list

    @property
    def solar_elevation(self) -> list:
        """Create list with solar elevation data per 5 minutes."""
        index = 0
        ele_list = []
        for _i in self.times:
            ele_list.append(
                self.location.solar_elevation(self.times[index], self.elevation)
            )
            index += 1
        return ele_list

    def sunset(self) -> datetime:
        """Fetch sunset time.

        Returns a far-future sentinel (midnight tonight) at polar latitudes
        during midnight sun when astral raises ValueError.
        """
        try:
            return self.location.sunset(date.today(), local=False)
        except (ValueError, AttributeError):
            # Polar midnight sun: sun never sets — treat as end of day
            today = date.today()
            return datetime(today.year, today.month, today.day, 23, 59, 59)  # noqa: DTZ001

    def sunrise(self) -> datetime:
        """Fetch sunrise time.

        Returns an early-morning sentinel (00:01 today) at polar latitudes
        during polar night when astral raises ValueError.
        """
        try:
            return self.location.sunrise(date.today(), local=False)
        except (ValueError, AttributeError):
            # Polar night: sun never rises — treat as very early morning
            today = date.today()
            return datetime(today.year, today.month, today.day, 0, 1, 0)  # noqa: DTZ001

    # def df_today(self)-> pd.DataFrame:
    #     """Create dataframe with azimuth and elevation data"""
    #     df_today = pd.DataFrame({"azimuth":self.solar_azimuth, "elevation":self.solar_elevation})
    #     df_today = df_today.set_index(self.times)
    #     return df_today
