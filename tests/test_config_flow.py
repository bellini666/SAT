from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import section
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message, async_fire_time_changed, async_mock_service
from pytest_homeassistant_custom_component.typing import MqttMockHAClient

from custom_components.sat.config_flow import SatFlowHandler
from custom_components.sat.const import CONF_RADIATORS, CONF_ROOMS, DOMAIN, MODE_FAKE, MODE_MQTT_OPENTHERM
from tests.const import BUTTON, DEFAULT_USER_DATA


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


@pytest.mark.parametrize(("flame_timeout", "plateau_timeout"), [("00:00:00", "00:40:00"), ("00:10:00", "00:04:59")])
async def test_options_reject_calibration_timeouts_without_a_plateau(hass: HomeAssistant, flame_timeout: str, plateau_timeout: str) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "system_configuration"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {
        "automatic_duty_cycle": True,
        "sync_climates_with_mode": True,
        "sensor_max_value_age": "06:00:00",
        "default_hvac_mode": "heat",
        "window_minimum_open_time": "00:00:15",
        "advanced": {
            "simulation": False,
            "thermal_comfort": False,
            "dynamic_minimum_setpoint": False,
            "climate_valve_offset": 0,
            "target_temperature_step": 0.5,
            "maximum_relative_modulation": 100,
            "sample_time": "00:01:00",
            "calibration_flame_timeout": flame_timeout,
            "calibration_plateau_timeout": plateau_timeout,
        },
    })

    assert result["type"] == "form"
    assert result["errors"] == {"base": "calibration_timeout"}
    assert "calibration_flame_timeout" not in entry.options
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


async def reconfigure_to_menu(hass: HomeAssistant, entry: MockConfigEntry, next_step_id: str) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "inside_sensor_entity_id": "sensor.test_inside_sensor",
        "outside_sensor_entity_id": ["sensor.test_outside_sensor"],
    })
    if result["step_id"] == "heating_system":
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"heating_system": "radiators"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": next_step_id})


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

    result = await reconfigure_to_menu(hass, entry, "calibrate")
    assert result["type"] == "progress"
    assert entry.runtime_data.climate.hvac_mode == HVACMode.HEAT
    assert entry.runtime_data.climate.control_paused

    await tick(hass, freezer, 14)
    result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["type"] == "menu"
    assert result["step_id"] == "calibrated"
    assert result["description_placeholders"]["minimum_setpoint"] == 40.0
    assert not entry.runtime_data.climate.control_paused

    hass.config_entries.flow.async_abort(result["flow_id"])
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_calibration_restores_the_climate_hvac_modes(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    hass.states.async_set("sensor.test_inside_sensor", "19.5")
    hass.states.async_set("sensor.test_outside_sensor", "5.0")
    hass.states.async_set("climate.radiator", HVACMode.OFF, {"hvac_modes": [HVACMode.OFF, HVACMode.HEAT]})
    hass.states.async_set("climate.room", HVACMode.HEAT, {"hvac_modes": [HVACMode.OFF, HVACMode.HEAT]})

    entry = MockConfigEntry(domain=DOMAIN, version=SatFlowHandler.VERSION, data={**DEFAULT_USER_DATA, "minimum_setpoint": 45, "heating_system": "radiators", CONF_RADIATORS: ["climate.radiator"], CONF_ROOMS: ["climate.room"]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.coordinator.async_set_boiler_temperature(40)
    calls = async_mock_service(hass, "climate", "set_hvac_mode")

    result = await reconfigure_to_menu(hass, entry, "calibrate")
    await tick(hass, freezer, 14)
    result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["step_id"] == "calibrated"
    assert [(call.data["entity_id"], call.data["hvac_mode"]) for call in calls] == [
        ("climate.radiator", HVACMode.HEAT),
        ("climate.room", HVACMode.HEAT),
        ("climate.radiator", HVACMode.OFF),
        ("climate.room", HVACMode.HEAT),
    ]

    hass.config_entries.flow.async_abort(result["flow_id"])
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_calibration_skips_a_climate_without_heat_mode(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    hass.states.async_set("sensor.test_inside_sensor", "19.5")
    hass.states.async_set("sensor.test_outside_sensor", "5.0")
    hass.states.async_set("climate.radiator", HVACMode.OFF, {"hvac_modes": [HVACMode.OFF, HVACMode.HEAT]})
    hass.states.async_set("climate.room", HVACMode.AUTO, {"hvac_modes": [HVACMode.OFF, HVACMode.AUTO]})

    entry = MockConfigEntry(domain=DOMAIN, version=SatFlowHandler.VERSION, data={**DEFAULT_USER_DATA, "minimum_setpoint": 45, "heating_system": "radiators", CONF_RADIATORS: ["climate.radiator"], CONF_ROOMS: ["climate.room"]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.coordinator.async_set_boiler_temperature(40)
    calls = async_mock_service(hass, "climate", "set_hvac_mode")

    result = await reconfigure_to_menu(hass, entry, "calibrate")
    await tick(hass, freezer, 14)
    result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["step_id"] == "calibrated"
    assert ("climate.room", HVACMode.HEAT) not in [(call.data["entity_id"], call.data["hvac_mode"]) for call in calls]

    hass.config_entries.flow.async_abort(result["flow_id"])
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_failed_calibration_offers_manual_entry(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = await setup_heating_entry(hass)
    coordinator = entry.runtime_data.coordinator

    result = await reconfigure_to_menu(hass, entry, "calibrate")
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


async def test_overshoot_protection_value_in_options(hass: HomeAssistant) -> None:
    entry = await setup_heating_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "general"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"minimum_setpoint": 47})
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert entry.runtime_data.coordinator.minimum_setpoint == 47
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_reconfigured_overshoot_protection_takes_effect(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=SatFlowHandler.VERSION, data={**DEFAULT_USER_DATA, "minimum_setpoint": 45, "heating_system": "radiators"}, options={"minimum_setpoint": 45})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await reconfigure_to_menu(hass, entry, "overshoot_protection")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"minimum_setpoint": 52})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"manufacturer": result["data_schema"].schema["manufacturer"].config["options"][0]["value"]})
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert entry.runtime_data.coordinator.minimum_setpoint == 52
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_cancels_a_flow_calibration(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = await setup_heating_entry(hass)

    result = await reconfigure_to_menu(hass, entry, "calibrate")
    await tick(hass, freezer, 2)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["step_id"] == "overshoot_protection"
    hass.config_entries.flow.async_abort(result["flow_id"])


def unloaded_mqtt_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=SatFlowHandler.VERSION,
        data={**DEFAULT_USER_DATA, "mode": MODE_MQTT_OPENTHERM, "device": "otgw", "mqtt_topic": "OTGW", "minimum_setpoint": 45, "heating_system": "radiators"},
    )
    entry.add_to_hass(hass)
    return entry


async def test_calibration_without_mqtt_offers_manual_entry(hass: HomeAssistant) -> None:
    entry = unloaded_mqtt_entry(hass)

    result = await reconfigure_to_menu(hass, entry, "calibrate")
    await hass.async_block_till_done()

    assert hass.config_entries.flow.async_get(result["flow_id"])["step_id"] == "overshoot_protection"
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_manufacturer_step_without_mqtt(hass: HomeAssistant) -> None:
    entry = unloaded_mqtt_entry(hass)

    result = await reconfigure_to_menu(hass, entry, "overshoot_protection")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"minimum_setpoint": 52})

    assert result["step_id"] == "manufacturer"
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_manufacturer_step_preselects_the_detected_manufacturer(hass: HomeAssistant, hass_storage: dict) -> None:
    hass_storage["sat_open_therm_mqtt_coordinator_otgw"] = {"version": 1, "key": "sat_open_therm_mqtt_coordinator_otgw", "data": {"slave_memberid_code": "95"}}
    entry = unloaded_mqtt_entry(hass)

    result = await reconfigure_to_menu(hass, entry, "overshoot_protection")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"minimum_setpoint": 52})

    field = next(key for key in result["data_schema"].schema if key == "manufacturer")
    assert field.default() == "Worcester"
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_calibration_refuses_while_another_runs(hass: HomeAssistant) -> None:
    entry = await setup_heating_entry(hass)
    await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)

    result = await reconfigure_to_menu(hass, entry, "calibrate")

    assert result["type"] == "abort"
    assert result["reason"] == "already_in_progress"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_reconfigure_offers_the_overshoot_protection_value_in_use(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=SatFlowHandler.VERSION, data={**DEFAULT_USER_DATA, "minimum_setpoint": 40, "heating_system": "radiators"}, options={"minimum_setpoint": 42})
    entry.add_to_hass(hass)

    result = await reconfigure_to_menu(hass, entry, "overshoot_protection")

    field = next(key for key in result["data_schema"].schema if key == "minimum_setpoint")
    assert field.default() == 42


