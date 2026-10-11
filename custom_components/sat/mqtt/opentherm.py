from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, override

from homeassistant.components import mqtt

from ..const import CONF_PUSH_SETPOINT_TO_THERMOSTAT
from ..coordinator import DeviceState
from ..helpers import float_value, int_value
from ..manufacturers.immergas import Immergas
from . import SatMqttCoordinator

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

STATE_ON = "ON"

DATA_FLAME_ACTIVE = "flame"
DATA_DHW_SETPOINT = "TdhwSet"
DATA_CONTROL_SETPOINT = "TSet"
DATA_MAXIMUM_CONTROL_SETPOINT = "MaxTSet"
DATA_REL_MOD_LEVEL = "RelModLevel"
DATA_BOILER_TEMPERATURE = "Tboiler"
DATA_RETURN_TEMPERATURE = "Tret"
DATA_DHW_ENABLE = "domestichotwater"
DATA_CENTRAL_HEATING = "centralheating"
DATA_SLAVE_MEMBERID = "slave_memberid_code"
DATA_BOILER_CAPACITY = "MaxCapacityMinModLevel_hb_u8"
DATA_REL_MIN_MOD_LEVEL = "MaxCapacityMinModLevel_lb_u8"
DATA_REL_MIN_MOD_LEVEL_LEGACY = "MaxCapacityMinModLevell_lb_u8"
DATA_MAX_REL_MOD_LEVEL_SETTING = "MaxRelModLevelSetting"
DATA_DHW_SETPOINT_MINIMUM = "TdhwSetUBTdhwSetLB_value_lb"
DATA_DHW_SETPOINT_MAXIMUM = "TdhwSetUBTdhwSetLB_value_hb"
DATA_FAULT = "fault"
DATA_FAULT_CODE = "OEMFaultCode"
DATA_FAULT_FLAGS = "ASF_flags"

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SatOpenThermMqttCoordinator(SatMqttCoordinator):
    """Class to manage to fetch data from the OTGW Gateway using mqtt."""

    def __init__(
        self,
        hass: HomeAssistant,
        device_id: str,
        config_data: Mapping[str, Any],
        options: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(hass, device_id, config_data, options)

        self._online: bool | None = None
        self._hot_water_setpoint: float | None = None

    @override
    @property
    def device_type(self) -> str:
        return "OpenThermGateway (via mqtt)"

    @override
    @property
    def online(self) -> bool:
        return self._online is not False

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
        return self._live_value(DATA_CENTRAL_HEATING) == STATE_ON

    @override
    @property
    def flame_active(self) -> bool:
        return self._live_value(DATA_FLAME_ACTIVE) == STATE_ON

    @override
    @property
    def hot_water_active(self) -> bool:
        return self._live_value(DATA_DHW_ENABLE) == STATE_ON

    @override
    @property
    def fault_active(self) -> bool:
        return self._live_value(DATA_FAULT) == STATE_ON

    @override
    @property
    def fault_code(self) -> int | None:
        if (value := int_value(self._live_value(DATA_FAULT_CODE))) is not None:
            return value

        return None

    @property
    def fault_flags(self) -> str | None:
        """Return the application-specific fault flags (ID 5 high byte), MSB first."""
        return self._live_value(DATA_FAULT_FLAGS)

    @override
    @property
    def setpoint(self) -> float | None:
        if (setpoint := float_value(self._live_value(DATA_CONTROL_SETPOINT))) is not None:
            return setpoint

        return None

    @override
    @property
    def maximum_setpoint_value(self) -> float | None:
        if (setpoint := float_value(self.data.get(DATA_MAXIMUM_CONTROL_SETPOINT))) is not None:
            return setpoint

        return super().maximum_setpoint_value

    @override
    @property
    def hot_water_setpoint(self) -> float | None:
        if (setpoint := float_value(self._live_value(DATA_DHW_SETPOINT))) is not None:
            return setpoint

        if self._hot_water_setpoint is not None:
            return self._hot_water_setpoint

        if (setpoint := float_value(self.data.get(DATA_DHW_SETPOINT))) is not None:
            return setpoint

        return super().hot_water_setpoint

    @override
    @property
    def minimum_hot_water_setpoint(self) -> float:
        if (setpoint := float_value(self.data.get(DATA_DHW_SETPOINT_MINIMUM))) is not None:
            return setpoint

        return super().minimum_hot_water_setpoint

    @override
    @property
    def maximum_hot_water_setpoint(self) -> float:
        if (setpoint := float_value(self.data.get(DATA_DHW_SETPOINT_MAXIMUM))) is not None:
            return setpoint

        return super().maximum_hot_water_setpoint

    @override
    @property
    def boiler_temperature(self) -> float | None:
        if (value := float_value(self._live_value(DATA_BOILER_TEMPERATURE))) is not None:
            return value

        return super().boiler_temperature

    @override
    @property
    def return_temperature(self) -> float | None:
        if (value := float_value(self._live_value(DATA_RETURN_TEMPERATURE))) is not None:
            return value

        return super().return_temperature

    @override
    @property
    def relative_modulation_value(self) -> float | None:
        if (value := float_value(self._live_value(DATA_REL_MOD_LEVEL))) is not None:
            return value

        return super().relative_modulation_value

    @override
    @property
    def boiler_capacity(self) -> float | None:
        if (value := float_value(self.data.get(DATA_BOILER_CAPACITY))) is not None:
            return value

        return super().boiler_capacity

    @override
    @property
    def minimum_relative_modulation_value(self) -> float | None:
        if (value := float_value(self.data.get(DATA_REL_MIN_MOD_LEVEL))) is not None:
            return value

        # Legacy
        if (value := float_value(self.data.get(DATA_REL_MIN_MOD_LEVEL_LEGACY))) is not None:
            return value

        return super().minimum_relative_modulation_value

    @override
    @property
    def maximum_relative_modulation_value(self) -> float | None:
        if (value := float_value(self._live_value(DATA_MAX_REL_MOD_LEVEL_SETTING))) is not None:
            return value

        return super().maximum_relative_modulation_value

    @override
    @property
    def member_id(self) -> int | None:
        if (value := int_value(self.data.get(DATA_SLAVE_MEMBERID))) is not None:
            return value

        return None

    @override
    async def async_setup(self) -> None:
        await super().async_setup()

        # OTGW-firmware publishes its retained will message, "online" or "offline", on the bare
        # value namespace
        self._subscriptions.append(
            await mqtt.async_subscribe(
                self.hass,
                f"{self._topic}/value/{self._device_id}",
                self._async_availability_changed,
            )
        )

    async def _async_availability_changed(self, message: mqtt.ReceiveMessage) -> None:
        was_online = self._online
        self._online = online = message.payload == "online"

        if not online:
            self._stale_keys = set(self.data)
            self.async_update_listeners()
            return

        # A live "online" is an ESP boot or reconnect; the ESP boot resets the PIC, which drops
        # every override
        if was_online is True or message.retain:
            return

        self._stale_keys = set(self.data)
        await self.boot()
        await self.async_set_control_max_setpoint(self.maximum_setpoint)

        if self._hot_water_setpoint is not None:
            await self.async_set_control_hot_water_setpoint(self._hot_water_setpoint)

        # The last sent MM is set by the first control loop and cleared by the release, so without
        # one SAT is not in control
        if self._control_max_relative_modulation is None:
            await self.async_release_control()
        else:
            await self.async_set_control_max_relative_modulation(
                self._control_max_relative_modulation
            )

        if self._control_thermostat_setpoint is not None:
            await self.async_set_control_thermostat_setpoint(self._control_thermostat_setpoint)

        # The climate schedules a control loop when the boiler temperature goes missing
        self.async_update_listeners()

    @override
    async def boot(self) -> None:
        await self._publish_command("PM=3")
        await self._publish_command("PM=15")
        await self._publish_command("PM=48")

    @override
    def get_tracked_entities(self) -> list[str]:
        return [
            DATA_SLAVE_MEMBERID,
            DATA_CENTRAL_HEATING,
            DATA_FLAME_ACTIVE,
            DATA_DHW_ENABLE,
            DATA_DHW_SETPOINT,
            DATA_CONTROL_SETPOINT,
            DATA_REL_MOD_LEVEL,
            DATA_BOILER_TEMPERATURE,
            DATA_RETURN_TEMPERATURE,
            DATA_BOILER_CAPACITY,
            DATA_REL_MIN_MOD_LEVEL,
            DATA_REL_MIN_MOD_LEVEL_LEGACY,
            DATA_MAX_REL_MOD_LEVEL_SETTING,
            DATA_DHW_SETPOINT_MINIMUM,
            DATA_DHW_SETPOINT_MAXIMUM,
            DATA_FAULT,
            DATA_FAULT_CODE,
            DATA_FAULT_FLAGS,
        ]

    @override
    async def async_set_control_setpoint(self, value: float) -> None:
        await self._publish_command(f"CS={value}")
        await self._publish_command("PM=25")

        await super().async_set_control_setpoint(value)

    @override
    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        if await self._publish_command(f"SW={value}"):
            self._hot_water_setpoint = value

        await super().async_set_control_hot_water_setpoint(value)

    @override
    async def async_set_control_thermostat_setpoint(self, value: float) -> None:
        await self._publish_command(f"TC={value}")

        await super().async_set_control_thermostat_setpoint(value)

    @override
    async def async_set_heater_state(self, state: DeviceState) -> None:
        await self._publish_command(f"CH={1 if state == DeviceState.ON else 0}")

        await super().async_set_heater_state(state)

    @override
    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        if isinstance(self.manufacturer, Immergas):
            await self._publish_command(f"TP=11:12={min(value, 80)}")

        await self._publish_command(f"MM={value}")

        await super().async_set_control_max_relative_modulation(value)

    @override
    async def async_release_control(self) -> None:
        await self._publish_command("CS=0")
        await self._publish_command("MM=T")

        # TC has no expiry on the gateway, and one sent before a restart is not tracked
        if self._options.get(CONF_PUSH_SETPOINT_TO_THERMOSTAT):
            await self._publish_command("TC=0")

        await super().async_release_control()

    @override
    async def async_set_control_max_setpoint(self, value: float) -> None:
        await self._publish_command(f"SH={value}")

        await super().async_set_control_max_setpoint(value)

    @override
    def _get_topic_for_subscription(self, key: str) -> str:
        return f"{self._topic}/value/{self._device_id}/{key}"

    @override
    def _get_topic_for_publishing(self) -> str:
        return f"{self._topic}/set/{self._device_id}/command"
