"""Options → ``SolarPropertiesConfig`` plumbing (issue #1236).

The five solar-transmittance options are read in exactly one place. This pins
the defaults an absent key resolves to (which is what makes the feature inert
on every existing install) and the ``0.0`` vs. unset distinction that the
optional ``g_total`` override depends on.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.config_types import SolarPropertiesConfig
from custom_components.adaptive_cover_pro.const import (
    CONF_SOLAR_COVER_SHADE,
    CONF_SOLAR_COVER_SIDE,
    CONF_SOLAR_G_GLAZING,
    CONF_SOLAR_G_TOTAL,
    CONF_SOLAR_PROPERTIES_ENABLED,
    DEFAULT_SOLAR_COVER_SHADE,
    DEFAULT_SOLAR_COVER_SIDE,
    DEFAULT_SOLAR_G_GLAZING,
)
from custom_components.adaptive_cover_pro.services.configuration_service import (
    ConfigurationService,
)


@pytest.mark.unit
def test_empty_options_yield_the_inert_defaults() -> None:
    cfg = SolarPropertiesConfig.from_options({})
    assert cfg.enabled is False
    assert cfg.cover_side == DEFAULT_SOLAR_COVER_SIDE
    assert cfg.cover_shade == DEFAULT_SOLAR_COVER_SHADE
    assert cfg.g_total is None
    assert cfg.g_glazing == pytest.approx(DEFAULT_SOLAR_G_GLAZING)


@pytest.mark.unit
def test_enabled_reads_the_master_toggle() -> None:
    cfg = SolarPropertiesConfig.from_options({CONF_SOLAR_PROPERTIES_ENABLED: True})
    assert cfg.enabled is True


@pytest.mark.unit
def test_explicit_false_toggle_stays_off() -> None:
    cfg = SolarPropertiesConfig.from_options({CONF_SOLAR_PROPERTIES_ENABLED: False})
    assert cfg.enabled is False


@pytest.mark.unit
def test_selects_are_read_verbatim() -> None:
    cfg = SolarPropertiesConfig.from_options(
        {
            CONF_SOLAR_PROPERTIES_ENABLED: True,
            CONF_SOLAR_COVER_SIDE: "internal",
            CONF_SOLAR_COVER_SHADE: "dark",
        }
    )
    assert cfg.cover_side == "internal"
    assert cfg.cover_shade == "dark"


@pytest.mark.unit
def test_zero_g_total_is_distinguished_from_unset() -> None:
    """``0.0`` is a legitimate fully-opaque declaration, not "cleared"."""
    assert SolarPropertiesConfig.from_options({CONF_SOLAR_G_TOTAL: 0.0}).g_total == 0.0
    assert SolarPropertiesConfig.from_options({}).g_total is None


@pytest.mark.unit
def test_g_total_and_g_glazing_are_floats() -> None:
    cfg = SolarPropertiesConfig.from_options(
        {CONF_SOLAR_G_TOTAL: 1, CONF_SOLAR_G_GLAZING: 1}
    )
    assert isinstance(cfg.g_total, float)
    assert isinstance(cfg.g_glazing, float)


@pytest.mark.unit
def test_none_g_glazing_falls_back_to_the_default() -> None:
    """A cleared slider must not become 0 % glazing transmittance."""
    cfg = SolarPropertiesConfig.from_options({CONF_SOLAR_G_GLAZING: None})
    assert cfg.g_glazing == pytest.approx(DEFAULT_SOLAR_G_GLAZING)


@pytest.mark.unit
def test_configuration_service_delegates_to_from_options() -> None:
    svc = ConfigurationService(
        hass=MagicMock(),
        config_entry=MagicMock(),
        logger=MagicMock(),
        cover_type="cover_blind",
        temp_toggle=None,
        lux_toggle=None,
        irradiance_toggle=None,
    )
    options = {
        CONF_SOLAR_PROPERTIES_ENABLED: True,
        CONF_SOLAR_COVER_SIDE: "internal",
        CONF_SOLAR_COVER_SHADE: "light",
    }
    assert svc.get_solar_properties(options) == SolarPropertiesConfig.from_options(
        options
    )
