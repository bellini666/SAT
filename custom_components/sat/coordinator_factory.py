from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .const import (
    MODE_ESPHOME,
    MODE_FAKE,
    MODE_MQTT_EMS,
    MODE_MQTT_OPENTHERM,
    MODE_SERIAL,
    MODE_SIMULATOR,
    MODE_SWITCH,
)
from .esphome import SatEspHomeCoordinator
from .fake import SatFakeCoordinator
from .mqtt.ems import SatEmsMqttCoordinator
from .mqtt.opentherm import SatOpenThermMqttCoordinator
from .serial import SatSerialCoordinator
from .simulator import SatSimulatorCoordinator
from .switch import SatSwitchCoordinator

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

    from .coordinator import SatDataUpdateCoordinator


class SatDataUpdateCoordinatorFactory:
    @staticmethod
    def resolve(
        hass: HomeAssistant,
        mode: str,
        device: str,
        data: Mapping[str, Any],
        options: Mapping[str, Any] | None = None,
    ) -> SatDataUpdateCoordinator:
        if mode == MODE_FAKE:
            return SatFakeCoordinator(hass=hass, config_data=data, options=options)

        if mode == MODE_SIMULATOR:
            return SatSimulatorCoordinator(hass=hass, config_data=data, options=options)

        if mode == MODE_SWITCH:
            return SatSwitchCoordinator(
                hass=hass, entity_id=device, config_data=data, options=options
            )

        if mode == MODE_ESPHOME:
            return SatEspHomeCoordinator(
                hass=hass, device_id=device, config_data=data, options=options
            )

        if mode == MODE_MQTT_EMS:
            return SatEmsMqttCoordinator(
                hass=hass, device_id=device, config_data=data, options=options
            )

        if mode == MODE_MQTT_OPENTHERM:
            return SatOpenThermMqttCoordinator(
                hass=hass, device_id=device, config_data=data, options=options
            )

        if mode == MODE_SERIAL:
            return SatSerialCoordinator(hass=hass, port=device, config_data=data, options=options)

        msg = f"Invalid mode[{mode}]"
        raise ValueError(msg)
