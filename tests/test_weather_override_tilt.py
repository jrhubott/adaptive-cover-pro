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

from custom_components.adaptive_cover_pro.const import (
    CONF_WEATHER_OVERRIDE_TILT,
    CoverType,
)
from custom_components.adaptive_cover_pro.cover_types.day_night_shade.policy import (
    DayNightShadePolicy,
)
from custom_components.adaptive_cover_pro.pipeline.handlers import (
    WeatherOverrideHandler,
)

from tests.test_pipeline.conftest import _make_mock_cover, make_snapshot

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


def test_day_night_shade_gets_no_blend_from_a_stored_weather_tilt() -> None:
    """A day/night shade holding the key end-to-end: options → snapshot → fabric.

    ``DayNightShadePolicy.weather_override_includes_tilt`` is False by design —
    its second axis is a fabric blend (sheer ↔ blackout), not a slat angle — so
    the config flow never offers the field. But the key can still be *stored* on
    such an entry: ``acp.set_weather_safety`` has no cover-type gate, and a
    venetian → day/night switch deliberately deletes nothing (#1132).

    Read ungated, that stray 100 would reach ``_resolve_blend`` as a
    handler-supplied blend, be honored unconditionally, and stash itself in
    ``_last_blend`` for ``maybe_update_tilt_only`` to replay — driving the shade
    to full blackout on every storm, with no UI field to see or clear it. The
    whole chain is walked here because each link is individually plausible; only
    the composition shows the damage.
    """
    from tests.test_cover_types.test_day_night_shade import _resolve_kwargs
    from tests.test_pipeline.test_snapshot_builder import _make_builder

    policy = DayNightShadePolicy()
    builder, _, _ = _make_builder(policy=policy)
    cover_data = _make_mock_cover()

    snapshot = builder.build(
        {CONF_WEATHER_OVERRIDE_TILT: _STORM_TILT},
        cover_data=cover_data,
        cover_type=CoverType.DAY_NIGHT_SHADE,
        climate_readings=None,
        manual_override_active=False,
        motion_timeout_active=False,
        weather_override_active=True,
        in_time_window=True,
        current_cover_position=None,
        is_glare_zone_enabled=lambda idx: False,
        effective_default=0,
        is_sunset_active=False,
    )
    result = WeatherOverrideHandler().evaluate(snapshot)
    assert result is not None

    resolved = policy.post_pipeline_resolve(result, cover=None, **_resolve_kwargs())
    assert resolved.tilt is None
    assert policy._last_blend is None
