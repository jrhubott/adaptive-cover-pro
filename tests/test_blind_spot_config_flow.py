"""Config-flow blind-spot multi-slot rendering + per-slot validation (#701)."""

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


def test_schema_renders_all_slot_keys():
    schema = blind_spot_schema({"fov_left": 45, "fov_right": 45})
    keys = _schema_keys(schema)
    # Slot 1 legacy keys
    assert "blind_spot_left" in keys
    assert "blind_spot_right" in keys
    # Slots 2 and 3
    assert "blind_spot_left_2" in keys
    assert "blind_spot_right_2" in keys
    assert "blind_spot_left_3" in keys
    assert "blind_spot_right_3" in keys


def test_per_slot_left_right_errors():
    from custom_components.adaptive_cover_pro.config_flow import (
        _blind_spot_step_errors,
    )

    # Slot 2 right <= left → error keyed on right_2.
    errors = _blind_spot_step_errors(
        {"blind_spot_left_2": 30, "blind_spot_right_2": 20}
    )
    assert "blind_spot_right_2" in errors

    # Valid slot 2 → no error.
    assert (
        _blind_spot_step_errors({"blind_spot_left_2": 10, "blind_spot_right_2": 30})
        == {}
    )

    # Absent slot keys → no error.
    assert _blind_spot_step_errors({"blind_spot_left": 5}) == {}


# ----------------------------------------------------------------------------
# Geometry-save clamp (#852): narrowing the FOV on the geometry step must
# re-clamp stale blind-spot slot values, not leave them stranded past the new
# span's slider max.
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
async def test_geometry_save_clamps_stale_blind_spot_to_narrowed_fov():
    # Starting FOV 86/86 (edges=172) with an enabled blind-spot slot pinned at
    # the old edge; narrowing to 75/75 (edges=150) must clamp the stored
    # right value down to the new max instead of leaving it stranded at 172.
    flow = _options_flow(
        {
            CONF_FOV_LEFT: 86,
            CONF_FOV_RIGHT: 86,
            CONF_ENABLE_BLIND_SPOT: True,
            "blind_spot_left": 0,
            "blind_spot_right": 172,
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
    assert flow.options["blind_spot_right"] == 150
    assert flow.options["blind_spot_left"] == 0  # already in range, unchanged
