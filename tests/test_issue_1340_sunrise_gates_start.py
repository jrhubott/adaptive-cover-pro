"""End-to-end guards for issue #1340 — "cover opens at Start Time before sunrise".

The reporter configures ``start_entity`` = 05:30 and a ``sunrise_time_entity``
that resolves to 06:25. At 05:30:17 the window opens, ``window_explicitly_started``
flips True, ``is_sunset_active`` flips False, and the **climate** handler
(priority 50 — it never reads ``is_sunset_active``) commands ``open_cover`` 100%
55 minutes before sunrise.

These tests exercise the whole chain a unit test of ``compute_effective_default``
cannot: the live ``TimeWindowManager`` → ``_read_current_effective_default`` →
the pipeline. They also pin the two properties the opt-in exists to protect:
#438's behaviour with the flag OFF, and live/forecast agreement with it ON.
"""

from __future__ import annotations

import datetime as dt
from datetime import UTC
from unittest.mock import MagicMock, patch

import pytest

from custom_components.adaptive_cover_pro.config_types import RuntimeConfig
from custom_components.adaptive_cover_pro.const import (
    CONF_DEFAULT_HEIGHT,
    CONF_SUNRISE_GATES_START,
    CONF_SUNSET_POS,
)
from custom_components.adaptive_cover_pro.helpers import (
    _read_current_effective_default,
    compute_effective_default,
)
from custom_components.adaptive_cover_pro.managers.time_window import TimeWindowManager
from custom_components.adaptive_cover_pro.pipeline.handlers.climate import (
    ClimateHandler,
)
from custom_components.adaptive_cover_pro.pipeline.handlers.default import (
    DefaultHandler,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.pipeline.types import ClimateOptions
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateReadings

from tests.test_pipeline.conftest import make_snapshot

pytestmark = pytest.mark.unit

_TIME_WINDOW = "custom_components.adaptive_cover_pro.managers.time_window"

# The reporter's morning: start 05:30, sunrise 06:25, sunset 19:00.
_START_STR = "05:30:00"
_OPTIONS = {CONF_DEFAULT_HEIGHT: 100, CONF_SUNSET_POS: 0}


def _today(hour: int, minute: int = 0) -> dt.datetime:
    """Return a naive datetime for today at the given wall-clock time."""
    today = dt.date.today()
    return dt.datetime(today.year, today.month, today.day, hour, minute, 0)


def _make_sun_data(
    *, sunset_hour: int = 19, sunrise_hour: int = 6, sunrise_minute: int = 25
) -> MagicMock:
    """Mock SunData with controllable sunset/sunrise (naive), as in #438's tests."""
    sun = MagicMock()
    sun.sunset.return_value = _today(sunset_hour, 0)
    sun.sunrise.return_value = _today(sunrise_hour, sunrise_minute)
    return sun


def _freeze_now(naive_dt: dt.datetime):
    """Patch helpers' ``dt.datetime.now(UTC)`` to return ``naive_dt`` as UTC-aware."""
    return patch(
        "custom_components.adaptive_cover_pro.helpers.dt.datetime",
        **{"now.return_value": naive_dt.replace(tzinfo=UTC)},
    )


def _make_time_mgr(*, gates_start: bool) -> TimeWindowManager:
    """Build a real TimeWindowManager wired the way the coordinator wires it.

    The sunrise value is materialised HERE, not inside the provider closure:
    ``_freeze_now`` swaps the stdlib ``datetime.datetime`` class wholesale, so a
    datetime constructed while frozen comes back as a MagicMock.
    """
    sunrise = _today(6, 25)
    mgr = TimeWindowManager(
        hass=MagicMock(),
        logger=MagicMock(),
        sunrise_provider=lambda: sunrise,
    )
    mgr.update_config(
        start_time=_START_STR,
        start_time_entity=None,
        end_time=None,
        end_time_entity=None,
        sunrise_gates_start=gates_start,
    )
    return mgr


def _live_frame(now: dt.datetime):
    """Return the three patches a live read at ``now`` needs, in one place.

    ``get_datetime_from_str`` is patched in the ``managers.time_window``
    namespace because the real parser dates its result from
    ``helpers.local_now_naive`` — a different binding from the one these tests
    pin (see the ``_HA_NOW`` note in tests/test_time_window_manager.py).
    """
    return (
        _freeze_now(now),
        patch(f"{_TIME_WINDOW}.local_now_naive", return_value=now),
        patch(f"{_TIME_WINDOW}.get_datetime_from_str", return_value=_today(5, 30)),
    )


def _read_live(mgr: TimeWindowManager, now: dt.datetime, sun_data) -> tuple[int, bool]:
    """``(effective_default, is_sunset_active)`` as the coordinator reads it at ``now``."""
    hass = MagicMock()
    hass.states.get.return_value = None
    frozen, local_now, parse = _live_frame(now)
    with frozen, local_now, parse:
        return _read_current_effective_default(hass, _OPTIONS, sun_data, mgr)


# ---------------------------------------------------------------------------
# TEST_GAP 1 — the live effective default across the morning boundary
# ---------------------------------------------------------------------------


def test_live_effective_default_holds_sunset_position_until_sunrise_with_flag_on():
    """Flag ON: the night position holds from the start time until sunrise (#1340)."""
    sun_data = _make_sun_data()
    mgr = _make_time_mgr(gates_start=True)

    assert _read_live(mgr, _today(5, 31), sun_data) == (0, True)
    assert _read_live(mgr, _today(6, 30), sun_data) == (100, False)


def test_live_effective_default_releases_at_start_time_with_flag_off():
    """Flag OFF: #438's decision of record — the start time ends the night position."""
    sun_data = _make_sun_data()
    mgr = _make_time_mgr(gates_start=False)

    assert _read_live(mgr, _today(5, 31), sun_data) == (100, False)
    assert _read_live(mgr, _today(6, 30), sun_data) == (100, False)


# ---------------------------------------------------------------------------
# TEST_GAP 3 — the forecast and the live pipeline must agree
# ---------------------------------------------------------------------------


def test_live_and_forecast_agree_at_morning_boundary_with_flag_on():
    """The card's forecast and the live pipeline must not disagree at 05:31.

    ``forecast.py`` calls ``compute_effective_default`` WITHOUT
    ``window_explicitly_started``, so it holds 0% until sunrise while the live
    pipeline (pre-#1340, flag ON) commanded 100% at the start time — visible in
    all three diagnostics attachments as ``position_forecast`` 06:30 vs a
    05:30 ``cover_command_sent``.
    """
    sun_data = _make_sun_data()
    mgr = _make_time_mgr(gates_start=True)

    for now in (_today(5, 31), _today(6, 30)):
        forecast = compute_effective_default(
            100,
            0,
            sun_data,
            0,
            0,
            eval_time=now.replace(tzinfo=UTC),
        )
        assert _read_live(mgr, now, sun_data) == forecast, f"diverged at {now:%H:%M}"


# ---------------------------------------------------------------------------
# TEST_GAP 4 — climate (priority 50) is what actually opens the cover
# ---------------------------------------------------------------------------


def _make_blind_cover():
    """Build a blind with no valid direct sun — the reporter's 05:30 sun state."""
    cover = MagicMock()
    cover.direct_sun_valid = False
    cover.valid = False
    cover.calculate_percentage = MagicMock(return_value=60.0)
    cover.calculate_raw_percentage = MagicMock(return_value=60.0)
    cover.logger = MagicMock()
    config = MagicMock()
    config.min_pos = None
    config.max_pos = None
    config.min_pos_sun_only = False
    config.max_pos_sun_only = False
    config.min_pos_sun_tracking = None
    cover.config = config
    return cover


def _evaluate_pipeline(mgr: TimeWindowManager, now: dt.datetime, sun_data):
    """Run climate + default against the live window state at ``now``."""
    hass = MagicMock()
    hass.states.get.return_value = None
    frozen, local_now, parse = _live_frame(now)
    with frozen, local_now, parse:
        in_window = mgr.is_active
        started = mgr.window_explicitly_started
        effective, active = compute_effective_default(
            100,
            0,
            sun_data,
            0,
            0,
            window_explicitly_started=started,
        )

    snap = make_snapshot(
        cover=_make_blind_cover(),
        climate_mode_enabled=True,
        climate_readings=ClimateReadings(
            inside_temperature=15.0,
            outside_temperature=None,
            is_presence=True,
            is_sunny=False,
            lux_below_threshold=False,
            irradiance_below_threshold=False,
            cloud_coverage_above_threshold=False,
        ),
        climate_options=ClimateOptions(
            temp_low=18.0,
            temp_high=26.0,
            temp_switch=False,
            transparent_blind=False,
            temp_summer_outside=None,
            cloud_suppression_enabled=False,
            winter_close_insulation=False,
        ),
        in_time_window=in_window,
        default_position=effective,
        is_sunset_active=active,
    )
    return PipelineRegistry([ClimateHandler(), DefaultHandler()]).evaluate(snap)


def _matched(result, handler: str) -> bool:
    """Whether ``handler`` matched in the decision trace."""
    return next(s.matched for s in result.decision_trace if s.handler == handler)


def test_climate_cannot_open_cover_before_sunrise_with_flag_on():
    """Flag ON: climate must be gated out, so the cover holds the sunset position.

    Restoring ``is_sunset_active`` alone would NOT stop the cover — climate at
    priority 50 outranks default and never consults it. The gate has to be the
    window itself.
    """
    result = _evaluate_pipeline(
        _make_time_mgr(gates_start=True), _today(5, 31), _make_sun_data()
    )

    assert _matched(result, "climate") is False
    assert _matched(result, "default") is True
    assert result.position == 0


def test_climate_wins_before_sunrise_with_flag_off():
    """Flag OFF: today's shipped trace, pinned verbatim from the diagnostics.

    ``[6] climate matched=TRUE … pos=100`` / ``[9] default matched=false …
    outprioritized by climate``. ``control_method`` stays DEFAULT even here —
    it labels the climate LOW_LIGHT *branch*, not the winning handler — so the
    trace is what distinguishes the two outcomes.
    """
    result = _evaluate_pipeline(
        _make_time_mgr(gates_start=False), _today(5, 31), _make_sun_data()
    )

    assert _matched(result, "climate") is True
    assert _matched(result, "default") is False
    assert result.position == 100


# ---------------------------------------------------------------------------
# The single runtime read
# ---------------------------------------------------------------------------


def test_runtime_config_reads_sunrise_gates_start():
    """``RuntimeConfig`` is the one place the option is read for the manager."""
    assert RuntimeConfig.from_options({}).time_window.sunrise_gates_start is False
    assert (
        RuntimeConfig.from_options(
            {CONF_SUNRISE_GATES_START: True}
        ).time_window.sunrise_gates_start
        is True
    )
