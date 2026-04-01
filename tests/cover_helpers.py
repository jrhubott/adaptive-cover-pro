"""Helper functions for constructing cover instances in tests.

Provides build_vertical_cover, build_horizontal_cover, and build_tilt_cover
which accept flat kwargs (old-style API) and route them to the correct typed
config dataclasses (CoverConfig, VerticalConfig, etc.).
"""

from custom_components.adaptive_cover_pro.config_types import (
    CoverConfig,
    HorizontalConfig,
    TiltConfig,
    VerticalConfig,
)


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
_VERT_CONFIG_FIELDS = {"distance", "h_win", "window_depth", "sill_height"}

# HorizontalConfig field names
_HORIZ_CONFIG_FIELDS = {"awn_length", "awn_angle"}

# TiltConfig field names
_TILT_CONFIG_FIELDS = {"slat_distance", "depth", "mode"}


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
