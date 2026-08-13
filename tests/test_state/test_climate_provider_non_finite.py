"""Non-finite sensor states must never leave the provider (issue #1237 audit).

``float("nan")``, ``float("inf")`` and ``float("-inf")`` all parse cleanly, so a
sensor publishing the literal strings ``nan`` / ``inf`` / ``-inf`` used to sail
straight through ``_read_float`` and out into ``ClimateReadings.irradiance_value``.
From there NaN survives ``max(nan, 0.0)`` inside ``plane_of_array_irradiance``,
multiplies through the whole transposition, and detonates in the gain sensor's
``int(round(gain_w))`` — ``ValueError: cannot convert float NaN to integer``, on
every single update, forever.

The guard lives in ``_read_float``, the provider's ONE entity-read-and-parse
seam, so a non-finite reading is indistinguishable from a non-numeric one:
``None``, one debug log, and every consumer's existing fail-open path.

Lives in its own module because ``test_climate_provider.py`` is a locked
regression surface for issues #864 / #269 and stays byte-identical.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.state.climate_provider import ClimateProvider

pytestmark = pytest.mark.unit

#: Every string Python's ``float()`` accepts but arithmetic cannot survive.
NON_FINITE_STATES = ("nan", "NaN", "inf", "-inf", "Infinity", "-Infinity")


@pytest.fixture
def mock_hass():
    h = MagicMock()
    h.states.get.return_value = None
    return h


@pytest.fixture
def provider(mock_hass, mock_logger):
    return ClimateProvider(hass=mock_hass, logger=mock_logger)


def _mock_state(entity_id: str, state: str):
    s = MagicMock()
    s.entity_id = entity_id
    s.state = state
    s.attributes = {}
    return s


class TestNonFiniteIrradiance:
    """The raw W/m² admission rejects anything arithmetic cannot survive."""

    @pytest.mark.parametrize("state", NON_FINITE_STATES)
    def test_raw_value_is_none(self, provider, mock_hass, state):
        mock_hass.states.get.return_value = _mock_state("sensor.solar", state)
        readings = provider.read(
            use_irradiance=False,
            irradiance_entity="sensor.solar",
            irradiance_threshold=300,
        )
        assert readings.irradiance_value is None

    @pytest.mark.parametrize("state", NON_FINITE_STATES)
    def test_raw_value_is_none_with_suppression_enabled_too(
        self, provider, mock_hass, state
    ):
        """Both admission paths go through the same reader, so both are guarded."""
        mock_hass.states.get.return_value = _mock_state("sensor.solar", state)
        readings = provider.read(
            use_irradiance=True,
            irradiance_entity="sensor.solar",
            irradiance_threshold=300,
        )
        assert readings.irradiance_value is None

    @pytest.mark.parametrize("state", NON_FINITE_STATES)
    def test_it_logs_the_same_debug_line_as_a_non_numeric_state(
        self, provider, mock_hass, mock_logger, state
    ):
        """Treated *exactly* like garbage — same message, so triage reads alike."""
        mock_hass.states.get.return_value = _mock_state("sensor.solar", state)
        provider.read(use_irradiance=False, irradiance_entity="sensor.solar")
        messages = [str(call.args[0]) for call in mock_logger.debug.call_args_list]
        assert any("non-numeric value" in m for m in messages)


class TestNonFiniteThresholdsFailOpen:
    """The Schmitt-latch pair keeps its fail-open contract for these states.

    Not a behaviour change dressed up as a test: a NaN already compared False
    against both edges, so ``activate`` was already False. The guard only fixes
    the release edge, which used to report "not cleared" for NaN when a release
    threshold was configured — i.e. a garbage reading could pin the suppression
    latch on. ``(False, True)`` is the documented contract for every failed read.
    """

    @pytest.mark.parametrize("state", NON_FINITE_STATES)
    @pytest.mark.parametrize("release", [None, 500.0])
    def test_irradiance_pair_fails_open(self, provider, mock_hass, state, release):
        mock_hass.states.get.return_value = _mock_state("sensor.solar", state)
        readings = provider.read(
            use_irradiance=True,
            irradiance_entity="sensor.solar",
            irradiance_threshold=300,
            irradiance_release_threshold=release,
        )
        assert readings.irradiance_below_threshold is False
        assert readings.irradiance_release_cleared is True

    @pytest.mark.parametrize("state", NON_FINITE_STATES)
    def test_lux_pair_fails_open(self, provider, mock_hass, state):
        mock_hass.states.get.return_value = _mock_state("sensor.lux", state)
        readings = provider.read(
            use_lux=True,
            lux_entity="sensor.lux",
            lux_threshold=300,
            lux_release_threshold=500.0,
        )
        assert readings.lux_below_threshold is False
        assert readings.lux_release_cleared is True

    @pytest.mark.parametrize("state", NON_FINITE_STATES)
    def test_cloud_coverage_pair_fails_open(self, provider, mock_hass, state):
        mock_hass.states.get.return_value = _mock_state("sensor.cloud", state)
        readings = provider.read(
            use_cloud_coverage=True,
            cloud_coverage_entity="sensor.cloud",
            cloud_coverage_threshold=70,
            cloud_coverage_release_threshold=50.0,
        )
        assert readings.cloud_coverage_above_threshold is False
        assert readings.cloud_coverage_release_cleared is True


class TestFiniteReadingsStillPass:
    """The guard rejects only the pathological values, nothing else."""

    @pytest.mark.parametrize("state", ["0", "-12.5", "1e30", "612.5"])
    def test_ordinary_and_extreme_finite_values_survive(
        self, provider, mock_hass, state
    ):
        mock_hass.states.get.return_value = _mock_state("sensor.solar", state)
        readings = provider.read(use_irradiance=False, irradiance_entity="sensor.solar")
        assert readings.irradiance_value == pytest.approx(float(state))
