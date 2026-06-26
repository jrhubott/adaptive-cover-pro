"""Building-profile virtual entry-type policy.

A building profile is not a physical cover: it holds shared building-level
sensor entity IDs (weather, lux/irradiance, outside temperature, daytime
gate, sunrise/sunset time) that linked covers copy into their own options.
Its config entry registers no platforms and builds no coordinator — setup
short-circuits in ``__init__.async_setup_entry`` before any engine is built.

The policy exists only so the registry/menu machinery treats the profile
uniformly with real cover types. It declares ``controls_cover = False`` and
zero axes, which is the discriminator every cover-only surface filters on
(never a cover-type string branch).
"""

from __future__ import annotations

from typing import ClassVar

from ..const import CoverType
from .base import CoverAxis, CoverTypePolicy


class BuildingProfilePolicy(CoverTypePolicy, register=True):
    """Virtual entry type holding shared building-level sensor IDs."""

    cover_type = CoverType.BUILDING_PROFILE
    controls_cover: ClassVar[bool] = False
    axes: ClassVar[tuple[CoverAxis, ...]] = ()

    def build_calc_engine(self, **kwargs):  # type: ignore[override]  # noqa: ARG002
        """Never called — profile setup short-circuits before any engine build."""
        raise NotImplementedError  # pragma: no cover
