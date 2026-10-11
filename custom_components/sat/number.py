from __future__ import annotations

from typing import TYPE_CHECKING, override

from homeassistant.components.number import NumberDeviceClass, NumberEntity
from homeassistant.const import UnitOfTemperature

from .entity import SatEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import SatConfigEntry
    from .coordinator import SatDataUpdateCoordinator


async def async_setup_entry(
    _hass: HomeAssistant,
    config_entry: SatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = config_entry.runtime_data.coordinator

    if coordinator.supports_hot_water_setpoint_management:
        async_add_entities([SatHotWaterSetpointEntity(coordinator, config_entry)])


class SatHotWaterSetpointEntity(SatEntity, NumberEntity):
    _attr_translation_key = "hot_water_setpoint"
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_icon = "mdi:thermometer"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)

        self._attr_unique_id = f"{config_entry.entry_id}-boiler-dhw-setpoint"

    @override
    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        return self._coordinator.hot_water_setpoint is not None

    @override
    def _update_attrs(self) -> None:
        self._attr_native_value = self._coordinator.hot_water_setpoint
        self._attr_native_min_value = self._coordinator.minimum_hot_water_setpoint
        self._attr_native_max_value = self._coordinator.maximum_hot_water_setpoint

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Update the setpoint."""
        await self._coordinator.async_set_control_hot_water_setpoint(value)
