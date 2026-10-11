from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from homeassistant.components.input_boolean import DOMAIN as INPUT_BOOLEAN_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_ON
from homeassistant.core import split_entity_id

from ..coordinator import DeviceState, SatDataUpdateCoordinator

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

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

        return state.state == STATE_ON

    @override
    @property
    def member_id(self) -> int | None:
        return -1

    @override
    async def async_set_heater_state(self, state: DeviceState) -> None:
        if not self._simulation:
            domain_service = DOMAIN_SERVICE.get(split_entity_id(self._entity_id)[0])
            state_service = SERVICE_TURN_ON if state == DeviceState.ON else SERVICE_TURN_OFF

            if domain_service is not None:
                await self.hass.services.async_call(
                    domain_service,
                    state_service,
                    {ATTR_ENTITY_ID: self._entity_id},
                    blocking=True,
                )

        await super().async_set_heater_state(state)
