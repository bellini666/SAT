from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from homeassistant.components.input_boolean import DOMAIN as INPUT_BOOLEAN_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.valve import DOMAIN as VALVE_DOMAIN
from homeassistant.components.valve import ValveState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_CLOSE_VALVE,
    SERVICE_OPEN_VALVE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
)
from homeassistant.core import split_entity_id

from ..coordinator import DeviceState, SatDataUpdateCoordinator

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

DOMAIN_SERVICES = {
    SWITCH_DOMAIN: (SERVICE_TURN_ON, SERVICE_TURN_OFF),
    INPUT_BOOLEAN_DOMAIN: (SERVICE_TURN_ON, SERVICE_TURN_OFF),
    VALVE_DOMAIN: (SERVICE_OPEN_VALVE, SERVICE_CLOSE_VALVE),
}


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

        self._entity_id: str = entity_id

    @override
    @property
    def device_id(self) -> str:
        return self._entity_id

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
        if (state := self.hass.states.get(self._entity_id)) is None:
            return False

        return state.state in (STATE_ON, ValveState.OPEN)

    @override
    @property
    def member_id(self) -> int | None:
        return -1

    @override
    async def async_set_heater_state(self, state: DeviceState) -> None:
        if not self._simulation:
            domain = split_entity_id(self._entity_id)[0]

            if (services := DOMAIN_SERVICES.get(domain)) is not None:
                await self.hass.services.async_call(
                    domain,
                    services[0] if state == DeviceState.ON else services[1],
                    {ATTR_ENTITY_ID: self._entity_id},
                    blocking=True,
                )

        await super().async_set_heater_state(state)
