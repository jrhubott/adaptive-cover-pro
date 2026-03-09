"""Config flow for Adaptive Cover Pro integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_AWNING_ANGLE,
    CONF_AZIMUTH,
    CONF_BLIND_SPOT_ELEVATION,
    CONF_BLIND_SPOT_LEFT,
    CONF_BLIND_SPOT_RIGHT,
    CONF_CLIMATE_MODE,
    CONF_DEFAULT_HEIGHT,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_DISTANCE,
    CONF_ENABLE_BLIND_SPOT,
    CONF_ENABLE_DIAGNOSTICS,
    CONF_ENABLE_MAX_POSITION,
    CONF_ENABLE_MIN_POSITION,
    CONF_END_ENTITY,
    CONF_END_TIME,
    CONF_ENTITIES,
    CONF_FORCE_OVERRIDE_POSITION,
    CONF_FORCE_OVERRIDE_SENSORS,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_HEIGHT_WIN,
    CONF_INTERP,
    CONF_INTERP_END,
    CONF_INTERP_LIST,
    CONF_INTERP_LIST_NEW,
    CONF_INTERP_START,
    CONF_INVERSE_STATE,
    CONF_IRRADIANCE_ENTITY,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_LENGTH_AWNING,
    CONF_LUX_ENTITY,
    CONF_LUX_THRESHOLD,
    CONF_MANUAL_IGNORE_INTERMEDIATE,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_MANUAL_OVERRIDE_RESET,
    CONF_MANUAL_THRESHOLD,
    CONF_MAX_ELEVATION,
    CONF_MAX_POSITION,
    CONF_MIN_ELEVATION,
    CONF_MIN_POSITION,
    CONF_MODE,
    CONF_MOTION_SENSORS,
    CONF_MOTION_TIMEOUT,
    CONF_OPEN_CLOSE_THRESHOLD,
    CONF_OUTSIDE_THRESHOLD,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_RETURN_SUNSET,
    CONF_SENSOR_TYPE,
    CONF_START_ENTITY,
    CONF_START_TIME,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_TEMP_ENTITY,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_MODE,
    CONF_TRANSPARENT_BLIND,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_STATE,
    CONF_WINDOW_DEPTH,
    DEFAULT_MOTION_TIMEOUT,
    DIRECT_MAPPING_FIELDS,
    DOMAIN,
    LEGACY_DOMAIN,
    SensorType,
)

_LOGGER = logging.getLogger(__name__)

# DEFAULT_NAME = "Adaptive Cover"

SENSOR_TYPE_MENU = [SensorType.BLIND, SensorType.AWNING, SensorType.TILT]


CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required("name"): selector.TextSelector(),
        vol.Optional(CONF_MODE): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=SENSOR_TYPE_MENU, translation_key="mode"
            )
        ),
    }
)

CLIMATE_MODE = vol.Schema(
    {
        vol.Optional(CONF_CLIMATE_MODE, default=False): selector.BooleanSelector(),
    }
)

OPTIONS = vol.Schema(
    {
        vol.Required(CONF_AZIMUTH, default=180): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=359,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Required(CONF_DEFAULT_HEIGHT, default=60): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(CONF_MAX_POSITION): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(
            CONF_ENABLE_MAX_POSITION, default=False
        ): selector.BooleanSelector(),
        vol.Optional(CONF_MIN_POSITION): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=99,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(
            CONF_ENABLE_MIN_POSITION, default=False
        ): selector.BooleanSelector(),
        vol.Optional(CONF_MIN_ELEVATION): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=90,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Optional(CONF_MAX_ELEVATION): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=90,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Required(CONF_FOV_LEFT, default=90): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=180,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Required(CONF_FOV_RIGHT, default=90): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=180,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Required(CONF_SUNSET_POS, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Required(CONF_SUNSET_OFFSET, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                mode=selector.NumberSelectorMode.BOX, unit_of_measurement="minutes"
            )
        ),
        vol.Required(CONF_SUNRISE_OFFSET, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                mode=selector.NumberSelectorMode.BOX, unit_of_measurement="minutes"
            )
        ),
        vol.Required(CONF_INVERSE_STATE, default=False): selector.BooleanSelector(),
        vol.Required(CONF_ENABLE_BLIND_SPOT, default=False): selector.BooleanSelector(),
        vol.Required(CONF_INTERP, default=False): selector.BooleanSelector(),
    }
)

VERTICAL_OPTIONS = vol.Schema(
    {
        vol.Optional(CONF_ENTITIES, default=[]): selector.EntitySelector(
            selector.EntitySelectorConfig(
                multiple=True,
                filter=selector.EntityFilterSelectorConfig(
                    domain="cover",
                ),
            )
        ),
        vol.Required(CONF_HEIGHT_WIN, default=2.1): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                max=6,
                step=0.01,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="m",
            )
        ),
        vol.Required(CONF_DISTANCE, default=0.5): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                max=5,
                step=0.1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="m",
            )
        ),
        vol.Optional(CONF_WINDOW_DEPTH, default=0.0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=0.5,
                step=0.01,
                unit_of_measurement="m",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
    }
).extend(OPTIONS.schema)


HORIZONTAL_OPTIONS = vol.Schema(
    {
        vol.Required(CONF_LENGTH_AWNING, default=2.1): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.3,
                max=6,
                step=0.01,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="m",
            )
        ),
        vol.Required(CONF_AWNING_ANGLE, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=45,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
    }
).extend(VERTICAL_OPTIONS.schema)

TILT_OPTIONS = vol.Schema(
    {
        vol.Optional(CONF_ENTITIES, default=[]): selector.EntitySelector(
            selector.EntitySelectorConfig(
                multiple=True,
                filter=selector.EntityFilterSelectorConfig(
                    domain="cover",
                    supported_features=["cover.CoverEntityFeature.SET_TILT_POSITION"],
                ),
            )
        ),
        vol.Required(CONF_TILT_DEPTH, default=3): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                max=15,
                step=0.1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="cm",
            )
        ),
        vol.Required(CONF_TILT_DISTANCE, default=2): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                max=15,
                step=0.1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="cm",
            )
        ),
        vol.Required(CONF_TILT_MODE, default="mode2"): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=["mode1", "mode2"], translation_key="tilt_mode"
            )
        ),
    }
).extend(OPTIONS.schema)

CLIMATE_OPTIONS = vol.Schema(
    {
        vol.Required(CONF_TEMP_ENTITY): selector.EntitySelector(
            selector.EntityFilterSelectorConfig(domain=["climate", "sensor"])
        ),
        vol.Required(CONF_TEMP_LOW, default=21): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=86,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Required(CONF_TEMP_HIGH, default=25): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=90,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="°",
            )
        ),
        vol.Optional(
            CONF_OUTSIDETEMP_ENTITY, default=vol.UNDEFINED
        ): selector.EntitySelector(
            selector.EntityFilterSelectorConfig(domain=["sensor"])
        ),
        vol.Optional(CONF_OUTSIDE_THRESHOLD, default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=100)
        ),
        vol.Optional(
            CONF_PRESENCE_ENTITY, default=vol.UNDEFINED
        ): selector.EntitySelector(
            selector.EntityFilterSelectorConfig(
                domain=["device_tracker", "zone", "binary_sensor", "input_boolean"]
            )
        ),
        vol.Optional(CONF_LUX_ENTITY, default=vol.UNDEFINED): selector.EntitySelector(
            selector.EntityFilterSelectorConfig(
                domain=["sensor"], device_class="illuminance"
            )
        ),
        vol.Optional(CONF_LUX_THRESHOLD, default=1000): selector.NumberSelector(
            selector.NumberSelectorConfig(
                mode=selector.NumberSelectorMode.BOX, unit_of_measurement="lux"
            )
        ),
        vol.Optional(
            CONF_IRRADIANCE_ENTITY, default=vol.UNDEFINED
        ): selector.EntitySelector(
            selector.EntityFilterSelectorConfig(
                domain=["sensor"], device_class="irradiance"
            )
        ),
        vol.Optional(CONF_IRRADIANCE_THRESHOLD, default=300): selector.NumberSelector(
            selector.NumberSelectorConfig(
                mode=selector.NumberSelectorMode.BOX, unit_of_measurement="W/m²"
            )
        ),
        vol.Optional(CONF_TRANSPARENT_BLIND, default=False): selector.BooleanSelector(),
        vol.Optional(
            CONF_WEATHER_ENTITY, default=vol.UNDEFINED
        ): selector.EntitySelector(
            selector.EntityFilterSelectorConfig(domain="weather")
        ),
    }
)

WEATHER_OPTIONS = vol.Schema(
    {
        vol.Optional(
            CONF_WEATHER_STATE, default=["sunny", "partlycloudy", "cloudy", "clear"]
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                multiple=True,
                sort=False,
                options=[
                    "clear-night",
                    "clear",
                    "cloudy",
                    "fog",
                    "hail",
                    "lightning",
                    "lightning-rainy",
                    "partlycloudy",
                    "pouring",
                    "rainy",
                    "snowy",
                    "snowy-rainy",
                    "sunny",
                    "windy",
                    "windy-variant",
                    "exceptional",
                ],
            )
        )
    }
)


AUTOMATION_CONFIG = vol.Schema(
    {
        vol.Required(CONF_DELTA_POSITION, default=1): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=90,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(CONF_DELTA_TIME, default=2): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=2,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="minutes",
            )
        ),
        vol.Optional(CONF_START_TIME, default="00:00:00"): selector.TimeSelector(),
        vol.Optional(CONF_START_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor", "input_datetime"])
        ),
        vol.Required(
            CONF_MANUAL_OVERRIDE_DURATION, default={"minutes": 15}
        ): selector.DurationSelector(),
        vol.Required(
            CONF_MANUAL_OVERRIDE_RESET, default=False
        ): selector.BooleanSelector(),
        vol.Optional(CONF_MANUAL_THRESHOLD): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=99,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(
            CONF_MANUAL_IGNORE_INTERMEDIATE, default=False
        ): selector.BooleanSelector(),
        vol.Optional(CONF_OPEN_CLOSE_THRESHOLD, default=50): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=99,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(CONF_END_TIME, default="00:00:00"): selector.TimeSelector(),
        vol.Optional(CONF_END_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor", "input_datetime"])
        ),
        vol.Optional(CONF_FORCE_OVERRIDE_SENSORS, default=[]): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=["binary_sensor"],
                multiple=True,
            )
        ),
        vol.Optional(CONF_FORCE_OVERRIDE_POSITION, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(CONF_MOTION_SENSORS, default=[]): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=["binary_sensor"],
                multiple=True,
                device_class="motion",
            )
        ),
        vol.Optional(CONF_MOTION_TIMEOUT, default=DEFAULT_MOTION_TIMEOUT): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=30,
                max=3600,
                step=30,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="seconds",
            )
        ),
        vol.Optional(CONF_RETURN_SUNSET, default=False): selector.BooleanSelector(),
        vol.Optional(
            CONF_ENABLE_DIAGNOSTICS, default=False
        ): selector.BooleanSelector(),
    }
)

INTERPOLATION_OPTIONS = vol.Schema(
    {
        vol.Optional(CONF_INTERP_START): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(CONF_INTERP_END): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(CONF_INTERP_LIST, default=[]): selector.SelectSelector(
            selector.SelectSelectorConfig(
                multiple=True, custom_value=True, options=["0", "50", "100"]
            )
        ),
        vol.Optional(CONF_INTERP_LIST_NEW, default=[]): selector.SelectSelector(
            selector.SelectSelectorConfig(
                multiple=True, custom_value=True, options=["0", "50", "100"]
            )
        ),
    }
)


def _get_azimuth_edges(data) -> int:
    """Calculate azimuth edges."""
    return data[CONF_FOV_LEFT] + data[CONF_FOV_RIGHT]


class ConfigFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle ConfigFlow."""

    def __init__(self) -> None:  # noqa: D107
        super().__init__()
        self.type_blind: str | None = None
        self.config: dict[str, Any] = {}
        self.mode: str = "basic"
        self.legacy_entries: list[dict[str, Any]] = []
        self.selected_for_import: list[str] = []
        self.import_index: int = 0
        self.imported_count: int = 0

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step with import detection."""
        # errors = {}

        # Detect legacy entries on first load
        if not user_input and not hasattr(self, "_legacy_detected"):
            self._legacy_detected = True
            legacy_entries = await self._detect_legacy_entries(self.hass)

            if legacy_entries:
                # Show menu with import option
                return self.async_show_menu(  # type: ignore[return-value]
                    step_id="user",
                    menu_options=["create_new", "import_legacy"],
                    description_placeholders={"legacy_count": str(len(legacy_entries))},
                )

        if user_input:
            self.config = user_input
            if self.config[CONF_MODE] == SensorType.BLIND:
                return await self.async_step_vertical()
            if self.config[CONF_MODE] == SensorType.AWNING:
                return await self.async_step_horizontal()
            if self.config[CONF_MODE] == SensorType.TILT:
                return await self.async_step_tilt()
        return self.async_show_form(
            step_id="user",
            data_schema=CONFIG_SCHEMA,
            description_placeholders={"legacy_count": "0"},
        )

    async def async_step_vertical(self, user_input: dict[str, Any] | None = None):
        """Show basic config for vertical blinds."""
        self.type_blind = SensorType.BLIND
        if user_input is not None:
            if (
                user_input.get(CONF_MAX_ELEVATION) is not None
                and user_input.get(CONF_MIN_ELEVATION) is not None
            ):
                if user_input[CONF_MAX_ELEVATION] <= user_input[CONF_MIN_ELEVATION]:
                    return self.async_show_form(
                        step_id="vertical",
                        data_schema=CLIMATE_MODE.extend(VERTICAL_OPTIONS.schema),
                        errors={
                            CONF_MAX_ELEVATION: "Must be greater than 'Minimal Elevation'"
                        },
                    )
            self.config.update(user_input)

            # Extract first cover entity's name to auto-populate device name
            if CONF_ENTITIES in user_input and user_input[CONF_ENTITIES]:
                first_entity_id = user_input[CONF_ENTITIES][0]
                entity_reg = er.async_get(self.hass)
                entity_entry = entity_reg.async_get(first_entity_id)

                if entity_entry:
                    # Get entity name (use original_name or name, fallback to ID)
                    entity_name = (
                        entity_entry.original_name
                        or entity_entry.name
                        or first_entity_id.split(".")[-1].replace("_", " ").title()
                    )

                    # Create suggested device name with "Adaptive" prefix
                    suggested_name = f"Adaptive {entity_name}"

                    # Update the config name with suggestion
                    self.config["name"] = suggested_name

            if self.config[CONF_INTERP]:
                return await self.async_step_interp()
            if self.config[CONF_ENABLE_BLIND_SPOT]:
                return await self.async_step_blind_spot()
            return await self.async_step_automation()
        return self.async_show_form(
            step_id="vertical",
            data_schema=CLIMATE_MODE.extend(VERTICAL_OPTIONS.schema),
        )

    async def async_step_horizontal(self, user_input: dict[str, Any] | None = None):
        """Show basic config for horizontal blinds."""
        self.type_blind = SensorType.AWNING
        if user_input is not None:
            if (
                user_input.get(CONF_MAX_ELEVATION) is not None
                and user_input.get(CONF_MIN_ELEVATION) is not None
            ):
                if user_input[CONF_MAX_ELEVATION] <= user_input[CONF_MIN_ELEVATION]:
                    return self.async_show_form(
                        step_id="horizontal",
                        data_schema=CLIMATE_MODE.extend(HORIZONTAL_OPTIONS.schema),
                        errors={
                            CONF_MAX_ELEVATION: "Must be greater than 'Minimal Elevation'"
                        },
                    )
            self.config.update(user_input)

            # Extract first cover entity's name to auto-populate device name
            if CONF_ENTITIES in user_input and user_input[CONF_ENTITIES]:
                first_entity_id = user_input[CONF_ENTITIES][0]
                entity_reg = er.async_get(self.hass)
                entity_entry = entity_reg.async_get(first_entity_id)

                if entity_entry:
                    # Get entity name (use original_name or name, fallback to ID)
                    entity_name = (
                        entity_entry.original_name
                        or entity_entry.name
                        or first_entity_id.split(".")[-1].replace("_", " ").title()
                    )

                    # Create suggested device name with "Adaptive" prefix
                    suggested_name = f"Adaptive {entity_name}"

                    # Update the config name with suggestion
                    self.config["name"] = suggested_name

            if self.config[CONF_INTERP]:
                return await self.async_step_interp()
            if self.config[CONF_ENABLE_BLIND_SPOT]:
                return await self.async_step_blind_spot()
            return await self.async_step_automation()
        return self.async_show_form(
            step_id="horizontal",
            data_schema=CLIMATE_MODE.extend(HORIZONTAL_OPTIONS.schema),
        )

    async def async_step_tilt(self, user_input: dict[str, Any] | None = None):
        """Show basic config for tilted blinds."""
        self.type_blind = SensorType.TILT
        if user_input is not None:
            if (
                user_input.get(CONF_MAX_ELEVATION) is not None
                and user_input.get(CONF_MIN_ELEVATION) is not None
            ):
                if user_input[CONF_MAX_ELEVATION] <= user_input[CONF_MIN_ELEVATION]:
                    return self.async_show_form(
                        step_id="tilt",
                        data_schema=CLIMATE_MODE.extend(TILT_OPTIONS.schema),
                        errors={
                            CONF_MAX_ELEVATION: "Must be greater than 'Minimal Elevation'"
                        },
                    )
            self.config.update(user_input)

            # Extract first cover entity's name to auto-populate device name
            if CONF_ENTITIES in user_input and user_input[CONF_ENTITIES]:
                first_entity_id = user_input[CONF_ENTITIES][0]
                entity_reg = er.async_get(self.hass)
                entity_entry = entity_reg.async_get(first_entity_id)

                if entity_entry:
                    # Get entity name (use original_name or name, fallback to ID)
                    entity_name = (
                        entity_entry.original_name
                        or entity_entry.name
                        or first_entity_id.split(".")[-1].replace("_", " ").title()
                    )

                    # Create suggested device name with "Adaptive" prefix
                    suggested_name = f"Adaptive {entity_name}"

                    # Update the config name with suggestion
                    self.config["name"] = suggested_name

            if self.config[CONF_INTERP]:
                return await self.async_step_interp()
            if self.config[CONF_ENABLE_BLIND_SPOT]:
                return await self.async_step_blind_spot()
            return await self.async_step_automation()
        return self.async_show_form(
            step_id="tilt", data_schema=CLIMATE_MODE.extend(TILT_OPTIONS.schema)
        )

    async def async_step_interp(self, user_input: dict[str, Any] | None = None):
        """Show interpolation options."""
        if user_input is not None:
            if len(user_input[CONF_INTERP_LIST]) != len(
                user_input[CONF_INTERP_LIST_NEW]
            ):
                return self.async_show_form(
                    step_id="interp",
                    data_schema=INTERPOLATION_OPTIONS,
                    errors={
                        CONF_INTERP_LIST_NEW: "Must have same length as 'Interpolation' list"
                    },
                )
            self.config.update(user_input)
            if self.config[CONF_ENABLE_BLIND_SPOT]:
                return await self.async_step_blind_spot()
            return await self.async_step_automation()
        return self.async_show_form(step_id="interp", data_schema=INTERPOLATION_OPTIONS)

    async def async_step_blind_spot(self, user_input: dict[str, Any] | None = None):
        """Add blindspot to data."""
        edges = _get_azimuth_edges(self.config)
        schema = vol.Schema(
            {
                vol.Required(CONF_BLIND_SPOT_LEFT, default=0): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="°",
                        min=0,
                        max=edges - 1,
                    )
                ),
                vol.Required(CONF_BLIND_SPOT_RIGHT, default=1): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="°",
                        min=1,
                        max=edges,
                    )
                ),
                vol.Optional(CONF_BLIND_SPOT_ELEVATION): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=90,
                        step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="°",
                    )
                ),
            }
        )
        if user_input is not None:
            if user_input[CONF_BLIND_SPOT_RIGHT] <= user_input[CONF_BLIND_SPOT_LEFT]:
                return self.async_show_form(
                    step_id="blind_spot",
                    data_schema=schema,
                    errors={
                        CONF_BLIND_SPOT_RIGHT: "Must be greater than 'Blind Spot Left Edge'"
                    },
                )
            self.config.update(user_input)
            return await self.async_step_automation()

        return self.async_show_form(step_id="blind_spot", data_schema=schema)

    async def async_step_automation(self, user_input: dict[str, Any] | None = None):
        """Manage automation options."""
        if user_input is not None:
            self.config.update(user_input)
            if self.config[CONF_CLIMATE_MODE] is True:
                return await self.async_step_climate()
            return await self.async_step_update()
        return self.async_show_form(step_id="automation", data_schema=AUTOMATION_CONFIG)

    async def async_step_climate(self, user_input: dict[str, Any] | None = None):
        """Manage climate options."""
        if user_input is not None:
            self.config.update(user_input)
            if self.config.get(CONF_WEATHER_ENTITY):
                return await self.async_step_weather()
            return await self.async_step_update()
        return self.async_show_form(step_id="climate", data_schema=CLIMATE_OPTIONS)

    async def async_step_weather(self, user_input: dict[str, Any] | None = None):
        """Manage weather conditions."""
        if user_input is not None:
            self.config.update(user_input)
            return await self.async_step_update()
        return self.async_show_form(step_id="weather", data_schema=WEATHER_OPTIONS)

    async def async_step_update(self, user_input: dict[str, Any] | None = None):
        """Create entry."""
        if self.type_blind is None:
            msg = "type_blind must be set before calling async_step_update"
            raise ValueError(msg)

        type_mapping = {
            "cover_blind": "Vertical",
            "cover_awning": "Horizontal",
            "cover_tilt": "Tilt",
        }
        return self.async_create_entry(
            title=f"{type_mapping[self.type_blind]} {self.config['name']}",
            data={
                "name": self.config["name"],
                CONF_SENSOR_TYPE: self.type_blind,
            },
            options={
                CONF_MODE: self.mode,
                CONF_AZIMUTH: self.config.get(CONF_AZIMUTH),
                CONF_HEIGHT_WIN: self.config.get(CONF_HEIGHT_WIN),
                CONF_DISTANCE: self.config.get(CONF_DISTANCE),
                CONF_WINDOW_DEPTH: self.config.get(CONF_WINDOW_DEPTH),
                CONF_DEFAULT_HEIGHT: self.config.get(CONF_DEFAULT_HEIGHT),
                CONF_MAX_POSITION: self.config.get(CONF_MAX_POSITION),
                CONF_ENABLE_MAX_POSITION: self.config.get(CONF_ENABLE_MAX_POSITION),
                CONF_MIN_POSITION: self.config.get(CONF_MIN_POSITION),
                CONF_ENABLE_MIN_POSITION: self.config.get(CONF_ENABLE_MIN_POSITION),
                CONF_FOV_LEFT: self.config.get(CONF_FOV_LEFT),
                CONF_FOV_RIGHT: self.config.get(CONF_FOV_RIGHT),
                CONF_ENTITIES: self.config.get(CONF_ENTITIES),
                CONF_INVERSE_STATE: self.config.get(CONF_INVERSE_STATE),
                CONF_SUNSET_POS: self.config.get(CONF_SUNSET_POS),
                CONF_SUNSET_OFFSET: self.config.get(CONF_SUNSET_OFFSET),
                CONF_SUNRISE_OFFSET: self.config.get(CONF_SUNRISE_OFFSET),
                CONF_LENGTH_AWNING: self.config.get(CONF_LENGTH_AWNING),
                CONF_AWNING_ANGLE: self.config.get(CONF_AWNING_ANGLE),
                CONF_TILT_DISTANCE: self.config.get(CONF_TILT_DISTANCE),
                CONF_TILT_DEPTH: self.config.get(CONF_TILT_DEPTH),
                CONF_TILT_MODE: self.config.get(CONF_TILT_MODE),
                CONF_TEMP_ENTITY: self.config.get(CONF_TEMP_ENTITY),
                CONF_PRESENCE_ENTITY: self.config.get(CONF_PRESENCE_ENTITY),
                CONF_WEATHER_ENTITY: self.config.get(CONF_WEATHER_ENTITY),
                CONF_TEMP_LOW: self.config.get(CONF_TEMP_LOW),
                CONF_TEMP_HIGH: self.config.get(CONF_TEMP_HIGH),
                CONF_OUTSIDETEMP_ENTITY: self.config.get(CONF_OUTSIDETEMP_ENTITY),
                CONF_CLIMATE_MODE: self.config.get(CONF_CLIMATE_MODE),
                CONF_WEATHER_STATE: self.config.get(CONF_WEATHER_STATE),
                CONF_DELTA_POSITION: self.config.get(CONF_DELTA_POSITION),
                CONF_DELTA_TIME: self.config.get(CONF_DELTA_TIME),
                CONF_START_TIME: self.config.get(CONF_START_TIME),
                CONF_START_ENTITY: self.config.get(CONF_START_ENTITY),
                CONF_END_TIME: self.config.get(CONF_END_TIME),
                CONF_END_ENTITY: self.config.get(CONF_END_ENTITY),
                CONF_FORCE_OVERRIDE_SENSORS: self.config.get(
                    CONF_FORCE_OVERRIDE_SENSORS, []
                ),
                CONF_FORCE_OVERRIDE_POSITION: self.config.get(
                    CONF_FORCE_OVERRIDE_POSITION, 0
                ),
                CONF_MOTION_SENSORS: self.config.get(CONF_MOTION_SENSORS, []),
                CONF_MOTION_TIMEOUT: self.config.get(
                    CONF_MOTION_TIMEOUT, DEFAULT_MOTION_TIMEOUT
                ),
                CONF_MANUAL_OVERRIDE_DURATION: self.config.get(
                    CONF_MANUAL_OVERRIDE_DURATION
                ),
                CONF_MANUAL_OVERRIDE_RESET: self.config.get(CONF_MANUAL_OVERRIDE_RESET),
                CONF_MANUAL_THRESHOLD: self.config.get(CONF_MANUAL_THRESHOLD),
                CONF_MANUAL_IGNORE_INTERMEDIATE: self.config.get(
                    CONF_MANUAL_IGNORE_INTERMEDIATE
                ),
                CONF_OPEN_CLOSE_THRESHOLD: self.config.get(
                    CONF_OPEN_CLOSE_THRESHOLD, 50
                ),
                CONF_BLIND_SPOT_RIGHT: self.config.get(CONF_BLIND_SPOT_RIGHT, None),
                CONF_BLIND_SPOT_LEFT: self.config.get(CONF_BLIND_SPOT_LEFT, None),
                CONF_BLIND_SPOT_ELEVATION: self.config.get(
                    CONF_BLIND_SPOT_ELEVATION, None
                ),
                CONF_ENABLE_BLIND_SPOT: self.config.get(CONF_ENABLE_BLIND_SPOT),
                CONF_MIN_ELEVATION: self.config.get(CONF_MIN_ELEVATION, None),
                CONF_MAX_ELEVATION: self.config.get(CONF_MAX_ELEVATION, None),
                CONF_TRANSPARENT_BLIND: self.config.get(CONF_TRANSPARENT_BLIND, False),
                CONF_INTERP: self.config.get(CONF_INTERP),
                CONF_INTERP_START: self.config.get(CONF_INTERP_START, None),
                CONF_INTERP_END: self.config.get(CONF_INTERP_END, None),
                CONF_INTERP_LIST: self.config.get(CONF_INTERP_LIST, []),
                CONF_INTERP_LIST_NEW: self.config.get(CONF_INTERP_LIST_NEW, []),
                CONF_LUX_ENTITY: self.config.get(CONF_LUX_ENTITY),
                CONF_LUX_THRESHOLD: self.config.get(CONF_LUX_THRESHOLD),
                CONF_IRRADIANCE_ENTITY: self.config.get(CONF_IRRADIANCE_ENTITY),
                CONF_IRRADIANCE_THRESHOLD: self.config.get(CONF_IRRADIANCE_THRESHOLD),
                CONF_OUTSIDE_THRESHOLD: self.config.get(CONF_OUTSIDE_THRESHOLD),
            },
        )

    async def async_step_create_new(self, user_input: dict[str, Any] | None = None):
        """Handle create new configuration flow."""
        # Redirect to original user flow
        return await self.async_step_user(user_input)

    async def async_step_import_legacy(self, user_input: dict[str, Any] | None = None):
        """Handle import from legacy Adaptive Cover."""
        return await self.async_step_import_detect(user_input)

    async def _detect_legacy_entries(self, hass: HomeAssistant) -> list[ConfigEntry]:
        """Detect existing adaptive_cover config entries."""
        from homeassistant.config_entries import ConfigEntryState

        legacy_entries = hass.config_entries.async_entries(LEGACY_DOMAIN)
        return [
            entry for entry in legacy_entries if entry.state == ConfigEntryState.LOADED
        ]

    async def _validate_imported_config(
        self, legacy_entry: ConfigEntry
    ) -> dict[str, Any]:
        """Validate that imported configuration entities still exist."""
        errors = []
        entity_reg = er.async_get(self.hass)

        # Validate cover entities
        cover_entities = legacy_entry.options.get(CONF_ENTITIES, [])
        for entity_id in cover_entities:
            if not entity_reg.async_get(entity_id):
                errors.append(f"Cover entity not found: {entity_id}")

        # Validate optional entities
        optional_entities = [
            (CONF_TEMP_ENTITY, "Temperature"),
            (CONF_PRESENCE_ENTITY, "Presence"),
            (CONF_WEATHER_ENTITY, "Weather"),
            (CONF_START_ENTITY, "Start time"),
            (CONF_END_ENTITY, "End time"),
            (CONF_LUX_ENTITY, "Lux"),
            (CONF_IRRADIANCE_ENTITY, "Irradiance"),
            (CONF_OUTSIDETEMP_ENTITY, "Outside temperature"),
            (CONF_FORCE_OVERRIDE_SENSORS, "Force override sensors"),
            (CONF_MOTION_SENSORS, "Motion sensors"),
        ]

        for conf_key, label in optional_entities:
            entity_id = legacy_entry.options.get(conf_key)
            if entity_id and not entity_reg.async_get(entity_id):
                errors.append(f"{label} entity not found: {entity_id}")

        return {"valid": len(errors) == 0, "errors": errors}

    async def _ensure_unique_name(self, name: str) -> str:
        """Ensure imported name doesn't conflict with existing entries."""
        existing_entries = self.hass.config_entries.async_entries(DOMAIN)
        existing_names = {e.data.get("name") for e in existing_entries}

        if name not in existing_names:
            return name

        # Try with " (Imported)" suffix
        imported_name = f"{name} (Imported)"
        if imported_name not in existing_names:
            return imported_name

        # Add number suffix if needed
        counter = 2
        while f"{name} (Imported {counter})" in existing_names:
            counter += 1

        return f"{name} (Imported {counter})"

    async def async_step_import_detect(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Detect and present importable entries."""
        legacy_entries = await self._detect_legacy_entries(self.hass)

        if not legacy_entries:
            return self.async_abort(reason="no_legacy_entries")  # type: ignore[return-value]

        # Store entries in instance variable
        self.legacy_entries = [
            {
                "entry_id": e.entry_id,
                "name": e.data.get("name"),
                "type": e.data.get(CONF_SENSOR_TYPE),
            }
            for e in legacy_entries
        ]

        return await self.async_step_import_select()

    async def async_step_import_select(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Allow user to select which entries to import."""
        legacy_entries = self.legacy_entries

        if user_input is not None:
            selected_ids = user_input.get("selected_entries", [])
            self.selected_for_import = selected_ids
            return await self.async_step_import_review()

        # Build selection schema with multi-select
        return self.async_show_form(  # type: ignore[return-value]
            step_id="import_select",
            data_schema=vol.Schema(
                {
                    vol.Required("selected_entries"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            multiple=True,
                            options=[
                                {
                                    "value": e["entry_id"],
                                    "label": f"{e['name']} ({e['type']})",
                                }
                                for e in legacy_entries
                            ],
                        )
                    )
                }
            ),
        )

    async def async_step_import_review(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Review configurations before import with validation."""
        if user_input is not None:
            if user_input.get("confirm"):
                return await self.async_step_import_execute()
            return self.async_abort(reason="user_cancelled")  # type: ignore[return-value]

        # Load legacy entry details and validate
        selected_ids = self.selected_for_import
        legacy_entries = self.hass.config_entries.async_entries(LEGACY_DOMAIN)

        preview_data = []
        validation_errors = []

        for entry_id in selected_ids:
            legacy = next((e for e in legacy_entries if e.entry_id == entry_id), None)
            if not legacy:
                continue

            # Validate configuration
            validation_result = await self._validate_imported_config(legacy)

            if not validation_result["valid"]:
                validation_errors.extend(
                    [
                        f"{legacy.data.get('name')}: {error}"
                        for error in validation_result["errors"]
                    ]
                )
                continue

            # Build preview data
            cover_entities = legacy.options.get(CONF_ENTITIES, [])
            climate_enabled = legacy.options.get(CONF_CLIMATE_MODE, False)

            preview_data.append(
                {
                    "name": legacy.data.get("name"),
                    "type": legacy.data.get(CONF_SENSOR_TYPE),
                    "entities": cover_entities,
                    "climate_mode": climate_enabled,
                    "delta_pos": legacy.options.get(CONF_DELTA_POSITION, 1),
                    "delta_time": legacy.options.get(CONF_DELTA_TIME, 2),
                }
            )

        # If validation errors, show error and abort
        if validation_errors:
            return self.async_abort(  # type: ignore[return-value]
                reason="validation_failed",
                description_placeholders={
                    "errors": "\n".join([f"• {e}" for e in validation_errors])
                },
            )

        # Build summary for display
        entries_summary = []
        for p in preview_data:
            type_label = {
                SensorType.BLIND: "Vertical",
                SensorType.AWNING: "Horizontal",
                SensorType.TILT: "Tilt",
            }.get(p["type"], p["type"])

            entries_summary.append(
                f"• {p['name']} ({type_label})\n"
                f"  - {len(p['entities'])} cover(s): {', '.join(p['entities'])}\n"
                f"  - Climate mode: {'Enabled' if p['climate_mode'] else 'Disabled'}\n"
                f"  - Automation: {p['delta_pos']}% threshold, {p['delta_time']} min intervals"
            )

        return self.async_show_form(  # type: ignore[return-value]
            step_id="import_review",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=True): selector.BooleanSelector()}
            ),
            description_placeholders={"entries_summary": "\n\n".join(entries_summary)},
        )

    async def async_step_import_execute(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Execute the import operation."""
        selected_ids = self.selected_for_import
        if not selected_ids:
            return self.async_abort(reason="no_entries_selected")  # type: ignore[return-value]

        # Get the current entry being processed (or start with the first)
        current_index = getattr(self, "import_index", 0)

        if current_index >= len(selected_ids):
            # All entries processed, show completion
            imported_count = self.context.get("imported_count", 0)
            self.hass.components.persistent_notification.async_create(  # type: ignore[attr-defined]
                message=(
                    f"Successfully imported {imported_count} Adaptive Cover "
                    f"configuration(s) to Adaptive Cover Pro.\n\n"
                    f"Next steps:\n"
                    f"1. Verify imported configurations work correctly\n"
                    f"2. Test cover movements\n"
                    f"3. Disable old Adaptive Cover entries when ready\n"
                    f"4. (Optional) Remove old integration from HACS\n\n"
                    f"Both integrations can run simultaneously during transition."
                ),
                title="Adaptive Cover Pro Import Complete",
                notification_id=f"adaptive_cover_pro_import_{imported_count}",
            )
            return self.async_abort(reason="import_successful")  # type: ignore[return-value]

        # Process current entry
        entry_id = selected_ids[current_index]
        legacy_entries = self.hass.config_entries.async_entries(LEGACY_DOMAIN)
        legacy = next((e for e in legacy_entries if e.entry_id == entry_id), None)

        if not legacy:
            # Skip this entry and move to next
            self.import_index = current_index + 1
            return await self.async_step_import_execute()

        # Map data (immutable setup info)
        new_data = {
            "name": legacy.data.get("name"),
            CONF_SENSOR_TYPE: legacy.data.get(CONF_SENSOR_TYPE),
        }

        # Map options (all configuration fields)
        new_options = {}
        for field in DIRECT_MAPPING_FIELDS:
            if field in legacy.options:
                new_options[field] = legacy.options[field]

        # Ensure unique name
        new_data["name"] = await self._ensure_unique_name(new_data["name"])  # type: ignore[arg-type]

        # Create title
        type_labels = {
            SensorType.BLIND: "Vertical",
            SensorType.AWNING: "Horizontal",
            SensorType.TILT: "Tilt",
        }
        sensor_type = new_data[CONF_SENSOR_TYPE]
        title = f"{type_labels.get(sensor_type, 'Unknown')} {new_data['name']}"  # type: ignore[arg-type]

        # Update tracking for next iteration
        self.import_index = current_index + 1
        self.imported_count += 1

        # Create the entry
        return self.async_create_entry(  # type: ignore[return-value]
            title=title,
            data=new_data,
            options=new_options,
        )


class OptionsFlowHandler(OptionsFlow):
    """Options to adjust parameters."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        # super().__init__(config_entry)
        self._config_entry = config_entry
        self.current_config: dict = dict(config_entry.data)
        self.options = dict(config_entry.options)
        self.sensor_type: SensorType = (  # type: ignore[misc]
            self.current_config.get(CONF_SENSOR_TYPE) or SensorType.BLIND
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        options = ["automation", "blind"]
        if self.options[CONF_CLIMATE_MODE]:
            options.append("climate")
        if self.options.get(CONF_WEATHER_ENTITY):
            options.append("weather")
        if self.options.get(CONF_ENABLE_BLIND_SPOT):
            options.append("blind_spot")
        if self.options.get(CONF_INTERP):
            options.append("interp")
        return self.async_show_menu(step_id="init", menu_options=options)  # type: ignore[return-value]

    async def async_step_automation(self, user_input: dict[str, Any] | None = None):
        """Manage automation options."""
        if user_input is not None:
            entities = [
                CONF_START_ENTITY,
                CONF_END_ENTITY,
                CONF_MANUAL_THRESHOLD,
                CONF_FORCE_OVERRIDE_SENSORS,
                CONF_MOTION_SENSORS,
            ]
            self.optional_entities(entities, user_input)
            self.options.update(user_input)
            return await self._update_options()
        return self.async_show_form(
            step_id="automation",
            data_schema=self.add_suggested_values_to_schema(
                AUTOMATION_CONFIG, user_input or self.options
            ),
        )

    async def async_step_blind(self, user_input: dict[str, Any] | None = None):
        """Adjust blind parameters."""
        if self.sensor_type == SensorType.BLIND:
            return await self.async_step_vertical()
        if self.sensor_type == SensorType.AWNING:
            return await self.async_step_horizontal()
        if self.sensor_type == SensorType.TILT:
            return await self.async_step_tilt()

    async def async_step_vertical(self, user_input: dict[str, Any] | None = None):
        """Show basic config for vertical blinds."""
        self.type_blind = SensorType.BLIND
        schema = CLIMATE_MODE.extend(VERTICAL_OPTIONS.schema)
        if self.options[CONF_CLIMATE_MODE]:
            schema = VERTICAL_OPTIONS
        if user_input is not None:
            keys = [
                CONF_MIN_ELEVATION,
                CONF_MAX_ELEVATION,
            ]
            self.optional_entities(keys, user_input)
            if (
                user_input.get(CONF_MAX_ELEVATION) is not None
                and user_input.get(CONF_MIN_ELEVATION) is not None
            ):
                if user_input[CONF_MAX_ELEVATION] <= user_input[CONF_MIN_ELEVATION]:
                    return self.async_show_form(
                        step_id="vertical",
                        data_schema=CLIMATE_MODE.extend(VERTICAL_OPTIONS.schema),
                        errors={
                            CONF_MAX_ELEVATION: "Must be greater than 'Minimal Elevation'"
                        },
                    )
            self.options.update(user_input)
            if self.options.get(CONF_INTERP, False):
                return await self.async_step_interp()
            if self.options[CONF_ENABLE_BLIND_SPOT]:
                return await self.async_step_blind_spot()
            if self.options[CONF_CLIMATE_MODE]:
                return await self.async_step_climate()
            return await self._update_options()
        return self.async_show_form(
            step_id="vertical",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self.options
            ),
        )

    async def async_step_horizontal(self, user_input: dict[str, Any] | None = None):
        """Show basic config for horizontal blinds."""
        self.type_blind = SensorType.AWNING
        schema = CLIMATE_MODE.extend(HORIZONTAL_OPTIONS.schema)
        if self.options[CONF_CLIMATE_MODE]:
            schema = HORIZONTAL_OPTIONS
        if user_input is not None:
            keys = [
                CONF_MIN_ELEVATION,
                CONF_MAX_ELEVATION,
            ]
            self.optional_entities(keys, user_input)
            if (
                user_input.get(CONF_MAX_ELEVATION) is not None
                and user_input.get(CONF_MIN_ELEVATION) is not None
            ):
                if user_input[CONF_MAX_ELEVATION] <= user_input[CONF_MIN_ELEVATION]:
                    return self.async_show_form(
                        step_id="horizontal",
                        data_schema=CLIMATE_MODE.extend(HORIZONTAL_OPTIONS.schema),
                        errors={
                            CONF_MAX_ELEVATION: "Must be greater than 'Minimal Elevation'"
                        },
                    )
            self.options.update(user_input)
            if self.options[CONF_CLIMATE_MODE]:
                return await self.async_step_climate()
            return await self._update_options()
        return self.async_show_form(
            step_id="horizontal",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self.options
            ),
        )

    async def async_step_tilt(self, user_input: dict[str, Any] | None = None):
        """Show basic config for tilted blinds."""
        self.type_blind = SensorType.TILT
        schema = CLIMATE_MODE.extend(TILT_OPTIONS.schema)
        if self.options[CONF_CLIMATE_MODE]:
            schema = TILT_OPTIONS
        if user_input is not None:
            keys = [
                CONF_MIN_ELEVATION,
                CONF_MAX_ELEVATION,
            ]
            self.optional_entities(keys, user_input)
            if (
                user_input.get(CONF_MAX_ELEVATION) is not None
                and user_input.get(CONF_MIN_ELEVATION) is not None
            ):
                if user_input[CONF_MAX_ELEVATION] <= user_input[CONF_MIN_ELEVATION]:
                    return self.async_show_form(
                        step_id="tilt",
                        data_schema=CLIMATE_MODE.extend(TILT_OPTIONS.schema),
                        errors={
                            CONF_MAX_ELEVATION: "Must be greater than 'Minimal Elevation'"
                        },
                    )
            self.options.update(user_input)
            if self.options[CONF_CLIMATE_MODE]:
                return await self.async_step_climate()
            return await self._update_options()
        return self.async_show_form(
            step_id="tilt",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self.options
            ),
        )

    async def async_step_interp(self, user_input: dict[str, Any] | None = None):
        """Show interpolation options."""
        if user_input is not None:
            if len(user_input[CONF_INTERP_LIST]) != len(
                user_input[CONF_INTERP_LIST_NEW]
            ):
                return self.async_show_form(
                    step_id="interp",
                    data_schema=INTERPOLATION_OPTIONS,
                    errors={
                        CONF_INTERP_LIST_NEW: "Must have same length as 'Interpolation' list"
                    },
                )
            self.options.update(user_input)
            return await self._update_options()
        return self.async_show_form(
            step_id="interp",
            data_schema=self.add_suggested_values_to_schema(
                INTERPOLATION_OPTIONS, user_input or self.options
            ),
        )

    async def async_step_blind_spot(self, user_input: dict[str, Any] | None = None):
        """Add blindspot to data."""
        edges = _get_azimuth_edges(self.options)
        schema = vol.Schema(
            {
                vol.Required(CONF_BLIND_SPOT_LEFT, default=0): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="°",
                        min=0,
                        max=edges - 1,
                    )
                ),
                vol.Required(CONF_BLIND_SPOT_RIGHT, default=1): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="°",
                        min=1,
                        max=edges,
                    )
                ),
                vol.Optional(CONF_BLIND_SPOT_ELEVATION): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=90,
                        step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="°",
                    )
                ),
            }
        )
        if user_input is not None:
            if user_input[CONF_BLIND_SPOT_RIGHT] <= user_input[CONF_BLIND_SPOT_LEFT]:
                return self.async_show_form(
                    step_id="blind_spot",
                    data_schema=schema,
                    errors={
                        CONF_BLIND_SPOT_RIGHT: "Must be greater than 'Blind Spot Left Edge'"
                    },
                )
            self.options.update(user_input)
            return await self._update_options()
        return self.async_show_form(
            step_id="blind_spot",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self.options
            ),
        )

    async def async_step_climate(self, user_input: dict[str, Any] | None = None):
        """Manage climate options."""
        if user_input is not None:
            entities = [
                CONF_OUTSIDETEMP_ENTITY,
                CONF_WEATHER_ENTITY,
                CONF_PRESENCE_ENTITY,
                CONF_LUX_ENTITY,
                CONF_IRRADIANCE_ENTITY,
            ]
            self.optional_entities(entities, user_input)
            self.options.update(user_input)
            if self.options.get(CONF_WEATHER_ENTITY):
                return await self.async_step_weather()
            return await self._update_options()
        return self.async_show_form(
            step_id="climate",
            data_schema=self.add_suggested_values_to_schema(
                CLIMATE_OPTIONS, user_input or self.options
            ),
        )

    async def async_step_weather(self, user_input: dict[str, Any] | None = None):
        """Manage weather conditions."""
        if user_input is not None:
            self.options.update(user_input)
            return await self._update_options()
        return self.async_show_form(
            step_id="weather",
            data_schema=self.add_suggested_values_to_schema(
                WEATHER_OPTIONS, user_input or self.options
            ),
        )

    async def _update_options(self) -> FlowResult:
        """Update config entry options."""
        return self.async_create_entry(title="", data=self.options)  # type: ignore[return-value]

    def optional_entities(self, keys: list, user_input: dict[str, Any]):
        """Set value to None if key does not exist."""
        for key in keys:
            if key not in user_input:
                user_input[key] = None
