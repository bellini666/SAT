from __future__ import annotations

import logging
import typing

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER: logging.Logger = logging.getLogger(__name__)

if typing.TYPE_CHECKING:
    from .climate import SatClimate
    from .coordinator import SatDataUpdateCoordinator


class SatEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry):
        super().__init__(coordinator)

        self._coordinator = coordinator
        self._config_entry = config_entry

    @property
    def device_info(self):
        manufacturer = "Unknown"
        if self._coordinator.manufacturer is not None:
            manufacturer = self._coordinator.manufacturer.friendly_name

        return DeviceInfo(
            name=self._config_entry.title,
            manufacturer=manufacturer,
            model=self._coordinator.device_type,
            identifiers={(DOMAIN, self._config_entry.entry_id)}
        )


class SatClimateEntity(SatEntity):
    def __init__(self, coordinator, config_entry: ConfigEntry, climate: SatClimate):
        super().__init__(coordinator, config_entry)

        self._climate = climate

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._climate.async_add_state_listener(self.async_write_ha_state))

    @property
    def climate_added(self) -> bool:
        return self._climate.hass is not None
