"""Tests for templated threshold options (issue #577).

Covers the runtime resolver, the ``is_template_string`` predicate, the
number-or-template service validators, the ``_num_or`` setup-time guard, the
``acp`` self-reference namespace (issue #1159), and an end-to-end check that a
templated lux threshold drives the climate read.
"""

import logging

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from jinja2.exceptions import UndefinedError

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
    ACP_TEMPLATE_ENTITY_KEYS,
    ACP_TEMPLATE_KEY_ALIASES,
    TemplateResolver,
    build_acp_template_variables,
    combine_with_mode,
    fold_condition_template,
    is_template_string,
    render_condition,
    render_condition_or_none,
    uses_acp_namespace,
)
from homeassistant.exceptions import ServiceValidationError, TemplateError
from tests._helpers.acp_namespace import make_acp_entry, seed_acp_row

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

    @pytest.mark.parametrize(
        ("state", "expected"),
        [("on", 80.0), ("off", 30.0)],
    )
    async def test_acp_state_drives_a_numeric_threshold(
        self, hass: HomeAssistant, state, expected
    ):
        """Numeric renders get all three forms, including the value form (#1159)."""
        entry = make_acp_entry(hass, f"acp_num_{state}")
        entity_id = seed_acp_row(
            hass, entry, "binary_sensor", "sun_motion", f"shade_{state}_sun_infront"
        )
        hass.states.async_set(entity_id, state)
        await hass.async_block_till_done()

        resolver = TemplateResolver(
            hass,
            variables=build_acp_template_variables(
                hass, entry.entry_id, include_state=True
            ),
        )
        out = resolver.resolve(
            {CONF_LUX_THRESHOLD: "{{ 80 if acp_state.sun_infront == 'on' else 30 }}"}
        )
        assert out[CONF_LUX_THRESHOLD] == expected

    async def test_acp_entity_form_drives_a_numeric_threshold(
        self, hass: HomeAssistant
    ):
        """The entity_id forms work in numeric renders too, not just conditions."""
        entry = make_acp_entry(hass, "acp_num_entity_form")
        entity_id = seed_acp_row(
            hass, entry, "switch", "sun_tracking", "shade_sun_tracking"
        )
        hass.states.async_set(entity_id, "on")
        await hass.async_block_till_done()

        resolver = TemplateResolver(
            hass,
            variables=build_acp_template_variables(
                hass, entry.entry_id, include_state=True
            ),
        )
        out = resolver.resolve(
            {
                CONF_LUX_THRESHOLD: (
                    "{{ 900 if is_state(acp.sun_tracking, 'on') else 100 }}"
                )
            }
        )
        assert out[CONF_LUX_THRESHOLD] == 900.0

    async def test_acp_unknown_key_drops_key_and_warns_once(
        self, hass: HomeAssistant, caplog
    ):
        """Unresolvable key → field default, warned once per failure transition."""
        entry = make_acp_entry(hass, "acp_num_bad")
        resolver = TemplateResolver(
            hass,
            variables=build_acp_template_variables(
                hass, entry.entry_id, include_state=True
            ),
        )
        options = {CONF_LUX_THRESHOLD: "{{ acp_state.no_such_key }}", "name": "x"}
        with caplog.at_level(logging.WARNING):
            out = resolver.resolve(options)
        assert CONF_LUX_THRESHOLD not in out
        assert out["name"] == "x"
        assert caplog.text.count("failed to render to a number") == 1

        caplog.clear()
        with caplog.at_level(logging.WARNING):
            resolver.resolve(options)
        assert "failed to render to a number" not in caplog.text


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

    async def test_acp_namespace_variables_resolve(self, hass: HomeAssistant):
        """A condition template can name this instance's own binary sensor (#1159)."""
        entry = make_acp_entry(hass, "acp_cond_ok")
        entity_id = seed_acp_row(
            hass, entry, "binary_sensor", "sun_motion", "dining_room_shade_sun_infront"
        )
        hass.states.async_set(entity_id, "on")
        await hass.async_block_till_done()

        ctx = build_acp_template_variables(hass, entry.entry_id)
        assert (
            render_condition(
                hass, "{{ is_state(acp.sun_infront, 'on') }}", variables=ctx
            )
            is True
        )
        assert (
            render_condition(
                hass,
                "{{ is_state(acp_entity('sun_infront'), 'on') }}",
                variables=ctx,
            )
            is True
        )
        hass.states.async_set(entity_id, "off")
        await hass.async_block_till_done()
        assert (
            render_condition(
                hass, "{{ is_state(acp.sun_infront, 'on') }}", variables=ctx
            )
            is False
        )

    async def test_acp_unknown_key_falls_back_to_default(self, hass: HomeAssistant):
        """UndefinedError is a TemplateError → the existing fail-soft path runs."""
        entry = make_acp_entry(hass, "acp_cond_bad")
        ctx = build_acp_template_variables(hass, entry.entry_id)
        tmpl = "{{ is_state(acp.no_such_key, 'on') }}"
        assert render_condition(hass, tmpl, variables=ctx) is False
        assert render_condition(hass, tmpl, default=True, variables=ctx) is True

    async def test_acp_or_none_unknown_key_has_no_opinion(self, hass: HomeAssistant):
        entry = make_acp_entry(hass, "acp_cond_none")
        ctx = build_acp_template_variables(hass, entry.entry_id)
        assert (
            render_condition_or_none(
                hass, "{{ is_state(acp.no_such_key, 'on') }}", variables=ctx
            )
            is None
        )

    async def test_acp_or_none_renders_through(self, hass: HomeAssistant):
        entry = make_acp_entry(hass, "acp_cond_or_none")
        entity_id = seed_acp_row(
            hass, entry, "switch", "automatic_control", "shade_automatic_control"
        )
        hass.states.async_set(entity_id, "off")
        await hass.async_block_till_done()
        ctx = build_acp_template_variables(hass, entry.entry_id)
        assert (
            render_condition_or_none(
                hass, "{{ is_state(acp.automatic_control, 'on') }}", variables=ctx
            )
            is False
        )

    async def test_fold_condition_template_passes_variables_through(
        self, hass: HomeAssistant
    ):
        entry = make_acp_entry(hass, "acp_cond_fold")
        entity_id = seed_acp_row(
            hass, entry, "binary_sensor", "manual_override", "shade_manual_override"
        )
        hass.states.async_set(entity_id, "on")
        await hass.async_block_till_done()
        ctx = build_acp_template_variables(hass, entry.entry_id)
        assert (
            fold_condition_template(
                hass,
                "{{ is_state(acp.manual_override, 'on') }}",
                "or",
                others_truthy=False,
                has_others=False,
                variables=ctx,
            )
            is True
        )


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


