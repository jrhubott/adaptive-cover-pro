"""Cloud-suppression slat angle for dual-axis covers (issue #175).

Before this option the cloud-suppression handler named no tilt at all on its
``cloudy_position`` branch, so a venetian holding a cloudy position left its
slats wherever the last solar cycle had put them and the room stayed dark for
as long as the cloud lasted. The fix is the seam
``pipeline/handlers/weather.py`` already documents as one of exactly two
sanctioned ways to drive the tilt axis (#1153): the winning handler names the
tilt on its own ``PipelineResult``.

Three contracts are pinned here, and the second matters as much as the first:

* configured tilt  → the handler claims it, clamped like every other
  non-sun tilt (#503);
* **unset tilt     → the handler claims nothing**, so an existing install
  behaves byte-identically to before #175. That is what makes the option
  safe to ship with no config migration, and it is what keeps
  ``test_cloudy_position_not_sunset_stays_untilted`` green;
* a configured ``0`` is a real answer (slats closed for privacy), not the
  absence of one — the ``is not None`` rule for optional numeric fields.
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.const import ControlMethod
from custom_components.adaptive_cover_pro.pipeline.handlers.cloud_suppression import (
    CloudSuppressionHandler,
)
from custom_components.adaptive_cover_pro.pipeline.types import ClimateOptions
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings

from tests.test_pipeline.conftest import make_snapshot

pytestmark = pytest.mark.unit


# The reporter's own values: a venetian holding cloudy_position 100 whose
# carriage is pinned closed by tilt-only mode, wanting the slats wide open.
_CLOUDY_POSITION = 100
_CLOUDY_TILT = 100
# Deliberately different from _CLOUDY_TILT so a test that accidentally reads
# the default tilt instead of the cloud tilt fails loudly.
_DEFAULT_TILT = 50


def _overcast_readings() -> ClimateReadings:
    """Build readings that resolve cloud suppression active: weather is not sunny."""
    return ClimateReadings(
        outside_temperature=None,
        inside_temperature=None,
        is_presence=True,
        is_sunny=False,
        lux_below_threshold=False,
        irradiance_below_threshold=False,
        cloud_coverage_above_threshold=False,
    )


def _cloud_options(**overrides) -> ClimateOptions:
    """Climate options with cloud suppression on and a cloudy position set."""
    kwargs = {
        "temp_low": None,
        "temp_high": None,
        "temp_switch": True,
        "transparent_blind": False,
        "temp_summer_outside": None,
        "cloud_suppression_enabled": True,
        "winter_close_insulation": False,
        "cloudy_position": _CLOUDY_POSITION,
    }
    kwargs.update(overrides)
    return ClimateOptions(**kwargs)


def _cloud_snapshot(*, options: ClimateOptions | None = None, **overrides):
    """Build a snapshot that clears every guard in ``evaluate``'s stack."""
    kwargs = {
        "direct_sun_valid": True,
        "in_time_window": True,
        "climate_readings": _overcast_readings(),
        "climate_options": options if options is not None else _cloud_options(),
        "is_sunset_active": False,
        "default_tilt": _DEFAULT_TILT,
        "sunset_tilt": 0,
    }
    kwargs.update(overrides)
    return make_snapshot(**kwargs)


def test_cloudy_branch_names_a_configured_tilt() -> None:
    """A configured cloudy tilt rides out on the winning result's own tilt field.

    The whole point of #175: the carriage holds ``cloudy_position`` while the
    slats go to the angle the user asked for. ``default_tilt`` is 50 here, so
    a result of 100 can only have come from ``cloudy_tilt``.
    """
    result = CloudSuppressionHandler().evaluate(
        _cloud_snapshot(options=_cloud_options(cloudy_tilt=_CLOUDY_TILT))
    )

    assert result is not None
    assert result.control_method is ControlMethod.CLOUD
    assert result.position == _CLOUDY_POSITION
    assert result.tilt == _CLOUDY_TILT


def test_cloudy_tilt_absent_names_nothing() -> None:
    """No configured tilt → no claim, so the slats hold exactly as before #175.

    The "unset means unchanged" invariant. ``cloudy_tilt`` has no ``DEFAULT_*``
    constant and no fallback to ``default_tilt``: an absent key must resolve to
    ``None`` or every venetian install that upgraded without opening the Light
    & Cloud step just started moving its slats under clouds it never configured
    — and the no-migration argument for the option collapses with it.

    This is the unit-level sibling of
    ``tests/test_pipeline/test_tilt_integration.py::test_cloudy_position_not_sunset_stays_untilted``,
    which pins the same rule end-to-end through the registry. If either goes
    red the change must stop.
    """
    result = CloudSuppressionHandler().evaluate(_cloud_snapshot())

    assert result is not None
    assert result.position == _CLOUDY_POSITION
    assert result.tilt is None


