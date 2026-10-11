from typing import TYPE_CHECKING

from homeassistant.components.valve import DOMAIN as VALVE_DOMAIN
from homeassistant.components.valve import ValveState
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_CLOSE_VALVE, SERVICE_OPEN_VALVE, STATE_ON
from homeassistant.helpers import entity_registry
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.sat.coordinator import DeviceState
from custom_components.sat.switch import SatSwitchCoordinator
from tests.const import DEFAULT_USER_DATA

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_device_active_follows_the_switch_state(hass: HomeAssistant) -> None:
    entry = entity_registry.async_get(hass).async_get_or_create("switch", "test", "heater")
    hass.states.async_set(entry.entity_id, STATE_ON)

    coordinator = SatSwitchCoordinator(hass, entry.entity_id, DEFAULT_USER_DATA)

    assert coordinator.device_active


async def test_device_active_follows_an_open_valve(hass: HomeAssistant) -> None:
    hass.states.async_set("valve.heater", ValveState.OPEN)

    coordinator = SatSwitchCoordinator(hass, "valve.heater", DEFAULT_USER_DATA)

    assert coordinator.device_active


async def test_heater_state_opens_and_closes_a_valve(hass: HomeAssistant) -> None:
    opened = async_mock_service(hass, VALVE_DOMAIN, SERVICE_OPEN_VALVE)
    closed = async_mock_service(hass, VALVE_DOMAIN, SERVICE_CLOSE_VALVE)
    coordinator = SatSwitchCoordinator(hass, "valve.heater", DEFAULT_USER_DATA)

    await coordinator.async_set_heater_state(DeviceState.ON)
    await coordinator.async_set_heater_state(DeviceState.OFF)

    assert [call.data[ATTR_ENTITY_ID] for call in opened] == ["valve.heater"]
    assert [call.data[ATTR_ENTITY_ID] for call in closed] == ["valve.heater"]
