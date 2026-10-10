"""Tests for the commands SAT sends to an OpenTherm Gateway over MQTT."""

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
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


@pytest.mark.parametrize("turn_off", [True, False], ids=["off", "unload"])
async def test_hands_control_back_after_a_running_control_loop(hass: HomeAssistant, mqtt_mock: MqttMockHAClient, turn_off: bool) -> None:
    entry = await setup_heating(hass, mqtt_mock)
    climate = entry.runtime_data.climate
    coordinator = entry.runtime_data.coordinator
    set_control_setpoint = coordinator.async_set_control_setpoint
    gate = asyncio.Event()

    async def gated_control_setpoint(value: float) -> None:
        if value > 0:
            await gate.wait()
        await set_control_setpoint(value)

    with patch.object(coordinator, "async_set_control_setpoint", gated_control_setpoint):
        loop = hass.async_create_task(climate.async_control_heating_loop())
        await asyncio.sleep(0)
        if turn_off:
            release = hass.async_create_task(climate.async_set_hvac_mode(HVACMode.OFF))
        else:
            release = hass.async_create_task(hass.config_entries.async_unload(entry.entry_id))
        for _ in range(10):
            await asyncio.sleep(0)

        gate.set()
        await loop
        await release

    assert commands(mqtt_mock)[-2:] == ["CS=0", "MM=T"]
    if turn_off:
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_control_is_handed_back_once_inputs_stay_missing(hass: HomeAssistant, mqtt_mock: MqttMockHAClient, freezer: FrozenDateTimeFactory) -> None:
    entry = await setup_heating(hass, mqtt_mock)
    climate = entry.runtime_data.climate

    hass.states.async_set("sensor.test_inside_sensor", "unavailable")
    await climate.async_control_heating_loop()
    freezer.tick(timedelta(minutes=11))
    await climate.async_control_heating_loop()
    await climate.async_control_heating_loop()

    control = [command for command in commands(mqtt_mock) if command.startswith(("CS=", "MM="))]
    assert control[1:] == ["CS=0", "MM=T"]

    mqtt_mock.async_publish.reset_mock()
    hass.states.async_set("sensor.test_inside_sensor", "19.5")
    await climate.async_control_heating_loop()

    assert any(command.startswith("CS=") and command != "CS=0" for command in commands(mqtt_mock))
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(("hvac_mode", "released"), [(HVACMode.OFF, True), (HVACMode.HEAT, False)])
async def test_startup_hands_control_back_unless_heating(hass: HomeAssistant, mqtt_mock: MqttMockHAClient, hvac_mode: HVACMode, released: bool) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="otgw",
        data={**DEFAULT_USER_DATA, CONF_MODE: MODE_MQTT_OPENTHERM, CONF_DEVICE: "otgw", CONF_MQTT_TOPIC: "OTGW"},
        options={"default_hvac_mode": hvac_mode},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert ({"CS=0", "MM=T"} <= set(commands(mqtt_mock))) is released
    assert await hass.config_entries.async_unload(entry.entry_id)
