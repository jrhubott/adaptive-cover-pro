"""Enums for Adaptive Cover Pro integration."""

from enum import Enum, StrEnum


class CoverType(StrEnum):
    """Cover type enumeration."""

    BLIND = "cover_blind"
    AWNING = "cover_awning"
    TILT = "cover_tilt"

    @property
    def display_name(self) -> str:
        """Return human-readable display name."""
        return {
            self.BLIND: "Vertical Cover",
            self.AWNING: "Horizontal Cover",
            self.TILT: "Tilt Cover",
        }[self]


class TiltMode(StrEnum):
    """Tilt mode enumeration for venetian blinds."""

    MODE1 = "mode1"  # Single direction (0-90°)
    MODE2 = "mode2"  # Bi-directional (0-180°)

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


class ClimateStrategy(Enum):
    """Climate control strategies."""

    WINTER_HEATING = "winter_heating"  # Open for solar heating
    WINTER_INSULATION = (
        "winter_insulation"  # Close for heat retention when sun not hitting window
    )
    SUMMER_COOLING = "summer_cooling"  # Close for heat blocking
    LOW_LIGHT = "low_light"  # Use default position
    GLARE_CONTROL = "glare_control"  # Use calculated position


class ControlState(StrEnum):
    """Control status states for diagnostic sensor."""

    ACTIVE = "active"
    OUTSIDE_TIME_WINDOW = "outside_time_window"
    POSITION_DELTA_TOO_SMALL = "position_delta_too_small"
    TIME_DELTA_TOO_SMALL = "time_delta_too_small"
    MANUAL_OVERRIDE = "manual_override"
    AUTOMATIC_CONTROL_OFF = "automatic_control_off"
    SUN_NOT_VISIBLE = "sun_not_visible"


class ControlMethod(StrEnum):
    """What is currently driving the cover position.

    Priority order (highest to lowest):
    FORCE > WEATHER > MOTION > MANUAL > CLOUD > SUMMER/WINTER > SOLAR > DEFAULT
    """

    SOLAR = "solar"
    """Sun is within the FOV; cover follows the calculated sun-position."""

    SUMMER = "summer"
    """Climate mode: temperature above max threshold; cover closes to block heat."""

    WINTER = "winter"
    """Climate mode: temperature below min threshold; cover opens for solar heat gain."""

    DEFAULT = "default"
    """Sun is outside FOV, elevation limits, blind spot, or sunset offset window."""

    MANUAL = "manual_override"
    """User manually moved the cover; automatic control is paused."""

    MOTION = "motion_timeout"
    """No occupancy detected after timeout; cover returns to default position."""

    FORCE = "force_override"
    """A force override binary sensor is active; cover moves to the override position."""

    WEATHER = "weather_override"
    """Weather conditions (wind/rain/storm) exceed thresholds; covers retract for safety."""

    CLOUD = "cloud_suppression"
    """Cloud coverage suppresses solar radiation; covers use default position."""

    GLARE_ZONE = "glare_zone"
    """Glare zone protection active; cover extends to shield a floor zone."""
