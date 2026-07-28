"""Tests for templated threshold options (issue #577).

Covers the runtime resolver, the ``is_template_string`` predicate, the
number-or-template service validators, the ``_num_or`` setup-time guard, and an
end-to-end check that a templated lux threshold drives the climate read.
"""

import logging

import pytest
from homeassistant.core import HomeAssistant

from custom_components.adaptive_cover_pro.config_types import RuntimeConfig, _num_or
from custom_components.adaptive_cover_pro.const import (
    CONF_IRRADIANCE_THRESHOLD,
    CONF_LUX_THRESHOLD,
    CONF_MOTION_TEMPLATE,
    CONF_TEMP_EXTREME_HEAT,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_WEATHER_WIND_SPEED_THRESHOLD,
    DEFAULT_WEATHER_WIND_SPEED_THRESHOLD,
)
from custom_components.adaptive_cover_pro.services.options_service import (
    _as_number,
    validate_options_patch,
)
from custom_components.adaptive_cover_pro.state.climate_provider import ClimateProvider
from custom_components.adaptive_cover_pro.templates import (
    TemplateResolver,
    combine_with_mode,
    is_template_string,
    render_condition,
)
from homeassistant.exceptions import ServiceValidationError

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# is_template_string
# ---------------------------------------------------------------------------


class TestIsTemplateString:
    """The strict "is this actually a Jinja template" predicate."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("{{ states('sensor.x') }}", True),
            ("{% if true %}1{% endif %}", True),
            ("1000", False),
            ("abc", False),
            (1000, False),
            (1000.5, False),
            (None, False),
        ],
    )
    def test_predicate(self, value, expected):
        assert is_template_string(value) is expected


# ---------------------------------------------------------------------------
# TemplateResolver (real hass for rendering)
# ---------------------------------------------------------------------------


class TestTemplateResolver:
    """Per-cycle rendering of templated threshold options."""

    async def test_no_templatable_strings_returns_same_object(
        self, hass: HomeAssistant
    ):
        """Fast path: numeric values are passed through without copying."""
        resolver = TemplateResolver(hass)
        options = {CONF_LUX_THRESHOLD: 1000, "name": "Living Room"}
        assert resolver.resolve(options) is options

    async def test_numeric_string_renders_to_float(self, hass: HomeAssistant):
        resolver = TemplateResolver(hass)
        out = resolver.resolve({CONF_LUX_THRESHOLD: "1000"})
        assert out[CONF_LUX_THRESHOLD] == 1000.0
        assert isinstance(out[CONF_LUX_THRESHOLD], float)

    async def test_constant_template_renders(self, hass: HomeAssistant):
        resolver = TemplateResolver(hass)
        out = resolver.resolve({CONF_IRRADIANCE_THRESHOLD: "{{ 300 + 50 }}"})
        assert out[CONF_IRRADIANCE_THRESHOLD] == 350.0

    async def test_entity_template_renders(self, hass: HomeAssistant):
        hass.states.async_set("sensor.lux_limit", "1234")
        await hass.async_block_till_done()
        resolver = TemplateResolver(hass)
        out = resolver.resolve(
            {CONF_LUX_THRESHOLD: "{{ states('sensor.lux_limit') | float }}"}
        )
        assert out[CONF_LUX_THRESHOLD] == 1234.0

    async def test_seasonal_template_renders(self, hass: HomeAssistant):
        """The exact shape requested in issue #577 (season → max)."""
        hass.states.async_set("sensor.season", "summer")
        await hass.async_block_till_done()
        tmpl = (
            "{% set s = states('sensor.season') %}"
            "{% if s == 'winter' %}300{% elif s == 'summer' %}550{% else %}500{% endif %}"
        )
        resolver = TemplateResolver(hass)
        out = resolver.resolve({CONF_IRRADIANCE_THRESHOLD: tmpl})
        assert out[CONF_IRRADIANCE_THRESHOLD] == 550.0

    async def test_bad_template_drops_key(self, hass: HomeAssistant):
        """A malformed template drops the key (falls back to default), no raise."""
        resolver = TemplateResolver(hass)
        out = resolver.resolve({CONF_LUX_THRESHOLD: "{{ unclosed", "name": "x"})
        assert CONF_LUX_THRESHOLD not in out
        assert out["name"] == "x"

    async def test_non_numeric_render_drops_key(self, hass: HomeAssistant):
        resolver = TemplateResolver(hass)
        out = resolver.resolve({CONF_LUX_THRESHOLD: "{{ 'not a number' }}"})
        assert CONF_LUX_THRESHOLD not in out

    async def test_failure_then_recovery(self, hass: HomeAssistant):
        """A key that fails once resolves cleanly once the template is valid."""
        resolver = TemplateResolver(hass)
        resolver.resolve({CONF_LUX_THRESHOLD: "{{ 'bad' }}"})
        out = resolver.resolve({CONF_LUX_THRESHOLD: "{{ 900 }}"})
        assert out[CONF_LUX_THRESHOLD] == 900.0

    async def test_non_templatable_string_untouched(self, hass: HomeAssistant):
        """Only TEMPLATABLE_KEYS are resolved; other string options are left alone."""
        resolver = TemplateResolver(hass)
        out = resolver.resolve(
            {CONF_LUX_THRESHOLD: "{{ 100 }}", "name": "{{ not_resolved }}"}
        )
        assert out[CONF_LUX_THRESHOLD] == 100.0
        assert out["name"] == "{{ not_resolved }}"

    async def test_extreme_heat_threshold_template_rendered(self, hass: HomeAssistant):
        """A templated ``temp_extreme_heat`` renders to a number, mirroring temp_high (#766)."""
        resolver = TemplateResolver(hass)
        out = resolver.resolve({CONF_TEMP_EXTREME_HEAT: "{{ 30 }}"})
        assert out[CONF_TEMP_EXTREME_HEAT] == 30.0


