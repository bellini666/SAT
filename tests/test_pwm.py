"""Tests for Pulse Width Modulation state handling in the climate."""

import logging
from dataclasses import replace
from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat.const import (
    CONF_DYNAMIC_MINIMUM_SETPOINT,
    CONF_DYNAMIC_MINIMUM_SETPOINT_VERSION,
    CONF_FORCE_PULSE_WIDTH_MODULATION,
    CONF_HEATING_CURVE_COEFFICIENT,
    CONF_HEATING_SYSTEM,
    CONF_MAXIMUM_SETPOINT,
    CONF_MINIMUM_SETPOINT,
    CONF_RADIATORS,
    DOMAIN,
    HEATING_SYSTEM_RADIATORS,
    MINIMUM_SETPOINT,
    BoilerStatus,
    PWMStatus,
)
from custom_components.sat.coordinator import DeviceState
from tests.const import DEFAULT_USER_DATA


async def setup_climate(hass: HomeAssistant, options: dict):
    hass.states.async_set("sensor.test_inside_sensor", "20.9")
    hass.states.async_set("sensor.test_outside_sensor", "9.9")

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**DEFAULT_USER_DATA, CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS, CONF_MINIMUM_SETPOINT: 57, CONF_MAXIMUM_SETPOINT: 75},
        options={CONF_HEATING_CURVE_COEFFICIENT: 1.8, **options},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    return entry, entry.runtime_data.climate, entry.runtime_data.coordinator


