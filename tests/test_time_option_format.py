"""The single wire format for time-typed options, across every write path (#1049).

``BLANK_TIME`` is ``"00:00:00"`` and the integration compares against it by
literal string equality in half a dozen places, so any stored time in a
different shape reads as a *configured* window end while
``TimeWindowManager`` maps it to tomorrow's midnight — an
``until_window_end`` override deadline that recedes a day at every local
midnight and never expires.

Three write paths reach ``config_entry.options``, and each closes the hole its
own way: the service paths (``import_config``, ``set_options``) reject a
malformed value outright, while the config/options flow **normalizes** it —
HA's ``TimeSelector`` validates via ``dt_util.parse_time`` but stores the raw
submission, so a non-frontend flow client can hand it ``"00:00"`` or
``"٠٧:٣٠:٠٠"`` and it persists verbatim. Rejection would be wrong there: the
user picked a real time, only in a shape the picker itself would never emit.

Service-path rejection lives in ``tests/services/test_export_import_service.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.adaptive_cover_pro.config_flow import OptionsFlowHandler
from custom_components.adaptive_cover_pro.const import (
    BLANK_TIME,
    CONF_END_TIME,
    CONF_START_TIME,
    CoverType,
)
from custom_components.adaptive_cover_pro.helpers import normalize_time_string


def _options_flow(options: dict) -> OptionsFlowHandler:
    entry = MagicMock()
    entry.options = dict(options)
    entry.data = {"sensor_type": CoverType.BLIND}
    flow = OptionsFlowHandler(entry)
    flow.hass = MagicMock()
    flow.hass.states.get.return_value = None
    flow.sensor_type = CoverType.BLIND
    flow.options = dict(options)
    flow.async_step_init = AsyncMock(return_value={"type": "menu"})
    return flow


# ---------------------------------------------------------------------------
# normalize_time_string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00:00", BLANK_TIME),  # HH:MM — the reported case
        ("0:00:00", BLANK_TIME),  # unpadded hour
        ("00:00:00\n", BLANK_TIME),  # trailing newline
        ("٠٠:٠٠:٠٠", BLANK_TIME),  # Arabic-Indic digits
        ("7:30", "07:30:00"),  # unpadded hour, non-midnight
        ("٠٧:٣٠:٠٠", "07:30:00"),
        ("22:30", "22:30:00"),
    ],
)
def test_normalizes_parsable_times_to_canonical_form(raw, expected):
    assert normalize_time_string(raw) == expected


@pytest.mark.parametrize("canonical", ["00:00:00", "07:00:00", "22:30:00", "23:59:59"])
def test_leaves_canonical_values_untouched(canonical):
    assert normalize_time_string(canonical) == canonical


@pytest.mark.parametrize("value", [None, "", "not a time", "25:00:00", 12, True, {}])
def test_returns_unparsable_values_unchanged(value):
    """Anything ``parse_time`` rejects is passed through for the caller to handle.

    The TimeSelector has already refused these, so normalization has nothing to
    canonicalize; inventing a value here would mask the rejection.
    """
    assert normalize_time_string(value) == value


# ---------------------------------------------------------------------------
# Options flow — async_step_automation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("time_key", [CONF_START_TIME, CONF_END_TIME])
@pytest.mark.parametrize("blank", ["00:00", "0:00:00", "00:00:00\n", "٠٠:٠٠:٠٠"])
@pytest.mark.asyncio
async def test_automation_step_treats_normalized_blank_as_unset(time_key, blank):
    """A midnight submission in any shape must strip, exactly as BLANK_TIME does.

    Without normalization the #492 blank-strip below tests ``in (None,
    BLANK_TIME)`` and misses, so the value persists and every literal sentinel
    check downstream reads it as a configured window end (issue #1049).
    """
    flow = _options_flow({time_key: "22:00:00"})

    await flow.async_step_automation({time_key: blank})

    assert time_key not in flow.options


@pytest.mark.parametrize("time_key", [CONF_START_TIME, CONF_END_TIME])
@pytest.mark.asyncio
async def test_automation_step_canonicalizes_a_real_time(time_key):
    flow = _options_flow({})

    await flow.async_step_automation({time_key: "7:30"})

    assert flow.options[time_key] == "07:30:00"


@pytest.mark.asyncio
async def test_automation_step_leaves_canonical_times_alone():
    flow = _options_flow({})

    await flow.async_step_automation(
        {CONF_START_TIME: "07:00:00", CONF_END_TIME: "22:30:00"}
    )

    assert flow.options[CONF_START_TIME] == "07:00:00"
    assert flow.options[CONF_END_TIME] == "22:30:00"


# ---------------------------------------------------------------------------
# Single-source guards
# ---------------------------------------------------------------------------


def test_time_option_keys_is_re_exported_by_const():
    """``const`` is the documented import surface for the dedup hooks.

    ``OPTION_RANGES`` is re-exported from ``config_fields`` so call sites read
    ``from .const import …``; ``TIME_OPTION_KEYS`` is the same kind of derived
    map and must be reachable the same way, not from two modules.
    """
    from custom_components.adaptive_cover_pro import config_fields, const

    assert const.TIME_OPTION_KEYS is config_fields.TIME_OPTION_KEYS


def test_every_time_selector_field_is_tagged_time():
    """``TIME_OPTION_KEYS`` covers every TimeSelector field in the registry.

    The derivation is only as good as the ``ValidatorKind.TIME`` tagging, so the
    guard is on the tagging itself: a field rendering a ``TimeSelector`` but
    tagged something else would slip past both service validators exactly as
    start_time/end_time did.
    """
    from homeassistant.helpers import selector

    from custom_components.adaptive_cover_pro.config_fields import (
        FIELD_SPECS,
        TIME_OPTION_KEYS,
    )

    for key, spec in FIELD_SPECS.items():
        if spec.make_selector is None:
            continue
        if isinstance(spec.make_selector(None, {}), selector.TimeSelector):
            assert key in TIME_OPTION_KEYS


def test_every_time_field_has_a_set_options_validator():
    """Registry-derived import validation and hand-written FIELD_VALIDATORS agree.

    ``TIME_OPTION_KEYS`` is derived from ``FIELD_SPECS`` but
    ``options_service.FIELD_VALIDATORS`` is hand-written, so a new
    ``ValidatorKind.TIME`` field would be validated on import and rejected
    outright by ``set_options`` ("not supported by this service"). Fail-closed,
    but confusing — this keeps the two in step.
    """
    from custom_components.adaptive_cover_pro.config_fields import TIME_OPTION_KEYS
    from custom_components.adaptive_cover_pro.services.options_service import (
        FIELD_VALIDATORS,
    )

    assert set(FIELD_VALIDATORS) >= TIME_OPTION_KEYS
