"""Tests for the commands SAT sends to an OpenTherm Gateway over MQTT."""

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message
from pytest_homeassistant_custom_component.typing import MqttMockHAClient

from custom_components.sat.const import CONF_DEVICE, CONF_MINIMUM_SETPOINT, CONF_MODE, CONF_MQTT_TOPIC, DOMAIN, MODE_MQTT_OPENTHERM
from tests.const import DEFAULT_USER_DATA

COMMAND_TOPIC = "OTGW/set/otgw/command"

pytestmark = pytest.mark.usefixtures("instant_mqtt_command_delay")


def commands(mqtt_mock: MqttMockHAClient) -> list[str]:
    return [call.args[1] for call in mqtt_mock.async_publish.call_args_list if call.args[0] == COMMAND_TOPIC]


async def setup_heating(hass: HomeAssistant, mqtt_mock: MqttMockHAClient) -> MockConfigEntry:
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

    async_fire_mqtt_message(hass, "OTGW/value/otgw/Tboiler", "40.0")
    await hass.async_block_till_done()

    climate = entry.runtime_data.climate
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await climate.async_control_heating_loop()
    mqtt_mock.async_publish.reset_mock()

    return entry


async def test_off_hands_control_back(hass: HomeAssistant, mqtt_mock: MqttMockHAClient) -> None:
    entry = await setup_heating(hass, mqtt_mock)

    await entry.runtime_data.climate.async_set_hvac_mode(HVACMode.OFF)

    assert {"CS=0", "MM=T"} <= set(commands(mqtt_mock))
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_hands_control_back(hass: HomeAssistant, mqtt_mock: MqttMockHAClient) -> None:
    entry = await setup_heating(hass, mqtt_mock)

    assert await hass.config_entries.async_unload(entry.entry_id)

    assert {"CS=0", "MM=T"} <= set(commands(mqtt_mock))


# Firing the stop event by hand leaves MQTT's own reconnect cooldown timer behind
@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_stop_hands_control_back(hass: HomeAssistant, mqtt_mock: MqttMockHAClient) -> None:
    entry = await setup_heating(hass, mqtt_mock)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    assert {"CS=0", "MM=T"} <= set(commands(mqtt_mock))
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_control_setpoint_is_refreshed_while_inputs_are_missing(hass: HomeAssistant, mqtt_mock: MqttMockHAClient) -> None:
    entry = await setup_heating(hass, mqtt_mock)
    climate = entry.runtime_data.climate
    setpoint = climate.setpoint

    hass.states.async_set("sensor.test_inside_sensor", "unavailable")
    await climate.async_control_heating_loop()

    assert setpoint is not None
    assert f"CS={min(setpoint, entry.runtime_data.coordinator.maximum_setpoint)}" in commands(mqtt_mock)
    assert await hass.config_entries.async_unload(entry.entry_id)
