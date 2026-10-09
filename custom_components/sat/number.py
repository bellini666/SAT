from homeassistant.components.number import NumberEntity, NumberDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import *
from .coordinator import SatDataUpdateCoordinator
from .entity import SatEntity


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    coordinator = config_entry.runtime_data.coordinator

    if coordinator.supports_hot_water_setpoint_management:
        async_add_entities([SatHotWaterSetpointEntity(coordinator, config_entry)])


class SatHotWaterSetpointEntity(SatEntity, NumberEntity):
    _attr_translation_key = "hot_water_setpoint"


    @property
    def device_class(self):
        """Return the device class."""
        return NumberDeviceClass.TEMPERATURE

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.entry_id}-boiler-dhw-setpoint"

    @property
    def icon(self) -> str | None:
        return "mdi:thermometer"

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._coordinator.hot_water_setpoint is not None

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement in native units."""
        return "°C"

    @property
    def native_value(self):
        """Return the state of the device in native units."""
        return self._coordinator.hot_water_setpoint

    @property
    def native_min_value(self) -> float:
        """Return the minimum accepted temperature."""
        return self._coordinator.minimum_hot_water_setpoint

    @property
    def native_max_value(self) -> float:
        """Return the maximum accepted temperature."""
        return self._coordinator.maximum_hot_water_setpoint

    async def async_set_native_value(self, value: float) -> None:
        """Update the setpoint."""
        await self._coordinator.async_set_control_hot_water_setpoint(value)