# ---------------------------------------------------------------------------
# acp self-reference namespace (issue #1159)
# ---------------------------------------------------------------------------


class TestAcpNamespace:
    """The ``acp`` / ``acp_entity`` / ``acp_state`` self-reference namespace."""

    async def test_canonical_key_resolves_to_entity_id(self, hass: HomeAssistant):
        entry = make_acp_entry(hass, "acp_ns_01")
        expected = seed_acp_row(
            hass, entry, "binary_sensor", "sun_motion", "dining_room_shade_sun_infront"
        )
        ctx = build_acp_template_variables(hass, entry.entry_id)
        assert ctx["acp"].sun_motion == expected

    async def test_alias_resolves_to_the_same_entity_id(self, hass: HomeAssistant):
        """The maintainer's own ``sun_infront`` spelling must work (#1159)."""
        entry = make_acp_entry(hass, "acp_ns_alias")
        expected = seed_acp_row(
            hass, entry, "binary_sensor", "sun_motion", "dining_room_shade_sun_infront"
        )
        ctx = build_acp_template_variables(hass, entry.entry_id)
        assert ctx["acp"].sun_infront == expected
        assert ctx["acp"]["sun_infront"] == expected

    async def test_acp_entity_callable_matches_attribute_form(
        self, hass: HomeAssistant
    ):
        entry = make_acp_entry(hass, "acp_ns_callable")
        expected = seed_acp_row(
            hass, entry, "switch", "enabled_toggle", "dining_room_shade_integration"
        )
        ctx = build_acp_template_variables(hass, entry.entry_id)
        assert ctx["acp_entity"]("enabled_toggle") == expected
        assert ctx["acp_entity"]("integration_enabled") == expected
        assert ctx["acp_entity"]("enabled_toggle") == ctx["acp"].enabled_toggle

    async def test_unknown_key_raises_undefined_error(self, hass: HomeAssistant):
        entry = make_acp_entry(hass, "acp_ns_unknown")
        ctx = build_acp_template_variables(hass, entry.entry_id)
        with pytest.raises(UndefinedError) as err:
            ctx["acp"].not_a_real_key
        assert "not_a_real_key" in str(err.value)
        assert "Vertical Blind Dining Room Shade" in str(err.value)

    async def test_known_key_without_an_entity_raises_undefined_error(
        self, hass: HomeAssistant
    ):
        """``glare_active`` only exists when glare zones are on — absent → error."""
        entry = make_acp_entry(hass, "acp_ns_absent")
        seed_acp_row(
            hass, entry, "binary_sensor", "sun_motion", "dining_room_shade_sun_infront"
        )
        ctx = build_acp_template_variables(hass, entry.entry_id)
        with pytest.raises(UndefinedError) as err:
            ctx["acp"].glare_active
        assert "glare_active" in str(err.value)
        assert "Vertical Blind Dining Room Shade" in str(err.value)

    async def test_rename_is_picked_up_on_the_next_access(self, hass: HomeAssistant):
        """No caching: a mid-run entity rename resolves on the very next read."""
        entry = make_acp_entry(hass, "acp_ns_rename")
        original = seed_acp_row(
            hass, entry, "binary_sensor", "sun_motion", "dining_room_shade_sun_infront"
        )
        ctx = build_acp_template_variables(hass, entry.entry_id)
        assert ctx["acp"].sun_infront == original

        reg = er.async_get(hass)
        reg.async_update_entity(original, new_entity_id="binary_sensor.renamed_by_user")
        await hass.async_block_till_done()

        assert ctx["acp"].sun_infront == "binary_sensor.renamed_by_user"

    async def test_acp_state_returns_the_state_string(self, hass: HomeAssistant):
        entry = make_acp_entry(hass, "acp_ns_state")
        entity_id = seed_acp_row(
            hass, entry, "binary_sensor", "sun_motion", "dining_room_shade_sun_infront"
        )
        hass.states.async_set(entity_id, "on")
        await hass.async_block_till_done()

        ctx = build_acp_template_variables(hass, entry.entry_id, include_state=True)
        assert ctx["acp_state"].sun_infront == "on"

    async def test_acp_state_is_unknown_when_the_entity_has_no_state(
        self, hass: HomeAssistant
    ):
        entry = make_acp_entry(hass, "acp_ns_stateless")
        seed_acp_row(
            hass, entry, "binary_sensor", "sun_motion", "dining_room_shade_sun_infront"
        )
        ctx = build_acp_template_variables(hass, entry.entry_id, include_state=True)
        assert ctx["acp_state"].sun_infront == "unknown"

    @pytest.mark.parametrize("namespace", ["acp", "acp_state"])
    async def test_private_probes_look_like_ordinary_missing_attributes(
        self, hass: HomeAssistant, namespace
    ):
        """The Jinja sandbox probes ``unsafe_callable``/``alters_data`` style names.

        Those, and copy/pickle's dunder lookups, must raise ``AttributeError`` so
        ``getattr(obj, name, default)`` returns the default — an ``UndefinedError``
        there would escape from inside HA's sandbox checks.
        """
        entry = make_acp_entry(hass, f"acp_ns_probe_{namespace}")
        ctx = build_acp_template_variables(hass, entry.entry_id, include_state=True)
        ns = ctx[namespace]
        with pytest.raises(AttributeError):
            ns._not_a_public_key
        assert getattr(ns, "__deepcopy__", "fallback") == "fallback"

    @pytest.mark.parametrize("namespace", ["acp", "acp_state"])
    @pytest.mark.parametrize("probe", ["jinja_pass_arg", "unsafe_callable"])
    async def test_capability_probes_take_their_default(
        self, hass: HomeAssistant, namespace, probe
    ):
        """Jinja/HA capability probes must not be answered as bad entity keys.

        ``jinja2.utils._PassArg.from_obj`` does ``hasattr(obj, "jinja_pass_arg")``
        and ``SandboxedEnvironment.is_safe_callable`` does
        ``getattr(obj, "unsafe_callable", False)`` — both on the way to calling
        an object. Neither name starts with an underscore, so both used to reach
        the resolver and be reported to the user as a key they never wrote.
        """
        entry = make_acp_entry(hass, f"acp_ns_cap_{namespace}_{probe}")
        ctx = build_acp_template_variables(hass, entry.entry_id, include_state=True)
        ns = ctx[namespace]
        assert getattr(ns, probe, "fallback") == "fallback"
        assert hasattr(ns, probe) is False

    async def test_unknown_key_message_survives_the_attribute_error_base(
        self, hass: HomeAssistant
    ):
        """A missing key still raises ``UndefinedError`` with the same message.

        The probe fix gives attribute-access failures an ``AttributeError`` base;
        this pins that it did not weaken the key error itself, which several
        tests and the wiki's failure-mode section describe verbatim.
        """
        entry = make_acp_entry(hass, "acp_ns_dual_base")
        ctx = build_acp_template_variables(hass, entry.entry_id)
        with pytest.raises(UndefinedError) as err:
            ctx["acp"].nope
        assert str(err.value) == (
            "'nope' is not an Adaptive Cover Pro entity key "
            "(instance Vertical Blind Dining Room Shade)"
        )
        with pytest.raises(AttributeError):
            ctx["acp"].nope

    @pytest.mark.parametrize(
        ("template_str", "expected_fragment"),
        [
            ("{{ acp('sun_infront') }}", "'acp' namespace is not callable"),
            ("{{ acp_state('sun_infront') }}", "'acp_state' namespace is not callable"),
            (
                "{% for k in acp %}{{ k }}{% endfor %}",
                "'acp' namespace is not iterable",
            ),
            ("{{ 'sun_infront' in acp }}", "'acp' namespace is not iterable"),
            ("{{ acp | list }}", "'acp' namespace is not iterable"),
        ],
    )
    async def test_namespace_misuse_names_the_real_problem(
        self, hass: HomeAssistant, template_str, expected_fragment
    ):
        """Calling or iterating the namespace must not be reported as a bad key.

        Before this, ``{{ acp('x') }}`` failed with ``'jinja_pass_arg' is not an
        Adaptive Cover Pro entity key`` and both iteration spellings failed with
        ``'0' is not …`` — Python's legacy ``__getitem__(0)`` iteration protocol.
        Every one of these is fail-soft either way; the fix is the message.
        """
        from homeassistant.helpers.template import Template

        entry = make_acp_entry(hass, "acp_ns_misuse")
        seed_acp_row(
            hass, entry, "binary_sensor", "sun_motion", "dining_room_shade_sun_infront"
        )
        ctx = build_acp_template_variables(hass, entry.entry_id, include_state=True)
        with pytest.raises(TemplateError) as err:
            Template(template_str, hass).async_render(ctx)
        assert expected_fragment in str(err.value)

    async def test_bracket_form_still_reports_an_unknown_key(self, hass: HomeAssistant):
        """``acp['nope']`` keeps the key error rather than degrading to undefined.

        ``SandboxedEnvironment.getitem`` swallows ``AttributeError``, so item
        access must keep raising a plain ``UndefinedError`` — otherwise the
        bracket spelling the wiki documents would silently render nothing.
        """
        from homeassistant.helpers.template import Template

        entry = make_acp_entry(hass, "acp_ns_bracket_miss")
        ctx = build_acp_template_variables(hass, entry.entry_id)
        with pytest.raises(TemplateError) as err:
            Template("{{ acp['nope'] }}", hass).async_render(ctx)
        assert "'nope' is not an Adaptive Cover Pro entity key" in str(err.value)

    async def test_state_namespace_is_withheld_unless_requested(
        self, hass: HomeAssistant
    ):
        """Tracked/condition fields get the entity forms only — never ``acp_state``."""
        entry = make_acp_entry(hass, "acp_ns_withheld")
        ctx = build_acp_template_variables(hass, entry.entry_id)
        assert set(ctx) == {"acp", "acp_entity"}
        with_state = build_acp_template_variables(
            hass, entry.entry_id, include_state=True
        )
        assert set(with_state) == {"acp", "acp_entity", "acp_state"}

    async def test_another_entrys_entities_are_not_visible(self, hass: HomeAssistant):
        """The namespace is per-instance: a sibling entry's row must not leak in."""
        mine = make_acp_entry(hass, "acp_ns_mine")
        theirs = make_acp_entry(hass, "acp_ns_theirs")
        seed_acp_row(
            hass, theirs, "binary_sensor", "sun_motion", "other_shade_sun_infront"
        )
        ctx = build_acp_template_variables(hass, mine.entry_id)
        with pytest.raises(UndefinedError):
            ctx["acp"].sun_infront

    @pytest.mark.parametrize(
        ("template_str", "expected"),
        [
            ("{{ acp.sun_infront }}", True),
            ("{{ is_state(acp_entity('sun_infront'), 'on') }}", True),
            ("{{ acp_state.sun_infront == 'on' }}", True),
            ("{{ states('sensor.acp_foo') }}", False),
            ("{{ states('sensor.lux') | int > 100 }}", False),
            ("", False),
            (None, False),
        ],
    )
    def test_uses_acp_namespace(self, template_str, expected):
        assert uses_acp_namespace(template_str) is expected

    def test_every_alias_targets_a_real_key(self):
        for alias, canonical in ACP_TEMPLATE_KEY_ALIASES.items():
            assert (
                canonical in ACP_TEMPLATE_ENTITY_KEYS
            ), f"alias {alias!r} points at unknown key {canonical!r}"
            assert (
                alias not in ACP_TEMPLATE_ENTITY_KEYS
            ), f"alias {alias!r} shadows a canonical key"

    def test_key_map_records_its_own_translation_key(self):
        """Canonical key == translation_key, on a domain some platform serves.

        Only the map's internal shape — deliberately, because it is checked
        against the platforms themselves in
        ``test_spec_translation_keys.TestAcpNamespaceKeys``. That is the canary
        that catches a key renamed in ``switch.py`` / ``sensor.py`` /
        ``binary_sensor.py``; this one only keeps the map's two columns from
        drifting apart.
        """
        for key, (domain, translation_key) in ACP_TEMPLATE_ENTITY_KEYS.items():
            assert translation_key == key
            assert domain in ("binary_sensor", "switch", "sensor")
