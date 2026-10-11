from __future__ import annotations

from collections.abc import Mapping
from typing import Any, override

from homeassistant.components.input_boolean import DOMAIN as INPUT_BOOLEAN_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_registry import RegistryEntry

from ..coordinator import DeviceState, SatDataUpdateCoordinator

DOMAIN_SERVICE = {SWITCH_DOMAIN: SWITCH_DOMAIN, INPUT_BOOLEAN_DOMAIN: INPUT_BOOLEAN_DOMAIN}


class SatSwitchCoordinator(SatDataUpdateCoordinator):
    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        config_data: Mapping[str, Any],
        options: Mapping[str, Any] | None = None,
    ) -> None:
        """Initialize."""
        super().__init__(hass, config_data, options)

        self._entity: RegistryEntry = entity_registry.async_get(hass).async_get(entity_id)

    @override
    @property
    def device_id(self) -> str:
        return self._entity.name

    @override
    @property
    def device_type(self) -> str:
        return "Switch"

    @override
    @property
    def setpoint(self) -> float:
        return self.minimum_setpoint

    @override
    @property
    def maximum_setpoint(self) -> float:
        return self.minimum_setpoint

    @override
    @property
    def device_active(self) -> bool:
        if (state := self.hass.states.get(self._entity.id)) is None:
            return False

        return state.state == STATE_ON

    @override
    @property
    def member_id(self) -> int | None:
        return -1

    @override
    async def async_set_heater_state(self, state: DeviceState) -> None:
        if not self._simulation:
            domain_service = DOMAIN_SERVICE.get(self._entity.domain)
            state_service = SERVICE_TURN_ON if state == DeviceState.ON else SERVICE_TURN_OFF

            if domain_service:
                await self.hass.services.async_call(
                    domain_service,
                    state_service,
                    {ATTR_ENTITY_ID: self._entity.entity_id},
                    blocking=True,
                )

        await super().async_set_heater_state(state)
