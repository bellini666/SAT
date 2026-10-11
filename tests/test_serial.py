from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

from custom_components.sat.const import CONF_SIMULATION
from custom_components.sat.serial import SatSerialCoordinator
from tests.const import DEFAULT_USER_DATA

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_unload_in_simulation_sends_nothing_to_the_gateway(hass: HomeAssistant) -> None:
    with patch("custom_components.sat.serial.OpenThermGateway") as gateway:
        api = gateway.return_value
        api.set_control_setpoint = AsyncMock()
        api.set_max_relative_mod = AsyncMock()
        api.disconnect = AsyncMock()
        coordinator = SatSerialCoordinator(
            hass, "socket://otgw:25238", DEFAULT_USER_DATA, {CONF_SIMULATION: True}
        )

        await coordinator.async_will_remove_from_hass()

    api.set_control_setpoint.assert_not_awaited()
    api.set_max_relative_mod.assert_not_awaited()
    api.disconnect.assert_awaited_once()
