"""Per-cover-type policy registry.

The coordinator selects a single ``CoverTypePolicy`` instance at startup
and routes every cover-type-specific decision through it, so the shared
code paths (coordinator update cycle, cover command service, manual
override detection, config flow) never branch on cover type.
"""

from __future__ import annotations

from .base import POLICY_REGISTRY, CoverTypePolicy

# Importing each policy module triggers its ``register=True`` auto-registration
# in ``POLICY_REGISTRY`` (see ``CoverTypePolicy.__init_subclass__``). Import
# order sets the cover-type picker order (blind first, as before). A new cover
# type is added simply by creating its module and importing it here.
from .blind import BlindPolicy
from .awning import AwningPolicy
from .oscillating_awning import OscillatingAwningPolicy
from .roof_window import RoofWindowPolicy
from .tilt import TiltPolicy
from .venetian import VenetianPolicy

# Virtual entry type — imported LAST so it sorts to the bottom of the
# cover-type picker (``SENSOR_TYPE_MENU`` follows registration order). It is
# not a physical cover (``controls_cover = False``).
from .building_profile import BuildingProfilePolicy


def get_policy(cover_type) -> CoverTypePolicy:
    """Return a policy instance for the given cover-type identifier.

    Accepts a plain string, a ``CoverType`` ``StrEnum`` member, or any value
    with a ``.value`` attribute. Raises ``ValueError`` for unknown types —
    preserves the failure mode of the previous if/elif chain in
    ``coordinator.get_blind_data``.
    """
    key: str | None
    if cover_type is None:
        key = None
    elif hasattr(cover_type, "value"):
        key = cover_type.value
    else:
        key = cover_type
    cls = POLICY_REGISTRY.get(key) if key is not None else None
    if cls is None:
        msg = f"Unsupported cover type: {cover_type!r}"
        raise ValueError(msg)
    return cls()


def weather_retraction_default(cover_type) -> bool:
    """Policy default for the ``CONF_SHOW_WEATHER_RETRACTION`` toggle.

    Wraps :func:`get_policy` so callers (config flow, migration) read the
    per-cover-type default without branching on the cover-type string. Unknown
    or absent cover types fall back to ``False`` (pickers hidden).
    """
    try:
        return get_policy(cover_type).weather_retraction_default
    except ValueError:
        return False


__all__ = [
    "POLICY_REGISTRY",
    "AwningPolicy",
    "BlindPolicy",
    "BuildingProfilePolicy",
    "CoverTypePolicy",
    "OscillatingAwningPolicy",
    "RoofWindowPolicy",
    "TiltPolicy",
    "VenetianPolicy",
    "get_policy",
    "weather_retraction_default",
]
