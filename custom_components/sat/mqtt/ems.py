from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, override

from ..const import MINIMUM_SETPOINT
from ..coordinator import DeviceState
from ..helpers import float_value
from . import SatMqttCoordinator

if TYPE_CHECKING:
    from homeassistant.helpers.service_info.mqtt import ReceivePayloadType

DATA_ON = "on"
DATA_OFF = "off"

DATA_BOILER_DATA = "boiler_data"
DATA_FLAME_ACTIVE = "burngas"
DATA_DHW_SETPOINT = "dhw/seltemp"
DATA_CONTROL_SETPOINT = "selflowtemp"
DATA_REL_MOD_LEVEL = "curburnpow"
DATA_BOILER_TEMPERATURE = "curflowtemp"
DATA_RETURN_TEMPERATURE = "rettemp"

DATA_DHW_ENABLE = "tapactivated"
DATA_CENTRAL_HEATING = "heatingactive"
DATA_BOILER_CAPACITY = "nompower"

DATA_REL_MIN_MOD_LEVEL = "burnminpower"
DATA_MAX_REL_MOD_LEVEL_SETTING = "burnmaxpower"

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SatEmsMqttCoordinator(SatMqttCoordinator):
    """Class to manage to fetch data from the OTGW Gateway using MQTT."""

    @override
    @property
    def device_type(self) -> str:
        return "Energy Management System (via mqtt)"

    @override
    @property
    def supports_setpoint_management(self) -> bool:
        return True

    @override
    @property
    def supports_hot_water_setpoint_management(self) -> bool:
        return True

    @override
    @property
    def supports_maximum_setpoint_management(self) -> bool:
        return True

    @override
    @property
    def supports_relative_modulation(self) -> bool:
        return True

    @override
    @property
    def device_active(self) -> bool:
        return self._is_on(DATA_CENTRAL_HEATING)

    @override
    @property
    def flame_active(self) -> bool:
        return self._is_on(DATA_FLAME_ACTIVE)

    @override
    @property
    def hot_water_active(self) -> bool:
        return self._is_on(DATA_DHW_ENABLE)

    @override
    @property
    def setpoint(self) -> float | None:
        return float_value(self.data.get(DATA_CONTROL_SETPOINT))

    @override
    @property
    def hot_water_setpoint(self) -> float | None:
        return float_value(self.data.get(DATA_DHW_SETPOINT))

    @override
    @property
    def boiler_temperature(self) -> float | None:
        return float_value(self.data.get(DATA_BOILER_TEMPERATURE))

    @override
    @property
    def return_temperature(self) -> float | None:
        return float_value(self.data.get(DATA_RETURN_TEMPERATURE))

    @override
    @property
    def relative_modulation_value(self) -> float | None:
        return float_value(self.data.get(DATA_REL_MOD_LEVEL))

    @override
    @property
    def boiler_capacity(self) -> float | None:
        return float_value(self.data.get(DATA_BOILER_CAPACITY))

    @override
    @property
    def minimum_relative_modulation_value(self) -> float | None:
        return float_value(self.data.get(DATA_REL_MIN_MOD_LEVEL))

    @override
    @property
    def maximum_relative_modulation_value(self) -> float | None:
        return float_value(self.data.get(DATA_MAX_REL_MOD_LEVEL_SETTING))

    @override
    @property
    def member_id(self) -> int | None:
        # Not supported (yet)
        return None

    @override
    async def boot(self) -> None:
        # Nothing needs to be booted (yet)
        pass

    def _is_on(self, key: str) -> bool:
        value = self.data.get(key)
        return isinstance(value, str) and value == DATA_ON

    @override
    def get_tracked_entities(self) -> list[str]:
        return [DATA_BOILER_DATA]

    @override
    async def async_set_control_setpoint(self, value: float) -> None:
        await self._publish_command(
            f'{{"cmd": "selflowtemp", "value": {0 if value == MINIMUM_SETPOINT else value}}}'
        )

        await super().async_set_control_setpoint(value)

    @override
    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        await self._publish_command(f'{{"cmd": "dhw/seltemp", "value": {value}}}')

        await super().async_set_control_hot_water_setpoint(value)

    @override
    async def async_set_control_thermostat_setpoint(self, value: float) -> None:
        # Not supported (yet)
        await super().async_set_control_thermostat_setpoint(value)

    @override
    async def async_set_heater_state(self, state: DeviceState) -> None:
        value = DATA_ON if state == DeviceState.ON else DATA_OFF
        await self._publish_command(f'{{"cmd": "heatingactivated", "value": "{value}"}}')

        await super().async_set_heater_state(state)

    @override
    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        await self._publish_command(f'{{"cmd": "burnmaxpower", "value": {max(value, 20)}}}')

        await super().async_set_control_max_relative_modulation(value)

    @override
    async def async_set_control_max_setpoint(self, value: float) -> None:
        await self._publish_command(f'{{"cmd": "heatingtemp", "value": {value}}}')

        await super().async_set_control_max_setpoint(value)

    @override
    def _get_topic_for_subscription(self, key: str) -> str:
        return f"{self._topic}/{key}"

    @override
    def _get_topic_for_publishing(self) -> str:
        return f"{self._topic}/boiler"

    @override
    def _process_message_payload(self, key: str, value: ReceivePayloadType) -> None:
        try:
            self.async_set_updated_data(json.loads(value))
        except json.JSONDecodeError:
            _LOGGER.exception("Failed to decode JSON payload: %s", value)