async def test_reconfigure_offers_the_saved_manufacturer(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=SatFlowHandler.VERSION, data={**DEFAULT_USER_DATA, "minimum_setpoint": 45, "heating_system": "radiators", "manufacturer": "Ideal"})
    entry.add_to_hass(hass)

    result = await reconfigure_to_menu(hass, entry, "overshoot_protection")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"minimum_setpoint": 45})

    field = next(key for key in result["data_schema"].schema if key == "manufacturer")
    assert field.default() == "Ideal"
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_options_reject_a_maximum_setpoint_below_the_overshoot_protection_value(hass: HomeAssistant) -> None:
    entry = await setup_heating_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "general"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"maximum_setpoint": 40, "minimum_setpoint": 45})

    assert result["type"] == "form"
    assert result["errors"] == {"maximum_setpoint": "maximum_setpoint_below_overshoot_protection"}
    assert "maximum_setpoint" not in entry.options
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_reconfigure_rejects_an_overshoot_protection_value_above_the_maximum_setpoint(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=SatFlowHandler.VERSION, data={**DEFAULT_USER_DATA, "minimum_setpoint": 45, "heating_system": "radiators"}, options={"maximum_setpoint": 50})
    entry.add_to_hass(hass)

    result = await reconfigure_to_menu(hass, entry, "overshoot_protection")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"minimum_setpoint": 52})

    assert result["step_id"] == "overshoot_protection"
    assert result["errors"] == {"minimum_setpoint": "maximum_setpoint_below_overshoot_protection"}
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_setup_flow_applies_the_manual_pid_gains(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.test_inside_sensor", "19.5")
    hass.states.async_set("sensor.test_outside_sensor", "5.0")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "simulator"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Test"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "inside_sensor_entity_id": "sensor.test_inside_sensor",
        "outside_sensor_entity_id": ["sensor.test_outside_sensor"],
    })
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"heating_system": "radiators"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "pid_controller"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"proportional": "10", "integral": "0.2", "derivative": "300"})
    await hass.async_block_till_done()

    pid = result["result"].runtime_data.climate.pid
    assert (pid.kp, pid.ki, pid.kd) == (10.0, 0.2, 300.0)
    assert await hass.config_entries.async_unload(result["result"].entry_id)
