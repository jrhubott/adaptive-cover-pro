"""Per-policy default for the weather-retraction visibility toggle.

The ``weather_retraction_default`` ClassVar supplies the per-cover default for
``CONF_SHOW_WEATHER_RETRACTION`` — True for awning-style covers (wind/rain
retraction is their headline safety feature), False elsewhere. It is only a
default: any user can flip the toggle on for any cover type.
"""

from __future__ import annotations

from custom_components.adaptive_cover_pro.cover_types.awning import AwningPolicy
from custom_components.adaptive_cover_pro.cover_types.blind import BlindPolicy
from custom_components.adaptive_cover_pro.cover_types.oscillating_awning import (
    OscillatingAwningPolicy,
)
from custom_components.adaptive_cover_pro.cover_types.roof_window import (
    RoofWindowPolicy,
)
from custom_components.adaptive_cover_pro.cover_types.tilt import TiltPolicy
from custom_components.adaptive_cover_pro.cover_types.venetian import VenetianPolicy


def test_weather_retraction_defaults() -> None:
    """Awnings default the retraction pickers on; every other cover type off."""
    assert AwningPolicy.weather_retraction_default is True
    assert OscillatingAwningPolicy.weather_retraction_default is True
    assert BlindPolicy.weather_retraction_default is False
    assert TiltPolicy.weather_retraction_default is False
    assert VenetianPolicy.weather_retraction_default is False
    assert RoofWindowPolicy.weather_retraction_default is False
