from __future__ import annotations

from typing import TYPE_CHECKING, override

from homeassistant.components import sensor
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.entity import async_generate_entity_id

from ..const import (
    CONF_NAME,
)
from ..entity import SatEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .. import SatConfigEntry
    from ..simulator import SatSimulatorCoordinator


async def async_setup_entry(
    coordinator: SatSimulatorCoordinator,
    config_entry: SatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    async_add_entities(
        [
            SatSetpointSensor(coordinator, config_entry),
            SatBoilerTemperatureSensor(coordinator, config_entry),
        ]
    )


class SatSetpointSensor(SatEntity, sensor.SensorEntity):
    _attr_has_entity_name = False
    _attr_device_class = sensor.SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: SatSimulatorCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)

        self._config_entry = config_entry
        self.entity_id = async_generate_entity_id(
            sensor.DOMAIN + ".{}",
            f"{config_entry.data[CONF_NAME].lower()}_setpoint",
            hass=coordinator.hass,
        )
        self._attr_name = f"Current Setpoint {config_entry.data.get(CONF_NAME)} (Boiler)"
        self._attr_unique_id = f"{config_entry.entry_id}-setpoint"

    @override
    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        return self._coordinator.setpoint is not None

    @override
    def _update_attrs(self) -> None:
        self._attr_native_value = self._coordinator.setpoint


class SatBoilerTemperatureSensor(SatEntity, sensor.SensorEntity):
    _attr_has_entity_name = False
    _attr_device_class = sensor.SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: SatSimulatorCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)

        self._config_entry = config_entry
        self.entity_id = async_generate_entity_id(
            sensor.DOMAIN + ".{}",
            f"{config_entry.data[CONF_NAME].lower()}_boiler_temperature",
            hass=coordinator.hass,
        )
        self._attr_name = f"Current Temperature {config_entry.data.get(CONF_NAME)} (Boiler)"
        self._attr_unique_id = f"{config_entry.entry_id}-boiler_temperature"

    @override
    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        return self._coordinator.boiler_temperature is not None

    @override
    def _update_attrs(self) -> None:
        self._attr_native_value = self._coordinator.boiler_temperature