# ---------------------------------------------------------------------------
# render_condition — boolean condition templates (motion occupancy, #577 f/u)
# ---------------------------------------------------------------------------


class TestRenderCondition:
    """The reusable boolean-condition template primitive."""

    async def test_truthy_constant(self, hass: HomeAssistant):
        assert render_condition(hass, "{{ true }}") is True

    async def test_falsy_constant(self, hass: HomeAssistant):
        assert render_condition(hass, "{{ false }}") is False

    async def test_entity_state(self, hass: HomeAssistant):
        hass.states.async_set("input_boolean.guest", "on")
        await hass.async_block_till_done()
        assert (
            render_condition(hass, "{{ is_state('input_boolean.guest', 'on') }}")
            is True
        )
        hass.states.async_set("input_boolean.guest", "off")
        await hass.async_block_till_done()
        assert (
            render_condition(hass, "{{ is_state('input_boolean.guest', 'on') }}")
            is False
        )

    @pytest.mark.parametrize("value", [None, "", "not a template", 123])
    async def test_non_template_returns_default(self, hass: HomeAssistant, value):
        assert render_condition(hass, value) is False
        assert render_condition(hass, value, default=True) is True

    async def test_render_error_returns_default(self, hass: HomeAssistant):
        # References an undefined function → render raises → default.
        assert render_condition(hass, "{{ nonexistent_fn() }}") is False


# ---------------------------------------------------------------------------
# combine_with_mode — fold a condition template into the screen's other signals
# ---------------------------------------------------------------------------


class TestCombineWithMode:
    """Generic OR/AND combination used by any template-based condition field."""

    # (template, others, mode, has_template, has_others) -> expected
    @pytest.mark.parametrize(
        "template,others,mode,has_template,has_others,expected",
        [
            # Both sources present — OR is additive.
            (True, False, "or", True, True, True),
            (False, True, "or", True, True, True),
            (False, False, "or", True, True, False),
            (True, True, "or", True, True, True),
            # Both sources present — AND requires both.
            (True, True, "and", True, True, True),
            (True, False, "and", True, True, False),
            (False, True, "and", True, True, False),
            (False, False, "and", True, True, False),
            # Template only — AND degenerates to the template alone.
            (True, False, "and", True, False, True),
            (False, False, "and", True, False, False),
            (True, False, "or", True, False, True),
            # Others only — mode irrelevant, the sensors decide.
            (False, True, "and", False, True, True),
            (False, False, "and", False, True, False),
            (False, True, "or", False, True, True),
            # Unknown mode falls back to OR.
            (True, False, "weird", True, True, True),
            (False, False, "weird", True, True, False),
        ],
    )
    def test_truth_table(
        self, template, others, mode, has_template, has_others, expected
    ):
        assert (
            combine_with_mode(
                template,
                others,
                mode,
                has_template=has_template,
                has_others=has_others,
            )
            is expected
        )


