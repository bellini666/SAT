"""Tests for the SAT diagnostics."""

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator, MqttMockHAClient

from custom_components.sat.const import CONF_DEVICE, CONF_MINIMUM_SETPOINT, CONF_MODE, CONF_MQTT_TOPIC, DOMAIN, MODE_MQTT_OPENTHERM
from tests.const import DEFAULT_USER_DATA

pytestmark = pytest.mark.usefixtures("instant_mqtt_command_delay")


async def test_config_entry_diagnostics(hass: HomeAssistant, hass_client: ClientSessionGenerator, mqtt_mock: MqttMockHAClient) -> None:
    assert await async_setup_component(hass, "diagnostics", {})
    hass.states.async_set("sensor.test_inside_sensor", "19.5")
    hass.states.async_set("sensor.test_outside_sensor", "5.0")

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="otgw",
        data={**DEFAULT_USER_DATA, CONF_MODE: MODE_MQTT_OPENTHERM, CONF_DEVICE: "otgw", CONF_MQTT_TOPIC: "OTGW", CONF_MINIMUM_SETPOINT: 45},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async_fire_mqtt_message(hass, "OTGW/value/otgw/flame", "ON")
    async_fire_mqtt_message(hass, "OTGW/value/otgw/Tboiler", "40.0")
    await hass.async_block_till_done()
    await entry.runtime_data.climate.async_set_hvac_mode(HVACMode.HEAT)

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["entry"]["data"][CONF_DEVICE] == REDACTED
    assert diagnostics["entry"]["data"][CONF_MINIMUM_SETPOINT] == 45
    assert diagnostics["climate"]["hvac_mode"] == HVACMode.HEAT
    assert diagnostics["climate"]["control_problems"] == []
    assert "integral" in diagnostics["climate"]["attributes"]
    assert "pulse_width_modulation_state" in diagnostics["climate"]["attributes"]
    assert diagnostics["climate"]["errors"] == [{"entity_id": "climate.mock_title", "value": -1.5}]
    assert diagnostics["coordinator"]["data"]["flame"] == "ON"
    assert diagnostics["coordinator"]["boiler"]["flow_temperature"] == 40.0
    assert diagnostics["coordinator"]["messages"][-1]["topic"] == "OTGW/value/otgw/Tboiler"
    assert diagnostics["coordinator"]["messages"][-1]["payload"] == "40.0"

    assert await hass.config_entries.async_unload(entry.entry_id)
