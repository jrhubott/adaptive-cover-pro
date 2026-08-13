"""``coordinator.solar_transmittance`` — the HA-side gathering seam (#1236).

The pure blend lives in ``engine/solar_transmittance``; this accessor is the
only place the three inputs are gathered (the options, the policy's coverage
fraction, the pipeline's target position). Both the diagnostics block and the
future estimated-solar-gain sensor read the SAME per-cycle value, so getting
the position frame wrong here would be wrong everywhere at once.
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover_pro.const import (
    CONF_INVERSE_STATE,
    CONF_SOLAR_COVER_SHADE,
    CONF_SOLAR_COVER_SIDE,
    CONF_SOLAR_G_TOTAL,
    CONF_SOLAR_PROPERTIES_ENABLED,
)
from custom_components.adaptive_cover_pro.coordinator import (
    AdaptiveDataUpdateCoordinator,
)
from custom_components.adaptive_cover_pro.cover_types import get_policy
from custom_components.adaptive_cover_pro.services.configuration_service import (
    ConfigurationService,
)

pytestmark = pytest.mark.unit


_ENABLED_EXTERNAL_DARK = {
    CONF_SOLAR_PROPERTIES_ENABLED: True,
    CONF_SOLAR_COVER_SIDE: "external",
    CONF_SOLAR_COVER_SHADE: "dark",
}


def _coordinator(
    cover_type: str = "cover_blind",
    options: dict | None = None,
    *,
    state: int = 0,
) -> MagicMock:
    """Coordinator stub carrying only the seams the accessor reads.

    Same pattern as ``tests/test_coordinator_tilt_read_inverted.py`` — a
    ``MagicMock`` for the surrounding object with the REAL unbound method bound
    onto it, so the code under test is the shipped code.

    ``state`` is the coordinator's POST-inversion published position. It is set
    deliberately: nothing in this accessor may read it.
    """
    coord = MagicMock()
    coord._policy = get_policy(cover_type)
    coord.config_entry.options = dict(options or {})
    coord.state = state
    coord._config_service = ConfigurationService(
        hass=MagicMock(),
        config_entry=coord.config_entry,
        logger=MagicMock(),
        cover_type=cover_type,
        temp_toggle=None,
        lux_toggle=None,
        irradiance_toggle=None,
    )
    coord.solar_transmittance = types.MethodType(
        AdaptiveDataUpdateCoordinator.solar_transmittance, coord
    )
    return coord


# ---------------------------------------------------------------------------
# Inert by default
# ---------------------------------------------------------------------------


def test_returns_none_when_the_feature_is_unconfigured() -> None:
    assert _coordinator().solar_transmittance(position=50) is None


def test_returns_none_when_explicitly_disabled() -> None:
    coord = _coordinator(options={CONF_SOLAR_PROPERTIES_ENABLED: False})
    assert coord.solar_transmittance(position=50) is None


# ---------------------------------------------------------------------------
# Populated when enabled
# ---------------------------------------------------------------------------


def test_returns_a_populated_result_when_enabled() -> None:
    result = _coordinator(options=_ENABLED_EXTERNAL_DARK).solar_transmittance(
        position=25
    )
    assert result is not None
    assert result.shaded_fraction == pytest.approx(0.75)
    assert result.g_shaded == pytest.approx(0.22)
    assert result.g_unshaded == pytest.approx(0.70)
    assert result.effective_g == pytest.approx(0.75 * 0.22 + 0.25 * 0.70)
    assert result.source == "preset"
    assert result.is_estimate is True


def test_direct_override_reaches_the_engine() -> None:
    coord = _coordinator(options={**_ENABLED_EXTERNAL_DARK, CONF_SOLAR_G_TOTAL: 0.05})
    result = coord.solar_transmittance(position=0)
    assert result is not None
    assert result.g_shaded == pytest.approx(0.05)
    assert result.source == "direct"


def test_awning_polarity_reaches_the_engine() -> None:
    # An awning at 25 % shades a quarter, so it admits MORE than a blind at 25 %.
    blind = _coordinator("cover_blind", _ENABLED_EXTERNAL_DARK).solar_transmittance(
        position=25
    )
    awning = _coordinator("cover_awning", _ENABLED_EXTERNAL_DARK).solar_transmittance(
        position=25
    )
    assert blind is not None
    assert awning is not None
    assert blind.shaded_fraction == pytest.approx(0.75)
    assert awning.shaded_fraction == pytest.approx(0.25)
    assert awning.effective_g > blind.effective_g


# ---------------------------------------------------------------------------
# Positions that carry no coverage answer
# ---------------------------------------------------------------------------


def test_position_none_reports_shaded_only() -> None:
    """No pipeline result this cycle ⇒ no fraction to blend against."""
    coord = _coordinator(options=_ENABLED_EXTERNAL_DARK)
    result = coord.solar_transmittance(position=None)
    assert result is not None
    assert result.shaded_fraction is None
    assert result.source == "shaded_only"
    assert result.effective_g == pytest.approx(result.g_shaded)


def test_tilt_only_cover_reports_shaded_only() -> None:
    coord = _coordinator("cover_tilt", _ENABLED_EXTERNAL_DARK)
    result = coord.solar_transmittance(position=40)
    assert result is not None
    assert result.shaded_fraction is None
    assert result.source == "shaded_only"


# ---------------------------------------------------------------------------
# ⚠️ Inverse state — the highest-value guard (#1028 invariant, issue #1236)
# ---------------------------------------------------------------------------


def test_inverse_state_install_reports_the_same_physical_coverage() -> None:
    """Two installs, the same PHYSICAL position, the same transmittance.

    ``PipelineResult.position`` is the logical, HA-semantics target; the
    coordinator's published ``state`` is what goes on the wire, and on an
    inverse-state install it is the complement. Reading the wire value here
    would report a blind that is three-quarters down as three-quarters OPEN —
    silently inverting ``effective_g`` for every inverse-state user, and the
    watt figure that later reads this value along with it.
    """
    logical_position = 25

    normal = _coordinator(
        options=_ENABLED_EXTERNAL_DARK,
        state=logical_position,  # not inverted → wire == logical
    ).solar_transmittance(position=logical_position)

    inverted = _coordinator(
        options={**_ENABLED_EXTERNAL_DARK, CONF_INVERSE_STATE: True},
        state=100 - logical_position,  # inverted → wire is the complement
    ).solar_transmittance(position=logical_position)

    assert normal is not None
    assert inverted is not None
    assert normal.shaded_fraction == pytest.approx(0.75)
    assert inverted.shaded_fraction == pytest.approx(0.75)
    assert inverted.effective_g == pytest.approx(normal.effective_g)


@pytest.mark.parametrize("logical_position", [0, 10, 25, 50, 75, 90, 100])
def test_inverse_state_never_changes_the_answer(logical_position: int) -> None:
    """Across the whole travel, the wire frame must not leak into the estimate."""
    normal = _coordinator(
        options=_ENABLED_EXTERNAL_DARK, state=logical_position
    ).solar_transmittance(position=logical_position)
    inverted = _coordinator(
        options={**_ENABLED_EXTERNAL_DARK, CONF_INVERSE_STATE: True},
        state=100 - logical_position,
    ).solar_transmittance(position=logical_position)
    assert normal is not None
    assert inverted is not None
    assert inverted.shaded_fraction == pytest.approx(normal.shaded_fraction)
    assert inverted.effective_g == pytest.approx(normal.effective_g)


def test_accessor_never_reads_the_post_inversion_state() -> None:
    """Structural guard on the seam itself.

    The two frames differ only on inverse-state installs, so a ``self.state``
    read passes every non-inverted test and fails silently in production. Pin
    the source too, not just the behaviour.
    """
    import inspect

    source = inspect.getsource(AdaptiveDataUpdateCoordinator.solar_transmittance)
    body = source.split('"""')[-1]
    assert "self.state" not in body
    assert "final_state" not in body


def test_diagnostics_call_site_passes_the_pipeline_target_not_the_wire_value() -> None:
    """The single construction site must hand over ``result.position``.

    ``build_diagnostic_data`` already has ``self.state`` in scope (it populates
    ``DiagnosticContext.final_state`` two lines away), so the wrong value is
    genuinely within reach at the call site — pin which one it passes.
    """
    import inspect
    import re

    source = inspect.getsource(AdaptiveDataUpdateCoordinator.build_diagnostic_data)
    call = re.search(
        r"solar_transmittance=self\.solar_transmittance\((.*?)\)", source, re.DOTALL
    )
    assert call is not None, "the diagnostics context must carry solar_transmittance"
    argument = call.group(1)
    assert "result.position" in argument
    assert "self.state" not in argument
