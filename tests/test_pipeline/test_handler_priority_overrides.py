"""Tests for configurable built-in handler priorities.

Covers the override resolver, build_handlers applying overrides, the registry
re-sorting, the tie-break rule, the priority-chain visualization, option
validation, and the diagnostics surfacing.
"""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.adaptive_cover_pro.const import (
    CONF_CLIMATE_PRIORITY,
    CONF_MANUAL_OVERRIDE_PRIORITY,
    CONF_SOLAR_PRIORITY,
    CONF_WEATHER_PRIORITY,
    OPTION_RANGES,
)
from custom_components.adaptive_cover_pro.pipeline.handlers import (
    HANDLER_PRIORITY_CONF,
    HANDLER_PRIORITY_DEFAULTS,
    build_handlers,
    resolve_handler_priority,
)
from custom_components.adaptive_cover_pro.pipeline.registry import PipelineRegistry
from custom_components.adaptive_cover_pro.priority_chain import build_priority_chain


def _by_name(handlers):
    return {h.name: h.priority for h in handlers}


# ---------------------------------------------------------------------------
# resolve_handler_priority
# ---------------------------------------------------------------------------


def test_resolve_falls_back_to_class_default():
    assert resolve_handler_priority({}, "weather") == 90
    assert resolve_handler_priority({}, "climate") == 50


def test_resolve_uses_override():
    assert resolve_handler_priority({CONF_WEATHER_PRIORITY: 30}, "weather") == 30


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, 1), (250, 99), (1, 1), (99, 99), (50, 50), ("42", 42)],
)
def test_resolve_clamps_and_coerces(raw, expected):
    assert resolve_handler_priority({CONF_WEATHER_PRIORITY: raw}, "weather") == expected


def test_resolve_none_value_is_default():
    # A cleared option stores None — must fall back, not crash.
    assert resolve_handler_priority({CONF_CLIMATE_PRIORITY: None}, "climate") == 50


# ---------------------------------------------------------------------------
# build_handlers
# ---------------------------------------------------------------------------


def test_build_handlers_default_priorities():
    priorities = _by_name(build_handlers({}))
    for name, default in HANDLER_PRIORITY_DEFAULTS.items():
        assert priorities[name] == default
    # Default handler is never configurable and stays at the floor.
    assert priorities["default"] == 0


def test_build_handlers_applies_overrides():
    priorities = _by_name(
        build_handlers({CONF_WEATHER_PRIORITY: 30, CONF_CLIMATE_PRIORITY: 85})
    )
    assert priorities["weather"] == 30
    assert priorities["climate"] == 85
    # Untouched handlers keep their class default.
    assert priorities["manual_override"] == 80


def test_build_handlers_clamps_override():
    priorities = _by_name(build_handlers({CONF_WEATHER_PRIORITY: 500}))
    assert priorities["weather"] == 99


def test_build_handlers_does_not_mutate_class_default():
    # An instance override must not leak onto the class attribute.
    build_handlers({CONF_WEATHER_PRIORITY: 12})
    assert HANDLER_PRIORITY_DEFAULTS["weather"] == 90
    assert _by_name(build_handlers({}))["weather"] == 90


# ---------------------------------------------------------------------------
# Registry re-sorting + tie-break
# ---------------------------------------------------------------------------


def test_registry_resorts_on_override():
    # Climate raised above manual_override should evaluate earlier.
    registry = PipelineRegistry(
        build_handlers({CONF_CLIMATE_PRIORITY: 85, CONF_MANUAL_OVERRIDE_PRIORITY: 80})
    )
    order = [h.name for h in registry._handlers]
    assert order.index("climate") < order.index("manual_override")


def test_tie_breaks_by_default_order():
    # climate forced to the same priority as manual_override (80). The handler
    # with the higher built-in default (manual_override) must come first.
    registry = PipelineRegistry(build_handlers({CONF_CLIMATE_PRIORITY: 80}))
    order = [h.name for h in registry._handlers]
    assert order.index("manual_override") < order.index("climate")


# ---------------------------------------------------------------------------
# build_priority_chain visualization
# ---------------------------------------------------------------------------


_CHAIN_KW = {
    "has_weather": True,
    "has_motion": True,
    "has_cloud": True,
    "has_climate": True,
    "sun_tracking_enabled": True,
    "has_glare": True,
    "supports_glare": True,
}


def test_chain_default_priorities():
    entries = {e.label: e.priority for e in build_priority_chain(**_CHAIN_KW)}
    assert entries["Weather"] == 90
    assert entries["Solar"] == 40


def test_chain_reflects_overrides():
    entries = build_priority_chain(**_CHAIN_KW, priorities={"solar": 95, "weather": 20})
    by_label = {e.label: e.priority for e in entries}
    assert by_label["Solar"] == 95
    assert by_label["Weather"] == 20
    # Solar now outranks everything → first entry.
    assert entries[0].label == "Solar"


# ---------------------------------------------------------------------------
# Option ranges + validation
# ---------------------------------------------------------------------------


def test_option_ranges_present():
    for key in HANDLER_PRIORITY_CONF.values():
        assert OPTION_RANGES[key] == (1, 99)


def test_field_validators_accept_and_reject():
    from custom_components.adaptive_cover_pro.services.options_service import (
        FIELD_VALIDATORS,
    )

    validator = FIELD_VALIDATORS[CONF_SOLAR_PRIORITY]
    assert validator(1) == 1
    assert validator(99) == 99
    for bad in (0, 100):
        with pytest.raises(vol.Invalid):
            validator(bad)
