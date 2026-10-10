"""Tests for cascading SAT changes to the controlled climates."""

from unittest.mock import patch

from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sat.const import CONF_PUSH_SETPOINT_TO_THERMOSTAT, CONF_RADIATORS, DOMAIN
from tests.const import DEFAULT_USER_DATA


async def test_hvac_mode_reaches_radiators_after_an_unsupported_one(hass: HomeAssistant) -> None:
    hass.states.async_set("climate.cooler", HVACMode.OFF, {"hvac_modes": [HVACMode.OFF, HVACMode.COOL]})
    hass.states.async_set("climate.radiator", HVACMode.OFF, {"hvac_modes": [HVACMode.OFF, HVACMode.HEAT]})

    entry = MockConfigEntry(domain=DOMAIN, data={**DEFAULT_USER_DATA, CONF_RADIATORS: ["climate.cooler", "climate.radiator"]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    calls = async_mock_service(hass, "climate", "set_hvac_mode")
    await entry.runtime_data.climate.async_set_hvac_mode(HVACMode.HEAT)

    assert [call.data["entity_id"] for call in calls] == ["climate.radiator"]


async def test_push_setpoint_option_reaches_the_boiler_thermostat(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA, options={CONF_PUSH_SETPOINT_TO_THERMOSTAT: True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with patch.object(entry.runtime_data.coordinator, "async_set_control_thermostat_setpoint") as push:
        await entry.runtime_data.climate.async_set_target_temperature(22.5)

    push.assert_called_once_with(22.5)