# ---------------------------------------------------------------------------
# Service validators — number or template
# ---------------------------------------------------------------------------


class TestTemplatableValidators:
    """FIELD_VALIDATORS / validate_options_patch accept numbers and templates."""

    def test_plain_number_accepted(self):
        result = validate_options_patch({CONF_LUX_THRESHOLD: 5000}, {})
        assert result[CONF_LUX_THRESHOLD] == 5000

    def test_template_accepted_unbounded_field(self):
        tmpl = "{{ states('input_number.lux') | float }}"
        result = validate_options_patch({CONF_LUX_THRESHOLD: tmpl}, {})
        assert result[CONF_LUX_THRESHOLD] == tmpl

    def test_template_accepted_bounded_field(self):
        tmpl = "{{ 21 }}"
        result = validate_options_patch({CONF_TEMP_LOW: tmpl}, {})
        assert result[CONF_TEMP_LOW] == tmpl

    def test_malformed_template_rejected(self):
        with pytest.raises(ServiceValidationError):
            validate_options_patch({CONF_LUX_THRESHOLD: "{{ unclosed"}, {})

    def test_non_numeric_non_template_rejected(self):
        with pytest.raises(ServiceValidationError):
            validate_options_patch({CONF_LUX_THRESHOLD: "abc"}, {})

    def test_out_of_range_number_rejected_for_bounded_field(self):
        with pytest.raises(ServiceValidationError):
            validate_options_patch({CONF_TEMP_LOW: 999}, {})

    def test_temp_ordering_enforced_for_numbers(self):
        with pytest.raises(ServiceValidationError, match="temp_low"):
            validate_options_patch({CONF_TEMP_LOW: 30, CONF_TEMP_HIGH: 25}, {})

    def test_temp_ordering_skipped_when_low_is_template(self):
        """A templated bound can't be compared, so the ordering check is skipped."""
        result = validate_options_patch(
            {CONF_TEMP_LOW: "{{ 30 }}", CONF_TEMP_HIGH: 25}, {}
        )
        assert result[CONF_TEMP_HIGH] == 25

    def test_temp_ordering_skipped_when_high_is_template(self):
        result = validate_options_patch(
            {CONF_TEMP_LOW: 30, CONF_TEMP_HIGH: "{{ 25 }}"}, {}
        )
        assert result[CONF_TEMP_LOW] == 30

    def test_extreme_heat_threshold_number_accepted(self):
        """A plain number is accepted and coerced (issue #766)."""
        result = validate_options_patch({CONF_TEMP_EXTREME_HEAT: 35}, {})
        assert result[CONF_TEMP_EXTREME_HEAT] == 35.0

    def test_extreme_heat_threshold_template_accepted(self):
        """A Jinja template is accepted by the templatable-num validator."""
        result = validate_options_patch({CONF_TEMP_EXTREME_HEAT: "{{ 30 }}"}, {})
        assert result[CONF_TEMP_EXTREME_HEAT] == "{{ 30 }}"

    def test_extreme_heat_threshold_out_of_range_rejected(self):
        with pytest.raises(ServiceValidationError):
            validate_options_patch({CONF_TEMP_EXTREME_HEAT: 999}, {})

    def test_condition_template_accepted(self):
        tmpl = "{{ is_state('input_boolean.guest', 'on') }}"
        result = validate_options_patch({CONF_MOTION_TEMPLATE: tmpl}, {})
        assert result[CONF_MOTION_TEMPLATE] == tmpl

    def test_condition_template_empty_accepted(self):
        # Empty is accepted (no raise); treated as no-template at runtime.
        result = validate_options_patch({CONF_MOTION_TEMPLATE: ""}, {})
        assert result[CONF_MOTION_TEMPLATE] == ""

    def test_condition_template_malformed_rejected(self):
        with pytest.raises(ServiceValidationError):
            validate_options_patch({CONF_MOTION_TEMPLATE: "{{ unclosed"}, {})

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (21, 21.0),
            ("21", 21.0),
            ("{{ 30 }}", None),  # template → unresolvable here
            ("garbage", None),  # non-numeric → skip comparison
            (None, None),
        ],
    )
    def test_as_number_coercion(self, value, expected):
        assert _as_number(value) == expected


