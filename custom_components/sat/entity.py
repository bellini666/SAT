from __future__ import annotations

import logging
import typing
from typing import override

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER: logging.Logger = logging.getLogger(__name__)

if typing.TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .climate import SatClimate
    from .coordinator import SatDataUpdateCoordinator


class SatEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)

        self._coordinator = coordinator
        self._config_entry = config_entry

        manufacturer = "Unknown"
        if coordinator.manufacturer is not None:
            manufacturer = coordinator.manufacturer.friendly_name

        self._attr_device_info = DeviceInfo(
            name=config_entry.title,
            manufacturer=manufacturer,
            model=coordinator.device_type,
            identifiers={(DOMAIN, config_entry.entry_id)},
        )

    @override
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        self._update_attrs()

    @override
    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_attrs()

        super()._handle_coordinator_update()

    def _update_attrs(self) -> None:
        """Set the dynamic `_attr_*` values right before a state write."""


class SatClimateEntity(SatEntity):
    def __init__(
        self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry, climate: SatClimate
    ) -> None:
        super().__init__(coordinator, config_entry)

        self._climate = climate

    @override
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._climate.async_add_state_listener(self._async_climate_updated))

    @callback
    def _async_climate_updated(self) -> None:
        self._update_attrs()

        self.async_write_ha_state()

    @property
    def climate_added(self) -> bool:
        return self._climate.hass is not None
