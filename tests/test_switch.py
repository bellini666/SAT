from typing import TYPE_CHECKING

from homeassistant.const import STATE_ON
from homeassistant.helpers import entity_registry

from custom_components.sat.switch import SatSwitchCoordinator
from tests.const import DEFAULT_USER_DATA

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_device_active_follows_the_switch_state(hass: HomeAssistant) -> None:
    entry = entity_registry.async_get(hass).async_get_or_create("switch", "test", "heater")
    hass.states.async_set(entry.entity_id, STATE_ON)

    coordinator = SatSwitchCoordinator(hass, entry.entity_id, DEFAULT_USER_DATA)

    assert coordinator.device_active
