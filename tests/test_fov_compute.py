"""Field + policy registration for the FOV-from-measurements button (#565).

The "Generate field of view from measurements" button is a transient
``CONF_FOV_COMPUTE`` toggle: ticking it fills ``fov_left``/``fov_right`` from
the window width + reveal depth, then the form re-renders un-ticked. It is never
persisted, so it must NOT appear in ``live_option_keys``. Cover types that carry
window geometry (vertical blinds + venetians) advertise it; awnings/tilt don't.
"""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.adaptive_cover_pro import config_fields as cf
from custom_components.adaptive_cover_pro.config_flow import _get_sun_tracking_schema
from custom_components.adaptive_cover_pro.const import (
    CONF_FOV_COMPUTE,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CoverType,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy


def _keys(schema) -> list[str]:
    return [str(m) for m in schema.schema]


def test_conf_fov_compute_key():
    assert CONF_FOV_COMPUTE == "fov_compute"


def test_fov_compute_field_spec_registered_as_bool():
    spec = cf.FIELD_SPECS[CONF_FOV_COMPUTE]
    assert spec.validator is cf.ValidatorKind.BOOL
    assert spec.section == cf.SECTION_SUN_TRACKING


def test_fov_compute_default_is_false():
    assert cf.option_default(CONF_FOV_COMPUTE) is False


def test_no_legacy_fov_mode_symbols():
    # The two-mode selector was removed; its const symbols must be gone.
    from custom_components.adaptive_cover_pro import const

    assert not hasattr(const, "FovMode")
    assert not hasattr(const, "CONF_FOV_MODE")


@pytest.mark.parametrize(
    ("cover_type", "supported"),
    [
        (CoverType.BLIND, True),
        (CoverType.VENETIAN, True),
        (CoverType.AWNING, False),
        (CoverType.TILT, False),
    ],
)
def test_supports_fov_compute_per_cover_type(cover_type, supported):
    assert get_policy(cover_type).supports_fov_compute is supported


@pytest.mark.parametrize("cover_type", [CoverType.BLIND, CoverType.VENETIAN])
def test_toggle_in_schema_before_sliders(cover_type):
    keys = _keys(_get_sun_tracking_schema(cover_type))
    assert CONF_FOV_COMPUTE in keys
    assert keys.index(CONF_FOV_COMPUTE) < keys.index(CONF_FOV_LEFT)


@pytest.mark.parametrize("cover_type", [CoverType.AWNING, CoverType.TILT])
def test_no_toggle_for_unsupported_cover_types(cover_type):
    keys = _keys(_get_sun_tracking_schema(cover_type))
    assert CONF_FOV_COMPUTE not in keys
    # The plain fov sliders are still present.
    assert CONF_FOV_LEFT in keys
    assert CONF_FOV_RIGHT in keys


@pytest.mark.parametrize("cover_type", [CoverType.BLIND, CoverType.VENETIAN])
def test_toggle_is_transient_not_a_live_option_key(cover_type):
    # The toggle is popped before save, so it must never be a persisted option
    # key — otherwise options_service would treat a stale value as savable.
    assert CONF_FOV_COMPUTE not in get_policy(cover_type).live_option_keys()


@pytest.mark.parametrize("cover_type", [CoverType.BLIND, CoverType.VENETIAN])
def test_fov_sliders_optional_with_default_when_button_present(cover_type):
    # The sliders are relaxed to vol.Optional so the frontend "required field"
    # check never blocks the button's re-render submit (#565). Default preserved.
    schema = _get_sun_tracking_schema(cover_type)
    markers = {str(m): m for m in schema.schema}
    for key in (CONF_FOV_LEFT, CONF_FOV_RIGHT):
        assert isinstance(markers[key], vol.Optional)
        assert markers[key].default() == 90


@pytest.mark.parametrize("cover_type", [CoverType.AWNING, CoverType.TILT])
def test_fov_sliders_stay_required_without_button(cover_type):
    schema = _get_sun_tracking_schema(cover_type)
    markers = {str(m): m for m in schema.schema}
    for key in (CONF_FOV_LEFT, CONF_FOV_RIGHT):
        assert isinstance(markers[key], vol.Required)
