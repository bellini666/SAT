from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, override

from homeassistant.core import HomeAssistant

from ..coordinator import DeviceState, SatDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SatFakeConfig:
    def __init__(
        self,
        supports_setpoint_management: bool = False,
        supports_maximum_setpoint_management: bool = False,
        supports_hot_water_setpoint_management: bool = False,
        supports_relative_modulation_management: bool = False,
    ):
        self.supports_setpoint_management = supports_setpoint_management
        self.supports_maximum_setpoint_management = supports_maximum_setpoint_management
        self.supports_hot_water_setpoint_management = supports_hot_water_setpoint_management
        self.supports_relative_modulation_management = supports_relative_modulation_management


class SatFakeCoordinator(SatDataUpdateCoordinator):
    @override
    @property
    def device_id(self) -> str:
        return "Fake"

    @override
    @property
    def device_type(self) -> str:
        return "Fake"

    @override
    @property
    def member_id(self) -> int | None:
        return -1

    def __init__(
        self,
        hass: HomeAssistant,
        config_data: Mapping[str, Any],
        options: Mapping[str, Any] | None = None,
    ) -> None:
        self.config = SatFakeConfig(True)

        self._setpoint = None
        self._maximum_setpoint = None
        self._hot_water_setpoint = None
        self._boiler_temperature = None
        self._device_state = DeviceState.OFF
        self._relative_modulation_value = 100

        super().__init__(hass, config_data, options)

    @override
    @property
    def setpoint(self) -> float | None:
        return self._setpoint

    @override
    @property
    def boiler_temperature(self) -> float | None:
        return self._boiler_temperature

    @override
    @property
    def relative_modulation_value(self) -> float | None:
        return self._relative_modulation_value

    @override
    @property
    def device_active(self) -> bool:
        return self._device_state == DeviceState.ON

    @override
    @property
    def supports_setpoint_management(self):
        if self.config is None:
            return super().supports_setpoint_management

        return self.config.supports_setpoint_management

    @override
    @property
    def supports_hot_water_setpoint_management(self):
        if self.config is None:
            return super().supports_hot_water_setpoint_management

        return self.config.supports_hot_water_setpoint_management

    @override
    @property
    def supports_maximum_setpoint_management(self):
        if self.config is None:
            return super().supports_maximum_setpoint_management

        return self.config.supports_maximum_setpoint_management

    @override
    @property
    def supports_relative_modulation_management(self):
        if self.config is None:
            return super().supports_relative_modulation_management

        return self.config.supports_relative_modulation_management

    @override
    @property
    def supports_relative_modulation(self):
        return self.supports_relative_modulation_management

    async def async_set_boiler_temperature(self, value: float) -> None:
        self._boiler_temperature = value

    @override
    async def async_set_heater_state(self, state: DeviceState) -> None:
        self._device_state = state

        await super().async_set_heater_state(state)

    @override
    async def async_set_control_setpoint(self, value: float) -> None:
        self._setpoint = value

        await super().async_set_control_setpoint(value)

    @override
    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        self._hot_water_setpoint = value

        await super().async_set_control_hot_water_setpoint(value)

    @override
    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        self._relative_modulation_value = value

        await super().async_set_control_max_relative_modulation(value)

    @override
    async def async_set_control_max_setpoint(self, value: float) -> None:
        self._maximum_setpoint = value

        await super().async_set_control_max_setpoint(value)
