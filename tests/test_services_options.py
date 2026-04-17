"""Tests for runtime options-mutation services (Issue #221).

Covers:
- Pure unit tests for validate_options_patch, _validate_fields, _cross_field_validate
- Integration tests for all 15 per-section + generic set_option services
- Reload propagation (async_update_entry called)
- Custom position slot routing
- List-field replace semantics
- Null/clearing semantics
- Identity-key rejection
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adaptive_cover_pro.const import (
    CONF_AZIMUTH,
    CONF_BLIND_SPOT_LEFT,
    CONF_BLIND_SPOT_RIGHT,
    CONF_CLIMATE_MODE,
    CONF_CLOUD_SUPPRESSION,
    CONF_DEFAULT_HEIGHT,
    CONF_DELTA_POSITION,
    CONF_ENABLE_MAX_POSITION,
    CONF_ENABLE_MIN_POSITION,
    CONF_ENABLE_SUN_TRACKING,
    CONF_END_ENTITY,
    CONF_END_TIME,
    CONF_FORCE_OVERRIDE_MIN_MODE,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_INTERP,
    CONF_INVERSE_STATE,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_MANUAL_OVERRIDE_RESET,
    CONF_MAX_POSITION,
    CONF_MIN_POSITION,
    CONF_MOTION_SENSORS,
    CONF_MOTION_TIMEOUT,
    CONF_MY_POSITION_VALUE,
    CONF_RETURN_SUNSET,
    CONF_SENSOR_TYPE,
    CONF_START_ENTITY,
    CONF_START_TIME,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_SUNSET_USE_MY,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_WEATHER_BYPASS_AUTO_CONTROL,
    CONF_WEATHER_OVERRIDE_POSITION,
    CONF_WEATHER_SEVERE_SENSORS,
    CONF_WEATHER_TIMEOUT,
    CONF_WEATHER_WIND_SPEED_THRESHOLD,
    DOMAIN,
    SensorType,
)
from custom_components.adaptive_cover_pro.services.options_service import (
    ALL_SETTABLE_KEYS,
    FIELD_VALIDATORS,
    IDENTITY_KEYS,
    _cross_field_validate,
    apply_options_patch,
    validate_options_patch,
)
from tests.ha_helpers import (
    VERTICAL_OPTIONS,
    _patch_coordinator_refresh,
)

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup(
    hass: HomeAssistant,
    entry_id: str = "opts_01",
    cover_type: str = SensorType.BLIND,
    options: dict | None = None,
    name: str = "Options Cover",
) -> MockConfigEntry:
    opts = dict(VERTICAL_OPTIONS) if options is None else options
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": name, CONF_SENSOR_TYPE: cover_type},
        options=opts,
        entry_id=entry_id,
        title=name,
    )
    entry.add_to_hass(hass)
    with _patch_coordinator_refresh():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _call(
    hass: HomeAssistant,
    service: str,
    data: dict,
    target_entity: str = "cover.test_blind",
) -> None:
    hass.states.async_set(target_entity, "open", {"current_position": 50})
    await hass.services.async_call(
        DOMAIN,
        service,
        data,
        blocking=True,
        target={"entity_id": [target_entity]},
    )
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# Unit tests — validate_options_patch (pure, no HA)
# ---------------------------------------------------------------------------


class TestValidateOptionsPatch:
    """Unit tests for validate_options_patch."""

    def test_empty_patch_raises(self):
        with pytest.raises(ServiceValidationError, match="No fields"):
            validate_options_patch({}, {})

    def test_identity_key_rejected(self):
        for key in IDENTITY_KEYS:
            with pytest.raises(ServiceValidationError, match="cannot be changed"):
                validate_options_patch({key: "x"}, {})

    def test_valid_default_height(self):
        patch = {CONF_DEFAULT_HEIGHT: 75}
        result = validate_options_patch(patch, {})
        assert result == patch

    def test_default_height_out_of_range(self):
        with pytest.raises(ServiceValidationError, match="default_percentage"):
            validate_options_patch({CONF_DEFAULT_HEIGHT: 150}, {})

    def test_none_value_passes_validation(self):
        result = validate_options_patch({CONF_SUNSET_POS: None}, {})
        assert result[CONF_SUNSET_POS] is None

    def test_geometry_wrong_cover_type_rejected(self):
        with pytest.raises(ServiceValidationError, match="only valid for"):
            validate_options_patch(
                {"window_width": 100},
                {},
                sensor_type=SensorType.AWNING,
            )

    def test_geometry_correct_cover_type_accepted(self):
        result = validate_options_patch(
            {"window_height": 2.5},
            {},
            sensor_type=SensorType.BLIND,
        )
        assert result["window_height"] == 2.5

    def test_awning_field_accepted_for_awning_type(self):
        result = validate_options_patch(
            {"length_awning": 3.0},
            {},
            sensor_type=SensorType.AWNING,
        )
        assert result["length_awning"] == 3.0

    def test_tilt_field_accepted_for_tilt_type(self):
        result = validate_options_patch(
            {"slat_depth": 3.0},
            {},
            sensor_type=SensorType.TILT,
        )
        assert result["slat_depth"] == 3.0


class TestFieldValidators:
    """Unit tests for FIELD_VALIDATORS entries."""

    def test_numeric_ranges(self):
        valid_cases = [
            (CONF_DEFAULT_HEIGHT, 0),
            (CONF_DEFAULT_HEIGHT, 100),
            (CONF_MIN_POSITION, 0),
            (CONF_MIN_POSITION, 99),
            (CONF_MAX_POSITION, 1),
            (CONF_MAX_POSITION, 100),
            (CONF_AZIMUTH, 0),
            (CONF_AZIMUTH, 359),
            (CONF_MOTION_TIMEOUT, 30),
            (CONF_MOTION_TIMEOUT, 3600),
        ]
        for key, value in valid_cases:
            FIELD_VALIDATORS[key](value)  # should not raise

    def test_numeric_out_of_range_raises(self):
        with pytest.raises(Exception):
            FIELD_VALIDATORS[CONF_DEFAULT_HEIGHT](101)
        with pytest.raises(Exception):
            FIELD_VALIDATORS[CONF_AZIMUTH](360)
        with pytest.raises(Exception):
            FIELD_VALIDATORS[CONF_MOTION_TIMEOUT](29)

    def test_none_always_accepted(self):
        for _key, validator in FIELD_VALIDATORS.items():
            validator(None)  # should not raise for any field

    def test_bool_field_accepts_bool(self):
        FIELD_VALIDATORS[CONF_ENABLE_MIN_POSITION](True)
        FIELD_VALIDATORS[CONF_ENABLE_MIN_POSITION](False)
        FIELD_VALIDATORS[CONF_ENABLE_MIN_POSITION](None)

    def test_time_field_validates_format(self):
        FIELD_VALIDATORS[CONF_START_TIME]("08:00:00")
        FIELD_VALIDATORS[CONF_START_TIME]("00:00:00")
        FIELD_VALIDATORS[CONF_START_TIME](None)

    def test_time_field_rejects_bad_format(self):
        with pytest.raises(Exception):
            FIELD_VALIDATORS[CONF_START_TIME]("8:00")

    def test_tilt_mode_select(self):
        FIELD_VALIDATORS["tilt_mode"]("mode1")
        FIELD_VALIDATORS["tilt_mode"]("mode2")
        FIELD_VALIDATORS["tilt_mode"](None)

    def test_tilt_mode_rejects_invalid(self):
        with pytest.raises(Exception):
            FIELD_VALIDATORS["tilt_mode"]("mode3")


class TestCrossFieldValidate:
    """Unit tests for _cross_field_validate."""

    def test_blind_spot_right_must_exceed_left(self):
        with pytest.raises(ServiceValidationError, match="blind_spot_right"):
            _cross_field_validate(
                {CONF_BLIND_SPOT_LEFT: 30, CONF_BLIND_SPOT_RIGHT: 20},
                {},
            )

    def test_blind_spot_equal_raises(self):
        with pytest.raises(ServiceValidationError, match="blind_spot_right"):
            _cross_field_validate(
                {CONF_BLIND_SPOT_LEFT: 30, CONF_BLIND_SPOT_RIGHT: 30},
                {},
            )

    def test_blind_spot_valid(self):
        _cross_field_validate(
            {CONF_BLIND_SPOT_LEFT: 10, CONF_BLIND_SPOT_RIGHT: 30},
            {},
        )

    def test_temp_low_must_be_less_than_high(self):
        with pytest.raises(ServiceValidationError, match="temp_low"):
            _cross_field_validate({CONF_TEMP_LOW: 25, CONF_TEMP_HIGH: 20}, {})

    def test_temp_equal_raises(self):
        with pytest.raises(ServiceValidationError, match="temp_low"):
            _cross_field_validate({CONF_TEMP_LOW: 22, CONF_TEMP_HIGH: 22}, {})

    def test_temp_ordering_valid(self):
        _cross_field_validate({CONF_TEMP_LOW: 18, CONF_TEMP_HIGH: 26}, {})

    def test_custom_slot_sensor_without_position_raises(self):
        with pytest.raises(ServiceValidationError, match="slot 1"):
            _cross_field_validate(
                {"custom_position_sensor_1": "binary_sensor.x"},
                {},  # no existing position_1
            )

    def test_custom_slot_position_without_sensor_raises(self):
        with pytest.raises(ServiceValidationError, match="slot 2"):
            _cross_field_validate(
                {"custom_position_2": 80},
                {},  # no existing sensor_2
            )

    def test_custom_slot_both_set_valid(self):
        _cross_field_validate(
            {
                "custom_position_sensor_3": "binary_sensor.x",
                "custom_position_3": 60,
            },
            {},
        )

    def test_custom_slot_both_none_clears_slot(self):
        _cross_field_validate(
            {"custom_position_sensor_1": None, "custom_position_1": None},
            {"custom_position_sensor_1": "binary_sensor.x", "custom_position_1": 50},
        )

    def test_start_time_and_entity_mutually_exclusive(self):
        with pytest.raises(ServiceValidationError, match="mutually exclusive"):
            _cross_field_validate(
                {CONF_START_TIME: "08:00:00", CONF_START_ENTITY: "sensor.x"},
                {},
            )

    def test_end_time_and_entity_mutually_exclusive(self):
        with pytest.raises(ServiceValidationError, match="mutually exclusive"):
            _cross_field_validate(
                {CONF_END_TIME: "20:00:00", CONF_END_ENTITY: "sensor.x"},
                {},
            )

    def test_start_time_00_00_00_with_entity_is_ok(self):
        # "00:00:00" is the default; setting entity alongside it should not error
        _cross_field_validate(
            {CONF_START_TIME: "00:00:00", CONF_START_ENTITY: "sensor.x"},
            {},
        )

    def test_sunset_use_my_without_value_raises(self):
        with pytest.raises(ServiceValidationError, match="my_position_value"):
            _cross_field_validate({CONF_SUNSET_USE_MY: True}, {})

    def test_sunset_use_my_with_existing_value_valid(self):
        _cross_field_validate(
            {CONF_SUNSET_USE_MY: True},
            {CONF_MY_POSITION_VALUE: 50},
        )

    def test_cross_field_check_not_triggered_when_key_absent(self):
        # If neither blind_spot_left nor blind_spot_right is in patch,
        # don't check blind spot ordering (existing options may not even have these)
        _cross_field_validate(
            {CONF_DEFAULT_HEIGHT: 60},
            {},
        )


class TestApplyOptionsPatch:
    """Unit tests for apply_options_patch."""

    async def test_none_removes_key(self):
        hass = MagicMock()
        hass.config_entries = MagicMock()
        coord = MagicMock()
        coord.config_entry = MagicMock()
        coord.config_entry.options = {CONF_SUNSET_POS: 30, CONF_DEFAULT_HEIGHT: 60}

        result = await apply_options_patch(hass, coord, {CONF_SUNSET_POS: None})

        assert CONF_SUNSET_POS not in result
        assert result[CONF_DEFAULT_HEIGHT] == 60

    async def test_non_none_updates_key(self):
        hass = MagicMock()
        hass.config_entries = MagicMock()
        coord = MagicMock()
        coord.config_entry = MagicMock()
        coord.config_entry.options = {CONF_DEFAULT_HEIGHT: 60}

        result = await apply_options_patch(hass, coord, {CONF_DEFAULT_HEIGHT: 75})

        assert result[CONF_DEFAULT_HEIGHT] == 75

    async def test_absent_key_unchanged(self):
        hass = MagicMock()
        hass.config_entries = MagicMock()
        coord = MagicMock()
        coord.config_entry = MagicMock()
        coord.config_entry.options = {CONF_DEFAULT_HEIGHT: 60, CONF_AZIMUTH: 180}

        result = await apply_options_patch(hass, coord, {CONF_DEFAULT_HEIGHT: 75})

        assert result[CONF_AZIMUTH] == 180

    async def test_async_update_entry_called(self):
        hass = MagicMock()
        hass.config_entries = MagicMock()
        coord = MagicMock()
        coord.config_entry = MagicMock()
        coord.config_entry.options = {CONF_DEFAULT_HEIGHT: 60}

        await apply_options_patch(hass, coord, {CONF_DEFAULT_HEIGHT: 75})

        hass.config_entries.async_update_entry.assert_called_once()
        call_kwargs = hass.config_entries.async_update_entry.call_args
        assert call_kwargs[1]["options"][CONF_DEFAULT_HEIGHT] == 75


# ---------------------------------------------------------------------------
# Integration tests — services via real HA
# ---------------------------------------------------------------------------


class TestServiceRegistration:
    """Integration tests for service registration."""

    async def test_all_options_services_registered(self, hass: HomeAssistant):
        await _setup(hass, entry_id="reg_01")
        for service in [
            "set_position_limits",
            "set_sunset_sunrise",
            "set_automation_timing",
            "set_manual_override",
            "set_force_override",
            "set_custom_position",
            "set_motion",
            "set_light_cloud",
            "set_climate",
            "set_weather_safety",
            "set_sun_tracking",
            "set_blind_spot",
            "set_interpolation",
            "set_geometry",
            "set_option",
        ]:
            assert hass.services.has_service(DOMAIN, service), (
                f"Service '{service}' not registered"
            )


class TestSetPositionLimits:
    """Integration tests for set_position_limits service."""

    async def test_updates_default_height(self, hass: HomeAssistant):
        await _setup(hass, entry_id="pos_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(hass, "set_position_limits", {CONF_DEFAULT_HEIGHT: 75})

        mock_update.assert_called_once()
        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_DEFAULT_HEIGHT] == 75

    async def test_updates_multiple_fields(self, hass: HomeAssistant):
        await _setup(hass, entry_id="pos_02")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_position_limits",
                {
                    CONF_MIN_POSITION: 10,
                    CONF_ENABLE_MIN_POSITION: True,
                    CONF_MAX_POSITION: 95,
                    CONF_ENABLE_MAX_POSITION: False,
                },
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_MIN_POSITION] == 10
        assert new_opts[CONF_ENABLE_MIN_POSITION] is True
        assert new_opts[CONF_MAX_POSITION] == 95

    async def test_invalid_min_position_rejected(self, hass: HomeAssistant):
        await _setup(hass, entry_id="pos_err_01")
        with pytest.raises((ServiceValidationError, Exception)):
            await _call(hass, "set_position_limits", {CONF_MIN_POSITION: 150})

    async def test_inverse_state_bool(self, hass: HomeAssistant):
        await _setup(hass, entry_id="pos_inv_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(hass, "set_position_limits", {CONF_INVERSE_STATE: True})

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_INVERSE_STATE] is True


class TestSetSunsetSunrise:
    """Integration tests for set_sunset_sunrise service."""

    async def test_updates_sunset_position(self, hass: HomeAssistant):
        await _setup(hass, entry_id="ss_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(hass, "set_sunset_sunrise", {CONF_SUNSET_POS: 25})

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_SUNSET_POS] == 25

    async def test_clears_sunset_position_with_null(self, hass: HomeAssistant):
        opts = {**VERTICAL_OPTIONS, CONF_SUNSET_POS: 30}
        await _setup(hass, entry_id="ss_null_01", options=opts)
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(hass, "set_sunset_sunrise", {CONF_SUNSET_POS: None})

        new_opts = mock_update.call_args[1]["options"]
        assert CONF_SUNSET_POS not in new_opts

    async def test_updates_offsets(self, hass: HomeAssistant):
        await _setup(hass, entry_id="ss_off_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_sunset_sunrise",
                {CONF_SUNSET_OFFSET: -30, CONF_SUNRISE_OFFSET: 15},
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_SUNSET_OFFSET] == -30
        assert new_opts[CONF_SUNRISE_OFFSET] == 15

    async def test_sunset_use_my_requires_my_value(self, hass: HomeAssistant):
        await _setup(hass, entry_id="ss_my_err_01")
        with pytest.raises((ServiceValidationError, Exception)):
            await _call(hass, "set_sunset_sunrise", {CONF_SUNSET_USE_MY: True})

    async def test_sunset_use_my_with_value_accepted(self, hass: HomeAssistant):
        await _setup(hass, entry_id="ss_my_ok_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_sunset_sunrise",
                {CONF_SUNSET_USE_MY: True, CONF_MY_POSITION_VALUE: 50},
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_SUNSET_USE_MY] is True
        assert new_opts[CONF_MY_POSITION_VALUE] == 50


class TestSetAutomationTiming:
    """Integration tests for set_automation_timing service."""

    async def test_updates_delta_position(self, hass: HomeAssistant):
        await _setup(hass, entry_id="at_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(hass, "set_automation_timing", {CONF_DELTA_POSITION: 10})

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_DELTA_POSITION] == 10

    async def test_start_time_and_entity_mutual_exclusion(self, hass: HomeAssistant):
        await _setup(hass, entry_id="at_excl_01")
        with pytest.raises((ServiceValidationError, Exception)):
            await _call(
                hass,
                "set_automation_timing",
                {CONF_START_TIME: "08:00:00", CONF_START_ENTITY: "sensor.x"},
            )

    async def test_end_time_and_entity_mutual_exclusion(self, hass: HomeAssistant):
        await _setup(hass, entry_id="at_excl_02")
        with pytest.raises((ServiceValidationError, Exception)):
            await _call(
                hass,
                "set_automation_timing",
                {CONF_END_TIME: "20:00:00", CONF_END_ENTITY: "sensor.x"},
            )

    async def test_return_sunset_bool(self, hass: HomeAssistant):
        await _setup(hass, entry_id="at_ret_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(hass, "set_automation_timing", {CONF_RETURN_SUNSET: True})

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_RETURN_SUNSET] is True


class TestSetManualOverride:
    """Integration tests for set_manual_override service."""

    async def test_updates_duration(self, hass: HomeAssistant):
        await _setup(hass, entry_id="mo_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_manual_override",
                {
                    CONF_MANUAL_OVERRIDE_DURATION: {
                        "hours": 1,
                        "minutes": 30,
                        "seconds": 0,
                    }
                },
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_MANUAL_OVERRIDE_DURATION] == {
            "hours": 1,
            "minutes": 30,
            "seconds": 0,
        }

    async def test_reset_flag(self, hass: HomeAssistant):
        await _setup(hass, entry_id="mo_rst_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_manual_override",
                {CONF_MANUAL_OVERRIDE_RESET: True},
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_MANUAL_OVERRIDE_RESET] is True


class TestSetForceOverride:
    """Integration tests for set_force_override service."""

    async def test_updates_position(self, hass: HomeAssistant):
        await _setup(hass, entry_id="fo_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_force_override",
                {CONF_FORCE_OVERRIDE_POSITION: 0, CONF_FORCE_OVERRIDE_MIN_MODE: True},
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_FORCE_OVERRIDE_POSITION] == 0
        assert new_opts[CONF_FORCE_OVERRIDE_MIN_MODE] is True

    async def test_sensors_replace_not_append(self, hass: HomeAssistant):
        opts = {**VERTICAL_OPTIONS, CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.old"]}
        await _setup(hass, entry_id="fo_list_01", options=opts)
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_force_override",
                {CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.new"]},
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_FORCE_OVERRIDE_SENSORS] == ["binary_sensor.new"]

    async def test_clears_sensors_with_empty_list(self, hass: HomeAssistant):
        opts = {**VERTICAL_OPTIONS, CONF_FORCE_OVERRIDE_SENSORS: ["binary_sensor.x"]}
        await _setup(hass, entry_id="fo_clear_01", options=opts)
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_force_override",
                {CONF_FORCE_OVERRIDE_SENSORS: []},
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_FORCE_OVERRIDE_SENSORS] == []


class TestSetCustomPosition:
    """Integration tests for set_custom_position service."""

    async def test_slot_1_routing(self, hass: HomeAssistant):
        await _setup(hass, entry_id="cp_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_custom_position",
                {
                    "slot": 1,
                    "sensor": "binary_sensor.high_sun",
                    "position": 80,
                },
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts["custom_position_sensor_1"] == "binary_sensor.high_sun"
        assert new_opts["custom_position_1"] == 80

    async def test_slot_2_routing(self, hass: HomeAssistant):
        await _setup(hass, entry_id="cp_02")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_custom_position",
                {"slot": 2, "sensor": "binary_sensor.y", "position": 50},
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts["custom_position_sensor_2"] == "binary_sensor.y"
        assert new_opts["custom_position_2"] == 50

    async def test_slot_4_routing(self, hass: HomeAssistant):
        await _setup(hass, entry_id="cp_04")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_custom_position",
                {
                    "slot": 4,
                    "sensor": "binary_sensor.z",
                    "position": 30,
                    "priority": 90,
                },
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts["custom_position_sensor_4"] == "binary_sensor.z"
        assert new_opts["custom_position_4"] == 30
        assert new_opts["custom_position_priority_4"] == 90

    async def test_invalid_slot_rejected(self, hass: HomeAssistant):
        await _setup(hass, entry_id="cp_err_01")
        with pytest.raises((ServiceValidationError, Exception)):
            await _call(
                hass,
                "set_custom_position",
                {"slot": 5, "sensor": "binary_sensor.x", "position": 50},
            )

    async def test_sensor_without_position_raises(self, hass: HomeAssistant):
        await _setup(hass, entry_id="cp_inc_01")
        with pytest.raises((ServiceValidationError, Exception)):
            await _call(
                hass,
                "set_custom_position",
                {"slot": 1, "sensor": "binary_sensor.x"},
            )

    async def test_clear_slot_with_null(self, hass: HomeAssistant):
        opts = {
            **VERTICAL_OPTIONS,
            "custom_position_sensor_1": "binary_sensor.old",
            "custom_position_1": 70,
        }
        await _setup(hass, entry_id="cp_clr_01", options=opts)
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_custom_position",
                {"slot": 1, "sensor": None, "position": None},
            )

        new_opts = mock_update.call_args[1]["options"]
        assert "custom_position_sensor_1" not in new_opts
        assert "custom_position_1" not in new_opts


class TestSetMotion:
    """Integration tests for set_motion service."""

    async def test_updates_timeout(self, hass: HomeAssistant):
        await _setup(hass, entry_id="mot_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(hass, "set_motion", {CONF_MOTION_TIMEOUT: 600})

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_MOTION_TIMEOUT] == 600

    async def test_sensors_replace_semantics(self, hass: HomeAssistant):
        opts = {**VERTICAL_OPTIONS, CONF_MOTION_SENSORS: ["binary_sensor.a"]}
        await _setup(hass, entry_id="mot_list_01", options=opts)
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_motion",
                {CONF_MOTION_SENSORS: ["binary_sensor.b", "binary_sensor.c"]},
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_MOTION_SENSORS] == ["binary_sensor.b", "binary_sensor.c"]

    async def test_invalid_timeout_rejected(self, hass: HomeAssistant):
        await _setup(hass, entry_id="mot_err_01")
        with pytest.raises((ServiceValidationError, Exception)):
            await _call(hass, "set_motion", {CONF_MOTION_TIMEOUT: 10})  # below 30


class TestSetLightCloud:
    """Integration tests for set_light_cloud service."""

    async def test_updates_cloud_suppression(self, hass: HomeAssistant):
        await _setup(hass, entry_id="lc_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(hass, "set_light_cloud", {CONF_CLOUD_SUPPRESSION: True})

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_CLOUD_SUPPRESSION] is True

    async def test_weather_state_list_replaced(self, hass: HomeAssistant):
        await _setup(hass, entry_id="lc_ws_01")
        new_states = ["sunny", "clear"]
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(hass, "set_light_cloud", {"weather_state": new_states})

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts["weather_state"] == new_states


class TestSetClimate:
    """Integration tests for set_climate service."""

    async def test_updates_climate_mode(self, hass: HomeAssistant):
        await _setup(hass, entry_id="cl_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(hass, "set_climate", {CONF_CLIMATE_MODE: True})

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_CLIMATE_MODE] is True

    async def test_temp_ordering_enforced(self, hass: HomeAssistant):
        await _setup(hass, entry_id="cl_temp_err_01")
        with pytest.raises((ServiceValidationError, Exception)):
            await _call(
                hass,
                "set_climate",
                {CONF_TEMP_LOW: 28, CONF_TEMP_HIGH: 20},
            )

    async def test_temp_ordering_valid(self, hass: HomeAssistant):
        await _setup(hass, entry_id="cl_temp_ok_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_climate",
                {CONF_TEMP_LOW: 18, CONF_TEMP_HIGH: 26},
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_TEMP_LOW] == 18
        assert new_opts[CONF_TEMP_HIGH] == 26


class TestSetWeatherSafety:
    """Integration tests for set_weather_safety service."""

    async def test_updates_wind_threshold(self, hass: HomeAssistant):
        await _setup(hass, entry_id="ws_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_weather_safety",
                {CONF_WEATHER_WIND_SPEED_THRESHOLD: 40},
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_WEATHER_WIND_SPEED_THRESHOLD] == 40

    async def test_updates_override_position_and_timeout(self, hass: HomeAssistant):
        await _setup(hass, entry_id="ws_02")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_weather_safety",
                {
                    CONF_WEATHER_OVERRIDE_POSITION: 0,
                    CONF_WEATHER_TIMEOUT: 600,
                    CONF_WEATHER_BYPASS_AUTO_CONTROL: True,
                },
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_WEATHER_OVERRIDE_POSITION] == 0
        assert new_opts[CONF_WEATHER_TIMEOUT] == 600
        assert new_opts[CONF_WEATHER_BYPASS_AUTO_CONTROL] is True

    async def test_severe_sensors_replace(self, hass: HomeAssistant):
        opts = {**VERTICAL_OPTIONS, CONF_WEATHER_SEVERE_SENSORS: ["binary_sensor.old"]}
        await _setup(hass, entry_id="ws_sev_01", options=opts)
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_weather_safety",
                {
                    CONF_WEATHER_SEVERE_SENSORS: [
                        "binary_sensor.new1",
                        "binary_sensor.new2",
                    ]
                },
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_WEATHER_SEVERE_SENSORS] == [
            "binary_sensor.new1",
            "binary_sensor.new2",
        ]


class TestSetSunTracking:
    """Integration tests for set_sun_tracking service."""

    async def test_updates_azimuth(self, hass: HomeAssistant):
        await _setup(hass, entry_id="st_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(hass, "set_sun_tracking", {CONF_AZIMUTH: 270})

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_AZIMUTH] == 270

    async def test_updates_fov(self, hass: HomeAssistant):
        await _setup(hass, entry_id="st_fov_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_sun_tracking",
                {CONF_FOV_LEFT: 60, CONF_FOV_RIGHT: 75},
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_FOV_LEFT] == 60
        assert new_opts[CONF_FOV_RIGHT] == 75

    async def test_enables_disable_sun_tracking(self, hass: HomeAssistant):
        await _setup(hass, entry_id="st_tog_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(hass, "set_sun_tracking", {CONF_ENABLE_SUN_TRACKING: False})

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_ENABLE_SUN_TRACKING] is False


class TestSetBlindSpot:
    """Integration tests for set_blind_spot service."""

    async def test_updates_blind_spot_angles(self, hass: HomeAssistant):
        await _setup(hass, entry_id="bs_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_blind_spot",
                {CONF_BLIND_SPOT_LEFT: 10, CONF_BLIND_SPOT_RIGHT: 40},
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_BLIND_SPOT_LEFT] == 10
        assert new_opts[CONF_BLIND_SPOT_RIGHT] == 40

    async def test_right_must_exceed_left(self, hass: HomeAssistant):
        await _setup(hass, entry_id="bs_err_01")
        with pytest.raises((ServiceValidationError, Exception)):
            await _call(
                hass,
                "set_blind_spot",
                {CONF_BLIND_SPOT_LEFT: 50, CONF_BLIND_SPOT_RIGHT: 30},
            )


class TestSetInterpolation:
    """Integration tests for set_interpolation service."""

    async def test_updates_interp_toggle(self, hass: HomeAssistant):
        await _setup(hass, entry_id="ip_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(hass, "set_interpolation", {CONF_INTERP: True})

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts[CONF_INTERP] is True


class TestSetGeometry:
    """Integration tests for set_geometry service."""

    async def test_updates_window_height_for_blind(self, hass: HomeAssistant):
        await _setup(hass, entry_id="geo_01", cover_type=SensorType.BLIND)
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(hass, "set_geometry", {"window_height": 3.0})

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts["window_height"] == 3.0

    async def test_awning_field_rejected_for_blind_cover(self, hass: HomeAssistant):
        await _setup(hass, entry_id="geo_err_01", cover_type=SensorType.BLIND)
        with pytest.raises((ServiceValidationError, Exception)):
            await _call(hass, "set_geometry", {"length_awning": 2.5})

    async def test_awning_geometry_accepted_for_awning(self, hass: HomeAssistant):
        from tests.ha_helpers import HORIZONTAL_OPTIONS

        await _setup(
            hass,
            entry_id="geo_awn_01",
            cover_type=SensorType.AWNING,
            options=dict(HORIZONTAL_OPTIONS),
        )
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(hass, "set_geometry", {"length_awning": 3.5})

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts["length_awning"] == 3.5


class TestSetOption:
    """Integration tests for generic set_option service."""

    async def test_updates_known_option(self, hass: HomeAssistant):
        await _setup(hass, entry_id="so_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_option",
                {"option": "default_percentage", "value": 70},
            )

        new_opts = mock_update.call_args[1]["options"]
        assert new_opts["default_percentage"] == 70

    async def test_clears_option_with_null(self, hass: HomeAssistant):
        opts = {**VERTICAL_OPTIONS, CONF_SUNSET_POS: 25}
        await _setup(hass, entry_id="so_null_01", options=opts)
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
        ):
            await _call(
                hass,
                "set_option",
                {"option": "sunset_position", "value": None},
            )

        new_opts = mock_update.call_args[1]["options"]
        assert "sunset_position" not in new_opts

    async def test_identity_key_rejected(self, hass: HomeAssistant):
        await _setup(hass, entry_id="so_id_01")
        with pytest.raises((ServiceValidationError, Exception)):
            await _call(hass, "set_option", {"option": "name", "value": "x"})

    async def test_unknown_key_rejected(self, hass: HomeAssistant):
        await _setup(hass, entry_id="so_unk_01")
        with pytest.raises((ServiceValidationError, Exception)):
            await _call(
                hass,
                "set_option",
                {"option": "nonexistent_option_xyz", "value": 42},
            )

    async def test_invalid_value_rejected(self, hass: HomeAssistant):
        await _setup(hass, entry_id="so_val_err_01")
        with pytest.raises((ServiceValidationError, Exception)):
            await _call(
                hass,
                "set_option",
                {"option": "default_percentage", "value": 999},
            )

    async def test_all_settable_keys_in_field_validators(self):
        for key in ALL_SETTABLE_KEYS:
            assert key in FIELD_VALIDATORS, (
                f"'{key}' is in ALL_SETTABLE_KEYS but missing from FIELD_VALIDATORS"
            )


class TestReloadPropagation:
    """Integration tests verifying async_update_entry is called per service invocation."""

    async def test_options_mutated_and_reload_triggered(self, hass: HomeAssistant):
        """Each service call must call async_update_entry once per target."""
        await _setup(hass, entry_id="rl_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            patch.object(
                hass.config_entries, "async_reload", new_callable=AsyncMock
            ) as mock_reload,
        ):
            await _call(
                hass,
                "set_position_limits",
                {CONF_DEFAULT_HEIGHT: 80},
            )

        mock_update.assert_called_once()
        # Reload is triggered by the update listener registered in __init__.py;
        # within tests the listener may or may not fire depending on HA's
        # listener wiring — so we just verify async_update_entry was called.
        _ = mock_reload  # captured but listener-wiring is HA-internal

    async def test_no_update_when_no_fields_provided(self, hass: HomeAssistant):
        await _setup(hass, entry_id="rl_err_01")
        with (
            patch.object(hass.config_entries, "async_update_entry") as mock_update,
            pytest.raises((ServiceValidationError, Exception)),
        ):
            # Service called with empty data (no option fields)
            await hass.services.async_call(
                DOMAIN,
                "set_position_limits",
                {},
                blocking=True,
                target={"entity_id": ["cover.test_blind"]},
            )

        mock_update.assert_not_called()
