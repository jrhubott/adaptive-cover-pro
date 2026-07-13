"""Config-flow blind-spot multi-slot rendering + per-slot validation.

Signed-gamma storage (issue #247): the schema exposes ``blind_spot_*_gamma``
keys with signed sliders (left ∈ [-fov_right, fov_left], right ∈
[-fov_left, fov_right]); the per-slot gate rejects an empty wedge
(``left_gamma + right_gamma <= 0``). The #868 left-backfill and #852 clamp-on-save
behaviours are preserved on the new keys.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.config_dynamic import blind_spot_schema
from custom_components.adaptive_cover_pro.const import (
    CONF_ENABLE_BLIND_SPOT,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CoverType,
)


def _schema_keys(schema) -> set[str]:
    return {str(marker) for marker in schema.schema}


def _selector_for(schema, key):
    for marker, sel in schema.schema.items():
        if str(marker) == key:
            return sel
    raise KeyError(key)


def test_schema_renders_all_gamma_slot_keys():
    schema = blind_spot_schema({"fov_left": 45, "fov_right": 45})
    keys = _schema_keys(schema)
    assert "blind_spot_left_gamma" in keys
    assert "blind_spot_right_gamma" in keys
    assert "blind_spot_left_gamma_2" in keys
    assert "blind_spot_right_gamma_2" in keys
    assert "blind_spot_left_gamma_3" in keys
    assert "blind_spot_right_gamma_3" in keys


def test_schema_does_not_render_legacy_keys():
    """Legacy keys are migration-read-only — they are NOT in the editable schema."""
    keys = _schema_keys(blind_spot_schema({"fov_left": 45, "fov_right": 45}))
    assert "blind_spot_left" not in keys
    assert "blind_spot_right" not in keys


def test_left_slider_bounds_are_neg_fov_right_to_fov_left():
    schema = blind_spot_schema({"fov_left": 60, "fov_right": 40})
    sel = _selector_for(schema, "blind_spot_left_gamma")
    assert sel.config["min"] == -40  # -fov_right
    assert sel.config["max"] == 60  # fov_left


def test_right_slider_bounds_are_neg_fov_left_to_fov_right():
    schema = blind_spot_schema({"fov_left": 60, "fov_right": 40})
    sel = _selector_for(schema, "blind_spot_right_gamma")
    assert sel.config["min"] == -60  # -fov_left
    assert sel.config["max"] == 40  # fov_right


def test_slot_2_left_gamma_survives_omission_when_right_is_configured():
    """Regression for issue #868 on the new keys: slot-2 left at rest (0) must
    not be dropped when its right edge is configured.
    """
    schema = blind_spot_schema({"fov_left": 45, "fov_right": 45})
    validated = schema(
        {
            "blind_spot_left_gamma": 0,
            "blind_spot_right_gamma": 10,
            "blind_spot_right_gamma_2": 30,
        }
    )
    assert validated.get("blind_spot_left_gamma_2") == 0


def test_slot_2_stays_inactive_when_completely_untouched():
    schema = blind_spot_schema({"fov_left": 45, "fov_right": 45})
    validated = schema({"blind_spot_left_gamma": 0, "blind_spot_right_gamma": 10})
    assert validated.get("blind_spot_left_gamma_2") == 0  # new default present
    assert "blind_spot_right_gamma_2" not in validated  # still genuinely absent

    from custom_components.adaptive_cover_pro.config_types import CoverConfig

    config = CoverConfig.from_options({"blind_spot": True, **validated})
    assert len(config.blind_spots) == 1  # slot 2 did NOT activate


def test_per_slot_empty_wedge_errors():
    from custom_components.adaptive_cover_pro.config_flow import (
        _blind_spot_step_errors,
    )

    # Empty/degenerate wedge (left_gamma + right_gamma <= 0) → error on right key.
    errors = _blind_spot_step_errors(
        {"blind_spot_left_gamma_2": 10, "blind_spot_right_gamma_2": -20}
    )
    assert "blind_spot_right_gamma_2" in errors

    # Exactly degenerate (sum == 0) → still an error.
    errors0 = _blind_spot_step_errors(
        {"blind_spot_left_gamma_2": 10, "blind_spot_right_gamma_2": -10}
    )
    assert "blind_spot_right_gamma_2" in errors0

    # Valid non-empty wedge → no error.
    assert (
        _blind_spot_step_errors(
            {"blind_spot_left_gamma_2": 35, "blind_spot_right_gamma_2": -15}
        )
        == {}
    )

    # Absent slot keys → no error.
    assert _blind_spot_step_errors({"blind_spot_left_gamma": 5}) == {}


# ----------------------------------------------------------------------------
# Geometry-save clamp (#852) on the signed-gamma keys.
# ----------------------------------------------------------------------------


def _options_flow(options: dict, sensor_type=CoverType.BLIND):
    from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler

    entry = MagicMock()
    entry.options = dict(options)
    entry.data = {"sensor_type": sensor_type}
    flow = OptionsFlowHandler(entry)
    flow.hass = MagicMock()
    flow.hass.states.get.return_value = None
    flow.sensor_type = sensor_type
    flow.options = dict(options)
    flow.async_step_init = AsyncMock(return_value={"type": "menu"})
    return flow


@pytest.mark.asyncio
async def test_geometry_save_clamps_stale_gamma_to_narrowed_fov():
    # FOV 86/86 with a gamma edge pinned near the old edge; narrowing to 75/75
    # must clamp the stored gamma down to the new signed bound.
    flow = _options_flow(
        {
            CONF_FOV_LEFT: 86,
            CONF_FOV_RIGHT: 86,
            CONF_ENABLE_BLIND_SPOT: True,
            "blind_spot_left_gamma": 86,
            "blind_spot_right_gamma": 0,
        }
    )
    result = await flow.async_step_geometry(
        {
            CONF_FOV_LEFT: 75,
            CONF_FOV_RIGHT: 75,
            "distance_shaded_area": 0.5,
        }
    )
    assert result["type"] == "menu"  # advanced (saved)
    assert flow.options["blind_spot_left_gamma"] == 75  # clamped to new fov_left
    assert flow.options["blind_spot_right_gamma"] == 0  # in range, unchanged
