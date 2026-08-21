"""Weather-override slat angle for dual-axis covers (issue #1297).

Before this feature the weather handler named no tilt at all, so a venetian
under a storm retraction moved its carriage but left the slats wherever the
last solar cycle had put them. The fix is the same seam
``CustomPositionHandler`` already uses: the winning handler names the tilt on
its own ``PipelineResult``, which ``VenetianPolicy.post_pipeline_resolve``
then honors unconditionally.

Two contracts are pinned here, and the second matters as much as the first:

* configured tilt  → the handler claims it;
* **unset tilt     → the handler claims nothing**, so an existing install
  behaves byte-identically to before #1297. That is what makes the option
  safe to ship with no config migration.
"""

from __future__ import annotations

import pytest

from custom_components.adaptive_cover_pro.pipeline.handlers import (
    WeatherOverrideHandler,
)

from tests.test_pipeline.conftest import make_snapshot

pytestmark = pytest.mark.unit


_STORM_TILT = 100


def _weather_snapshot(**overrides):
    """Build an active weather retraction, clock open and min mode off."""
    kwargs = {
        "weather_override_active": True,
        "weather_override_position": 0,
        "clock_window_open": True,
        "weather_override_min_mode": False,
    }
    kwargs.update(overrides)
    return make_snapshot(**kwargs)


def test_weather_handler_names_the_configured_tilt() -> None:
    """A configured tilt rides out on the winning result's own tilt field."""
    result = WeatherOverrideHandler().evaluate(
        _weather_snapshot(weather_override_tilt=_STORM_TILT)
    )
    assert result is not None
    assert result.tilt == _STORM_TILT


def test_weather_handler_leaves_tilt_unclaimed_when_unset() -> None:
    """No configured tilt → no claim, so the slats are left exactly as they were.

    The "unset means unchanged" regression guard. If this ever goes red, every
    venetian install that upgraded without touching the weather step just
    started moving its slats during storms it never configured — and the
    no-migration argument for the option collapses with it.
    """
    result = WeatherOverrideHandler().evaluate(_weather_snapshot())
    assert result is not None
    assert result.tilt is None


def test_weather_handler_defers_in_min_mode_so_tilt_is_not_claimed() -> None:
    """Minimum-position mode stays position-only, tilt configured or not.

    In min mode the handler returns ``None`` and a lower-priority handler wins
    the seat. Letting the weather tilt through here would hand the tilt axis to
    a handler the pipeline explicitly outprioritized — the exact hole #1153
    closed. The config summary warns the user instead.
    """
    snapshot = _weather_snapshot(
        weather_override_min_mode=True, weather_override_tilt=_STORM_TILT
    )
    assert WeatherOverrideHandler().evaluate(snapshot) is None
