"""Tests for Pulse Width Modulation state handling in the climate."""

import logging

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat.const import (
    CONF_FORCE_PULSE_WIDTH_MODULATION,
    CONF_HEATING_CURVE_COEFFICIENT,
    CONF_HEATING_SYSTEM,
    CONF_MAXIMUM_SETPOINT,
    CONF_MINIMUM_SETPOINT,
    DOMAIN,
    HEATING_SYSTEM_RADIATORS,
    PWMStatus,
)
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
