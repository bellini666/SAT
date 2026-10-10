"""Tests for the commands SAT sends to an OpenTherm Gateway over MQTT."""

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.climate import HVACMode
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE, EVENT_HOMEASSISTANT_STOP, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message, async_fire_time_changed
from pytest_homeassistant_custom_component.typing import MqttMockHAClient

from custom_components.sat.const import (
    CONF_DEVICE,
    CONF_MANUFACTURER,
    CONF_MINIMUM_SETPOINT,
    CONF_MODE,
    CONF_MQTT_TOPIC,
    CONF_PUSH_SETPOINT_TO_THERMOSTAT,
    DOMAIN,
    MODE_MQTT_OPENTHERM,
)
from tests.const import DEFAULT_USER_DATA

COMMAND_TOPIC = "OTGW/set/otgw/command"
STORAGE_KEY = "sat_open_therm_mqtt_coordinator_otgw"
AVAILABILITY_TOPIC = "OTGW/value/otgw"

pytestmark = pytest.mark.usefixtures("instant_mqtt_command_delay")


def commands(mqtt_mock: MqttMockHAClient) -> list[str]:
    return [call.args[1] for call in mqtt_mock.async_publish.call_args_list if call.args[0] == COMMAND_TOPIC]


async def setup_heating(hass: HomeAssistant, mqtt_mock: MqttMockHAClient, options: dict | None = None) -> MockConfigEntry:
    hass.states.async_set("sensor.test_inside_sensor", "19.5")
    hass.states.async_set("sensor.test_outside_sensor", "5.0")

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="otgw",
        data={**DEFAULT_USER_DATA, CONF_MODE: MODE_MQTT_OPENTHERM, CONF_DEVICE: "otgw", CONF_MQTT_TOPIC: "OTGW", CONF_MINIMUM_SETPOINT: 45},
        options=options or {},
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


@pytest.mark.parametrize("push_setpoint", [True, False])
async def test_off_cancels_the_thermostat_override_only_when_pushing_the_setpoint(hass: HomeAssistant, mqtt_mock: MqttMockHAClient, push_setpoint: bool) -> None:
    entry = await setup_heating(hass, mqtt_mock, {CONF_PUSH_SETPOINT_TO_THERMOSTAT: push_setpoint})

    await entry.runtime_data.climate.async_set_hvac_mode(HVACMode.OFF)

    assert [command for command in commands(mqtt_mock) if command.startswith("TC=")] == (["TC=0"] if push_setpoint else [])
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("release", ["reload", "off"])
async def test_control_loop_restores_the_thermostat_override_after_a_release(hass: HomeAssistant, mqtt_mock: MqttMockHAClient, release: str) -> None:
    entry = await setup_heating(hass, mqtt_mock, {CONF_PUSH_SETPOINT_TO_THERMOSTAT: True})

    if release == "reload":
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    else:
        await entry.runtime_data.climate.async_set_hvac_mode(HVACMode.OFF)
        await entry.runtime_data.climate.async_set_hvac_mode(HVACMode.HEAT)

    await entry.runtime_data.climate.async_control_heating_loop()
    sent = commands(mqtt_mock)

    assert "TC=21.0" in sent[sent.index("TC=0"):]
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


