from __future__ import annotations

import logging
import typing

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTemperature, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MODE, MODE_MQTT_OPENTHERM, MODE_SERIAL, MODE_SIMULATOR, CONF_MINIMUM_CONSUMPTION, CONF_MAXIMUM_CONSUMPTION
from .coordinator import SatDataUpdateCoordinator
from .entity import SatEntity, SatClimateEntity
from .serial import sensor as serial_sensor
from .simulator import sensor as simulator_sensor

if typing.TYPE_CHECKING:
    pass

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(_hass: HomeAssistant, _config_entry: ConfigEntry, _async_add_entities: AddEntitiesCallback):
    """
    Add sensors for the serial protocol if the integration is set to use it.
    """
    climate = _config_entry.runtime_data.climate
    coordinator = _config_entry.runtime_data.coordinator

    # Check if integration is set to use the serial protocol
    if _config_entry.data.get(CONF_MODE) == MODE_SERIAL:
        await serial_sensor.async_setup_entry(_hass, _config_entry, _async_add_entities)

    # Check if integration is set to use the simulator
    if _config_entry.data.get(CONF_MODE) == MODE_SIMULATOR:
        await simulator_sensor.async_setup_entry(_hass, _config_entry, _async_add_entities)

    if _config_entry.data.get(CONF_MODE) == MODE_MQTT_OPENTHERM:
        _async_add_entities([SatBoilerFaultCodeSensor(coordinator, _config_entry)])

    _async_add_entities([
        SatFlameSensor(coordinator, _config_entry),
        SatBoilerSensor(coordinator, _config_entry),
        SatManufacturerSensor(coordinator, _config_entry),
        SatErrorValueSensor(coordinator, _config_entry, climate),
        SatHeatingCurveSensor(coordinator, _config_entry, climate),
    ])

    if coordinator.supports_relative_modulation_management:
        _async_add_entities([SatCurrentPowerSensor(coordinator, _config_entry)])

        if float(_config_entry.options.get(CONF_MINIMUM_CONSUMPTION) or 0) > 0 and float(_config_entry.options.get(CONF_MAXIMUM_CONSUMPTION) or 0) > 0:
            _async_add_entities([SatCurrentConsumptionSensor(coordinator, _config_entry)])


class SatCurrentPowerSensor(SatEntity, SensorEntity):
    _attr_translation_key = "boiler_power"


    @property
    def device_class(self):
        """Return the device class."""
        return SensorDeviceClass.POWER

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfPower.KILO_WATT

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._coordinator.boiler_power is not None

    @property
    def native_value(self) -> float:
        """Return the state of the device in native units.

        In this case, the state represents the current power of the boiler in kW.
        """
        return self._coordinator.boiler_power

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.entry_id}-boiler-current-power"


class SatCurrentConsumptionSensor(SatEntity, SensorEntity):
    _attr_translation_key = "boiler_consumption"

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry):
        super().__init__(coordinator, config_entry)

        self._minimum_consumption = self._config_entry.options.get(CONF_MINIMUM_CONSUMPTION)
        self._maximum_consumption = self._config_entry.options.get(CONF_MAXIMUM_CONSUMPTION)


    @property
    def device_class(self):
        """Return the device class."""
        return SensorDeviceClass.GAS

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfVolume.CUBIC_METERS

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._coordinator.relative_modulation_value is not None

    @property
    def native_value(self) -> float:
        """Return the state of the device in native units.

        In this case, the state represents the current consumption of the boiler in m³/h.
        """

        if not self._coordinator.device_active:
            return 0

        if not self._coordinator.flame_active:
            return 0

        differential_gas_consumption = self._maximum_consumption - self._minimum_consumption
        relative_modulation_value = self._coordinator.relative_modulation_value

        return round(self._minimum_consumption + ((relative_modulation_value / 100) * differential_gas_consumption), 3)

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.entry_id}-boiler-current-consumption"


class SatHeatingCurveSensor(SatClimateEntity, SensorEntity):
    _attr_translation_key = "heating_curve"


    @property
    def device_class(self):
        """Return the device class."""
        return SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def available(self):
        """Return availability of the sensor."""
        return self.climate_added and self._climate.heating_curve.value is not None

    @property
    def native_value(self) -> float:
        """Return the state of the device in native units.

        In this case, the state represents the current heating curve value.
        """
        return self._climate.heating_curve.value

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.entry_id}-heating-curve"


class SatErrorValueSensor(SatClimateEntity, SensorEntity):
    _attr_translation_key = "error_value"


    @property
    def device_class(self):
        """Return the device class."""
        return SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def available(self):
        """Return availability of the sensor."""
        return self.climate_added

    @property
    def native_value(self) -> float:
        """Return the state of the device in native units.

        In this case, the state represents the current error value.
        """
        return self._climate.max_error.value

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.entry_id}-error-value"


class SatManufacturerSensor(SatEntity, SensorEntity):
    _attr_translation_key = "boiler_manufacturer"

    @property
    def native_value(self) -> str:
        manufacturer = self._coordinator.manufacturer
        return manufacturer.friendly_name if manufacturer is not None else None

    @property
    def available(self) -> bool:
        return self._coordinator.manufacturer is not None

    @property
    def unique_id(self) -> str:
        return f"{self._config_entry.entry_id}-manufacturer"


class SatFlameSensor(SatEntity, SensorEntity):
    _attr_translation_key = "flame_status"

    @property
    def native_value(self) -> str:
        return self._coordinator.flame.health_status.name

    @property
    def available(self) -> bool:
        return self._coordinator.flame.health_status is not None

    @property
    def unique_id(self) -> str:
        return f"{self._config_entry.entry_id}-flame-status"


class SatBoilerSensor(SatEntity, SensorEntity):
    _attr_translation_key = "boiler_status"

    @property
    def native_value(self) -> str:
        return self._coordinator.device_status.name

    @property
    def available(self) -> bool:
        return self._coordinator.device_status is not None

    @property
    def unique_id(self) -> str:
        return f"{self._config_entry.entry_id}-boiler-status"


class SatBoilerFaultCodeSensor(SatEntity, SensorEntity):
    _attr_translation_key = "boiler_fault_code"

    @property
    def native_value(self) -> int | None:
        return self._coordinator.fault_code

    @property
    def available(self) -> bool:
        return self._coordinator.fault_code is not None

    @property
    def unique_id(self) -> str:
        return f"{self._config_entry.entry_id}-boiler-fault-code"
