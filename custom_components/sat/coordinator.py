from __future__ import annotations

import logging
from abc import abstractmethod
from enum import StrEnum
from time import monotonic
from typing import TYPE_CHECKING, Any, TypedDict, override

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HassJob, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .boiler import STABILIZATION_MARGIN, STABLE_DERIVATIVE, BoilerState, BoilerTemperatureTracker
from .const import (
    BOILER_DEADBAND,
    CONF_HEATING_SYSTEM,
    CONF_MANUFACTURER,
    CONF_MINIMUM_SETPOINT,
    CONF_SIMULATION,
    CONTROL_LOOP_STALL_TIME,
    DOMAIN,
    HEATING_SYSTEM_UNKNOWN,
    MAX_BOILER_TEMPERATURE_AGE,
    MINIMUM_SETPOINT,
    BoilerStatus,
)
from .flame import Flame, FlameState
from .helpers import configured_maximum_setpoint, seconds_since
from .manufacturer import Manufacturer, ManufacturerFactory
from .manufacturers.geminox import Geminox
from .manufacturers.ideal import Ideal
from .manufacturers.intergas import Intergas
from .manufacturers.nefit import Nefit

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from homeassistant.helpers.service_info.mqtt import ReceivePayloadType

    from .pwm import PWMState

_LOGGER: logging.Logger = logging.getLogger(__name__)


class DeviceState(StrEnum):
    ON = "on"
    OFF = "off"


class MqttMessage(TypedDict):
    received: str
    topic: str
    payload: ReceivePayloadType


class SatData(dict[str, Any]):
    _is_dirty: bool = False

    @override
    def __setitem__(self, key: str, value: object) -> None:
        if self.get(key) != value:
            self._is_dirty = True

        super().__setitem__(key, value)

    @override
    def __delitem__(self, key: str) -> None:
        self._is_dirty = True
        super().__delitem__(key)

    def reset_dirty(self) -> None:
        self._is_dirty = False

    def is_dirty(self) -> bool:
        return self._is_dirty


class SatDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(
        self,
        hass: HomeAssistant,
        config_data: Mapping[str, Any],
        options: Mapping[str, Any] | None = None,
    ) -> None:
        """Initialize."""
        super().__init__(hass, _LOGGER, name=DOMAIN)
        self.data: SatData = SatData()

        self._boiler_temperature_cold: float | None = None
        self._boiler_temperatures: list[tuple[float, float]] = []
        self._boiler_temperature_tracker: BoilerTemperatureTracker = BoilerTemperatureTracker()

        self._flame: Flame = Flame()
        self._created_at: float = monotonic()
        self._device_on_since: float | None = None
        self._listeners_unsub: Callable[[], None] | None = None

        self._options: Mapping[str, Any] = options or {}
        self._config_data: Mapping[str, Any] = config_data

        self._manufacturer: Manufacturer | None = None
        self._control_max_relative_modulation: int | None = None
        self._control_thermostat_setpoint: float | None = None
        self._simulation: bool = bool(self._options.get(CONF_SIMULATION))
        self._heating_system: str = str(
            config_data.get(CONF_HEATING_SYSTEM, HEATING_SYSTEM_UNKNOWN)
        )

        if (manufacturer := config_data.get(CONF_MANUFACTURER)) is not None:
            self._manufacturer = ManufacturerFactory.resolve_by_name(manufacturer)

        self.async_add_listener(lambda: self._flame.update(boiler_state=self.boiler))

    @property
    @abstractmethod
    def device_id(self) -> str:
        pass

    @property
    @abstractmethod
    def device_type(self) -> str:
        pass

    @property
    def messages(self) -> list[MqttMessage]:
        return []

    @property
    def online(self) -> bool:
        """Return whether the device reports itself as reachable."""
        return True

    @property
    def awaiting_boiler_data(self) -> bool:
        """Return True while the first boiler values after setup are still on their way."""
        return (
            self.online
            and self.boiler_temperature is None
            and seconds_since(self._created_at) < CONTROL_LOOP_STALL_TIME.total_seconds()
        )

    @property
    def device_status(self) -> BoilerStatus:
        """Return the current status of the device."""
        if self.boiler_temperature is None:
            return BoilerStatus.INSUFFICIENT_DATA

        if self.hot_water_active:
            return BoilerStatus.HOT_WATER

        if self.setpoint is None or self.setpoint <= MINIMUM_SETPOINT:
            return BoilerStatus.IDLE

        if (
            self.device_active
            and self.boiler_temperature_cold is not None
            and self.boiler_temperature_cold > self.boiler_temperature
        ):
            if (
                self.boiler_temperature_derivative is not None
                and self.boiler_temperature_derivative < STABLE_DERIVATIVE
            ):
                return BoilerStatus.PUMP_STARTING

            if self._boiler_temperature_tracker.active and self.setpoint > self.boiler_temperature:
                return BoilerStatus.PREHEATING

        # We assume space-heating mode with a valid setpoint.
        delta = self.boiler_temperature - self.setpoint

        # Below the setpoint
        if delta < -BOILER_DEADBAND:
            if self.flame_active:
                if self._boiler_temperature_tracker.active:
                    return BoilerStatus.HEATING_UP

                return BoilerStatus.BELOW_SETPOINT
            return BoilerStatus.WAITING_FOR_FLAME

        # Near/at the setpoint
        if abs(delta) <= BOILER_DEADBAND:
            return BoilerStatus.AT_SETPOINT

        # Above the setpoint
        if delta > BOILER_DEADBAND:
            if self.flame_active:
                if self._boiler_temperature_tracker.active:
                    # Way above setpoint -> cooling down.
                    if delta > STABILIZATION_MARGIN:
                        return BoilerStatus.COOLING_DOWN

                    # Slightly above the setpoint while still burning -> stabilizing.
                    return BoilerStatus.NEAR_SETPOINT

                # Flame still on, but tracker inactive. We are over setpoint -> overshoot handling.
                return BoilerStatus.OVERSHOOT_HANDLING

            # Most modern boilers are in anti-cycling here.
            return BoilerStatus.ANTI_CYCLING

        return BoilerStatus.INSUFFICIENT_DATA

    @property
    def boiler(self) -> BoilerState:
        return BoilerState(
            flame_active=self.flame_active,
            hot_water_active=self.hot_water_active,
            status=self.device_status,
            is_active=self.device_active,
            is_inactive=not self.device_active,
            setpoint=self.setpoint,
            flow_temperature=self.boiler_temperature,
            return_temperature=self.return_temperature,
            relative_modulation_level=self.relative_modulation_value,
        )

    @property
    def flame(self) -> FlameState:
        return FlameState(
            is_active=self._flame.is_active,
            is_inactive=self._flame.is_inactive,
            health_status=self._flame.health_status,
            latest_on_time_seconds=self._flame.latest_on_time_seconds,
            average_on_time_seconds=self._flame.average_on_time_seconds,
            last_cycle_duration_seconds=self._flame.last_cycle_duration_seconds,
            sample_count_4h=self._flame.sample_count_4h,
            cycles_last_hour=self._flame.cycles_last_hour,
            duty_ratio_last_15m=self._flame.duty_ratio_last_15m,
            median_on_duration_seconds_4h=self._flame.median_on_duration_seconds_4h,
            last_cut_short_peak_flow=self._flame.last_cut_short_peak_flow,
            last_cut_short_setpoint=self._flame.last_cut_short_setpoint,
            cut_short_burn_ends=self._flame.cut_short_burn_ends,
        )

    @property
    def manufacturer(self) -> Manufacturer | None:
        return self._manufacturer

    @property
    @abstractmethod
    def setpoint(self) -> float | None:
        pass

    @property
    @abstractmethod
    def device_active(self) -> bool:
        pass

    @property
    @abstractmethod
    def member_id(self) -> int | None:
        pass

    @property
    def flame_active(self) -> bool:
        return self.device_active

    @property
    def hot_water_active(self) -> bool:
        return False

    @property
    def hot_water_setpoint(self) -> float | None:
        return None

    @property
    def fault_active(self) -> bool:
        return False

    @property
    def fault_code(self) -> int | None:
        return None

    @property
    def boiler_temperature(self) -> float | None:
        return None

    @property
    def return_temperature(self) -> float | None:
        return None

    @property
    def boiler_temperature_derivative(self) -> float | None:
        if len(self._boiler_temperatures) <= 1:
            return None

        first_time, first_temperature = self._boiler_temperatures[-2]
        last_time, last_temperature = self._boiler_temperatures[-1]

        time_delta = last_time - first_time
        if time_delta <= 0:
            return None

        return (last_temperature - first_temperature) / time_delta

    @property
    def boiler_temperature_cold(self) -> float | None:
        return self._boiler_temperature_cold

    @property
    def boiler_temperature_tracking(self) -> bool:
        return self._boiler_temperature_tracker.active

    @property
    def minimum_hot_water_setpoint(self) -> float:
        return 30

    @property
    def maximum_hot_water_setpoint(self) -> float:
        return 60

    @property
    def relative_modulation_value(self) -> float | None:
        return None

    @property
    def maximum_setpoint_value(self) -> float | None:
        return self.maximum_setpoint

    @property
    def boiler_capacity(self) -> float | None:
        return None

    @property
    def minimum_boiler_capacity(self) -> float | None:
        if (minimum_relative_modulation_value := self.minimum_relative_modulation_value) is None:
            return None

        if (boiler_capacity := self.boiler_capacity) is None:
            return None

        if boiler_capacity == 0:
            return 0

        return boiler_capacity * (minimum_relative_modulation_value / 100)

    @property
    def boiler_power(self) -> float | None:
        if (boiler_capacity := self.boiler_capacity) is None:
            return None

        if (minimum_boiler_capacity := self.minimum_boiler_capacity) is None:
            return None

        if (relative_modulation_value := self.relative_modulation_value) is None:
            return None

        if not self.flame_active:
            return 0

        return minimum_boiler_capacity + (
            (boiler_capacity - minimum_boiler_capacity) * (relative_modulation_value / 100)
        )

    @property
    def minimum_relative_modulation_value(self) -> float | None:
        return None

    @property
    def maximum_relative_modulation_value(self) -> float | None:
        return None

    @property
    def control_max_relative_modulation(self) -> int | None:
        """Return the maximum relative modulation SAT sent since it last took control."""
        return self._control_max_relative_modulation

    @property
    def control_thermostat_setpoint(self) -> float | None:
        """Return the thermostat setpoint SAT sent since it last took control."""
        return self._control_thermostat_setpoint

    @property
    def minimum_setpoint(self) -> float:
        """Return the minimum setpoint temperature before the device starts to overshoot."""
        return float(
            self._options.get(CONF_MINIMUM_SETPOINT, self._config_data.get(CONF_MINIMUM_SETPOINT))
        )

    @property
    def maximum_setpoint(self) -> float:
        """Return the maximum setpoint temperature that the device can support."""
        return configured_maximum_setpoint(self._options, self._heating_system)

    @property
    def supports_setpoint_management(self) -> bool:
        """Return whether the device supports setting a boiler setpoint.

        This property is used to determine whether the coordinator can send a setpoint to the
        device. If a device doesn't support setpoint management, the coordinator won't be able
        to control the temperature.
        """
        return False

    @property
    def supports_hot_water_setpoint_management(self) -> bool:
        """Return whether the device supports setting a hot water setpoint.

        This property is used to determine whether the coordinator can send a setpoint to the
        device. If a device doesn't support setpoint management, the coordinator won't be able
        to control the temperature.
        """
        return False

    @property
    def supports_relative_modulation(self) -> bool:
        """Return whether the device supports having relative modulation value.

        This property is used to determine whether the coordinator can retrieve the relative
        modulation value from the device. If a device doesn't support the relative modulation
        value, the coordinator won't be able to retrieve the value.
        """
        return not isinstance(self.manufacturer, (Ideal, Intergas, Geminox, Nefit))

    @property
    def supports_relative_modulation_management(self) -> bool:
        """Return whether the device supports setting a relative modulation value.

        This property is used to determine whether the coordinator can send a relative
        modulation value to the device. If a device doesn't support relative modulation
        management, the coordinator won't be able to control the value.
        """
        return True

    @property
    def supports_maximum_setpoint_management(self) -> bool:
        """Return whether the device supports setting a maximum setpoint.

        This property is used to determine whether the coordinator can send a maximum setpoint
        to the device. If a device doesn't support maximum setpoint management, the coordinator
        won't be able to control the value.
        """
        return False

    async def async_setup(self) -> None:
        """Perform setup when the integration is about to be added to Home Assistant."""

    async def async_added_to_hass(self) -> None:
        """Perform setup when the integration is added to Home Assistant."""
        await self.async_set_control_max_setpoint(self.maximum_setpoint)

    async def async_will_remove_from_hass(self) -> None:
        """Run when an entity is removed from hass."""
        if self._listeners_unsub is not None:
            self._listeners_unsub()
            self._listeners_unsub = None

        await self.async_shutdown()

    async def async_initialize_flame(self, entry_id: str) -> None:
        await self._flame.async_initialize(self.hass, entry_id)

    def update_flame(self, pwm_state: PWMState) -> None:
        self._flame.update(boiler_state=self.boiler, pwm_state=pwm_state)

    async def async_control_heating_loop(
        self, pwm_state: PWMState | None = None, _time: datetime | None = None
    ) -> None:
        """Control the heating loop for the device."""
        # Update Flame State
        self._flame.update(boiler_state=self.boiler, pwm_state=pwm_state)

        # Update Device State
        if not self.device_active:
            self._device_on_since = None
        elif self._device_on_since is None:
            self._device_on_since = monotonic()

        # See if we can determine the manufacturer (deprecated)
        if self._manufacturer is None and self.member_id is not None:
            manufacturers = ManufacturerFactory.resolve_by_member_id(self.member_id)
            self._manufacturer = manufacturers[0] if len(manufacturers) > 0 else None

        # Nothing further to do without the temperature
        if self.boiler_temperature is None:
            return

        # Handle the temperature tracker
        if (
            self.setpoint is not None
            and self.boiler_temperature_derivative is not None
            and self.device_status is not BoilerStatus.HOT_WATER
        ):
            self._boiler_temperature_tracker.update(
                flame_active=self.flame_active,
                setpoint=round(self.setpoint, 0),
                boiler_temperature=round(self.boiler_temperature, 0),
                boiler_temperature_derivative=self.boiler_temperature_derivative,
            )

        # Append current boiler temperature if valid and unique
        current_time = monotonic()
        if not self._boiler_temperatures or self._boiler_temperatures[-1][0] != current_time:
            self._boiler_temperatures.append((current_time, self.boiler_temperature))

        # Remove old temperature records beyond the allowed age
        self._boiler_temperatures = [
            (timestamp, temperature)
            for timestamp, temperature in self._boiler_temperatures
            if seconds_since(timestamp) <= MAX_BOILER_TEMPERATURE_AGE
        ]

        # Update the cold temperature of the boiler
        if (boiler_temperature_cold := self._get_latest_boiler_cold_temperature()) is not None:
            self._boiler_temperature_cold = boiler_temperature_cold
        elif self._boiler_temperature_cold is not None:
            self._boiler_temperature_cold = min(
                self.boiler_temperature, self._boiler_temperature_cold
            )

    async def async_set_heater_state(self, state: DeviceState) -> None:
        """Set the state of the device heater."""
        _LOGGER.info("Set central heater state %s", state)

    async def async_set_control_setpoint(self, value: float) -> None:
        """Control the boiler setpoint temperature for the device."""
        if self.supports_setpoint_management:
            _LOGGER.info("Set control boiler setpoint to %d°C", value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        """Control the DHW setpoint temperature for the device."""
        if self.supports_hot_water_setpoint_management:
            _LOGGER.info("Set control hot water setpoint to %d°C", value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        """Control the maximum setpoint temperature for the device."""
        if self.supports_maximum_setpoint_management:
            _LOGGER.info("Set maximum setpoint to %d°C", value)

    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        """Control the maximum relative modulation for the device."""
        if self.supports_relative_modulation_management:
            _LOGGER.info("Set maximum relative modulation to %d%%", value)

        self._control_max_relative_modulation = value

    async def async_set_control_thermostat_setpoint(self, value: float) -> None:
        """Control the setpoint temperature for the thermostat."""
        self._control_thermostat_setpoint = value

    async def async_release_control(self) -> None:
        """Hand boiler control back to the room thermostat."""
        self._control_max_relative_modulation = None
        self._control_thermostat_setpoint = None

    async def async_notify_listeners(self, _time: datetime | None = None) -> None:
        """Notify listeners of an update asynchronously."""
        # Make sure we do not spam
        self._async_unsub_refresh()
        self._debounced_refresh.async_cancel()

        if self._listeners_unsub is not None:
            self._listeners_unsub()
            self._listeners_unsub = None

        # Inform the listeners that we are updated
        self.async_update_listeners()

    @override
    @callback
    def async_set_updated_data(self, data: Mapping[str, Any]) -> None:
        """Update the stored data and notify listeners if changes are detected."""
        for key, value in data.items():
            self.data[key] = value

        if self.data.is_dirty():
            # Confirm that we've taken care of the changes
            self.data.reset_dirty()

            # A scheduled run reads the latest data, so a busy bus still notifies every 5 seconds
            if self._listeners_unsub is None:
                self._listeners_unsub = async_call_later(
                    self.hass, 5, HassJob(self.async_notify_listeners)
                )

    def _get_latest_boiler_cold_temperature(self) -> float | None:
        """Get the latest boiler cold temperature based on recent boiler temperatures."""
        max_temperature = None

        for timestamp, temperature in self._boiler_temperatures:
            is_before_device_on = self._device_on_since is None or timestamp < self._device_on_since
            is_before_flame_on = self._flame.on_since is None or timestamp < self._flame.on_since

            if is_before_device_on and is_before_flame_on:
                max_temperature = (
                    max(max_temperature, temperature)
                    if max_temperature is not None
                    else temperature
                )

        return max_temperature


class SatEntityCoordinator(DataUpdateCoordinator):
    def get(self, domain: str, key: str) -> str | None:
        """Get the value for the given `key` from the boiler data.

        :param domain: Domain of where this value is located.
        :param key: Key of the value to retrieve from the boiler data.
        :return: Value for the given key from the boiler data, or None if the boiler data or the
            value are not available.
        """
        entity_id = self._get_entity_id(domain, key)
        if entity_id is None:
            return None

        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None

        return state.state

    @abstractmethod
    def _get_entity_id(self, domain: str, key: str) -> str | None:
        pass