async def test_synchro_sensors_ignore_the_room_thermostat_after_the_release(hass: HomeAssistant, mqtt_mock: MqttMockHAClient, freezer: FrozenDateTimeFactory) -> None:
    entry = await setup_heating(hass, mqtt_mock)
    climate = entry.runtime_data.climate

    hass.states.async_set("sensor.test_inside_sensor", "unavailable")
    await climate.async_control_heating_loop()
    freezer.tick(timedelta(minutes=11))
    await climate.async_control_heating_loop()
    async_fire_mqtt_message(hass, "OTGW/value/otgw/MaxRelModLevelSetting", "0.0")
    await hass.async_block_till_done()
    climate.async_write_ha_state()
    freezer.tick(timedelta(seconds=61))
    climate.async_write_ha_state()

    assert climate.setpoint is None
    assert hass.states.get("binary_sensor.mock_title_relative_modulation_synchro").state == STATE_OFF
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(("hvac_mode", "released"), [(HVACMode.OFF, True), (HVACMode.HEAT, False)])
async def test_startup_hands_control_back_unless_heating(hass: HomeAssistant, mqtt_mock: MqttMockHAClient, hvac_mode: HVACMode, released: bool) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="otgw",
        data={**DEFAULT_USER_DATA, CONF_MODE: MODE_MQTT_OPENTHERM, CONF_DEVICE: "otgw", CONF_MQTT_TOPIC: "OTGW"},
        options={"default_hvac_mode": hvac_mode, CONF_PUSH_SETPOINT_TO_THERMOSTAT: True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert ({"CS=0", "MM=T", "TC=0"} <= set(commands(mqtt_mock))) is released
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_listeners_are_notified_while_values_keep_changing(hass: HomeAssistant, mqtt_mock: MqttMockHAClient, freezer: FrozenDateTimeFactory) -> None:
    entry = await setup_heating(hass, mqtt_mock)
    updates = []
    unsubscribe = entry.runtime_data.coordinator.async_add_listener(lambda: updates.append(True))

    for second in range(0, 20, 2):
        async_fire_mqtt_message(hass, "OTGW/value/otgw/Tboiler", f"{41 + second}.0")
        await hass.async_block_till_done()
        freezer.tick(timedelta(seconds=2))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    assert len(updates) >= 3
    unsubscribe()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_boot_does_not_set_the_stand_alone_message_interval(hass: HomeAssistant, mqtt_mock: MqttMockHAClient) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="otgw",
        data={**DEFAULT_USER_DATA, CONF_MODE: MODE_MQTT_OPENTHERM, CONF_DEVICE: "otgw", CONF_MQTT_TOPIC: "OTGW", CONF_MANUFACTURER: "Intergas"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert not any(command.startswith("MI=") for command in commands(mqtt_mock))
    assert await hass.config_entries.async_unload(entry.entry_id)


def otgw_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="otgw",
        data={**DEFAULT_USER_DATA, CONF_MODE: MODE_MQTT_OPENTHERM, CONF_DEVICE: "otgw", CONF_MQTT_TOPIC: "OTGW"},
    )
    entry.add_to_hass(hass)

    return entry


async def test_restored_values_are_stale_until_a_live_message(hass: HomeAssistant, mqtt_mock: MqttMockHAClient, hass_storage: dict) -> None:
    hass_storage[STORAGE_KEY] = {"version": 1, "key": STORAGE_KEY, "data": {"Tboiler": "40.0", "domestichotwater": "ON"}}
    entry = otgw_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinator

    assert coordinator.data["Tboiler"] == "40.0"
    assert coordinator.boiler_temperature is None
    assert not coordinator.hot_water_active

    async_fire_mqtt_message(hass, "OTGW/value/otgw/Tboiler", "41.0")
    await hass.async_block_till_done()

    assert coordinator.boiler_temperature == 41.0
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_listeners_are_notified_when_a_restored_value_comes_back_unchanged(hass: HomeAssistant, mqtt_mock: MqttMockHAClient, hass_storage: dict, freezer: FrozenDateTimeFactory) -> None:
    hass_storage[STORAGE_KEY] = {"version": 1, "key": STORAGE_KEY, "data": {"Tboiler": "40.0"}}
    entry = otgw_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinator
    freezer.tick(timedelta(seconds=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    updates = []
    unsubscribe = coordinator.async_add_listener(lambda: updates.append(coordinator.boiler_temperature))

    async_fire_mqtt_message(hass, "OTGW/value/otgw/Tboiler", "40.0")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert updates == [40.0]
    unsubscribe()
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_values_are_saved_when_home_assistant_stops(hass: HomeAssistant, mqtt_mock: MqttMockHAClient, hass_storage: dict) -> None:
    entry = otgw_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async_fire_mqtt_message(hass, "OTGW/value/otgw/Tboiler", "41.0")
    await hass.async_block_till_done()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()

    assert hass_storage[STORAGE_KEY]["data"]["Tboiler"] == "41.0"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_max_modulation_is_sent_again_after_a_release(hass: HomeAssistant, mqtt_mock: MqttMockHAClient) -> None:
    entry = await setup_heating(hass, mqtt_mock)
    climate = entry.runtime_data.climate
    value = climate.relative_modulation_value
    async_fire_mqtt_message(hass, "OTGW/value/otgw/MaxRelModLevelSetting", f"{value}.00")
    await hass.async_block_till_done()

    await climate.async_set_hvac_mode(HVACMode.OFF)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    mqtt_mock.async_publish.reset_mock()
    await climate.async_control_heating_loop()

    assert f"MM={value}" in commands(mqtt_mock)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_restored_max_modulation_does_not_skip_the_command(hass: HomeAssistant, mqtt_mock: MqttMockHAClient, hass_storage: dict) -> None:
    hass_storage[STORAGE_KEY] = {"version": 1, "key": STORAGE_KEY, "data": {"MaxRelModLevelSetting": "100.00"}}

    entry = await setup_heating(hass, mqtt_mock)
    value = entry.runtime_data.climate.relative_modulation_value
    await entry.runtime_data.climate.async_control_heating_loop()

    assert value == 100
    assert f"MM={value}" in commands(mqtt_mock)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_offline_gateway_pauses_control(hass: HomeAssistant, mqtt_mock: MqttMockHAClient) -> None:
    entry = await setup_heating(hass, mqtt_mock)
    climate = entry.runtime_data.climate

    async_fire_mqtt_message(hass, AVAILABILITY_TOPIC, "offline")
    await hass.async_block_till_done()
    await climate.async_control_heating_loop()

    assert not entry.runtime_data.coordinator.online
    assert "gateway_offline" in climate.control_problems
    assert hass.states.get("binary_sensor.mock_title_boiler_health").state == STATE_ON
    assert commands(mqtt_mock) == []
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("retain", [False, True], ids=["reboot", "replay"])
async def test_gateway_reboot_restores_the_overrides(hass: HomeAssistant, mqtt_mock: MqttMockHAClient, freezer: FrozenDateTimeFactory, retain: bool) -> None:
    entry = await setup_heating(hass, mqtt_mock, {CONF_PUSH_SETPOINT_TO_THERMOSTAT: True})
    coordinator = entry.runtime_data.coordinator
    modulation = entry.runtime_data.climate.relative_modulation_value
    await coordinator.async_set_control_hot_water_setpoint(50)

    async_fire_mqtt_message(hass, AVAILABILITY_TOPIC, "offline")
    await hass.async_block_till_done()
    mqtt_mock.async_publish.reset_mock()
    async_fire_mqtt_message(hass, AVAILABILITY_TOPIC, "online", retain=retain)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert coordinator.online
    sent = commands(mqtt_mock)
    if retain:
        assert "PM=3" not in sent
    else:
        assert {"PM=3", "PM=15", "PM=48", f"SH={coordinator.maximum_setpoint}", "SW=50", f"MM={modulation}", "TC=21.0"} <= set(sent)
        assert any(command.startswith("CS=") for command in sent)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_gateway_reboot_repeats_the_release_while_off(hass: HomeAssistant, mqtt_mock: MqttMockHAClient) -> None:
    entry = await setup_heating(hass, mqtt_mock, {CONF_PUSH_SETPOINT_TO_THERMOSTAT: True})
    await entry.runtime_data.climate.async_set_hvac_mode(HVACMode.OFF)

    async_fire_mqtt_message(hass, AVAILABILITY_TOPIC, "offline")
    await hass.async_block_till_done()
    mqtt_mock.async_publish.reset_mock()
    async_fire_mqtt_message(hass, AVAILABILITY_TOPIC, "online")
    await hass.async_block_till_done()

    sent = commands(mqtt_mock)
    assert {"CS=0", "MM=T", "TC=0"} <= set(sent)
    assert not any(command.startswith(("MM=1", "TC=2")) for command in sent)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_boiler_fault_is_reported(hass: HomeAssistant, mqtt_mock: MqttMockHAClient, freezer: FrozenDateTimeFactory) -> None:
    entry = await setup_heating(hass, mqtt_mock)

    async_fire_mqtt_message(hass, "OTGW/value/otgw/fault", "ON")
    async_fire_mqtt_message(hass, "OTGW/value/otgw/OEMFaultCode", "38")
    async_fire_mqtt_message(hass, "OTGW/value/otgw/ASF_flags", "00001000")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    fault = hass.states.get("binary_sensor.mock_title_boiler_fault")
    assert fault.state == STATE_ON
    assert fault.attributes["flags"] == "00001000"
    assert hass.states.get("sensor.mock_title_boiler_fault_code").state == "38"
    assert hass.states.get("binary_sensor.mock_title_boiler_health").state == STATE_ON
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_startup_with_missing_inputs_hands_control_back_once(hass: HomeAssistant, mqtt_mock: MqttMockHAClient, freezer: FrozenDateTimeFactory) -> None:
    hass.states.async_set("sensor.test_inside_sensor", "unavailable")
    hass.states.async_set("sensor.test_outside_sensor", "5.0")
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="otgw",
        data={**DEFAULT_USER_DATA, CONF_MODE: MODE_MQTT_OPENTHERM, CONF_DEVICE: "otgw", CONF_MQTT_TOPIC: "OTGW", CONF_MINIMUM_SETPOINT: 45},
        options={"default_hvac_mode": HVACMode.HEAT},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    async_fire_mqtt_message(hass, "OTGW/value/otgw/Tboiler", "40.0")
    await hass.async_block_till_done()
    climate = entry.runtime_data.climate
    await climate.async_set_target_temperature(21.0)
    mqtt_mock.async_publish.reset_mock()

    await climate.async_control_heating_loop()
    freezer.tick(timedelta(minutes=11))
    await climate.async_control_heating_loop()
    await climate.async_control_heating_loop()

    assert [command for command in commands(mqtt_mock) if command.startswith(("CS=", "MM="))] == ["CS=0", "MM=T"]

    mqtt_mock.async_publish.reset_mock()
    hass.states.async_set("sensor.test_inside_sensor", "19.5")
    await climate.async_control_heating_loop()

    assert any(command.startswith("CS=") and command != "CS=0" for command in commands(mqtt_mock))
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_minimum_modulation_falls_back_to_the_legacy_topic(hass: HomeAssistant, mqtt_mock: MqttMockHAClient) -> None:
    entry = await setup_heating(hass, mqtt_mock)

    # OTGW-firmware up to v0.10.2 publishes the misspelled name
    async_fire_mqtt_message(hass, "OTGW/value/otgw/MaxCapacityMinModLevell_lb_u8", "20")
    await hass.async_block_till_done()

    assert entry.runtime_data.coordinator.minimum_relative_modulation_value == 20.0
    assert await hass.config_entries.async_unload(entry.entry_id)