def test_cloudy_tilt_of_zero_is_a_real_answer() -> None:
    """``0`` means slats closed, not "unset".

    The optional-numeric rule: a slider that stores 0 is indistinguishable from
    a cleared field under a truthiness check, and closed slats during a cloudy
    hold is a legitimate privacy setting. Every read of this option is
    ``is not None``.
    """
    result = CloudSuppressionHandler().evaluate(
        _cloud_snapshot(options=_cloud_options(cloudy_tilt=0))
    )

    assert result is not None
    assert result.tilt == 0


def test_cloudy_tilt_without_a_cloudy_position_leaves_the_default_branch_alone() -> (
    None
):
    """With no ``cloudy_position`` the branch IS the default, so ``default_tilt`` wins.

    Characterization of the Layer 1 boundary: ``cloudy_tilt`` rides the
    ``cloudy_position`` branch only. The no-override branch resolves its
    position *from* the effective default, so #1214's rule says it must pair
    that with the effective default tilt — not with a cloud override the
    handler never took. Pinned so a later widening of the option is a visible
    decision rather than a side effect.
    """
    result = CloudSuppressionHandler().evaluate(
        _cloud_snapshot(
            options=_cloud_options(cloudy_position=None, cloudy_tilt=_CLOUDY_TILT),
            default_position=0,
        )
    )

    assert result is not None
    assert result.tilt == _DEFAULT_TILT


def test_sunset_outranks_a_configured_cloudy_tilt() -> None:
    """At sunset the sunset tilt still wins — #128's carve-out is untouched.

    The sunset branch resolves its position from the effective default, so it
    keeps pairing with ``compute_default_tilt``. A cloudy tilt is a daytime
    answer.
    """
    result = CloudSuppressionHandler().evaluate(
        _cloud_snapshot(
            options=_cloud_options(cloudy_tilt=_CLOUDY_TILT),
            is_sunset_active=True,
            sunset_tilt=0,
        )
    )

    assert result is not None
    assert result.tilt == 0


@pytest.mark.parametrize(
    ("cloudy_tilt", "min_tilt", "max_tilt", "min_sun_only", "max_sun_only", "expected"),
    [
        # Unconstrained band → the configured angle passes through.
        (_CLOUDY_TILT, 0, 100, False, False, _CLOUDY_TILT),
        # #503: an always-on cap pulls the cloud tilt down, exactly as it pulls
        # down default_tilt on the branch right next to it.
        (_CLOUDY_TILT, 0, 75, False, False, 75),
        # The reporter's own config (max_tilt 75, max_tilt_sun_only True). The
        # cap is sun-only and a cloudy hold is not sun tracking, so 100 stands.
        (_CLOUDY_TILT, 0, 75, False, True, _CLOUDY_TILT),
        # An always-on floor lifts a low cloud tilt.
        (10, 40, 100, False, False, 40),
        # A sun-only floor does not apply outside sun tracking.
        (10, 40, 100, True, False, 10),
    ],
)
def test_cloudy_tilt_is_clamped_by_tilt_limits(
    cloudy_tilt: int,
    min_tilt: int,
    max_tilt: int,
    min_sun_only: bool,
    max_sun_only: bool,
    expected: int,
) -> None:
    """The cloud tilt honours min_tilt/max_tilt with ``sun_valid=False``.

    Two precedents disagreed and this picks one deliberately: ``default_tilt``
    is clamped (#503) while ``sunset_tilt`` bypasses (#128). ``cloudy_position``
    — this option's own position sibling, resolved on the very same branch — is
    clamped, so the tilt is too. A user who capped their slats at 75 % meant it.
    Parametrised across the ``*_sun_only`` flags because a cloudy hold is never
    sun tracking, which is what makes the reporter's sun-only cap a no-op here.
    """
    result = CloudSuppressionHandler().evaluate(
        _cloud_snapshot(
            options=_cloud_options(cloudy_tilt=cloudy_tilt),
            min_tilt=min_tilt,
            max_tilt=max_tilt,
            min_tilt_sun_only=min_sun_only,
            max_tilt_sun_only=max_sun_only,
        )
    )

    assert result is not None
    assert result.tilt == expected
