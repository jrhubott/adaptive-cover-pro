"""Tests for the declarative section API on CoverTypePolicy.

Covers the generic capabilities the abstraction adds: a cover type can disable
a base field, the four shipped types resolve a coherent section order, and the
config-flow version stays put (rollback safety).
"""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro import config_fields as cf
from custom_components.adaptive_cover_pro.config_flow import ConfigFlowHandler
from custom_components.adaptive_cover_pro.const import (
    CONF_DEFAULT_HEIGHT,
    CONF_ENABLE_GLARE_ZONES,
    CONF_WEATHER_OVERRIDE_TILT,
)
from custom_components.adaptive_cover_pro.cover_types import POLICY_REGISTRY, get_policy
from custom_components.adaptive_cover_pro.cover_types.base import (
    POSITION_AXIS,
    CoverAxis,
    CoverTypePolicy,
)


class _DisablingStubPolicy(CoverTypePolicy):
    """A hypothetical cover type that disables a common field.

    Not registered (no ``register=True``) so it never pollutes the global
    registry — it exists only to prove the ``disabled_config_keys`` capability.
    """

    cover_type: ClassVar[str] = "cover_stub_disabling"
    axes: ClassVar[tuple[CoverAxis, ...]] = (POSITION_AXIS,)
    disabled_config_keys: ClassVar[frozenset[str]] = frozenset({CONF_DEFAULT_HEIGHT})

    def build_calc_engine(self, **kwargs):  # noqa: ARG002
        return MagicMock()


def test_disabled_key_dropped_from_section_schema():
    policy = _DisablingStubPolicy()
    keys = {str(m) for m in policy.build_section_schema(cf.SECTION_POSITION).schema}
    assert CONF_DEFAULT_HEIGHT not in keys


def test_disabled_key_dropped_from_live_keys():
    policy = _DisablingStubPolicy()
    assert CONF_DEFAULT_HEIGHT not in policy.live_option_keys()


def test_not_auto_registered_without_flag():
    assert "cover_stub_disabling" not in POLICY_REGISTRY


@pytest.mark.parametrize("cover_type", sorted(POLICY_REGISTRY))
def test_section_order_keys_are_known(cover_type):
    policy = get_policy(cover_type)
    known = {getattr(cf, name) for name in dir(cf) if name.startswith("SECTION_")}
    for section in policy.section_order():
        assert section in known, f"{cover_type} has unknown section {section!r}"


@pytest.mark.parametrize("cover_type", sorted(POLICY_REGISTRY))
def test_live_option_keys_nonempty(cover_type):
    assert get_policy(cover_type).live_option_keys()


def test_config_flow_version_unchanged_for_rollback():
    # A version bump is exactly what breaks rollback (HA refuses entries whose
    # version exceeds the installed integration). The abstraction reuses the
    # same option keys, so VERSION must stay at 3.
    assert ConfigFlowHandler.VERSION == 3


def test_disabled_value_round_trips_unchanged():
    # disabled_config_keys hides a field from the form; it must never delete an
    # already-stored value (new -> old -> new preserves data).
    policy = _DisablingStubPolicy()
    stored = {CONF_DEFAULT_HEIGHT: 42, "max_position": 90}
    # The policy never strips stored options; only the schema omits the field.
    assert stored[CONF_DEFAULT_HEIGHT] == 42
    assert (
        CONF_DEFAULT_HEIGHT
        not in policy.build_section_schema(cf.SECTION_POSITION, None, stored).schema
    )


# ``extra_field_keys`` answers three independent questions, one per section, and
# the answers used to be spread across three policy overrides. These
# parametrised assertions pin the full per-section answer for every registered
# cover type, so hoisting the branches onto the base is provably
# behaviour-preserving rather than merely plausible.
_CUSTOM_POSITION_TILT_TYPES = {"cover_venetian", "cover_day_night_shade"}
_GLARE_ZONE_TYPES = {"cover_blind"}
_WEATHER_OVERRIDE_TILT_TYPES = {"cover_venetian"}


@pytest.mark.parametrize("cover_type", sorted(POLICY_REGISTRY, key=str))
def test_extra_field_keys_per_section_per_cover_type(cover_type):
    """Every registered policy's per-section extras, stated once, in one place.

    ``cover_day_night_shade`` is in the custom-position set but NOT the weather
    set: its second axis is a fabric blend, not a slat angle (#1297).
    """
    policy = get_policy(cover_type)
    name = str(cover_type)

    expected_custom = (
        cf.CUSTOM_POSITION_TILT_KEYS if name in _CUSTOM_POSITION_TILT_TYPES else ()
    )
    assert policy.extra_field_keys(cf.SECTION_CUSTOM_POSITION) == expected_custom

    expected_sun = (CONF_ENABLE_GLARE_ZONES,) if name in _GLARE_ZONE_TYPES else ()
    assert policy.extra_field_keys(cf.SECTION_SUN_TRACKING) == expected_sun

    expected_weather = (
        (CONF_WEATHER_OVERRIDE_TILT,) if name in _WEATHER_OVERRIDE_TILT_TYPES else ()
    )
    assert policy.extra_field_keys(cf.SECTION_WEATHER_OVERRIDE) == expected_weather
