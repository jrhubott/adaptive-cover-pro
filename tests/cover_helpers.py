"""Helper functions for constructing cover instances in tests.

Provides build_vertical_cover, build_horizontal_cover, and build_tilt_cover
which accept flat kwargs (old-style API) and route them to the correct typed
config dataclasses (CoverConfig, VerticalConfig, etc.).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from custom_components.adaptive_cover_pro.config_types import (
    CoverConfig,
    HorizontalConfig,
    LouveredRoofConfig,
    TiltConfig,
    VerticalConfig,
)
from custom_components.adaptive_cover_pro.engine.covers.base import (
    AdaptiveGeneralCover,
)
from custom_components.adaptive_cover_pro.engine.covers.louvered_roof import (
    AdaptiveLouveredRoofCover,
)

_COVERAGE_HOOKS = (
    "round_toward_coverage",
    "coverage_pivot_percentage",
    "coverage_travel_bounds",
    "coverage_distance",
)


def attach_coverage_rounding(cover):
    """Give a cover *double* the real base coverage hooks (#1090, #1104, #1222).

    The solar branch asks the cover which way to quantise its raw percentage,
    where its coverage bottoms out, how far it can be driven, and how much
    coverage a candidate percentage carries, because only the engine knows
    whether coverage is monotonic in the percentage (it is not on a
    bi-directional slat axis), whether the axis carries a band of its own, and
    what the metric even measures — percentage points on the base engine,
    degrees off horizontal on the slat engine. Doubles standing in for an
    ``AdaptiveGeneralCover`` therefore have to answer all four calls — a
    ``MagicMock`` would otherwise hand back a mock where a ``float | None`` or a
    ``(low, high)`` pair is expected.

    ``coverage_distance`` is bound even though ``more_protective_position``
    reads the pivot first and short-circuits on the base ``None``, so today it
    is never reached on these doubles. Leaving it off would make that
    short-circuit load-bearing for the double rather than for the production
    rule it mirrors, and the first engine-backed double with a real pivot would
    hand the comparator a ``MagicMock`` to subtract.

    Binds the production implementations to the double rather than
    reimplementing floor/ceil in test-land, so a double can never quietly
    disagree with the rules it is standing in for. Returns *cover* for chaining.
    """
    for name in _COVERAGE_HOOKS:
        setattr(cover, name, getattr(AdaptiveGeneralCover, name).__get__(cover))
    return cover


def make_daytime_sun_data() -> MagicMock:
    """Return a SunData mock whose sunrise/sunset bracket *now*.

    ``SunGeometry.sunset_valid`` compares wall-clock now against the sunrise /
    sunset offsets, so a bare ``MagicMock`` makes it unevaluatable. Bracketing
    now with real datetimes makes ``sunset_valid`` cleanly ``False``, which is
    what a test asserting on ``direct_sun_valid`` needs without patching the
    property away.
    """
    now = datetime.now(UTC)
    sun_data = MagicMock()
    sun_data.timezone = "UTC"
    sun_data.sunrise.return_value = now - timedelta(hours=6)
    sun_data.sunset.return_value = now + timedelta(hours=6)
    return sun_data


def make_cover_config(**overrides) -> CoverConfig:
    """Create a CoverConfig with sensible defaults and optional overrides."""
    defaults = {
        "win_azi": 180,
        "fov_left": 45,
        "fov_right": 45,
        "h_def": 50,
        "sunset_pos": 0,
        "sunset_off": 0,
        "sunrise_off": 0,
        "max_pos": 100,
        "min_pos": 0,
        "max_pos_sun_only": False,
        "min_pos_sun_only": False,
        "blind_spot_left": None,
        "blind_spot_right": None,
        "blind_spot_elevation": None,
        "blind_spot_on": False,
        "min_elevation": None,
        "max_elevation": None,
    }
    defaults.update(overrides)
    return CoverConfig(**defaults)


def make_vertical_config(**overrides) -> VerticalConfig:
    """Create a VerticalConfig with sensible defaults and optional overrides."""
    defaults = {
        "distance": 0.5,
        "h_win": 2.0,
        "window_depth": 0.0,
        "sill_height": 0.0,
        "glare_zones": None,
    }
    defaults.update(overrides)
    return VerticalConfig(**defaults)


def make_horizontal_config(**overrides) -> HorizontalConfig:
    """Create a HorizontalConfig with sensible defaults and optional overrides."""
    defaults = {
        "awn_length": 2.0,
        "awn_angle": 0.0,
    }
    defaults.update(overrides)
    return HorizontalConfig(**defaults)


def make_tilt_config(**overrides) -> TiltConfig:
    """Create a TiltConfig with sensible defaults and optional overrides."""
    defaults = {
        "slat_distance": 0.03,
        "depth": 0.02,
        "mode": "mode1",
        "max_tilt": 100,
        "min_tilt": 0,
        "safety_margin": 0.0,
    }
    defaults.update(overrides)
    return TiltConfig(**defaults)


# Mapping from old flat kwarg names to CoverConfig field names
_COVER_CONFIG_RENAMES = {
    "max_pos_bool": "max_pos_sun_only",
    "min_pos_bool": "min_pos_sun_only",
}

# All CoverConfig field names (including old aliases)
_COVER_CONFIG_FIELDS = {
    "win_azi",
    "fov_left",
    "fov_right",
    "h_def",
    "sunset_pos",
    "sunset_off",
    "sunrise_off",
    "max_pos",
    "min_pos",
    "max_pos_sun_only",
    "min_pos_sun_only",
    "max_pos_bool",
    "min_pos_bool",
    "blind_spot_left",
    "blind_spot_right",
    "blind_spot_elevation",
    "blind_spot_on",
    "min_elevation",
    "max_elevation",
}

# VerticalConfig field names
_VERT_CONFIG_FIELDS = {
    "distance",
    "h_win",
    "window_depth",
    "sill_height",
    "glare_zones",
}

# HorizontalConfig field names
_HORIZ_CONFIG_FIELDS = {"awn_length", "awn_angle", "shade_mode"}

# TiltConfig field names
_TILT_CONFIG_FIELDS = {
    "slat_distance",
    "depth",
    "mode",
    "safety_margin",
    "angle_0",
    "angle_100",
    "horizontal_percent",
    "max_tilt",
    "min_tilt",
    "min_tilt_sun_only",
    "max_tilt_sun_only",
    "tilt_transform",
}


def build_vertical_cover(**kwargs):
    """Build an AdaptiveVerticalCover from flat kwargs (old-style API).

    Accepts the same flat kwargs as the old constructor and routes them
    to the correct typed config dataclasses.
    """
    from custom_components.adaptive_cover_pro.calculation import AdaptiveVerticalCover

    cover_kwargs = {}
    vert_kwargs = {}
    direct_kwargs = {}

    for k, v in kwargs.items():
        if k in _COVER_CONFIG_RENAMES:
            cover_kwargs[_COVER_CONFIG_RENAMES[k]] = v
        elif k in _COVER_CONFIG_FIELDS:
            cover_kwargs[k] = v
        elif k in _VERT_CONFIG_FIELDS:
            vert_kwargs[k] = v
        else:
            direct_kwargs[k] = v

    return AdaptiveVerticalCover(
        config=make_cover_config(**cover_kwargs),
        vert_config=make_vertical_config(**vert_kwargs),
        **direct_kwargs,
    )


def build_horizontal_cover(**kwargs):
    """Build an AdaptiveHorizontalCover from flat kwargs (old-style API)."""
    from custom_components.adaptive_cover_pro.calculation import AdaptiveHorizontalCover

    cover_kwargs = {}
    vert_kwargs = {}
    horiz_kwargs = {}
    direct_kwargs = {}

    for k, v in kwargs.items():
        if k in _COVER_CONFIG_RENAMES:
            cover_kwargs[_COVER_CONFIG_RENAMES[k]] = v
        elif k in _COVER_CONFIG_FIELDS:
            cover_kwargs[k] = v
        elif k in _VERT_CONFIG_FIELDS:
            vert_kwargs[k] = v
        elif k in _HORIZ_CONFIG_FIELDS:
            horiz_kwargs[k] = v
        else:
            direct_kwargs[k] = v

    return AdaptiveHorizontalCover(
        config=make_cover_config(**cover_kwargs),
        vert_config=make_vertical_config(**vert_kwargs),
        horiz_config=make_horizontal_config(**horiz_kwargs),
        **direct_kwargs,
    )


def build_tilt_cover(**kwargs):
    """Build an AdaptiveTiltCover from flat kwargs (old-style API)."""
    from custom_components.adaptive_cover_pro.calculation import AdaptiveTiltCover

    cover_kwargs = {}
    tilt_kwargs = {}
    direct_kwargs = {}

    for k, v in kwargs.items():
        if k in _COVER_CONFIG_RENAMES:
            cover_kwargs[_COVER_CONFIG_RENAMES[k]] = v
        elif k in _COVER_CONFIG_FIELDS:
            cover_kwargs[k] = v
        elif k in _TILT_CONFIG_FIELDS:
            tilt_kwargs[k] = v
        else:
            direct_kwargs[k] = v

    return AdaptiveTiltCover(
        config=make_cover_config(**cover_kwargs),
        tilt_config=make_tilt_config(**tilt_kwargs),
        **direct_kwargs,
    )


def build_louvered_roof_cover(
    *,
    sol_azi: float,
    sol_elev: float,
    roof_pitch: float,
    slat_distance: float = 0.02,
    depth: float = 0.03,
    mode: str = "mode2",
    win_azi: float = 180.0,
    fov_left: float = 90.0,
    fov_right: float = 90.0,
    max_slat_angle: float = 0.0,
    safety_margin: float = 0.0,
):
    """Build an AdaptiveLouveredRoofCover at an explicit sun/slat geometry.

    Shared by the engine and pipeline-helpers suites (#1105 audit) so the
    louvered-roof construction isn't reimplemented at each call site — a
    change to ``AdaptiveLouveredRoofCover``'s constructor only needs updating
    here.
    """
    return AdaptiveLouveredRoofCover(
        logger=MagicMock(),
        sol_azi=sol_azi,
        sol_elev=sol_elev,
        sun_data=MagicMock(),
        config=make_cover_config(
            win_azi=win_azi, fov_left=fov_left, fov_right=fov_right
        ),
        tilt_config=make_tilt_config(
            slat_distance=slat_distance,
            depth=depth,
            mode=mode,
            safety_margin=safety_margin,
        ),
        roof_config=LouveredRoofConfig(
            roof_pitch=roof_pitch, max_slat_angle=max_slat_angle
        ),
    )