async def test_pwm_starts_once_the_boiler_temperature_arrives(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    entry, climate, coordinator = await setup_climate(hass, {CONF_FORCE_PULSE_WIDTH_MODULATION: True})

    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await climate.async_control_heating_loop()

    assert climate.pwm.status == PWMStatus.IDLE
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING and "PWM" in record.getMessage()]

    await coordinator.async_set_boiler_temperature(57)
    await climate.async_control_heating_loop()

    assert climate.pwm.status != PWMStatus.IDLE
    assert climate.pwm.duty_cycle is not None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_enabled_attribute_matches_the_effective_state(hass: HomeAssistant) -> None:
    entry, climate, coordinator = await setup_climate(hass, {})

    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await climate.async_control_heating_loop()

    assert climate.pulse_width_modulation_enabled
    assert climate.extra_state_attributes["pulse_width_modulation_enabled"] is True
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_dynamic_minimum_setpoint_starts_from_the_options_value(hass: HomeAssistant) -> None:
    entry, climate, coordinator = await setup_climate(hass, {CONF_DYNAMIC_MINIMUM_SETPOINT: True, CONF_MINIMUM_SETPOINT: 40})

    assert coordinator.minimum_setpoint == 40
    assert climate.minimum_setpoint.current == 40
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_full_duty_cycle_stays_on(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry, climate, coordinator = await setup_climate(hass, {CONF_FORCE_PULSE_WIDTH_MODULATION: True})

    await coordinator.async_set_boiler_temperature(30)
    await climate.async_set_target_temperature(25.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await climate.async_control_heating_loop()
    assert climate.pwm.status == PWMStatus.ON
    assert climate.pwm.duty_cycle[1] == 0

    freezer.tick(timedelta(seconds=climate.pwm.duty_cycle[0] + 1))
    await climate.async_control_heating_loop()

    assert climate.pwm.status == PWMStatus.ON
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_dynamic_minimum_setpoint_below_the_overshoot_value_still_enables_pwm(hass: HomeAssistant) -> None:
    entry, climate, coordinator = await setup_climate(hass, {CONF_DYNAMIC_MINIMUM_SETPOINT: True})
    await coordinator.async_set_boiler_temperature(57)
    climate.minimum_setpoint.warming_up(replace(coordinator.boiler, return_temperature=200))
    climate.minimum_setpoint.calculate(replace(coordinator.boiler, return_temperature=0), PWMStatus.ON)
    assert climate.minimum_setpoint.current < coordinator.minimum_setpoint

    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await climate.async_control_heating_loop()

    assert climate.pulse_width_modulation_enabled
    assert climate.setpoint == coordinator.minimum_setpoint
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(("options", "enabled"), [
    ({CONF_DYNAMIC_MINIMUM_SETPOINT: True, CONF_DYNAMIC_MINIMUM_SETPOINT_VERSION: 2}, True),
    ({CONF_DYNAMIC_MINIMUM_SETPOINT: True, CONF_DYNAMIC_MINIMUM_SETPOINT_VERSION: 1}, False),
    ({}, False),
])
async def test_overshoot_handling_enables_pwm_only_for_dynamic_minimum_setpoint_v2(hass: HomeAssistant, options: dict, enabled: bool) -> None:
    entry, climate, coordinator = await setup_climate(hass, {**options, CONF_MINIMUM_SETPOINT: 30})
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await coordinator.async_set_control_setpoint(60)
    await coordinator.async_set_heater_state(DeviceState.ON)
    await coordinator.async_set_boiler_temperature(75)
    assert coordinator.device_status == BoilerStatus.OVERSHOOT_HANDLING

    await climate.async_control_heating_loop()

    assert climate.pulse_width_modulation_enabled is enabled
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_closed_valves_hold_the_minimum_setpoint(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    hass.states.async_set("climate.radiator", HVACMode.HEAT, {"hvac_action": "idle", "temperature": 21.0, "current_temperature": 20.8})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**DEFAULT_USER_DATA, CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS, CONF_MINIMUM_SETPOINT: 57, CONF_MAXIMUM_SETPOINT: 75, CONF_RADIATORS: ["climate.radiator"]},
        options={CONF_HEATING_CURVE_COEFFICIENT: 1.8, CONF_FORCE_PULSE_WIDTH_MODULATION: True},
    )
    hass.states.async_set("sensor.test_inside_sensor", "20.9")
    hass.states.async_set("sensor.test_outside_sensor", "9.9")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    climate, coordinator = entry.runtime_data.climate, entry.runtime_data.coordinator

    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    for _ in range(3):
        await climate.async_control_heating_loop()

    assert climate.pwm.status == PWMStatus.IDLE
    assert coordinator.setpoint == MINIMUM_SETPOINT
    assert not coordinator.device_active
    assert len([record for record in caplog.records if record.levelno >= logging.WARNING and "valves" in record.getMessage()]) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_closed_valves_keep_the_pwm_enabled(hass: HomeAssistant) -> None:
    hass.states.async_set("climate.radiator", HVACMode.HEAT, {"hvac_action": "idle", "temperature": 21.0, "current_temperature": 20.8})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**DEFAULT_USER_DATA, CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS, CONF_MINIMUM_SETPOINT: 57, CONF_MAXIMUM_SETPOINT: 75, CONF_RADIATORS: ["climate.radiator"]},
        options={CONF_HEATING_CURVE_COEFFICIENT: 1.8},
    )
    hass.states.async_set("sensor.test_inside_sensor", "20.9")
    hass.states.async_set("sensor.test_outside_sensor", "9.9")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    climate = entry.runtime_data.climate

    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await climate.async_set_pulse_width_modulation(True)
    await climate.async_control_heating_loop()

    assert climate.pwm.enabled
    assert climate.pwm.status == PWMStatus.IDLE
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_disable_clears_the_adjusted_setpoint(hass: HomeAssistant) -> None:
    entry, climate, coordinator = await setup_climate(hass, {CONF_FORCE_PULSE_WIDTH_MODULATION: True})
    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await climate.async_set_target_temperature(21.0)
    await hass.async_block_till_done()
    assert climate.pwm.setpoint > MINIMUM_SETPOINT

    climate.pwm.disable()

    assert climate.pwm.setpoint == MINIMUM_SETPOINT
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_reset_keeps_the_adjusted_setpoint(hass: HomeAssistant) -> None:
    entry, climate, coordinator = await setup_climate(hass, {CONF_FORCE_PULSE_WIDTH_MODULATION: True})
    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await climate.async_set_target_temperature(21.0)
    await hass.async_block_till_done()
    adjusted = climate.pwm.setpoint
    assert adjusted > MINIMUM_SETPOINT

    climate.pwm.reset()
    assert climate.pwm.setpoint == adjusted

    await climate.async_control_heating_loop()
    assert climate.pwm.setpoint == adjusted
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_cycle_limit_survives_resets_and_holds_off(hass: HomeAssistant) -> None:
    entry, climate, coordinator = await setup_climate(hass, {CONF_FORCE_PULSE_WIDTH_MODULATION: True})
    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_hvac_mode(HVACMode.HEAT)

    for target in (21.0, 21.05, 21.1, 21.15):
        await climate.async_set_target_temperature(target)
        await climate.async_control_heating_loop()

    assert climate.pwm.status == PWMStatus.OFF
    assert climate.setpoint == MINIMUM_SETPOINT
    assert await hass.config_entries.async_unload(entry.entry_id)
