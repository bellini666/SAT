from __future__ import annotations

import logging
from typing import TYPE_CHECKING, override

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfPower, UnitOfTemperature, UnitOfVolumeFlowRate

from .const import (
    CONF_MAXIMUM_CONSUMPTION,
    CONF_MINIMUM_CONSUMPTION,
    CONF_MODE,
    MODE_MQTT_OPENTHERM,
)
from .entity import SatClimateEntity, SatEntity
from .serial import SatSerialCoordinator
from .serial import sensor as serial_sensor
from .simulator import SatSimulatorCoordinator
from .simulator import sensor as simulator_sensor

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import SatConfigEntry
    from .climate import SatClimate
    from .coordinator import SatDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(
    _hass: HomeAssistant,
    _config_entry: SatConfigEntry,
    _async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add sensors for the serial protocol if the integration is set to use it."""
    climate = _config_entry.runtime_data.climate
    coordinator = _config_entry.runtime_data.coordinator

    if isinstance(coordinator, SatSerialCoordinator):
        await serial_sensor.async_setup_entry(coordinator, _config_entry, _async_add_entities)

    if isinstance(coordinator, SatSimulatorCoordinator):
        await simulator_sensor.async_setup_entry(coordinator, _config_entry, _async_add_entities)

    if _config_entry.data.get(CONF_MODE) == MODE_MQTT_OPENTHERM:
        _async_add_entities([SatBoilerFaultCodeSensor(coordinator, _config_entry)])

    _async_add_entities(
        [
            SatFlameSensor(coordinator, _config_entry),
            SatBoilerSensor(coordinator, _config_entry),
            SatManufacturerSensor(coordinator, _config_entry),
            SatErrorValueSensor(coordinator, _config_entry, climate),
            SatHeatingCurveSensor(coordinator, _config_entry, climate),
        ]
    )

    if coordinator.supports_relative_modulation_management:
        _async_add_entities([SatCurrentPowerSensor(coordinator, _config_entry)])

        if (
            float(_config_entry.options.get(CONF_MINIMUM_CONSUMPTION) or 0) > 0
            and float(_config_entry.options.get(CONF_MAXIMUM_CONSUMPTION) or 0) > 0
        ):
            _async_add_entities([SatCurrentConsumptionSensor(coordinator, _config_entry)])


class SatCurrentPowerSensor(SatEntity, SensorEntity):
    _attr_translation_key = "boiler_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)

        self._attr_unique_id = f"{config_entry.entry_id}-boiler-current-power"

    @override
    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        return self._coordinator.boiler_power is not None

    @override
    def _update_attrs(self) -> None:
        self._attr_native_value = self._coordinator.boiler_power


class SatCurrentConsumptionSensor(SatEntity, SensorEntity):
    _attr_translation_key = "boiler_consumption"
    _attr_device_class = SensorDeviceClass.VOLUME_FLOW_RATE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)

        self._attr_unique_id = f"{config_entry.entry_id}-boiler-current-consumption"
        self._minimum_consumption = self._config_entry.options[CONF_MINIMUM_CONSUMPTION]
        self._maximum_consumption = self._config_entry.options[CONF_MAXIMUM_CONSUMPTION]

    @override
    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        return self._coordinator.relative_modulation_value is not None

    @override
    def _update_attrs(self) -> None:
        relative_modulation_value = self._coordinator.relative_modulation_value
        if relative_modulation_value is None:
            return

        if not self._coordinator.device_active or not self._coordinator.flame_active:
            self._attr_native_value = 0
            return

        differential_gas_consumption = self._maximum_consumption - self._minimum_consumption

        self._attr_native_value = round(
            self._minimum_consumption
            + ((relative_modulation_value / 100) * differential_gas_consumption),
            3,
        )


class SatHeatingCurveSensor(SatClimateEntity, SensorEntity):
    _attr_translation_key = "heating_curve"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry, climate: SatClimate
    ) -> None:
        super().__init__(coordinator, config_entry, climate)

        self._attr_unique_id = f"{config_entry.entry_id}-heating-curve"

    @override
    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        return self.climate_added and self._climate.heating_curve.value is not None

    @override
    def _update_attrs(self) -> None:
        self._attr_native_value = self._climate.heating_curve.value


class SatErrorValueSensor(SatClimateEntity, SensorEntity):
    _attr_translation_key = "error_value"
    _attr_device_class = SensorDeviceClass.TEMPERATURE_DELTA
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry, climate: SatClimate
    ) -> None:
        super().__init__(coordinator, config_entry, climate)

        self._attr_unique_id = f"{config_entry.entry_id}-error-value"

    @override
    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        return self.climate_added

    @override
    def _update_attrs(self) -> None:
        if self.available:
            self._attr_native_value = self._climate.max_error.value


class SatManufacturerSensor(SatEntity, SensorEntity):
    _attr_translation_key = "boiler_manufacturer"

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)

        self._attr_unique_id = f"{config_entry.entry_id}-manufacturer"

    @override
    def _update_attrs(self) -> None:
        manufacturer = self._coordinator.manufacturer
        self._attr_native_value = manufacturer.friendly_name if manufacturer is not None else None

    @override
    @property
    def available(self) -> bool:
        return self._coordinator.manufacturer is not None


class SatFlameSensor(SatEntity, SensorEntity):
    _attr_translation_key = "flame_status"

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)

        self._attr_unique_id = f"{config_entry.entry_id}-flame-status"

    @override
    def _update_attrs(self) -> None:
        if self.available:
            self._attr_native_value = self._coordinator.flame.health_status.name

    @override
    @property
    def available(self) -> bool:
        return self._coordinator.flame.health_status is not None


class SatBoilerSensor(SatEntity, SensorEntity):
    _attr_translation_key = "boiler_status"

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)

        self._attr_unique_id = f"{config_entry.entry_id}-boiler-status"

    @override
    def _update_attrs(self) -> None:
        if self.available:
            self._attr_native_value = self._coordinator.device_status.name

    @override
    @property
    def available(self) -> bool:
        return self._coordinator.device_status is not None


class SatBoilerFaultCodeSensor(SatEntity, SensorEntity):
    _attr_translation_key = "boiler_fault_code"

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)

        self._attr_unique_id = f"{config_entry.entry_id}-boiler-fault-code"

    @override
    def _update_attrs(self) -> None:
        self._attr_native_value = self._coordinator.fault_code

    @override
    @property
    def available(self) -> bool:
        return self._coordinator.fault_code is not None
