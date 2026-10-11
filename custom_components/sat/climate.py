"""Climate platform for SAT."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, override

from homeassistant.components import sensor, weather
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    PRESET_ACTIVITY,
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_HOME,
    PRESET_NONE,
    PRESET_SLEEP,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CoreState,
    Event,
    EventStateChangedData,
    HassJob,
    HomeAssistant,
    callback,
)
from homeassistant.helpers.entity import EntityPlatformState
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .area import SENSOR_TEMPERATURE_ID, Areas
from .const import (
    COLD_SETPOINT,
    CONF_CLIMATE_VALVE_OFFSET,
    CONF_DEFAULT_HVAC_MODE,
    CONF_DYNAMIC_MINIMUM_SETPOINT,
    CONF_DYNAMIC_MINIMUM_SETPOINT_VERSION,
    CONF_FORCE_PULSE_WIDTH_MODULATION,
    CONF_HEATING_MODE,
    CONF_HEATING_SYSTEM,
    CONF_HUMIDITY_SENSOR_ENTITY_ID,
    CONF_INSIDE_SENSOR_ENTITY_ID,
    CONF_MAXIMUM_RELATIVE_MODULATION,
    CONF_OUTSIDE_SENSOR_ENTITY_ID,
    CONF_OVERSHOOT_PROTECTION,
    CONF_PUSH_SETPOINT_TO_THERMOSTAT,
    CONF_RADIATORS,
    CONF_SENSOR_MAX_VALUE_AGE,
    CONF_SIMULATION,
    CONF_SYNC_CLIMATES_WITH_MODE,
    CONF_SYNC_CLIMATES_WITH_PRESET,
    CONF_TARGET_TEMPERATURE_STEP,
    CONF_THERMAL_COMFORT,
    CONF_THERMOSTAT,
    CONF_WINDOW_MINIMUM_OPEN_TIME,
    CONTROL_LOOP_STALL_TIME,
    DEADBAND,
    DYNAMIC_MINIMUM_SETPOINT_VERSION_1,
    DYNAMIC_MINIMUM_SETPOINT_VERSION_2,
    HEATING_MODE_ECO,
    MINIMUM_RELATIVE_MODULATION,
    MINIMUM_SETPOINT,
    OPTIONS_DEFAULTS,
    BoilerStatus,
    PWMStatus,
)
from .coordinator import DeviceState, SatDataUpdateCoordinator
from .entity import SatEntity
from .errors import Error, Errors
from .helpers import convert_time_str_to_seconds, float_value, is_state_stale, state_age_seconds
from .manufacturers.geminox import Geminox
from .relative_modulation import RelativeModulation, RelativeModulationState
from .summer_simmer import SummerSimmer
from .util import (
    create_heating_curve_controller,
    create_minimum_setpoint_controller,
    create_pid_controller,
    create_pwm_controller,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .pwm import PWMState

ATTR_ROOMS = "rooms"
ATTR_SETPOINT = "setpoint"
ATTR_OPTIMAL_COEFFICIENT = "optimal_coefficient"
ATTR_COEFFICIENT_DERIVATIVE = "coefficient_derivative"
ATTR_PRE_CUSTOM_TEMPERATURE = "pre_custom_temperature"
ATTR_PRE_ACTIVITY_TEMPERATURE = "pre_activity_temperature"

# How long the last control setpoint is refreshed while inputs are missing,
# before the boiler goes back to the room thermostat
INPUTS_MISSING_GRACE_PERIOD = timedelta(minutes=10)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    _hass: HomeAssistant, _config_entry: ConfigEntry, _async_add_devices: AddEntitiesCallback
) -> None:
    """Set up the SatClimate device."""
    _async_add_devices([_config_entry.runtime_data.climate])


@dataclass
class SatClimateExtraStoredData(ExtraStoredData):
    hvac_mode: str | None
    target_temperature: float | None
    preset_mode: str | None
    pre_custom_temperature: float | None

    @override
    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SatClimateExtraStoredData:
        return cls(
            data.get("hvac_mode"),
            data.get("target_temperature"),
            data.get("preset_mode"),
            data.get("pre_custom_temperature"),
        )


class SatClimate(SatEntity, ClimateEntity, RestoreEntity):
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(
        self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry, unit: str
    ) -> None:
        super().__init__(coordinator, config_entry)

        # Set up some public variables
        self.thermostat = config_entry.data.get(CONF_THERMOSTAT)

        # Get some sensor entity IDs
        self.inside_sensor_entity_id: str = config_entry.data[CONF_INSIDE_SENSOR_ENTITY_ID]
        self.humidity_sensor_entity_id = config_entry.data.get(CONF_HUMIDITY_SENSOR_ENTITY_ID)

        # Get outside sensor entity IDs
        outside_sensor_entities = config_entry.data[CONF_OUTSIDE_SENSOR_ENTITY_ID]

        # If outside sensor entity IDs is a string, make it a list
        self.outside_sensor_entities: list[str] = (
            [outside_sensor_entities]
            if isinstance(outside_sensor_entities, str)
            else outside_sensor_entities
        )

        # Create config options dictionary with defaults
        config_options = OPTIONS_DEFAULTS.copy()
        config_options.update(config_entry.options)

        # Create a dictionary mapping preset keys to temperature options
        conf_presets = {
            p: f"{p}_temperature"
            for p in (PRESET_ACTIVITY, PRESET_AWAY, PRESET_HOME, PRESET_SLEEP, PRESET_COMFORT)
        }

        # Create a dictionary mapping preset keys to temperature values
        self._presets = {
            key: config_options[value] for key, value in conf_presets.items() if key in conf_presets
        }

        self._alpha = 0.2
        self._sensors: list[str] = []
        self._rooms: dict[str, float] | None = None
        self._setpoint: float | None = None
        self._calculated_setpoint: float | None = None
        self._last_boiler_temperature: float | None = None

        self._window_sensor_handle: asyncio.Task[None] | None = None
        self._pre_custom_temperature: float | None = None
        self._pre_activity_temperature: float | None = None

        self._attr_temperature_unit = unit
        self._attr_hvac_mode = None
        self._attr_preset_mode = PRESET_NONE
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        self._attr_preset_modes = [PRESET_NONE, *self._presets.keys()]

        # Add features based on compatibility
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
        )

        # Conditionally add TURN_ON if it exists
        if hasattr(ClimateEntityFeature, "TURN_ON"):
            self._attr_supported_features |= ClimateEntityFeature.TURN_ON

        # Conditionally add TURN_OFF if it exists
        if hasattr(ClimateEntityFeature, "TURN_OFF"):
            self._attr_supported_features |= ClimateEntityFeature.TURN_OFF

        self._control_heating_loop_unsub: Callable[[], None] | None = None
        self._sensor_stale_unsub: Callable[[], None] | None = None
        self._last_control_at = dt_util.utcnow()
        self._inputs_missing_since: datetime | None = None
        self._inputs_missing_released = False
        self._valves_open = True
        self.control_paused = False
        self.calibration: asyncio.Task[None] | None = None
        self._control_lock = asyncio.Lock()
        self._state_listeners: list[Callable[[], None]] = []

        # System Configuration
        self._attr_name = None
        self._attr_unique_id = config_entry.entry_id

        self._radiators = config_entry.data.get(CONF_RADIATORS) or []

        self._simulation = bool(config_entry.data.get(CONF_SIMULATION))
        self._heating_system = str(config_entry.data.get(CONF_HEATING_SYSTEM))
        self._overshoot_protection = bool(config_entry.data.get(CONF_OVERSHOOT_PROTECTION))

        # User Configuration
        self._heating_mode = str(config_entry.options.get(CONF_HEATING_MODE))
        self._thermal_comfort = bool(config_options.get(CONF_THERMAL_COMFORT))
        self._push_setpoint_to_thermostat = bool(
            config_options.get(CONF_PUSH_SETPOINT_TO_THERMOSTAT)
        )
        self._climate_valve_offset = float(config_options[CONF_CLIMATE_VALVE_OFFSET])
        self._attr_target_temperature_step = float(config_options[CONF_TARGET_TEMPERATURE_STEP])
        self._dynamic_minimum_setpoint = bool(config_options.get(CONF_DYNAMIC_MINIMUM_SETPOINT))
        self._sync_climates_with_mode = bool(config_options.get(CONF_SYNC_CLIMATES_WITH_MODE))
        self._sync_climates_with_preset = bool(config_options.get(CONF_SYNC_CLIMATES_WITH_PRESET))
        self._maximum_relative_modulation = int(config_options[CONF_MAXIMUM_RELATIVE_MODULATION])
        self._minimum_setpoint_version = int(config_options[CONF_DYNAMIC_MINIMUM_SETPOINT_VERSION])
        self._force_pulse_width_modulation = bool(
            config_options.get(CONF_FORCE_PULSE_WIDTH_MODULATION)
        )
        self._sensor_max_value_age = convert_time_str_to_seconds(
            config_options[CONF_SENSOR_MAX_VALUE_AGE]
        )
        self._default_hvac_mode = HVACMode(config_options[CONF_DEFAULT_HVAC_MODE])
        self._window_minimum_open_time = convert_time_str_to_seconds(
            config_options[CONF_WINDOW_MINIMUM_OPEN_TIME]
        )

        # Create a PID controller with given configuration options
        self.pid = create_pid_controller(config_entry.data, config_options)

        # Create Area controllers
        self.areas = Areas(config_entry.data, config_options)

        # Create Relative Modulation controller
        self.relative_modulation = RelativeModulation(coordinator, self._heating_system)

        # Create a Heating Curve controller with given configuration options
        self.heating_curve = create_heating_curve_controller(config_entry.data, config_options)

        # Create the Minimum Setpoint controller
        self.minimum_setpoint = create_minimum_setpoint_controller(
            coordinator.minimum_setpoint, config_options
        )

        # Create a PWM controller with given configuration options
        self.pwm = create_pwm_controller(self.heating_curve, config_entry.data, config_options)

        if self._simulation:
            _LOGGER.warning("Simulation mode!")

    def async_track_coordinator_data(self) -> None:
        """Track changes in the coordinator's boiler temperature and trigger the heating loop."""
        if (
            self._coordinator.boiler_temperature is not None
            and self._last_boiler_temperature == self._coordinator.boiler_temperature
        ):
            return

        self.schedule_control_heating_loop()
        self._last_boiler_temperature = self._coordinator.boiler_temperature

    @override
    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # Restore the previous state if available, or set default values
        await self._restore_previous_state_or_set_defaults()

        # Update a heating curve if outside temperature is available
        if self.current_outside_temperature is not None and self.target_temperature is not None:
            self.areas.heating_curves.update(self.current_outside_temperature)
            self.heating_curve.update(self.target_temperature, self.current_outside_temperature)

        self.async_on_remove(async_at_started(self.hass, self._async_started))

        await self.minimum_setpoint.async_initialize(self.hass, self._config_entry.entry_id)

        # Initialize the area system
        await self.areas.async_added_to_hass(self.hass)

        # Let the coordinator know we are ready
        await self._coordinator.async_added_to_hass()

        # A release sent while the gateway was unreachable is lost,
        # and the gateway keeps the override until told otherwise
        if self.hvac_mode != HVACMode.HEAT:
            await self._coordinator.async_release_control()

    @callback
    def async_add_state_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call the listener every time this entity writes its state."""
        self._state_listeners.append(listener)
        return lambda: self._state_listeners.remove(listener)

    @override
    @callback
    def _async_write_ha_state(self) -> None:
        self._async_update_sensor_values()

        if self.hvac_mode == HVACMode.OFF:
            self._attr_hvac_action = HVACAction.OFF
        elif not self._coordinator.device_active:
            self._attr_hvac_action = HVACAction.IDLE
        else:
            self._attr_hvac_action = HVACAction.HEATING

        self._attr_extra_state_attributes = self.compute_extra_state_attributes()
        super()._async_write_ha_state()

        for listener in list(self._state_listeners):
            listener()

        if self._sensor_stale_unsub is not None:
            self._sensor_stale_unsub()
            self._sensor_stale_unsub = None

        if self._platform_state is not EntityPlatformState.ADDED or self._sensor_max_value_age <= 0:
            return

        ages = [
            state_age_seconds(state)
            for entity_id in (self.inside_sensor_entity_id, self.humidity_sensor_entity_id)
            if entity_id is not None
            and (state := self.hass.states.get(entity_id)) is not None
            and not is_state_stale(state, self._sensor_max_value_age)
        ]
        if ages:
            self._sensor_stale_unsub = async_call_later(
                self.hass,
                self._sensor_max_value_age - max(ages) + 1,
                HassJob(callback(lambda _now: self.async_write_ha_state())),
            )

    @override
    async def async_will_remove_from_hass(self) -> None:
        if self._control_heating_loop_unsub is not None:
            self._control_heating_loop_unsub()
            self._control_heating_loop_unsub = None

        if self._sensor_stale_unsub is not None:
            self._sensor_stale_unsub()
            self._sensor_stale_unsub = None

        if self._window_sensor_handle is not None:
            self._window_sensor_handle.cancel()
            self._window_sensor_handle = None

    async def _async_started(self, _hass: HomeAssistant) -> None:
        await self._register_event_listeners()
        await self.async_control_heating_loop()

    async def _register_event_listeners(self) -> None:
        """Register event listeners."""
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self.schedule_control_heating_loop, timedelta(seconds=30)
            )
        )

        self.async_on_remove(self.coordinator.async_add_listener(self.async_track_coordinator_data))

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self.inside_sensor_entity_id], self._async_inside_sensor_changed
            )
        )

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self.outside_sensor_entities, self._async_outside_entity_changed
            )
        )

        if self.thermostat is not None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, self.thermostat, self._async_thermostat_changed
                )
            )

        if self.humidity_sensor_entity_id is not None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self.humidity_sensor_entity_id], self._async_humidity_sensor_changed
                )
            )

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._radiators, self._async_main_climate_changed
            )
        )

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self.areas.items(), self._async_climate_changed
            )
        )

        for entity_id in self.areas.items():
            state = self.hass.states.get(entity_id)
            if state is not None and (
                sensor_temperature_id := state.attributes.get(SENSOR_TEMPERATURE_ID)
            ):
                self.async_track_sensor_temperature(sensor_temperature_id)

    async def _restore_previous_state_or_set_defaults(self) -> None:
        """Restore the previous state if available or set default values."""
        old_state = await self.async_get_last_state()
        extra_data = await self.async_get_last_extra_data()

        if extra_data is not None:
            restored = SatClimateExtraStoredData.from_dict(extra_data.as_dict())

            if restored.hvac_mode in self.hvac_modes:
                self._attr_hvac_mode = HVACMode(restored.hvac_mode)

            if restored.target_temperature is not None:
                self._attr_target_temperature = float(restored.target_temperature)

            if self.preset_modes is not None and restored.preset_mode in self.preset_modes:
                self._attr_preset_mode = restored.preset_mode

            if restored.pre_custom_temperature is not None:
                self._pre_custom_temperature = float(restored.pre_custom_temperature)

        # Unavailable or unknown placeholder states carry no attributes worth restoring
        if old_state is not None and old_state.state not in self.hvac_modes:
            old_state = None

        if old_state is not None:
            self.pwm.restore(old_state)
            self.pid.restore(old_state)

            if self.target_temperature is None:
                if old_state.attributes.get(ATTR_TEMPERATURE) is None:
                    self._attr_target_temperature = float(self._presets[PRESET_HOME])
                    _LOGGER.warning(
                        "Undefined target temperature, falling back to %s", self.target_temperature
                    )
                else:
                    self._attr_target_temperature = float(old_state.attributes[ATTR_TEMPERATURE])

            if self.hvac_mode is None:
                self._attr_hvac_mode = HVACMode(old_state.state)

            if old_state.attributes.get(ATTR_SETPOINT):
                self._setpoint = old_state.attributes.get(ATTR_SETPOINT)

            if extra_data is None and old_state.attributes.get(ATTR_PRESET_MODE):
                self._attr_preset_mode = old_state.attributes.get(ATTR_PRESET_MODE)

            if old_state.attributes.get(ATTR_PRE_ACTIVITY_TEMPERATURE):
                self._pre_activity_temperature = old_state.attributes.get(
                    ATTR_PRE_ACTIVITY_TEMPERATURE
                )

            if old_state.attributes.get(ATTR_PRE_CUSTOM_TEMPERATURE):
                self._pre_custom_temperature = old_state.attributes.get(ATTR_PRE_CUSTOM_TEMPERATURE)

            if optimal_coefficient := old_state.attributes.get(ATTR_OPTIMAL_COEFFICIENT):
                self.heating_curve.restore_autotune(
                    optimal_coefficient,
                    old_state.attributes.get(ATTR_COEFFICIENT_DERIVATIVE),
                )

            if old_state.attributes.get(ATTR_ROOMS):
                self._rooms = old_state.attributes.get(ATTR_ROOMS)
            else:
                await self._async_update_rooms_from_climates()
        else:
            if self._rooms is None:
                await self._async_update_rooms_from_climates()

            if self.target_temperature is None:
                self._attr_target_temperature = float(self._presets[PRESET_HOME])
                _LOGGER.info(
                    "Nothing to restore, starting at the home temperature %s",
                    self.target_temperature,
                )

            if self.hvac_mode is None:
                self._attr_hvac_mode = self._default_hvac_mode

        if self._attr_preset_mode != PRESET_NONE and self._pre_custom_temperature is None:
            self._pre_custom_temperature = self.target_temperature

        self.async_write_ha_state()

    async def async_stop_control(self) -> None:
        """Wait for a running control loop and stop sending boiler commands."""
        async with self._control_lock:
            self.control_paused = True

    @contextlib.asynccontextmanager
    async def async_calibrating(self) -> AsyncGenerator[None]:
        """Pause control while the current task calibrates, so off, unload and stop cancel it."""
        self.calibration = asyncio.current_task()
        try:
            await self.async_stop_control()
            self.async_write_ha_state()
            yield
        finally:
            self.calibration = None
            self.control_paused = False
            self._last_control_at = dt_util.utcnow()
            self.async_write_ha_state()

    async def async_cancel_calibration(self) -> None:
        if (calibration := self.calibration) is not None:
            calibration.cancel()
            await asyncio.wait([calibration])

    async def async_reset_integral(self) -> None:
        """Reset the integral part of the PID controllers."""
        self.pid.reset()
        self.areas.pids.reset()

    async def async_set_pulse_width_modulation(self, *, enabled: bool) -> None:
        """Enable or disable Pulse Width Modulation."""
        if enabled:
            self.pwm.enable()
        else:
            self.pwm.disable()

    @override
    @property
    def extra_restore_state_data(self) -> SatClimateExtraStoredData:
        return SatClimateExtraStoredData(
            self.hvac_mode,
            self.target_temperature,
            self._attr_preset_mode,
            self._pre_custom_temperature,
        )

    def compute_extra_state_attributes(self) -> dict[str, Any]:
        inside_temperature = self._get_entity_state_float(self.inside_sensor_entity_id)

        return {
            "error": self.max_error.value,
            "error_pid": self.pid.last_error,
            "error_source": self.max_error.entity_id,
            "integral": self.pid.integral,
            "derivative": self.pid.derivative,
            "proportional": self.pid.proportional,
            "integral_enabled": self.pid.integral_enabled,
            "boiler_flame_timing": self._coordinator.flame.average_on_time_seconds,
            "boiler_temperature_cold": self._coordinator.boiler_temperature_cold,
            "boiler_temperature_tracking": self._coordinator.boiler_temperature_tracking,
            "boiler_temperature_derivative": round(derivative, 3)
            if (derivative := self._coordinator.boiler_temperature_derivative) is not None
            else None,
            "pre_custom_temperature": self._pre_custom_temperature,
            "pre_activity_temperature": self._pre_activity_temperature,
            "derivative_enabled": self.pid.derivative_enabled,
            "derivative_raw": self.pid.raw_derivative,
            "current_kp": self.pid.kp,
            "current_ki": self.pid.ki,
            "current_kd": self.pid.kd,
            "rooms": self._rooms,
            "setpoint": self._setpoint,
            "current_humidity": self.current_humidity,
            "summer_simmer_index": SummerSimmer.index(inside_temperature, self.current_humidity),
            "summer_simmer_perception": SummerSimmer.perception(
                inside_temperature, self.current_humidity
            ),
            "valves_open": self.valves_open,
            "heating_curve": self.heating_curve.value,
            "requested_setpoint": self.requested_setpoint,
            "minimum_setpoint": self.minimum_setpoint_value,
            "outside_temperature": self.current_outside_temperature,
            "optimal_coefficient": self.heating_curve.optimal_coefficient,
            "coefficient_derivative": self.heating_curve.coefficient_derivative,
            "relative_modulation_value": self.relative_modulation_value,
            "relative_modulation_enabled": self.relative_modulation.enabled,
            "relative_modulation_state": self.relative_modulation_state.name,
            "pulse_width_modulation_enabled": self.pulse_width_modulation_enabled,
            "pulse_width_modulation_state": self.pwm.status.name,
            "pulse_width_modulation_duty_cycle": self.pwm.duty_cycle,
        }

    @callback
    def _async_update_sensor_values(self) -> None:
        """Read the inside temperature and humidity from their sensors.

        Sensors go stale by age without firing a state change, so readers refresh first.
        """
        if self.humidity_sensor_entity_id is None:
            self._attr_current_humidity = None
        else:
            self._attr_current_humidity = self._get_entity_state_float(
                self.humidity_sensor_entity_id
            )

        current_temperature = self._get_entity_state_float(self.inside_sensor_entity_id)
        if current_temperature is not None and self._thermal_comfort:
            current_temperature = SummerSimmer.index(current_temperature, self.current_humidity)

        self._attr_current_temperature = current_temperature

    @property
    def error(self) -> float:
        """Return the error value."""
        if self.target_temperature is None or self.current_temperature is None:
            return 0

        return round(self.target_temperature - self.current_temperature, 2)

    @property
    def current_outside_temperature(self) -> float | None:
        """Return the current outside temperature."""
        self.outside_sensor_entities.sort(key=lambda x: "sensor" not in x)
        for entity_id in self.outside_sensor_entities:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
                continue

            if sensor.DOMAIN in entity_id:
                return float_value(state.state)

            if weather.DOMAIN in entity_id:
                return float_value(state.attributes.get("temperature"))

        return None

    @property
    def errors(self) -> Errors:
        errors = Errors([Error(self.entity_id, self.error)])
        if self._heating_mode == HEATING_MODE_ECO:
            return errors

        return errors + self.areas.errors

    @property
    def max_error(self) -> Error:
        return max(self.errors, key=lambda error: error.value)

    @property
    def setpoint(self) -> float | None:
        return self._setpoint

    @property
    def requested_setpoint(self) -> float:
        """Get the requested setpoint based on the heating curve and PID output."""
        if self.heating_curve.value is None:
            return MINIMUM_SETPOINT

        return round(max(self.heating_curve.value + self.pid.output, MINIMUM_SETPOINT), 1)

    @property
    def valves_open(self) -> bool:
        """Determine if any of the controlled climates have open valves."""
        # Get the list of all controlled climates
        climates = self._radiators + self.areas.items()

        # If there are no radiators, we can safely assume the valves are open
        if len(self._radiators) == 0:
            return True

        # Iterate through each controlled thermostat
        for climate in climates:
            # Get the current state of the thermostat
            state = self.hass.states.get(climate)

            # If the thermostat is unavailable or has an unknown state, skip it
            if state is None or state.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
                continue

            # If the thermostat is turned off, skip it
            if state.state == HVACMode.OFF:
                continue

            # If the thermostat reports that it is heating, we can assume the valves are open
            if state.attributes.get("hvac_action") == HVACAction.HEATING:
                return True

            # If the thermostat does not support hvac action, we can assume the valves are
            # open if the current temperature is not at the target temperature
            if state.attributes.get("hvac_action") is None:
                target_temperature = float_value(state.attributes.get("temperature"))
                current_temperature = float_value(state.attributes.get("current_temperature"))

                # If there is a current temperature, and it is not at the target temperature,
                # we can assume the valves are open
                if (
                    current_temperature is not None
                    and target_temperature is not None
                    and target_temperature >= current_temperature + self._climate_valve_offset
                ):
                    return True

        # If none of the thermostats have open valves, return False
        return False

    @property
    def pulse_width_modulation_enabled(self) -> bool:
        """Return True if pulse width modulation is enabled, False otherwise."""
        if not self._coordinator.supports_setpoint_management or self._force_pulse_width_modulation:
            return True

        if not self._overshoot_protection or self._calculated_setpoint is None:
            return False

        if not self._dynamic_minimum_setpoint:
            return self._coordinator.minimum_setpoint > self._calculated_setpoint

        if self._minimum_setpoint_version == DYNAMIC_MINIMUM_SETPOINT_VERSION_1:
            return self.minimum_setpoint_value > self._calculated_setpoint

        return self.pwm.enabled

    @property
    def control_problems(self) -> list[str]:
        """Return why SAT is not controlling the boiler while it should be."""
        if (
            self.hvac_mode != HVACMode.HEAT
            or self.control_paused
            or self.hass.state is not CoreState.running
        ):
            return []

        problems = []
        if not self._coordinator.online:
            problems.append("gateway_offline")

        if (
            self.current_temperature is None
            or self.target_temperature is None
            or self.current_outside_temperature is None
        ):
            problems.append("inputs_missing")

        if (
            self._coordinator.boiler_temperature is None
            and not self._coordinator.awaiting_boiler_data
        ):
            problems.append("boiler_data_missing")

        if not problems and dt_util.utcnow() - self._last_control_at > CONTROL_LOOP_STALL_TIME:
            problems.append("control_loop_stalled")

        return problems

    @property
    def relative_modulation_value(self) -> int:
        if (
            not self.relative_modulation.enabled
            and self._coordinator.supports_relative_modulation_management
        ):
            return MINIMUM_RELATIVE_MODULATION

        return self._maximum_relative_modulation

    @property
    def relative_modulation_state(self) -> RelativeModulationState:
        return self.relative_modulation.state

    @property
    def minimum_setpoint_value(self) -> float:
        """Get the minimum allowable setpoint temperature."""
        if self._dynamic_minimum_setpoint:
            # Version 1: Use MinimumSetpoint controller
            if self._minimum_setpoint_version == DYNAMIC_MINIMUM_SETPOINT_VERSION_1:
                return max(self.minimum_setpoint.current, self._coordinator.minimum_setpoint)

            # Version 2: Use Pulse Width Modulation controller
            if self._minimum_setpoint_version == DYNAMIC_MINIMUM_SETPOINT_VERSION_2:
                return max(self.pwm.setpoint, self._coordinator.minimum_setpoint)

        # Default to coordinator's minimum setpoint
        return self._coordinator.minimum_setpoint

    def _calculate_control_setpoint(self) -> float:
        """Calculate the control setpoint based on the heating curve and PID output."""
        if self.heating_curve.value is None:
            return MINIMUM_SETPOINT

        return self.requested_setpoint

    async def _async_thermostat_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to the connected thermostat."""
        old_state = event.data.get("old_state")
        if old_state is None or old_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        # A thermostat in off or auto reports no setpoint
        if (temperature := new_state.attributes.get("temperature")) is None:
            return

        if (
            old_state.state != new_state.state
            or old_state.attributes.get("temperature") != temperature
        ):
            _LOGGER.debug("Thermostat State Changed.")
            await self.async_set_target_temperature(temperature, cascade=False)

    async def _async_inside_sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to the inside temperature sensor."""
        self._async_update_sensor_values()

        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        _LOGGER.debug("Inside Sensor Changed.")
        self.async_write_ha_state()

        self._async_control_pid()
        self.schedule_control_heating_loop()

    async def _async_outside_entity_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to the outside entity."""
        if event.data.get("new_state") is None:
            return

        _LOGGER.debug("Outside Sensor Changed.")
        self.async_write_ha_state()

        self._async_control_pid()
        self.schedule_control_heating_loop()

    async def _async_humidity_sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to the inside temperature sensor."""
        self._async_update_sensor_values()

        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        _LOGGER.debug("Humidity Sensor Changed.")
        self.async_write_ha_state()

        self._async_control_pid()
        self.schedule_control_heating_loop()

    async def _async_main_climate_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to the main climate entity."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        if old_state is None or new_state.state != old_state.state:
            _LOGGER.debug("Main Climate State Changed (%s).", new_state.entity_id)
            self.schedule_control_heating_loop()

    async def _async_climate_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to the climate entity.

        If the state, target temperature, or current temperature of the climate
        entity has changed, update the PID controller and heating control.
        """
        # Get the new state of the climate entity
        new_state = event.data.get("new_state")

        # Return if the new state is not available
        if not new_state:
            return

        # Get the old state of the climate entity
        old_state = event.data.get("old_state")

        # Get the attributes of the new state
        new_attrs = new_state.attributes

        # Get the attributes of the old state, if available
        old_attrs = old_state.attributes if old_state else {}

        _LOGGER.debug("Climate State Changed (%s).", new_state.entity_id)

        # Check if the last state is None, so we can track the attached sensor if needed
        if old_state is None and (sensor_temperature_id := new_attrs.get(SENSOR_TEMPERATURE_ID)):
            self.async_track_sensor_temperature(sensor_temperature_id)

        # If the state has changed or the old state is not available, update the PID controller
        if (
            not old_state
            or new_state.state != old_state.state
            or new_attrs.get("temperature") != old_attrs.get("temperature")
        ):
            self._async_control_pid(reset=True)

        # If the current temperature has changed, update the PID controller
        elif SENSOR_TEMPERATURE_ID not in new_attrs and new_attrs.get(
            "current_temperature"
        ) != old_attrs.get("current_temperature"):
            self._async_control_pid()

        # A synced preset writes its own temperature to the rooms,
        # which must not replace the home targets
        if (
            self._rooms is not None
            and (
                new_state.entity_id not in self._rooms
                or not self._sync_climates_with_preset
                or self.preset_mode == PRESET_HOME
            )
            and (target_temperature := new_attrs.get("temperature"))
        ):
            self._rooms[new_state.entity_id] = float(target_temperature)

        # Update the heating control
        self.schedule_control_heating_loop()

    async def _async_temperature_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to the climate sensor entity."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        _LOGGER.debug("Climate Sensor Changed (%s).", new_state.entity_id)
        self._async_control_pid()
        self.schedule_control_heating_loop()

    async def async_window_sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to the contact sensor entity."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        _LOGGER.debug("Window Sensor Changed to %s.", new_state.state)

        if new_state.state == STATE_ON:
            if self.preset_mode == PRESET_ACTIVITY or (
                self._window_sensor_handle is not None and not self._window_sensor_handle.done()
            ):
                return

            try:
                self._window_sensor_handle = asyncio.create_task(
                    asyncio.sleep(self._window_minimum_open_time)
                )
                self._pre_activity_temperature = self.target_temperature or self.min_temp

                await self._window_sensor_handle
                await self.async_set_preset_mode(PRESET_ACTIVITY)
            except asyncio.CancelledError:
                _LOGGER.debug("Window closed before minimum open time.")

            return

        if new_state.state == STATE_OFF:
            if self._window_sensor_handle is not None:
                self._window_sensor_handle.cancel()
                self._window_sensor_handle = None

            if self.preset_mode == PRESET_ACTIVITY:
                _LOGGER.debug("Restoring original target temperature.")
                await self.async_set_temperature(temperature=self._pre_activity_temperature)

            return

    def _async_control_pid(self, *, reset: bool = False) -> None:
        """Control the PID controller."""
        self._async_update_sensor_values()

        # We can't continue if we don't have a valid outside temperature
        if self.current_outside_temperature is None:
            return

        # Reset the PID controller if the sensor data is too old
        if is_state_stale(
            self.hass.states.get(self.inside_sensor_entity_id), self._sensor_max_value_age
        ):
            self.pid.reset()
            self.areas.pids.reset()

        # Calculate the maximum error between the current temperature
        # and the target temperature of all climates
        max_error = self.max_error

        # Make sure we use the latest heating curve value
        if self.target_temperature is not None:
            self.areas.heating_curves.update(self.current_outside_temperature)
            self.heating_curve.update(self.target_temperature, self.current_outside_temperature)

        # Update the PID controller with the maximum error
        if not reset:
            _LOGGER.info(
                "Updating error to %s from %s (Reset: False)", max_error.value, max_error.entity_id
            )

            # Calculate an optimal heating curve when we are in the deadband
            if self.target_temperature is not None and -DEADBAND <= max_error.value <= DEADBAND:
                self.heating_curve.autotune(
                    setpoint=self.requested_setpoint,
                    target_temperature=self.target_temperature,
                    outside_temperature=self.current_outside_temperature,
                )

            # Update our PID controllers if we have valid values
            if self._coordinator.boiler_temperature is not None:
                self.areas.pids.update()

                if self.heating_curve.value is not None:
                    self.pid.update(max_error, self.heating_curve.value)

        elif max_error.value != self.pid.last_error:
            _LOGGER.info(
                "Updating error to %s from %s (Reset: True)", max_error.value, max_error.entity_id
            )

            self.pid.update_reset(error=max_error, heating_curve_value=self.heating_curve.value)
            self._calculated_setpoint = None
            self.pwm.reset()

        self.async_write_ha_state()

    async def _async_control_setpoint(
        self, pwm_state: PWMState, calculated_setpoint: float
    ) -> None:
        """Control the setpoint of the heating system based on the current mode and PWM state."""
        if not self.pulse_width_modulation_enabled or pwm_state.status == PWMStatus.IDLE:
            # Normal cycle without PWM
            self._setpoint = calculated_setpoint
            _LOGGER.info(
                "Pulse Width Modulation is disabled or in IDLE state. Running normal heating cycle."
            )
            _LOGGER.debug("Calculated setpoint for normal cycle: %.1f°C", calculated_setpoint)

            # Some final checks to see if it's even warm, or if any heat can go anywhere
            if self._setpoint < COLD_SETPOINT or not self._valves_open:
                self._setpoint = MINIMUM_SETPOINT
                _LOGGER.debug(
                    "Calculated setpoint is too cold. Setting setpoint to minimum: %.1f°C",
                    MINIMUM_SETPOINT,
                )
        else:
            # PWM is enabled and actively controlling the cycle
            _LOGGER.info("Running PWM cycle with state: %s", pwm_state.status)

            if pwm_state.status == PWMStatus.ON:
                self._setpoint = self.minimum_setpoint_value
                _LOGGER.debug("Setting setpoint to minimum: %.1f°C", self._setpoint)
            else:
                self._setpoint = MINIMUM_SETPOINT
                _LOGGER.debug("Setting setpoint to absolute minimum: %.1f°C", MINIMUM_SETPOINT)

        # Apply the setpoint using the coordinator
        await self._coordinator.async_set_control_setpoint(
            min(self._setpoint, self._coordinator.maximum_setpoint)
        )
        _LOGGER.info("Control setpoint has been updated to: %.1f°C", self._setpoint)

    async def _async_control_relative_modulation(self) -> None:
        """Control the relative modulation value based on the conditions."""
        if not self._coordinator.supports_relative_modulation_management:
            _LOGGER.debug("Relative modulation management is not supported. Skipping control.")
            return

        # Update relative modulation state
        await self.relative_modulation.update(
            pulse_width_modulation_enabled=self.pulse_width_modulation_enabled
        )

        # Retrieve the relative modulation
        relative_modulation_value = self.relative_modulation_value

        # Apply some filters based on the manufacturer
        if isinstance(self._coordinator.manufacturer, Geminox):
            relative_modulation_value = max(10, relative_modulation_value)

        # Determine if the value needs to be updated
        if (
            self._coordinator.control_max_relative_modulation
            == relative_modulation_value
            == self._coordinator.maximum_relative_modulation_value
        ):
            _LOGGER.debug(
                "Relative modulation value unchanged (%d%%). No update necessary.",
                relative_modulation_value,
            )
            return

        await self._coordinator.async_set_control_max_relative_modulation(relative_modulation_value)

    async def _async_update_rooms_from_climates(self) -> None:
        """Update the temperature setpoint for each room based on its climate entity."""
        self._rooms = {}

        # Iterate through each climate entity
        for entity_id in self.areas.items():
            state = self.hass.states.get(entity_id)

            # Skip any entities that are unavailable or have an unknown state
            if not state or state.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
                continue

            # Retrieve the target temperature from the climate entity's attributes
            target_temperature = state.attributes.get("temperature")

            # If the target temperature exists, store it in the _rooms dictionary
            # with the climate entity as the key
            if target_temperature is not None:
                self._rooms[entity_id] = float(target_temperature)

    def reset_control_state(self) -> None:
        """Reset control state when major changes occur."""
        self.pwm.disable()
        self._calculated_setpoint = None

    def async_track_sensor_temperature(self, entity_id: str) -> None:
        """Track the temperature of the sensor specified by the given entity_id.

        If the sensor is already being tracked, the method will return without doing anything.
        Otherwise, it will register a callback for state changes on the specified sensor
        and start tracking its temperature.
        """
        if entity_id in self._sensors:
            return

        self.async_on_remove(
            async_track_state_change_event(self.hass, [entity_id], self._async_temperature_change)
        )

        self._sensors.append(entity_id)

    @callback
    def schedule_control_heating_loop(
        self,
        _time: datetime | None = None,
        *,
        force: bool = False,
    ) -> None:
        """Schedule a debounced execution of the heating control loop."""
        # Force immediate execution
        if force:
            # Cancel previous scheduled run, if any
            if self._control_heating_loop_unsub is not None:
                self._control_heating_loop_unsub()
                self._control_heating_loop_unsub = None

            self.hass.async_create_task(self.async_control_heating_loop())
            return

        # If a run is already scheduled, do nothing.
        if self._control_heating_loop_unsub is not None:
            return

        self._control_heating_loop_unsub = async_call_later(
            self.hass, 10, HassJob(self.async_control_heating_loop)
        )

    async def async_control_heating_loop(self, _time: datetime | None = None) -> None:
        """Control the heating from the current, target and outside temperatures."""
        if self._control_heating_loop_unsub is not None:
            self._control_heating_loop_unsub()
            self._control_heating_loop_unsub = None

        async with self._control_lock:
            self._async_update_sensor_values()

            # No need to do anything if we are not on, or while something else drives the boiler
            if (
                self.hvac_mode != HVACMode.HEAT
                or self.control_paused
                or not self._coordinator.online
            ):
                return

            # A sensor that stops reporting fires no state change,
            # so the stale reset has to happen here
            if is_state_stale(
                self.hass.states.get(self.inside_sensor_entity_id), self._sensor_max_value_age
            ):
                self.pid.reset()
                self.areas.pids.reset()

            if (
                self.current_temperature is None
                or self.target_temperature is None
                or self.current_outside_temperature is None
            ):
                if self._inputs_missing_since is None:
                    self._inputs_missing_since = dt_util.utcnow()

                # The gateway hands the boiler back to the room thermostat
                # when the override is not refreshed
                if (
                    self._setpoint is not None
                    and dt_util.utcnow() - self._inputs_missing_since < INPUTS_MISSING_GRACE_PERIOD
                ):
                    await self._coordinator.async_set_control_setpoint(
                        min(self._setpoint, self._coordinator.maximum_setpoint)
                    )
                elif (
                    dt_util.utcnow() - self._inputs_missing_since >= INPUTS_MISSING_GRACE_PERIOD
                    and not self._inputs_missing_released
                ):
                    _LOGGER.warning(
                        "Inputs missing for %s, handing the boiler back to the room thermostat.",
                        INPUTS_MISSING_GRACE_PERIOD,
                    )
                    self._setpoint = None
                    self._inputs_missing_released = True
                    await self._coordinator.async_release_control()

                return

            self._inputs_missing_since = None
            self._inputs_missing_released = False

            # Control the heating through the coordinator
            await self._coordinator.async_control_heating_loop(pwm_state=self.pwm.state)

            if self._calculated_setpoint is None:
                # Default to the calculated setpoint
                self._calculated_setpoint = self._calculate_control_setpoint()
            else:
                # Apply low filter on the requested setpoint
                self._calculated_setpoint = round(
                    self._alpha * self._calculate_control_setpoint()
                    + (1 - self._alpha) * self._calculated_setpoint,
                    1,
                )

            # Check for overshoot
            if self._coordinator.device_status == BoilerStatus.OVERSHOOT_HANDLING:
                _LOGGER.info("Overshoot Handling detected, enabling Pulse Width Modulation.")
                self.pwm.enable()

            # Without open valves there is no demand,
            # so PWM stays idle and the boiler is held at the minimum setpoint
            if (valves_open := self.valves_open) != self._valves_open:
                if valves_open:
                    _LOGGER.info("Valves opened, resuming the heating cycle.")
                else:
                    _LOGGER.warning(
                        "No valves are open, holding the boiler at the minimum setpoint."
                    )

                self._valves_open = valves_open

            # Pulse Width Modulation
            if not self.pulse_width_modulation_enabled:
                self.pwm.reset()
            elif not valves_open:
                self.pwm.reset()
                self.pwm.enable()
            else:
                await self.pwm.update(
                    flame=self._coordinator.flame,
                    boiler=self._coordinator.boiler,
                    requested_setpoint=self._calculated_setpoint,
                )

            # The next flame-off must be judged against the PWM state after this step
            self._coordinator.update_flame(pwm_state=self.pwm.state)

            # Set the control setpoint to make sure we always stay in control
            await self._async_control_setpoint(self.pwm.state, self._calculated_setpoint)
            self._last_control_at = dt_util.utcnow()

            # Set the relative modulation value, if supported
            await self._async_control_relative_modulation()

            if (
                self._push_setpoint_to_thermostat
                and self._coordinator.control_thermostat_setpoint != self.target_temperature
            ):
                await self._coordinator.async_set_control_thermostat_setpoint(
                    self.target_temperature
                )

            # Apply an error change that arrived inside the sample time,
            # then control the integral (if exceeded the time limit)
            if self._coordinator.boiler_temperature is not None:
                self.areas.pids.update()

            if self.heating_curve.value is not None:
                if self._coordinator.boiler_temperature is not None:
                    self.pid.update(self.max_error, self.heating_curve.value)

                self.pid.update_integral(self.max_error, self.heating_curve.value)

            # Control our areas
            await self.areas.async_control_heating_loops()

            # Control our dynamic minimum setpoint (version 1)
            if not self._coordinator.hot_water_active and self._coordinator.flame_active:
                # Calculate the base return temperature
                if self._coordinator.device_status == BoilerStatus.HEATING_UP:
                    self.minimum_setpoint.warming_up(self._coordinator.boiler)

                # Calculate the dynamic minimum setpoint
                self.minimum_setpoint.calculate(self._coordinator.boiler, self.pwm.status)

            # If the setpoint is high, turn on the heater
            await self.async_set_heater_state(
                DeviceState.ON
                if self._setpoint is not None and self._setpoint > COLD_SETPOINT
                else DeviceState.OFF
            )

            self.async_write_ha_state()

    async def async_set_heater_state(self, state: DeviceState) -> None:
        """Set the heater state, ensuring proper conditions are met."""
        _LOGGER.debug("Attempting to set heater state to: %s", state)

        if state == DeviceState.ON:
            if not self.valves_open:
                if self._coordinator.device_active:
                    _LOGGER.info("Valves closed while heater active. Turning off.")
                    await self._coordinator.async_set_heater_state(DeviceState.OFF)
                else:
                    _LOGGER.warning("Cannot turn on heater: no valves are open.")
                return

            if self._coordinator.device_active:
                _LOGGER.info("Heater is already active. No action taken.")
                return

        elif not self._coordinator.device_active:
            _LOGGER.info("Heater is already off. No action taken.")
            return

        await self._coordinator.async_set_heater_state(state)

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return None

        # Automatically select the preset
        for preset in self._presets:
            if float(self._presets[preset]) == float(temperature):
                return await self.async_set_preset_mode(preset)

        self._attr_preset_mode = PRESET_NONE
        return await self.async_set_target_temperature(temperature)

    @override
    async def async_set_hvac_mode(self, hvac_mode: HVACMode, cascade: bool = True) -> None:
        """Set the heating/cooling mode for the devices and update the state."""
        # Only allow the hvac mode to be set to heat or off
        if hvac_mode == HVACMode.HEAT:
            if self.hvac_mode != HVACMode.HEAT:
                self._last_control_at = dt_util.utcnow()

            self._attr_hvac_mode = HVACMode.HEAT
        elif hvac_mode == HVACMode.OFF:
            await self.async_cancel_calibration()
            async with self._control_lock:
                self._attr_hvac_mode = HVACMode.OFF
                await self.async_set_heater_state(DeviceState.OFF)
                await self._coordinator.async_release_control()
        else:
            # If an unsupported mode is passed, log an error message
            _LOGGER.error("Unrecognized hvac mode: %s", hvac_mode)
            return

        # Reset the PID controller
        self._async_control_pid(reset=True)

        # Reset the climate
        self.reset_control_state()

        # Collect which climates to control
        climates = self._radiators[:]
        if self._sync_climates_with_mode:
            climates += self.areas.items()

        if cascade:
            # Set the hvac mode for those climate devices
            for entity_id in climates:
                state = self.hass.states.get(entity_id)
                if state is None or hvac_mode not in state.attributes.get("hvac_modes", []):
                    continue

                data = {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: hvac_mode}
                await self.hass.services.async_call(
                    CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE, data, blocking=True
                )

        # Update the state and control the heating
        self.async_write_ha_state()
        self.schedule_control_heating_loop()

    @override
    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode for the thermostat."""
        # Check if the given preset mode is valid
        if self.preset_modes is None or preset_mode not in self.preset_modes:
            msg = f"Got unsupported preset_mode {preset_mode}. Must be one of {self.preset_modes}"
            raise ValueError(msg)

        # Return if the given preset mode is already set
        if preset_mode == self._attr_preset_mode:
            return

        # Reset the preset mode if `PRESET_NONE` is given
        if preset_mode == PRESET_NONE:
            self._attr_preset_mode = PRESET_NONE
            if self._pre_custom_temperature is None:
                self.async_write_ha_state()
            else:
                await self.async_set_target_temperature(self._pre_custom_temperature)

        else:
            # Save the current target temperature if the preset mode is being set for the first time
            if self._attr_preset_mode == PRESET_NONE:
                self._pre_custom_temperature = self.target_temperature

            # Set the preset mode and target temperature
            self._attr_preset_mode = preset_mode
            await self.async_set_target_temperature(self._presets[preset_mode])

            # Set the temperature for each room, when enabled
            if self._sync_climates_with_preset:
                for entity_id in self.areas.items():
                    state = self.hass.states.get(entity_id)
                    if state is None or state.state == HVACMode.OFF:
                        continue

                    if preset_mode != PRESET_HOME or self._rooms is None:
                        target_temperature = self._presets[preset_mode]
                    else:
                        target_temperature = self._rooms.get(entity_id, self._presets[preset_mode])

                    data = {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: target_temperature}
                    await self.hass.services.async_call(
                        CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True
                    )

    async def async_set_target_temperature(
        self, temperature: float, *, cascade: bool = True
    ) -> None:
        """Set the temperature setpoint for all main climates."""
        if self.target_temperature == temperature:
            return

        # Set the new target temperature
        self._attr_target_temperature = temperature

        if cascade:
            # Set the target temperature for each main climate
            for entity_id in self._radiators:
                data = {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: temperature}
                await self.hass.services.async_call(
                    CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True
                )

            # Set the target temperature for the connected boiler
            if self._push_setpoint_to_thermostat:
                await self._coordinator.async_set_control_thermostat_setpoint(temperature)

        # Reset the PID controller
        self._async_control_pid(reset=True)

        # Reset the climate
        self.reset_control_state()

        # Write the state to Home Assistant
        self.async_write_ha_state()

        # Control the heating based on the new temperature setpoint
        self.schedule_control_heating_loop(force=True)

    def _get_entity_state_float(self, entity_id: str) -> float | None:
        """Return state if available and valid."""
        if (entity := self.hass.states.get(entity_id)) is None:
            return None

        sensor_max_value_age = self._sensor_max_value_age

        if is_state_stale(entity, sensor_max_value_age):
            _LOGGER.debug(
                "Sensor %s stale for %s (age=%.1fs > %.1fs)",
                entity_id,
                self.entity_id,
                state_age_seconds(entity),
                sensor_max_value_age,
            )
            return None

        if entity.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            return None

        return float_value(entity.state)
