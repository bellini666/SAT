from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import section
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message, async_fire_time_changed
from pytest_homeassistant_custom_component.typing import MqttMockHAClient

from custom_components.sat.config_flow import SatFlowHandler
from custom_components.sat.const import DOMAIN, MODE_FAKE, MODE_MQTT_OPENTHERM
from tests.const import DEFAULT_USER_DATA


async def test_create_coordinator(hass):
    flow_handler = SatFlowHandler()
    flow_handler.data = {
        "name": "Test",
        "mode": MODE_FAKE,
        "device": "test_device",
    }

    await flow_handler.async_create_coordinator()


async def test_user_menu_without_advanced_mode(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert "simulator" in result["menu_options"]
    assert "show_advanced_options" not in caplog.text


async def test_options_advanced_section(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["general", "presets", "system_configuration"]

    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "system_configuration"})
    advanced = result["data_schema"].schema["advanced"]
    assert isinstance(advanced, section)
    assert advanced.options["collapsed"] is True

    result = await hass.config_entries.options.async_configure(result["flow_id"], {
        "automatic_duty_cycle": True,
        "sync_climates_with_mode": True,
        "sensor_max_value_age": "06:00:00",
        "default_hvac_mode": "heat",
        "window_minimum_open_time": "00:00:15",
        "advanced": {
            "simulation": False,
            "thermal_comfort": True,
            "dynamic_minimum_setpoint": False,
            "climate_valve_offset": 0,
            "target_temperature_step": 0.5,
            "maximum_relative_modulation": 100,
            "sample_time": "00:01:00",
        },
    })
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert entry.options["thermal_comfort"] is True
    assert "advanced" not in entry.options
    assert "show_advanced_options" not in caplog.text
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_cycles_per_hour_follow_the_heating_system(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=SatFlowHandler.VERSION, data={**DEFAULT_USER_DATA, "minimum_setpoint": 45, "heating_system": "heat_pump"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "system_configuration"})

    cycles = next(value for key, value in result["data_schema"].schema.items() if key == "cycles_per_hour")
    assert [option["value"] for option in cycles.config["options"]] == ["2", "3"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def reconfigure_to_calibration(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "inside_sensor_entity_id": "sensor.test_inside_sensor",
        "outside_sensor_entity_id": ["sensor.test_outside_sensor"],
    })
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "calibrate"})
    return result


async def setup_heating_entry(hass: HomeAssistant) -> MockConfigEntry:
    hass.states.async_set("sensor.test_inside_sensor", "19.5")
    hass.states.async_set("sensor.test_outside_sensor", "5.0")

    entry = MockConfigEntry(domain=DOMAIN, version=SatFlowHandler.VERSION, data={**DEFAULT_USER_DATA, "minimum_setpoint": 45, "heating_system": "radiators"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.climate.async_set_hvac_mode(HVACMode.HEAT)

    return entry


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, count: int) -> None:
    for _ in range(count):
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def test_calibration_uses_the_running_coordinator(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = await setup_heating_entry(hass)
    coordinator = entry.runtime_data.coordinator
    await coordinator.async_set_boiler_temperature(40)

    result = await reconfigure_to_calibration(hass, entry)
    assert result["type"] == "progress"
    assert entry.runtime_data.climate.hvac_mode == HVACMode.OFF

    await tick(hass, freezer, 14)
    result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["type"] == "menu"
    assert result["step_id"] == "calibrated"
    assert result["description_placeholders"]["minimum_setpoint"] == 40.0
    assert entry.runtime_data.climate.hvac_mode == HVACMode.HEAT

    hass.config_entries.flow.async_abort(result["flow_id"])
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_failed_calibration_offers_manual_entry(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = await setup_heating_entry(hass)
    coordinator = entry.runtime_data.coordinator

    result = await reconfigure_to_calibration(hass, entry)
    for step in range(90):
        await coordinator.async_set_boiler_temperature(30 + step)
        await tick(hass, freezer, 1)

    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] == "form"
    assert result["step_id"] == "overshoot_protection"
    assert entry.runtime_data.climate.hvac_mode == HVACMode.HEAT

    hass.config_entries.flow.async_abort(result["flow_id"])
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.usefixtures("instant_mqtt_command_delay")
async def test_flow_coordinator_receives_gateway_updates(hass: HomeAssistant, mqtt_mock: MqttMockHAClient) -> None:
    flow = SatFlowHandler()
    flow.hass = hass
    flow.data = {"name": "Test", "mode": MODE_MQTT_OPENTHERM, "device": "otgw", "mqtt_topic": "OTGW"}

    coordinator = await flow.async_create_coordinator()
    await coordinator.async_setup()
    async_fire_mqtt_message(hass, "OTGW/value/otgw/flame", "ON")
    await hass.async_block_till_done()

    assert coordinator.flame_active
    await coordinator.async_will_remove_from_hass()
