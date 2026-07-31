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
from .sliding_curtain import SlidingCurtainPolicy
from .tilt import TiltPolicy
from .venetian import VenetianPolicy
from .louvered_roof import LouveredRoofPolicy
from .day_night_shade import DayNightShadePolicy
from .dual_panel import DualPanelPolicy

# Virtual entry types — imported LAST so they sort to the bottom of
# registry-derived menus (``SENSOR_TYPE_MENU`` follows registration order).
# The building profile is not a physical cover (``controls_cover = False``);
# the group controls covers but is an orchestrator (``is_orchestrator =
# True``) — both are filtered out of the cover-type picker by capability.
from .building_profile import BuildingProfilePolicy
from .group import GroupPolicy


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


def percentage_option_keys() -> frozenset[str]:
    """Every option any registered cover type stores as a 0-100 % value.

    The structural enumeration behind ``config_fields._POSITION_ROLES``'
    completeness guard (issue #1132): a new percentage option shows up here the
    moment its section renders it, and the guard fails until it is classified as
    re-seeded / disclosed / neutral.

    Rendering the sections rather than reading ``FieldSpec.rng`` is what makes
    it exhaustive. Several percentage fields are built by the dynamic section
    builders and carry no range on their spec at all — ``cloudy_position`` has
    neither a range nor a static selector — so a registry-only sweep would
    quietly miss exactly the fields most likely to be forgotten.

    Lives here rather than in ``config_fields`` because it is a cross-policy
    question: ``config_fields`` must never import ``cover_types``.
    """
    from ..config_fields import is_percentage_marker

    keys: set[str] = set()
    for cover_type in POLICY_REGISTRY:
        policy = get_policy(cover_type)
        for section in policy.section_order():
            schema = policy.build_section_schema(section)
            keys.update(
                str(marker)
                for marker, validator in schema.schema.items()
                if is_percentage_marker(validator)
            )
    return frozenset(keys)


__all__ = [
    "POLICY_REGISTRY",
    "AwningPolicy",
    "BlindPolicy",
    "BuildingProfilePolicy",
    "CoverTypePolicy",
    "DayNightShadePolicy",
    "DualPanelPolicy",
    "GroupPolicy",
    "LouveredRoofPolicy",
    "OscillatingAwningPolicy",
    "RoofWindowPolicy",
    "SlidingCurtainPolicy",
    "TiltPolicy",
    "VenetianPolicy",
    "get_policy",
    "percentage_option_keys",
]
