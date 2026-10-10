"""The tests for the climate component."""

import pytest
from homeassistant.components.climate import HVACMode, PRESET_AWAY
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.template import DOMAIN as TEMPLATE_DOMAIN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat.climate import SatClimate
from custom_components.sat.config_flow import SatFlowHandler
from custom_components.sat.const import *
from custom_components.sat.errors import Error
from custom_components.sat.fake import SatFakeCoordinator
from tests.const import DEFAULT_USER_DATA


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 57,
                CONF_MAXIMUM_SETPOINT: 75,
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 1.8,
                CONF_FORCE_PULSE_WIDTH_MODULATION: True,
            },
            {
                TEMPLATE_DOMAIN: [
                    {
                        SENSOR_DOMAIN: [
                            {
                                "name": "test_inside_sensor",
                                "state": "{{ 20.9 | float }}",
                            },
                            {
                                "name": "test_outside_sensor",
                                "state": "{{ 9.9 | float }}",
                            }
                        ]
                    },
                ],
            },
    )],
])
async def test_scenario_1(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    climate.schedule_control_heating_loop(force=True)

    assert climate.setpoint == 57
    assert climate.heating_curve.value == 32.2

    assert climate.pulse_width_modulation_enabled
    assert climate.pwm.last_duty_cycle_percentage == 23.83
    assert climate.pwm.duty_cycle == (285, 914)


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 58,
                CONF_MAXIMUM_SETPOINT: 75
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 1.3,
                CONF_FORCE_PULSE_WIDTH_MODULATION: True,
            },
            {
                TEMPLATE_DOMAIN: [
                    {
                        SENSOR_DOMAIN: [
                            {
                                "name": "test_inside_sensor",
                                "state": "{{ 18.99 | float }}",
                            },
                            {
                                "name": "test_outside_sensor",
                                "state": "{{ 11.1 | float }}",
                            }
                        ]
                    },
                ],
            },
    )],
])
async def test_scenario_2(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    await coordinator.async_set_boiler_temperature(58)
    await climate.async_set_target_temperature(19.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    climate.schedule_control_heating_loop(force=True)

    assert climate.setpoint == 10
    assert climate.heating_curve.value == 27.8
    assert climate.requested_setpoint == 28.0

    assert climate.pulse_width_modulation_enabled
    assert climate.pwm.last_duty_cycle_percentage == 2.6
    assert climate.pwm.duty_cycle == (0, 2400)


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 41,
                CONF_MAXIMUM_SETPOINT: 75,
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 0.9,
                CONF_FORCE_PULSE_WIDTH_MODULATION: True,
            },
            {
                TEMPLATE_DOMAIN: [
                    {
                        SENSOR_DOMAIN: [
                            {
                                "name": "test_inside_sensor",
                                "state": "{{ 19.9 | float }}",
                            },
                            {
                                "name": "test_outside_sensor",
                                "state": "{{ -2.2 | float }}",
                            }
                        ]
                    },
                ],
            },
    )],
])
async def test_scenario_3(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    await coordinator.async_set_boiler_temperature(41)
    await climate.async_set_target_temperature(20.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    climate.schedule_control_heating_loop(force=True)

    assert climate.setpoint == 41.0
    assert climate.heating_curve.value == 32.5
    assert climate.requested_setpoint == 34.6

    assert climate.pulse_width_modulation_enabled
    assert climate.pwm.last_duty_cycle_percentage == 53.62
    assert climate.pwm.duty_cycle == (643, 556)


async def test_pid_uses_the_configured_heating_system(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.test_inside_sensor", "20.9")
    hass.states.async_set("sensor.test_outside_sensor", "9.9")

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=SatFlowHandler.VERSION,
        data={**DEFAULT_USER_DATA, CONF_HEATING_SYSTEM: HEATING_SYSTEM_UNDERFLOOR, CONF_MINIMUM_SETPOINT: 10, CONF_MAXIMUM_SETPOINT: 55},
        options={CONF_HEATING_CURVE_COEFFICIENT: 1.8},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    climate = entry.runtime_data.climate
    climate.pid.update_reset(error=Error("sensor.test_inside_sensor", 0.0), heating_curve_value=30.0)

    assert climate.pid.kp == 13.5


ROOM = "climate.room"


async def setup_rooms(hass: HomeAssistant, data: dict | None = None, options: dict | None = None) -> MockConfigEntry:
    hass.states.async_set("sensor.test_inside_sensor", "20.9")
    hass.states.async_set("sensor.test_outside_sensor", "9.9")

    entry = MockConfigEntry(domain=DOMAIN, data={**DEFAULT_USER_DATA, CONF_ROOMS: [ROOM], **(data or {})}, options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    return entry


async def test_room_turned_off_adds_no_error(hass: HomeAssistant) -> None:
    hass.states.async_set(ROOM, HVACMode.OFF, {"temperature": 23.0, "current_temperature": 19.0})
    entry = await setup_rooms(hass)
    climate = entry.runtime_data.climate

    await climate.async_set_target_temperature(21.0)

    assert len(climate.areas.errors) == 0
    assert climate.max_error.entity_id == climate.entity_id
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_missing_temperature_attributes_do_not_break_the_loop(hass: HomeAssistant) -> None:
    hass.states.async_set("climate.radiator", HVACMode.HEAT, {"current_temperature": 19.0})
    entry = await setup_rooms(hass, {CONF_RADIATORS: ["climate.radiator"], CONF_OUTSIDE_SENSOR_ENTITY_ID: ["weather.home"]})
    hass.states.async_set("weather.home", "sunny", {})
    climate = entry.runtime_data.climate

    assert climate.valves_open is False
    assert climate.current_outside_temperature is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_thermostat_without_a_setpoint_keeps_the_target(hass: HomeAssistant) -> None:
    hass.states.async_set("climate.thermostat", HVACMode.HEAT, {"temperature": 20.0})
    entry = await setup_rooms(hass, {CONF_THERMOSTAT: "climate.thermostat"})
    climate = entry.runtime_data.climate
    await climate.async_set_target_temperature(21.0)

    hass.states.async_set("climate.thermostat", HVACMode.OFF, {"temperature": None})
    await hass.async_block_till_done()

    assert climate.target_temperature == 21.0
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_rooms_attribute_follows_the_room_targets(hass: HomeAssistant) -> None:
    hass.states.async_set(ROOM, HVACMode.HEAT, {"temperature": 21.0, "current_temperature": 20.0})
    entry = await setup_rooms(hass)
    await entry.runtime_data.climate.async_set_preset_mode(PRESET_AWAY)

    hass.states.async_set(ROOM, HVACMode.HEAT, {"temperature": 22.0, "current_temperature": 20.0})
    await hass.async_block_till_done()

    assert entry.runtime_data.climate.extra_state_attributes["rooms"] == {ROOM: 22.0}
    assert await hass.config_entries.async_unload(entry.entry_id)
