"""``ClimateProvider.read_irradiance_unit`` — issue #1280.

HA's ``irradiance`` device class permits TWO units: W/m² and BTU/(h·ft²) — the
latter is what HA presents on the imperial unit system, and
``hass.states.get(entity).state`` returns that converted number. Nothing
upstream ever checked which one an irradiance entity reports, so a BTU-unit
install's raw number silently arrived at the estimated-solar-gain sensor as if
it were W/m², under-reporting gain by roughly a factor of 3
(1 BTU/(h·ft²) = 3.15 W/m²).

This read is DELIBERATELY separate from :meth:`ClimateProvider.read` and its
``_read_irradiance`` admission path — folding it in there would either touch
the shared threshold/admission read that cloud suppression depends on (a
change issue #1280's fix explicitly forbids) or add a second
``hass.states.get`` call for the SAME entity, breaking
``test_climate_provider.py``'s "read exactly once per cycle" contract. The
estimated-solar-gain sensor is the only consumer, so the coordinator calls
this as a genuinely separate read.

Lives in its own module for the same reason ``test_climate_provider_non_finite.py``
does: ``test_climate_provider.py`` is a locked regression surface (issues
#864 / #269 / #1237) and stays byte-identical.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.state.climate_provider import ClimateProvider

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_hass():
    h = MagicMock()
    h.states.get.return_value = None
    return h


@pytest.fixture
def provider(mock_hass, mock_logger):
    return ClimateProvider(hass=mock_hass, logger=mock_logger)


def _mock_state(entity_id: str, state: str, attributes: dict | None = None):
    s = MagicMock()
    s.entity_id = entity_id
    s.state = state
    s.attributes = attributes or {}
    return s


class TestReadIrradianceUnit:
    """The raw ``unit_of_measurement`` read — no interpretation, no gating."""

    def test_none_when_no_entity_is_configured(self, provider, mock_hass):
        assert provider.read_irradiance_unit(None) is None
        mock_hass.states.get.assert_not_called()

    def test_returns_the_metric_unit(self, provider, mock_hass):
        mock_hass.states.get.return_value = _mock_state(
            "sensor.solar", "612.5", {"unit_of_measurement": "W/m²"}
        )
        assert provider.read_irradiance_unit("sensor.solar") == "W/m²"

    def test_returns_the_imperial_unit_when_thats_what_the_entity_reports(
        self, provider, mock_hass
    ):
        mock_hass.states.get.return_value = _mock_state(
            "sensor.solar", "190.0", {"unit_of_measurement": "BTU/(h⋅ft²)"}
        )
        assert provider.read_irradiance_unit("sensor.solar") == "BTU/(h⋅ft²)"

    def test_none_when_the_entity_carries_no_unit_attribute(self, provider, mock_hass):
        mock_hass.states.get.return_value = _mock_state("sensor.solar", "612.5")
        assert provider.read_irradiance_unit("sensor.solar") is None

    def test_none_when_the_entity_does_not_exist(self, provider, mock_hass):
        mock_hass.states.get.return_value = None
        assert provider.read_irradiance_unit("sensor.missing") is None

    def test_does_not_touch_the_per_cycle_read_call_count(self, provider, mock_hass):
        """A SEPARATE call site — ``.read()``'s own single read is untouched."""
        mock_hass.states.get.return_value = _mock_state(
            "sensor.solar", "250", {"unit_of_measurement": "W/m²"}
        )
        provider.read(
            use_irradiance=True,
            irradiance_entity="sensor.solar",
            irradiance_threshold=300,
        )
        reads_from_dot_read = [
            c
            for c in mock_hass.states.get.call_args_list
            if c.args and c.args[0] == "sensor.solar"
        ]
        assert len(reads_from_dot_read) == 1
        # A subsequent, independent call to the new method is a SECOND read —
        # proving the two are genuinely decoupled call sites.
        provider.read_irradiance_unit("sensor.solar")
        all_reads = [
            c
            for c in mock_hass.states.get.call_args_list
            if c.args and c.args[0] == "sensor.solar"
        ]
        assert len(all_reads) == 2