# ---------------------------------------------------------------------------
# Setup-time robustness — _num_or and from_options
# ---------------------------------------------------------------------------


class TestNumOr:
    """The setup-time numeric coercion guard."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (50.0, 50.0),
            (17, 17.0),
            ("17", 17.0),
            ("{{ states('x') }}", 42.0),
            ("abc", 42.0),
            (None, 42.0),
        ],
    )
    def test_coercion(self, value, expected):
        assert _num_or(value, 42.0) == expected

    def test_from_options_tolerates_template_weather_threshold(self):
        """An unresolved template in a weather threshold falls back to the default."""
        rc = RuntimeConfig.from_options(
            {CONF_WEATHER_WIND_SPEED_THRESHOLD: "{{ states('input_number.wind') }}"}
        )
        assert rc.weather.wind_speed_threshold == DEFAULT_WEATHER_WIND_SPEED_THRESHOLD


# ---------------------------------------------------------------------------
# Diagnostics — raw template + resolved value surfaced
# ---------------------------------------------------------------------------


class TestDiagnosticsSurfacing:
    """The configuration diagnostics map templated thresholds raw → resolved."""

    def _ctx(self, config_options, resolved_options):
        from custom_components.adaptive_cover_pro.diagnostics.builder import (
            DiagnosticContext,
        )

        return DiagnosticContext(
            pos_sun=[180.0, 45.0],
            cover=None,
            pipeline_result=None,
            climate_mode=False,
            check_adaptive_time=True,
            after_start_time=True,
            before_end_time=True,
            start_time=None,
            end_time=None,
            automatic_control=True,
            config_options=config_options,
            resolved_options=resolved_options,
        )

    def test_templated_field_surfaced_with_resolved_value(self):
        from custom_components.adaptive_cover_pro.diagnostics.builder import (
            DiagnosticsBuilder,
        )

        tmpl = "{{ states('input_number.lux') | float }}"
        ctx = self._ctx(
            {CONF_LUX_THRESHOLD: tmpl, CONF_TEMP_LOW: 21},
            {CONF_LUX_THRESHOLD: 950.0, CONF_TEMP_LOW: 21},
        )
        config = DiagnosticsBuilder._build_configuration(ctx)["configuration"]
        tt = config["templated_thresholds"]
        assert tt == {CONF_LUX_THRESHOLD: {"template": tmpl, "resolved": 950.0}}
        # A plain-number field is not listed.
        assert CONF_TEMP_LOW not in tt

    def test_no_templates_yields_empty_map(self):
        from custom_components.adaptive_cover_pro.diagnostics.builder import (
            DiagnosticsBuilder,
        )

        ctx = self._ctx({CONF_LUX_THRESHOLD: 1000}, {CONF_LUX_THRESHOLD: 1000})
        config = DiagnosticsBuilder._build_configuration(ctx)["configuration"]
        assert config["templated_thresholds"] == {}


# ---------------------------------------------------------------------------
# End-to-end — templated threshold drives the climate read
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """Resolved templated threshold flows into the climate read."""

    async def test_templated_lux_threshold_drives_suppression(
        self, hass: HomeAssistant
    ):
        hass.states.async_set("sensor.lux", "500")
        hass.states.async_set("input_number.lux_limit", "1000")
        await hass.async_block_till_done()

        resolver = TemplateResolver(hass)
        options = {CONF_LUX_THRESHOLD: "{{ states('input_number.lux_limit') | float }}"}
        resolved = resolver.resolve(options)
        assert resolved[CONF_LUX_THRESHOLD] == 1000.0

        provider = ClimateProvider(hass=hass, logger=_LOGGER)
        readings = provider.read(
            use_lux=True,
            lux_entity="sensor.lux",
            lux_threshold=resolved[CONF_LUX_THRESHOLD],
        )
        # 500 lux <= 1000 threshold → sun considered absent (suppression fires).
        assert readings.lux_below_threshold is True
